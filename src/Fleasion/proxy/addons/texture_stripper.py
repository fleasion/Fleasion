"""TextureStripper: batch request/response modifier and CDN redirect manager.

All cross-connection state is held at the class level (singleton dicts) behind a
threading.Lock so it is safely shared across all MITM thread-pool workers.
"""

import gzip
import json
import urllib.request
import hashlib
import shutil
import logging
from pathlib import Path
from threading import Lock
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from ...utils import APP_CACHE_DIR, log_buffer

# Use orjson when available (2-3x faster JSON parse)
try:
    import orjson
    def _loads(s: bytes):
        return orjson.loads(s)
    def _dumps(obj) -> bytes:
        return orjson.dumps(obj)
except ImportError:
    def _loads(s: bytes):
        return json.loads(s)
    def _dumps(obj) -> bytes:
        return json.dumps(obj, separators=(',', ':')).encode()

logger = logging.getLogger(__name__)

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
_GZIP_MAGIC = b'\x1f\x8b'


def _decompress_cdn_response(data: bytes) -> bytes:
    if data[:4] == _ZSTD_MAGIC:
        import zstandard
        data = zstandard.ZstdDecompressor().decompress(data, max_output_size=64 * 1024 * 1024)
        log_buffer.log('CDN', f'Decompressed zstd CDN payload: {len(data)} bytes')
    elif data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
        log_buffer.log('CDN', f'Decompressed gzip CDN payload: {len(data)} bytes')
    return data


def _inject_obj_into_solidmodel(bin_data: bytes, obj_path: Path) -> bytes:
    from ...cache.tools.solidmodel_converter.obj_to_csg import export_csg_mesh
    from ...cache.tools.solidmodel_converter.converter import deserialize_rbxm
    from ...cache.tools.solidmodel_converter.rbxm.serializer import write_rbxm
    from ...cache.tools.solidmodel_converter.rbxm.types import PropertyFormat, RbxProperty
    from ...cache.tools.solidmodel_converter.csg_mesh import _detect_csgmdl_version

    bin_data = _decompress_cdn_response(bin_data)
    doc = deserialize_rbxm(bin_data)
    _INJECTABLE = frozenset({'PartOperationAsset', 'UnionOperation', 'NegateOperation', 'PartOperation'})

    csg_version = 3
    for inst in doc.roots:
        if inst.class_name in _INJECTABLE:
            prop = inst.properties.get('MeshData')
            if prop is not None and prop.value:
                mesh_bytes = prop.value if isinstance(prop.value, bytes) else bytes(prop.value, 'latin-1')
                detected = _detect_csgmdl_version(mesh_bytes)
                if detected is not None:
                    csg_version = detected
                    log_buffer.log('SolidModel', f'Detected original CSGMDL v{csg_version}')
            break

    csg_bytes = export_csg_mesh(obj_path, version=csg_version)
    injected = 0
    for inst in doc.roots:
        if inst.class_name in _INJECTABLE:
            inst.properties['MeshData'] = RbxProperty(
                name='MeshData', fmt=PropertyFormat.STRING, value=csg_bytes,
            )
            inst.properties['Color'] = RbxProperty(
                name='Color', fmt=PropertyFormat.COLOR3UINT8,
                value={'R': 255, 'G': 255, 'B': 255},
            )
            injected += 1

    if injected == 0:
        raise ValueError(f'No injectable root (roots: {[r.class_name for r in doc.roots]})')
    log_buffer.log('SolidModel', f'Injected CSGMDL into {injected} root(s)')
    return write_rbxm(doc)


def _try_mesh_to_obj(path: Path, ctx: str) -> Optional[Path]:
    try:
        from ...cache.tools.solidmodel_converter.mesh_intermediary import mesh_file_to_cached_obj
        return mesh_file_to_cached_obj(path)
    except Exception as exc:
        log_buffer.log('Intermediary', f'{ctx}: .mesh->OBJ failed: {exc}')
        return None


