"""Texture stripper addon for mitmproxy."""

import gzip
import json
import xml.etree.ElementTree as ET
import urllib.request
import hashlib
import shutil
from pathlib import Path
from urllib.parse import urlparse

from mitmproxy import http

from ...utils import APP_CACHE_DIR, PROXY_TARGET_HOST, STRIPPABLE_ASSET_TYPES, log_buffer


# ---------------------------------------------------------------------------
# SolidModel OBJ injection helper
# ---------------------------------------------------------------------------

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'  # Zstandard frame magic (little-endian 0xFD2FB528)
_GZIP_MAGIC  = b'\x1f\x8b'          # gzip magic


def _decompress_cdn_response(data: bytes) -> bytes:
    """Strip application-level zstd or gzip wrapping from a CDN payload."""
    if data[:4] == _ZSTD_MAGIC:
        import zstandard  # type: ignore[import-untyped]
        data = zstandard.ZstdDecompressor().decompress(
            data, max_output_size=64 * 1024 * 1024
        )
        log_buffer.log('CDN', f'Decompressed zstd CDN payload: {len(data)} bytes')
    elif data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
        log_buffer.log('CDN', f'Decompressed gzip CDN payload: {len(data)} bytes')
    return data


def _inject_obj_into_solidmodel(bin_data: bytes, obj_path: Path) -> bytes:
    """Replace the MeshData of a SolidModel CDN asset with geometry from an OBJ file.

    The CDN delivers SolidModels as binary RBXM with class ``PartOperationAsset``.
    That class name must be preserved exactly — the engine accepts it from CDN
    binary assets.  We decompress, inject the new MeshData, and re-serialize
    as binary RBXM without touching the class name.
    """
    from ...cache.tools.solidmodel_converter.obj_to_csg import export_csg_mesh
    from ...cache.tools.solidmodel_converter.converter import deserialize_rbxm
    from ...cache.tools.solidmodel_converter.rbxm.serializer import write_rbxm
    from ...cache.tools.solidmodel_converter.rbxm.types import PropertyFormat, RbxProperty

    bin_data = _decompress_cdn_response(bin_data)
    doc = deserialize_rbxm(bin_data)
    csg_bytes = export_csg_mesh(obj_path)

    _INJECTABLE = frozenset({'PartOperationAsset', 'UnionOperation', 'NegateOperation', 'PartOperation'})

    injected = 0
    for inst in doc.roots:
        if inst.class_name in _INJECTABLE:
            inst.properties['MeshData'] = RbxProperty(
                name='MeshData',
                fmt=PropertyFormat.STRING,
                value=csg_bytes,
            )
            # Force Part.Color to white so vertex colors from the OBJ are
            # rendered as-is.  Roblox multiplies Part.Color × vertex color,
            # so any non-white Part.Color would tint the injected geometry.
            # BasePart.Color is serialized as COLOR3UINT8 (fmt=26, 0-255 ints),
            # NOT as COLOR3 (fmt=12, interleaved floats) — wrong format is silently
            # discarded by the engine, leaving the scene's existing Part.Color intact.
            inst.properties['Color'] = RbxProperty(
                name='Color',
                fmt=PropertyFormat.COLOR3UINT8,
                value={'R': 255, 'G': 255, 'B': 255},
            )
            injected += 1

    if injected == 0:
        raise ValueError(
            f'No injectable root found (roots: {[r.class_name for r in doc.roots]})'
        )

    log_buffer.log('SolidModel', f'Injected CSGMDL into {injected} root(s)')
    return write_rbxm(doc)


# ---------------------------------------------------------------------------
# Intermediary conversion helpers (lazy — imported on first use)
# ---------------------------------------------------------------------------

