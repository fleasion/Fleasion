"""TextureStripper: batch request/response modifier and CDN redirect manager.

All cross-connection state is held at the class level (singleton dicts) behind a
threading.Lock so it is safely shared across all MITM thread-pool workers.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import importlib
import io
import json
import logging
import struct
import time
import zlib
from pathlib import Path
from threading import Lock, Thread
from typing import TYPE_CHECKING, ClassVar, Literal, Protocol, cast
from urllib.parse import urlparse

from fleasion.proxy.roblox_metadata import strip_roblox_metadata
from fleasion.utils import APP_CACHE_DIR, format_count, log_buffer
from fleasion.utils.http import http_download_to
from fleasion.utils.json_types import (
    JsonArray as _JsonList,
    JsonObject as _JsonObject,
    JsonValue as _JsonValue,
    as_json_object,
    require_json_value,
)

if TYPE_CHECKING:
    import xml.etree.ElementTree as ET
    from collections.abc import Callable

    from fleasion.cache.tools.solidmodel_converter.rbxm.types import RbxDocument, RbxProperty
    from fleasion.config.manager import ConfigManager, ReplacementMaps
    from fleasion.utils.r15_to_r6 import JointMap, PartMap

type _BuildType = int | str | None
type _ReplacementKey = int | str
type _AnimRequiredRig = str | frozenset[str]
type _PendingValue = tuple[str, str]
type _AnimPendingValue = tuple[str, _AnimRequiredRig]
type _CdnMatch = tuple[str, str | _AnimPendingValue]
type _MipmapMode = Literal['color', 'normal', 'linear']

_MIPMAP_MODES: dict[int | None, _MipmapMode] = {
    None: 'color',
    0: 'color',
    1: 'normal',
    2: 'linear',
}


class _CacheManagerLike(Protocol):
    def get_texturepack_slot_path(self, asset_id: str | int, slot: int) -> Path: ...


class _CacheScraperLike(Protocol):
    @property
    def cache_manager(self) -> _CacheManagerLike: ...

    def prefetch_texpack_layout(self, parent_id: int) -> None: ...
    def get_roblosecurity(self, *, wait: bool = False) -> str | None: ...
    def fetch_asset_with_place_id_retry(
        self,
        asset_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[bytes | None, int | None]: ...


class _RoblosecurityGetter(Protocol):
    def __call__(self, *, wait: bool = False) -> str | None: ...


class _AssetRetryGetter(Protocol):
    def __call__(
        self,
        asset_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[bytes | None, int | None]: ...


class _HttpsBodyGetter(Protocol):
    def __call__(
        self,
        hostname: str,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes | None: ...


class _HttpsStatusGetter(Protocol):
    def __call__(
        self,
        hostname: str,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        return_status: Literal[True],
    ) -> tuple[bytes | None, int | None]: ...


class _ZstdDecompressor(Protocol):
    def decompress(self, data: bytes, *, max_output_size: int) -> bytes: ...


class _ElementTreeWriter(Protocol):
    def write(
        self,
        file: str | Path | io.BytesIO,
        *,
        encoding: str,
        xml_declaration: bool,
    ) -> object: ...


_SCRAPER_HTTPS_GET_ATTR = '_https_get'
_SCRAPER_PRIVATE_ROBLOSECURITY_ATTR = '_get_roblosecurity'
_SCRAPER_PRIVATE_ASSET_RETRY_ATTR = '_fetch_asset_with_place_id_retry'


class _DynamicBoundaryError(RuntimeError):
    """Wrap failures from optional/dynamically loaded integration boundaries."""


def _call_dynamic[T](callback: Callable[..., T], /, *args: object, **kwargs: object) -> T:
    try:
        return callback(*args, **kwargs)
    except Exception as exc:
        raise _DynamicBoundaryError(str(exc)) from exc


_BEST_EFFORT_ERRORS = (
    _DynamicBoundaryError,
    OSError,
    TypeError,
    ValueError,
    EOFError,
    OverflowError,
    struct.error,
    zlib.error,
)


def _scraper_get_roblosecurity(scraper: _CacheScraperLike, *, wait: bool = False) -> str | None:
    getter = getattr(scraper, 'get_roblosecurity', None)
    if getter is not None:
        return _call_dynamic(cast('_RoblosecurityGetter', getter), wait=wait)

    legacy_getter = cast(
        '_RoblosecurityGetter', getattr(scraper, _SCRAPER_PRIVATE_ROBLOSECURITY_ATTR)
    )
    if wait:
        return _call_dynamic(legacy_getter, wait=True)
    return _call_dynamic(legacy_getter)


def _scraper_https_get(
    scraper: _CacheScraperLike,
    hostname: str,
    path: str,
    extra_headers: dict[str, str] | None = None,
) -> bytes | None:
    getter = cast('_HttpsBodyGetter', getattr(scraper, _SCRAPER_HTTPS_GET_ATTR))
    return _call_dynamic(getter, hostname, path, extra_headers=extra_headers)


def _scraper_https_get_status(
    scraper: _CacheScraperLike,
    hostname: str,
    path: str,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bytes | None, int | None]:
    getter = cast('_HttpsStatusGetter', getattr(scraper, _SCRAPER_HTTPS_GET_ATTR))
    return _call_dynamic(
        getter,
        hostname,
        path,
        extra_headers=extra_headers,
        return_status=True,
    )


def _scraper_fetch_asset_with_place_id_retry(
    scraper: _CacheScraperLike,
    asset_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> tuple[bytes | None, int | None]:
    getter = getattr(scraper, 'fetch_asset_with_place_id_retry', None)
    if getter is None:
        getter = getattr(scraper, _SCRAPER_PRIVATE_ASSET_RETRY_ATTR)
    return _call_dynamic(
        cast('_AssetRetryGetter', getter),
        asset_id,
        extra_headers=extra_headers,
    )


def _lazy_attr(module_name: str, attr_name: str) -> object:
    try:
        return vars(importlib.import_module(module_name))[attr_name]
    except (ImportError, KeyError) as exc:
        raise _DynamicBoundaryError(str(exc)) from exc


def _zstd_decompress(data: bytes) -> bytes:
    factory = cast(
        'Callable[[], _ZstdDecompressor]',
        _lazy_attr('zstandard', 'ZstdDecompressor'),
    )
    decompressor = _call_dynamic(factory)
    return _call_dynamic(decompressor.decompress, data, max_output_size=64 * 1024 * 1024)


def _deserialize_rbxm(data: bytes) -> RbxDocument:
    callback = cast(
        'Callable[[bytes], RbxDocument]',
        _lazy_attr('fleasion.cache.tools.solidmodel_converter.converter', 'deserialize_rbxm'),
    )
    return _call_dynamic(callback, data)


def _detect_csgmdl_version(data: bytes) -> int | None:
    callback = cast(
        'Callable[[bytes], int | None]',
        _lazy_attr(
            'fleasion.cache.tools.solidmodel_converter.csg_mesh',
            '_detect_csgmdl_version',
        ),
    )
    return _call_dynamic(callback, data)


def _export_csg_mesh(obj_path: Path, *, version: int) -> bytes:
    callback = cast(
        'Callable[..., bytes]',
        _lazy_attr('fleasion.cache.tools.solidmodel_converter.obj_to_csg', 'export_csg_mesh'),
    )
    return _call_dynamic(callback, obj_path, version=version)


def _write_rbxm(doc: RbxDocument) -> bytes:
    callback = cast(
        'Callable[[RbxDocument], bytes]',
        _lazy_attr('fleasion.cache.tools.solidmodel_converter.rbxm.serializer', 'write_rbxm'),
    )
    return _call_dynamic(callback, doc)


def _make_rbx_property(name: str, fmt_name: str, value: object) -> RbxProperty:
    module_name = 'fleasion.cache.tools.solidmodel_converter.rbxm.types'
    factory = cast('Callable[..., RbxProperty]', _lazy_attr(module_name, 'RbxProperty'))
    property_format = _lazy_attr(module_name, 'PropertyFormat')
    fmt = getattr(property_format, fmt_name)
    return _call_dynamic(factory, name=name, fmt=fmt, value=value)


def _mesh_file_to_cached_obj(path: Path) -> Path:
    callback = cast(
        'Callable[[Path], Path]',
        _lazy_attr(
            'fleasion.cache.tools.solidmodel_converter.mesh_intermediary',
            'mesh_file_to_cached_obj',
        ),
    )
    return _call_dynamic(callback, path)


def _bin_file_to_cached_obj(path: Path) -> Path:
    callback = cast(
        'Callable[[Path], Path]',
        _lazy_attr(
            'fleasion.cache.tools.solidmodel_converter.mesh_intermediary',
            'bin_file_to_cached_obj',
        ),
    )
    return _call_dynamic(callback, path)


def _rbxmx_file_to_cached_obj(path: Path) -> Path:
    callback = cast(
        'Callable[[Path], Path]',
        _lazy_attr(
            'fleasion.cache.tools.solidmodel_converter.mesh_intermediary',
            'rbxmx_file_to_cached_obj',
        ),
    )
    return _call_dynamic(callback, path)


def _is_binary_rbxm(data: bytes) -> bool:
    callback = cast(
        'Callable[[bytes], bool]',
        _lazy_attr(
            'fleasion.cache.tools.solidmodel_converter.mesh_intermediary',
            'is_binary_rbxm',
        ),
    )
    return _call_dynamic(callback, data)


def _detect_rig(data: bytes) -> str:
    callback = cast(
        'Callable[[bytes], str]',
        _lazy_attr('fleasion.utils.anim_converter', 'detect_rig'),
    )
    return _call_dynamic(callback, data)


def _rbxm_to_rbxmx(data: bytes) -> bytes:
    callback = cast(
        'Callable[[bytes], bytes]',
        _lazy_attr('fleasion.utils.anim_converter', 'rbxm_to_rbxmx'),
    )
    return _call_dynamic(callback, data)


def _curve_anim_to_keyframe_xml(data: bytes) -> bytes:
    callback = cast(
        'Callable[[bytes], bytes]',
        _lazy_attr('fleasion.utils.r15_to_r6', 'curve_anim_to_keyframe_xml'),
    )
    return _call_dynamic(callback, data)


def _sanitize_xml(data: bytes) -> str:
    callback = cast(
        'Callable[[bytes], str]',
        _lazy_attr('fleasion.utils.r15_to_r6', 'sanitize_xml'),
    )
    return _call_dynamic(callback, data)


def _defused_xml_fromstring(data: bytes | str) -> ET.Element:
    callback = cast(
        'Callable[[bytes | str], ET.Element]',
        _lazy_attr('defusedxml.ElementTree', 'fromstring'),
    )
    return _call_dynamic(callback, data)


def _element_tree(root: ET.Element) -> _ElementTreeWriter:
    factory = cast(
        'Callable[[ET.Element], _ElementTreeWriter]',
        _lazy_attr('xml.etree.ElementTree', 'ElementTree'),
    )
    return _call_dynamic(factory, root)


def _rig_maps() -> tuple[PartMap, JointMap, PartMap, JointMap]:
    module_name = 'fleasion.utils.rig_data'
    return (
        cast('PartMap', _lazy_attr(module_name, 'R6_PARTS')),
        cast('JointMap', _lazy_attr(module_name, 'R6_JOINTS')),
        cast('PartMap', _lazy_attr(module_name, 'R15_PARTS')),
        cast('JointMap', _lazy_attr(module_name, 'R15_JOINTS')),
    )


def _convert_keyframe(
    keyframe: ET.Element,
    *,
    target_rig: str,
    r6_parts: PartMap,
    r6_joints: JointMap,
    r15_parts: PartMap,
    r15_joints: JointMap,
) -> None:
    attr_name = 'convert_keyframe_r15_to_r6' if target_rig == 'R6' else 'convert_keyframe_r6_to_r15'
    callback = cast(
        'Callable[[ET.Element, PartMap, JointMap, PartMap, JointMap], None]',
        _lazy_attr('fleasion.utils.r15_to_r6', attr_name),
    )
    _call_dynamic(callback, keyframe, r6_parts, r6_joints, r15_parts, r15_joints)


def _keyframe_to_curve_anim(data: bytes) -> bytes:
    callback = cast(
        'Callable[[bytes], bytes]',
        _lazy_attr('fleasion.utils.r15_to_r6', 'keyframe_to_curve_anim'),
    )
    return _call_dynamic(callback, data)


def _write_rig_converted_animation(data: bytes, out_path: Path, target_rig: str) -> None:
    xml_data = _rbxm_to_rbxmx(data) if data[:8] == b'<roblox!' else data
    if b'CurveAnimation' in xml_data:
        xml_data = _curve_anim_to_keyframe_xml(xml_data)

    root = _defused_xml_fromstring(_sanitize_xml(xml_data))
    etree = _element_tree(root)
    r6_parts, r6_joints, r15_parts, r15_joints = _rig_maps()
    sequence = root.find("Item[@class='KeyframeSequence']")
    if sequence is None:
        msg = 'No KeyframeSequence found'
        raise ValueError(msg)
    keyframes = sequence.findall("Item[@class='Keyframe']")
    if not keyframes:
        msg = 'No Keyframes found'
        raise ValueError(msg)

    for keyframe in keyframes:
        _convert_keyframe(
            keyframe,
            target_rig=target_rig,
            r6_parts=r6_parts,
            r6_joints=r6_joints,
            r15_parts=r15_parts,
            r15_joints=r15_joints,
        )
    etree.write(str(out_path), encoding='utf-8', xml_declaration=True)


def _write_curve_converted_animation(data: bytes, out_path: Path, target_rig: str) -> None:
    xml_data = _rbxm_to_rbxmx(data) if data[:10].startswith(b'<roblox!\x89\xff') else data
    if b'CurveAnimation' in xml_data:
        xml_data = _curve_anim_to_keyframe_xml(xml_data)

    src_rig = _detect_rig(xml_data)
    if src_rig not in {'unknown', target_rig}:
        root = _defused_xml_fromstring(_sanitize_xml(xml_data))
        sequence = root.find("Item[@class='KeyframeSequence']")
        if sequence is None:
            msg = 'No KeyframeSequence found after curve conversion'
            raise ValueError(msg)
        keyframes = sequence.findall("Item[@class='Keyframe']")
        if not keyframes:
            msg = 'No Keyframes found after curve conversion'
            raise ValueError(msg)
        r6_parts, r6_joints, r15_parts, r15_joints = _rig_maps()
        for keyframe in keyframes:
            _convert_keyframe(
                keyframe,
                target_rig=target_rig,
                r6_parts=r6_parts,
                r6_joints=r6_joints,
                r15_parts=r15_parts,
                r15_joints=r15_joints,
            )
        buffer = io.BytesIO()
        _element_tree(root).write(buffer, encoding='utf-8', xml_declaration=True)
        xml_data = buffer.getvalue()

    out_path.write_bytes(_keyframe_to_curve_anim(xml_data))


def _get_or_create_ktx2_from_image(
    path: Path,
    *,
    mipmap_mode: _MipmapMode = 'color',
) -> Path:
    callback = cast(
        'Callable[..., Path]',
        _lazy_attr(
            'fleasion.cache.tools.image_to_ktx2.converter',
            'get_or_create_ktx2_from_image',
        ),
    )
    return _call_dynamic(callback, path, mipmap_mode=mipmap_mode)


def _rgba8_ktx2_cache_version() -> bytes:
    return cast(
        'bytes',
        _lazy_attr('fleasion.cache.tools.rgba_ktx2', 'RGBA8_KTX2_CACHE_VERSION'),
    )


def _read_rgba8_ktx2_levels(data: bytes) -> tuple[list[bytes], int, int] | None:
    callback = cast(
        'Callable[[bytes], tuple[list[bytes], int, int] | None]',
        _lazy_attr('fleasion.cache.tools.rgba_ktx2', 'read_rgba8_ktx2_levels'),
    )
    return _call_dynamic(callback, data)


def _write_rgba8_ktx2(
    rgba: bytes,
    width: int,
    height: int,
    out_path: Path,
    *,
    mipmap_mode: _MipmapMode,
) -> None:
    callback = cast(
        'Callable[..., None]',
        _lazy_attr('fleasion.cache.tools.rgba_ktx2', 'write_rgba8_ktx2'),
    )
    _call_dynamic(callback, rgba, width, height, out_path, mipmap_mode=mipmap_mode)


def _normalized_rgba8_ktx2_path(path: Path, *, mipmap_mode: _MipmapMode) -> Path:
    data = path.read_bytes()
    parsed = _read_rgba8_ktx2_levels(data)
    if parsed is None:
        return path
    levels, width, height = parsed
    if len(levels) > 1:
        return path

    digest = hashlib.md5(
        data + _rgba8_ktx2_cache_version() + mipmap_mode.encode('ascii'),
        usedforsecurity=False,
    ).hexdigest()[:16]
    out_path = APP_CACHE_DIR / f'{path.stem}_rgba8_{digest}.ktx2'
    if not out_path.exists():
        _write_rgba8_ktx2(
            levels[0],
            width,
            height,
            out_path,
            mipmap_mode=mipmap_mode,
        )
    return out_path


def _composite_orm(
    baseline: Path | None,
    channels: dict[str, Path | None],
    *,
    cache_dir: Path,
    normal_source: Path | None,
    normal_baseline: Path | None,
) -> str | None:
    callback = cast(
        'Callable[..., str | None]',
        _lazy_attr('fleasion.cache.tools.orm_compositor', 'composite_orm'),
    )
    return _call_dynamic(
        callback,
        baseline,
        channels,
        cache_dir,
        normal_source=normal_source,
        normal_baseline=normal_baseline,
    )


def _preserve_replacement_key(value: object) -> _ReplacementKey:
    if TYPE_CHECKING:
        assert isinstance(value, str | int)
    return value


def _preserve_asset_id(value: object) -> _ReplacementKey | None:
    if TYPE_CHECKING:
        assert value is None or isinstance(value, str | int)
    return value


def _preserve_location(value: object) -> str | None:
    if TYPE_CHECKING:
        assert value is None or isinstance(value, str)
    return value


def _preserve_optional_str(value: object) -> str | None:
    if TYPE_CHECKING:
        assert value is None or isinstance(value, str)
    return value


def _mipmap_mode(map_index: int | None) -> _MipmapMode:
    return _MIPMAP_MODES.get(map_index, 'color')


def _read_replacement_bytes(path: Path) -> bytes | None:
    try:
        return strip_roblox_metadata(path, path.read_bytes())
    except OSError:
        return None


def _decode_request_entries(body: bytes) -> tuple[_JsonList, list[str]]:
    try:
        loaded = _loads(body)
    except TypeError, ValueError:
        return [], []
    if not isinstance(loaded, list):
        return [], []
    request_ids = [
        str(entry.get('requestId', '')) if isinstance(entry, dict) else '' for entry in loaded
    ]
    return loaded, request_ids


def _cdn_target_diagnostics(target: object) -> tuple[str, bool, int, str]:
    path = Path(str(target))
    exists = path.exists()
    size = path.stat().st_size if exists else 0
    return _file_value(str(target)), exists, size, path.suffix.lower()


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    body = chunk_type + data
    return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)


def _ensure_blank_png(path: Path) -> bool:
    try:
        signature = b'\x89PNG\r\n\x1a\n'
        ihdr = _png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
        idat = _png_chunk(b'IDAT', zlib.compress(b'\x00\xff\xff\xff\xff', 9))
        iend = _png_chunk(b'IEND', b'')
        path.write_bytes(signature + ihdr + idat + iend)
    except (OSError, struct.error, zlib.error) as exc:
        log_buffer.log('TexPack', f'Failed to create blank placeholder PNG: {exc}')
        return False
    log_buffer.log('TexPack', 'Created blank 1×1 placeholder PNG')
    return True


# Use orjson when available (2-3x faster JSON parse)
try:
    import orjson

    def _loads(data: bytes) -> _JsonValue:
        return require_json_value(orjson.loads(data))

    def _dumps(obj: _JsonValue) -> bytes:
        return orjson.dumps(obj)
except ImportError:

    def _loads(data: bytes) -> _JsonValue:
        return require_json_value(json.loads(data))

    def _dumps(obj: _JsonValue) -> bytes:
        return json.dumps(obj, separators=(',', ':')).encode()


logger = logging.getLogger(__name__)

_ZSTD_MAGIC = b'\x28\xb5\x2f\xfd'
_GZIP_MAGIC = b'\x1f\x8b'

_TEXPACK_SLOT_NAMES = {
    0: 'Color',
    1: 'Normal',
    2: 'ORM',
}


def _texpack_slot_label(slot: int | None) -> str:
    if slot is None:
        return 'unknown'
    name = _TEXPACK_SLOT_NAMES.get(slot, 'unknown')
    return f'{slot}:{name}'


def _short_value(value: object, limit: int = 120) -> str:
    if value is None:
        return 'remove/default'
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + '...'


def _file_value(value: object) -> str:
    if value is None:
        return 'remove/default'
    text = str(value)
    if text.startswith(('http://', 'https://')):
        return _short_value(text)
    return Path(text).name or text


def _b64decode_padded(value: object) -> bytes:
    raw = str(value).encode('ascii', errors='ignore')
    raw += b'=' * (-len(raw) % 4)
    try:
        return base64.urlsafe_b64decode(raw)
    except ValueError as exc:
        msg = 'invalid base64 payload'
        raise ValueError(msg) from exc


def _normalized_build_type(value: object) -> _BuildType:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        try:
            return int(text)
        except ValueError:
            pass
    return ''.join(ch for ch in text.lower() if ch.isalnum())


def _texpack_slot_from_build_type(value: object) -> int | None:
    normalized = _normalized_build_type(value)
    if isinstance(normalized, int):
        return normalized if 0 <= normalized <= 2 else None
    if not isinstance(normalized, str) or not normalized:
        return None
    if any(token in normalized for token in ('color', 'albedo', 'diffuse', 'basecolor')):
        return 0
    if any(token in normalized for token in ('normal', 'bump')):
        return 1
    orm_tokens = ('metal', 'rough', 'emiss', 'height', 'displace')
    if normalized == 'orm' or any(token in normalized for token in orm_tokens):
        return 2
    return None


def _texpack_slot_from_request(e: _JsonObject) -> int | None:
    for key in (
        'requestedBuildType',
        'buildType',
        'textureType',
        'textureMap',
        'mapType',
        'contentType',
    ):
        slot = _texpack_slot_from_build_type(e.get(key))
        if slot is not None:
            return slot
    return None


def _texpack_build_key(e: _JsonObject) -> _BuildType:
    for key in (
        'requestedBuildType',
        'buildType',
        'textureType',
        'textureMap',
        'mapType',
        'contentType',
    ):
        value = _normalized_build_type(e.get(key))
        if value is not None:
            return value
    return None


def _representation_matches_requested(representation: _JsonObject, requested: _BuildType) -> bool:
    for key in (
        'requestedBuildType',
        'buildType',
        'contentType',
        'type',
        'format',
        'name',
        'representationType',
    ):
        if _normalized_build_type(representation.get(key)) == requested:
            return True
    return False


def _requested_content_representation(
    decoded: _JsonList,
    requested: _BuildType,
) -> _JsonObject | None:
    for representation in decoded:
        if isinstance(representation, dict) and _representation_matches_requested(
            representation,
            requested,
        ):
            return representation
    if isinstance(requested, int) and 0 <= requested < len(decoded):
        representation = decoded[requested]
        if isinstance(representation, dict):
            return representation
    return None


def _select_content_representation(e: _JsonObject) -> _JsonObject | None:
    crpl = e.get('contentRepresentationPriorityList')
    if not crpl:
        return None
    try:
        decoded = _loads(_b64decode_padded(crpl))
    except TypeError, ValueError:
        return None
    if not isinstance(decoded, list) or not decoded:
        return None

    requested = _normalized_build_type(e.get('requestedBuildType'))
    if requested is not None:
        selected = _requested_content_representation(decoded, requested)
        if selected is not None:
            return selected

    first = decoded[0]
    return first if isinstance(first, dict) else None


def _decode_fidelity_slot_quality(fidelity_b64: object | None) -> tuple[int, int] | None:
    if not fidelity_b64:
        return None
    try:
        fb = _b64decode_padded(fidelity_b64)
    except TypeError, ValueError:
        return None
    if len(fb) < 2:
        return None
    slot = (fb[0] & 0x60) >> 5
    if slot > 2:
        return None
    quality = (fb[1] & 0xC0) >> 6
    return slot, quality


def _decode_texpack_slot_quality(e: _JsonObject) -> tuple[int, int] | None:
    request_slot = _texpack_slot_from_request(e)
    if request_slot is not None:
        return request_slot, 0
    representation = _select_content_representation(e)
    if not representation:
        return None
    return _decode_fidelity_slot_quality(representation.get('fidelity'))


def _decode_selected_representation_slot_quality(e: _JsonObject) -> tuple[int, int] | None:
    representation = e.get('contentRepresentationSpecifier')
    if not isinstance(representation, dict):
        return None
    return _decode_fidelity_slot_quality(representation.get('fidelity'))


if TYPE_CHECKING:
    _ = _decode_selected_representation_slot_quality


def _decompress_cdn_response(data: bytes) -> bytes:
    if data[:4] == _ZSTD_MAGIC:
        data = _zstd_decompress(data)
        log_buffer.log('CDN', f'Decompressed zstd CDN payload: {len(data)} bytes')
    elif data[:2] == _GZIP_MAGIC:
        data = gzip.decompress(data)
        log_buffer.log('CDN', f'Decompressed gzip CDN payload: {len(data)} bytes')
    return data


def _inject_obj_into_solidmodel(bin_data: bytes, obj_path: Path, prefer_v3: bool = False) -> bytes:
    bin_data = _decompress_cdn_response(bin_data)
    doc = _deserialize_rbxm(bin_data)
    injectable = frozenset(
        {'PartOperationAsset', 'UnionOperation', 'NegateOperation', 'PartOperation'}
    )

    csg_version = 3
    if prefer_v3:
        log_buffer.log('SolidModel', 'Using forced CSGMDL v3 for direct OBJ replacement')
    else:
        for inst in doc.roots:
            if inst.class_name in injectable:
                prop = inst.properties.get('MeshData')
                if prop is not None and prop.value:
                    mesh_bytes = (
                        prop.value
                        if isinstance(prop.value, bytes)
                        else bytes(prop.value, 'latin-1')
                    )
                    detected = _detect_csgmdl_version(mesh_bytes)
                    if detected is not None:
                        csg_version = detected
                        log_buffer.log('SolidModel', f'Detected original CSGMDL v{csg_version}')
                break

    csg_bytes = _export_csg_mesh(obj_path, version=csg_version)
    injected = 0
    for inst in doc.roots:
        if inst.class_name in injectable:
            inst.properties['MeshData'] = _make_rbx_property(
                'MeshData',
                'STRING',
                csg_bytes,
            )
            inst.properties['Color'] = _make_rbx_property(
                'Color',
                'COLOR3UINT8',
                {'R': 255, 'G': 255, 'B': 255},
            )
            injected += 1

    if injected == 0:
        msg = f'No injectable root (roots: {[r.class_name for r in doc.roots]})'
        raise ValueError(msg)
    log_buffer.log('SolidModel', f'Injected CSGMDL into {format_count(injected, "root")}')
    return _write_rbxm(doc)


def _try_mesh_to_obj(path: Path, ctx: str) -> Path | None:
    try:
        return _mesh_file_to_cached_obj(path)
    except _BEST_EFFORT_ERRORS as exc:
        log_buffer.log('Intermediary', f'{ctx}: .mesh->OBJ failed: {exc}')
        return None


def _decompress_bin_candidate(raw: bytes) -> bytes:
    if raw[:4] == b'\x28\xb5\x2f\xfd':
        return _zstd_decompress(raw)
    if raw[:2] == b'\x1f\x8b':
        return gzip.decompress(raw)
    return raw


def _contains_injectable_csgmdl(data: bytes) -> bool:
    if not _is_binary_rbxm(data):
        return False
    doc = _deserialize_rbxm(data)
    injectable = frozenset(
        {'PartOperationAsset', 'UnionOperation', 'NegateOperation', 'PartOperation'}
    )
    for inst in doc.roots:
        if inst.class_name not in injectable:
            continue
        prop = inst.properties.get('MeshData')
        if prop is None or not prop.value:
            continue
        mesh_bytes = prop.value if isinstance(prop.value, bytes) else bytes(prop.value, 'latin-1')
        if _detect_csgmdl_version(mesh_bytes) is not None:
            return True
    return False


def _is_csgmdl_bin(path: Path) -> bool:
    """Return whether a .bin file contains an injectable CSGMDL payload."""
    try:
        return _contains_injectable_csgmdl(_decompress_bin_candidate(path.read_bytes()))
    except _BEST_EFFORT_ERRORS:
        return False


def _try_bin_to_obj(path: Path, ctx: str) -> Path | None:
    try:
        return _bin_file_to_cached_obj(path)
    except _BEST_EFFORT_ERRORS as exc:
        log_buffer.log('Intermediary', f'{ctx}: .bin->OBJ failed: {exc}')
        return None


def _try_rbxmx_to_obj(path: Path, ctx: str) -> Path | None:
    try:
        return _rbxmx_file_to_cached_obj(path)
    except _BEST_EFFORT_ERRORS as exc:
        log_buffer.log('Intermediary', f'{ctx}: .rbxmx->OBJ failed: {exc}')
        return None


def _download_remote_file(url: str, dest: Path, label: str) -> bool:
    try:
        APP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            return True
        log_buffer.log('Downloader', f'Downloading remote {label}: {url}')
        http_download_to(
            url,
            dest,
            timeout=30,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            },
        )
    except (OSError, RuntimeError, ValueError) as exc:
        log_buffer.log('Downloader', f'Failed to download {label}: {exc}')
        return False
    log_buffer.log('Downloader', f'Saved {label}: {dest.name}')
    return True


class TextureStripper:
    """Modifies Roblox asset batch requests/responses and manages CDN redirects."""

    # Shared singleton state (class-level)
    _lock: Lock = Lock()
    _pending: ClassVar[dict[str, tuple[str, str]]] = {}  # requestId -> (kind, value)
    _cdn_redirects: ClassVar[dict[str, str]] = {}  # base_cdn_url -> redirect_url
    _local_redirects: ClassVar[dict[str, str]] = {}  # base_cdn_url -> local_path
    _solidmodel_injections: ClassVar[dict[str, str]] = {}  # base_cdn_url -> obj_path
    _solidmodel_force_v3: ClassVar[set[str]] = (
        set()
    )  # base_cdn_url values that should force v3 CSG export
    _batch_generations: ClassVar[dict[str, int]] = {}  # batch_id -> route generation
    _routes_generation: int = 0

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
    _REVERSE: ClassVar[dict[str, int]] = {name.lower(): tid for tid, name in ASSET_TYPES.items()}

    def __init__(self, config_manager: ConfigManager) -> None:
        self.config_manager = config_manager
        self._cache_scraper: _CacheScraperLike | None = (
            None  # Set by ProxyMaster after construction
        )
        self._precheck_state_lock = Lock()
        self._precheck_retry_after: dict[int, float] = {}
        self._precheck_network_failure_count = 0
        self._seen_replacements_generation = getattr(
            config_manager, 'replacements_generation', None
        )

    @classmethod
    def reset_routes(cls, reason: str = '') -> dict[str, int]:
        """Discard every request-derived route without deleting reusable assets."""
        with cls._lock:
            counts = {
                'pending': len(cls._pending),
                'local': len(cls._local_redirects),
                'cdn': len(cls._cdn_redirects),
                'solid': len(cls._solidmodel_injections),
                'batches': len(cls._batch_generations),
            }
            cls._pending.clear()
            cls._local_redirects.clear()
            cls._cdn_redirects.clear()
            cls._solidmodel_injections.clear()
            cls._solidmodel_force_v3.clear()
            cls._batch_generations.clear()
            cls._routes_generation += 1
        with cls._anim_lock:
            counts['anim_pending'] = len(cls._anim_local_pending)
            counts['anim_routes'] = len(cls._anim_rig_local)
            cls._anim_local_pending.clear()
            cls._anim_rig_local.clear()

        if reason and any(counts.values()):
            summary = ', '.join(f'{name}={count}' for name, count in counts.items())
            log_buffer.log('Replacer', f'Cleared stale routes ({reason}): {summary}')
        return counts

    def _sync_replacements_generation(self) -> None:
        generation = getattr(self.config_manager, 'replacements_generation', None)
        if generation is None or generation == self._seen_replacements_generation:
            return
        self.reset_routes('replacement configuration changed')
        self._seen_replacements_generation = generation

    def _register_batch(self, batch_id: str) -> int:
        self._sync_replacements_generation()
        with self._lock:
            generation = self._routes_generation
            self._batch_generations[batch_id] = generation
            return generation

    def _queue_pending(self, req_id: str, value: _PendingValue) -> bool:
        batch_id = req_id.split('_', 1)[0]
        with self._lock:
            generation = self._batch_generations.get(batch_id)
            if generation is None or generation != self._routes_generation:
                return False
            self._pending[req_id] = value
            return True

    def _queue_anim_pending(self, req_id: str, value: _AnimPendingValue) -> bool:
        batch_id = req_id.split('_', 1)[0]
        with self._lock:
            generation = self._batch_generations.get(batch_id)
            if generation is None or generation != self._routes_generation:
                return False
            with self._anim_lock:
                self._anim_local_pending[req_id] = value
            return True

    def set_cache_scraper(self, scraper: _CacheScraperLike) -> None:
        """Wire in the CacheScraper for place-ID lookups on replacement assets."""
        self._cache_scraper = scraper

    # Pre-downloaded private replacement assets: replacement_id -> local file path.
    # Populated eagerly at proxy startup by precheck_replacements().
    _predownloaded: ClassVar[dict[int, str]] = {}
    # IDs confirmed publicly accessible (no pre-download needed).
    _checked_public: ClassVar[set[int]] = set()
    # IDs currently being checked in a precheck thread (to avoid duplicate spawns).
    _precheck_pending: ClassVar[set[int]] = set()

    _PREDOWNLOAD_DIR: Path = APP_CACHE_DIR / 'predownloaded'
    _PRECHECK_NETWORK_RETRY_BASE_SECONDS = 120.0
    _PRECHECK_NETWORK_RETRY_MAX_SECONDS = 15 * 60.0
    _PRECHECK_HTTP_RETRY_SECONDS = 15 * 60.0

    # Animation type IDs (main + all subtypes)
    _ANIM_TYPE_IDS: frozenset[int] = frozenset({24, 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 78})
    _CONV_CACHE_DIR: Path = APP_CACHE_DIR / 'rig_converted'

    # Virtual rig-filter type keys -> required original rig ('R6', 'R15', 'unknown')
    _VIRTUAL_ANIM_RIG: ClassVar[dict[str, str]] = {
        'R6Animation': 'R6',
        'R15Animation': 'R15',
        'NonPlayerAnimation': 'unknown',
    }

    # rig of replacement local file, keyed by normalised path string
    _anim_repl_rig: ClassVar[dict[str, str]] = {}
    # converted file path, keyed by f'{content_hash16}_{target_rig}'
    _anim_conv_paths: ClassVar[dict[str, str]] = {}
    # CDN URLs for animation replacements that need upstream rig detection before serving.
    # Populated by process_batch_response; checked by check_cdn_request.
    # These do NOT short-circuit - upstream response is read to detect original rig.
    # Value: (local_path, required_rig) where required_rig is 'R6'|'R15'|'unknown'|'any'
    _anim_rig_local: ClassVar[
        dict[str, _AnimPendingValue]
    ] = {}  # base_cdn_url -> (local_path, required_rig)
    # pending_key -> (local_path, required_rig)
    _anim_local_pending: ClassVar[dict[str, _AnimPendingValue]] = {}
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
        self._precheck_texpack_layouts(replacements_tuple)
        replacements = replacements_tuple[0]  # dict[int, int]: original -> replacement

        if not replacements:
            return

        # Deduplicate and keep failed probes on a cooldown. A failed target used
        # to become immediately "unknown" again, so the first asset batch could
        # start all 100+ startup probes a second time while the network was
        # already failing.
        now = time.monotonic()
        with self._precheck_state_lock:
            unique_targets = {
                int(target_id)
                for target_id in replacements.values()
                if int(target_id) not in self._precheck_pending
                and self._precheck_retry_after.get(int(target_id), 0.0) <= now
            }
            self._precheck_pending.update(unique_targets)
        if not unique_targets:
            return
        log_buffer.log(
            'Replacer',
            f'Pre-checking {format_count(unique_targets, "replacement asset")}...',
        )

        self._PREDOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

        cookie = _scraper_get_roblosecurity(scraper, wait=True)
        extra: dict[str, str] = {}
        if cookie:
            extra['Cookie'] = f'.ROBLOSECURITY={cookie};'

        public_count = 0
        private_count = 0
        failed_count = 0

        targets = sorted(unique_targets)
        network_deferred: set[int] = set()
        for index, target_id in enumerate(targets):
            local_path = self._PREDOWNLOAD_DIR / f'{target_id}.dat'
            legacy_path = self._PREDOWNLOAD_DIR / f'{target_id}.bin'

            # Do not trust a previous-session file until the accessibility
            # check below establishes that this target actually needs the
            # private/local route. Public non-animation assets must remain ID
            # swaps; otherwise TexturePack XML is served as texture bytes.
            if legacy_path.exists() and legacy_path.stat().st_size > 0:
                legacy_path.rename(local_path)

            # Quick accessibility check — needs auth cookie just to use the API.
            # A 200 here means the asset is publicly downloadable (no place-ID
            # needed); the cookie is required for API auth, not ownership.
            data_, status = _scraper_https_get_status(
                scraper,
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={target_id}',
                extra_headers=dict(extra) if extra else None,
            )
            if data_:
                # 200 — publicly accessible.
                # Only animations require a local copy for rig conversion.
                # All other public assets use the normal ID swap.
                needs_rig_conversion = any(
                    value == target_id and self._is_anim_replacement_key(key)
                    for key, value in replacements.items()
                )
                if needs_rig_conversion:
                    try:
                        local_path.write_bytes(data_)
                        self._predownloaded[int(target_id)] = str(local_path)
                        log_buffer.log(
                            'Replacer',
                            f'Cached public animation {target_id} for rig conversion ({len(data_)} bytes)',
                        )
                    except OSError:
                        self._checked_public.add(int(target_id))
                else:
                    self._predownloaded.pop(int(target_id), None)
                    self._checked_public.add(int(target_id))
                public_count += 1
                continue

            if status == 404:
                log_buffer.log(
                    'Replacer',
                    f'Replacement asset {target_id} not found (404) — skipping',
                )
                failed_count += 1
                with self._precheck_state_lock:
                    self._precheck_retry_after[target_id] = (
                        time.monotonic() + self._PRECHECK_HTTP_RETRY_SECONDS
                    )
                continue

            if status != 403:
                log_buffer.log(
                    'Replacer',
                    f'Replacement asset {target_id} returned status {status} — skipping',
                )
                failed_count += 1
                if status is None:
                    # A transport failure is not an asset verdict. Stop the
                    # batch instead of hammering every remaining target while
                    # Windows, the router, or a WFP filter is unhealthy.
                    network_deferred.update(targets[index:])
                    break
                with self._precheck_state_lock:
                    self._precheck_retry_after[target_id] = (
                        time.monotonic() + self._PRECHECK_HTTP_RETRY_SECONDS
                    )
                continue

            # 403 — private asset, download via place-ID bypass
            if local_path.exists() and local_path.stat().st_size > 0:
                self._predownloaded[int(target_id)] = str(local_path)
                private_count += 1
                log_buffer.log('Replacer', f'Reusing cached private pre-download for {target_id}')
                continue
            log_buffer.log(
                'Replacer',
                f'Replacement asset {target_id} is private, pre-downloading...',
            )
            data, dl_status = _scraper_fetch_asset_with_place_id_retry(
                scraper,
                str(target_id),
                extra_headers=dict(extra) if extra else None,
            )
            if data:
                try:
                    local_path.write_bytes(data)
                    self._predownloaded[int(target_id)] = str(local_path)
                    private_count += 1
                    log_buffer.log(
                        'Replacer',
                        f'Pre-downloaded private asset {target_id} ({len(data)} bytes)',
                    )
                except OSError as exc:
                    log_buffer.log(
                        'Replacer',
                        f'Failed to save pre-download for {target_id}: {exc}',
                    )
                    failed_count += 1
            else:
                log_buffer.log(
                    'Replacer',
                    f'Could not pre-download private asset {target_id} (status {dl_status})',
                )
                failed_count += 1
                retry_seconds = (
                    self._PRECHECK_NETWORK_RETRY_BASE_SECONDS
                    if dl_status is None
                    else self._PRECHECK_HTTP_RETRY_SECONDS
                )
                with self._precheck_state_lock:
                    self._precheck_retry_after[target_id] = time.monotonic() + retry_seconds
                if dl_status is None:
                    network_deferred.update(targets[index:])
                    break

        with self._precheck_state_lock:
            self._precheck_pending.difference_update(unique_targets)
            if network_deferred:
                self._precheck_network_failure_count += 1
                exponent = min(self._precheck_network_failure_count - 1, 3)
                network_retry_seconds = min(
                    self._PRECHECK_NETWORK_RETRY_BASE_SECONDS * (2**exponent),
                    self._PRECHECK_NETWORK_RETRY_MAX_SECONDS,
                )
            else:
                self._precheck_network_failure_count = 0
                network_retry_seconds = self._PRECHECK_NETWORK_RETRY_BASE_SECONDS
            retry_at = time.monotonic() + network_retry_seconds
            for target_id in network_deferred:
                self._precheck_retry_after[target_id] = retry_at
        if network_deferred:
            log_buffer.log(
                'Replacer',
                f'Paused replacement precheck after a network failure; '
                f'{len(network_deferred)} target(s) deferred for '
                f'{network_retry_seconds:.0f}s',
            )
        log_buffer.log(
            'Replacer',
            f'Pre-check complete: {public_count} public, {private_count} private (pre-downloaded), {failed_count} failed',
        )

    def _precheck_texpack_layouts(self, replacements_tuple: ReplacementMaps) -> None:
        """Pre-fetch layouts for pack-specific ORM slot keys in any replacement mode."""
        scraper = self._cache_scraper
        if scraper is None or not hasattr(scraper, 'prefetch_texpack_layout'):
            return

        replacements, removals, cdn_replacements, local_replacements = replacements_tuple
        replacements = {
            key: value
            for key, value in replacements.items()
            if self._normalize_asset_id(value) not in {0, 1}
        }
        parent_ids: set[int] = set()
        for key in (
            set(replacements.keys())
            | set(cdn_replacements.keys())
            | set(local_replacements.keys())
            | set(removals)
        ):
            if not isinstance(key, str) or ':' not in key:
                continue
            parent_raw, global_index_raw = key.split(':', 1)
            if not parent_raw.isdigit() or not global_index_raw.isdigit():
                continue
            if int(global_index_raw) >= 2:
                parent_ids.add(int(parent_raw))

        for parent_id in sorted(parent_ids):
            log_buffer.log(
                'TexPackTrace',
                f'Precheck: fetching TexturePack XML layout for pack {parent_id} because a slot >=2 rule exists',
            )
            scraper.prefetch_texpack_layout(parent_id)

    # Rig auto-conversion helpers

    def _is_anim_asset_id(self, asset_id: int) -> bool:
        """Check whether an asset ID is an animation type via the economy API."""
        scraper = self._cache_scraper
        if scraper is None:
            return False
        try:
            cookie = _scraper_get_roblosecurity(scraper)
            extra = {'Cookie': f'.ROBLOSECURITY={cookie};'} if cookie else {}
            data = _scraper_https_get(
                scraper,
                'economy.roblox.com',
                f'/v2/assets/{asset_id}/details',
                extra_headers=extra or None,
            )
            if data:
                info = json.loads(data)
                return int(info.get('AssetTypeId', -1)) in self._ANIM_TYPE_IDS
        except OSError, RuntimeError, TypeError, ValueError:
            pass
        return False

    def _is_anim_replacement_key(self, key: object) -> bool:
        """Return True if this local-replacement key targets an animation asset."""
        # All animation-related string keys: virtual rig-filter types + every
        # named animation asset type from ASSET_TYPES
        anim_str_keys = frozenset(
            name for tid, name in self.ASSET_TYPES.items() if tid in self._ANIM_TYPE_IDS
        ) | frozenset(self._VIRTUAL_ANIM_RIG)
        if isinstance(key, str) and key in anim_str_keys:
            return True
        # TexturePack slot keys (e.g. "12345:2") are never animations
        if isinstance(key, str) and ':' in key:
            return False
        # Numeric asset ID — look up via economy API
        try:
            aid = int(_preserve_replacement_key(key))
        except TypeError, ValueError:
            return False
        return self._is_anim_asset_id(aid)

    def precheck_anim_rigs(self) -> None:
        try:
            self._CONV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        replacements_tuple = self.config_manager.get_all_replacements()
        _, _, _, local_replacements = replacements_tuple

        anim_exts = {'.rbxm', '.rbxmx'}

        # Collect local paths that are definitely animation replacements.
        # Check: animation key/type, .rbxm/.rbxmx extension, or asset type via API.
        paths_to_process: list[str] = []

        for key, path_value in local_replacements.items():
            local_path = str(path_value)
            if local_path in paths_to_process:
                continue
            if Path(local_path).suffix.lower() in anim_exts or self._is_anim_replacement_key(key):
                paths_to_process.append(local_path)

        # Predownloaded ID-to-ID replacements: check replacement asset ID via API,
        # then fall back to extension and magic-byte sniffing.
        for repl_id, path_value in self._predownloaded.items():
            local_path = str(path_value)
            if local_path in paths_to_process:
                continue
            if Path(local_path).suffix.lower() in anim_exts:
                paths_to_process.append(local_path)
                continue
            # Check the replacement asset ID itself via economy API
            try:
                if self._is_anim_asset_id(int(repl_id)):
                    paths_to_process.append(local_path)
                    continue
            except TypeError, ValueError:
                pass
            # Last resort: peek at magic bytes for .dat / extensionless files
            path = Path(local_path)
            data = _read_replacement_bytes(path)
            if data is not None:
                head = data[:64]
                if (
                    head.startswith(b'<roblox!')
                    or b'KeyframeSequence' in head
                    or b'CurveAnimation' in head
                ):
                    paths_to_process.append(local_path)

        converted = 0
        for local_path in paths_to_process:
            p = Path(local_path)
            if not p.exists():
                continue
            data = _read_replacement_bytes(p)
            if data is None:
                continue

            # Only process animation files
            rig = _detect_rig(data)
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

        log_buffer.log(
            'AnimConv',
            f'Pre-conversion complete: {format_count(converted, "animation")} processed',
        )

    def _get_or_create_converted(
        self, local_path: str, target_rig: str, data: bytes | None = None
    ) -> str | None:
        """Return path to a rig-converted copy of local_path, creating it if needed."""
        p = Path(local_path)
        if not p.exists():
            return None

        try:
            if data is None:
                data = strip_roblox_metadata(p, p.read_bytes())
            content_key = hashlib.sha256(data).hexdigest()[:16]
        except OSError:
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
            _write_rig_converted_animation(data, out_path, target_rig)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log('AnimConv', f'Conversion failed for {p.name} -> {target_rig}: {exc}')
            return None

        with self._anim_lock:
            self._anim_conv_paths[cache_key] = str(out_path)
        log_buffer.log('AnimConv', f'Created {target_rig} version: {out_path.name}')
        return str(out_path)

    def _get_or_create_converted_curve(
        self, local_path: str, target_rig: str, data: bytes | None = None
    ) -> str | None:
        """Return path to a rig-converted CurveAnimation copy of local_path, creating it if needed.

        Pipeline: source -> XML (if binary) -> KeyframeSequence (if CurveAnimation)
                  -> rig-convert if needed -> CurveAnimation
        """
        p = Path(local_path)
        if not p.exists():
            return None

        try:
            if data is None:
                data = strip_roblox_metadata(p, p.read_bytes())
            content_key = hashlib.sha256(data).hexdigest()[:16]
        except OSError:
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
            _write_curve_converted_animation(data, out_path, target_rig)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log(
                'AnimConv',
                f'CurveAnim conversion failed for {p.name} -> {target_rig}: {exc}',
            )
            return None

        with self._anim_lock:
            self._anim_conv_paths[cache_key] = str(out_path)
        log_buffer.log(
            'AnimConv',
            f'Created {target_rig} CurveAnimation version: {out_path.name}',
        )
        return str(out_path)

    def _detect_repl_rig(self, local_path: str) -> str:
        """Detect and cache the rig type of a local replacement animation file."""
        with self._anim_lock:
            if local_path in self._anim_repl_rig:
                return self._anim_repl_rig[local_path]
        try:
            path = Path(local_path)
            rig = _detect_rig(strip_roblox_metadata(path, path.read_bytes()))
        except _BEST_EFFORT_ERRORS:
            rig = 'unknown'
        with self._anim_lock:
            self._anim_repl_rig[local_path] = rig
        return rig

    def _is_anim_entry(self, e: _JsonObject) -> bool:
        """Return True if batch entry is an animation asset type."""
        tid = e.get('assetTypeId')
        if tid in self._ANIM_TYPE_IDS:
            return True
        at_name = str(e.get('assetType', '')).lower()
        mapped = self._REVERSE.get(at_name)
        return mapped in self._ANIM_TYPE_IDS

    def _route_texpack_slot_id_replacement(
        self,
        *,
        batch_id: str,
        req_id: object,
        aid: _ReplacementKey,
        replacement_id: int,
        source_key: _ReplacementKey,
        map_index: int | None,
        is_solidmodel: bool,
    ) -> bool:
        scraper = self._cache_scraper
        if scraper is None:
            return False

        local_tgt = self._predownloaded.get(replacement_id)
        if local_tgt is None:
            dl_path = APP_CACHE_DIR / f'predownloaded/{replacement_id}.dat'
            dl_path.parent.mkdir(parents=True, exist_ok=True)
            if not dl_path.exists():
                log_buffer.log(
                    'Replacer',
                    f'Downloading asset {replacement_id} for KTX2 conversion...',
                )
                extra_hdrs: dict[str, str] = {}
                cookie = _scraper_get_roblosecurity(scraper)
                if cookie:
                    extra_hdrs['Cookie'] = f'.ROBLOSECURITY={cookie};'
                scraped_data, _dl_status = _scraper_fetch_asset_with_place_id_retry(
                    scraper,
                    str(replacement_id),
                    extra_headers=extra_hdrs or None,
                )
                if scraped_data:
                    dl_path.write_bytes(scraped_data)
            if dl_path.exists():
                local_tgt = str(dl_path)

        if local_tgt is None:
            return False

        self._route_local(
            f'{batch_id}_{req_id}',
            aid,
            local_tgt,
            is_solidmodel=is_solidmodel,
            is_texpack=True,
            source_key=source_key,
            map_index=map_index,
        )
        return True

    # Batch request (called from server MITM thread)

    def process_batch_request(
        self,
        body: bytes,
        req_headers: dict[str, str],
        replacements_tuple: ReplacementMaps,
        batch_id: str = '',
    ) -> tuple[bytes, bytes] | bytes:
        """Modify batch JSON: removals, ID replacements, CDN/local routing.

        Returns ``(modified_body, scraper_body)`` where *scraper_body* has
        the original asset IDs restored (index-aligned with the upstream
        response) so the cache scraper stores content under original IDs.
        """
        del req_headers
        if not body:
            return body, body
        try:
            data = _loads(body)
        except TypeError, ValueError:
            return body
        if not isinstance(data, list):
            return body
        self._register_batch(batch_id)
        # The server's tuple may have been read just before a UI config toggle.
        # Resolve it again after synchronizing the generation so this batch is
        # never populated from the old config snapshot.
        if getattr(self.config_manager, 'replacements_generation', None) is not None:
            replacements_tuple = self.config_manager.get_all_replacements()

        replacements, removals, cdn_replacements, local_replacements = replacements_tuple
        replacements = {
            key: value
            for key, value in replacements.items()
            if self._normalize_asset_id(value) not in {0, 1}
        }
        # Move pre-downloaded private replacements into local_replacements so
        # they follow the exact same code path as user-configured local files
        # (keeps batch body unmodified, CDN URL mapped at response time).
        if self._predownloaded:
            replacements = dict(replacements)
            local_replacements = dict(local_replacements)
            for orig_id, repl_id in list(replacements.items()):
                predownloaded = self._predownloaded.get(int(repl_id))
                if predownloaded is not None:
                    # A TexturePack API download is an XML manifest, not a
                    # texture payload. Never route it to Color/Normal/ORM CDN
                    # requests; retain the parent ID replacement instead.
                    try:
                        head = Path(predownloaded).read_bytes()[:2048].lower()
                    except OSError:
                        head = b''
                    if b'<texturepack_version>' in head:
                        continue
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
                all_src_ids = (
                    set(replacements.keys())
                    | set(cdn_replacements.keys())
                    | set(local_replacements.keys())
                    | removals
                )
                needs_resolve = all_src_ids & set(lookup.keys())
                if needs_resolve:
                    replacements = dict(replacements)
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
            now = time.monotonic()
            with self._precheck_state_lock:
                unknown = {
                    int(v)
                    for v in replacements.values()
                    if int(v) not in self._predownloaded
                    and int(v) not in self._checked_public
                    and int(v) not in self._precheck_pending
                    and self._precheck_retry_after.get(int(v), 0.0) <= now
                }
            if unknown:
                Thread(
                    target=self.precheck_replacements,
                    name='ReplacementPrecheck',
                    daemon=True,
                ).start()

        modified = False
        # Track original IDs for items that undergo ID replacement so the
        # scraper body can be built with original IDs after the loop.
        id_swapped: dict[int, _JsonValue] = {}  # index → original_aid

        # Convert TexturePack slot removals to blank-placeholder local routes.
        # Dropping a slot from the batch breaks the entire TexturePack in Roblox;
        # serving a 1x1 blank KTX2 keeps the pack intact for the other slots.
        # Matches "parentId:mapIndex" and wildcard "TexturePack:N" removal keys.
        synthetic_slot_removals: set[int | str] = set()
        if removals:
            tp_slot_removals = {
                r
                for r in removals
                if (
                    isinstance(r, str)
                    and ':' in r
                    and r.split(':', 1)[1].isdigit()
                    and (r.split(':', 1)[0].isdigit() or r.split(':', 1)[0] == 'TexturePack')
                )
            }
            if tp_slot_removals:
                blank = self._get_blank_ktx2_path()
                if blank:
                    removals = set(removals) - tp_slot_removals
                    local_replacements = dict(local_replacements)
                    for r in tp_slot_removals:
                        # An explicit replacement for this slot wins over a
                        # removal inherited from another enabled config.
                        if r not in local_replacements:
                            local_replacements[r] = blank
                            synthetic_slot_removals.add(r)
                    log_buffer.log(
                        'TexPack',
                        f'Routing {format_count(tp_slot_removals, "slot removal")} to blank placeholder',
                    )

        slot_target_ids: set[int] = set()
        all_texpack_rule_keys = (
            set(replacements.keys())
            | set(cdn_replacements.keys())
            | set(local_replacements.keys())
            | set(removals)
        )
        for key in all_texpack_rule_keys:
            if isinstance(key, str) and ':' in key:
                pk = key.split(':', 1)[0]
                if pk.isdigit():
                    slot_target_ids.add(int(pk))
        texpack_request_slots = self._build_texpack_request_slot_map(data, slot_target_ids)

        orig_len = len(data)
        filtered_data: _JsonList = []
        filtered_slots: dict[int, int] = {}
        for old_idx, e in enumerate(data):
            if not isinstance(e, dict):
                continue
            map_index = texpack_request_slots.get(old_idx)
            aid = self._normalize_asset_id(e.get('assetId'))
            slot_key = f'{aid}:{map_index}' if (aid is not None and map_index is not None) else None
            wildcard_key = f'TexturePack:{map_index}' if map_index is not None else None
            replacement_keys = (
                ([slot_key] if slot_key else [])
                + ([wildcard_key] if wildcard_key else [])
                + ([aid] if aid is not None else [])
                + self._get_type_keys(e)
            )
            if self._is_anim_entry(e):
                replacement_keys += list(self._VIRTUAL_ANIM_RIG)
            has_replacement = any(
                key in source
                and not (source is local_replacements and key in synthetic_slot_removals)
                for key in replacement_keys
                for source in (replacements, cdn_replacements, local_replacements)
            )
            if not has_replacement and self._should_remove(e, removals, map_index):
                continue
            new_idx = len(filtered_data)
            filtered_data.append(e)
            if old_idx in texpack_request_slots:
                filtered_slots[new_idx] = texpack_request_slots[old_idx]
        data = filtered_data
        texpack_request_slots = filtered_slots
        if len(data) < orig_len:
            log_buffer.log('Remover', f'Removed {format_count(orig_len - len(data), "asset")}')
            modified = True

        # Pre-build ORM channel override index.
        # Global indices 2-5 in local_replacements map to ORM sub-channels:
        #   GI2 = Metalness (ORM.R), GI3 = Roughness (ORM.G),
        #   GI4 = Emissive  (ORM.B), GI5 = Height    (ORM.A).
        # All route through the ORM compositor targeting Roblox fidelity slot 2
        # (the combined ORM CDN request).  The mapping is GLOBAL and FIXED —
        # it does NOT depend on the per-asset XML tag ordering.
        global_index_channel = {
            2: 'metalness',
            3: 'roughness',
            4: 'emissive',
            5: 'height',
        }
        # _orm_overrides: pack_id_or_'TexturePack' -> {channel_name: local_path}
        orm_overrides: dict[int | str, dict[str, str | None]] = {}
        # Normal is a full physical slot (GI1), but ORM roughness mip generation
        # needs its vectors as an input when the same TexturePack overrides it.
        normal_overrides: dict[int | str, str | int | None] = {}
        # Seed GI1 from ID replacements, then let CDN/local replacements win on
        # the same key just like the normal routing specificity rules do.
        for nk, nv in replacements.items():
            if not isinstance(nk, str) or ':' not in nk:
                continue
            npk, ngi = nk.split(':', 1)
            if ngi != '1' or self._normalize_asset_id(nv) in {0, 1}:
                continue
            normal_overrides[int(npk) if npk.isdigit() else npk] = nv
        # Scan both cdn_replacements and local_replacements. Local replacements
        # are processed last so they win on key collisions.
        vs2_sources: dict[_ReplacementKey, str] = {**cdn_replacements, **local_replacements}
        for ck, cv in vs2_sources.items():
            if not isinstance(ck, str) or ':' not in ck:
                continue
            pk, gi_str = ck.split(':', 1)
            if not gi_str.isdigit():
                continue
            gi = int(gi_str)
            pk_key: int | str = int(pk) if pk.isdigit() else pk
            if gi == 1:
                normal_overrides[pk_key] = cv
                continue
            if gi < 2:
                continue  # GI0 is the Color full-slot route.
            ch = global_index_channel.get(gi)
            if not ch:
                continue
            # KTX2/KTX paths (e.g. blank placeholder) are not valid scalar PNG
            # sources; treat them as None = zero out the requested ORM channel.
            cv_value = _preserve_optional_str(cv)
            cv_resolved: str | None = (
                None
                if (cv_value is not None and cv_value.lower().endswith(('.ktx2', '.ktx')))
                else cv_value
            )
            orm_overrides.setdefault(pk_key, {})[ch] = cv_resolved
        for idx, e in enumerate(data):
            if not isinstance(e, dict):
                continue
            aid_raw = e.get('assetId')
            aid = self._normalize_asset_id(aid_raw)
            req_id = e.get('requestId')
            type_keys = self._get_type_keys(e)
            is_solidmodel = (e.get('assetTypeId') == 39) or (
                self._REVERSE.get(str(e.get('assetType', '')).lower()) == 39
            )

            # Build slot key from request metadata first. The fidelity field can
            # collapse distinct TexturePack CDN entries to slot 0.
            # slot_key = "assetId:mapIndex" (e.g. "7547298786:1" for the normal-map slot).
            # wildcard_key = "TexturePack:N" matches the N-th slot of ANY TexturePack.
            map_index = texpack_request_slots.get(idx)
            slot_key = f'{aid}:{map_index}' if (aid is not None and map_index is not None) else None
            wildcard_key = f'TexturePack:{map_index}' if map_index is not None else None

            # Resolve specificity across *all* replacement modes before doing
            # anything. An exact local/CDN rule must prevent a broader type ID
            # rule from rewriting the request to a shared asset, otherwise the
            # exact override becomes attached to CDN URLs used by every asset
            # rewritten by that type rule.
            replacement_key_groups: tuple[list[_ReplacementKey], ...] = (
                ([slot_key] if slot_key else []),
                ([aid] if aid is not None else []),
                ([wildcard_key] if wildcard_key else []),
                type_keys,
            )
            winning_keys: list[_ReplacementKey] = next(
                (
                    keys
                    for keys in replacement_key_groups
                    if any(
                        key in source
                        for key in keys
                        for source in (
                            replacements,
                            cdn_replacements,
                            local_replacements,
                        )
                    )
                ),
                list[_ReplacementKey](),
            )
            winning_local_key = next(
                (key for key in winning_keys if key in local_replacements), None
            )
            winning_cdn_key = next((key for key in winning_keys if key in cdn_replacements), None)
            matched = None
            if winning_local_key is None and winning_cdn_key is None:
                matched = next((key for key in winning_keys if key in replacements), None)
            if matched is not None:
                replacement_id = replacements[matched]

                # Only a slot/sub-asset rule represents a single image that
                # needs downloading and KTX2 conversion.  Whole TexturePack
                # ID/type rules must keep the normal parent-ID swap below so
                # Roblox resolves every map from the replacement pack.  Treating
                # a whole pack as an image downloads its XML and serves that XML
                # for every slot, effectively removing all textures.
                is_texpack_slot_match = matched in {slot_key, wildcard_key}

                if (
                    is_texpack_slot_match
                    and req_id
                    and aid
                    and str(replacement_id).isdigit()
                    and self._route_texpack_slot_id_replacement(
                        batch_id=batch_id,
                        req_id=req_id,
                        aid=aid,
                        replacement_id=int(replacement_id),
                        source_key=matched,
                        map_index=map_index,
                        is_solidmodel=is_solidmodel,
                    )
                ):
                    modified = True
                    continue  # Skip the usual e['assetId'] = replacement_id logic

                e['assetId'] = replacement_id
                id_swapped[idx] = aid_raw
                slot_info = f' (slot {map_index})' if (slot_key and slot_key == matched) else ''
                log_buffer.log('Replacer', f'Replaced {aid} -> {replacement_id}{slot_info}')
                modified = True

            # CDN / local routing — slot key / wildcard key takes priority
            if req_id and aid:
                # ORM channel compositing (virtual slots 2-5)
                # When the fidelity slot is 2 (ORM) and per-channel PNG overrides
                # are configured via VSN keys (N≥2), composite them into one texture.
                # This check runs BEFORE normal local_key routing so that e.g.
                # "packId:2 → metalness.png" is composited rather than served raw.
                if map_index == 2 and orm_overrides:
                    orm_chs: dict[str, str | None] = {}
                    # Wildcard always lowest priority
                    orm_chs.update(orm_overrides.get('TexturePack', {}))
                    # Pack-specific overrides win
                    if aid in orm_overrides:
                        orm_chs.update(orm_overrides[aid])
                    if orm_chs:
                        normal_source = normal_overrides.get('TexturePack')
                        if aid in normal_overrides:
                            normal_source = normal_overrides[aid]
                        comp = self._build_orm_composite(
                            aid,
                            orm_chs,
                            normal_source=normal_source,
                        )
                        if comp:
                            self._route_local(
                                f'{batch_id}_{req_id}',
                                aid,
                                comp,
                                is_solidmodel=is_solidmodel,
                                is_texpack=True,
                                source_key=f'ORM[{", ".join(sorted(orm_chs))}]',
                                map_index=map_index,
                            )
                            modified = True
                            continue
                        # Composite failed — fall through to normal routing as best-effort

                # Only the globally winning specificity may route this asset.
                # Prefer explicit local content, then CDN, then the ID rule
                # already handled above when conflicting configs use the same
                # key at the same specificity.
                all_keys = list(winning_keys)
                # For animation entries, also check virtual rig-filter keys as fallback
                if self._is_anim_entry(e):
                    all_keys += list(self._VIRTUAL_ANIM_RIG)

                cdn_key = winning_cdn_key
                # Prefer real local replacements over blank routes synthesized
                # from removal rules. A synthesized route is only a fallback
                # when no replacement of any kind matched this asset.
                local_key = (
                    winning_local_key if winning_local_key not in synthetic_slot_removals else None
                )
                if local_key is None and matched is None and cdn_key is None:
                    local_key = next(
                        (
                            k
                            for k in all_keys
                            if k in local_replacements and k in synthetic_slot_removals
                        ),
                        None,
                    )
                if local_key is not None:
                    # Check if this replacement specifically targets a TexturePack slot or type
                    is_texpack = (
                        (':' in str(local_key))
                        or (e.get('assetTypeId') == 63)
                        or (self._REVERSE.get(str(e.get('assetType', '')).lower()) == 63)
                    )
                    repl_local_path = local_replacements[local_key]
                    self._route_local(
                        f'{batch_id}_{req_id}',
                        aid,
                        repl_local_path,
                        is_solidmodel=is_solidmodel,
                        is_texpack=is_texpack,
                        source_key=local_key,
                        map_index=map_index,
                    )
                    # Tag animation replacements for upstream rig detection.
                    # Determine required_rig from virtual type keys, or 'any' for normal types.
                    if self._is_anim_entry(e):
                        if str(local_key) in self._VIRTUAL_ANIM_RIG:
                            # Collect all virtual keys in local_replacements pointing to the
                            # same file — user may have "R6Animation, R15Animation" in one rule.
                            covered = frozenset(
                                self._VIRTUAL_ANIM_RIG[vk]
                                for vk in self._VIRTUAL_ANIM_RIG
                                if local_replacements.get(vk) == repl_local_path
                            )
                            required_rig = 'any' if covered >= {'R6', 'R15', 'unknown'} else covered
                        else:
                            required_rig = 'any'
                        self._queue_anim_pending(
                            f'{batch_id}_{req_id}',
                            (str(repl_local_path), required_rig),
                        )
                elif cdn_key is not None:
                    is_texpack_cdn = (
                        (':' in str(cdn_key))
                        or (e.get('assetTypeId') == 63)
                        or (self._REVERSE.get(str(e.get('assetType', '')).lower()) == 63)
                    )
                    self._route_cdn(
                        f'{batch_id}_{req_id}',
                        aid,
                        cdn_replacements[cdn_key],
                        is_solidmodel=is_solidmodel,
                        is_texpack=is_texpack_cdn,
                        map_index=map_index,
                    )

        if modified:
            result = _dumps(data)
            # Build scraper body: same structure but with original IDs restored
            # so the cache scraper stores content under the original asset IDs.
            if id_swapped:
                for i, orig_aid in id_swapped.items():
                    item = as_json_object(data[i])
                    if item is None:
                        continue
                    item['assetId'] = orig_aid
                scraper_body = _dumps(data)
            else:
                scraper_body = result
            return result, scraper_body
        return body, body

    def _process_batch_response_entry_locked(
        self,
        *,
        idx: int,
        item: _JsonObject,
        batch_id: str,
        req_data: _JsonList,
        req_ids_by_index: list[str],
        texpack_request_slots: dict[int, int],
        local_replacements: dict[_ReplacementKey, str],
        cdn_replacements: dict[_ReplacementKey, str],
        orm_overrides: dict[int | str, dict[str, str | None]],
        normal_overrides: dict[int | str, str | int | None],
    ) -> None:
        req_id_raw = str(item.get('requestId', ''))
        location = _preserve_location(item.get('location'))
        base_loc = location.split('?')[0] if location else ''

        pending_key = ''
        if req_id_raw:
            by_response_id = f'{batch_id}_{req_id_raw}'
            if by_response_id in self._pending:
                pending_key = by_response_id

        # Fallback: map by response index to the outbound requestId.
        # This handles batches where Roblox rewrites requestIds in the response.
        mapped_req_id = req_ids_by_index[idx] if idx < len(req_ids_by_index) else ''
        if not pending_key and mapped_req_id:
            by_request_index = f'{batch_id}_{mapped_req_id}'
            if by_request_index in self._pending:
                pending_key = by_request_index

        req_item = as_json_object(req_data[idx]) if idx < len(req_data) else None
        if req_item is None:
            req_item = {}
        aid = self._normalize_asset_id(req_item.get('assetId'))
        map_index = texpack_request_slots.get(idx)

        if location and not pending_key and aid is not None and map_index is not None:
            if map_index == 2 and orm_overrides:
                orm_chs: dict[str, str | None] = {}
                orm_chs.update(orm_overrides.get('TexturePack', {}))
                if aid in orm_overrides:
                    orm_chs.update(orm_overrides[aid])
                if orm_chs:
                    normal_source = normal_overrides.get('TexturePack')
                    if aid in normal_overrides:
                        normal_source = normal_overrides[aid]
                    comp = self._build_orm_composite(
                        aid,
                        orm_chs,
                        normal_source=normal_source,
                    )
                    if comp:
                        self._local_redirects[base_loc] = comp
                        log_buffer.log(
                            'Local',
                            f'Will serve local for {base_loc[:60]}... '
                            f'(key=ORM[{", ".join(sorted(orm_chs))}], slot={map_index}, file={Path(comp).name})',
                        )
                        return

            slot_key = f'{aid}:{map_index}'
            wildcard_key = f'TexturePack:{map_index}'
            # Match the request-side specificity order so an exact
            # asset override always beats a TexturePack wildcard or
            # type fallback during response recovery as well.
            all_keys = [slot_key, aid, wildcard_key, *self._get_type_keys(req_item)]
            local_key = next((k for k in all_keys if k in local_replacements), None)
            cdn_key = next((k for k in all_keys if k in cdn_replacements), None)
            if local_key is not None:
                local_path = self._convert_texpack_local(
                    local_replacements[local_key],
                    map_index=map_index,
                )
                self._local_redirects[base_loc] = local_path
                log_buffer.log(
                    'Local',
                    f'Will serve local for {base_loc[:60]}... '
                    f'(key={local_key}, slot={map_index}, file={Path(local_path).name})',
                )
                return
            if cdn_key is not None:
                self._cdn_redirects[base_loc] = cdn_replacements[cdn_key]
                log_buffer.log(
                    'CDN',
                    f'Will redirect {base_loc[:60]}... (key={cdn_key}, slot={map_index})',
                )
                return

        if not location or not pending_key:
            return
        url_type, url_value = self._pending.pop(pending_key)
        if url_type == 'cdn':
            self._cdn_redirects[base_loc] = url_value
            log_buffer.log('CDN', f'Will redirect {base_loc[:60]}...')
        elif url_type == 'local':
            # Check if this is a tagged animation replacement — if so, put it in
            # _anim_rig_local so server.py reads the upstream CDN response first
            # to detect the original rig, rather than short-circuiting immediately.
            with self._anim_lock:
                anim_pending = self._anim_local_pending.pop(pending_key, None)
            if anim_pending is not None:
                anim_path, required_rig = anim_pending
                self._anim_rig_local[base_loc] = (anim_path, required_rig)
                log_buffer.log('AnimConv', f'Queued rig-detect for {base_loc[:60]}...')
            else:
                self._local_redirects[base_loc] = url_value
                log_buffer.log('Local', f'Will serve local for {base_loc[:60]}...')
        elif url_type == 'solid':
            self._solidmodel_injections[base_loc] = url_value
            self._solidmodel_force_v3.discard(base_loc)
            log_buffer.log('SolidModel', f'Will inject OBJ for {base_loc[:60]}...')
        elif url_type == 'solid_obj':
            self._solidmodel_injections[base_loc] = url_value
            self._solidmodel_force_v3.add(base_loc)
            log_buffer.log('SolidModel', f'Will inject OBJ for {base_loc[:60]}...')

    # Batch response (called from server MITM thread)

    def process_batch_response(
        self,
        req_body: bytes,
        resp_body: bytes,
        req_headers: dict[str, str],
        batch_id: str = '',
    ) -> None:
        """Commit CDN URL -> redirect/local/solid mappings from batch response."""
        del req_headers
        if not resp_body:
            return
        try:
            resp_data = _loads(resp_body)
        except TypeError, ValueError:
            return
        if not isinstance(resp_data, list):
            return

        # Roblox may renumber response requestIds after the request body has
        # been filtered/rewritten. Keep an index-aligned copy of outbound
        # requestIds so we can map each response entry back to the right
        # pending route even when response requestIds drift.
        req_data: _JsonList = []
        req_ids_by_index: list[str] = []
        if req_body:
            req_data, req_ids_by_index = _decode_request_entries(req_body)

        self._sync_replacements_generation()
        replacements, _, cdn_replacements, local_replacements = (
            self.config_manager.get_all_replacements()
        )
        global_index_channel = {
            2: 'metalness',
            3: 'roughness',
            4: 'emissive',
            5: 'height',
        }
        orm_overrides: dict[int | str, dict[str, str | None]] = {}
        normal_overrides: dict[int | str, str | int | None] = {}
        for nk, nv in replacements.items():
            if not isinstance(nk, str) or ':' not in nk:
                continue
            npk, ngi = nk.split(':', 1)
            if ngi != '1' or self._normalize_asset_id(nv) in {0, 1}:
                continue
            normal_overrides[int(npk) if npk.isdigit() else npk] = nv
        for ck, cv in {**cdn_replacements, **local_replacements}.items():
            if not isinstance(ck, str) or ':' not in ck:
                continue
            pk, gi_str = ck.split(':', 1)
            if not gi_str.isdigit():
                continue
            gi = int(gi_str)
            pk_key: int | str = int(pk) if pk.isdigit() else pk
            if gi == 1:
                normal_overrides[pk_key] = cv
                continue
            ch = global_index_channel.get(gi)
            if not ch:
                continue
            cv_value = _preserve_optional_str(cv)
            cv_resolved: str | None = (
                None
                if (cv_value is not None and str(cv_value).lower().endswith(('.ktx2', '.ktx')))
                else cv_value
            )
            orm_overrides.setdefault(pk_key, {})[ch] = cv_resolved

        slot_target_ids: set[int] = set()
        all_texpack_route_keys = set(cdn_replacements.keys()) | set(local_replacements.keys())
        for key in all_texpack_route_keys:
            if isinstance(key, str) and ':' in key:
                pk = key.split(':', 1)[0]
                if pk.isdigit():
                    slot_target_ids.add(int(pk))
        texpack_request_slots = self._build_texpack_request_slot_map(req_data, slot_target_ids)

        with self._lock:
            batch_generation = self._batch_generations.pop(batch_id, None)
            if batch_generation is None or batch_generation != self._routes_generation:
                return
            for idx, item in enumerate(resp_data):
                if not isinstance(item, dict):
                    continue
                self._process_batch_response_entry_locked(
                    idx=idx,
                    item=item,
                    batch_id=batch_id,
                    req_data=req_data,
                    req_ids_by_index=req_ids_by_index,
                    texpack_request_slots=texpack_request_slots,
                    local_replacements=local_replacements,
                    cdn_replacements=cdn_replacements,
                    orm_overrides=orm_overrides,
                    normal_overrides=normal_overrides,
                )

    # CDN request check (called from server MITM thread for Roblox CDN hosts)

    def check_cdn_request(self, host: str, path: str) -> _CdnMatch | None:
        """Returns ('local'|'cdn'|'solid'|'solid_v3'|'anim_rig', value) or None.

        'anim_rig' means: let the upstream CDN request proceed normally so server.py
        can read the original response bytes, detect the rig, then serve the
        rig-matched local replacement file instead.
        """
        self._sync_replacements_generation()
        base_url = f'https://{host}{path}'.split('?')[0]

        def _log_cdn_match(action: str, value: str | _AnimPendingValue) -> None:
            target = value[0] if action == 'anim_rig' and isinstance(value, tuple) else value
            try:
                display, exists, size, ext = _cdn_target_diagnostics(target)
            except OSError as exc:
                log_buffer.log('CDN', f'CDN short-circuit log failed for {base_url[:80]}: {exc}')
                return
            category = 'TexPackTrace' if ext in {'.ktx', '.ktx2'} else 'CDN'
            log_buffer.log(
                category,
                f'CDN short-circuit match: action={action} url={base_url[:120]} '
                f'target={display} exists={exists} bytes={size}',
            )

        # Check animation rig-detect entries first (separate dict, no _lock needed here
        # since _anim_lock guards it; checked before _local_redirects so these never
        # accidentally land in the normal short-circuit path).
        with self._anim_lock:
            anim_entry = self._anim_rig_local.pop(base_url, None)
        if anim_entry is not None:
            _log_cdn_match('anim_rig', anim_entry)
            return ('anim_rig', anim_entry)

        with self._lock:
            if base_url in self._local_redirects:
                value = self._local_redirects.pop(base_url)
                _log_cdn_match('local', value)
                return ('local', value)
            if base_url in self._cdn_redirects:
                value = self._cdn_redirects.pop(base_url)
                return ('cdn', value)
            if base_url in self._solidmodel_injections:
                if base_url in self._solidmodel_force_v3:
                    value = self._solidmodel_injections[base_url]
                    _log_cdn_match('solid_v3', value)
                    return ('solid_v3', value)
                value = self._solidmodel_injections[base_url]
                _log_cdn_match('solid', value)
                return ('solid', value)
        return None

    def has_pending(self) -> bool:
        """Return True if any batch req_ids are awaiting CDN URL mapping.

        Used by the server to decide whether to wait briefly for the batch
        response coroutine to register a CDN URL before giving up.
        """
        self._sync_replacements_generation()
        with self._lock:
            return bool(self._pending)

    # SolidModel response injection (called from server MITM thread)

    def process_solidmodel_response(
        self,
        resp_body: bytes,
        obj_path_str: str,
        cdn_url: str = '',
        prefer_v3: bool = False,
    ) -> bytes:
        # Pop ONLY this specific CDN URL, not every URL mapped to the same obj.
        # Popping all-by-value was the root cause of the SolidModel partial-replacement
        # bug: SolidModel A's injection would pop entries for B, C, D, E (same .obj),
        # so their CDN requests found nothing and passed through unreplaced.
        obj_path = Path(obj_path_str)
        with self._lock:
            if cdn_url:
                self._solidmodel_injections.pop(cdn_url, None)
                self._solidmodel_force_v3.discard(cdn_url)
            else:
                # Fallback: pop all by value (legacy path, shouldn't be hit)
                to_pop = [k for k, v in self._solidmodel_injections.items() if v == obj_path_str]
                for k in to_pop:
                    self._solidmodel_injections.pop(k, None)
                    self._solidmodel_force_v3.discard(k)
        try:
            modified = _inject_obj_into_solidmodel(resp_body, obj_path, prefer_v3=prefer_v3)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log('SolidModel', f'Injection failed: {exc}')
            return resp_body
        log_buffer.log('SolidModel', f'Injected OBJ ({len(modified)} bytes)')
        return modified

    # Internal routing helpers

    @staticmethod
    def _convert_texpack_local(local_path: str, map_index: int | None = None) -> str:
        path = Path(local_path)
        ext = path.suffix.lower()
        mipmap_mode = _mipmap_mode(map_index)
        if ext == '.ktx2':
            normalized = TextureStripper._normalize_rgba8_ktx2(
                path,
                mipmap_mode=mipmap_mode,
            )
            if normalized != path:
                log_buffer.log(
                    'TexPackTrace',
                    f'Normalized local RGBA8 KTX2 for Roblox: input={path.name} output={normalized.name}',
                )
                return str(normalized)
            log_buffer.log('TexPackTrace', f'Local TexturePack map already KTX: file={path.name}')
            return local_path
        if ext == '.ktx':
            log_buffer.log('TexPackTrace', f'Local TexturePack map already KTX: file={path.name}')
            return local_path
        try:
            log_buffer.log(
                'TexPackTrace',
                f'Converting local TexturePack map to KTX2: input={path.name}',
            )
            converted_path = _get_or_create_ktx2_from_image(
                path,
                mipmap_mode=mipmap_mode,
            )
            if converted_path:
                converted_path = TextureStripper._normalize_rgba8_ktx2(
                    converted_path,
                    mipmap_mode=mipmap_mode,
                )
                log_buffer.log(
                    'TexPackTrace',
                    f'Converted local TexturePack map: input={path.name} output={converted_path.name}',
                )
                return str(converted_path)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log('Local', f'Failed to convert {path.name} to KTX2: {exc}')
            log_buffer.log(
                'TexPackTrace',
                f'Local TexturePack KTX2 conversion failed for {path.name}: {exc}',
            )
        return local_path

    @staticmethod
    def _normalize_rgba8_ktx2(
        path: Path, *, mipmap_mode: Literal['color', 'normal', 'linear'] = 'color'
    ) -> Path:
        """Normalize RGBA8 KTX2 while preserving authored mip chains."""
        try:
            return _normalized_rgba8_ktx2_path(path, mipmap_mode=mipmap_mode)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log(
                'TexPackTrace',
                f'RGBA8 KTX2 normalization skipped for {path.name}: {exc}',
            )
            return path

    def _route_cdn(
        self,
        req_id: str,
        aid: object,
        cdn_url: str,
        *,
        is_solidmodel: bool,
        is_texpack: bool = False,
        map_index: int | None = None,
    ) -> None:
        parsed = urlparse(str(cdn_url))
        ext = Path(parsed.path).suffix.lower()
        url_hash = hashlib.md5(str(cdn_url).encode(), usedforsecurity=False).hexdigest()
        if is_texpack:
            log_buffer.log(
                'TexPackTrace',
                f'Queue CDN TexturePack route req={req_id} aid={aid} ext={ext or "none"} url={_short_value(cdn_url)}',
            )

        if ext == '.obj':
            local_cache = APP_CACHE_DIR / f'{url_hash}.obj'
            if _download_remote_file(cdn_url, local_cache, '.obj'):
                kind = 'solid_obj' if is_solidmodel else 'local'
                self._queue_pending(req_id, (kind, str(local_cache)))
            else:
                self._queue_pending(req_id, ('cdn', cdn_url))
        elif ext == '.mesh':
            if not is_solidmodel:
                self._queue_pending(req_id, ('cdn', cdn_url))
                log_buffer.log('CDN', f'Queued direct .mesh redirect for {aid}')
            else:
                local_cache = APP_CACHE_DIR / f'{url_hash}.mesh'
                obj = (
                    _try_mesh_to_obj(local_cache, f'SolidModel CDN {aid}')
                    if _download_remote_file(cdn_url, local_cache, '.mesh')
                    else None
                )
                if obj:
                    self._queue_pending(req_id, ('solid', str(obj)))
                else:
                    self._queue_pending(req_id, ('cdn', cdn_url))
        elif ext == '.bin':
            local_cache = APP_CACHE_DIR / f'{url_hash}.bin'
            downloaded = _download_remote_file(cdn_url, local_cache, '.bin')
            if downloaded and _is_csgmdl_bin(local_cache):
                obj = _try_bin_to_obj(local_cache, f'CDN {aid}')
                if obj:
                    kind = 'solid' if is_solidmodel else 'local'
                    self._queue_pending(req_id, (kind, str(obj)))
                else:
                    log_buffer.log('CDN', f'{aid}: CSGMDL conversion failed, redirecting to CDN')
                    self._queue_pending(req_id, ('cdn', cdn_url))
            elif downloaded:
                kind = 'solid' if is_solidmodel else 'local'
                self._queue_pending(req_id, (kind, str(local_cache)))
            else:
                self._queue_pending(req_id, ('cdn', cdn_url))
        elif is_texpack and ext != '.ktx2':
            local_cache = APP_CACHE_DIR / f'{url_hash}{ext}'
            if _download_remote_file(cdn_url, local_cache, 'CDN TexPack Map'):
                log_buffer.log(
                    'TexPackTrace',
                    f'Downloaded CDN TexturePack map for local conversion aid={aid} file={local_cache.name}',
                )
                self._route_local(
                    req_id,
                    aid,
                    str(local_cache),
                    is_solidmodel=is_solidmodel,
                    is_texpack=True,
                    map_index=map_index,
                )
            else:
                log_buffer.log(
                    'TexPackTrace',
                    f'CDN TexturePack map download failed for aid={aid}; falling back to CDN redirect',
                )
                self._queue_pending(req_id, ('cdn', cdn_url))
                log_buffer.log('CDN', f'Queued CDN redirect for {aid}')
        else:
            self._queue_pending(req_id, ('cdn', cdn_url))
            log_buffer.log('CDN', f'Queued CDN redirect for {aid}')

    def _route_local(
        self,
        req_id: str,
        aid: object,
        local_path: str,
        *,
        is_solidmodel: bool,
        is_texpack: bool = False,
        source_key: object | None = None,
        map_index: int | None = None,
    ) -> None:
        path = Path(local_path)
        ext = path.suffix.lower()
        original_path = path
        mipmap_mode = _mipmap_mode(map_index)
        if is_texpack:
            log_buffer.log(
                'TexPackTrace',
                f'Queue local TexturePack route req={req_id} aid={aid} key={source_key} '
                f'slot={_texpack_slot_label(map_index)} input={path.name} ext={ext or "none"}',
            )

        # Isolate KTX2 explicit conversion only to TexturePack image replacements
        if is_texpack and ext not in {'.ktx2', '.ktx'}:
            try:
                converted_path = _get_or_create_ktx2_from_image(
                    path,
                    mipmap_mode=mipmap_mode,
                )
            except _BEST_EFFORT_ERRORS as exc:
                log_buffer.log('Local', f'Failed to convert {path.name} to KTX2: {exc}')
                log_buffer.log(
                    'TexPackTrace',
                    f'Local TexturePack conversion failed for {path.name}: {exc}',
                )
            else:
                converted_path = self._normalize_rgba8_ktx2(
                    converted_path,
                    mipmap_mode=mipmap_mode,
                )
                local_path = str(converted_path)
                path = converted_path
                ext = path.suffix.lower()
                log_buffer.log(
                    'TexPackTrace',
                    f'Local TexturePack conversion selected output={path.name} '
                    f'from={original_path.name} slot={_texpack_slot_label(map_index)}',
                )
        elif is_texpack and ext == '.ktx2':
            normalized = self._normalize_rgba8_ktx2(
                path,
                mipmap_mode=mipmap_mode,
            )
            if normalized != path:
                local_path = str(normalized)
                path = normalized
                ext = path.suffix.lower()
                log_buffer.log(
                    'TexPackTrace',
                    f'Local TexturePack RGBA8 KTX2 normalized output={path.name} '
                    f'from={original_path.name} slot={_texpack_slot_label(map_index)}',
                )

        def _log_local_queued(label: str = 'Queued local') -> None:
            details: list[str] = []
            if source_key is not None:
                details.append(f'key={source_key}')
            if map_index is not None:
                details.append(f'slot={map_index}')
            if is_texpack:
                details.append(f'file={path.name}')
                if path != original_path:
                    details.append(f'from={original_path.name}')
            suffix = f' ({", ".join(details)})' if details else ''
            log_buffer.log('Local', f'{label} for {aid}{suffix}')

        if is_solidmodel:
            if ext == '.obj':
                self._queue_pending(req_id, ('solid_obj', local_path))
            elif ext == '.mesh':
                obj = _try_mesh_to_obj(path, f'SolidModel {aid}')
                val = ('solid', str(obj)) if obj else ('local', local_path)
                self._queue_pending(req_id, val)
            elif ext == '.bin':
                # Only try conversion if it's actually a CSGMDL
                if _is_csgmdl_bin(path):
                    obj = _try_bin_to_obj(path, f'SolidModel {aid}')
                    val = ('solid', str(obj)) if obj else ('local', local_path)
                else:
                    # Not a CSGMDL, serve as-is
                    val = ('local', local_path)
                self._queue_pending(req_id, val)
            elif ext == '.rbxmx':
                obj = _try_rbxmx_to_obj(path, f'SolidModel {aid}')
                val = ('solid', str(obj)) if obj else ('local', local_path)
                self._queue_pending(req_id, val)
            else:
                self._queue_pending(req_id, ('local', local_path))
        elif ext == '.bin':
            # Only try conversion if it's actually a CSGMDL
            if _is_csgmdl_bin(path):
                obj = _try_bin_to_obj(path, f'Mesh {aid}')
                if obj:
                    self._queue_pending(req_id, ('local', str(obj)))
                else:
                    _log_local_queued('Failed to convert CSGMDL; serving .bin as-is')
                    self._queue_pending(req_id, ('local', local_path))
            else:
                # Not a CSGMDL, serve as-is
                _log_local_queued('Queued local .bin (not CSGMDL)')
                self._queue_pending(req_id, ('local', local_path))
        else:
            self._queue_pending(req_id, ('local', local_path))
            _log_local_queued()

    def _build_orm_composite(
        self,
        parent_id: int | str,
        channel_pngs: dict[str, str | None],
        *,
        normal_source: str | int | None = None,
    ) -> str | None:
        """Build (or retrieve from cache) a composite ORM KTX2 from per-channel PNGs.

        *parent_id* is the TexturePack asset ID.  *channel_pngs* maps channel
        name (``metalness``, ``roughness``, ``emissive``, ``height``) to local
        PNG file paths.  The baseline ORM KTX2 from the persistent per-slot
        TexturePack cache is used if available so unspecified channels retain
        their CDN values.
        """
        try:
            return self._build_orm_composite_impl(
                parent_id,
                channel_pngs,
                normal_source=normal_source,
            )
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log('ORM', f'Composite failed for pack {parent_id}: {exc}')
            return None

    def _build_orm_composite_impl(
        self,
        parent_id: int | str,
        channel_pngs: dict[str, str | None],
        *,
        normal_source: str | int | None = None,
    ) -> str | None:
        # Resolve CDN URLs to local cached files before compositing.
        # On download failure the channel is omitted so the baseline KTX2
        # value shows through, as if the user never replaced that channel.
        resolved: dict[str, str | None] = {}
        for ch, val in channel_pngs.items():
            if val is not None and (
                str(val).startswith('http://') or str(val).startswith('https://')
            ):
                url_hash = hashlib.md5(val.encode(), usedforsecurity=False).hexdigest()[:16]
                ext = Path(urlparse(val).path).suffix.lower() or '.png'
                cdn_cache = APP_CACHE_DIR / 'orm_cdn_cache' / f'{url_hash}{ext}'
                cdn_cache.parent.mkdir(parents=True, exist_ok=True)
                if not cdn_cache.exists() and not _download_remote_file(
                    val, cdn_cache, f'ORM channel {ch}'
                ):
                    log_buffer.log(
                        'ORM',
                        f'CDN download failed for channel {ch} — using original',
                    )
                    continue  # skip channel; baseline value preserved
                resolved[ch] = str(cdn_cache)
            else:
                resolved[ch] = val
        resolved_normal = normal_source
        if resolved_normal is not None and (
            isinstance(resolved_normal, int) or resolved_normal.isdigit()
        ):
            normal_id = int(resolved_normal)
            resolved_normal = self._predownloaded.get(normal_id)
            if resolved_normal is None:
                normal_download = APP_CACHE_DIR / 'predownloaded' / f'{normal_id}.dat'
                normal_download.parent.mkdir(parents=True, exist_ok=True)
                if not normal_download.exists():
                    scraper = self._cache_scraper
                    if scraper is not None:
                        extra_headers: dict[str, str] = {}
                        cookie = _scraper_get_roblosecurity(scraper)
                        if cookie:
                            extra_headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
                        normal_data, _status = _scraper_fetch_asset_with_place_id_retry(
                            scraper,
                            str(normal_id),
                            extra_headers=extra_headers or None,
                        )
                        if normal_data:
                            normal_download.write_bytes(normal_data)
                resolved_normal = str(normal_download) if normal_download.exists() else None
                if resolved_normal is None:
                    log_buffer.log(
                        'ORM',
                        f'Normal asset {normal_id} could not be downloaded — using captured Normal baseline',
                    )

        if resolved_normal is not None and str(resolved_normal).startswith(('http://', 'https://')):
            url_hash = hashlib.md5(
                str(resolved_normal).encode(), usedforsecurity=False
            ).hexdigest()[:16]
            ext = Path(urlparse(str(resolved_normal)).path).suffix.lower() or '.png'
            normal_cache = APP_CACHE_DIR / 'orm_normal_cache' / f'{url_hash}{ext}'
            normal_cache.parent.mkdir(parents=True, exist_ok=True)
            if not normal_cache.exists() and not _download_remote_file(
                str(resolved_normal), normal_cache, 'TexturePack Normal map'
            ):
                log_buffer.log(
                    'ORM',
                    'Normal CDN download failed — using captured Normal baseline',
                )
                resolved_normal = None
            elif normal_cache.exists():
                resolved_normal = str(normal_cache)

        cache_manager = getattr(getattr(self, '_cache_scraper', None), 'cache_manager', None)
        if cache_manager is not None:
            baseline = cache_manager.get_texturepack_slot_path(parent_id, 2)
            normal_baseline = cache_manager.get_texturepack_slot_path(parent_id, 1)
        else:
            # Compatibility fallback for isolated tests/legacy callers that
            # construct TextureStripper without wiring the CacheScraper.
            baseline = APP_CACHE_DIR / 'texpack_slots' / f'{parent_id}_slot2.ktx2'
            normal_baseline = APP_CACHE_DIR / 'texpack_slots' / f'{parent_id}_slot1.ktx2'
        log_buffer.log(
            'TexPackTrace',
            f'ORM composite input pack={parent_id} baseline={baseline.name if baseline.exists() else "missing"} '
            f'normal={_file_value(resolved_normal) if resolved_normal else (normal_baseline.name if normal_baseline.exists() else "missing")} '
            f'channels={", ".join(f"{ch}={_file_value(val)}" for ch, val in sorted(resolved.items()))}',
        )
        result = _composite_orm(
            baseline=(baseline if baseline.exists() else None),
            channels={k: (Path(v) if v is not None else None) for k, v in resolved.items()},
            cache_dir=APP_CACHE_DIR,
            normal_source=(Path(resolved_normal) if resolved_normal is not None else None),
            normal_baseline=(normal_baseline if normal_baseline.exists() else None),
        )
        log_buffer.log(
            'TexPackTrace',
            f'ORM composite result pack={parent_id} output={_file_value(result)}',
        )
        return result

    def _build_texpack_request_slot_map(
        self,
        data: _JsonList,
        slot_target_ids: set[int] | None = None,
        infer_repeated_assets: bool = False,
    ) -> dict[int, int]:
        """Infer TexturePack delivery slots from the index-aligned batch request.

        The fidelity byte is not reliable for all Roblox TexturePack batch
        shapes: some requests/selected response specifiers report slot 0 for
        multiple distinct TexturePack CDN URLs. Slot-specific replacement must
        therefore prefer request-side build metadata and, when Roblox gives only
        opaque build values, a stable per-pack request order.
        """
        texpack_ids = set(slot_target_ids or ())
        asset_counts: dict[int, int] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            aid = self._normalize_asset_id(item.get('assetId'))
            if not isinstance(aid, int):
                continue
            asset_counts[aid] = asset_counts.get(aid, 0) + 1
            if (
                item.get('assetTypeId') == 63
                or self._REVERSE.get(str(item.get('assetType', '')).lower()) == 63
            ):
                texpack_ids.add(aid)

        if infer_repeated_assets:
            for item in data:
                if not isinstance(item, dict):
                    continue
                aid = self._normalize_asset_id(item.get('assetId'))
                if not isinstance(aid, int) or aid in texpack_ids:
                    continue
                if asset_counts.get(aid, 0) > 1 and (
                    item.get('contentRepresentationPriorityList') is not None
                    or _texpack_build_key(item) is not None
                ):
                    texpack_ids.add(aid)

        result: dict[int, int] = {}
        build_slots: dict[int, dict[object, int]] = {}
        next_slot: dict[int, int] = {}
        occurrence_slot: dict[int, int] = {}

        for idx, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            aid = self._normalize_asset_id(item.get('assetId'))
            if not isinstance(aid, int) or aid not in texpack_ids:
                continue

            slot_quality = _decode_texpack_slot_quality(item)
            slot = slot_quality[0] if slot_quality is not None else None
            if slot is None:
                build_key = _texpack_build_key(item)
                if build_key is not None and asset_counts.get(aid, 0) > 1:
                    slots_for_asset = build_slots.setdefault(aid, {})
                    if build_key not in slots_for_asset:
                        raw_slot = next_slot.get(aid, 0)
                        slots_for_asset[build_key] = min(2, raw_slot)
                        next_slot[aid] = raw_slot + 1
                    slot = slots_for_asset[build_key]
                elif asset_counts.get(aid, 0) > 1:
                    raw_slot = occurrence_slot.get(aid, 0)
                    occurrence_slot[aid] = raw_slot + 1
                    slot = min(2, raw_slot)
                else:
                    continue

            result[idx] = slot

        return result

    def _should_remove(
        self, e: _JsonObject, removals: set[_ReplacementKey], map_index: int | None = None
    ) -> bool:
        aid = self._normalize_asset_id(e.get('assetId'))
        if map_index is None:
            map_index = self._get_texpack_map_index(e)
        should_remove = bool(
            aid is not None
            and map_index is not None
            and (f'{aid}:{map_index}' in removals or f'TexturePack:{map_index}' in removals)
        )
        if not should_remove:
            should_remove = aid in removals
        at_id = e.get('assetTypeId')
        if not should_remove and at_id is not None:
            should_remove = at_id in removals
        at_name = e.get('assetType')
        if not should_remove and at_name:
            should_remove = (
                at_name in removals or self._REVERSE.get(str(at_name).lower()) in removals
            )
        return should_remove

    @staticmethod
    def _get_blank_ktx2_path() -> str | None:
        """Return a path to a 1x1 white RGBA KTX2 placeholder texture.

        Created on first call and cached in APP_CACHE_DIR.  Used to fill
        TexturePack slots that the user has set to "Nothing" so the rest of
        the pack keeps loading normally.
        """
        png_path = APP_CACHE_DIR / '_blank_texpack.png'
        if not png_path.exists() and not _ensure_blank_png(png_path):
            return None
        try:
            ktx_path = _get_or_create_ktx2_from_image(png_path)
            if ktx_path and ktx_path.exists():
                return str(ktx_path)
        except _BEST_EFFORT_ERRORS as exc:
            log_buffer.log('TexPack', f'Failed to convert blank placeholder to KTX2: {exc}')
        return None

    def _get_type_keys(self, e: _JsonObject) -> list[_ReplacementKey]:
        keys: list[_ReplacementKey] = []
        at_id = e.get('assetTypeId')
        if at_id is not None:
            keys.append(_preserve_replacement_key(at_id))
        at_name = e.get('assetType')
        if at_name:
            keys.append(_preserve_replacement_key(at_name))
            mapped = self._REVERSE.get(str(at_name).lower())
            if mapped is not None:
                keys.append(mapped)
        return keys

    @staticmethod
    def _normalize_asset_id(asset_id: object) -> _ReplacementKey | None:
        """Normalize JSON numeric-string asset IDs to ints for config matching."""
        if isinstance(asset_id, bool):
            return asset_id
        if isinstance(asset_id, int):
            return asset_id
        if isinstance(asset_id, str) and asset_id.isdigit():
            try:
                return int(asset_id)
            except ValueError:
                return asset_id
        return _preserve_asset_id(asset_id)

    @staticmethod
    def _get_texpack_map_index(e: _JsonObject) -> int | None:
        """Return texture map fidelity slot index from the batch item.

        Roblox sends a priority list plus a requested build type.  The selected
        The representation's base64 fidelity bytes encode the TexturePack slot
        in byte 0 bits 5-6 and quality/LOD in byte 1 bits 6-7. Examples observed
        from live batches: 0040=color, 2040=normal, 4040=ORM.

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
        Global indices 2-5 are resolved via ``_global_index_channel``
        in the ORM compositor path.
        """
        slot_quality = _decode_texpack_slot_quality(e)
        if slot_quality is None:
            return None
        return slot_quality[0]