def _is_csgmdl_bin(path: Path) -> bool:
    """Check if a .bin file is actually a CSGMDL by looking for RBXM header and MeshData.
    
    Returns True only if:
    1. The file is a valid binary RBXM
    2. It contains an injectable root (PartOperationAsset, etc.)
    3. That root has MeshData containing a CSGMDL blob
    """
    try:
        raw = path.read_bytes()
        # Decompress if needed
        data = raw
        if raw[:4] == b'\x28\xb5\x2f\xfd':  # zstd
            import zstandard
            data = zstandard.ZstdDecompressor().decompress(raw, max_output_size=64 * 1024 * 1024)
        elif raw[:2] == b'\x1f\x8b':  # gzip
            data = gzip.decompress(raw)
        
        # Check if it's a valid binary RBXM
        from ...cache.tools.solidmodel_converter.mesh_intermediary import is_binary_rbxm
        if not is_binary_rbxm(data):
            return False
        
        # Try to deserialize and find injectable roots with MeshData
        from ...cache.tools.solidmodel_converter.converter import deserialize_rbxm
        doc = deserialize_rbxm(data)
        _INJECTABLE = frozenset({'PartOperationAsset', 'UnionOperation', 'NegateOperation', 'PartOperation'})
        
        for inst in doc.roots:
            if inst.class_name in _INJECTABLE:
                prop = inst.properties.get('MeshData')
                if prop is not None and prop.value:
                    # Check if MeshData looks like CSGMDL
                    from ...cache.tools.solidmodel_converter.csg_mesh import _detect_csgmdl_version
                    mesh_bytes = prop.value if isinstance(prop.value, bytes) else bytes(prop.value, 'latin-1')
                    if _detect_csgmdl_version(mesh_bytes) is not None:
                        return True
        return False
    except Exception:
        return False


def _try_bin_to_obj(path: Path, ctx: str) -> Optional[Path]:
    try:
        from ...cache.tools.solidmodel_converter.mesh_intermediary import bin_file_to_cached_obj
        return bin_file_to_cached_obj(path)
    except Exception as exc:
        log_buffer.log('Intermediary', f'{ctx}: .bin->OBJ failed: {exc}')
        return None


def _download_remote_file(url: str, dest: Path, label: str) -> bool:
    try:
        APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return True
        log_buffer.log('Downloader', f'Downloading remote {label}: {url}')
        req = urllib.request.Request(url, headers={
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )
        })
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, 'wb') as out:
            shutil.copyfileobj(resp, out)
        log_buffer.log('Downloader', f'Saved {label}: {dest.name}')
        return True
    except Exception as exc:
        log_buffer.log('Downloader', f'Failed to download {label}: {exc}')
        return False