def _try_mesh_to_obj(path: Path, context: str) -> Path | None:
    """Convert a local .mesh file to a cached OBJ.  Returns None on failure."""
    try:
        from ...cache.tools.solidmodel_converter.mesh_intermediary import mesh_file_to_cached_obj
        return mesh_file_to_cached_obj(path)
    except Exception as exc:
        log_buffer.log('Intermediary', f'{context}: .mesh→OBJ failed: {exc}')
        return None


def _try_bin_to_obj(path: Path, context: str) -> Path | None:
    """Convert a local .bin (binary CSG RBXM) to a cached OBJ.  Returns None on failure."""
    try:
        from ...cache.tools.solidmodel_converter.mesh_intermediary import bin_file_to_cached_obj
        return bin_file_to_cached_obj(path)
    except Exception as exc:
        log_buffer.log('Intermediary', f'{context}: .bin→OBJ failed: {exc}')
        return None


def _download_remote_file(url: str, dest: Path, label: str) -> bool:
    """Download *url* to *dest*.  Returns True on success, False on error."""
    try:
        APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return True  # already cached

        log_buffer.log('Downloader', f'Downloading remote {label}: {url}')
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp, \
                open(dest, 'wb') as out_file:
            shutil.copyfileobj(resp, out_file)

        log_buffer.log('Downloader', f'Saved {label} to cache: {dest.name}')
        return True
    except Exception as exc:
        log_buffer.log('Downloader', f'Failed to download {label}: {exc}')
        return False


