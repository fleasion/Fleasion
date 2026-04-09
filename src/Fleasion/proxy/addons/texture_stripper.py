"""TextureStripper: batch request/response modifier and CDN redirect manager.

All cross-connection state is held at the class level (singleton dicts) behind a
threading.Lock so it is safely shared across all MITM thread-pool workers.
"""

import gzip
import io
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

    # Animation type IDs (main + all subtypes)
    _ANIM_TYPE_IDS: frozenset = frozenset({24, 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 78})
    _CONV_CACHE_DIR: Path = APP_CACHE_DIR / 'rig_converted'

    # Virtual rig-filter type keys -> required original rig ('R6', 'R15', 'unknown')
    _VIRTUAL_ANIM_RIG: Dict[str, str] = {
        'R6Animation':        'R6',
        'R15Animation':       'R15',
        'NonPlayerAnimation': 'unknown',
    }

    # rig of replacement local file, keyed by normalised path string
    _anim_repl_rig: Dict[str, str] = {}
    # converted file path, keyed by f'{content_hash16}_{target_rig}'
    _anim_conv_paths: Dict[str, str] = {}
    # CDN URLs for animation replacements that need upstream rig detection before serving.
    # Populated by process_batch_response; checked by check_cdn_request.
    # These do NOT short-circuit - upstream response is read to detect original rig.
    # Value: (local_path, required_rig) where required_rig is 'R6'|'R15'|'unknown'|'any'
    _anim_rig_local: Dict[str, Tuple[str, str]] = {}   # base_cdn_url -> (local_path, required_rig)
    # pending_key -> (local_path, required_rig)
    _anim_local_pending: Dict[str, Tuple[str, str]] = {}
    # separate lock for rig-conversion state (avoids holding _lock during file I/O)
    _anim_lock: Lock = Lock()

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
                # 200 — publicly accessible.
                # Always save to disk so the rig-conversion path can intercept
                # the CDN response instead of a raw ID swap.
                try:
                    local_path.write_bytes(_data)
                    self._predownloaded[int(target_id)] = str(local_path)
                    log_buffer.log('Replacer', f'Cached public asset {target_id} for rig conversion ({len(_data)} bytes)')
                except Exception:
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

    # Rig auto-conversion helpers

    def _is_anim_asset_id(self, asset_id: int) -> bool:
        """Check whether an asset ID is an animation type via the economy API."""
        scraper = self._cache_scraper
        if scraper is None:
            return False
        try:
            cookie = scraper._get_roblosecurity()
            extra = {'Cookie': f'.ROBLOSECURITY={cookie};'} if cookie else {}
            data = scraper._https_get(
                'economy.roblox.com',
                f'/v2/assets/{asset_id}/details',
                extra_headers=extra or None,
            )
            if data:
                import json as _json
                info = _json.loads(data)
                return int(info.get('AssetTypeId', -1)) in self._ANIM_TYPE_IDS
        except Exception:
            pass
        return False

    def _is_anim_replacement_key(self, key) -> bool:
        """Return True if this local-replacement key targets an animation asset."""
        # All animation-related string keys: virtual rig-filter types + every
        # named animation asset type from ASSET_TYPES
        _ANIM_STR_KEYS = frozenset(
            name for tid, name in self.ASSET_TYPES.items() if tid in self._ANIM_TYPE_IDS
        ) | frozenset(self._VIRTUAL_ANIM_RIG)
        if isinstance(key, str) and key in _ANIM_STR_KEYS:
            return True
        # TexturePack slot keys (e.g. "12345:2") are never animations
        if isinstance(key, str) and ':' in key:
            return False
        # Numeric asset ID — look up via economy API
        try:
            aid = int(key)
        except (TypeError, ValueError):
            return False
        return self._is_anim_asset_id(aid)

    def precheck_anim_rigs(self) -> None:
        from ...utils.anim_converter import detect_rig

        try:
            self._CONV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        replacements_tuple = self.config_manager.get_all_replacements()
        _, _, _, local_replacements = replacements_tuple

        _ANIM_EXTS = {'.rbxm', '.rbxmx'}

        # Collect local paths that are definitely animation replacements.
        # Check: animation key/type, .rbxm/.rbxmx extension, or asset type via API.
        paths_to_process: list[str] = []

        for key, local_path in local_replacements.items():
            local_path = str(local_path)
            if local_path in paths_to_process:
                continue
            if (Path(local_path).suffix.lower() in _ANIM_EXTS
                    or self._is_anim_replacement_key(key)):
                paths_to_process.append(local_path)

        # Predownloaded ID-to-ID replacements: check replacement asset ID via API,
        # then fall back to extension and magic-byte sniffing.
        for repl_id, local_path in self._predownloaded.items():
            local_path = str(local_path)
            if local_path in paths_to_process:
                continue
            if Path(local_path).suffix.lower() in _ANIM_EXTS:
                paths_to_process.append(local_path)
                continue
            # Check the replacement asset ID itself via economy API
            try:
                if self._is_anim_asset_id(int(repl_id)):
                    paths_to_process.append(local_path)
                    continue
            except (TypeError, ValueError):
                pass
            # Last resort: peek at magic bytes for .dat / extensionless files
            try:
                head = Path(local_path).read_bytes()[:64]
                if (head.startswith(b'<roblox!')
                        or b'KeyframeSequence' in head
                        or b'CurveAnimation' in head):
                    paths_to_process.append(local_path)
            except Exception:
                pass

        converted = 0
        for local_path in paths_to_process:
            p = Path(local_path)
            if not p.exists():
                continue
            try:
                data = p.read_bytes()
            except Exception:
                continue

            # Only process animation files
            rig = detect_rig(data)
            if rig == 'unknown':
                continue

            with self._anim_lock:
                self._anim_repl_rig[local_path] = rig

            # Pre-create the opposite rig KeyframeSequence version
            for target_rig in ('R6', 'R15'):
                if target_rig == rig:
                    continue
                self._get_or_create_converted(local_path, target_rig, data=data)

            # Pre-create CurveAnimation versions for both rigs (needed when the CDN
            # asset is a CurveAnimation — we must serve back a CurveAnimation)
            for target_rig in ('R6', 'R15'):
                self._get_or_create_converted_curve(local_path, target_rig, data=data)
            converted += 1

        log_buffer.log('AnimConv', f'Pre-conversion complete: {converted} animation(s) processed')

    def _get_or_create_converted(self, local_path: str, target_rig: str,
                                  data: bytes | None = None) -> Optional[str]:
        """Return path to a rig-converted copy of local_path, creating it if needed."""
        import xml.etree.ElementTree as ET

        p = Path(local_path)
        if not p.exists():
            return None

        try:
            if data is None:
                data = p.read_bytes()
            content_key = hashlib.sha256(data).hexdigest()[:16]
        except Exception:
            return None

        cache_key = f'{content_key}_{target_rig}'

        with self._anim_lock:
            if cache_key in self._anim_conv_paths:
                cp = Path(self._anim_conv_paths[cache_key])
                if cp.exists():
                    return str(cp)

        # Build converted file path
        out_path = self._CONV_CACHE_DIR / f'{cache_key}.rbxmx'
        if out_path.exists():
            with self._anim_lock:
                self._anim_conv_paths[cache_key] = str(out_path)
            return str(out_path)

        try:
            self._CONV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            # Convert binary .rbxm to XML if needed
            from ...utils.anim_converter import rbxm_to_rbxmx
            if data[:8] == b'<roblox!':
                xml_data = rbxm_to_rbxmx(data)
            else:
                xml_data = data

            # If it's a CurveAnimation, convert to KeyframeSequence first
            if b'CurveAnimation' in xml_data:
                from ...utils.r15_to_r6 import curve_anim_to_keyframe_xml
                xml_data = curve_anim_to_keyframe_xml(xml_data)

            # Use the same conversion logic as the misc tab
            from ...utils.r15_to_r6 import (convert_keyframe_r15_to_r6,
                                             convert_keyframe_r6_to_r15, sanitize_xml)
            from ...utils.rig_data import R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS

            root = ET.fromstring(sanitize_xml(xml_data))
            etree = ET.ElementTree(root)

            ks = root.find("Item[@class='KeyframeSequence']")
            if ks is None:
                raise ValueError('No KeyframeSequence found')
            keyframes = ks.findall("Item[@class='Keyframe']")
            if not keyframes:
                raise ValueError('No Keyframes found')

            if target_rig == 'R6':
                for kf in keyframes:
                    convert_keyframe_r15_to_r6(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)
            else:
                for kf in keyframes:
                    convert_keyframe_r6_to_r15(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)

            etree.write(str(out_path), encoding='utf-8', xml_declaration=True)
            with self._anim_lock:
                self._anim_conv_paths[cache_key] = str(out_path)
            log_buffer.log('AnimConv', f'Created {target_rig} version: {out_path.name}')
            return str(out_path)
        except Exception as exc:
            log_buffer.log('AnimConv', f'Conversion failed for {p.name} -> {target_rig}: {exc}')
            return None

    def _get_or_create_converted_curve(self, local_path: str, target_rig: str,
                                        data: bytes | None = None) -> Optional[str]:
        """Return path to a rig-converted CurveAnimation copy of local_path, creating it if needed.

        Pipeline: source -> XML (if binary) -> KeyframeSequence (if CurveAnimation)
                  -> rig-convert if needed -> CurveAnimation
        """
        p = Path(local_path)
        if not p.exists():
            return None

        try:
            if data is None:
                data = p.read_bytes()
            content_key = hashlib.sha256(data).hexdigest()[:16]
        except Exception:
            return None

        cache_key = f'{content_key}_{target_rig}_curve'

        with self._anim_lock:
            if cache_key in self._anim_conv_paths:
                cp = Path(self._anim_conv_paths[cache_key])
                if cp.exists():
                    return str(cp)

        out_path = self._CONV_CACHE_DIR / f'{cache_key}.rbxmx'
        if out_path.exists():
            with self._anim_lock:
                self._anim_conv_paths[cache_key] = str(out_path)
            return str(out_path)

        try:
            self._CONV_CACHE_DIR.mkdir(parents=True, exist_ok=True)

            # Step 1: Convert binary .rbxm -> XML if needed
            from ...utils.anim_converter import rbxm_to_rbxmx, detect_rig
            xml_data = rbxm_to_rbxmx(data) if data[:10].startswith(b'<roblox!\x89\xff') else data

            # Step 2: Convert CurveAnimation -> KeyframeSequence if needed
            if b'CurveAnimation' in xml_data:
                from ...utils.r15_to_r6 import curve_anim_to_keyframe_xml
                xml_data = curve_anim_to_keyframe_xml(xml_data)

            # Step 3: Rig-convert if source rig differs from target rig
            import xml.etree.ElementTree as ET
            from ...utils.r15_to_r6 import (convert_keyframe_r15_to_r6,
                                             convert_keyframe_r6_to_r15, sanitize_xml)
            from ...utils.rig_data import R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS

            src_rig = detect_rig(xml_data)
            if src_rig != 'unknown' and src_rig != target_rig:
                root = ET.fromstring(sanitize_xml(xml_data))
                ks = root.find("Item[@class='KeyframeSequence']")
                if ks is None:
                    raise ValueError('No KeyframeSequence found after curve conversion')
                keyframes = ks.findall("Item[@class='Keyframe']")
                if not keyframes:
                    raise ValueError('No Keyframes found after curve conversion')
                if target_rig == 'R6':
                    for kf in keyframes:
                        convert_keyframe_r15_to_r6(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)
                else:
                    for kf in keyframes:
                        convert_keyframe_r6_to_r15(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)
                buf = io.BytesIO()
                ET.ElementTree(root).write(buf, encoding='utf-8', xml_declaration=True)
                xml_data = buf.getvalue()

            # Step 4: Convert KeyframeSequence -> CurveAnimation
            from ...utils.r15_to_r6 import keyframe_to_curve_anim
            curve_data = keyframe_to_curve_anim(xml_data)

            out_path.write_bytes(curve_data)
            with self._anim_lock:
                self._anim_conv_paths[cache_key] = str(out_path)
            log_buffer.log('AnimConv', f'Created {target_rig} CurveAnimation version: {out_path.name}')
            return str(out_path)
        except Exception as exc:
            log_buffer.log('AnimConv', f'CurveAnim conversion failed for {p.name} -> {target_rig}: {exc}')
            return None

    def _detect_repl_rig(self, local_path: str) -> str:
        """Detect and cache the rig type of a local replacement animation file."""
        with self._anim_lock:
            if local_path in self._anim_repl_rig:
                return self._anim_repl_rig[local_path]
        try:
            from ...utils.anim_converter import detect_rig
            rig = detect_rig(Path(local_path).read_bytes())
        except Exception:
            rig = 'unknown'
        with self._anim_lock:
            self._anim_repl_rig[local_path] = rig
        return rig

    def _is_anim_entry(self, e: dict) -> bool:
        """Return True if batch entry is an animation asset type."""
        tid = e.get('assetTypeId')
        if tid in self._ANIM_TYPE_IDS:
            return True
        at_name = str(e.get('assetType', '')).lower()
        mapped = self._REVERSE.get(at_name)
        return mapped in self._ANIM_TYPE_IDS

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

        # Convert TexturePack slot removals to blank-placeholder local routes.
        # Dropping a slot from the batch breaks the entire TexturePack in Roblox;
        # serving a 1×1 blank KTX2 keeps the pack intact for the other slots.
        # Matches "parentId:mapIndex" and wildcard "TexturePack:N" removal keys.
        if removals:
            tp_slot_removals = {
                r for r in removals
                if (isinstance(r, str) and ':' in r
                    and r.split(':', 1)[1].isdigit()
                    and (r.split(':', 1)[0].isdigit() or r.split(':', 1)[0] == 'TexturePack'))
            }
            if tp_slot_removals:
                blank = self._get_blank_ktx2_path()
                if blank:
                    removals = set(removals) - tp_slot_removals
                    local_replacements = dict(local_replacements)
                    for r in tp_slot_removals:
                        local_replacements[r] = blank
                    log_buffer.log('TexPack', f'Routing {len(tp_slot_removals)} slot removal(s) to blank placeholder')

        orig_len = len(data)
        data = [e for e in data if isinstance(e, dict) and not self._should_remove(e, removals)]
        if len(data) < orig_len:
            log_buffer.log('Remover', f'Removed {orig_len - len(data)} asset(s)')
            modified = True

        # Pre-build ORM channel override index.
        # Global indices 2-5 in local_replacements map to ORM sub-channels:
        #   GI2 = Metalness (ORM.R), GI3 = Roughness (ORM.G),
        #   GI4 = Emissive  (ORM.B), GI5 = Height    (ORM.A).
        # All route through the ORM compositor targeting Roblox fidelity slot 2
        # (the combined ORM CDN request).  The mapping is GLOBAL and FIXED —
        # it does NOT depend on the per-asset XML tag ordering.
        _GLOBAL_INDEX_CHANNEL = {2: 'metalness', 3: 'roughness', 4: 'emissive', 5: 'height'}
        # _orm_overrides: pack_id_or_'TexturePack' -> {channel_name: local_path}
        _orm_overrides: dict[int | str, dict[str, str | None]] = {}
        # Scan both cdn_replacements and local_replacements for GI≥2 keys.
        # local_replacements is processed last so it wins on key collisions.
        _vs2_sources: dict = {**cdn_replacements, **local_replacements}
        for _ck, _cv in _vs2_sources.items():
            if not isinstance(_ck, str) or ':' not in _ck:
                continue
            _pk, _gi_str = _ck.split(':', 1)
            if not _gi_str.isdigit():
                continue
            _gi = int(_gi_str)
            if _gi < 2:
                continue  # GI0/GI1 are full-slot; handled by normal local_key routing
            _ch = _GLOBAL_INDEX_CHANNEL.get(_gi)
            if not _ch:
                continue
            _pk_key: int | str = int(_pk) if _pk.isdigit() else _pk
            # KTX2/KTX paths (e.g. blank placeholder) are not valid PNG sources;
            # treat as None = zero out this channel with _CHANNEL_ZERO defaults.
            _cv_resolved: str | None = (
                None if (_cv is not None and _cv.lower().endswith(('.ktx2', '.ktx'))) else _cv
            )
            _orm_overrides.setdefault(_pk_key, {})[_ch] = _cv_resolved

        for idx, e in enumerate(data):
            if not isinstance(e, dict):
                continue
            aid = e.get('assetId')
            req_id = e.get('requestId')
            type_keys = self._get_type_keys(e)
            is_solidmodel = (e.get('assetTypeId') == 39) or (
                self._REVERSE.get(str(e.get('assetType', '')).lower()) == 39
            )

            # Build slot key from the fidelity field in contentRepresentationPriorityList.
            # slot_key = "assetId:mapIndex" (e.g. "7547298786:1" for the normal-map slot).
            # wildcard_key = "TexturePack:N" matches the N-th slot of ANY TexturePack.
            map_index = self._get_texpack_map_index(e)
            slot_key = f'{aid}:{map_index}' if (aid is not None and map_index is not None) else None
            wildcard_key = f'TexturePack:{map_index}' if map_index is not None else None

            # ID/type replacement — slot-specific match takes priority over whole-asset
            matched = None
            if slot_key and slot_key in replacements:
                matched = slot_key
            elif wildcard_key and wildcard_key in replacements:
                matched = wildcard_key
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

            # CDN / local routing — slot key / wildcard key takes priority
            if req_id and aid:
                # ── ORM channel compositing (virtual slots 2-5) ──────────────────
                # When the fidelity slot is 2 (ORM) and per-channel PNG overrides
                # are configured via VSN keys (N≥2), composite them into one texture.
                # This check runs BEFORE normal local_key routing so that e.g.
                # "packId:2 → metalness.png" is composited rather than served raw.
                if map_index == 2 and _orm_overrides:
                    _orm_chs: dict[str, str | None] = {}
                    # Wildcard always lowest priority
                    _orm_chs.update(_orm_overrides.get('TexturePack', {}))
                    # Pack-specific overrides win
                    if aid in _orm_overrides:
                        _orm_chs.update(_orm_overrides[aid])
                    if _orm_chs:
                        _comp = self._build_orm_composite(aid, _orm_chs)
                        if _comp:
                            self._route_local(f'{batch_id}_{req_id}', aid, _comp, is_solidmodel, is_texpack=True)
                            modified = True
                            continue
                        # Composite failed — fall through to normal routing as best-effort

                all_keys = ([slot_key] if slot_key else []) + ([wildcard_key] if wildcard_key else []) + [aid] + type_keys
                # For animation entries, also check virtual rig-filter keys as fallback
                if self._is_anim_entry(e):
                    all_keys = all_keys + [k for k in self._VIRTUAL_ANIM_RIG]

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
                    _repl_local_path = local_replacements[local_key]
                    self._route_local(f'{batch_id}_{req_id}', aid, _repl_local_path, is_solidmodel, is_texpack)
                    # Tag animation replacements for upstream rig detection.
                    # Determine required_rig from virtual type key(s), or 'any' for normal types.
                    if aid is not None and self._is_anim_entry(e):
                        if str(local_key) in self._VIRTUAL_ANIM_RIG:
                            # Collect all virtual keys in local_replacements pointing to the
                            # same file — user may have "R6Animation, R15Animation" in one rule.
                            _covered = frozenset(
                                self._VIRTUAL_ANIM_RIG[vk]
                                for vk in self._VIRTUAL_ANIM_RIG
                                if local_replacements.get(vk) == _repl_local_path
                            )
                            _required_rig = 'any' if _covered >= {'R6', 'R15', 'unknown'} else _covered
                        else:
                            _required_rig = 'any'
                        with self._anim_lock:
                            self._anim_local_pending[f'{batch_id}_{req_id}'] = (str(_repl_local_path), _required_rig)

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
                    # Check if this is a tagged animation replacement — if so, put it in
                    # _anim_rig_local so server.py reads the upstream CDN response first
                    # to detect the original rig, rather than short-circuiting immediately.
                    with self._anim_lock:
                        _anim_pending = self._anim_local_pending.pop(pending_key, None)
                    if _anim_pending is not None:
                        _anim_path, _required_rig = _anim_pending
                        self._anim_rig_local[base_loc] = (_anim_path, _required_rig)
                        log_buffer.log('AnimConv', f'Queued rig-detect for {base_loc[:60]}...')
                    else:
                        self._local_redirects[base_loc] = url_value
                        log_buffer.log('Local', f'Will serve local for {base_loc[:60]}...')
                elif url_type == 'solid':
                    self._solidmodel_injections[base_loc] = url_value
                    log_buffer.log('SolidModel', f'Will inject OBJ for {base_loc[:60]}...')

    # ------------------------------------------------------------------
    # CDN request check (called from server MITM thread for fts.rbxcdn.com)
    # ------------------------------------------------------------------

    def check_cdn_request(self, host: str, path: str) -> Optional[Tuple[str, str]]:
        """Returns ('local'|'cdn'|'solid'|'anim_rig', value) or None.

        'anim_rig' means: let the upstream CDN request proceed normally so server.py
        can read the original response bytes, detect the rig, then serve the
        rig-matched local replacement file instead.
        """
        base_url = f'https://{host}{path}'.split('?')[0]
        # Check animation rig-detect entries first (separate dict, no _lock needed here
        # since _anim_lock guards it; checked before _local_redirects so these never
        # accidentally land in the normal short-circuit path).
        with self._anim_lock:
            anim_entry = self._anim_rig_local.pop(base_url, None)
        if anim_entry is not None:
            return ('anim_rig', anim_entry)

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

    def _build_orm_composite(
        self, parent_id, channel_pngs: dict[str, str | None],
    ) -> Optional[str]:
        """Build (or retrieve from cache) a composite ORM KTX2 from per-channel PNGs.

        *parent_id* is the TexturePack asset ID.  *channel_pngs* maps channel
        name (``metalness``, ``roughness``, ``emissive``, ``height``) to local
        PNG file paths.  The baseline ORM KTX2 from texpack_slots/ is used if
        available so unspecified channels retain their CDN values.
        """
        try:
            from ...cache.tools.orm_compositor import composite_orm
            # Resolve CDN URLs to local cached files before compositing.
            # On download failure the channel is omitted so the baseline KTX2
            # value shows through, as if the user never replaced that channel.
            resolved: dict[str, str | None] = {}
            for _ch, _val in channel_pngs.items():
                if _val is not None and (str(_val).startswith('http://') or str(_val).startswith('https://')):
                    _url_hash = hashlib.md5(_val.encode()).hexdigest()[:16]
                    _ext = Path(urlparse(_val).path).suffix.lower() or '.png'
                    _cdn_cache = APP_CACHE_DIR / 'orm_cdn_cache' / f'{_url_hash}{_ext}'
                    _cdn_cache.parent.mkdir(parents=True, exist_ok=True)
                    if not _cdn_cache.exists():
                        if not _download_remote_file(_val, _cdn_cache, f'ORM channel {_ch}'):
                            log_buffer.log('ORM', f'CDN download failed for channel {_ch} — using original')
                            continue  # skip channel; baseline value preserved
                    resolved[_ch] = str(_cdn_cache)
                else:
                    resolved[_ch] = _val
            baseline = APP_CACHE_DIR / 'texpack_slots' / f'{parent_id}_slot2.ktx2'
            result = composite_orm(
                baseline=(baseline if baseline.exists() else None),
                channels={k: (Path(v) if v is not None else None) for k, v in resolved.items()},
                cache_dir=APP_CACHE_DIR,
            )
            return result
        except Exception as exc:
            log_buffer.log('ORM', f'Composite failed for pack {parent_id}: {exc}')
            return None

    def _should_remove(self, e: dict, removals: set) -> bool:
        aid = e.get('assetId')
        # Slot-specific check using fidelity-based map_index
        map_index = self._get_texpack_map_index(e)
        if aid is not None and map_index is not None:
            if f'{aid}:{map_index}' in removals:
                return True
            # Wildcard TexturePack:N removal
            if f'TexturePack:{map_index}' in removals:
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

    @staticmethod
    def _get_blank_ktx2_path() -> str | None:
        """Return a path to a 1×1 white RGBA KTX2 placeholder texture.

        Created on first call and cached in APP_CACHE_DIR.  Used to fill
        TexturePack slots that the user has set to "Nothing" so the rest of
        the pack keeps loading normally.
        """
        import struct as _struct, zlib as _zl
        png_path = APP_CACHE_DIR / '_blank_texpack.png'
        if not png_path.exists():
            try:
                def _chunk(ctype: bytes, data: bytes) -> bytes:
                    body = ctype + data
                    return _struct.pack('>I', len(data)) + body + _struct.pack('>I', _zl.crc32(body) & 0xFFFFFFFF)
                sig   = b'\x89PNG\r\n\x1a\n'
                ihdr  = _chunk(b'IHDR', _struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))  # 1×1 RGBA
                idat  = _chunk(b'IDAT', _zl.compress(b'\x00\xff\xff\xff\xff', 9))        # white, opaque
                iend  = _chunk(b'IEND', b'')
                png_path.write_bytes(sig + ihdr + idat + iend)
                log_buffer.log('TexPack', 'Created blank 1×1 placeholder PNG')
            except Exception as exc:
                log_buffer.log('TexPack', f'Failed to create blank placeholder PNG: {exc}')
                return None
        try:
            from ...cache.tools.image_to_ktx2.converter import get_or_create_ktx2_from_image
            ktx_path = get_or_create_ktx2_from_image(png_path)
            if ktx_path and ktx_path.exists():
                return str(ktx_path)
        except Exception as exc:
            log_buffer.log('TexPack', f'Failed to convert blank placeholder to KTX2: {exc}')
        return None

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
        """Return texture map fidelity slot index from the batch item.

        Roblox encodes the slot as a base64 fidelity value inside
        ``contentRepresentationPriorityList``.  The fidelity byte encodes:
          bits 0–5 (& 0x3F): slot index
          bits 6–7 (>> 6):   quality/LOD level (0=low … 3=ultra)

        Roblox fidelity slot values (empirically verified):
          0 = Color / Albedo
          1 = Normal
          2 = ORM (combined Metalness-Roughness-Emissive-Height)

        Fleasion global indices (fixed, asset-independent):
          0 = Color       (fidelity 0, full slot)
          1 = Normal      (fidelity 1, full slot)
          2 = Metalness   (fidelity 2, ORM R channel)
          3 = Roughness   (fidelity 2, ORM G channel)
          4 = Emissive    (fidelity 2, ORM B channel)
          5 = Height      (fidelity 2, ORM A channel)

        This method returns the RAW fidelity index (0, 1, or 2).
        Global indices 2–5 are resolved via ``_GLOBAL_INDEX_CHANNEL``
        in the ORM compositor path.
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
            if not fb:
                return None
            return fb[0] & 0x3F
        except Exception:
            return None
