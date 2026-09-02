"""Cache manager for storing and organizing intercepted Roblox assets."""

from __future__ import annotations

import gzip
import hashlib
import importlib
import json
import threading
import zlib
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar, NotRequired, Protocol, TypedDict, cast

import requests
from defusedxml import ElementTree as DefusedElementTree

from fleasion.utils import CONFIG_DIR, log_buffer
from fleasion.utils.roblox_auth import get_roblosecurity

from . import mesh_processing
from .roblox_document import (
    export_roblox_document,
    get_default_roblox_document_export_format,
    get_roblox_document_export_formats,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def _export_boundary[T](action: Callable[[], T], *, category: str, message: str, fallback: T) -> T:
    try:
        return action()
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log(category, f'{message}: {exc}')
        return fallback


class AssetEntry(TypedDict):
    id: str
    type: int
    type_name: str
    url: str
    size: int
    compressed: bool
    hash: str
    cached_at: str
    metadata: dict[str, object]
    detected_type: NotRequired[str]
    raw_size: NotRequired[int]


class CacheIndex(TypedDict):
    assets: dict[str, AssetEntry]
    version: str


class CacheStats(TypedDict):
    total_assets: int
    total_size: int
    types_count: dict[str, int]


class _ExportConfig(Protocol):
    export_naming: list[str]


class _ExportObjCallable(Protocol):
    def __call__(self, doc: object, output_path: Path, *, decompose: bool = False) -> None: ...


def _lazy_attr(module_name: str, attr_name: str) -> object:
    """Load a deliberately lazy module attribute without importing it at startup."""
    module = importlib.import_module(module_name, package=__package__)
    return getattr(module, attr_name)


def _solidmodel_rbxmx_bytes(data: bytes) -> bytes:
    deserialize_rbxm = cast(
        'Callable[[bytes], object]',
        _lazy_attr('.tools.solidmodel_converter.converter', 'deserialize_rbxm'),
    )
    get_top_level_mesh_data = cast(
        'Callable[[object], bytes | None]',
        _lazy_attr('.tools.solidmodel_converter.converter', 'get_top_level_mesh_data'),
    )
    inject_mesh_data = cast(
        'Callable[[object, bytes], None]',
        _lazy_attr('.tools.solidmodel_converter.converter', 'inject_mesh_data'),
    )
    try_extract_child_data = cast(
        'Callable[[object], object | None]',
        _lazy_attr('.tools.solidmodel_converter.converter', 'try_extract_child_data'),
    )
    write_rbxmx = cast(
        'Callable[[object], bytes]',
        _lazy_attr('.tools.solidmodel_converter.rbxm.xml_writer', 'write_rbxmx'),
    )

    decompressed = gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data
    doc = deserialize_rbxm(decompressed)
    top_mesh_data = get_top_level_mesh_data(doc)
    child_doc = try_extract_child_data(doc)
    if child_doc is not None:
        doc = child_doc
        if top_mesh_data is not None:
            inject_mesh_data(doc, top_mesh_data)
    return write_rbxmx(doc)


def _converted_animation_bytes(data: bytes, *, curve: bool) -> bytes:
    anim_data = gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data
    if anim_data.startswith(b'<roblox!'):
        rbxm_to_rbxmx = cast(
            'Callable[[bytes], bytes]',
            _lazy_attr('fleasion.utils.anim_converter', 'rbxm_to_rbxmx'),
        )
        anim_data = rbxm_to_rbxmx(anim_data)

    if curve:
        if b'CurveAnimation' not in anim_data:
            keyframe_to_curve_anim = cast(
                'Callable[[bytes], bytes]',
                _lazy_attr('fleasion.utils.r15_to_r6', 'keyframe_to_curve_anim'),
            )
            anim_data = keyframe_to_curve_anim(anim_data)
    elif b'CurveAnimation' in anim_data:
        curve_anim_to_keyframe_xml = cast(
            'Callable[[bytes], bytes]',
            _lazy_attr('fleasion.utils.r15_to_r6', 'curve_anim_to_keyframe_xml'),
        )
        anim_data = curve_anim_to_keyframe_xml(anim_data)
    return anim_data


class _CacheScraper(Protocol):
    def get_roblosecurity(self, *, wait: bool = False) -> str | None: ...

    def fetch_asset_with_place_id_retry(
        self, asset_id: str, *, extra_headers: dict[str, str] | None = None
    ) -> tuple[bytes | None, int | None]: ...


# Use orjson if available (2-3x faster), fallback to json
try:
    import orjson

    def json_dumps(obj: object, *, indent: int | None = None) -> str:
        # orjson doesn't support indent parameter in the same way, but we can do it for pretty-print
        if indent:
            return orjson.dumps(obj, option=orjson.OPT_INDENT_2).decode('utf-8')
        return orjson.dumps(obj).decode('utf-8')

    def json_loads(data: str | bytes | bytearray) -> object:
        return orjson.loads(data)
except ImportError:
    import json

    def json_dumps(obj: object, *, indent: int | None = None) -> str:
        return json.dumps(obj, indent=indent)

    def json_loads(data: str | bytes | bytearray) -> object:
        return json.loads(data)


class CacheManager:
    """Manages cached Roblox assets organized by type."""

    # Asset types mapping
    ASSET_TYPES: ClassVar[dict[int, str]] = {
        1: 'Image',
        2: 'TShirt',
        3: 'Audio',
        4: 'Mesh',
        5: 'Lua',
        6: 'HTML',
        7: 'Text',
        8: 'Hat',
        9: 'Place',
        10: 'Model',
        11: 'Shirt',
        12: 'Pants',
        13: 'Decal',
        16: 'Avatar',
        17: 'Head',
        18: 'Face',
        19: 'Gear',
        21: 'Badge',
        22: 'GroupEmblem',
        24: 'Animation',
        25: 'Arms',
        26: 'Legs',
        27: 'Torso',
        28: 'RightArm',
        29: 'LeftArm',
        30: 'LeftLeg',
        31: 'RightLeg',
        32: 'Package',
        33: 'YouTubeVideo',
        34: 'GamePass',
        35: 'App',
        37: 'Code',
        38: 'Plugin',
        39: 'SolidModel',
        40: 'MeshPart',
        41: 'HairAccessory',
        42: 'FaceAccessory',
        43: 'NeckAccessory',
        44: 'ShoulderAccessory',
        45: 'FrontAccessory',
        46: 'BackAccessory',
        47: 'WaistAccessory',
        48: 'ClimbAnimation',
        49: 'DeathAnimation',
        50: 'FallAnimation',
        51: 'IdleAnimation',
        52: 'JumpAnimation',
        53: 'RunAnimation',
        54: 'SwimAnimation',
        55: 'WalkAnimation',
        56: 'PoseAnimation',
        57: 'EarAccessory',
        58: 'EyeAccessory',
        59: 'LocalizationTableManifest',
        61: 'EmoteAnimation',
        62: 'Video',
        63: 'TexturePack',
        64: 'TShirtAccessory',
        65: 'ShirtAccessory',
        66: 'PantsAccessory',
        67: 'JacketAccessory',
        68: 'SweaterAccessory',
        69: 'ShortsAccessory',
        70: 'LeftShoeAccessory',
        71: 'RightShoeAccessory',
        72: 'DressSkirtAccessory',
        73: 'FontFamily',
        74: 'FontFace',
        75: 'MeshHiddenSurfaceRemoval',
        76: 'EyebrowAccessory',
        77: 'EyelashAccessory',
        78: 'MoodAnimation',
        79: 'DynamicHead',
        80: 'CodeSnippet',
    }

    def __init__(self, config_manager: _ExportConfig | None = None) -> None:
        """Initialize cache manager."""
        self.cache_dir = CONFIG_DIR / 'FleasionNT' / 'Cache'
        self.export_dir = CONFIG_DIR / 'FleasionNT' / 'Exports'
        self.index_file = self.cache_dir / 'index.json'
        self.config_manager = config_manager
        self._lock = threading.Lock()

        # Cache scraper for asset downloads (optional, set by ProxyMaster)
        self._cache_scraper: _CacheScraper | None = None

        # Debounced index writes: reduce disk I/O by batching writes
        # Instead of writing on every store_asset(), we schedule a write 500ms in the future
        # If another write comes in before 500ms, we cancel the pending one and reschedule
        self._index_dirty = False
        self._index_commit_timer: threading.Timer | None = None

        # LRU cache for asset reads (256 assets max ~50-100MB depending on size)
        # This drastically speeds up repeated lookups during preview, search, export operations
        self._asset_cache: dict[str, bytes] = {}
        self._asset_cache_lock = threading.Lock()
        self._asset_cache_maxsize = 256

        # Create cache directory structure
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)

        # Load or create index
        self.index: CacheIndex = self._load_index()

    def set_scraper(self, scraper: _CacheScraper) -> None:
        """Set the cache scraper for private asset downloads."""
        self._cache_scraper = scraper

    def _load_index(self) -> CacheIndex:
        """Load cache index from disk."""
        if self.index_file.exists():
            try:
                with self.index_file.open('r', encoding='utf-8') as f:
                    return cast('CacheIndex', json_loads(f.read()))
            except (
                ValueError,
                OSError,
            ):  # ValueError for orjson, JSONDecodeError for json
                pass
        return {'assets': {}, 'version': '1.0'}

    def _save_index(self) -> None:
        """Save cache index to disk (called by debounced timer)."""
        try:
            with self.index_file.open('w', encoding='utf-8') as f:
                f.write(json_dumps(self.index, indent=2))
        except OSError as e:
            log_buffer.log('Scraper', f'Failed to save cache index: {e}')

    def _schedule_index_commit(self) -> None:
        """Schedule index write with debouncing (500ms delay).

        If _index_dirty is already True and a timer is pending, cancel it and reschedule.
        This reduces disk writes from 1000+ per scraping to ~20 when caching thousands of assets.
        """
        if self._index_commit_timer is not None:
            self._index_commit_timer.cancel()

        self._index_dirty = True
        self._index_commit_timer = threading.Timer(0.5, self._flush_index)
        self._index_commit_timer.daemon = True
        self._index_commit_timer.start()

    def _flush_index(self) -> None:
        """Internal method called by timer to actually write the index."""
        with self._lock:
            if self._index_dirty:
                self._save_index()
                self._index_dirty = False
            self._index_commit_timer = None

    def get_asset_type_name(self, type_id: int) -> str:
        """Get asset type name from ID."""
        return self.ASSET_TYPES.get(type_id, f'Unknown({type_id})')

    def get_asset_path(self, asset_id: str, asset_type: int) -> Path:
        """Get storage path for an asset."""
        type_name = self.get_asset_type_name(asset_type)
        type_dir = self.cache_dir / type_name
        type_dir.mkdir(exist_ok=True)
        return type_dir / f'{asset_id}.bin'

    def get_raw_asset_path(self, asset_id: str, asset_type: int) -> Path:
        """Get storage path for the raw (pre-conversion) sidecar file."""
        type_name = self.get_asset_type_name(asset_type)
        type_dir = self.cache_dir / type_name
        type_dir.mkdir(exist_ok=True)
        return type_dir / f'{asset_id}.raw'

    def get_texturepack_slot_dir(self) -> Path:
        """Return the persistent cache directory for captured TexturePack slots."""
        slot_dir = self.cache_dir / 'TexturePack' / 'slots'
        slot_dir.mkdir(parents=True, exist_ok=True)
        return slot_dir

    def get_texturepack_slot_path(self, asset_id: str | int, slot: int) -> Path:
        """Return the canonical highest-resolution path for one TexturePack slot."""
        if slot not in {0, 1, 2}:
            msg = f'invalid TexturePack slot: {slot}'
            raise ValueError(msg)
        return self.get_texturepack_slot_dir() / f'{asset_id}_slot{slot}.ktx2'

    def get_texturepack_slot_pack_path(  # ruff: ignore[too-many-positional-arguments]
        self,
        asset_id: str | int,
        slot: int,
        pack_index: int | None,
        quality: int,
        width: int,
        height: int,
        level_count: int,
        digest: str,
    ) -> Path:
        """Return the persistent path for one exact raw Roblox mip-pack response.

        The payload digest is part of the filename on purpose: two captures with
        the same packIndex/dimensions must never overwrite each other.  That makes
        the cache suitable for byte-for-byte codec/mipmap research as well as the
        normal cache viewer/export flow.
        """
        if slot not in {0, 1, 2}:
            msg = f'invalid TexturePack slot: {slot}'
            raise ValueError(msg)
        safe_digest = ''.join(ch for ch in str(digest).lower() if ch in '0123456789abcdef')
        if not safe_digest:
            msg = 'TexturePack mip-pack digest must not be empty'
            raise ValueError(msg)
        pack_label = f'pack{pack_index}' if pack_index is not None else 'packunknown'
        return self.get_texturepack_slot_dir() / (
            f'{asset_id}_slot{slot}_{pack_label}_q{max(0, int(quality))}_'
            f'{max(0, int(width))}x{max(0, int(height))}_mips{max(0, int(level_count))}_'
            f'{safe_digest}.ktx2'
        )

    def get_texturepack_slot_pack_paths(
        self,
        asset_id: str | int,
        slot: int | None = None,
    ) -> list[Path]:
        """List archived raw Roblox mip-pack responses for an asset/slot."""
        if slot is not None and slot not in {0, 1, 2}:
            msg = f'invalid TexturePack slot: {slot}'
            raise ValueError(msg)
        slot_pattern = str(slot) if slot is not None else '?'
        return sorted(self.get_texturepack_slot_dir().glob(f'{asset_id}_slot{slot_pattern}_*.ktx2'))

    def delete_texturepack_slot_files(self, asset_id: str | int) -> int:
        """Delete persistent canonical/archive KTX2 files for one TexturePack."""
        paths = set(self.get_texturepack_slot_pack_paths(asset_id))
        paths.update(self.get_texturepack_slot_path(asset_id, slot) for slot in (0, 1, 2))

        deleted = 0
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    deleted += 1
            except OSError as exc:
                log_buffer.log('Scraper', f'Failed to delete TexturePack slot file {path}: {exc}')
        return deleted

    def _record_raw_asset_size(self, asset_id: str, asset_type: int, size: int) -> None:
        with self._lock:
            asset_key = f'{asset_type}_{asset_id}'
            if asset_key in self.index['assets']:
                self.index['assets'][asset_key]['raw_size'] = size
                self._schedule_index_commit()

    def store_raw_asset(self, asset_id: str, asset_type: int, data: bytes) -> bool:
        """
        Store the raw pre-conversion asset bytes as a sidecar file and record
        its size in the index under 'raw_size'.  Used for TexturePack KTX2 files.
        """
        try:
            self.get_raw_asset_path(asset_id, asset_type).write_bytes(data)
            self._record_raw_asset_size(asset_id, asset_type, len(data))
        except (OSError, RuntimeError) as exc:
            log_buffer.log('Scraper', f'Failed to store raw asset {asset_id}: {exc}')
            return False
        else:
            return True

    def get_raw_asset(self, asset_id: str, asset_type: int) -> bytes | None:
        """Return the raw pre-conversion sidecar bytes, or None if not present."""
        try:
            raw_path = self.get_raw_asset_path(asset_id, asset_type)
            if raw_path.exists():
                return raw_path.read_bytes()
        except OSError as e:
            log_buffer.log('Scraper', f'Failed to retrieve raw asset {asset_id}: {e}')
        return None

    def _is_json_data(self, data: bytes) -> bool:
        """
        Quick check if binary data is valid JSON.

        Returns:
            True if data is valid JSON, False otherwise
        """
        if not data or len(data) < 2:
            return False

        # Check for gzip compression
        if data[:2] == b'\x1f\x8b':
            try:
                data = gzip.decompress(data)
            except EOFError, OSError, zlib.error:
                return False

        # Try multiple encodings
        for encoding in ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be']:
            try:
                text = data.decode(encoding)
                json.loads(text)
            except UnicodeDecodeError, json.JSONDecodeError:
                continue
            else:
                return True

        return False

    def _detect_payload_type(self, data: bytes, asset_type: int) -> str | None:
        """Return a display type override when the cached bytes identify better."""
        if asset_type not in {1, 13} and self._is_image_data(data):
            return 'Image'

        if asset_type != 4 and mesh_processing.is_mesh_data(data):
            return 'Mesh'

        if asset_type != 3 and self._is_audio_data(data):
            return 'Audio'

        type_name = self.get_asset_type_name(asset_type)
        if type_name.startswith('Unknown') and self._is_json_data(data):
            return 'Json'

        return None

    @staticmethod
    def _is_image_data(data: bytes) -> bool:
        """Return True when bytes look like an image payload supported by preview."""
        if not data:
            return False
        if len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WEBP':
            return True
        return data.startswith(
            (
                b'\x89PNG\r\n\x1a\n',
                b'\xff\xd8\xff',
                b'\xabKTX 11\xbb\r\n\x1a\n',
                b'\xabKTX 20\xbb\r\n\x1a\n',
            )
        )

    @staticmethod
    def _is_audio_data(data: bytes) -> bool:
        """Return True when bytes look like a common Roblox audio payload."""
        if not data:
            return False
        return data.startswith(
            (b'OggS', b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2', b'fLaC')
        ) or (len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WAVE')

    def _store_asset_impl(
        self,
        asset_id: str,
        asset_type: int,
        data: bytes,
        url: str,
        metadata: dict[str, object] | None,
    ) -> None:
        asset_path = self.get_asset_path(asset_id, asset_type)
        if len(data) > 10240:  # 10KB threshold
            with gzip.open(asset_path, 'wb') as f:
                f.write(data)
            compressed = True
        else:
            asset_path.write_bytes(data)
            compressed = False

        detected_type = self._detect_payload_type(data, asset_type)
        type_name = self.get_asset_type_name(asset_type)
        asset_entry: AssetEntry = {
            'id': asset_id,
            'type': asset_type,
            'type_name': type_name,
            'url': url,
            'size': len(data),
            'compressed': compressed,
            'hash': hashlib.sha256(data).hexdigest()[:16],
            'cached_at': datetime.now(UTC).astimezone().replace(tzinfo=None).isoformat(),
            'metadata': metadata or {},
        }
        if detected_type:
            asset_entry['detected_type'] = detected_type
            asset_entry['type_name'] = detected_type

        with self._lock:
            self.index['assets'][f'{asset_type}_{asset_id}'] = asset_entry
            self._schedule_index_commit()

        with self._asset_cache_lock:
            self._asset_cache.pop(f'{asset_type}_{asset_id}', None)

    def store_asset(
        self,
        asset_id: str,
        asset_type: int,
        data: bytes,
        url: str = '',
        metadata: dict[str, object] | None = None,
    ) -> bool:
        """
        Store an asset in the cache.

        Args:
            asset_id: Asset ID (usually from URL)
            asset_type: Roblox asset type ID
            data: Raw asset data
            url: Original URL
            metadata: Additional metadata

        Returns:
            True if stored successfully
        """
        try:
            self._store_asset_impl(asset_id, asset_type, data, url, metadata)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log_buffer.log('Scraper', f'Failed to store asset {asset_id}: {exc}')
            return False
        else:
            return True

    def _load_asset_from_disk(self, asset_id: str, asset_type: int, cache_key: str) -> bytes | None:
        asset_path = self.get_asset_path(asset_id, asset_type)
        if not asset_path.exists():
            return None

        asset_info = self.index['assets'].get(cache_key)
        if asset_info is not None and asset_info.get('compressed', False):
            with gzip.open(asset_path, 'rb') as f:
                data = f.read()
        else:
            data = asset_path.read_bytes()

        with self._asset_cache_lock:
            if len(self._asset_cache) >= self._asset_cache_maxsize:
                oldest_key = next(iter(self._asset_cache))
                del self._asset_cache[oldest_key]
            self._asset_cache[cache_key] = data
        return data

    def get_asset(self, asset_id: str, asset_type: int) -> bytes | None:
        """
        Retrieve an asset from cache with LRU in-memory caching.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID

        Returns:
            Asset data or None if not found
        """
        cache_key = f'{asset_type}_{asset_id}'

        # Check LRU cache first (avoids disk I/O for repeated reads)
        with self._asset_cache_lock:
            if cache_key in self._asset_cache:
                return self._asset_cache[cache_key]

        try:
            data = self._load_asset_from_disk(asset_id, asset_type, cache_key)
        except (EOFError, OSError, zlib.error) as e:
            log_buffer.log('Scraper', f'Failed to retrieve asset {asset_id}: {e}')
            return None
        return data

    def clear_memory_cache(self) -> int:
        """Evict all in-memory asset payloads and return the number removed."""
        with self._asset_cache_lock:
            count = len(self._asset_cache)
            self._asset_cache.clear()
        return count

    def _read_asset_prefix(self, asset_id: str, asset_type: int, max_bytes: int) -> bytes | None:
        asset_path = self.get_asset_path(asset_id, asset_type)
        if not asset_path.exists():
            return None

        asset_info = self.index['assets'].get(f'{asset_type}_{asset_id}')
        if asset_info is not None and asset_info.get('compressed', False):
            with gzip.open(asset_path, 'rb') as f:
                return f.read(max_bytes)

        with asset_path.open('rb') as f:
            return f.read(max_bytes)

    def peek_asset_bytes(self, asset_id: str, asset_type: int, max_bytes: int = 16) -> bytes | None:
        """Read only the beginning of an asset without populating the LRU cache.

        Type correction only needs magic bytes for the formats currently
        detected by the cache viewer. Keeping this separate from ``get_asset``
        prevents a metadata operation from reading and retaining a full image,
        mesh, or audio payload in memory.
        """
        if max_bytes <= 0:
            return b''

        try:
            prefix = self._read_asset_prefix(asset_id, asset_type, max_bytes)
        except (EOFError, OSError, zlib.error) as e:
            log_buffer.log('Scraper', f'Failed to peek at asset {asset_id}: {e}')
            return None
        else:
            return prefix

    def detect_asset_type_from_header(self, asset_id: str, asset_type: int) -> str | None:
        """Detect a corrected display type using only cached payload headers."""
        if asset_type not in {1, 4, 13}:
            return None

        data = self.peek_asset_bytes(asset_id, asset_type, max_bytes=16)
        if not data:
            return None
        return self._detect_payload_type(data, asset_type)

    def get_asset_info(self, asset_id: str, asset_type: int) -> AssetEntry | None:
        """Get metadata about a cached asset."""
        asset_key = f'{asset_type}_{asset_id}'
        return self.index['assets'].get(asset_key)

    def _set_detected_type_locked(self, asset_id: str, asset_type: int, detected_type: str) -> None:
        with self._lock:
            asset_key = f'{asset_type}_{asset_id}'
            if asset_key in self.index['assets']:
                self.index['assets'][asset_key]['detected_type'] = detected_type
                self.index['assets'][asset_key]['type_name'] = detected_type
                self._schedule_index_commit()

    def set_detected_type(self, asset_id: str, asset_type: int, detected_type: str) -> None:
        """
        Store a detected asset type (e.g., 'Json' for unknown types that are actually JSON).
        This persists to the cache index and overrides the default type name.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID
            detected_type: Detected type name (e.g., 'Json')
        """
        try:
            self._set_detected_type_locked(asset_id, asset_type, detected_type)
        except RuntimeError as exc:
            log_buffer.log('Scraper', f'Failed to set detected type for {asset_id}: {exc}')

    def get_type_name_for_asset(
        self, asset_id: str, asset_type: int, *, probe_payload: bool = True
    ) -> str:
        """Get the type name for an asset, considering detected type."""
        asset_key = f'{asset_type}_{asset_id}'
        asset_info = self.index['assets'].get(asset_key)

        # Return detected type if available, otherwise use standard type name
        if asset_info is not None and 'detected_type' in asset_info:
            return asset_info['detected_type']

        # Roblox metadata can disagree with the CDN payload type (for example,
        # RenderMesh bytes reported as Image and legacy PNG textures reported
        # as Mesh). Heal persisted entries lazily without requiring a re-download
        if probe_payload and asset_type in {1, 4, 13}:
            detected_type = self.detect_asset_type_from_header(asset_id, asset_type)
            if detected_type:
                self.set_detected_type(asset_id, asset_type, detected_type)
                return detected_type

        return self.get_asset_type_name(asset_type)

    def list_assets(self, asset_types: set[int | str] | None = None) -> list[AssetEntry]:
        """
        List all cached assets, optionally filtered by types.

        Args:
            asset_types: Optional set of asset type IDs to filter by

        Returns:
            List of asset metadata dictionaries (with type_name updated for detected types)
        """
        # Take a snapshot to avoid dictionary changed during iteration
        assets = list(dict(self.index['assets']).values())

        if asset_types is not None and len(asset_types) > 0:
            int_filters = {t for t in asset_types if isinstance(t, int)}
            str_filters = {t for t in asset_types if isinstance(t, str)}

            def _matches(a: AssetEntry) -> bool:
                if int_filters and a['type'] in int_filters:
                    return True
                return bool(str_filters and a.get('detected_type') in str_filters)

            assets = [a for a in assets if _matches(a)]

        # Update type_name with detected_type if available (this makes detected types persistent in listings)
        for asset in assets:
            if 'detected_type' in asset:
                asset['type_name'] = asset['detected_type']

        # Sort by cached_at descending (newest first)
        assets.sort(key=lambda a: a.get('cached_at', ''), reverse=True)

        return assets

    def get_available_export_formats(self, asset_type: int) -> list[str]:
        """
        Get available export formats for an asset type.

        Args:
            asset_type: Asset type ID

        Returns:
            List of available formats with extensions e.g: 'converted_obj', 'bin', 'raw'
        """
        formats = ['raw']  # Raw is always available (original cached data)

        # Add 'bin' for decompressed data if applicable
        formats.append('bin')

        # Add 'converted' for types that support conversion
        if asset_type == 4:  # Mesh - can convert to OBJ
            formats.insert(0, 'converted_obj')
        elif asset_type == 3:  # Audio - proper extension (ogg/mp3 handled in export)
            formats.insert(0, 'converted_audio')
        elif asset_type in {1, 13}:  # Image, Decal
            formats.insert(0, 'converted_png')
        elif asset_type == 63:  # TexturePack
            formats.insert(0, 'slot_ktx2')
            formats.insert(0, 'converted_images')
            formats.insert(0, 'converted')
        elif asset_type == 24:  # Animation
            formats.insert(0, 'converted_rbxmx')
            formats.insert(1, 'converted_rbxmx_curve')
        elif asset_type == 39:  # SolidModel
            formats.insert(0, 'converted_rbxmx_model')
            formats.insert(0, 'converted_obj')
        elif asset_type == 73:  # FontFamily - JSON metadata
            formats.insert(0, 'converted_json')
        elif asset_type == 74:  # FontFace - actual font file
            formats.insert(0, 'converted_font')

        return formats

    def get_available_export_formats_for_asset(self, asset_id: str, asset_type: int) -> list[str]:
        """Get export formats for an asset, including payload-detected document formats."""
        detected_type = self.get_type_name_for_asset(asset_id, asset_type)
        effective_type = {'Audio': 3, 'Image': 1, 'Mesh': 4}.get(detected_type, asset_type)
        formats = self.get_available_export_formats(effective_type)
        data = self.get_asset(asset_id, asset_type)
        if not data:
            return formats

        if effective_type == 4:
            has_embedded_rig = cast(
                'Callable[[bytes], bool]',
                _lazy_attr('.mesh_rig', 'has_embedded_rig'),
            )
            if has_embedded_rig(data) and 'converted_rigged_glb' not in formats:
                formats.insert(0, 'converted_rigged_glb')

        document_formats = get_roblox_document_export_formats(data, asset_type=asset_type)
        for fmt in reversed(document_formats):
            if fmt not in formats:
                formats.insert(0, fmt)
        return formats

    def _export_directory_and_filename(
        self,
        asset_id: str,
        asset_type: int,
        resolved_name: str | None,
        export_format: str,
    ) -> tuple[Path, str]:
        type_name = self.get_type_name_for_asset(asset_id, asset_type)
        if export_format.startswith('converted'):
            export_type_dir = self.export_dir / 'converted' / type_name
        elif export_format == 'bin':
            export_type_dir = self.export_dir / 'bin' / type_name
        else:
            export_type_dir = self.export_dir / 'raw' / type_name
        export_type_dir.mkdir(parents=True, exist_ok=True)

        asset_info = self.get_asset_info(asset_id, asset_type)
        hash_val = asset_info.get('hash', '') if asset_info else ''
        filename_parts: list[str] = []
        if self.config_manager:
            naming_options = self.config_manager.export_naming
            if 'name' in naming_options and resolved_name:
                sanitized_name = ''.join(
                    char if char.isalnum() or char in {' ', '-', '_'} else '_'
                    for char in resolved_name
                )
                filename_parts.append(sanitized_name[:100])
            if 'id' in naming_options and asset_id:
                filename_parts.append(asset_id)
            if 'hash' in naming_options and hash_val:
                filename_parts.append(hash_val)
        if not filename_parts:
            filename_parts.append(asset_id or hash_val)
        return export_type_dir, '_'.join(filename_parts)[:200]

    def _export_primary_format(
        self,
        data: bytes,
        asset_id: str,
        asset_type: int,
        *,
        export_format: str,
        export_type_dir: Path,
        filename: str,
    ) -> tuple[bool, Path | None]:
        handled = False
        result: Path | None = None
        if export_format == 'converted':
            document_format = get_default_roblox_document_export_format(data, asset_type=asset_type)
            if document_format is not None:
                export_data, ext = export_roblox_document(
                    data, document_format, asset_type=asset_type
                )
                result = export_type_dir / f'{filename}{ext}'
                result.write_bytes(export_data)
                handled = True
        elif export_format == 'raw':
            result = export_type_dir / f'{filename}.bin'
            result.write_bytes(data)
            handled = True
        elif export_format in {
            'converted_document_rbxm',
            'converted_document_rbxmx',
            'converted_document_rbxl',
        }:
            export_data, ext = export_roblox_document(data, export_format, asset_type=asset_type)
            result = export_type_dir / f'{filename}{ext}'
            result.write_bytes(export_data)
            handled = True
        elif export_format == 'bin':
            raw_data = self.get_raw_asset(asset_id, asset_type) if asset_type == 63 else None
            if raw_data is not None:
                result = export_type_dir / f'{filename}.ktx2'
                result.write_bytes(raw_data)
            else:
                ext = self._detect_extension(data, asset_type)
                result = export_type_dir / f'{filename}{ext}'
                result.write_bytes(data)
            handled = True
        elif export_format == 'converted_rigged_glb':
            export_glb = cast(
                'Callable[[bytes], bytes]',
                _lazy_attr('.mesh_rig', 'export_glb'),
            )
            result = export_type_dir / f'{filename}.glb'
            result.write_bytes(export_glb(data))
            handled = True
        return handled, result

    @staticmethod
    def _export_solidmodel_obj(data: bytes, output_path: Path) -> None:
        deserialize_rbxm = cast(
            'Callable[[bytes], object]',
            _lazy_attr('.tools.solidmodel_converter.converter', 'deserialize_rbxm'),
        )
        export_obj_from_doc = cast(
            '_ExportObjCallable',
            _lazy_attr('.tools.solidmodel_converter.converter', 'export_obj_from_doc'),
        )
        doc = deserialize_rbxm(data)
        export_obj_from_doc(doc, output_path, decompose=False)

    @staticmethod
    def _export_solidmodel_rbxmx(data: bytes, output_path: Path) -> None:
        output_path.write_bytes(_solidmodel_rbxmx_bytes(data))

    def _try_export_solidmodel_conversion(
        self,
        data: bytes,
        output_path: Path,
        *,
        as_rbxmx: bool,
    ) -> bool:
        label = 'RBXMX' if as_rbxmx else 'OBJ'

        def export() -> bool:
            if as_rbxmx:
                self._export_solidmodel_rbxmx(data, output_path)
            else:
                self._export_solidmodel_obj(data, output_path)
            return True

        return _export_boundary(
            export,
            category='Export',
            message=f'Failed to export SolidModel {label}',
            fallback=False,
        )

    def _export_special_format(
        self,
        data: bytes,
        asset_id: str,
        asset_type: int,
        *,
        export_format: str,
        export_type_dir: Path,
        filename: str,
    ) -> Path | None:
        output_path: Path | None = None
        direct_result: Path | None = None
        direct_handled = False

        if export_format == 'converted_obj' and mesh_processing.is_mesh_data(data):
            with suppress(Exception):
                mesh_data = gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data
                obj_data = mesh_processing.convert(mesh_data)
                if obj_data:
                    direct_result = export_type_dir / f'{filename}.obj'
                    direct_result.write_text(obj_data, encoding='utf-8')
                    direct_handled = True
            if not direct_handled:
                output_path = export_type_dir / f'{filename}.mesh'
        elif export_format == 'converted_obj' and asset_type == 39:
            output_path = export_type_dir / f'{filename}.obj'
            if self._try_export_solidmodel_conversion(data, output_path, as_rbxmx=False):
                direct_result = output_path
                direct_handled = True
        elif export_format == 'converted_rbxmx_model' and asset_type == 39:
            output_path = export_type_dir / f'{filename}.rbxmx'
            if self._try_export_solidmodel_conversion(data, output_path, as_rbxmx=True):
                direct_result = output_path
                direct_handled = True
        elif export_format == 'converted_audio':
            if data.startswith(b'OggS'):
                output_path = export_type_dir / f'{filename}.ogg'
            elif data.startswith((b'ID3', b'\xff\xfb')):
                output_path = export_type_dir / f'{filename}.mp3'
            else:
                output_path = export_type_dir / f'{filename}.ogg'
        elif export_format == 'converted_png':
            ktx_magic = (
                b'\xabKTX 11\xbb\r\n\x1a\n',
                b'\xabKTX 20\xbb\r\n\x1a\n',
            )
            export_data = data
            if data[:12] in ktx_magic:
                ktx_convert = cast(
                    'Callable[[bytes], bytes | None]',
                    _lazy_attr('.tools.ktx_to_png', 'convert'),
                )
                converted = ktx_convert(data)
                if converted:
                    export_data = converted
            direct_result = export_type_dir / f'{filename}.png'
            direct_result.write_bytes(export_data)
            direct_handled = True
        elif export_format == 'converted' and asset_type == 63:
            xml_data = gzip.decompress(data) if data.startswith(b'\x1f\x8b') else data
            direct_result = export_type_dir / f'{filename}.xml'
            direct_result.write_bytes(xml_data)
            direct_handled = True
        elif export_format == 'converted_images' and asset_type == 63:
            direct_result = self._export_texturepack(data, asset_id, export_type_dir, filename)
            direct_handled = True
        elif export_format == 'converted_rbxmx' and asset_type == 24:
            output_path = export_type_dir / f'{filename}.rbxmx'
            try:
                anim_data = _converted_animation_bytes(data, curve=False)
                output_path.write_bytes(anim_data)
            except (
                EOFError,
                ImportError,
                OSError,
                RuntimeError,
                SyntaxError,
                TypeError,
                ValueError,
            ) as exc:
                log_buffer.log('Export', f'Failed to convert animation to RBXMX: {exc}')
            else:
                direct_result = output_path
                direct_handled = True
        elif export_format == 'converted_rbxmx_curve' and asset_type == 24:
            output_path = export_type_dir / f'{filename}.rbxmx'
            try:
                anim_data = _converted_animation_bytes(data, curve=True)
                output_path.write_bytes(anim_data)
            except (
                EOFError,
                ImportError,
                OSError,
                RuntimeError,
                SyntaxError,
                TypeError,
                ValueError,
            ) as exc:
                log_buffer.log('Export', f'Failed to convert animation to RBXMX: {exc}')
            else:
                direct_result = output_path
                direct_handled = True
        elif export_format == 'converted_json' and asset_type == 73:
            direct_result = export_type_dir / f'{filename}.json'
            direct_result.write_bytes(data)
            direct_handled = True
        elif export_format == 'converted_font' and asset_type == 74:
            output_path = export_type_dir / f'{filename}{self._detect_font_extension(data)}'
        else:
            output_path = export_type_dir / f'{filename}.bin'

        if direct_handled:
            return direct_result
        if output_path is None:
            msg = 'Export path was not resolved'
            raise RuntimeError(msg)
        output_path.write_bytes(data)
        return output_path

    def _export_asset_impl(
        self,
        asset_id: str,
        asset_type: int,
        output_path: Path | None,
        resolved_name: str | None,
        export_format: str,
    ) -> Path | None:
        data = self.get_asset(asset_id, asset_type)
        if not data:
            return None
        if output_path is not None:
            output_path.write_bytes(data)
            return output_path

        export_type_dir, filename = self._export_directory_and_filename(
            asset_id,
            asset_type,
            resolved_name,
            export_format,
        )
        handled, result = self._export_primary_format(
            data,
            asset_id,
            asset_type,
            export_format=export_format,
            export_type_dir=export_type_dir,
            filename=filename,
        )
        if handled:
            return result
        return self._export_special_format(
            data,
            asset_id,
            asset_type,
            export_format=export_format,
            export_type_dir=export_type_dir,
            filename=filename,
        )

    def export_asset(
        self,
        asset_id: str,
        asset_type: int,
        output_path: Path | None = None,
        resolved_name: str | None = None,
        export_format: str = 'converted',
    ) -> Path | None:
        """
        Export an asset to the exports folder.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID
            output_path: Optional custom output path
            resolved_name: Optional resolved asset name for filename
            export_format: Export format - 'converted', 'bin', or 'raw'

        Returns:
            Path to exported file or None on failure
        """
        return _export_boundary(
            lambda: self._export_asset_impl(
                asset_id,
                asset_type,
                output_path,
                resolved_name,
                export_format,
            ),
            category='Scraper',
            message=f'Failed to export asset {asset_id}',
            fallback=None,
        )

    def _detect_extension(self, data: bytes, asset_type: int) -> str:
        """Detect file extension based on data signature."""
        fixed_asset_extensions = {39: '.bin', 73: '.json'}
        extension = fixed_asset_extensions.get(asset_type)
        if extension is not None:
            return extension
        if asset_type == 74:  # FontFace - actual font file
            extension = self._detect_font_extension(data)
        elif data.startswith(b'<roblox'):
            is_binary = data.startswith(b'<roblox!')
            if asset_type == 9:
                extension = '.rbxl' if is_binary else '.rbxlx'
            elif asset_type == 63:  # TexturePack XML
                extension = '.xml'
            else:
                extension = '.rbxm' if is_binary else '.rbxmx'
        elif len(data) >= 12 and data.startswith(b'RIFF'):
            if data[8:12] == b'WAVE':
                extension = '.wav'
            elif data[8:12] == b'WEBP':
                extension = '.webp'
            else:
                extension = '.bin'
        else:
            prefix_extensions = (
                ((b'\x89PNG',), '.png'),
                ((b'\xff\xd8\xff',), '.jpg'),
                ((b'OggS',), '.ogg'),
                ((b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'), '.mp3'),
                ((b'fLaC',), '.flac'),
                ((b'version ',), '.mesh'),
                ((b'\xabKTX',), '.ktx'),
                ((b'\x1f\x8b',), '.gz'),
            )
            extension = next(
                (
                    candidate
                    for signatures, candidate in prefix_extensions
                    if data.startswith(signatures)
                ),
                '.bin',
            )
        return extension

    def _detect_font_extension(self, data: bytes) -> str:
        """Detect font file extension from magic bytes."""
        if not data:
            return '.ttf'

        # TrueType
        if data[:4] == b'\x00\x01\x00\x00':
            return '.ttf'
        # OpenType (CFF-based)
        if data[:4] == b'OTTO':
            return '.otf'
        # TrueType Collection
        if data[:4] == b'ttcf':
            return '.ttc'
        # Alternative TrueType magic
        if data[:2] == b'\x01\x00':
            return '.ttf'

        return '.ttf'  # Default to TTF for font types

    def _fetch_texture_for_export(self, map_name: str, map_id: str) -> bytes | None:
        if self._cache_scraper is not None:
            extra: dict[str, str] = {}
            cookie = self._cache_scraper.get_roblosecurity()
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            texture_data, _status = self._cache_scraper.fetch_asset_with_place_id_retry(
                map_id,
                extra_headers=extra or None,
            )
            return texture_data

        api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={map_id}'
        headers = {'User-Agent': 'Roblox/WinInet'}
        with suppress(Exception):
            cookie = get_roblosecurity()
            if cookie:
                headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        response = requests.get(
            api_url,
            headers=headers,
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code == 200 and response.content:
            log_buffer.log(
                'Export',
                f'Successfully fetched {map_name} texture {map_id}, size: {len(response.content)} bytes',
            )
            return response.content
        log_buffer.log(
            'Export',
            f'API returned status {response.status_code} for {map_name} texture {map_id}',
        )
        return None

    def _export_texturepack_impl(
        self,
        data: bytes,
        asset_id: str,
        export_type_dir: Path,
    ) -> Path | None:
        log_buffer.log('Export', f'Starting TexturePack export for {asset_id}')
        xml_data = data
        if data.startswith(b'\x1f\x8b'):
            xml_data = gzip.decompress(data)
            log_buffer.log('Export', f'Decompressed gzip data, size: {len(xml_data)} bytes')

        xml_text = xml_data.decode('utf-8', errors='replace')
        root = DefusedElementTree.fromstring(xml_text)
        log_buffer.log('Export', 'Parsed XML successfully')

        map_order = ['color', 'normal', 'metalness', 'roughness', 'emissive']
        maps: dict[str, str] = {}
        for elem in map_order:
            node = root.find(elem)
            if node is not None and node.text:
                maps[elem.capitalize()] = node.text
        log_buffer.log('Export', f'Found {len(maps)} texture maps: {list(maps.keys())}')

        if not maps:
            log_buffer.log('Export', f'No texture maps found in texture pack {asset_id}')
            return None

        exported_count = 0
        for map_name, map_id in maps.items():
            type_dir = export_type_dir / map_name
            type_dir.mkdir(exist_ok=True)
            texture_data = self.get_asset(str(map_id), 1)
            texture_hash = ''
            if texture_data:
                log_buffer.log('Export', f'Found cached {map_name} texture {map_id}')
                texture_info = self.get_asset_info(str(map_id), 1)
                texture_hash = texture_info.get('hash', '') if texture_info else ''
            else:
                log_buffer.log('Export', f'Fetching {map_name} texture {map_id} from API')
                try:
                    texture_data = self._fetch_texture_for_export(map_name, str(map_id))
                except (RuntimeError, requests.RequestException) as exc:
                    log_buffer.log('Export', f'Failed to fetch texture {map_id}: {exc}')
                    continue
            if not texture_data:
                log_buffer.log('Export', f'No data for texture {map_id}')
                continue

            filename_parts: list[str] = [map_id]
            if texture_hash:
                filename_parts.append(texture_hash)
            texture_path = type_dir / f'{"_".join(filename_parts)}.png'
            try:
                texture_path.write_bytes(texture_data)
            except OSError as exc:
                log_buffer.log(
                    'Export', f'Failed to write {map_name} texture to {texture_path}: {exc}'
                )
                continue
            exported_count += 1
            log_buffer.log('Export', f'Saved {map_name} texture to {texture_path}')

        if exported_count > 0:
            log_buffer.log('Export', f'Exported {exported_count} textures from pack {asset_id}')
            return export_type_dir
        log_buffer.log('Export', f'No textures exported for pack {asset_id}')
        return None

    def _export_texturepack(
        self,
        data: bytes,
        asset_id: str,
        export_type_dir: Path,
        _base_filename: str,
    ) -> Path | None:
        """Export texture pack by extracting all textures to subfolders."""
        try:
            return self._export_texturepack_impl(data, asset_id, export_type_dir)
        except (
            EOFError,
            OSError,
            RuntimeError,
            SyntaxError,
            TypeError,
            ValueError,
            requests.RequestException,
        ) as exc:
            log_buffer.log('Export', f'Failed to export texture pack {asset_id}: {exc}')
            return None

    def _delete_asset_files(self, asset_id: str, asset_type: int) -> None:
        asset_path = self.get_asset_path(asset_id, asset_type)
        if asset_path.exists():
            asset_path.unlink()
        raw_path = self.get_raw_asset_path(asset_id, asset_type)
        if raw_path.exists():
            raw_path.unlink()
        if asset_type == 63:
            self.delete_texturepack_slot_files(asset_id)

    def _delete_asset_impl(self, asset_id: str, asset_type: int) -> None:
        self._delete_asset_files(asset_id, asset_type)
        with self._lock:
            asset_key = f'{asset_type}_{asset_id}'
            if asset_key in self.index['assets']:
                del self.index['assets'][asset_key]
                self._save_index()

    def delete_asset(self, asset_id: str, asset_type: int) -> bool:
        """
        Delete an asset from cache.

        Args:
            asset_id: Asset ID
            asset_type: Asset type ID

        Returns:
            True if deleted successfully
        """
        try:
            self._delete_asset_impl(asset_id, asset_type)
        except (OSError, RuntimeError) as exc:
            log_buffer.log('Scraper', f'Failed to delete asset {asset_id}: {exc}')
            return False
        else:
            return True

    def _delete_batch_asset_files(self, asset_id: str, asset_type: int) -> bool:
        try:
            self._delete_asset_files(asset_id, asset_type)
        except OSError as e:
            log_buffer.log('Scraper', f'Failed to delete asset file {asset_id}: {e}')
            return False
        else:
            return True

    def _commit_batch_delete(self, assets: list[tuple[str, int]], deleted_count: int) -> None:
        with self._lock:
            for asset_id, asset_type in assets:
                self.index['assets'].pop(f'{asset_type}_{asset_id}', None)
            if deleted_count > 0:
                self._save_index()

        with self._asset_cache_lock:
            for asset_id, asset_type in assets:
                self._asset_cache.pop(f'{asset_type}_{asset_id}', None)

    def delete_assets_batch(self, assets: list[tuple[str, int]]) -> tuple[int, int]:
        """
        Delete multiple assets efficiently by batching index writes.

        This is MUCH faster than calling delete_asset() multiple times because
        it only writes the index file ONCE instead of N times for N assets.

        Args:
            assets: List of (asset_id, asset_type) tuples to delete

        Returns:
            Tuple of (deleted_count, failed_count)
        """
        deleted_count = 0
        failed_count = 0

        try:
            for asset_id, asset_type in assets:
                if self._delete_batch_asset_files(asset_id, asset_type):
                    deleted_count += 1
                else:
                    failed_count += 1
            self._commit_batch_delete(assets, deleted_count)
        except RuntimeError as exc:
            log_buffer.log('Scraper', f'Batch delete failed: {exc}')
            return deleted_count, failed_count
        else:
            return deleted_count, failed_count

    def clear_cache(self, asset_type: int | None = None) -> int:
        """
        Clear cached assets.

        Args:
            asset_type: Optional asset type to clear, or None for all

        Returns:
            Number of assets deleted
        """
        count = 0
        assets_to_delete = [
            (asset_info['id'], asset_info['type'])
            for asset_info in self.index['assets'].values()
            if asset_type is None or asset_info['type'] == asset_type
        ]

        if assets_to_delete:
            count, _failed = self.delete_assets_batch(assets_to_delete)
        return count

    def get_cache_stats(self) -> CacheStats:
        """Get cache statistics."""
        # Take a snapshot to avoid dictionary changed during iteration
        assets_snapshot = dict(self.index['assets'])

        total_assets = len(assets_snapshot)
        total_size = sum(a.get('size', 0) for a in assets_snapshot.values())

        types_count: dict[str, int] = {}
        for asset_info in assets_snapshot.values():
            type_name = asset_info.get('type_name', 'Unknown')
            types_count[type_name] = types_count.get(type_name, 0) + 1

        return {
            'total_assets': total_assets,
            'total_size': total_size,
            'types_count': types_count,
        }