class TextureStripper:
    """Mitmproxy addon that strips textures and performs asset replacements."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        # Maps flow ID to requestId we're tracking
        self.pending_requests: dict[str, tuple[str, str, str]] = {}  # flow_id -> (requestId, url_type, url_value)
        # Maps CDN URLs to replacement URLs/paths
        self.cdn_redirects: dict[str, str] = {}
        self.local_redirects: dict[str, str] = {}
        # Maps CDN URLs to local .obj paths for SolidModel injection
        # (the original .bin is fetched from CDN and the OBJ mesh is injected
        # into it on the fly before forwarding the response to Roblox)
        self.solidmodel_injections: dict[str, str] = {}
        # Cache replacement rules per flow to avoid multiple disk reads
        self._replacements_cache: dict[str, tuple] = {}  # flow_id -> (replacements, removals, cdn, local)
        # Import to get access to ASSET_TYPES mappings
        self.asset_types_mapping = {
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
        # Reverse mapping: "Mesh" -> 4 (lowercased key)
        self.reverse_asset_types_mapping = {name.lower(): tid for tid, name in self.asset_types_mapping.items()}

    @staticmethod
    def _decode(content: bytes, enc: str):
        """Decode content based on encoding."""
        if enc == 'gzip':
            content = gzip.decompress(content)
        return json.loads(content)

    @staticmethod
    def _encode(data, enc: str) -> bytes:
        """Encode data based on encoding."""
        raw = json.dumps(data, separators=(',', ':')).encode()
        return gzip.compress(raw) if enc == 'gzip' else raw

    def _get_replacements(self, flow_id: str) -> tuple:
        """Get cached replacement rules for a flow, or load from disk if not cached."""
        if flow_id not in self._replacements_cache:
            self._replacements_cache[flow_id] = self.config_manager.get_all_replacements()
        return self._replacements_cache[flow_id]

    def _clear_flow_cache(self, flow_id: str):
        """Clear cached replacements for a completed flow."""
        self._replacements_cache.pop(flow_id, None)

    def _route_local_replacement(
        self,
        flow_id: str,
        req_id: str,
        aid,
        local_path: str,
        is_solidmodel: bool,
    ) -> None:
        """Determine the correct pending-request entry for a local file replacement.

        Handles the following cases transparently:

        ┌──────────────┬──────────┬────────────────────────────────────────────────────────────┐
        │ Asset type   │ Ext      │ Strategy                                                    │
        ├──────────────┼──────────┼────────────────────────────────────────────────────────────┤
        │ SolidModel   │ .obj     │ solidmodel_obj injection (existing, unchanged)              │
        │ SolidModel   │ .mesh    │ .mesh → OBJ intermediary → solidmodel_obj injection         │
        │ SolidModel   │ .bin     │ .bin → OBJ intermediary → solidmodel_obj injection          │
        │ Mesh / other │ .bin     │ .bin → OBJ intermediary → local (OBJ→.mesh auto-converts)  │
        │ Mesh / other │ anything │ local (existing path; OBJ→.mesh handled in local handler)  │
        └──────────────┴──────────┴────────────────────────────────────────────────────────────┘
        """
        path = Path(local_path)
        ext = path.suffix.lower()
        key = f'{flow_id}_{req_id}'

        if is_solidmodel:
            if ext == '.obj':
                # Existing direct injection path — no conversion needed
                self.pending_requests[key] = (req_id, 'solidmodel_obj', local_path)
                log_buffer.log('SolidModel', f'Tracking SolidModel {aid} for OBJ injection')

            elif ext == '.mesh':
                # .mesh → OBJ intermediary → inject into CDN SolidModel RBXM
                obj_path = _try_mesh_to_obj(path, f'SolidModel {aid}')
                if obj_path:
                    self.pending_requests[key] = (req_id, 'solidmodel_obj', str(obj_path))
                    log_buffer.log('SolidModel', f'Tracking SolidModel {aid} for .mesh→OBJ injection')
                else:
                    log_buffer.log('SolidModel', f'Skipping SolidModel {aid}: .mesh→OBJ conversion failed')

            elif ext == '.bin':
                # binary CSG RBXM → OBJ intermediary → inject into CDN SolidModel RBXM
                obj_path = _try_bin_to_obj(path, f'SolidModel {aid}')
                if obj_path:
                    self.pending_requests[key] = (req_id, 'solidmodel_obj', str(obj_path))
                    log_buffer.log('SolidModel', f'Tracking SolidModel {aid} for .bin→OBJ injection')
                else:
                    log_buffer.log('SolidModel', f'Skipping SolidModel {aid}: .bin→OBJ conversion failed')

            else:
                # Unknown extension for SolidModel — fall through to local serving
                self.pending_requests[key] = (req_id, 'local', local_path)
                log_buffer.log('Local', f'Tracking SolidModel {aid} for local replacement (ext={ext})')

        else:
            # Non-SolidModel (Mesh, MeshPart, etc.)
            if ext == '.bin':
                # binary CSG RBXM → OBJ intermediary; the local handler will
                # auto-convert .obj → .mesh before serving it.
                obj_path = _try_bin_to_obj(path, f'Mesh {aid}')
                if obj_path:
                    self.pending_requests[key] = (req_id, 'local', str(obj_path))
                    log_buffer.log('Local', f'Tracking Mesh {aid} for .bin→OBJ→.mesh replacement')
                else:
                    log_buffer.log('Local', f'Skipping Mesh {aid}: .bin→OBJ conversion failed')
            else:
                # .mesh, .obj, or any other type — serve directly (existing handler
                # already auto-converts .obj → .mesh when the local file is served).
                self.pending_requests[key] = (req_id, 'local', local_path)
                log_buffer.log('Local', f'Tracking asset {aid} for local replacement')

    def _route_cdn_replacement(
        self,
        flow_id: str,
        req_id: str,
        aid,
        cdn_url: str,
        is_solidmodel: bool,
    ) -> None:
        """Determine the correct pending-request entry for a CDN URL replacement.

        For `.obj` URLs the existing download-and-cache logic is preserved
        exactly.  For `.mesh` and `.bin` URLs that require conversion, the
        remote file is downloaded to ``APP_CACHE_DIR`` and converted to an
        intermediary OBJ, which is then routed through the same injection /
        local-serve paths as local files.

        CDN `.mesh` links targeting a *non-SolidModel* asset (i.e. a regular
        Mesh/MeshPart) are handled by a direct CDN redirect — Roblox accepts
        `.mesh` files from CDN without any wrapping, so no download or
        conversion is required in that case.

        ┌──────────────┬──────────┬────────────────────────────────────────────────────────────┐
        │ Asset type   │ CDN ext  │ Strategy                                                    │
        ├──────────────┼──────────┼────────────────────────────────────────────────────────────┤
        │ SolidModel   │ .obj     │ download → cache → solidmodel_obj (existing)               │
        │ SolidModel   │ .mesh    │ download → cache → .mesh→OBJ → solidmodel_obj injection    │
        │ SolidModel   │ .bin     │ download → cache → .bin→OBJ  → solidmodel_obj injection    │
        │ Mesh / other │ .obj     │ download → cache → local (OBJ→.mesh auto)  (existing)      │
        │ Mesh / other │ .bin     │ download → cache → .bin→OBJ  → local (OBJ→.mesh auto)      │
        │ Mesh / other │ .mesh    │ CDN redirect (Roblox accepts .mesh directly — no conversion)│
        │ any          │ other    │ CDN redirect (existing)                                     │
        └──────────────┴──────────┴────────────────────────────────────────────────────────────┘
        """
        parsed_url = urlparse(str(cdn_url))
        cdn_ext = Path(parsed_url.path).suffix.lower()
        key = f'{flow_id}_{req_id}'
        url_hash = hashlib.md5(str(cdn_url).encode('utf-8')).hexdigest()

        # ── .obj (existing logic, preserved unchanged) ─────────────────────
        if cdn_ext == '.obj':
            local_cache_path = APP_CACHE_DIR / f'{url_hash}.obj'
            ok = _download_remote_file(cdn_url, local_cache_path, '.obj')
            if ok:
                if is_solidmodel:
                    self.pending_requests[key] = (req_id, 'solidmodel_obj', str(local_cache_path))
                    log_buffer.log('SolidModel', f'Tracking online SolidModel {aid} for local OBJ injection')
                else:
                    self.pending_requests[key] = (req_id, 'local', str(local_cache_path))
                    log_buffer.log('Local', f'Tracking online asset {aid} for local OBJ replacement')
            else:
                # Download failed — fall back to a plain CDN redirect
                self.pending_requests[key] = (req_id, 'cdn', cdn_url)
                log_buffer.log('CDN', f'Tracking asset {aid} for CDN redirect (OBJ download fallback)')
            return

        # ── .mesh ──────────────────────────────────────────────────────────
        if cdn_ext == '.mesh':
            if not is_solidmodel:
                # Roblox accepts .mesh directly from CDN — no conversion needed
                self.pending_requests[key] = (req_id, 'cdn', cdn_url)
                log_buffer.log('CDN', f'Tracking Mesh {aid} for direct CDN .mesh redirect')
                return

            # SolidModel needs .mesh → OBJ → CSG injection
            local_cache_path = APP_CACHE_DIR / f'{url_hash}.mesh'
            ok = _download_remote_file(cdn_url, local_cache_path, '.mesh')
            if ok:
                obj_path = _try_mesh_to_obj(local_cache_path, f'SolidModel CDN {aid}')
                if obj_path:
                    self.pending_requests[key] = (req_id, 'solidmodel_obj', str(obj_path))
                    log_buffer.log('SolidModel', f'Tracking online SolidModel {aid} for .mesh→OBJ injection')
                    return
            # Fallback
            self.pending_requests[key] = (req_id, 'cdn', cdn_url)
            log_buffer.log('CDN', f'Tracking SolidModel {aid} for CDN redirect (.mesh conversion fallback)')
            return

        # ── .bin (binary CSG RBXM) ─────────────────────────────────────────
        if cdn_ext == '.bin':
            local_cache_path = APP_CACHE_DIR / f'{url_hash}.bin'
            ok = _download_remote_file(cdn_url, local_cache_path, '.bin')
            if ok:
                obj_path = _try_bin_to_obj(local_cache_path, f'CDN {aid}')
                if obj_path:
                    if is_solidmodel:
                        self.pending_requests[key] = (req_id, 'solidmodel_obj', str(obj_path))
                        log_buffer.log('SolidModel', f'Tracking online SolidModel {aid} for .bin→OBJ injection')
                    else:
                        self.pending_requests[key] = (req_id, 'local', str(obj_path))
                        log_buffer.log('Local', f'Tracking online Mesh {aid} for .bin→OBJ→.mesh replacement')
                    return
            # Fallback
            self.pending_requests[key] = (req_id, 'cdn', cdn_url)
            log_buffer.log('CDN', f'Tracking asset {aid} for CDN redirect (.bin conversion fallback)')
            return

        # ── Any other extension — plain CDN redirect (unchanged) ──────────
        self.pending_requests[key] = (req_id, 'cdn', cdn_url)
        log_buffer.log('CDN', f'Tracking asset {aid} for CDN redirect')

    def request(self, flow: http.HTTPFlow):
        """Process request and apply modifications."""
        url = flow.request.pretty_url

        # Check for CDN redirect intercepts
        for cdn_url, redirect_url in list(self.cdn_redirects.items()):
            if cdn_url in url:
                log_buffer.log('CDN', f'Redirecting to: {redirect_url}')
                flow.response = http.Response.make(302, b'', {'Location': redirect_url})
                self.cdn_redirects.pop(cdn_url, None)
                return

        # Check for local file intercepts
        for cdn_url, local_path in list(self.local_redirects.items()):
            if cdn_url in url:
                try:
                    path = Path(local_path)

                    # Convert .obj to .mesh if necessary
                    if path.suffix.lower() == '.obj':
                        from ...cache.tools.solidmodel_converter.obj_to_mesh import get_or_create_mesh_from_obj
                        try:
                            path = get_or_create_mesh_from_obj(path)
                        except Exception as e:
                            log_buffer.log('Local', f'Error converting OBJ to Mesh: {e}')
                            # Fallback to original path just in case
                            path = Path(local_path)

                    if path.exists():
                        content = path.read_bytes()
                        # Determine content type from extension
                        ext = path.suffix.lower()
                        content_types = {
                            '.png': 'image/png',
                            '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg',
                            '.gif': 'image/gif',
                            '.webp': 'image/webp',
                            '.ogg': 'audio/ogg',
                            '.mp3': 'audio/mpeg',
                            '.wav': 'audio/wav',
                            '.rbxm': 'application/octet-stream',
                            '.rbxmx': 'application/xml',
                            '.mesh': 'application/octet-stream',
                        }
                        content_type = content_types.get(ext, 'application/octet-stream')
                        flow.response = http.Response.make(
                            200,
                            content,
                            {'Content-Type': content_type, 'Content-Length': str(len(content))}
                        )
                        log_buffer.log('Local', f'Served local file: {path.name}')
                    else:
                        log_buffer.log('Local', f'File not found: {local_path}')
                except OSError as e:
                    log_buffer.log('Local', f'Error reading file: {e}')
                self.local_redirects.pop(cdn_url, None)
                return

        # Process batch asset requests
        if (
            urlparse(url).hostname != PROXY_TARGET_HOST
            or not flow.request.raw_content
        ):
            return

        enc = flow.request.headers.get('Content-Encoding', '').lower()
        try:
            data = self._decode(flow.request.raw_content, enc)
        except (json.JSONDecodeError, gzip.BadGzipFile, OSError):
            return

        if not isinstance(data, list):
            return

        modified = False
        # Use cached replacements to avoid repeated disk I/O
        replacements, removals, cdn_replacements, local_replacements = self._get_replacements(flow.id)

        # Track asset IDs that need CDN/local replacement for response processing
        for e in data:
            if not isinstance(e, dict):
                continue
            aid = e.get('assetId')
            req_id = e.get('requestId')
            if aid and req_id:
                matched_key = aid
                if aid not in cdn_replacements and aid not in local_replacements:
                    type_keys = []
                    if (at_id := e.get('assetTypeId')) is not None:
                        type_keys.append(at_id)
                    if (at_name := e.get('assetType')):
                        type_keys.append(at_name)
                        if mapped_id := self.reverse_asset_types_mapping.get(str(at_name).lower()):
                            type_keys.append(mapped_id)

                    for tk in type_keys:
                        if tk in cdn_replacements or tk in local_replacements:
                            matched_key = tk
                            break

                # Determine asset type context for routing decisions
                asset_type_id = e.get('assetTypeId')
                at_name = str(e.get('assetType', '')).lower()
                mapped_type_id = self.reverse_asset_types_mapping.get(at_name)
                is_solidmodel = (asset_type_id == 39) or (mapped_type_id == 39)

                if matched_key in cdn_replacements:
                    cdn_url = cdn_replacements[matched_key]
                    self._route_cdn_replacement(
                        flow.id, req_id, aid, cdn_url, is_solidmodel
                    )

                elif matched_key in local_replacements:
                    local_path = local_replacements[matched_key]
                    self._route_local_replacement(
                        flow.id, req_id, aid, local_path, is_solidmodel
                    )

        # Remove assets
        original_len = len(data)

        def _should_remove(e: dict) -> bool:
            if not isinstance(e, dict):
                return False
            # Check explicit ID
            if e.get('assetId') in removals:
                return True
            # Check type ID / Name
            if (at_id := e.get('assetTypeId')) is not None and at_id in removals:
                return True
            if (at_name := e.get('assetType')):
                if at_name in removals:
                    return True
                if (mapped_id := self.reverse_asset_types_mapping.get(str(at_name).lower())) in removals:
                    return True
            return False

        data[:] = [e for e in data if not _should_remove(e)]

        if (removed := original_len - len(data)) > 0:
            log_buffer.log('Remover', f'Removed {removed} asset(s)')
            modified = True

        # Replace assets (ID/Type mode)
        def _get_type_ids(e: dict) -> list:
            ids = []
            if (at_id := e.get('assetTypeId')) is not None:
                ids.append(at_id)
            if (at_name := e.get('assetType')):
                ids.append(at_name)  # include raw string key e.g. "Mesh"
                # Map integer ID too
                if mapped_id := self.reverse_asset_types_mapping.get(str(at_name).lower()):
                    ids.append(mapped_id)
            return ids

        for e in data:
            if not isinstance(e, dict):
                continue

            aid = e.get('assetId')
            type_keys = _get_type_ids(e)

            # Asset replacement (ID and Type mode)
            matched_key = None
            if aid in replacements:
                matched_key = aid
            else:
                for tk in type_keys:
                    if tk in replacements:
                        matched_key = tk
                        break

            if matched_key is not None:
                e['assetId'] = replacements[matched_key]
                log_buffer.log('Replacer', f'Replaced {aid} -> {replacements[matched_key]}')
                modified = True

        if modified:
            flow.request.raw_content = self._encode(data, enc)
            flow.request.headers['Content-Length'] = str(len(flow.request.raw_content))

    def _modify_texturepack_xml(self, content: bytes, replacements: dict[int, int]) -> bytes | None:
        """Modify texturepack XML to replace nested asset IDs.

        Returns modified XML bytes if any changes were made, None otherwise.
        """
        try:
            xml_text = content.decode('utf-8', errors='replace')
            root = ET.fromstring(xml_text)

            modified = False
            for elem_name in ['color', 'normal', 'metalness', 'roughness', 'emissive']:
                node = root.find(elem_name)
                if node is not None and node.text:
                    try:
                        nested_id = int(str(node.text))
                        if nested_id in replacements:
                            new_id = replacements[nested_id]
                            node.text = str(new_id)
                            log_buffer.log('TexturePack', f'Replaced {elem_name} ID {nested_id} -> {new_id}')
                            modified = True
                    except (ValueError, TypeError):
                        pass

            # Return modified XML only if something changed
            if modified:
                return ET.tostring(root, encoding='unicode').encode('utf-8')
            return None
        except ET.ParseError:
            return None

    def response(self, flow: http.HTTPFlow):
        """Process response to capture CDN URLs for redirection."""
        url = flow.request.pretty_url

        # Handle texturepack XML responses - modify nested asset IDs directly
        content_type = flow.response.headers.get('Content-Type', '') if flow.response else ''
        if flow.response and flow.response.raw_content and 'xml' in content_type.lower():
            # Use cached replacements
            replacements, removals, cdn_replacements, local_replacements = self._get_replacements(flow.id)
            # Try to modify texturepack XML with ID replacements
            if replacements:
                modified_xml = self._modify_texturepack_xml(flow.response.raw_content, replacements)
                if modified_xml:
                    flow.response.raw_content = modified_xml
                    flow.response.headers['Content-Length'] = str(len(modified_xml))
                    log_buffer.log('TexturePack', 'Modified texturepack XML with ID replacements')

        # ── SolidModel OBJ injection ─────────────────────────────────────────
        # Runs for CDN responses (hostname differs from PROXY_TARGET_HOST), so
        # it must be checked before the early-return guard below.
        for cdn_url, obj_path_str in list(self.solidmodel_injections.items()):
            if cdn_url in url:
                self.solidmodel_injections.pop(cdn_url, None)
                if flow.response and flow.response.raw_content:
                    try:
                        modified = _inject_obj_into_solidmodel(
                            flow.response.raw_content,
                            Path(obj_path_str),
                        )
                        flow.response.raw_content = modified
                        flow.response.headers['Content-Type'] = 'application/octet-stream'
                        flow.response.headers['Content-Length'] = str(len(modified))
                        # Strip content-encoding so the client sees plain binary
                        flow.response.headers.pop('Content-Encoding', None)
                        log_buffer.log(
                            'SolidModel',
                            f'Injected OBJ mesh into SolidModel response ({len(modified)} bytes)',
                        )
                    except Exception as exc:  # noqa: BLE001
                        log_buffer.log('SolidModel', f'OBJ injection failed: {exc}')
                return

        if (
            urlparse(url).hostname != PROXY_TARGET_HOST
            or not flow.response
            or not flow.response.raw_content
        ):
            return

        enc = flow.response.headers.get('Content-Encoding', '').lower()
        try:
            data = self._decode(flow.response.raw_content, enc)
        except (json.JSONDecodeError, gzip.BadGzipFile, OSError):
            return

        if not isinstance(data, list):
            return

        # Find CDN URLs from response and set up redirects
        for item in data:
            if not isinstance(item, dict):
                continue

            req_id = item.get('requestId')
            location = item.get('location')

            if not req_id or not location:
                continue

            # Check if we're tracking this request
            key = f'{flow.id}_{req_id}'
            if key in self.pending_requests:
                _, url_type, url_value = self.pending_requests.pop(key)
                if url_type == 'cdn' and url_value:
                    self.cdn_redirects[location] = url_value
                    log_buffer.log('CDN', f'Will redirect {location[:50]}...')
                elif url_type == 'local' and url_value:
                    self.local_redirects[location] = url_value
                    log_buffer.log('Local', f'Will serve local file for {location[:50]}...')
                elif url_type == 'solidmodel_obj' and url_value:
                    self.solidmodel_injections[location] = url_value
                    log_buffer.log('SolidModel', f'Will inject OBJ into CDN response: {location[:50]}...')

        # Clear cache for this flow after response is complete
        self._clear_flow_cache(flow.id)