class TextureStripper:
    """Modifies Roblox asset batch requests/responses and manages CDN redirects."""

    # ── Shared singleton state (class-level) ──────────────────────────────
    _lock: Lock = Lock()
    _pending: Dict[str, Tuple[str, str]] = {}       # requestId -> (kind, value)
    _cdn_redirects: Dict[str, str] = {}              # base_cdn_url -> redirect_url
    _local_redirects: Dict[str, str] = {}            # base_cdn_url -> local_path
    _solidmodel_injections: Dict[str, str] = {}      # base_cdn_url -> obj_path
    # ─────────────────────────────────────────────────────────────────────

    ASSET_TYPES: Dict[int, str] = {
        1: 'Image', 2: 'TShirt', 3: 'Audio', 4: 'Mesh', 5: 'Lua',
        6: 'HTML', 7: 'Text', 8: 'Hat', 9: 'Place', 10: 'Model',
        11: 'Shirt', 12: 'Pants', 13: 'Decal', 16: 'Avatar', 17: 'Head',
        18: 'Face', 19: 'Gear', 21: 'Badge', 22: 'GroupEmblem',
        24: 'Animation', 25: 'Arms', 26: 'Legs', 27: 'Torso',
        28: 'RightArm', 29: 'LeftArm', 30: 'LeftLeg', 31: 'RightLeg',
        32: 'Package', 33: 'YouTubeVideo', 34: 'GamePass', 35: 'App',
        37: 'Code', 38: 'Plugin', 39: 'SolidModel', 40: 'MeshPart',
        41: 'HairAccessory', 42: 'FaceAccessory', 43: 'NeckAccessory',
        44: 'ShoulderAccessory', 45: 'FrontAccessory', 46: 'BackAccessory',
        47: 'WaistAccessory', 48: 'ClimbAnimation', 49: 'DeathAnimation',
        50: 'FallAnimation', 51: 'IdleAnimation', 52: 'JumpAnimation',
        53: 'RunAnimation', 54: 'SwimAnimation', 55: 'WalkAnimation',
        56: 'PoseAnimation', 57: 'EarAccessory', 58: 'EyeAccessory',
        59: 'LocalizationTableManifest', 61: 'EmoteAnimation', 62: 'Video',
        63: 'TexturePack', 64: 'TShirtAccessory', 65: 'ShirtAccessory',
        66: 'PantsAccessory', 67: 'JacketAccessory', 68: 'SweaterAccessory',
        69: 'ShortsAccessory', 70: 'LeftShoeAccessory', 71: 'RightShoeAccessory',
        72: 'DressSkirtAccessory', 73: 'FontFamily', 74: 'FontFace',
        75: 'MeshHiddenSurfaceRemoval', 76: 'EyebrowAccessory',
        77: 'EyelashAccessory', 78: 'MoodAnimation', 79: 'DynamicHead',
        80: 'CodeSnippet',
    }
    _REVERSE: Dict[str, int] = {name.lower(): tid for tid, name in ASSET_TYPES.items()}

    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self._cache_scraper = None  # Set by ProxyMaster after construction

    def set_cache_scraper(self, scraper) -> None:
        """Wire in the CacheScraper for place-ID lookups on replacement assets."""
        self._cache_scraper = scraper

    # Pre-downloaded private replacement assets: replacement_id -> local file path.
    # Populated eagerly at proxy startup by precheck_replacements().
    _predownloaded: Dict[int, str] = {}
    # IDs confirmed publicly accessible (no pre-download needed).
    _checked_public: set = set()

    _PREDOWNLOAD_DIR: Path = APP_CACHE_DIR / 'predownloaded'

    def precheck_replacements(self) -> None:
        """Eagerly check all replacement asset IDs and pre-download private ones.

        Called in a background thread at proxy startup. For each ID-based
        replacement target, tests accessibility:
          - 200 → public, normal ID swap will work, skip.
          - 403 → private, download via place-ID bypass, save to disk.
          - 404 → deleted/invalid, log warning.

        Pre-downloaded files are served as local file replacements so the
        batch request body stays unmodified (no placeId injection needed).
        """
        scraper = self._cache_scraper
        if scraper is None:
            log_buffer.log('Replacer', 'No scraper wired — skipping replacement precheck')
            return

        replacements_tuple = self.config_manager.get_all_replacements()
        replacements = replacements_tuple[0]  # dict[int, int]: original -> replacement
        if not replacements:
            return

        # Deduplicate: multiple originals can map to the same replacement ID
        unique_targets = set(replacements.values())
        log_buffer.log('Replacer', f'Pre-checking {len(unique_targets)} replacement asset(s)...')

        self._PREDOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

        cookie = scraper._get_roblosecurity()
        extra: dict = {}
        if cookie:
            extra['Cookie'] = f'.ROBLOSECURITY={cookie};'

        public_count = 0
        private_count = 0
        failed_count = 0

        for target_id in unique_targets:
            if int(target_id) in self._predownloaded:
                continue  # Already handled (e.g. from a previous call)

            local_path = self._PREDOWNLOAD_DIR / f'{target_id}.dat'
            legacy_path = self._PREDOWNLOAD_DIR / f'{target_id}.bin'

            # If file already exists on disk from a previous session, reuse it.
            # Check both new (.dat) and legacy (.bin) extension.
            if legacy_path.exists() and legacy_path.stat().st_size > 0:
                legacy_path.rename(local_path)
            if local_path.exists() and local_path.stat().st_size > 0:
                self._predownloaded[int(target_id)] = str(local_path)
                private_count += 1
                log_buffer.log('Replacer', f'Reusing cached pre-download for {target_id}')
                continue

            # Quick accessibility check — needs auth cookie just to use the API.
            # A 200 here means the asset is publicly downloadable (no place-ID
            # needed); the cookie is required for API auth, not ownership.
            _data, status = scraper._https_get(
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={target_id}',
                extra_headers=dict(extra) if extra else None,
                return_status=True,
            )
            if _data:
                # 200 — publicly accessible, normal ID swap will work
                self._checked_public.add(int(target_id))
                public_count += 1
                continue

            if status == 404:
                log_buffer.log('Replacer', f'Replacement asset {target_id} not found (404) — skipping')
                failed_count += 1
                continue

            if status != 403:
                log_buffer.log('Replacer', f'Replacement asset {target_id} returned status {status} — skipping')
                failed_count += 1
                continue

            # 403 — private asset, download via place-ID bypass
            log_buffer.log('Replacer', f'Replacement asset {target_id} is private, pre-downloading...')
            data, dl_status = scraper._fetch_asset_with_place_id_retry(
                str(target_id), extra_headers=dict(extra) if extra else None,
            )
            if data:
                try:
                    local_path.write_bytes(data)
                    self._predownloaded[int(target_id)] = str(local_path)
                    private_count += 1
                    log_buffer.log('Replacer', f'Pre-downloaded private asset {target_id} ({len(data)} bytes)')
                except Exception as exc:
                    log_buffer.log('Replacer', f'Failed to save pre-download for {target_id}: {exc}')
                    failed_count += 1
            else:
                log_buffer.log('Replacer', f'Could not pre-download private asset {target_id} (status {dl_status})')
                failed_count += 1

        log_buffer.log('Replacer',
                       f'Pre-check complete: {public_count} public, {private_count} private (pre-downloaded), {failed_count} failed')

    # ------------------------------------------------------------------
    # Batch request (called from server MITM thread)
    # ------------------------------------------------------------------

    def process_batch_request(self, body: bytes, req_headers: dict, replacements_tuple: tuple, batch_id: str = '') -> tuple[bytes, bytes]:
        """Modify batch JSON: removals, ID replacements, CDN/local routing.

        Returns ``(modified_body, scraper_body)`` where *scraper_body* has
        the original asset IDs restored (index-aligned with the upstream
        response) so the cache scraper stores content under original IDs.
        """
        if not body:
            return body, body
        try:
            data = _loads(body)
        except Exception:
            return body
        if not isinstance(data, list):
            return body

        replacements, removals, cdn_replacements, local_replacements = replacements_tuple

        # Move pre-downloaded private replacements into local_replacements so
        # they follow the exact same code path as user-configured local files
        # (keeps batch body unmodified, CDN URL mapped at response time).
        if self._predownloaded:
            replacements = dict(replacements)
            local_replacements = dict(local_replacements)
            for orig_id, repl_id in list(replacements.items()):
                predownloaded = self._predownloaded.get(int(repl_id))
                if predownloaded is not None:
                    del replacements[orig_id]
                    local_replacements[orig_id] = predownloaded

        # Pre-resolve TexturePack sub-asset IDs to slot keys.
        # If the user targets a sub-asset ID (e.g. 7547298681, the normal map),
        # we convert it to "parentId:mapIndex" (e.g. "7547298786:1") so the slot
        # replacement below can match the correct batch entry.
        # This lookup is populated by cache_scraper whenever a TexturePack is cached.
        if self._cache_scraper is not None:
            lookup = getattr(self._cache_scraper, '_texpack_subasset_lookup', {})
            if lookup:
                all_src_ids = (set(replacements.keys()) | set(cdn_replacements.keys()) |
                               set(local_replacements.keys()) | removals)
                needs_resolve = all_src_ids & set(lookup.keys())
                if needs_resolve:
                    replacements     = dict(replacements)
                    cdn_replacements = dict(cdn_replacements)
                    local_replacements = dict(local_replacements)
                    removals = set(removals)
                    for sub_id in needs_resolve:
                        parent_id, map_idx = lookup[sub_id]
                        slot_key = f'{parent_id}:{map_idx}'
                        if sub_id in replacements:
                            replacements[slot_key] = replacements.pop(sub_id)
                        if sub_id in cdn_replacements:
                            cdn_replacements[slot_key] = cdn_replacements.pop(sub_id)
                        if sub_id in local_replacements:
                            local_replacements[slot_key] = local_replacements.pop(sub_id)
                        if sub_id in removals:
                            removals.discard(sub_id)
                            removals.add(slot_key)

        # If any replacement targets are newly added (not yet checked),
        # trigger a background precheck so the next batch can serve them locally.
        if replacements and self._cache_scraper is not None:
            unknown = {int(v) for v in replacements.values()
                       if int(v) not in self._predownloaded and int(v) not in self._checked_public}
            if unknown:
                import threading as _thr
                _thr.Thread(target=self.precheck_replacements,
                            name='ReplacementPrecheck', daemon=True).start()

        modified = False
        # Track original IDs for items that undergo ID replacement so the
        # scraper body can be built with original IDs after the loop.
        id_swapped: dict[int, int] = {}   # index → original_aid

        orig_len = len(data)
        data = [e for e in data if isinstance(e, dict) and not self._should_remove(e, removals)]
        if len(data) < orig_len:
            log_buffer.log('Remover', f'Removed {orig_len - len(data)} asset(s)')
            modified = True

        for idx, e in enumerate(data):
            if not isinstance(e, dict):
                continue
            aid = e.get('assetId')
            req_id = e.get('requestId')
            type_keys = self._get_type_keys(e)

            # Build slot key from the fidelity field in contentRepresentationPriorityList.
            # slot_key = "assetId:mapIndex" (e.g. "7547298786:1" for the normal-map slot).
            # This allows per-slot replacement: the user can target a specific texture map
            # of a TexturePack by its sub-asset ID (auto-resolved below) or directly.
            map_index = self._get_texpack_map_index(e)
            slot_key = f'{aid}:{map_index}' if (aid is not None and map_index is not None) else None

            # ID/type replacement — slot-specific match takes priority over whole-asset
            matched = None
            if slot_key and slot_key in replacements:
                matched = slot_key
            elif aid in replacements:
                matched = aid
            else:
                for tk in type_keys:
                    if tk in replacements:
                        matched = tk
                        break
            if matched is not None:
                replacement_id = replacements[matched]
                
                is_texpack_match = (':' in str(matched)) or (e.get('assetTypeId') == 63) or (
                    self._REVERSE.get(str(e.get('assetType', '')).lower()) == 63
                )
                
                if is_texpack_match and req_id and aid and str(replacement_id).isdigit():
                    scraper = self._cache_scraper
                    if scraper:
                        local_tgt = None
                        if int(replacement_id) in self._predownloaded:
                            local_tgt = self._predownloaded[int(replacement_id)]
                        else:
                            dl_path = APP_CACHE_DIR / f'predownloaded/{replacement_id}.dat'
                            dl_path.parent.mkdir(parents=True, exist_ok=True)
                            if not dl_path.exists():
                                log_buffer.log('Replacer', f'Downloading asset {replacement_id} for KTX2 conversion...')
                                extra_hdrs = {}
                                cookie = scraper._get_roblosecurity()
                                if cookie:
                                    extra_hdrs['Cookie'] = f'.ROBLOSECURITY={cookie};'
                                scraped_data, dl_status = scraper._fetch_asset_with_place_id_retry(str(replacement_id), extra_headers=extra_hdrs or None)
                                if scraped_data:
                                    dl_path.write_bytes(scraped_data)
                            if dl_path.exists():
                                local_tgt = str(dl_path)
                        
                        if local_tgt is not None:
                            # Instead of pushing the ID to Roblox, we route it as a local file, prompting conversion
                            self._route_local(f'{batch_id}_{req_id}', aid, local_tgt, is_solidmodel, is_texpack=True)
                            modified = True
                            continue # Skip the usual e['assetId'] = replacement_id logic

                e['assetId'] = replacement_id
                id_swapped[idx] = aid
                slot_info = f' (slot {map_index})' if (slot_key and slot_key == matched) else ''
                log_buffer.log('Replacer', f'Replaced {aid} -> {replacement_id}{slot_info}')
                modified = True

            # CDN / local routing — slot key takes priority
            if req_id and aid:
                is_solidmodel = (e.get('assetTypeId') == 39) or (
                    self._REVERSE.get(str(e.get('assetType', '')).lower()) == 39
                )
                all_keys = ([slot_key] if slot_key else []) + [aid] + type_keys
                cdn_key = next((k for k in all_keys if k in cdn_replacements), None)
                local_key = next((k for k in all_keys if k in local_replacements), None)
                if cdn_key is not None:
                    is_texpack_cdn = (':' in str(cdn_key)) or (e.get('assetTypeId') == 63) or (
                        self._REVERSE.get(str(e.get('assetType', '')).lower()) == 63
                    )
                    self._route_cdn(f'{batch_id}_{req_id}', aid, cdn_replacements[cdn_key], is_solidmodel, is_texpack_cdn)
                elif local_key is not None:
                    # Check if this replacement specifically targets a TexturePack slot or type
                    is_texpack = (':' in str(local_key)) or (e.get('assetTypeId') == 63) or (
                        self._REVERSE.get(str(e.get('assetType', '')).lower()) == 63
                    )
                    self._route_local(f'{batch_id}_{req_id}', aid, local_replacements[local_key], is_solidmodel, is_texpack)

        if modified:
            result = _dumps(data)
            # Build scraper body: same structure but with original IDs restored
            # so the cache scraper stores content under the original asset IDs.
            if id_swapped:
                for i, orig_aid in id_swapped.items():
                    data[i]['assetId'] = orig_aid
                scraper_body = _dumps(data)
            else:
                scraper_body = result
            return result, scraper_body
        return body, body

    # ------------------------------------------------------------------
    # Batch response (called from server MITM thread)
    # ------------------------------------------------------------------

    def process_batch_response(self, req_body: bytes, resp_body: bytes, req_headers: dict, batch_id: str = '') -> None:
        """Commit CDN URL -> redirect/local/solid mappings from batch response."""
        if not resp_body:
            return
        try:
            resp_data = _loads(resp_body)
        except Exception:
            return
        if not isinstance(resp_data, list):
            return

        with self._lock:
            for item in resp_data:
                if not isinstance(item, dict):
                    continue
                req_id_raw = str(item.get('requestId', ''))
                location = item.get('location')
                pending_key = f'{batch_id}_{req_id_raw}'
                if not req_id_raw or not location or pending_key not in self._pending:
                    continue
                url_type, url_value = self._pending.pop(pending_key)
                base_loc = location.split('?')[0]
                if url_type == 'cdn':
                    self._cdn_redirects[base_loc] = url_value
                    log_buffer.log('CDN', f'Will redirect {base_loc[:60]}...')
                elif url_type == 'local':
                    self._local_redirects[base_loc] = url_value
                    log_buffer.log('Local', f'Will serve local for {base_loc[:60]}...')
                elif url_type == 'solid':
                    self._solidmodel_injections[base_loc] = url_value
                    log_buffer.log('SolidModel', f'Will inject OBJ for {base_loc[:60]}...')

    # ------------------------------------------------------------------
    # CDN request check (called from server MITM thread for fts.rbxcdn.com)
    # ------------------------------------------------------------------

    def check_cdn_request(self, host: str, path: str) -> Optional[Tuple[str, str]]:
        """Returns ('local'|'cdn'|'solid', value) or None."""
        base_url = f'https://{host}{path}'.split('?')[0]
        with self._lock:
            if base_url in self._local_redirects:
                return ('local', self._local_redirects.pop(base_url))
            if base_url in self._cdn_redirects:
                return ('cdn', self._cdn_redirects.pop(base_url))
            if base_url in self._solidmodel_injections:
                return ('solid', self._solidmodel_injections[base_url])
        return None

    def has_pending(self) -> bool:
        """Return True if any batch req_ids are awaiting CDN URL mapping.

        Used by the server to decide whether to wait briefly for the batch
        response coroutine to register a CDN URL before giving up.
        """
        with self._lock:
            return bool(self._pending)

    # ------------------------------------------------------------------
    # SolidModel response injection (called from server MITM thread)
    # ------------------------------------------------------------------

    def process_solidmodel_response(self, resp_body: bytes, obj_path_str: str, cdn_url: str = '') -> bytes:
        # Pop ONLY this specific CDN URL, not every URL mapped to the same obj.
        # Popping all-by-value was the root cause of the SolidModel partial-replacement
        # bug: SolidModel A's injection would pop entries for B, C, D, E (same .obj),
        # so their CDN requests found nothing and passed through unreplaced.
        obj_path = Path(obj_path_str)
        with self._lock:
            if cdn_url:
                self._solidmodel_injections.pop(cdn_url, None)
            else:
                # Fallback: pop all by value (legacy path, shouldn't be hit)
                to_pop = [k for k, v in self._solidmodel_injections.items() if v == obj_path_str]
                for k in to_pop:
                    self._solidmodel_injections.pop(k, None)
        try:
            modified = _inject_obj_into_solidmodel(resp_body, obj_path)
            log_buffer.log('SolidModel', f'Injected OBJ ({len(modified)} bytes)')
            return modified
        except Exception as exc:
            log_buffer.log('SolidModel', f'Injection failed: {exc}')
            return resp_body

    # ------------------------------------------------------------------
    # Internal routing helpers
    # ------------------------------------------------------------------

    def _route_cdn(self, req_id: str, aid, cdn_url: str, is_solidmodel: bool, is_texpack: bool = False) -> None:
        parsed = urlparse(str(cdn_url))
        ext = Path(parsed.path).suffix.lower()
        url_hash = hashlib.md5(str(cdn_url).encode()).hexdigest()

        if ext == '.obj':
            local_cache = APP_CACHE_DIR / f'{url_hash}.obj'
            if _download_remote_file(cdn_url, local_cache, '.obj'):
                kind = 'solid' if is_solidmodel else 'local'
                with self._lock:
                    self._pending[req_id] = (kind, str(local_cache))
                return
            with self._lock:
                self._pending[req_id] = ('cdn', cdn_url)
            return

        if ext == '.mesh':
            if not is_solidmodel:
                with self._lock:
                    self._pending[req_id] = ('cdn', cdn_url)
                log_buffer.log('CDN', f'Queued direct .mesh redirect for {aid}')
                return
            local_cache = APP_CACHE_DIR / f'{url_hash}.mesh'
            if _download_remote_file(cdn_url, local_cache, '.mesh'):
                obj = _try_mesh_to_obj(local_cache, f'SolidModel CDN {aid}')
                if obj:
                    with self._lock:
                        self._pending[req_id] = ('solid', str(obj))
                    return
            with self._lock:
                self._pending[req_id] = ('cdn', cdn_url)
            return

        if ext == '.bin':
            local_cache = APP_CACHE_DIR / f'{url_hash}.bin'
            if _download_remote_file(cdn_url, local_cache, '.bin'):
                # Only attempt conversion if it's actually a CSGMDL
                if _is_csgmdl_bin(local_cache):
                    obj = _try_bin_to_obj(local_cache, f'CDN {aid}')
                    if obj:
                        kind = 'solid' if is_solidmodel else 'local'
                        with self._lock:
                            self._pending[req_id] = (kind, str(obj))
                        return
                    # Conversion failed, fall back to CDN redirect
                    log_buffer.log('CDN', f'{aid}: CSGMDL conversion failed, redirecting to CDN')
                else:
                    # Not a CSGMDL, serve the .bin directly
                    kind = 'solid' if is_solidmodel else 'local'
                    with self._lock:
                        self._pending[req_id] = (kind, str(local_cache))
                    return
            with self._lock:
                self._pending[req_id] = ('cdn', cdn_url)
            return

        # Any other extension
        if is_texpack and ext != '.ktx2':
            local_cache = APP_CACHE_DIR / f'{url_hash}{ext}'
            if _download_remote_file(cdn_url, local_cache, 'CDN TexPack Map'):
                # Divert back to local routing so it triggers KTX2 conversion!
                self._route_local(req_id, aid, str(local_cache), is_solidmodel, is_texpack=True)
                return

        with self._lock:
            self._pending[req_id] = ('cdn', cdn_url)
        log_buffer.log('CDN', f'Queued CDN redirect for {aid}')

    def _route_local(self, req_id: str, aid, local_path: str, is_solidmodel: bool, is_texpack: bool = False) -> None:
        path = Path(local_path)
        ext = path.suffix.lower()
        
        # Isolate KTX2 explicit conversion only to TexturePack image replacements
        if is_texpack and ext != '.ktx2' and ext != '.ktx':
            try:
                from ...cache.tools.image_to_ktx2.converter import get_or_create_ktx2_from_image
                converted_path = get_or_create_ktx2_from_image(path)
                if converted_path:
                    local_path = str(converted_path)
                    path = converted_path
                    ext = path.suffix.lower()
            except Exception as e:
                log_buffer.log('Local', f'Failed to convert {path.name} to KTX2: {e}')

        if is_solidmodel:
            if ext == '.obj':
                with self._lock:
                    self._pending[req_id] = ('solid', local_path)
            elif ext == '.mesh':
                obj = _try_mesh_to_obj(path, f'SolidModel {aid}')
                val = ('solid', str(obj)) if obj else ('local', local_path)
                with self._lock:
                    self._pending[req_id] = val
            elif ext == '.bin':
                # Only try conversion if it's actually a CSGMDL
                if _is_csgmdl_bin(path):
                    obj = _try_bin_to_obj(path, f'SolidModel {aid}')
                    val = ('solid', str(obj)) if obj else ('local', local_path)
                else:
                    # Not a CSGMDL, serve as-is
                    val = ('local', local_path)
                with self._lock:
                    self._pending[req_id] = val
            else:
                with self._lock:
                    self._pending[req_id] = ('local', local_path)
        else:
            if ext == '.bin':
                # Only try conversion if it's actually a CSGMDL
                if _is_csgmdl_bin(path):
                    obj = _try_bin_to_obj(path, f'Mesh {aid}')
                    if obj:
                        with self._lock:
                            self._pending[req_id] = ('local', str(obj))
                    else:
                        log_buffer.log('Local', f'Failed to convert CSGMDL for {aid}, serving .bin as-is')
                        with self._lock:
                            self._pending[req_id] = ('local', local_path)
                else:
                    # Not a CSGMDL, serve as-is
                    log_buffer.log('Local', f'Queued local .bin for {aid} (not CSGMDL)')
                    with self._lock:
                        self._pending[req_id] = ('local', local_path)
            else:
                with self._lock:
                    self._pending[req_id] = ('local', local_path)
                log_buffer.log('Local', f'Queued local for {aid}')

    def _should_remove(self, e: dict, removals: set) -> bool:
        aid = e.get('assetId')
        # Slot-specific check using fidelity-based map_index
        map_index = self._get_texpack_map_index(e)
        if aid is not None and map_index is not None:
            if f'{aid}:{map_index}' in removals:
                return True
        if aid in removals:
            return True
        at_id = e.get('assetTypeId')
        if at_id is not None and at_id in removals:
            return True
        at_name = e.get('assetType')
        if at_name:
            if at_name in removals:
                return True
            if self._REVERSE.get(str(at_name).lower()) in removals:
                return True
        return False

    def _get_type_keys(self, e: dict) -> list:
        keys = []
        at_id = e.get('assetTypeId')
        if at_id is not None:
            keys.append(at_id)
        at_name = e.get('assetType')
        if at_name:
            keys.append(at_name)
            mapped = self._REVERSE.get(str(at_name).lower())
            if mapped is not None:
                keys.append(mapped)
        return keys

    @staticmethod
    def _get_texpack_map_index(e: dict) -> int | None:
        """Return texture map slot index from the batch item's fidelity field.

        Roblox encodes the slot as a base64 fidelity value inside
        ``contentRepresentationPriorityList``.  The slot index lives in the
        low 6 bits of the first decoded byte:
          0 = Color / Albedo
          1 = Normal
          2 = Roughness / Metalness
        Higher bits encode the quality/LOD level (1, 2, 3).
        """
        crpl = e.get('contentRepresentationPriorityList')
        if not crpl:
            return None
        try:
            import base64 as _b64
            import json as _json
            decoded = _json.loads(_b64.b64decode(crpl))
            if not isinstance(decoded, list) or not decoded:
                return None
            fidelity_b64 = decoded[0].get('fidelity')
            if not fidelity_b64:
                return None
            fb = _b64.b64decode(fidelity_b64)
            return fb[0] & 0x3F
        except Exception:
            return None
