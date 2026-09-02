"""Cache viewer tab - simplified version for viewing cached assets."""

from __future__ import annotations

import contextlib
import gzip as gzip_module
import importlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import webbrowser
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast, overload, override
from xml.parsers.expat import ExpatError

import requests
from defusedxml import ElementTree as DefusedElementTree, minidom as safe_minidom
from defusedxml.common import DefusedXmlException
from PIL import Image
from PySide6.QtCore import (
    QEvent,
    QMimeData,
    QObject,
    QPoint,
    QProcess,
    QRect,
    QSignalBlocker,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QCursor,
    QFontDatabase,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
    QScreen,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from fleasion.localization import tr, tr_count
from fleasion.utils import format_count, get_icon_path, log_buffer, open_folder
from fleasion.utils.clipboard import copy_pixmap_to_clipboard
from fleasion.utils.json_types import JsonValue, require_json_value
from fleasion.utils.roblox_auth import get_roblosecurity as _get_roblosecurity

from . import asset_type_filter as _asset_type_filter, mesh_processing
from .asset_type_filter import CategoryFilterPopup
from .audio_player import AudioPlayerWidget
from .cache_json_viewer import CacheJsonViewer
from .cache_manager import CacheManager
from .font_viewer import FontViewerWidget
from .rbxm_preview import RbxmPreviewWidget, is_rbx_model_data
from .roblox_document import export_roblox_document, get_roblox_document_export_formats

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterator, Sequence
    from types import TracebackType

    from fleasion.config.manager import ConfigManager
    from fleasion.utils.r15_to_r6 import JointMap, PartMap

    from .animation_viewer import AnimationViewerPanel
    from .cache_manager import CacheIndex
    from .obj_viewer import ObjViewerPanel
    from .rbxm_preview import PreviewDocument


type _AssetTypeFilter = int | str
type _TypeProbeKey = tuple[str, int, str]
type _TypeProbeResult = tuple[str, int, str, str | None]
type _ScraperColumn = tuple[str, bool, int]
type _ResolvedScraperColumn = tuple[str, str, bool, int]
type _SearchColumn = tuple[str, bool]
type _ResolvedSearchColumn = tuple[str, str, bool]
type _ExportPath = str | Path


def _ui_boundary[T](
    action: Callable[[], T], *, fallback: T, on_error: Callable[[Exception], object]
) -> T:
    try:
        return action()
    except Exception as exc:  # ruff: ignore[blind-except]
        on_error(exc)
        return fallback


class _AssetRecord(TypedDict):
    id: str
    asset_id: NotRequired[str | int]
    type: int
    type_name: str
    url: NotRequired[str]
    size: NotRequired[int]
    raw_size: NotRequired[int]
    compressed: NotRequired[bool]
    hash: NotRequired[str]
    cached_at: NotRequired[str]
    metadata: NotRequired[dict[str, object]]
    detected_type: NotRequired[str]
    resolved_name: NotRequired[str | None]
    resolved_creator_id: NotRequired[int | None]
    resolved_creator_name: NotRequired[str | None]
    resolved_creator_type: NotRequired[int | None]
    resolved_created_at: NotRequired[str | None]
    resolved_updated_at: NotRequired[str | None]


class _ResolvedAssetInfo(TypedDict, total=False):
    hash: str
    resolved_name: str | None
    creator_id: int | None
    creator_name: str | None
    creator_type: int | None
    created_at: str | None
    updated_at: str | None
    row: int | None


class _FetchedAssetMetadata(TypedDict):
    name: str
    type: int
    creator_id: int | None
    creator_type: int | None
    created_at: str
    updated_at: str
    creator_name: NotRequired[str]


class _FetchedNameMetadata(TypedDict):
    name: str
    creator_id: int | None
    creator_type: int | None
    created_at: str
    updated_at: str


class _TexturePackData(TypedDict):
    id: str
    hash: str
    data: bytes


class _RbxmDraft(TypedDict):
    cached_at: str
    document: PreviewDocument


class _CacheScraper(Protocol):
    enabled: bool

    def set_enabled(self, enabled: bool) -> None: ...

    def clear_tracking(self) -> None: ...

    def _https_get(
        self,
        hostname: str,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
        timeout: float = 8.0,
        max_redirects: int = 6,
        return_status: bool = False,
    ) -> bytes | tuple[bytes | None, int | None] | None: ...

    def _fetch_asset_with_place_id_retry(
        self, asset_id: str, extra_headers: dict[str, str] | None = None
    ) -> tuple[bytes | None, int | None]: ...


class _ConfigManager(Protocol):
    settings: dict[str, object]
    show_names: bool
    show_creator_id: bool
    scraper_blacklist: list[str]

    def save(self) -> None: ...


class _ConstrainablePopup(Protocol):
    def constrain_to_available_geometry(
        self, available_geometry: QRect, anchor_y: int | None = None
    ) -> None: ...


class _LockContext(Protocol):
    def __enter__(self) -> object: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...


class _HttpsGetCallable(Protocol):
    def __call__(
        self,
        hostname: str,
        path: str,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes | None: ...


class _FetchAssetRetryCallable(Protocol):
    def __call__(
        self, asset_id: str, extra_headers: dict[str, str] | None = None
    ) -> tuple[bytes | None, int | None]: ...


class _ExportObjCallable(Protocol):
    def __call__(self, doc: object, output_path: Path, *, decompose: bool = False) -> None: ...


def _set_tray_cache_scraper_enabled(tray: object, enabled: bool) -> bool:
    """Use the tray's compatibility hook without exposing private-member access at call sites."""
    setter = getattr(tray, '_set_cache_scraper_enabled', None)
    if not callable(setter):
        return False
    cast('Callable[[bool], None]', setter)(enabled)
    return True


def _lazy_attr(module_name: str, attr_name: str) -> object:
    """Load a deliberately lazy module attribute without importing it at startup."""
    module = importlib.import_module(module_name, package=__package__)
    return getattr(module, attr_name)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(cast('int | str', value))
    except TypeError, ValueError:
        return None


def _asset_metadata_from_response(response_json: object) -> dict[str, _FetchedAssetMetadata]:
    """Extract normalized asset metadata from a Roblox develop API response."""
    if not isinstance(response_json, dict):
        return {}
    response_mapping = cast('dict[str, object]', response_json)
    raw_entries = response_mapping.get('data', [])
    if not isinstance(raw_entries, list):
        return {}

    result: dict[str, _FetchedAssetMetadata] = {}
    for raw_entry in cast('list[object]', raw_entries):
        if not isinstance(raw_entry, dict):
            continue
        item = cast('dict[str, object]', raw_entry)
        aid = item.get('id')
        if aid is None:
            continue
        creator_obj = item.get('creator')
        creator_id: int | None = None
        creator_type: int | None = None
        if isinstance(creator_obj, dict) and creator_obj:
            creator_data = cast('dict[str, object]', creator_obj)
            creator_id = _optional_int(creator_data.get('targetId'))
            creator_type = _optional_int(creator_data.get('typeId'))
        if creator_id is None:
            creator_id = _optional_int(item.get('creatorTargetId'))
        if creator_type is None:
            creator_type = _optional_int(item.get('creatorType'))
        result[str(aid)] = {
            'name': cast('str', item.get('name', 'Unknown')),
            'type': cast('int', item.get('typeId') or item.get('assetTypeId') or 1),
            'creator_id': creator_id,
            'creator_type': creator_type,
            'created_at': cast('str', item.get('created') or ''),
            'updated_at': cast('str', item.get('updated') or ''),
        }
    return result


def _creator_names_from_response(response_json: object) -> dict[int, str]:
    """Extract valid creator IDs and names from a Roblox users response."""
    if not isinstance(response_json, dict):
        return {}
    response_mapping = cast('dict[str, object]', response_json)
    raw_entries = response_mapping.get('data', [])
    if not isinstance(raw_entries, list):
        return {}

    result: dict[int, str] = {}
    for raw_entry in cast('list[object]', raw_entries):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast('dict[str, object]', raw_entry)
        uid = entry.get('id')
        name = entry.get('name') or entry.get('displayName') or 'Unknown'
        if isinstance(uid, int) and isinstance(name, str):
            result[uid] = name
    return result


def _open_export_path(target: Path, *, select_file: bool, export_dir: Path) -> None:
    """Open an export path, selecting a file in Explorer when available."""
    if select_file:
        if target.is_file():
            explorer = shutil.which('explorer.exe')
            if explorer is not None:
                started, _pid = QProcess.startDetached(
                    explorer,
                    ['/select,', str(target.resolve())],
                )
                if started:
                    return
        open_folder(target.parent)
        return
    open_folder(target if target.is_dir() else export_dir)


def _object_attribute(obj: object, name: str) -> object:
    return cast('object', getattr(obj, name))


def _cache_lock(cache_manager: CacheManager) -> _LockContext:
    return cast('_LockContext', _object_attribute(cache_manager, '_lock'))


def _schedule_cache_index_commit(cache_manager: CacheManager) -> None:
    cast('Callable[[], None]', _object_attribute(cache_manager, '_schedule_index_commit'))()


def _save_cache_index(cache_manager: CacheManager) -> None:
    cast('Callable[[], None]', _object_attribute(cache_manager, '_save_index'))()


def _detect_cache_extension(cache_manager: CacheManager, data: bytes, asset_type: int) -> str:
    detector = cast(
        'Callable[[bytes, int], str]', _object_attribute(cache_manager, '_detect_extension')
    )
    return detector(data, asset_type)


def _scraper_https_get(
    scraper: _CacheScraper,
    hostname: str,
    path: str,
    extra_headers: dict[str, str] | None,
) -> bytes | None:
    getter = cast('_HttpsGetCallable', _object_attribute(scraper, '_https_get'))
    return getter(hostname, path, extra_headers=extra_headers)


def _scraper_fetch_asset_with_place_id_retry(
    scraper: _CacheScraper,
    asset_id: str,
    extra_headers: dict[str, str] | None,
) -> tuple[bytes | None, int | None]:
    fetcher = cast(
        '_FetchAssetRetryCallable',
        _object_attribute(scraper, '_fetch_asset_with_place_id_retry'),
    )
    return fetcher(asset_id, extra_headers=extra_headers)


def _set_rbxm_dirty(viewer: RbxmPreviewWidget, *, dirty: bool) -> None:
    setter = cast('Callable[[bool], None]', _object_attribute(viewer, '_set_dirty'))
    setter(dirty)


def _toggle_audio_play_pause(player: AudioPlayerWidget) -> None:
    toggle = cast('Callable[[], None]', _object_attribute(player, '_toggle_play_pause'))
    toggle()


asset_type_display_name = cast(
    'Callable[[_AssetTypeFilter], str]',
    _asset_type_filter.__dict__['asset_type_display_name'],
)


def _localized_asset_type_name(asset_type: int | str | None, raw_name: str | None = None) -> str:
    raw_overrides: dict[str, _AssetTypeFilter] = {
        'Mesh': 4,
        'Audio': 3,
        'Json': 'Json',
    }
    if raw_name in raw_overrides:
        return asset_type_display_name(raw_overrides[raw_name])
    if raw_name == 'RBXM/RBXMX':
        return tr('ui.cache.cache_viewer.rbxm_rbxmx')
    if isinstance(asset_type, int):
        canonical = CacheManager.ASSET_TYPES.get(asset_type)
        if raw_name is None or raw_name == canonical:
            return asset_type_display_name(asset_type)
        if raw_name.startswith('Unknown'):
            return tr('cache.asset_type.unknown', type_id=asset_type)
    return raw_name or str(asset_type or '')


def _export_format_label(export_format: str) -> str:
    return {
        'converted_rigged_glb': tr('cache.export_format.rigged_mesh_glb'),
        'converted_obj': tr('cache.export_format.converted_obj'),
        'converted_rbxmx': tr('cache.export_format.keyframe_sequence_rbxmx'),
        'converted_rbxmx_curve': tr('cache.export_format.curve_animation_rbxmx'),
        'converted_rbxmx_model': tr('cache.export_format.converted_rbxmx'),
        'converted_document_rbxm': tr('cache.export_format.roblox_document_rbxm'),
        'converted_document_rbxmx': tr('cache.export_format.roblox_document_rbxmx'),
        'converted_document_rbxl': tr('cache.export_format.roblox_place_rbxl'),
        'converted_modified_rbxm': tr('cache.export_format.modified_rbxm'),
        'converted_modified_rbxmx': tr('cache.export_format.modified_rbxmx'),
        'converted_png': tr('cache.export_format.converted_png'),
        'converted_audio': tr('cache.export_format.converted_audio'),
        'converted': tr('cache.export_format.converted_xml'),
        'converted_images': tr('cache.export_format.converted_images'),
        'slot_ktx2': tr('cache.export_format.slot_ktx2_files'),
        'bin': tr('cache.export_format.binary_decompressed'),
        'raw': tr('cache.export_format.raw_original_cache'),
    }.get(export_format, export_format)


def _format_table_timestamp(value: object) -> str:
    """Format ISO-ish timestamps for scraper table date columns."""
    text = str(value or '')
    if not text:
        return ''
    try:
        if 'T' in text:
            date_part, time_part = text.split('T', 1)
            time_part = time_part.rstrip('Z')
            time_part = time_part.split('.', 1)[0]
            return f'{date_part} {time_part}'
    except ValueError, AttributeError:
        pass
    return text


def _asset_metadata_needs_resolution(info: _ResolvedAssetInfo) -> bool:
    """Return whether an asset still has display metadata to resolve.

    Creator lookup can fail independently of the asset metadata request.  A
    numeric creator ID is therefore not a completed creator resolution: keep
    the asset eligible for a later retry until the display name is stored.
    """
    if (
        info.get('resolved_name') is None
        or info.get('created_at') is None
        or info.get('updated_at') is None
    ):
        return True

    return info.get('creator_id') is not None and info.get('creator_name') is None


class NumericSortItem(QTableWidgetItem):
    """Custom table item that sorts based on a numeric value rather than text."""

    def __init__(self, numeric_val: float, text: str) -> None:
        super().__init__(text)
        self.numeric_val = numeric_val

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, NumericSortItem):
            return self.numeric_val < other.numeric_val
        return super().__lt__(other)


class SearchWorkerThread(QThread):
    """Worker thread for filtering assets without blocking UI."""

    results_ready = Signal(list)

    def __init__(
        self,
        assets: list[_AssetRecord],
        search_text: str,
        asset_info: dict[str, _ResolvedAssetInfo],
        search_columns: Collection[str] | None = None,
    ) -> None:
        super().__init__()
        self.assets = assets
        self.search_text = search_text.strip().lower()
        self.asset_info = asset_info
        self.search_columns = (
            search_columns if search_columns is not None else _DEFAULT_SEARCH_COL_KEYS
        )
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        """Filter assets in background thread."""
        if not self.search_text or self._stop_requested:
            self.results_ready.emit(self.assets)
            return

        filtered: list[_AssetRecord] = []
        batch_size = 100  # Process in batches to allow interruption

        for i in range(0, len(self.assets), batch_size):
            if self._stop_requested:
                return

            batch = self.assets[i : i + batch_size]

            for a in batch:
                if self._stop_requested:
                    return

                asset_id = a['id']
                cols = self.search_columns
                matched = False

                if not matched and 'id' in cols and self.search_text in asset_id.lower():
                    matched = True

                if not matched and 'type' in cols and self.search_text in a['type_name'].lower():
                    matched = True

                if (
                    not matched
                    and ('name' in cols or 'creator' in cols)
                    and asset_id in self.asset_info
                ):
                    info = self.asset_info[asset_id]
                    if 'name' in cols:
                        name = info.get('resolved_name')
                        if name and self.search_text in name.lower():
                            matched = True
                    if not matched and 'creator' in cols:
                        creator_name = info.get('creator_name')
                        if creator_name and self.search_text in creator_name.lower():
                            matched = True

                if not matched and 'url' in cols and self.search_text in a.get('url', '').lower():
                    matched = True

                if not matched and 'hash' in cols and self.search_text in a.get('hash', '').lower():
                    matched = True

                if (
                    not matched
                    and 'cached_at' in cols
                    and (
                        self.search_text in a.get('cached_at', '').lower()
                        or self.search_text in _format_table_timestamp(a.get('cached_at')).lower()
                    )
                ):
                    matched = True

                if not matched and ('updated_at' in cols or 'created_at' in cols):
                    info = self.asset_info.get(asset_id, {})
                    updated_at = info.get('updated_at') or a.get('resolved_updated_at') or ''
                    created_at = info.get('created_at') or a.get('resolved_created_at') or ''
                    updated_display = _format_table_timestamp(updated_at).lower()
                    created_display = _format_table_timestamp(created_at).lower()
                    if 'updated_at' in cols and (
                        self.search_text in updated_at.lower()
                        or self.search_text in updated_display
                    ):
                        matched = True
                    if (
                        not matched
                        and 'created_at' in cols
                        and (
                            self.search_text in created_at.lower()
                            or self.search_text in created_display
                        )
                    ):
                        matched = True

                if matched:
                    filtered.append(a)

        if not self._stop_requested:
            self.results_ready.emit(filtered)


class TypeProbeWorker(QThread):
    """Resolve corrected asset types from small payload headers off the UI thread."""

    results_ready = Signal(list)

    def __init__(self, cache_manager: CacheManager, requests: list[_TypeProbeKey]) -> None:
        super().__init__()
        self.cache_manager = cache_manager
        self.requests = requests
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        results: list[_TypeProbeResult] = []
        for asset_id, asset_type, cache_hash in self.requests:
            if self._stop_requested:
                return

            try:
                detected_type = self.cache_manager.detect_asset_type_from_header(
                    asset_id, asset_type
                )
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                log_buffer.log('Scraper', f'Failed to detect type for {asset_id}: {exc}')
                detected_type = None

            results.append((asset_id, asset_type, cache_hash, detected_type))

        if not self._stop_requested:
            self.results_ready.emit(results)


class DeleteWorkerThread(QThread):
    """Worker thread for deleting multiple assets without blocking UI."""

    progress = Signal(int, int)  # (current, total)
    deletion_complete = Signal(int, int)  # (deleted_count, failed_count)

    def __init__(self, assets: list[_AssetRecord], cache_manager: CacheManager) -> None:
        super().__init__()
        self.assets = assets
        self.cache_manager = cache_manager
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        """Delete assets in background thread using batch delete for efficiency."""
        if self._stop_requested or not self.assets:
            self.deletion_complete.emit(0, 0)
            return

        # Convert assets list to (asset_id, asset_type) tuples
        assets_to_delete = [(a['id'], a['type']) for a in self.assets]

        # Use batch delete which only writes index once (much faster than N writes)
        deleted_count, failed_count = self.cache_manager.delete_assets_batch(assets_to_delete)

        if not self._stop_requested:
            self.deletion_complete.emit(deleted_count, failed_count)


class ImageLoaderThread(QThread):
    """Worker thread for loading and processing images."""

    image_ready = Signal(QPixmap)
    error = Signal(str)

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _load_pixmap(self) -> tuple[QPixmap, int, int] | None:
        log_buffer.log('Preview', f'Loading image ({len(self.data)} bytes)')
        data = self.data
        ktx_convert = cast(
            'Callable[[bytes], bytes | None]',
            _lazy_attr('.tools.ktx_to_png', 'convert'),
        )
        strip_prefixed_ktx = cast(
            'Callable[[bytes], bytes | None]',
            _lazy_attr('.tools.ktx_to_png', 'strip_prefixed_ktx'),
        )
        ktx_payload = strip_prefixed_ktx(data)
        if ktx_payload is not None:
            log_buffer.log('Preview', 'KTX detected, converting to PNG...')
            data = ktx_convert(ktx_payload)
            if data is None:
                if not self._stop_requested:
                    self.error.emit(tr('cache.preview.ktx_conversion_failed'))
                return None

        image = Image.open(io.BytesIO(data))
        if self._stop_requested:
            return None
        if image.mode not in {'RGB', 'RGBA'} or image.mode == 'RGB':
            image = image.convert('RGBA')
        if self._stop_requested:
            return None

        qimage = QImage(
            image.tobytes(),
            image.width,
            image.height,
            QImage.Format.Format_RGBA8888,
        )
        return QPixmap.fromImage(qimage), image.width, image.height

    def run(self) -> None:
        try:
            result = self._load_pixmap()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Image load error: {exc}')
                self.error.emit(str(exc))
            return
        if result is not None and not self._stop_requested:
            pixmap, width, height = result
            log_buffer.log('Preview', f'Image loaded: {width}x{height}')
            self.image_ready.emit(pixmap)


class MeshLoaderThread(QThread):
    """Worker thread for loading and converting meshes."""

    mesh_ready = Signal(str)  # OBJ content
    error = Signal(str)

    def __init__(self, data: bytes, asset_id: str) -> None:
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _convert_mesh(self) -> str | None:
        log_buffer.log('Preview', f'Loading mesh {self.asset_id} ({len(self.data)} bytes)')
        decompressed = self.data
        if self.data.startswith(b'\x1f\x8b'):
            decompressed = gzip_module.decompress(self.data)
            log_buffer.log('Preview', f'Decompressed mesh: {len(decompressed)} bytes')
        if self._stop_requested:
            return None
        return mesh_processing.convert(decompressed)

    def run(self) -> None:
        try:
            obj_content = self._convert_mesh()
        except (EOFError, OSError, RuntimeError, TypeError, ValueError, zlib.error) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Mesh conversion error: {exc}')
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            log_buffer.log('Preview', 'Mesh converted successfully')
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('cache.preview.mesh_conversion_failed'))


class SolidModelLoaderThread(QThread):
    """Worker thread for loading and converting solid models (CSG)."""

    mesh_ready = Signal(str)  # OBJ content
    error = Signal(str)

    def __init__(self, data: bytes, asset_id: str) -> None:
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _convert_solid_model(self) -> str | None:
        log_buffer.log(
            'Preview',
            f'Loading SolidModel {self.asset_id} ({len(self.data)} bytes)',
        )
        if self._stop_requested:
            return None

        deserialize_rbxm = cast(
            'Callable[[bytes], object]',
            _lazy_attr('.tools.solidmodel_converter.converter', 'deserialize_rbxm'),
        )
        export_obj_from_doc = cast(
            '_ExportObjCallable',
            _lazy_attr('.tools.solidmodel_converter.converter', 'export_obj_from_doc'),
        )
        decompressed = self.data
        if self.data.startswith(b'\x1f\x8b'):
            decompressed = gzip_module.decompress(self.data)
            log_buffer.log('Preview', f'Decompressed SolidModel: {len(decompressed)} bytes')

        doc = deserialize_rbxm(decompressed)
        with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as f:
            temp_obj_path = Path(f.name)
        try:
            export_obj_from_doc(doc, temp_obj_path, decompose=False)
            return temp_obj_path.read_text(encoding='utf-8')
        finally:
            with contextlib.suppress(OSError):
                temp_obj_path.unlink(missing_ok=True)

    def run(self) -> None:
        try:
            obj_content = self._convert_solid_model()
        except (EOFError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'SolidModel conversion error: {exc}')
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            log_buffer.log('Preview', 'SolidModel converted successfully')
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('cache.preview.solidmodel_conversion_failed'))


class AnimationLoaderThread(QThread):
    """Worker thread for loading animation data asynchronously."""

    animation_ready = Signal(bytes)  # Animation data ready to load into viewer
    error = Signal(str)

    def __init__(self, data: bytes, asset_id: str) -> None:
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _animation_data(self) -> bytes:
        decompressed = self.data
        if self.data.startswith(b'\x1f\x8b'):
            decompressed = gzip_module.decompress(self.data)
            log_buffer.log('Preview', f'Decompressed animation: {len(decompressed)} bytes')
        return decompressed

    def run(self) -> None:
        try:
            decompressed = self._animation_data()
        except (EOFError, OSError, zlib.error) as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Animation load error: {e}')
                self.error.emit(str(e))
            return
        if not self._stop_requested:
            # The actual animation loading must happen on main thread due to OpenGL context.
            self.animation_ready.emit(decompressed)


class TexturePackLoaderThread(QThread):
    """Worker thread for loading texture pack images asynchronously."""

    texture_loaded = Signal(str, str, str, bytes)  # map_name, map_id, hash, image_data
    texture_error = Signal(str, str)  # map_name, error_message
    finished_loading = Signal()

    def __init__(
        self,
        maps: dict[str, str | int],
        cache_manager: CacheManager,
        cache_scraper: _CacheScraper | None = None,
    ) -> None:
        super().__init__()
        self.maps = maps
        self.cache_manager = cache_manager
        self._cache_scraper = cache_scraper
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _load_texture(self, map_name: str, map_id: str | int) -> tuple[bytes, str] | None:
        data = self.cache_manager.get_asset(str(map_id), 1)
        if data:
            asset_info = cast(
                '_AssetRecord | None', self.cache_manager.get_asset_info(str(map_id), 1)
            )
            hash_val = asset_info.get('hash', '') if asset_info else ''
            log_buffer.log('Preview', f'Loaded {map_name} from cache')
            return data, hash_val

        if self._stop_requested:
            return None
        log_buffer.log('Preview', f'Fetching {map_name} from API')
        if self._cache_scraper is not None:
            cookie = _get_roblosecurity()
            extra: dict[str, str] = {}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            data = _scraper_https_get(
                self._cache_scraper,
                'assetdelivery.roblox.com',
                f'/v1/asset/?id={map_id}',
                extra or None,
            )
            if not data:
                self.texture_error.emit(map_name, tr('cache.texturepack.api_no_data'))
                return None
            return data, ''

        api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={map_id}'
        headers = {'User-Agent': 'Mozilla/5.0'}
        cookie = _get_roblosecurity()
        if cookie:
            headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        response = requests.get(api_url, headers=headers, timeout=10)
        if response.status_code == 200 and response.content:
            return response.content, ''
        self.texture_error.emit(
            map_name,
            tr('cache.texturepack.api_error', status_code=response.status_code),
        )
        return None

    def run(self) -> None:
        log_buffer.log('Preview', f'Loading texture pack with {len(self.maps)} maps')
        for map_name, map_id in self.maps.items():
            if self._stop_requested:
                return
            try:
                loaded = self._load_texture(map_name, map_id)
            except (OSError, RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
                if not self._stop_requested:
                    log_buffer.log('Preview', f'Texture {map_name} error: {exc}')
                    self.texture_error.emit(map_name, str(exc))
                continue
            if loaded is None:
                continue
            if self._stop_requested:
                return
            data, hash_val = loaded
            self.texture_loaded.emit(map_name, str(map_id), hash_val, data)

        if not self._stop_requested:
            log_buffer.log('Preview', 'Texture pack loading complete')
            self.finished_loading.emit()


class AssetLoaderThread(QThread):
    """Worker thread for downloading assets from Roblox API and storing them in the cache."""

    progress = Signal(int, int)  # (current, total)
    asset_loaded = Signal(str, str, int)  # (asset_id, name, asset_type)
    finished_loading = Signal(int, int)  # (loaded_count, failed_count)
    status_message = Signal(str)  # status text for the dialog

    def __init__(
        self,
        asset_ids: list[int],
        cache_manager: CacheManager,
        cache_scraper: _CacheScraper | None = None,
    ) -> None:
        super().__init__()
        self.asset_ids = asset_ids
        self.cache_manager = cache_manager
        self._cache_scraper = cache_scraper
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    @staticmethod
    def _creator_place_ids(
        sess: requests.Session,
        creator_id: int,
        creator_type: int,
    ) -> Iterator[int]:
        creator_game_max_scan = cast(
            'int',
            _lazy_attr('fleasion.proxy.addons.cache_scraper', 'CREATOR_GAME_MAX_SCAN'),
        )
        creator_game_page_limits = cast(
            'tuple[int, ...]',
            _lazy_attr('fleasion.proxy.addons.cache_scraper', 'CREATOR_GAME_PAGE_LIMITS'),
        )
        creator_game_base_paths = cast(
            'Callable[[int, int, int], list[str]]',
            _lazy_attr('fleasion.proxy.addons.cache_scraper', 'creator_game_base_paths'),
        )

        seen_pids: set[int] = set()
        attempted_paths: set[str] = set()
        for limit in creator_game_page_limits:
            found_before_limit = len(seen_pids)
            max_pages = max(1, (creator_game_max_scan + limit - 1) // limit)
            for game_path in creator_game_base_paths(creator_id, creator_type, limit):
                if game_path in attempted_paths:
                    continue
                attempted_paths.add(game_path)
                cursor = ''
                for _page in range(max_pages):
                    path = game_path + (f'&cursor={cursor}' if cursor else '')
                    response = sess.get(
                        f'https://games.roblox.com{path}',
                        headers={'Accept': 'application/json'},
                        timeout=10,
                    )
                    if response.status_code != 200:
                        break
                    response_json = cast('dict[str, object]', response.json())
                    games = cast('list[dict[str, object]]', response_json.get('data', []))
                    for game in games:
                        root_place = cast('dict[str, object] | None', game.get('rootPlace'))
                        if not root_place or not root_place.get('id'):
                            continue
                        place_id = int(cast('int', root_place['id']))
                        if place_id in seen_pids:
                            continue
                        seen_pids.add(place_id)
                        yield place_id
                    cursor = cast('str', response_json.get('nextPageCursor') or '')
                    if not cursor:
                        break
            if len(seen_pids) > found_before_limit:
                return

    @classmethod
    def _retry_asset_with_creator_places(
        cls,
        sess: requests.Session,
        asset_id: str,
        *,
        api_url: str,
        headers: dict[str, str],
        creator_id: int,
        creator_type: int,
    ) -> bytes | None:
        # Creator-game lookup is best-effort; any backend/schema failure falls back to a miss
        with contextlib.suppress(Exception):
            for place_id in cls._creator_place_ids(sess, creator_id, creator_type):
                retry_headers = {**headers, 'Roblox-Place-Id': str(place_id)}
                response = sess.get(
                    api_url,
                    headers=retry_headers,
                    timeout=15,
                    allow_redirects=True,
                )
                if response.status_code == 200 and response.content:
                    log_buffer.log(
                        'Scraper',
                        f'[Load Asset] Place-ID bypass succeeded for {asset_id}',
                    )
                    return response.content
        return None

    def _download_asset_data(
        self,
        sess: requests.Session,
        asset_id: str,
        metadata: _FetchedAssetMetadata | None,
        cookie: str | None,
    ) -> bytes | None:
        if self._cache_scraper is not None:
            extra: dict[str, str] = {}
            if cookie:
                extra['Cookie'] = f'.ROBLOSECURITY={cookie};'
            data, _status = _scraper_fetch_asset_with_place_id_retry(
                self._cache_scraper,
                asset_id,
                extra or None,
            )
            return data

        api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
        headers = {'User-Agent': 'Roblox/WinInet'}
        if cookie:
            headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        response = sess.get(api_url, headers=headers, timeout=15, allow_redirects=True)
        data = response.content if response.status_code == 200 else None
        if data is not None or response.status_code != 403 or metadata is None:
            return data

        creator_id = metadata.get('creator_id')
        creator_type = metadata.get('creator_type')
        if creator_id is None or creator_type is None:
            return None
        return self._retry_asset_with_creator_places(
            sess,
            asset_id,
            api_url=api_url,
            headers=headers,
            creator_id=int(creator_id),
            creator_type=int(creator_type),
        )

    def run(self) -> None:

        total = len(self.asset_ids)
        loaded_count = 0
        failed_count = 0

        if not self.asset_ids or self._stop_requested:
            self.finished_loading.emit(0, 0)
            return

        # Get authentication cookie
        cookie = _get_roblosecurity()

        # Build session
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update(
            {
                'User-Agent': 'Roblox/WinInet',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            }
        )
        if cookie:
            try:
                sess.cookies.set('.ROBLOSECURITY', cookie)
            except TypeError, ValueError:
                sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

        # Phase 1: Batch-fetch asset metadata (name, type, creator, timestamps) in groups of 50
        self.status_message.emit(tr('cache.load_assets.fetching_asset_info'))
        log_buffer.log('Scraper', f'[Load Asset] Fetching info for {format_count(total, "asset")}')

        asset_metadata: dict[str, _FetchedAssetMetadata] = {}  # asset_id_str -> metadata
        batch_size = 50
        str_ids = [str(aid) for aid in self.asset_ids]

        for i in range(0, len(str_ids), batch_size):
            if self._stop_requested:
                self.finished_loading.emit(loaded_count, failed_count)
                return

            batch = str_ids[i : i + batch_size]
            query = ','.join(batch)
            url = f'https://develop.roblox.com/v1/assets?assetIds={query}'

            try:
                response = sess.get(url, timeout=10)
                response.raise_for_status()
                asset_metadata.update(_asset_metadata_from_response(response.json()))
                log_buffer.log(
                    'Scraper',
                    f'[Load Asset] Fetched metadata for batch {i // batch_size + 1}',
                )
            except (TypeError, ValueError, requests.RequestException) as exc:
                log_buffer.log('Scraper', f'[Load Asset] Failed to fetch metadata batch: {exc}')

        # Phase 2: Resolve creator names
        creators_to_resolve: dict[int, int] = {}
        for meta in asset_metadata.values():
            cid = meta.get('creator_id')
            ctype = meta.get('creator_type')
            if cid is not None and ctype is not None and cid not in creators_to_resolve:
                creators_to_resolve[cid] = ctype

        creator_names: dict[int, str] = {}
        if creators_to_resolve:
            self.status_message.emit(tr('cache.load_assets.resolving_creator_names'))
            log_buffer.log(
                'Scraper',
                f'[Load Asset] Resolving {format_count(creators_to_resolve, "creator name")}',
            )

            # Batch-resolve users
            user_ids = [cid for cid, ctype in creators_to_resolve.items() if ctype == 1]
            group_ids = [cid for cid, ctype in creators_to_resolve.items() if ctype == 2]

            if user_ids:
                try:
                    resp = sess.post(
                        'https://users.roblox.com/v1/users',
                        json={'userIds': user_ids, 'excludeBannedUsers': False},
                        timeout=10,
                    )
                    resp.raise_for_status()
                    creator_names.update(_creator_names_from_response(resp.json()))
                except (TypeError, ValueError, requests.RequestException) as exc:
                    log_buffer.log('Scraper', f'[Load Asset] Failed to fetch user names: {exc}')

            for gid in group_ids:
                if self._stop_requested:
                    break
                try:
                    resp = sess.get(
                        f'https://groups.roblox.com/v1/groups/{gid}',
                        timeout=10,
                    )
                    resp.raise_for_status()
                    response_json = cast('dict[str, object]', resp.json())
                    creator_names[gid] = cast('str', response_json.get('name', 'Unknown'))
                except (TypeError, ValueError, requests.RequestException) as exc:
                    log_buffer.log('Scraper', f'[Load Asset] Failed to fetch group {gid}: {exc}')

        # Store creator names back into asset_metadata
        for meta in asset_metadata.values():
            cid = meta.get('creator_id')
            if cid is not None and cid in creator_names:
                meta['creator_name'] = creator_names[cid]

        # Phase 3: Download each asset's data and store in cache IN PARALLEL
        # Use ThreadPoolExecutor for concurrent downloads to dramatically improve speed.
        # The V1 assetdelivery endpoint doesn't support batch data download, so we
        # parallelize individual requests. 6 workers gives good throughput without
        # hitting rate limits too aggressively.
        self.status_message.emit(tr('cache.load_assets.downloading_assets'))
        log_buffer.log(
            'Scraper',
            f'[Load Asset] Starting parallel download of {format_count(total, "asset")}',
        )

        progress_lock = threading.Lock()
        progress_count = [0]  # mutable counter for closure

        def _download_one(aid_str: str) -> tuple[str, bool]:
            """Download a single asset. Returns (aid_str, success)."""
            if self._stop_requested:
                return aid_str, False

            meta = asset_metadata.get(aid_str)
            asset_type = meta['type'] if meta else 1
            asset_name = meta['name'] if meta else 'Unknown'

            try:
                data = self._download_asset_data(sess, aid_str, meta, cookie)
                if data:
                    self.cache_manager.store_asset(
                        aid_str,
                        asset_type,
                        data,
                        url=f'https://assetdelivery.roblox.com/v1/asset/?id={aid_str}',
                    )
                    log_buffer.log(
                        'Scraper',
                        f'[Load Asset] Stored asset {aid_str} ({asset_name})',
                    )
                    return aid_str, True
                log_buffer.log('Scraper', f'[Load Asset] No data returned for asset {aid_str}')

            except (
                ImportError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
                requests.RequestException,
            ) as exc:
                log_buffer.log('Scraper', f'[Load Asset] Failed to download asset {aid_str}: {exc}')
            return aid_str, False

        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_download_one, aid_str): aid_str for aid_str in str_ids}

            for future in as_completed(futures):
                if self._stop_requested:
                    pool.shutdown(wait=False, cancel_futures=True)
                    break

                aid_str, success = future.result()
                if success:
                    loaded_count += 1
                    meta = asset_metadata.get(aid_str)
                    asset_name = meta['name'] if meta else 'Unknown'
                    self.asset_loaded.emit(aid_str, asset_name, meta['type'] if meta else 1)
                else:
                    failed_count += 1

                with progress_lock:
                    progress_count[0] += 1
                    done = progress_count[0]
                self.progress.emit(done, total)
                self.status_message.emit(
                    tr('cache.load_assets.downloaded_progress', done=done, total=total)
                )

        # Re-stamp cached_at timestamps to preserve the user's original input order.
        # Parallel downloads finish in arbitrary order, so the auto-generated timestamps
        # from store_asset() don't reflect the intended sequence.
        # We assign monotonically increasing timestamps (1ms apart) so that:
        #   first ID in list  → earliest timestamp → bottom of descending sort
        #   last ID in list   → latest timestamp   → top of descending sort

        base_time = datetime.now(UTC).astimezone().replace(tzinfo=None)
        assets_index = cast('dict[str, _AssetRecord]', self.cache_manager.index['assets'])
        with _cache_lock(self.cache_manager):
            for order_idx, aid_str in enumerate(str_ids):
                meta = asset_metadata.get(aid_str)
                asset_type = meta['type'] if meta else 1
                asset_key = f'{asset_type}_{aid_str}'
                entry = assets_index.get(asset_key)
                if entry is not None:
                    # Offset: first ID gets base_time, last ID gets base_time + N ms
                    entry['cached_at'] = (base_time + timedelta(milliseconds=order_idx)).isoformat()
            _schedule_cache_index_commit(self.cache_manager)

        # Store resolved metadata so the name resolver picks it up
        self._resolved_metadata = asset_metadata
        self._resolved_creator_names = creator_names

        log_buffer.log(
            'Scraper',
            f'[Load Asset] Complete: {loaded_count} loaded, {failed_count} failed',
        )
        self.finished_loading.emit(loaded_count, failed_count)


# Column 0 is the fixed-width toggle/counter column; data columns begin at 1.
COL_TOGGLE_WIDTH = 14

_SCRAPER_COLUMN_META: list[_ScraperColumn] = [
    # (key, default_visible, default_width)
    ('hash_name', True, 200),
    ('creator', False, 120),
    ('asset_id', True, 100),
    ('type', True, 120),
    ('size', True, 70),
    ('cached_at', True, 135),
    ('updated_at', False, 180),
    ('created_at', False, 180),
    ('url', False, 300),
]


def _scraper_columns() -> list[_ResolvedScraperColumn]:
    labels = {
        'hash_name': tr('cache.scraper.column.hash_name'),
        'creator': tr('cache.scraper.column.creator'),
        'asset_id': tr('cache.scraper.column.asset_id'),
        'type': tr('cache.scraper.column.type'),
        'size': tr('cache.scraper.column.size'),
        'cached_at': tr('cache.scraper.column.cached_at'),
        'updated_at': tr('cache.scraper.column.updated_at'),
        'created_at': tr('cache.scraper.column.created_at'),
        'url': tr('cache.scraper.column.url'),
    }
    return [(key, labels[key], visible, width) for key, visible, width in _SCRAPER_COLUMN_META]


class _LazyScraperColumns:
    """Compatibility view that resolves translated labels when accessed."""

    def __iter__(self) -> Iterator[_ResolvedScraperColumn]:
        return iter(_scraper_columns())

    def __len__(self) -> int:
        return len(_SCRAPER_COLUMN_META)

    @overload
    def __getitem__(self, index: int) -> _ResolvedScraperColumn: ...

    @overload
    def __getitem__(self, index: slice) -> list[_ResolvedScraperColumn]: ...

    def __getitem__(
        self, index: int | slice
    ) -> _ResolvedScraperColumn | list[_ResolvedScraperColumn]:
        return _scraper_columns()[index]


SCRAPER_COLUMNS = _LazyScraperColumns()


# Logical index → column key  (index 0 = toggle column, 1+ = data columns)
_COL_IDX_TO_KEY: list[str] = ['_toggle'] + [c[0] for c in _SCRAPER_COLUMN_META]
# Column key → logical index
_COL_KEY_TO_IDX: dict[str, int] = {
    '_toggle': 0,
    **{c[0]: i + 1 for i, c in enumerate(_SCRAPER_COLUMN_META)},
}

_SEARCH_COLUMN_META: list[_SearchColumn] = [
    # (key, default_active)
    ('id', True),
    ('type', True),
    ('name', True),
    ('creator', True),
    ('hash', True),
    ('cached_at', True),
    ('updated_at', False),
    ('created_at', False),
    ('url', False),
]


def _search_columns() -> list[_ResolvedSearchColumn]:
    labels = {
        'id': tr('cache.search.column.asset_id'),
        'type': tr('cache.search.column.type'),
        'name': tr('cache.search.column.name'),
        'creator': tr('cache.search.column.creator'),
        'hash': tr('cache.search.column.hash'),
        'cached_at': tr('cache.search.column.cached_at'),
        'updated_at': tr('cache.search.column.updated_at'),
        'created_at': tr('cache.search.column.created_at'),
        'url': tr('cache.search.column.url'),
    }
    return [(key, labels[key], default) for key, default in _SEARCH_COLUMN_META]


_ALL_SEARCH_COL_KEYS: frozenset[str] = frozenset(k for k, _default in _SEARCH_COLUMN_META)
_DEFAULT_SEARCH_COL_KEYS: frozenset[str] = frozenset(
    k for k, default in _SEARCH_COLUMN_META if default
)


class ColumnFilterPopup(QMenu):
    """Simple popup to pick which fields are included in the text search."""

    cols_changed = Signal(set)

    def __init__(
        self, parent: QWidget | None = None, active_cols: Collection[str] | None = None
    ) -> None:
        super().__init__(parent)
        self.setStyleSheet("""
            QMenu { background-color: palette(window); border: 1px solid palette(mid);
                    border-radius: 4px; color: palette(window-text); }
            QWidget#ColContainer { background-color: palette(window); }
            QCheckBox { padding: 2px 4px; color: palette(window-text); font-size: 12px; }
            QCheckBox::indicator { width: 14px; height: 14px; }
        """)
        self.active_cols = (
            set(active_cols) if active_cols is not None else set(_DEFAULT_SEARCH_COL_KEYS)
        )

        container = QWidget()
        container.setObjectName('ColContainer')
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(10, 10, 10, 10)
        vbox.setSpacing(4)

        self.checkboxes: dict[str, QCheckBox] = {}
        for key, label, _default in _search_columns():
            cb = QCheckBox(label)
            cb.setChecked(key in self.active_cols)

            def on_state_changed(state: int, key_: str = key) -> None:
                self._on_toggle(key_, bool(state))

            cb.stateChanged.connect(on_state_changed)
            self.checkboxes[key] = cb
            vbox.addWidget(cb)

        btn_row = QHBoxLayout()
        all_btn = QPushButton(tr('ui.cache.cache_viewer.all'))
        all_btn.setFixedHeight(22)
        all_btn.clicked.connect(self._select_all)
        none_btn = QPushButton(tr('ui.cache.cache_viewer.none'))
        none_btn.setFixedHeight(22)
        none_btn.clicked.connect(self._select_none)
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)
        vbox.addLayout(btn_row)

        wa = QWidgetAction(self)
        wa.setDefaultWidget(container)
        self.addAction(wa)

    def _on_toggle(self, key: str, checked: bool) -> None:
        if checked:
            self.active_cols.add(key)
        else:
            self.active_cols.discard(key)
        self.cols_changed.emit(set(self.active_cols))

    def _select_all(self) -> None:
        self.active_cols = set(_ALL_SEARCH_COL_KEYS)
        for cb in self.checkboxes.values():
            with QSignalBlocker(cb):
                cb.setChecked(True)
        self.cols_changed.emit(set(self.active_cols))

    def _select_none(self) -> None:
        self.active_cols.clear()
        for cb in self.checkboxes.values():
            with QSignalBlocker(cb):
                cb.setChecked(False)
        self.cols_changed.emit(set(self.active_cols))


class ColumnVisibilityMenu(QMenu):
    """
    A non-closing QMenu that lets the user toggle which Scraper columns are
    visible.  Styled identically to the ObjViewer options menu (native Qt
    checkable actions).  The menu only closes when the user clicks outside it.
    """

    visibility_changed = Signal(dict)  # {col_key: bool}

    def __init__(self, column_visibility: dict[str, bool], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._col_visibility = dict(column_visibility)
        self._actions: dict[str, QAction] = {}
        self._building = True

        for key, label, _default, _w in _scraper_columns():
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self._col_visibility.get(key, True))

            def on_toggled(checked: bool, key_: str = key) -> None:
                self._on_toggled(key_, checked)

            action.toggled.connect(on_toggled)
            self.addAction(action)
            self._actions[key] = action

        self._building = False

    # Prevent the menu from closing when the user clicks a checkable item.
    # It will still close on Escape or clicking outside.
    @override
    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:
        if a0 is None:
            return
        if a0.button() != Qt.MouseButton.LeftButton:
            a0.ignore()
            return
        action = self.actionAt(a0.pos())
        if action and action.isCheckable():
            action.toggle()  # manually toggle without closing
            return
        super().mouseReleaseEvent(a0)

    def _on_toggled(self, key: str, checked: bool) -> None:
        if self._building:
            return

        self._col_visibility[key] = checked

        # Enforce: at least one column must remain visible
        any_visible = any(self._col_visibility.values())
        if not any_visible:
            # Revert this action and restore Hash/Name
            self._building = True
            self._col_visibility[key] = True
            self._actions[key].setChecked(True)
            self._col_visibility['hash_name'] = True
            self._actions['hash_name'].setChecked(True)
            self._building = False

        self.visibility_changed.emit(dict(self._col_visibility))

    def update_from(self, col_visibility: dict[str, bool]) -> None:
        """Sync action states from an external dict (e.g. after config load)."""
        self._building = True
        for key, action in self._actions.items():
            action.setChecked(col_visibility.get(key, True))
        self._col_visibility = dict(col_visibility)
        self._building = False


class CacheViewerTab(QWidget):
    """Tab for viewing and managing cached Roblox assets."""

    # Signal to request table sync from background threads (thread-safe)
    _sync_table_requested = Signal()

    def __init__(
        self,
        cache_manager: CacheManager,
        cache_scraper: _CacheScraper | None = None,
        parent: QWidget | None = None,
        config_manager: _ConfigManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.cache_manager = cache_manager
        self.cache_scraper = cache_scraper
        self.config_manager = config_manager
        self._active_filters: set[_AssetTypeFilter] = set()
        self._active_search_cols: set[str] = self._load_search_cols()
        self._last_asset_count = 0  # Track for change detection
        self._selected_asset_id: str | None = None  # Track selected asset by ID
        self._show_names = config_manager.show_names if config_manager is not None else True
        self._show_creator_id = (
            config_manager.show_creator_id if config_manager is not None else False
        )
        self._asset_info: dict[
            str, _ResolvedAssetInfo
        ] = {}  # asset_id -> resolved metadata, hash, row
        self._current_pixmap: QPixmap | None = None  # Store current image for resize
        # OpenGL preview surfaces are intentionally created only for a 3D
        # preview. Rendering stays offscreen and is copied into raster widgets
        # so the dashboard never depends on native OpenGL window presentation.
        self.obj_viewer: ObjViewerPanel | None = None
        self.animation_viewer: AnimationViewerPanel | None = None

        # OPTIMIZATION: Cache asset_id -> row mapping for O(1) lookups instead of O(n) linear search
        # Updated whenever table structure changes (populate, sort). Validates on read for thread-safety.
        self._asset_row_cache: dict[str, int] = {}
        self._modified_rbxm_drafts: dict[tuple[str, object], _RbxmDraft] = {}
        self._rbxm_preview_asset_key: tuple[str, object] | None = None
        self._rbxm_preview_cached_at = ''

        # Worker threads for async preview loading
        self._image_loader: ImageLoaderThread | None = None
        self._mesh_loader: MeshLoaderThread | SolidModelLoaderThread | None = None
        self._animation_loader: AnimationLoaderThread | None = None
        self._texturepack_loader: TexturePackLoaderThread | None = None

        # Search worker thread
        self._search_worker: SearchWorkerThread | None = None
        self._pending_search_text: str = ''
        self._is_searching: bool = False

        # Delete worker thread
        self._delete_worker: DeleteWorkerThread | None = None

        # Asset loader worker thread
        self._asset_loader: AssetLoaderThread | None = None
        self._is_deleting: bool = False

        # Type correction is lazy: only rows near the viewport are probed.
        # Include the cache hash in each key so a replaced payload is checked
        # again even when its asset ID and type stay the same.
        self._type_probe_pending: dict[_TypeProbeKey, _TypeProbeKey] = {}
        self._type_probe_inflight: set[_TypeProbeKey] = set()
        self._type_probe_checked: set[_TypeProbeKey] = set()
        self._type_probe_worker: TypeProbeWorker | None = None

        # Blacklisted asset IDs (excluded from table)
        if config_manager is not None:
            self._blacklisted_ids: set[str] = set(config_manager.scraper_blacklist)
            if self._blacklisted_ids:
                log_buffer.log(
                    'Scraper',
                    f'Loaded blacklist: {format_count(self._blacklisted_ids, "ID")} active — {", ".join(sorted(self._blacklisted_ids, key=lambda x: int(x) if x.isdigit() else 0))}',
                )
        else:
            self._blacklisted_ids: set[str] = set()

        # Texturepack data for context menu
        self._texturepack_data: dict[str, _TexturePackData] = {}  # map_name -> {id, hash, data}
        self._texturepack_xml: str = ''  # Original XML
        # Track whether we've installed the global event filter for audio hotkeys
        self._audio_key_filter_installed = False

        # Column visibility - loaded from config, validated, then applied
        self._col_visibility: dict[str, bool] = self._load_col_visibility()
        # Column widths (pixels) - None means "use default"
        self._col_widths: dict[str, int | None] = self._load_col_widths()
        # Toggle column (col 0) width - start with legacy constant, will be recalculated
        self._col_toggle_width: int = COL_TOGGLE_WIDTH
        # Currently active sort column (logical index). Defaults to Cached At.
        self._sort_col_idx: int = _COL_KEY_TO_IDX['cached_at']
        self._sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder
        # Guard against re-entrant sort-indicator resets when blocking col-0 sort
        self._in_sort_guard: bool = False
        # Reference to the shared non-closing visibility menu (created lazily)
        self._col_visibility_menu: ColumnVisibilityMenu | None = None
        # Guard: prevent re-entrant column resize saves during programmatic resizes
        self._resizing_cols: bool = False

        self._setup_ui()
        self.set_proxy_features_enabled(self._proxy_features_enabled())
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_for_updates)
        self._refresh_timer.start(3000)  # Check every 3 seconds

        # Search debounce timer (longer delay to batch rapid keystrokes)
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._do_search)

        # Filter debounce timer
        self._filter_debounce = QTimer()
        self._filter_debounce.setSingleShot(True)
        self._filter_debounce.timeout.connect(self._refresh_assets)

        self._type_probe_debounce = QTimer()
        self._type_probe_debounce.setSingleShot(True)
        self._type_probe_debounce.timeout.connect(self._queue_visible_type_probes)

        # Load persisted resolved names from index
        self._load_persisted_names()

        # Connect the table sync signal (thread-safe way to update from background threads)
        self._sync_table_requested.connect(self._sync_visible_rows_with_asset_info)

        # Refresh to show persisted names
        QTimer.singleShot(0, self._refresh_assets)

        # Start name resolver daemon thread
        threading.Thread(target=self._name_resolver_loop, daemon=True).start()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop the lightweight type-probe worker before the tab is destroyed."""
        if hasattr(self, '_type_probe_debounce'):
            self._type_probe_debounce.stop()
        worker = getattr(self, '_type_probe_worker', None)
        if worker is not None:
            worker.stop()
            worker.wait()
            self._type_probe_worker = None
        super().closeEvent(event)

    # Column visibility / width helpers

    def _default_col_visibility(self) -> dict[str, bool]:
        return {key: default_vis for key, default_vis, _w in _SCRAPER_COLUMN_META}

    def _load_col_visibility(self) -> dict[str, bool]:
        """Load column visibility from config. Fall back to defaults, validate."""
        defaults = self._default_col_visibility()
        if self.config_manager is None:
            return defaults
        saved = cast(
            'dict[str, object]', self.config_manager.settings.get('scraper_column_visibility', {})
        )
        merged = {**defaults, **{k: bool(v) for k, v in saved.items() if k in defaults}}
        # Validate: at least one visible
        if not any(merged.values()):
            # All off - fall back to Hash/Name only (per spec)
            merged = {key: False for key, *_ in _SCRAPER_COLUMN_META}
            merged['hash_name'] = True

        return merged

    def _load_col_widths(self) -> dict[str, int | None]:
        """Load saved column widths from config."""
        defaults: dict[str, int | None] = {key: None for key, *_ in _SCRAPER_COLUMN_META}
        if self.config_manager is None:
            return defaults
        saved = cast(
            'dict[str, object]', self.config_manager.settings.get('scraper_column_widths', {})
        )
        merged: dict[str, int | None] = {}
        for key, _vis, _default_w in _SCRAPER_COLUMN_META:
            w = saved.get(key)
            merged[key] = int(w) if isinstance(w, (int, float)) and w > 0 else None
        return merged

    def _recalc_toggle_width(self, total_rows: int | None = None) -> None:
        """Recalculate and apply the minimal width for column 0 so numeric
        row counters never get truncated. Uses the table font metrics and
        applies a small padding for spacing.
        """
        try:
            self._apply_toggle_width(total_rows)
        except RuntimeError, TypeError, ValueError:
            # Fall back silently to the legacy constant on Qt/type errors.
            self._col_toggle_width = COL_TOGGLE_WIDTH
            with contextlib.suppress(RuntimeError):
                self.table.setColumnWidth(0, COL_TOGGLE_WIDTH)

    def _apply_toggle_width(self, total_rows: int | None) -> None:
        if total_rows is None:
            total_rows = self.table.rowCount()
        total_rows = max(1, int(total_rows))
        fm = self.table.fontMetrics()
        padding = 10 if sys.platform == 'darwin' else 7
        width = max(
            COL_TOGGLE_WIDTH,
            fm.horizontalAdvance(str(total_rows)) + padding,
            fm.horizontalAdvance('▼') + padding,
        )
        self._col_toggle_width = int(width)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, self._col_toggle_width)

    def _renumber_counters(self) -> None:
        """Renumber the left-most counter column so it shows 1..N in the
        current visible order. This should be called after any sort or
        when the visible ordering changes.
        """
        # Block signals to avoid spurious selection/changed events
        signal_blocker = QSignalBlocker(self.table)
        with contextlib.suppress(Exception):
            row_count = self.table.rowCount()
            for r in range(row_count):
                old = self.table.item(r, 0)
                flags = old.flags() if old is not None else Qt.ItemFlag.ItemIsEnabled
                align = old.textAlignment() if old is not None else Qt.AlignmentFlag.AlignCenter
                new = NumericSortItem(r, str(r + 1))
                new.setFlags(flags)
                new.setTextAlignment(align)
                self.table.setItem(r, 0, new)
        del signal_blocker

        # OPTIMIZATION: Update row cache after sort completes so next sync uses fresh positions
        self._update_asset_row_cache()

    def _save_col_settings(self) -> None:
        """Persist column visibility and widths to config."""
        if self.config_manager is None:
            return
        self.config_manager.settings['scraper_column_visibility'] = dict(self._col_visibility)
        self.config_manager.settings['scraper_column_widths'] = dict(self._col_widths)
        self.config_manager.save()

    def _apply_column_visibility(self, initial: bool = False) -> None:
        """Show/hide table data columns and update resize modes.

        Column 0 (▼ toggle/counter) is always visible and Fixed — never touched here.
        The last *visible* data column (index ≥ 1) gets Stretch so it fills the
        remaining table width with no seam on its right edge.  Every other visible
        data column is Interactive so the user can drag its seam.

        If the currently active sort column is hidden, reset the sort to
        'Cached At'.
        """
        header = self.table.horizontalHeader()

        # Find which data column will be last visible.
        last_visible_idx = -1
        for i, (key, *_) in enumerate(_SCRAPER_COLUMN_META, start=1):
            if self._col_visibility.get(key, True):
                last_visible_idx = i

        for i, (key, *_) in enumerate(_SCRAPER_COLUMN_META, start=1):
            visible = self._col_visibility.get(key, True)
            header.setSectionHidden(i, not visible)
            if visible:
                if i == last_visible_idx:
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch)
                else:
                    header.setSectionResizeMode(i, QHeaderView.ResizeMode.Interactive)

        # If sort column just became hidden, reset to Cached At.
        sort_key = (
            _COL_IDX_TO_KEY[self._sort_col_idx]
            if self._sort_col_idx < len(_COL_IDX_TO_KEY)
            else None
        )
        if sort_key and sort_key != '_toggle' and not self._col_visibility.get(sort_key, True):
            self._sort_col_idx = _COL_KEY_TO_IDX['cached_at']
            self._sort_order: Qt.SortOrder = Qt.SortOrder.DescendingOrder
            self.table.sortByColumn(self._sort_col_idx, Qt.SortOrder.DescendingOrder)

        if not initial:
            self._save_col_settings()
            QTimer.singleShot(0, self._auto_snap_splitter)

    # Column-visibility menu helpers

    def _get_or_create_col_menu(self) -> ColumnVisibilityMenu:
        """Return (and lazily create) the shared ColumnVisibilityMenu."""
        if self._col_visibility_menu is None:
            self._col_visibility_menu = ColumnVisibilityMenu(self._col_visibility, self)
            self._col_visibility_menu.visibility_changed.connect(self._on_col_visibility_changed)
        else:
            # Keep it in sync with any external changes
            self._col_visibility_menu.update_from(self._col_visibility)
        return self._col_visibility_menu

    def _on_header_section_clicked(self, logical_index: int) -> None:
        """Open the visibility menu when the ▼ column (index 0) is clicked."""
        if logical_index == 0:
            menu = self._get_or_create_col_menu()
            # Position below the ▼ header section
            header = self.table.horizontalHeader()
            x = header.sectionPosition(0)
            pos = header.mapToGlobal(header.rect().bottomLeft())
            pos.setX(pos.x() + x)
            menu.exec(pos)

    def _show_col_visibility_from_header(self, _pos: QPoint) -> None:
        """Right-click on any header section: open menu at cursor."""
        menu = self._get_or_create_col_menu()
        menu.exec(QCursor.pos())

    def _on_col_visibility_changed(self, new_visibility: dict[str, bool]) -> None:
        """Called when the user toggles a column in the visibility menu."""
        self._col_visibility = new_visibility
        self._apply_column_visibility()

    # Column resize tracking

    def _on_sort_indicator_changed(self, logical_index: int, order: Qt.SortOrder) -> None:
        """Block sort on col 0 (▼ toggle); track sort column for all others."""
        if self._in_sort_guard:
            return
        if logical_index == 0:
            # Column 0 is the toggle — restore the previous sort immediately
            self._in_sort_guard = True
            self.table.sortByColumn(self._sort_col_idx, self._sort_order)
            self._in_sort_guard = False
            return
        self._sort_col_idx = logical_index
        self._sort_order = order
        # After the internal Qt sort completes (this signal fires before
        # Qt performs the actual sort), renumber the left-most counter
        # column so it always shows 1..N in the current visible order.
        QTimer.singleShot(0, self._renumber_counters)
        QTimer.singleShot(0, self._schedule_visible_type_probes)

    def _on_column_resized(self, logical_index: int, _old_size: int, new_size: int) -> None:
        """Save user-dragged column widths to config."""
        if self._resizing_cols:
            return
        # Col 0 is Fixed, last visible is Stretch — neither should be persisted
        if logical_index == 0:
            return
        header = self.table.horizontalHeader()
        if header.sectionResizeMode(logical_index) == QHeaderView.ResizeMode.Stretch:
            return
        key = _COL_IDX_TO_KEY[logical_index]
        self._col_widths[key] = new_size
        self._save_col_settings()
        QTimer.singleShot(0, self._auto_snap_splitter)

    # Splitter auto-snap

    def _auto_snap_splitter(self) -> None:
        """Resize the splitter so the preview gets as much space as possible.

        Column 0 is a Fixed-width toggle column (COL_TOGGLE_WIDTH).
        The vertical header is hidden — its counter role is filled by col 0.
        The last visible data column is always Stretch; we use only its header
        label minimum width when computing table_min so that an over-wide
        user session doesn't prevent the preview from opening at the right size.
        """
        if self.preview_panel.isHidden():
            return

        total = self.splitter.width()
        if total <= 0:
            return

        header = self.table.horizontalHeader()

        # Find last visible data column (Stretch mode)
        last_visible_idx = -1
        for i in range(len(_SCRAPER_COLUMN_META), 0, -1):
            if not header.isSectionHidden(i):
                last_visible_idx = i
                break

        # Col 0: fixed toggle/counter width (always visible)
        col_w = self._col_toggle_width

        for i in range(1, len(_SCRAPER_COLUMN_META) + 1):
            if header.isSectionHidden(i):
                continue
            if i == last_visible_idx:
                key = _SCRAPER_COLUMN_META[i - 1][0]
                if key == 'url':
                    fm = header.fontMetrics()
                    label = _scraper_columns()[i - 1][1]
                    col_w += fm.horizontalAdvance(label) + 24
                else:
                    col_w += self.table.sizeHintForColumn(i) + 20
            else:
                col_w += self.table.columnWidth(i)

        sb_margin = self.table.verticalScrollBar().sizeHint().width() + 4
        table_min = col_w + sb_margin
        splitter_handle = self.splitter.handleWidth()

        if table_min + splitter_handle < total:
            self.splitter.setSizes([table_min, total - table_min - splitter_handle])
        else:
            table_w = max(int(total * 0.6), table_min)
            preview_w = max(total - table_w - splitter_handle, 50)
            self.splitter.setSizes([table_w, preview_w])

    # resizeEvent - update splitter continuously as main window resizes

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        # Snap splitter in real-time if preview is open
        if not self.preview_panel.isHidden():
            self._auto_snap_splitter()

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._update_table_alt_palette()

    def _update_table_alt_palette(self) -> None:
        """Apply a slightly darker alternate-row colour in dark mode, or reset in light/system mode."""
        pal = self.palette()
        is_dark = pal.color(QPalette.ColorRole.Window).lightness() < 128
        if is_dark:
            table_pal = self.table.palette()
            table_pal.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
            self.table.setPalette(table_pal)
        else:
            self.table.setPalette(QPalette())  # inherit from application

    def _sanitize_filename(self, name: str) -> str:
        """Replace characters that are illegal in Windows filenames with Unicode look-alikes."""
        char_map = {
            '/': '∕',  # U+2215  (division slash)
            '\\': '⧵',  # U+29F5  (reverse solidus operator)
            ':': '꞉',  # U+A789  (modifier letter colon)
            '*': '∗',  # U+2217  (asterisk operator)
            '?': '？',  # U+FF1F  (fullwidth question mark)
            '"': '＂',  # U+FF02  (fullwidth quotation mark)
            '<': '＜',  # U+FF1C  (fullwidth less-than sign)
            '>': '＞',  # U+FF1E  (fullwidth greater-than sign)
            '|': '｜',  # U+FF5C  (fullwidth vertical line)
        }
        return ''.join(char_map.get(c, c) for c in name)

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)

        # Filters (includes scraper toggle and stats)
        self._create_filters(layout)

        # Splitter for table and preview
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Asset table
        table_widget = QWidget()
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._create_table(table_layout)
        table_widget.setLayout(table_layout)
        self.splitter.addWidget(table_widget)

        # Right side: Preview panel
        self.preview_panel = self._create_preview_panel()
        self.splitter.addWidget(self.preview_panel)

        # Set splitter sizes (table gets more space initially)
        self.splitter.setSizes([600, 300])

        # Initially hide the preview panel (as requested: hide if no asset selected)
        self.preview_panel.setHidden(True)

        # Connect splitter moved to rescale image
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        layout.addWidget(self.splitter, stretch=1)

        # Actions
        self._create_actions(layout)

        self.setLayout(layout)
        # Don't refresh here - wait for the queued refresh after persisted names are loaded
        # self._refresh_assets()

    def _create_filters(self, parent_layout: QVBoxLayout) -> None:
        """Create filter controls."""
        filter_group = QGroupBox(tr('ui.cache.cache_viewer.filters'))
        filter_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        filter_layout = QHBoxLayout()

        # Search box first
        filter_layout.addWidget(QLabel(tr('ui.cache.cache_viewer.search')))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText(tr('ui.cache.cache_viewer.search_columns'))
        self.search_box.textChanged.connect(self._on_search_text_changed)
        filter_layout.addWidget(self.search_box)

        # Type selector second
        self.filter_btn = QPushButton(tr('ui.cache.cache_viewer.type_all_types'))
        self.filter_btn.clicked.connect(self._show_filter_popup)
        filter_layout.addWidget(self.filter_btn)

        # Search columns picker
        self.search_col_btn = QPushButton(tr('ui.cache.cache_viewer.search_columns_all'))
        self.search_col_btn.clicked.connect(self._show_search_col_popup)
        self._update_search_col_btn()
        filter_layout.addWidget(self.search_col_btn)

        filter_layout.addStretch()

        sep1 = QLabel(tr('ui.cache.cache_viewer.text'))
        sep1.setStyleSheet('padding-bottom: 6px;')
        filter_layout.addWidget(sep1)

        # Cache scraper toggle - reflect actual scraper state
        self.scraper_toggle = QCheckBox(tr('ui.cache.cache_viewer.enable_cache_scraper'))
        scraper_enabled = self.cache_scraper.enabled if self.cache_scraper else False
        self.scraper_toggle.setChecked(scraper_enabled)
        self.scraper_toggle.stateChanged.connect(self._toggle_scraper)
        filter_layout.addWidget(self.scraper_toggle)

        sep2 = QLabel(tr('ui.cache.cache_viewer.text'))
        sep2.setStyleSheet('padding-bottom: 6px;')
        filter_layout.addWidget(sep2)

        # Stats labels
        self.stats_total_label = QLabel(tr('ui.cache.cache_viewer.total_0_assets'))
        filter_layout.addWidget(self.stats_total_label)

        sep3 = QLabel(tr('ui.cache.cache_viewer.text'))
        sep3.setStyleSheet('padding-bottom: 6px;')
        filter_layout.addWidget(sep3)

        self.stats_size_label = QLabel(tr('ui.cache.cache_viewer.size_0_b'))
        filter_layout.addWidget(self.stats_size_label)

        filter_group.setLayout(filter_layout)
        parent_layout.addWidget(filter_group)

    def _show_filter_popup(self) -> None:
        self.popup = CategoryFilterPopup(self, self._active_filters)
        self.popup.filters_changed.connect(self._on_filters_changed)

        # Position the popup relative to the filter button, keeping it on the
        # same monitor.  The critical rule: NEVER pass a coordinate that is
        # outside the button's screen geometry to QMenu.exec() — Qt interprets
        # an off-screen position as "find nearest available space", which may
        # jump to a completely different monitor.
        #
        # Strategy:
        #   1. Get the screen the button lives on.
        #   2. Force-size the popup so sizeHint() is accurate.
        #   3. Prefer below the button; flip above if it overflows bottom.
        #   4. Clamp X so the right edge stays within the screen.
        #   5. If neither above nor below fits, pin to bottom of screen on same monitor.
        # Force layout so sizeHint reflects real dimensions
        self.popup.adjustSize()

        btn_rect = self.filter_btn.rect()
        btn_bottom = self.filter_btn.mapToGlobal(btn_rect.bottomLeft())
        btn_top = self.filter_btn.mapToGlobal(btn_rect.topLeft())

        screen = cast('QScreen | None', self.filter_btn.screen())
        if screen is None:
            app = cast('QApplication | None', QApplication.instance())
            if app is not None:
                screen = app.screenAt(btn_bottom)

        if screen is None:
            # No screen info — just show below and trust Qt
            self.popup.exec(btn_bottom)
            return

        sg = screen.availableGeometry()
        cast('_ConstrainablePopup', self.popup).constrain_to_available_geometry(sg, btn_bottom.y())
        ph = self.popup.sizeHint().height()
        pw = self.popup.sizeHint().width()

        # X: left-align with button, clamp so right edge stays on screen
        x = btn_bottom.x()
        if x + pw > sg.right():
            x = sg.right() - pw
        x = max(x, sg.left())

        # Y: below preferred; flip above if it overflows the bottom of this screen
        if btn_bottom.y() + ph <= sg.bottom():
            y = btn_bottom.y()
        elif btn_top.y() - ph >= sg.top():
            y = btn_top.y() - ph
        else:
            # Neither fits fully — pin to screen bottom so popup is on correct monitor
            y = sg.bottom() - ph
            y = max(y, sg.top())

        self.popup.exec(QPoint(x, y))

    def _on_filters_changed(self, filters: set[_AssetTypeFilter]) -> None:
        self._active_filters = set(filters)
        count = len(self._active_filters)
        if count == 0:
            self.filter_btn.setText(tr('ui.cache.cache_viewer.type_all_types'))
        elif count == 1:
            tid = next(iter(self._active_filters))
            name = asset_type_display_name(tid)
            self.filter_btn.setText(tr('ui.cache.cache_viewer.type_value', value0=name))
        else:
            self.filter_btn.setText(tr('ui.cache.cache_viewer.value_filters', value0=count))

        self._filter_debounce.start(300)

    # Search columns picker

    def _load_search_cols(self) -> set[str]:
        if self.config_manager is None:
            return set(_DEFAULT_SEARCH_COL_KEYS)
        saved = self.config_manager.settings.get('scraper_search_columns', None)
        if saved is None:
            return set(_DEFAULT_SEARCH_COL_KEYS)
        saved_columns = cast('Collection[object]', saved)
        valid = {k for k in saved_columns if isinstance(k, str) and k in _ALL_SEARCH_COL_KEYS}
        return valid or set(_DEFAULT_SEARCH_COL_KEYS)

    def _save_search_cols(self) -> None:
        if self.config_manager is None:
            return
        self.config_manager.settings['scraper_search_columns'] = sorted(self._active_search_cols)
        self.config_manager.save()

    def _update_search_col_btn(self) -> None:
        cols = self._active_search_cols
        if cols >= _ALL_SEARCH_COL_KEYS:
            self.search_col_btn.setText(tr('ui.cache.cache_viewer.search_columns_all'))
        elif not cols:
            self.search_col_btn.setText(tr('ui.cache.cache_viewer.search_columns_none'))
        elif len(cols) == 1:
            key = next(iter(cols))
            label = next(
                (label for column_key, label, _default in _search_columns() if column_key == key),
                key,
            )
            self.search_col_btn.setText(
                tr('ui.cache.cache_viewer.search_columns_value', value0=label)
            )
        else:
            self.search_col_btn.setText(
                tr('ui.cache.cache_viewer.search_columns_value_selected', value0=len(cols))
            )

    def _show_search_col_popup(self) -> None:
        self._col_popup = ColumnFilterPopup(self, self._active_search_cols)
        self._col_popup.cols_changed.connect(self._on_search_cols_changed)

        self._col_popup.adjustSize()
        btn = self.search_col_btn
        btn_rect = btn.rect()
        btn_bottom = btn.mapToGlobal(btn_rect.bottomLeft())
        btn_top = btn.mapToGlobal(btn_rect.topLeft())

        screen = cast('QScreen | None', btn.screen())
        if screen is None:
            self._col_popup.exec(btn_bottom)
            return

        sg = screen.availableGeometry()
        ph = self._col_popup.sizeHint().height()
        pw = self._col_popup.sizeHint().width()

        x = btn_bottom.x()
        if x + pw > sg.right():
            x = sg.right() - pw
        x = max(x, sg.left())

        if btn_bottom.y() + ph <= sg.bottom():
            y = btn_bottom.y()
        elif btn_top.y() - ph >= sg.top():
            y = btn_top.y() - ph
        else:
            y = max(sg.bottom() - ph, sg.top())

        self._col_popup.exec(QPoint(x, y))

    def _on_search_cols_changed(self, cols: set[str]) -> None:
        self._active_search_cols = cols
        self._update_search_col_btn()
        self._save_search_cols()
        self._search_debounce.start(300)

    def _create_table(self, parent_layout: QVBoxLayout) -> None:
        """Create asset table."""
        self.table = QTableWidget()
        self.table.setColumnCount(len(_COL_IDX_TO_KEY))
        self.table.setHorizontalHeaderLabels(
            ['▼'] + [label for _key, label, _vis, _width in _scraper_columns()]
        )

        header = self.table.horizontalHeader()

        # Column 0: ▼ toggle — Fixed width, never sorted, never resized by user
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        # Use dynamic width based on number of rows so the numeric counter never
        # gets truncated. The actual width will be recalculated when rows are
        # populated via `_recalc_toggle_width`.
        self.table.setColumnWidth(0, self._col_toggle_width)

        # Apply saved (or default) widths for data columns (1-6)
        self._resizing_cols = True
        for i, (key, _vis, default_w) in enumerate(_SCRAPER_COLUMN_META, start=1):
            w = self._col_widths.get(key) or default_w
            self.table.setColumnWidth(i, w)
        self._resizing_cols = False

        # Apply visibility + resize modes for data columns (last visible → Stretch)
        self._apply_column_visibility(initial=True)

        # Hide the native row-number vertical header — col 0 now shows the counter
        self.table.verticalHeader().hide()

        # Intercept clicks on col 0 to open the visibility menu
        # sortIndicatorChanged fires before Qt's internal sort call, so we can
        # restore the previous sort inside the guard without a visible flicker.
        header.sortIndicatorChanged.connect(self._on_sort_indicator_changed)
        # sectionClicked: open the menu when col 0 is clicked
        header.sectionClicked.connect(self._on_header_section_clicked)

        # Right-click on any header section also opens the visibility menu
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_col_visibility_from_header)

        # Save column widths when the user drags a seam
        header.sectionResized.connect(self._on_column_resized)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        # Apply a slightly darker alternating-row colour in dark mode; this is
        # managed by _update_table_alt_palette() and kept in sync via changeEvent.
        self._update_table_alt_palette()
        self.table.currentItemChanged.connect(self._on_selection_changed)
        self.table.verticalScrollBar().valueChanged.connect(self._schedule_visible_type_probes)
        # Prevent column 0 (counter) from ever becoming the current item.
        # If Qt lands on column 0 (e.g. during keyboard nav), silently redirect
        # focus to column 1 of the same row so there is only one selection anchor.
        self.table.currentCellChanged.connect(self._redirect_counter_focus)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        parent_layout.addWidget(self.table)

    def _create_preview_panel(self) -> QWidget:
        """Create preview panel for viewing assets."""
        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_group = QWidget()
        preview_group_layout = QVBoxLayout()
        preview_group_layout.setContentsMargins(0, 0, 0, 0)
        preview_group_layout.setSpacing(4)

        self.preview_title_label = QLabel(tr('ui.cache.cache_viewer.preview'))
        preview_group_layout.addWidget(self.preview_title_label)

        # Scrollable container for all preview content
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Container widget inside scroll area
        self.preview_container = QWidget()
        self.preview_container_layout = QVBoxLayout()
        self.preview_container_layout.setContentsMargins(5, 5, 5, 5)

        # Loading indicator
        self.loading_label = QLabel(tr('ui.cache.cache_viewer.loading'))
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet(
            'QLabel { background-color: palette(base); color: #888; font-size: 14px; padding: 20px; }'
        )
        self.preview_container_layout.addWidget(self.loading_label)
        self.loading_label.hide()

        # Image viewer (will show/hide as needed)
        self.image_label = QLabel(tr('ui.cache.cache_viewer.select_an_asset_to_preview'))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: palette(base); color: #888; }')
        self.image_label.setScaledContents(False)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_image_context_menu)
        self.preview_container_layout.addWidget(self.image_label)

        # Audio player container with centering wrapper
        self.audio_player: AudioPlayerWidget | None = None  # Created dynamically when needed
        self.audio_wrapper = QWidget()
        audio_wrapper_layout = QVBoxLayout()
        audio_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        audio_wrapper_layout.addStretch(1)
        self.audio_container = QWidget()
        self.audio_container_layout = QVBoxLayout()
        self.audio_container_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_container.setLayout(self.audio_container_layout)
        audio_wrapper_layout.addWidget(self.audio_container)
        audio_wrapper_layout.addStretch(1)
        self.audio_wrapper.setLayout(audio_wrapper_layout)
        self.preview_container_layout.addWidget(self.audio_wrapper)

        # Text viewer for other types
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText(tr('ui.cache.cache_viewer.select_an_asset_to_preview'))
        self.preview_container_layout.addWidget(self.text_viewer)
        self._text_viewer_default_font = self.text_viewer.font()
        self._text_viewer_default_wrap = QTextEdit.LineWrapMode.WidgetWidth

        # JSON viewer for JSON files
        self.json_viewer = CacheJsonViewer()
        self.preview_container_layout.addWidget(self.json_viewer)

        # RBXM/RBXMX structure viewer for Roblox model files
        self.rbxm_viewer = RbxmPreviewWidget()
        self.preview_container_layout.addWidget(self.rbxm_viewer)

        # Font viewer for font files
        self.font_wrapper = QWidget()
        font_wrapper_layout = QVBoxLayout()
        font_wrapper_layout.setContentsMargins(0, 0, 0, 0)
        font_wrapper_layout.addStretch(1)
        self.font_container = QWidget()
        self.font_container_layout = QVBoxLayout()
        self.font_container_layout.setContentsMargins(0, 0, 0, 0)
        self.font_container.setLayout(self.font_container_layout)
        font_wrapper_layout.addWidget(self.font_container)
        font_wrapper_layout.addStretch(1)
        self.font_wrapper.setLayout(font_wrapper_layout)
        self.preview_container_layout.addWidget(self.font_wrapper)

        # Texture pack container (dynamically created)
        self.texturepack_widget: QWidget | None = None

        # Set up scroll area
        self.preview_container.setLayout(self.preview_container_layout)
        self.preview_scroll.setWidget(self.preview_container)
        preview_group_layout.addWidget(self.preview_scroll)

        # Initially hide all non-OpenGL preview widgets. The 3D viewers do not
        # exist yet and are created only when a 3D asset is actually previewed.
        self.audio_wrapper.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.rbxm_viewer.hide()

        self.preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(self.preview_group)

        preview_widget.setLayout(preview_layout)
        return preview_widget

    def _ensure_obj_viewer(self) -> ObjViewerPanel:
        """Create the mesh OpenGL widget only when a mesh is actually previewed."""
        if self.obj_viewer is None:
            obj_viewer_panel = cast(
                'type[ObjViewerPanel]',
                _lazy_attr('.obj_viewer', 'ObjViewerPanel'),
            )

            log_buffer.log('OpenGL', 'Creating OBJ preview widget on demand')
            viewer = obj_viewer_panel(
                config_manager=cast('ConfigManager | None', self.config_manager)
            )
            viewer.clear_requested.connect(self._clear_preview)
            viewer.hide()
            self.preview_container_layout.insertWidget(0, viewer)
            self.obj_viewer = viewer
        return self.obj_viewer

    def _ensure_animation_viewer(self) -> AnimationViewerPanel:
        """Create the animation OpenGL widget only when an animation is previewed."""
        if self.animation_viewer is None:
            animation_viewer_panel = cast(
                'type[AnimationViewerPanel]',
                _lazy_attr('.animation_viewer', 'AnimationViewerPanel'),
            )

            log_buffer.log('OpenGL', 'Creating animation preview widget on demand')
            viewer = animation_viewer_panel(
                config_manager=cast('ConfigManager | None', self.config_manager)
            )
            viewer.hide()
            index = self.preview_container_layout.indexOf(self.text_viewer)
            self.preview_container_layout.insertWidget(
                index if index >= 0 else self.preview_container_layout.count(), viewer
            )
            self.animation_viewer = viewer
        return self.animation_viewer

    def _create_actions(self, parent_layout: QVBoxLayout) -> None:
        """Create action buttons."""
        actions_layout = QHBoxLayout()
        actions_layout.setContentsMargins(8, 4, 8, 4)

        delete_db_btn = QPushButton(tr('ui.cache.cache_viewer.delete_db'))
        delete_db_btn.clicked.connect(self._clear_cache)
        actions_layout.addWidget(delete_db_btn)

        delete_cache_btn = QPushButton(tr('ui.cache.cache_viewer.clear_cache'))
        delete_cache_btn.clicked.connect(self._delete_roblox_cache)
        actions_layout.addWidget(delete_cache_btn)

        self.stop_preview_btn = QPushButton(tr('ui.cache.cache_viewer.stop_preview'))
        self.stop_preview_btn.clicked.connect(self._stop_preview)
        self.stop_preview_btn.hide()
        actions_layout.addWidget(self.stop_preview_btn)

        self.rbxm_view_btn = QPushButton(tr('ui.cache.cache_viewer.swap_to_rbxm_view'))
        self.rbxm_view_btn.clicked.connect(self._swap_to_rbxm_view)
        self.rbxm_view_btn.hide()
        actions_layout.addWidget(self.rbxm_view_btn)

        actions_layout.addStretch()

        blacklist_btn = QPushButton(tr('ui.cache.cache_viewer.blacklist_ids'))
        blacklist_btn.clicked.connect(self._show_blacklist_dialog)
        actions_layout.addWidget(blacklist_btn)

        load_asset_btn = QPushButton(tr('ui.cache.cache_viewer.load_asset'))
        load_asset_btn.clicked.connect(self._show_load_asset_dialog)
        actions_layout.addWidget(load_asset_btn)

        open_cache_btn = QPushButton(tr('ui.cache.cache_viewer.open_cache_folder'))
        open_cache_btn.clicked.connect(lambda: open_folder(self.cache_manager.cache_dir))
        actions_layout.addWidget(open_cache_btn)

        open_export_btn = QPushButton(tr('ui.cache.cache_viewer.open_export_folder'))
        open_export_btn.clicked.connect(lambda: open_folder(self.cache_manager.export_dir))
        actions_layout.addWidget(open_export_btn)

        parent_layout.addLayout(actions_layout)

    def _check_for_updates(self) -> None:
        """Check if cache has new assets and update stats only."""
        with contextlib.suppress(Exception):
            stats = cast('dict[str, int]', self.cache_manager.get_cache_stats())
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])
            self.stats_total_label.setText(
                tr('ui.cache.cache_viewer.total_value_assets', value0=total_assets)
            )
            self.stats_size_label.setText(tr('ui.cache.cache_viewer.size_value', value0=total_size))

            # Only refresh table if asset count changed
            if total_assets != self._last_asset_count:
                self._last_asset_count = total_assets
                self._refresh_assets()

    def _refresh_assets(self) -> None:
        """Refresh the asset list using search worker for all searches."""
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Get search text
        search_text = self.search_box.text().strip()

        # Get filter type
        filter_types = self._active_filters

        # Get assets
        assets = cast(
            'list[_AssetRecord]',
            self.cache_manager.list_assets(filter_types),
        )

        # Apply blacklist filter
        if self._blacklisted_ids:
            assets = [a for a in assets if a['id'] not in self._blacklisted_ids]

        # Ensure all assets have _asset_info entries so the background name
        # resolver can discover and resolve them even when a search filter
        # hides them from the table.
        for a in assets:
            aid = a['id']
            if aid not in self._asset_info:
                self._asset_info[aid] = {
                    'hash': a.get('hash', ''),
                    'resolved_name': None,
                    'creator_id': None,
                    'creator_name': None,
                    'creator_type': None,
                    'created_at': a.get('resolved_created_at'),
                    'updated_at': a.get('resolved_updated_at'),
                    'row': None,
                }

        # For empty search, show all immediately
        if not search_text:
            self._populate_table(assets)
            return

        # Always use worker thread for searches to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(
            assets, search_text, self._asset_info, self._active_search_cols
        )
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _populate_table(self, assets: list[_AssetRecord]) -> None:
        """Populate the table with assets."""
        # Save scroll anchor
        # Capture the asset_id of the row at the top of the visible viewport
        # so we can scroll back to it after rebuilding the table.
        # This prevents the "user teleportation" bug where inserting new rows
        # resets or jumps the scroll position unexpectedly.
        anchor_asset_id: str | None = None
        vsb = self.table.verticalScrollBar()
        saved_scroll = vsb.value()
        top_index = self.table.indexAt(self.table.viewport().rect().topLeft())
        if top_index.isValid():
            top_row = top_index.row()
            id_item_ = self.table.item(top_row, 1)  # col 1 carries the asset dict in UserRole
            if id_item_ is not None:
                raw_asset_data = id_item_.data(Qt.ItemDataRole.UserRole)
                if isinstance(raw_asset_data, dict):
                    asset_data = cast('_AssetRecord', raw_asset_data)
                    anchor_asset_id = asset_data.get('id')

        # Disable updates while populating (major performance boost)
        signal_blocker = QSignalBlocker(self.table)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)

        # Track row to restore selection
        row_to_select: int | None = None

        try:
            # Clear old item memory in C++ before allocating new rows
            self.table.clearContents()
            self.table.setRowCount(0)

            # Update table
            self.table.setRowCount(len(assets))

            # Recalculate toggle column width to fit the largest row number
            # without truncation.
            self._recalc_toggle_width(len(assets))

            for row, asset in enumerate(assets):
                asset_id = asset['id']
                hash_val = asset.get('hash', '')

                # Track if this is the previously selected asset
                if self._selected_asset_id and asset_id == self._selected_asset_id:
                    row_to_select = row

                # Initialize or update asset info tracking
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': hash_val,
                        'resolved_name': None,
                        'creator_id': None,
                        'creator_name': None,
                        'creator_type': None,
                        'created_at': asset.get('resolved_created_at'),
                        'updated_at': asset.get('resolved_updated_at'),
                        'row': row,
                    }
                else:
                    self._asset_info[asset_id]['row'] = row
                    if (
                        self._asset_info[asset_id].get('created_at') is None
                        and asset.get('resolved_created_at') is not None
                    ):
                        self._asset_info[asset_id]['created_at'] = asset.get('resolved_created_at')
                    if (
                        self._asset_info[asset_id].get('updated_at') is None
                        and asset.get('resolved_updated_at') is not None
                    ):
                        self._asset_info[asset_id]['updated_at'] = asset.get('resolved_updated_at')

                # Column 0: row counter (1-based), not selectable, centred
                counter_item = NumericSortItem(row, str(row + 1))
                counter_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                # Enabled but NOT selectable and NOT focusable - prevents the counter
                # column from acting as an independent selection anchor when using
                # keyboard navigation, which caused the double-selection UI bug.
                counter_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemNeverHasChildren)
                self.table.setItem(row, 0, counter_item)

                # Column 1: Hash/Name — also carries the asset UserRole payload
                info = self._asset_info[asset_id]
                resolved_name = info.get('resolved_name')
                display_val = resolved_name if self._show_names and resolved_name else hash_val
                name_item = QTableWidgetItem(display_val)
                name_item.setData(Qt.ItemDataRole.UserRole, asset)
                self.table.setItem(row, 1, name_item)

                # Column 2: Creator
                creator_item = QTableWidgetItem(self._creator_display(info))
                self.table.setItem(row, 2, creator_item)

                # Column 3: Asset ID
                id_item = QTableWidgetItem(asset_id)
                self.table.setItem(row, 3, id_item)

                # Column 4: Type
                # Use persisted metadata only while building the table. Payload
                # correction is lazy and runs for rows near the viewport.
                raw_type_name = (
                    asset.get('detected_type')
                    or asset.get('type_name')
                    or self.cache_manager.get_asset_type_name(asset['type'])
                )
                type_name = _localized_asset_type_name(asset['type'], raw_type_name)
                fm = self.table.fontMetrics()
                max_w = max(100, int(self.width() * 0.15))
                elided_type = fm.elidedText(type_name, Qt.TextElideMode.ElideRight, max_w)
                type_item = QTableWidgetItem(elided_type)
                if elided_type != type_name:
                    type_item.setToolTip(type_name)
                self.table.setItem(row, 4, type_item)

                # Column 5: Size
                # For TexturePack show the combined on-disk slot KTX2 sizes so the
                # user sees the real texture data footprint, not the tiny XML size.
                size = asset.get('raw_size', asset.get('size', 0))
                if asset['type'] == 63:
                    with contextlib.suppress(Exception):
                        pack_files = self.cache_manager.get_texturepack_slot_pack_paths(asset_id)
                        if pack_files:
                            tp_slot_size = sum(f.stat().st_size for f in pack_files)
                        else:
                            tp_slot_size = sum(
                                self.cache_manager.get_texturepack_slot_path(asset_id, slot)
                                .stat()
                                .st_size
                                for slot in (0, 1, 2)
                                if self.cache_manager.get_texturepack_slot_path(
                                    asset_id, slot
                                ).exists()
                            )
                        if tp_slot_size > 0:
                            size = tp_slot_size
                size_str = self._format_size(size)
                size_item = NumericSortItem(size, size_str)
                self.table.setItem(row, 5, size_item)

                # Column 6: Cached At
                cached_at = _format_table_timestamp(asset.get('cached_at'))
                cached_item = QTableWidgetItem(cached_at)
                self.table.setItem(row, _COL_KEY_TO_IDX['cached_at'], cached_item)

                # Column 7: Updated At
                updated_at = _format_table_timestamp(
                    info.get('updated_at') or asset.get('resolved_updated_at')
                )
                updated_item = QTableWidgetItem(updated_at)
                self.table.setItem(row, _COL_KEY_TO_IDX['updated_at'], updated_item)

                # Column 8: Created At
                created_at = _format_table_timestamp(
                    info.get('created_at') or asset.get('resolved_created_at')
                )
                created_item = QTableWidgetItem(created_at)
                self.table.setItem(row, _COL_KEY_TO_IDX['created_at'], created_item)

                # Column 9: URL
                url = asset.get('url', '')
                url_item = QTableWidgetItem(url)
                self.table.setItem(row, _COL_KEY_TO_IDX['url'], url_item)

        finally:
            # Re-enable updates
            del signal_blocker
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)

        # Ensure counters reflect the visible ordering (in case a sort
        # operation occurred while populating). Defer to the event loop
        # so any pending sort completes first.
        QTimer.singleShot(0, self._renumber_counters)

        # Restore selection if the asset still exists
        if row_to_select is not None:
            with QSignalBlocker(self.table):
                self.table.selectRow(row_to_select)

        # Restore scroll anchor
        # Rules:
        #   1. If user was at the very top (scroll == 0), stay at the top.
        #      New assets arriving should not push the user away from the top.
        #   2. If user was scrolled down and anchor asset is still visible,
        #      restore it to the top of the viewport.
        #   3. If anchor asset is gone (filter changed), go to top — do NOT
        #      use the saved pixel value which maps to a random row in the new set.
        if saved_scroll == 0:
            # Was at top — stay at top (don't chase anchor, just leave it)
            vsb.setValue(0)
        elif anchor_asset_id is not None:
            new_anchor_row: int | None = None
            for r in range(self.table.rowCount()):
                it = self.table.item(r, 1)
                if it is not None:
                    raw_asset_data = it.data(Qt.ItemDataRole.UserRole)
                    if isinstance(raw_asset_data, dict):
                        d = cast('_AssetRecord', raw_asset_data)
                    else:
                        d = None
                    if d is not None and d.get('id') == anchor_asset_id:
                        new_anchor_row = r
                        break
            if new_anchor_row is not None:
                self.table.scrollTo(
                    self.table.model().index(new_anchor_row, 0),
                    self.table.ScrollHint.PositionAtTop,
                )
            # else: filter changed, anchor gone — leave at top (row 0)

        # Update stats
        with contextlib.suppress(Exception):
            stats = cast('dict[str, int]', self.cache_manager.get_cache_stats())
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])

            self.stats_total_label.setText(
                tr('ui.cache.cache_viewer.total_value_assets', value0=total_assets)
            )
            self.stats_size_label.setText(tr('ui.cache.cache_viewer.size_value', value0=total_size))

            self._last_asset_count = total_assets

        # OPTIMIZATION: Update row cache after table populate so background thread can use cached lookups
        self._update_asset_row_cache()
        self._schedule_visible_type_probes()

    def _schedule_visible_type_probes(self, *_args: object) -> None:
        """Debounce type correction requests caused by scrolling or sorting."""
        if hasattr(self, '_type_probe_debounce'):
            self._type_probe_debounce.start(60)

    def _queue_visible_type_probes(self) -> None:
        """Queue type checks for the visible rows and a small scroll-ahead buffer."""
        row_count = self.table.rowCount()
        if row_count == 0:
            return

        viewport_height = max(1, self.table.viewport().height())
        top_row = self.table.rowAt(0)
        bottom_row = self.table.rowAt(viewport_height - 1)
        top_row = max(top_row, 0)
        if bottom_row < 0:
            bottom_row = row_count - 1

        visible_rows = max(1, bottom_row - top_row + 1)
        prefetch_rows = max(50, visible_rows * 2)
        start_row = max(0, top_row - prefetch_rows)
        end_row = min(row_count, bottom_row + prefetch_rows + 1)

        for row in range(start_row, end_row):
            name_item = self.table.item(row, 1)
            if name_item is None:
                continue

            asset = name_item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(asset, dict):
                continue
            asset_record = cast('_AssetRecord', asset)
            if asset_record.get('type') not in {1, 13}:
                continue
            if asset_record.get('detected_type'):
                continue

            asset_id = str(asset_record.get('id', ''))
            if not asset_id:
                continue
            cache_hash = str(asset_record.get('hash', ''))
            key = (asset_id, int(asset_record['type']), cache_hash)
            if key in self._type_probe_checked or key in self._type_probe_inflight:
                continue
            self._type_probe_pending[key] = key

        self._start_next_type_probe()

    def _start_next_type_probe(self) -> None:
        """Start one bounded header-probe batch, leaving later rows queued."""
        if self._type_probe_worker is not None or not self._type_probe_pending:
            return

        keys = list(self._type_probe_pending)[:128]
        for key in keys:
            self._type_probe_pending.pop(key, None)
            self._type_probe_inflight.add(key)

        worker = TypeProbeWorker(self.cache_manager, keys)
        worker.results_ready.connect(self._on_type_probe_results)
        worker.finished.connect(self._on_type_probe_finished)
        self._type_probe_worker = worker
        worker.start()

    def _on_type_probe_results(self, results: list[_TypeProbeResult]) -> None:
        """Apply header-probe results and update only rows still in the table."""
        self.table.setUpdatesEnabled(False)
        try:
            for asset_id, asset_type, cache_hash, detected_type in results:
                key = (asset_id, asset_type, cache_hash)
                self._type_probe_inflight.discard(key)
                self._type_probe_checked.add(key)

                if not detected_type:
                    continue

                current_info = cast(
                    '_AssetRecord', self.cache_manager.get_asset_info(asset_id, asset_type) or {}
                )
                if str(current_info.get('hash', '')) != cache_hash:
                    continue

                # Persist the correction once, after the cheap header probe,
                # so future sessions do not need to inspect this payload.
                self.cache_manager.set_detected_type(asset_id, asset_type, detected_type)

                row = self._asset_row_cache.get(asset_id)
                if row is None or row >= self.table.rowCount():
                    continue

                name_item = self.table.item(row, 1)
                asset = name_item.data(Qt.ItemDataRole.UserRole) if name_item else None
                if not isinstance(asset, dict):
                    continue
                asset_record = cast('_AssetRecord', asset)
                if (
                    str(asset_record.get('hash', '')) != cache_hash
                    or asset_record.get('type') != asset_type
                ):
                    continue

                asset_record['detected_type'] = detected_type
                asset_record['type_name'] = detected_type
                type_item = self.table.item(row, 4)
                if type_item is None:
                    continue

                fm = self.table.fontMetrics()
                max_w = max(100, int(self.width() * 0.15))
                elided_type = fm.elidedText(detected_type, Qt.TextElideMode.ElideRight, max_w)
                type_item.setText(elided_type)
                type_item.setToolTip(detected_type if elided_type != detected_type else '')
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

    def _on_type_probe_finished(self) -> None:
        """Release the completed worker and continue queued viewport probes."""
        worker = self._type_probe_worker
        self._type_probe_worker = None
        if worker is not None:
            worker.deleteLater()
        self._start_next_type_probe()

    def _format_size(self, size_bytes: float) -> str:
        """Format size in bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.1f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.1f} TB'

    def _toggle_scraper(self, state: int) -> None:
        """Toggle cache scraper on/off."""
        enabled = bool(state)
        owner = getattr(self, '_replacer_window_ref', None)
        if owner is None:
            owner = self.window()
        tray = getattr(owner, '_system_tray', None)
        if tray is not None and _set_tray_cache_scraper_enabled(tray, enabled):
            return
        if self.cache_scraper:
            self.cache_scraper.set_enabled(enabled)

    def _proxy_features_enabled(self) -> bool:
        if self.config_manager is None:
            return True
        return bool(getattr(self.config_manager, 'proxy_features_enabled', True))

    def set_proxy_features_enabled(self, enabled: bool) -> None:
        """Keep scraper browsing available while blocking proxy-only actions."""
        if hasattr(self, 'scraper_toggle'):
            self.scraper_toggle.setEnabled(enabled)
            self.scraper_toggle.setToolTip(
                ''
                if enabled
                else tr('ui.cache.cache_viewer.enable_proxy_features_in_settings_to_use')
            )

    def set_cache_scraper_enabled(self, enabled: bool) -> None:
        """Update the scraper toggle without re-emitting the toggle signal."""
        if hasattr(self, 'scraper_toggle'):
            with QSignalBlocker(self.scraper_toggle):
                self.scraper_toggle.setChecked(enabled)

    def _on_search_text_changed(self) -> None:
        """Handle search text change - debounce to avoid too many searches."""
        self._search_debounce.stop()
        self._search_debounce.start(300)  # 300ms debounce

    def _do_search(self) -> None:
        """Execute the actual search after debounce using worker thread."""
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        search_text = self.search_box.text().strip()

        # Get filter type and assets
        filter_types = self._active_filters
        assets = cast(
            'list[_AssetRecord]',
            self.cache_manager.list_assets(filter_types),
        )

        # For empty search, show all immediately
        if not search_text:
            self._populate_table(assets)
            return

        # Always use worker thread to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(
            assets, search_text, self._asset_info, self._active_search_cols
        )
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_complete(self, filtered_assets: list[_AssetRecord]) -> None:
        """Handle search results from worker thread."""
        self._populate_table(filtered_assets)

    def _on_search_finished(self) -> None:
        """Handle search worker thread finished."""
        self._is_searching = False

    def _on_deletion_complete(self, deleted_count: int, failed_count: int) -> None:
        """Handle deletion completion from worker thread."""
        self._refresh_assets()

        total = deleted_count + failed_count
        if failed_count == 0:
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.success'),
                tr(
                    'ui.cache.cache_viewer.deleted_value',
                    value0=tr_count(deleted_count, 'count.asset.one', 'count.asset.other'),
                ),
            )
        else:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.partial_success'),
                tr(
                    'ui.cache.cache_viewer.deleted_value_value_value_failed_to_delete',
                    value0=deleted_count,
                    value1=tr_count(total, 'count.asset.one', 'count.asset.other'),
                    value2=tr_count(failed_count, 'count.asset.one', 'count.asset.other'),
                ),
            )

        log_buffer.log(
            'Scraper',
            f'Batch deletion completed: {deleted_count} deleted, {failed_count} failed',
        )

    def _on_deletion_finished(self) -> None:
        """Handle deletion worker thread finished."""
        self._is_deleting = False

    def _load_persisted_names(self) -> None:
        """Load persisted resolved names from index.json."""
        loaded_count = 0
        assets_index = cast('dict[str, _AssetRecord]', self.cache_manager.index['assets'])
        for asset_data in assets_index.values():
            asset_id = asset_data['id']
            resolved_name = asset_data.get('resolved_name')
            creator_id = asset_data.get('resolved_creator_id')
            creator_name = asset_data.get('resolved_creator_name')
            creator_type = asset_data.get('resolved_creator_type')
            created_at = asset_data.get('resolved_created_at')
            updated_at = asset_data.get('resolved_updated_at')
            if (
                resolved_name is not None
                or creator_id is not None
                or created_at is not None
                or updated_at is not None
            ):
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': asset_data.get('hash', ''),
                        'resolved_name': resolved_name,
                        'creator_id': creator_id,
                        'creator_name': creator_name,
                        'creator_type': creator_type,
                        'created_at': created_at,
                        'updated_at': updated_at,
                        'row': None,
                    }
                    loaded_count += 1
                else:
                    if resolved_name is not None:
                        self._asset_info[asset_id]['resolved_name'] = resolved_name
                    if creator_id is not None:
                        self._asset_info[asset_id]['creator_id'] = creator_id
                        self._asset_info[asset_id]['creator_name'] = creator_name
                        self._asset_info[asset_id]['creator_type'] = creator_type
                    if created_at is not None:
                        self._asset_info[asset_id]['created_at'] = created_at
                    if updated_at is not None:
                        self._asset_info[asset_id]['updated_at'] = updated_at
        log_buffer.log(
            'Scraper',
            f'[Cache Viewer] Loaded {loaded_count} persisted asset names from index',
        )

    def _creator_display(self, info: _ResolvedAssetInfo) -> str:
        """Return the display text for the creator column based on current toggle state."""
        creator_name = info.get('creator_name')
        creator_id = info.get('creator_id')
        if self._show_creator_id:
            if creator_id is not None:
                return str(creator_id)
            return creator_name or ''
        if creator_name is not None:
            return creator_name
        if creator_id is not None:
            return str(creator_id)
        return ''

    def _on_show_creator_id_toggled(self, checked: bool) -> None:
        """Handle Show User ID toggle — refresh the creator column for all rows."""
        self._show_creator_id = checked
        if self.config_manager is not None:
            self.config_manager.show_creator_id = checked
        self.table.setUpdatesEnabled(False)
        try:
            for info in self._asset_info.values():
                row = info.get('row')
                if row is None or row >= self.table.rowCount():
                    continue
                creator_item = self.table.item(row, 2)
                if creator_item:
                    creator_item.setText(self._creator_display(info))
        finally:
            self.table.setUpdatesEnabled(True)

    def _on_show_names_toggled(self, checked: bool) -> None:
        """Handle Show Names toggle."""
        self._show_names = checked
        if self.config_manager is not None:
            self.config_manager.show_names = checked

        # Disable updates for performance
        self.table.setUpdatesEnabled(False)
        try:
            # Update all rows to show either resolved name or hash
            for info in self._asset_info.values():
                row = info.get('row')
                if row is None:
                    continue
                if row >= self.table.rowCount():
                    continue

                resolved_name = info.get('resolved_name')
                display_val = resolved_name if checked and resolved_name else info.get('hash', '')

                item = self.table.item(row, 1)  # Hash/Name is now col 1
                if item:
                    item.setText(display_val)
        finally:
            # Re-enable updates
            self.table.setUpdatesEnabled(True)

    def _update_asset_row_cache(self) -> None:
        """Update the asset_id->row mapping cache after table structure changes.

        Called after table populate or sort operations. This enables O(1) lookups
        instead of O(n) linear searches in _find_row_for_asset.

        OPTIMIZATION: Caches asset positions so the background name resolver thread
        doesn't have to linearly search the table on every sync.
        """
        self._asset_row_cache.clear()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # Name column has UserRole data
            if item:
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if asset_data:
                    asset_id = asset_data.get('id')
                    if asset_id:
                        self._asset_row_cache[asset_id] = row

    def _find_row_for_asset(self, asset_id: str) -> int | None:
        """Find the actual row index for an asset.

        OPTIMIZATION: Uses cached asset_id->row mapping for O(1) lookup when valid.
        Falls back to linear search (with validation) if cache miss/stale, which also
        updates the cache automatically.

        Args:
            asset_id: The asset ID to find

        Returns:
            The current row index if found, None otherwise
        """
        # Try cache first (O(1) path - common case)
        if asset_id in self._asset_row_cache:
            row = self._asset_row_cache[asset_id]
            # Validate cache is still correct (handles sort/filter invalidation)
            if row < self.table.rowCount():
                item = self.table.item(row, 1)
                if item:
                    asset_data = item.data(Qt.ItemDataRole.UserRole)
                    if asset_data and asset_data.get('id') == asset_id:
                        return row  # Cache hit!
            # Cache is stale, fall through to linear search and update

        # Linear search (O(n) path - fallback, also updates cache on hit)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)  # Name column (col 1) has UserRole data
            if item:
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if asset_data and asset_data.get('id') == asset_id:
                    self._asset_row_cache[asset_id] = row  # Update cache for next time
                    return row
        return None

    def _sync_visible_rows_with_asset_info(self) -> None:
        """Update visible table rows with resolved names/creators from _asset_info.

        Called after the background name-resolver finishes a batch.

        Performance contract: O(k) where k = number of assets with new data.
        Never falls back to O(n) linear scan — if an asset isn't in
        _asset_row_cache it's simply not in the current view (filtered/sorted
        out) and we skip it.  The cache is always rebuilt by
        _update_asset_row_cache() after every _populate_table call, so a
        cache miss genuinely means "not visible", not "cache stale".
        """
        row_count = self.table.rowCount()
        if row_count == 0:
            return

        self.table.setUpdatesEnabled(False)
        try:
            for asset_id, info in self._asset_info.items():
                # Fast O(1) cache lookup — no linear scan ever.
                row = self._asset_row_cache.get(asset_id)
                if row is None or row >= row_count:
                    continue  # Not in current view; skip.

                # Validate cache is still correct (sort/filter may shift rows).
                # Validation is O(1) — just one item() call.
                item = self.table.item(row, 1)
                if not item:
                    continue
                asset_data = item.data(Qt.ItemDataRole.UserRole)
                if not asset_data or asset_data.get('id') != asset_id:
                    # Cache is stale for this asset — skip rather than scan.
                    # It will be corrected on the next _populate_table call.
                    continue

                # Update name column if resolved
                resolved_name = info.get('resolved_name')
                if self._show_names and resolved_name and item.text() != resolved_name:
                    item.setText(resolved_name)

                # Update creator column if resolved
                if info.get('creator_name') is not None or info.get('creator_id') is not None:
                    creator_item = self.table.item(row, 2)
                    if creator_item:
                        desired = self._creator_display(info)
                        if creator_item.text() != desired:
                            creator_item.setText(desired)

                # Update Roblox asset timestamps if resolved
                updated_item = self.table.item(row, _COL_KEY_TO_IDX['updated_at'])
                updated_at = _format_table_timestamp(info.get('updated_at'))
                if updated_item and updated_item.text() != updated_at:
                    updated_item.setText(updated_at)
                created_item = self.table.item(row, _COL_KEY_TO_IDX['created_at'])
                created_at = _format_table_timestamp(info.get('created_at'))
                if created_item and created_item.text() != created_at:
                    created_item.setText(created_at)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.viewport().update()

        # If a search is active, re-run it so that assets whose names were
        # just resolved (and now match the query) appear in the results.
        if self.search_box.text().strip():
            self._search_debounce.start(400)

    def _update_row_name(self, asset_id: str, name: str) -> None:
        """Update a single row's name cell (thread-safe via QTimer)."""
        info = self._asset_info.get(asset_id)
        if not info:
            return
        row = info.get('row')
        if row is None or row >= self.table.rowCount():
            return
        # Only update if Show Names is enabled
        if self._show_names:
            item = self.table.item(row, 1)  # Hash/Name is col 1
            if item:
                item.setText(name)
                # Force immediate repaint of this row
                self.table.viewport().update()

    def _update_row_creator(
        self, asset_id: str, _creator_id: int | None, _creator_name: str | None
    ) -> None:
        """Update a single row's creator cell (thread-safe via QTimer).

        Args:
            asset_id: The asset ID
            creator_id: The numeric creator ID (for fallback display)
            creator_name: The resolved creator name (preferred display)
        """
        info = self._asset_info.get(asset_id)
        if not info:
            return
        row = info.get('row')
        if row is None or row >= self.table.rowCount():
            return
        item = self.table.item(row, 2)  # Creator is col 2
        if item:
            item.setText(self._creator_display(info))
            # Force immediate repaint of this row
            self.table.viewport().update()

    def _save_resolved_name_to_index(self, asset_id: str, name: str) -> None:
        """Save resolved name to index.json for persistence."""
        assets_index = cast('dict[str, _AssetRecord]', self.cache_manager.index['assets'])
        asset_keys = list(assets_index.keys())
        for asset_key in asset_keys:
            if asset_key not in assets_index:
                continue
            asset_data = assets_index[asset_key]
            if asset_data['id'] == asset_id:
                asset_data['resolved_name'] = name
                break

    def _save_resolved_creator_to_index(
        self,
        asset_id: str,
        creator_id: int | None,
        creator_name: str | None,
        creator_type: int | None,
    ) -> None:
        """Save resolved creator info to index.json for persistence."""
        assets_index = cast('dict[str, _AssetRecord]', self.cache_manager.index['assets'])
        asset_keys = list(assets_index.keys())
        for asset_key in asset_keys:
            if asset_key not in assets_index:
                continue
            asset_data = assets_index[asset_key]
            if asset_data['id'] == asset_id:
                asset_data['resolved_creator_id'] = creator_id
                asset_data['resolved_creator_name'] = creator_name
                asset_data['resolved_creator_type'] = creator_type
                break

    def _save_resolved_timestamps_to_index(
        self, asset_id: str, created_at: str | None, updated_at: str | None
    ) -> None:
        """Save Roblox asset created/updated timestamps to index.json for persistence."""
        assets_index = cast('dict[str, _AssetRecord]', self.cache_manager.index['assets'])
        asset_keys = list(assets_index.keys())
        for asset_key in asset_keys:
            if asset_key not in assets_index:
                continue
            asset_data = assets_index[asset_key]
            if asset_data['id'] == asset_id:
                asset_data['resolved_created_at'] = created_at or ''
                asset_data['resolved_updated_at'] = updated_at or ''
                break

    def _get_roblosecurity(self) -> str | None:
        return _get_roblosecurity()

    def _fetch_asset_names(
        self, asset_ids: list[str], cookie: str | None
    ) -> dict[str, _FetchedNameMetadata] | None:
        """Fetch asset names and creator info from Roblox Develop API (batch up to 50).

        Returns a dict keyed by asset_id with values:
            {'name': str, 'creator_id': int|None, 'creator_type': int|None}
        """

        if not asset_ids:
            return None

        # Build session with auth
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update(
            {
                'User-Agent': 'Roblox/WinInet',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            }
        )
        if cookie:
            try:
                # Prefer setting cookie on the session so requests handles it properly
                sess.cookies.set('.ROBLOSECURITY', cookie)
            except TypeError, ValueError:
                # Fallback to header if cookie set fails
                sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

        # Build query: assetIds=123,456,789
        query = ','.join(str(aid) for aid in asset_ids)
        url = f'https://develop.roblox.com/v1/assets?assetIds={query}'

        try:
            response = sess.get(url, timeout=10)
            response.raise_for_status()
            response_json = cast('object', response.json())
        except requests.RequestException as e:
            log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch names: {e}')
            return None

        if not isinstance(response_json, dict):
            log_buffer.log('Scraper', '[Name Resolver] Asset response was not a JSON object')
            return None
        response_mapping = cast('dict[str, object]', response_json)
        raw_data = response_mapping.get('data', [])
        if not isinstance(raw_data, list):
            log_buffer.log('Scraper', '[Name Resolver] Asset response data was not a list')
            return None

        result: dict[str, _FetchedNameMetadata] = {}
        for raw_item in cast('list[object]', raw_data):
            if not isinstance(raw_item, dict):
                continue
            item = cast('dict[str, object]', raw_item)
            aid = item.get('id')
            if aid is None:
                continue

            # Newer API returns a nested 'creator' object; older APIs used
            # flat 'creatorTargetId' and 'creatorType' fields. Support both.
            creator_obj = cast('object', item.get('creator') or {})
            creator_id: int | None = None
            creator_type: int | None = None

            # New format: {'type': 'User'|'Group', 'typeId': 1|2, 'targetId': <id>}
            if isinstance(creator_obj, dict) and creator_obj:
                creator_data = cast('dict[str, object]', creator_obj)
                creator_id = cast('int | None', creator_data.get('targetId'))
                creator_type = cast('int | None', creator_data.get('typeId'))

            # Fallback to legacy flat fields
            if creator_id is None:
                creator_id = cast('int | None', item.get('creatorTargetId'))
            if creator_type is None:
                creator_type = cast('int | None', item.get('creatorType'))

            # Normalise numeric types (ensure int or None)
            try:
                if creator_type is not None:
                    creator_type = int(creator_type)
            except TypeError, ValueError:
                creator_type = None
            try:
                if creator_id is not None:
                    creator_id = int(creator_id)
            except TypeError, ValueError:
                creator_id = None

            result[str(aid)] = {
                'name': cast('str', item.get('name', 'Unknown')),
                'creator_id': creator_id,
                'creator_type': creator_type,  # 1 = User, 2 = Group
                'created_at': cast('str', item.get('created') or ''),
                'updated_at': cast('str', item.get('updated') or ''),
            }

        return result

    def _fetch_creator_names(
        self, creators: dict[int, int], sess: requests.Session
    ) -> dict[int, str]:
        """Resolve creator IDs to display names.

        Args:
            creators: dict mapping creator_id (int) → creator_type (int)
                      creator_type 1 = User, 2 = Group
            sess: requests.Session to reuse

        Returns:
            dict mapping creator_id (int) → creator display name (str)
        """
        result: dict[int, str] = {}
        if not creators:
            return result

        user_ids = [cid for cid, ctype in creators.items() if ctype == 1]
        group_ids = [cid for cid, ctype in creators.items() if ctype == 2]

        # Batch-resolve users via POST /v1/users
        if user_ids:
            try:
                resp = sess.post(
                    'https://users.roblox.com/v1/users',
                    json={'userIds': user_ids, 'excludeBannedUsers': False},
                    timeout=10,
                )
                resp.raise_for_status()
                response_json = cast('object', resp.json())
            except requests.RequestException as e:
                # If user batch lookup fails, continue without user names
                log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch user names: {e}')
            else:
                result.update(_creator_names_from_response(response_json))

        # Resolve groups one-by-one (no batch endpoint on v1)
        for gid in group_ids:
            try:
                resp = sess.get(
                    f'https://groups.roblox.com/v1/groups/{gid}',
                    timeout=10,
                )
                resp.raise_for_status()
                response_json = cast('object', resp.json())
            except requests.RequestException as e:
                # If a single group lookup fails, skip that group
                log_buffer.log('Scraper', f'[Name Resolver] Failed to fetch group {gid}: {e}')
                continue
            if not isinstance(response_json, dict):
                continue
            response_mapping = cast('dict[str, object]', response_json)
            name = response_mapping.get('name', 'Unknown')
            if isinstance(name, str):
                result[gid] = name

        return result

    def _name_resolver_loop(self) -> None:
        """Background thread to resolve asset names and creator names."""

        while True:
            # Skip if Show Names is OFF
            if not self._show_names:
                time.sleep(0.2)
                continue

            # Get authentication cookie
            cookie = self._get_roblosecurity()
            if not cookie:
                # No cookie - wait longer to avoid spam
                time.sleep(5)
                continue

            try:
                # Build pending list - assets without resolved names
                # Build pending list - assets without resolved names.
                # Prioritise assets in visible rows, then resolve the rest so that
                # search-by-name works even for assets not currently displayed.
                row_count = self.table.rowCount()
            except RuntimeError:
                # Widget has been deleted (app shutting down)
                break

            visible: list[str] = []
            hidden: list[str] = []
            for asset_id, info in self._asset_info.items():
                if not _asset_metadata_needs_resolution(info):
                    continue
                row = info.get('row')
                if row is not None and row < row_count:
                    visible.append(asset_id)
                else:
                    hidden.append(asset_id)
            pending = visible + hidden

            if not pending:
                time.sleep(0.2)
                continue

            # Batch size and delay
            batch_size = 50
            delay = 0.2 if len(pending) > 50 else 0.5

            # Take the first batch
            batch = pending[:batch_size]

            # Fetch names + creator IDs
            try:
                asset_data_map = self._fetch_asset_names(batch, cookie)
            except (RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
                log_buffer.log('Scraper', f'[Name Resolver] Fetch failed: {exc}')
                time.sleep(delay)
                continue

            if not asset_data_map:
                time.sleep(delay)
                continue

            # Build a reusable session for creator lookups (same auth headers)
            sess = requests.Session()
            sess.trust_env = False
            sess.proxies = {}
            sess.headers.update(
                {
                    'User-Agent': 'Roblox/WinInet',
                    'Content-Type': 'application/json',
                    'Accept': 'application/json',
                    'Referer': 'https://www.roblox.com/',
                    'Origin': 'https://www.roblox.com',
                }
            )
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

            # Collect creator IDs that need name resolution
            creators_to_resolve: dict[int, int] = {}  # creator_id → creator_type
            for data in asset_data_map.values():
                cid = data.get('creator_id')
                ctype = data.get('creator_type')
                if cid is not None and ctype is not None and cid not in creators_to_resolve:
                    creators_to_resolve[cid] = ctype

            log_buffer.log(
                'Scraper',
                f'[Name Resolver] Collected {format_count(creators_to_resolve, "unique creator ID")} to resolve',
            )

            # Fetch creator display names
            creator_names: dict[int, str] = {}
            if creators_to_resolve:
                try:
                    creator_names = self._fetch_creator_names(creators_to_resolve, sess)
                except (RuntimeError, TypeError, ValueError, requests.RequestException) as exc:
                    log_buffer.log('Scraper', f'[Name Resolver] Creator fetch failed: {exc}')

            log_buffer.log(
                'Scraper',
                f'[Name Resolver] Resolved {format_count(creator_names, "creator name")}',
            )

            # Update cache and UI
            for asset_id, data in asset_data_map.items():
                info = self._asset_info.get(asset_id)
                if not info:
                    continue

                name = data.get('name', 'Unknown')
                creator_id = data.get('creator_id')
                creator_type = data.get('creator_type')
                creator_name = creator_names.get(creator_id) if creator_id is not None else None
                created_at = data.get('created_at') or ''
                updated_at = data.get('updated_at') or ''
                # Store resolved name in memory
                info['resolved_name'] = name
                info['creator_id'] = creator_id
                info['creator_type'] = creator_type
                info['creator_name'] = creator_name
                info['created_at'] = created_at
                info['updated_at'] = updated_at

                # Save to index.json for persistence
                self._save_resolved_name_to_index(asset_id, name)
                self._save_resolved_creator_to_index(
                    asset_id, creator_id, creator_name, creator_type
                )
                self._save_resolved_timestamps_to_index(asset_id, created_at, updated_at)

            # Save index after batch update (less frequent saves)
            try:
                _save_cache_index(self.cache_manager)
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                log_buffer.log('Scraper', f'[Name Resolver] Failed to save index: {exc}')

            # CRITICAL: After resolving a batch, immediately sync all visible rows with the updated data
            # This ensures that the last asset (and all assets) show their resolved names/creators
            # without waiting for the next _populate_table call
            # Emit signal which is thread-safe and connected to the sync slot
            self._sync_table_requested.emit()

            time.sleep(delay)

    def _get_selected_asset(self) -> _AssetRecord | None:
        """Get the currently selected asset."""
        current_row = self.table.currentRow()
        if current_row < 0:
            return None

        id_item = self.table.item(current_row, 1)  # col 1 = Hash/Name (carries UserRole)
        if not id_item:
            return None

        return cast('_AssetRecord | None', id_item.data(Qt.ItemDataRole.UserRole))

    def _export_selected(self) -> None:
        """Export the selected asset."""
        asset = self._get_selected_asset()
        if not asset:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.no_selection'),
                tr('ui.cache.cache_viewer.please_select_an_asset_to_export'),
            )
            return

        # Ask for export location
        default_name = f'{asset["id"]}.bin'
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.cache.cache_viewer.export_asset'),
            default_name,
            tr('ui.cache.cache_viewer.all_files'),
        )

        if not file_path:
            return

        # Sanitize resolved name if present (though the user's chosen filename already safe)
        asset_id = asset['id']
        resolved_name = None
        if asset_id in self._asset_info:
            resolved_name = self._asset_info[asset_id].get('resolved_name')
        safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

        export_path = self.cache_manager.export_asset(
            asset['id'], asset['type'], Path(file_path), resolved_name=safe_name
        )

        if export_path:
            log_buffer.log('Scraper', f'Exported asset {asset["id"]} to {export_path}')
            self._show_export_complete_message(
                tr('cache.export.success_title'),
                tr('cache.export.asset_exported_to', path=export_path),
                [export_path],
            )
        else:
            QMessageBox.critical(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.failed_to_export_asset'),
            )

    def _get_export_open_target(
        self, exported_paths: Sequence[_ExportPath | None]
    ) -> tuple[Path, bool]:
        """Return the Explorer target and whether it should be selected."""

        existing_paths: list[Path] = []
        for path in exported_paths:
            if not path:
                continue
            p = Path(path)
            if p.exists():
                existing_paths.append(p)

        if not existing_paths:
            return self.cache_manager.export_dir, False

        if len(existing_paths) == 1:
            path = existing_paths[0]
            return path, path.is_file()

        containers: list[Path] = [path if path.is_dir() else path.parent for path in existing_paths]
        if all(container == containers[0] for container in containers):
            return containers[0], False

        common_parent = Path(os.path.commonpath([str(container) for container in containers]))
        return common_parent, False

    def _open_export_target(self, exported_paths: Sequence[_ExportPath | None]) -> None:
        """Open exported output in Explorer, selecting a single exported file when possible."""

        target, select_file = self._get_export_open_target(exported_paths)
        target = Path(target)

        def _fallback_open_folder() -> None:
            # Multi-tier fallback: parent of selected file -> computed target -> root export dir.
            if select_file:
                open_folder(target.parent)
                return
            open_folder(target if target.is_dir() else self.cache_manager.export_dir)

        try:
            _open_export_path(
                target,
                select_file=select_file,
                export_dir=self.cache_manager.export_dir,
            )
        except (OSError, RuntimeError) as exc:
            log_buffer.log('Export', f'Could not open export target {target}: {exc}')
            try:
                _fallback_open_folder()
            except (OSError, RuntimeError) as fallback_exc:
                log_buffer.log(
                    'Export',
                    f'Fallback open failed for export target {target}: {fallback_exc}',
                )

    def _show_export_complete_message(
        self, title: str, message: str, exported_paths: Sequence[_ExportPath | None]
    ) -> None:
        """Show an export completion dialog with a shortcut to the destination folder."""
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(title)
        msg.setText(message)
        open_button = msg.addButton(
            tr('ui.cache.cache_viewer.open_in_explorer'), QMessageBox.ButtonRole.ActionRole
        )
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()

        if msg.clickedButton() == open_button:
            self._open_export_target(exported_paths)

    def _export_all(self) -> None:
        """Export all visible assets."""
        # Get current filter
        filter_types = self._active_filters
        assets = cast(
            'list[_AssetRecord]',
            self.cache_manager.list_assets(filter_types),
        )

        # Apply search filter across all columns (same as _refresh_assets)
        search_text = self.search_box.text().strip().lower()
        if search_text:
            filtered: list[_AssetRecord] = []
            for a in assets:
                asset_id = a['id'].lower()
                type_name = a['type_name'].lower()
                url = a.get('url', '').lower()
                hash_val = a.get('hash', '').lower()
                size_str = self._format_size(a.get('raw_size', a.get('size', 0))).lower()
                cached_at = a.get('cached_at', '').lower()
                cached_at_display = _format_table_timestamp(a.get('cached_at')).lower()
                created_at = (a.get('resolved_created_at') or '').lower()
                updated_at = (a.get('resolved_updated_at') or '').lower()
                created_at_display = _format_table_timestamp(a.get('resolved_created_at')).lower()
                updated_at_display = _format_table_timestamp(a.get('resolved_updated_at')).lower()

                resolved_name = ''
                if asset_id in self._asset_info:
                    info = self._asset_info[asset_id]
                    name = info.get('resolved_name')
                    resolved_name = name.lower() if name else ''
                    created_at = (info.get('created_at') or created_at).lower()
                    updated_at = (info.get('updated_at') or updated_at).lower()
                    created_at_display = _format_table_timestamp(
                        info.get('created_at') or created_at
                    ).lower()
                    updated_at_display = _format_table_timestamp(
                        info.get('updated_at') or updated_at
                    ).lower()

                searchable_fields = (
                    asset_id,
                    type_name,
                    url,
                    hash_val,
                    resolved_name,
                    size_str,
                    cached_at,
                    cached_at_display,
                    updated_at,
                    updated_at_display,
                    created_at,
                    created_at_display,
                )
                if any(search_text in field for field in searchable_fields):
                    filtered.append(a)
            assets = filtered

        if not assets:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.no_assets'),
                tr('ui.cache.cache_viewer.no_assets_to_export'),
            )
            return

        reply = QMessageBox.question(
            self,
            tr('ui.cache.cache_viewer.export_all'),
            tr(
                'ui.cache.cache_viewer.export_value_to_the_export_folder',
                value0=tr_count(assets, 'count.asset.one', 'count.asset.other'),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        exported_count = 0
        exported_paths: list[Path] = []
        for asset in assets:
            asset_id = asset['id']
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')
            safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

            export_path = self.cache_manager.export_asset(
                asset['id'], asset['type'], resolved_name=safe_name
            )
            if export_path:
                exported_count += 1
                exported_paths.append(export_path)

        log_buffer.log('Scraper', f'Exported {exported_count}/{len(assets)} assets')
        location, _ = self._get_export_open_target(exported_paths)
        self._show_export_complete_message(
            tr('cache.export.complete_title'),
            tr(
                'cache.export.one_asset_location'
                if exported_count == 1
                else 'cache.export.assets_location',
                count=exported_count,
                location=location,
            ),
            exported_paths,
        )

    def _delete_selected(self) -> None:
        """Delete the selected assets using background worker thread."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.no_selection'),
                tr('ui.cache.cache_viewer.please_select_assets_to_delete'),
            )
            return

        # Collect assets to delete
        assets_to_delete: list[_AssetRecord] = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    assets_to_delete.append(asset)

        if not assets_to_delete:
            return

        # Confirm deletion
        count = len(assets_to_delete)
        reply = QMessageBox.question(
            self,
            tr('ui.cache.cache_viewer.delete_assets'),
            tr(
                'ui.cache.cache_viewer.delete_value',
                value0=tr_count(count, 'count.asset.one', 'count.asset.other'),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # Use background worker thread for fast batch deletion
            self._is_deleting = True
            self._delete_worker = DeleteWorkerThread(assets_to_delete, self.cache_manager)
            self._delete_worker.deletion_complete.connect(self._on_deletion_complete)
            self._delete_worker.finished.connect(self._on_deletion_finished)
            self._delete_worker.start()

    def _reset_cache_database(self) -> None:
        """Reset cached files, index state, and in-flight cache work."""
        if hasattr(self, '_type_probe_debounce'):
            self._type_probe_debounce.stop()
        type_probe_worker = self._type_probe_worker
        if type_probe_worker is not None:
            type_probe_worker.stop()
            type_probe_worker.wait()
            self._type_probe_worker = None
        self._type_probe_pending.clear()
        self._type_probe_inflight.clear()
        self._type_probe_checked.clear()

        self.cache_manager.clear_memory_cache()
        if self.cache_scraper:
            reset_scraper = getattr(self.cache_scraper, 'reset_for_cache_clear', None)
            if callable(reset_scraper):
                reset_scraper()
            else:
                self.cache_scraper.clear_tracking()

        cache_dir = self.cache_manager.cache_dir
        if cache_dir.exists():
            shutil.rmtree(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_manager.index = cast('CacheIndex', {'assets': {}})
        _save_cache_index(self.cache_manager)
        self._last_asset_count = 0
        self._asset_info.clear()
        self._refresh_assets()
        log_buffer.log('Scraper', 'Database deleted and reset')

    def _clear_cache(self) -> None:
        """Delete the entire cache database and files (old Delete DB functionality)."""
        reply = QMessageBox.question(
            self,
            tr('ui.cache.cache_viewer.delete_database'),
            tr('ui.cache.cache_viewer.this_will_delete_all_cached_assets_and'),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self._reset_cache_database()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.critical(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.failed_to_delete_database_value', value0=exc),
            )
            return
        QMessageBox.information(
            self,
            tr('ui.cache.cache_viewer.success'),
            tr('ui.cache.cache_viewer.database_deleted_successfully'),
        )

    def _delete_roblox_cache(self) -> None:
        """Delete Roblox cache using system tray method."""
        delete_cache_window = cast(
            'Callable[[], QWidget]',
            _lazy_attr('fleasion.gui', 'DeleteCacheWindow'),
        )
        window = delete_cache_window()
        window.show()

    def _redirect_counter_focus(
        self, current_row: int, current_col: int, _previous_row: int, _previous_col: int
    ) -> None:
        """If Qt moves focus onto column 0 (the counter), immediately redirect it
        to column 1 of the same row.  This ensures there is always exactly one
        selection anchor and prevents the double-selection / jump bug that occurs
        during keyboard navigation when column 0 is non-selectable.
        """
        if current_col == 0 and current_row >= 0:
            with QSignalBlocker(self.table):
                self.table.setCurrentCell(current_row, 1)

    def _set_preview_title(self, asset_id: str, asset: _AssetRecord) -> None:
        info = self._asset_info.get(asset_id, {})
        resolved = info.get('resolved_name') if info else None
        display = resolved or asset.get('hash') or asset_id
        if len(display) > 60:
            display = display[:57] + '...'
        self.preview_title_label.setText(tr('ui.cache.cache_viewer.preview_value', value0=display))

    def _preview_selected_asset(
        self,
        asset: _AssetRecord,
        asset_type: int,
        asset_id: str,
    ) -> None:
        data = self.cache_manager.get_asset(asset_id, asset_type)
        if not data:
            self._show_text_preview(tr('cache.preview.failed_to_load_asset', asset_id=asset_id))
            return
        detected_type_name = self.cache_manager.get_type_name_for_asset(asset_id, asset_type)
        if detected_type_name != self.cache_manager.get_asset_type_name(asset_type):
            asset['type_name'] = detected_type_name
            type_item = self.table.item(self.table.currentRow(), 4)
            if type_item is not None:
                type_item.setText(_localized_asset_type_name(asset_type, detected_type_name))
        if asset_type == 63:
            self._show_loading()
            self._preview_texturepack(data, asset_id)
            return
        is_rbx_document = is_rbx_model_data(data)
        if asset_type in {24, 39} and is_rbx_document:
            self.rbxm_view_btn.show()
        elif is_rbx_document:
            self._preview_rbxm(data, asset)
            return
        is_mesh_payload = mesh_processing.is_mesh_data(data)
        if is_mesh_payload or asset_type in {4, 39, 1, 13, 63}:
            self._show_loading()

        if is_mesh_payload or asset_type == 4:
            self._preview_mesh(data, asset_id)
        elif asset_type == 39:
            self._preview_solidmodel(data, asset_id)
        elif detected_type_name == 'Audio':
            self._preview_audio(data, asset_id)
        elif asset_type in {1, 13}:
            self._preview_image(data)
        elif asset_type == 3:
            self._preview_audio(data, asset_id)
        elif asset_type == 24:
            self._preview_animation(data, asset_id)
        elif asset_type == 74:
            self._preview_font(data)
        elif asset_type == 73:
            is_json, _ = self._is_json_data(data)
            if is_json:
                self._preview_json(data, asset)
            else:
                self._show_text_preview(tr('cache.preview.fontfamily_json_parse_failed'))
        elif is_rbx_model_data(data):
            self._preview_rbxm(data, asset)
        else:
            is_json, _ = self._is_json_data(data)
            if is_json:
                self._preview_json(data, asset)
            else:
                self._preview_hex(data, asset)

    def _on_selection_changed(self) -> None:
        """Handle table selection change to preview asset."""
        self._remember_current_rbxm_draft()
        asset = self._get_selected_asset()
        if not asset:
            self._selected_asset_id = None
            self._clear_preview()
            return

        # Track if preview was hidden before showing it
        was_hidden = self.preview_panel.isHidden()
        self.preview_panel.show()

        # Auto-snap splitter ONLY if it was previously hidden (first selection)
        if was_hidden:
            QTimer.singleShot(0, self._auto_snap_splitter)

        # Track selected asset ID for persistence across refreshes
        self._selected_asset_id = asset['id']

        # Stop all loaders first
        self._stop_all_loaders()

        # Hide all preview widgets first
        if self.obj_viewer is not None:
            self.obj_viewer.hide()
        self.image_label.hide()
        self.loading_label.hide()
        self.audio_wrapper.hide()
        if self.animation_viewer is not None:
            self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.rbxm_viewer.hide()
        self.font_wrapper.hide()
        self.rbxm_view_btn.hide()

        # Clean up texture pack widget
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

        # Stop any playing audio
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        # Remove global audio key event filter if installed
        with contextlib.suppress(Exception):
            if self._audio_key_filter_installed:
                app = cast('QApplication | None', QApplication.instance())
                if app is not None:
                    app.removeEventFilter(self)
                    self._audio_key_filter_installed = False

        # Stop animation playback
        if self.animation_viewer is not None:
            self.animation_viewer.stop()

        asset_type = asset['type']
        asset_id = asset['id']

        # Update preview group title to show resolved name or hash for clarity
        try:
            self._set_preview_title(asset_id, asset)
        except RuntimeError:
            with contextlib.suppress(RuntimeError):
                self.preview_title_label.setText(tr('ui.cache.cache_viewer.preview'))

        try:
            self._preview_selected_asset(asset, asset_type, asset_id)
        except (
            AttributeError,
            EOFError,
            ImportError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            zlib.error,
        ) as exc:
            self._show_text_preview(tr('cache.preview.asset_error', error=exc))

    def _show_context_menu(self, position: QPoint) -> None:
        """Show right-click context menu."""
        menu = QMenu(self)

        # Get selected rows
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        send_replace_action = menu.addAction(tr('ui.cache.cache_viewer.replace'))
        send_replace_with_action = menu.addAction(tr('ui.cache.cache_viewer.replace_with'))
        proxy_enabled = self._proxy_features_enabled()
        send_replace_action.setEnabled(proxy_enabled)
        send_replace_with_action.setEnabled(proxy_enabled)

        # Export submenu with format options
        export_menu = menu.addMenu(tr('cache.context.export_selected'))

        # Get selected assets and types to determine available formats
        selected_assets: list[_AssetRecord] = []
        asset_types: set[int] = set()
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    selected_assets.append(asset)
                    asset_types.add(asset['type'])

        # Determine available formats (intersection of all selected types)
        available_formats = None
        for asset in selected_assets:
            formats = set(
                self.cache_manager.get_available_export_formats_for_asset(
                    asset['id'],
                    asset['type'],
                )
            )
            if available_formats is None:
                available_formats = formats
            else:
                available_formats &= formats

        if not available_formats:
            available_formats = {'raw', 'bin'}

        modified_asset = None
        if len(selected_rows) == 1:
            row = selected_rows[0].row()
            item = self.table.item(row, 1)
            if item:
                candidate = item.data(Qt.ItemDataRole.UserRole)
                if (
                    candidate
                    and candidate.get('type') != 9
                    and self._get_modified_rbxm_draft(candidate) is not None
                ):
                    modified_asset = candidate
                    available_formats = set(available_formats)
                    available_formats.update(
                        {'converted_modified_rbxm', 'converted_modified_rbxmx'}
                    )

        # Add format options
        export_actions: dict[QAction, str] = {}
        for fmt in [
            'converted_modified_rbxm',
            'converted_modified_rbxmx',
            'converted_document_rbxl',
            'converted_document_rbxm',
            'converted_document_rbxmx',
            'slot_ktx2',
            'converted_rigged_glb',
            'converted_obj',
            'converted_rbxmx_model',
            'converted_rbxmx',
            'converted_rbxmx_curve',
            'converted_png',
            'converted_audio',
            'converted',
            'converted_images',
            'bin',
            'raw',
        ]:
            if fmt in available_formats:
                action = export_menu.addAction(_export_format_label(fmt))
                export_actions[action] = fmt

        menu.addSeparator()

        # Copy submenu
        copy_menu = menu.addMenu(tr('cache.context.copy'))
        copy_hash_action = copy_menu.addAction(tr('ui.cache.cache_viewer.hash_name'))
        copy_id_action = copy_menu.addAction(tr('ui.cache.cache_viewer.asset_id'))
        copy_url_action = copy_menu.addAction(tr('ui.cache.cache_viewer.url'))
        copy_menu.addSeparator()
        copy_creator_name_action = copy_menu.addAction(tr('ui.cache.cache_viewer.creator_name'))
        copy_creator_id_action = copy_menu.addAction(tr('ui.cache.cache_viewer.creator_id'))

        # Add "Copy Converted" if at least one selected asset supports conversion
        copy_converted_action: QAction | None = None
        if any(f.startswith('converted') for f in available_formats):
            copy_menu.addSeparator()
            copy_converted_action = copy_menu.addAction(tr('ui.cache.cache_viewer.converted_data'))

        # Add Open Creator action below the Copy menu
        open_creator_action = menu.addAction(tr('ui.cache.cache_viewer.open_creator'))

        # Export as game dump
        copy_dump_action = menu.addAction(tr('ui.cache.cache_viewer.export_as_game_dump'))

        # Convert animation rig - only when exactly one Animation is selected
        convert_anim_action: QAction | None = None
        convert_anim_asset: _AssetRecord | None = None
        target_rig: str | None = None
        if len(selected_rows) == 1 and asset_types == {24}:
            row = selected_rows[0].row()
            item = self.table.item(row, 1)
            if item:
                convert_anim_asset = cast(
                    '_AssetRecord | None', item.data(Qt.ItemDataRole.UserRole)
                )
                if convert_anim_asset:
                    with contextlib.suppress(Exception):
                        detect_rig = cast(
                            'Callable[[bytes], str]',
                            _lazy_attr('fleasion.utils.anim_converter', 'detect_rig'),
                        )

                        anim_data = self.cache_manager.get_asset(
                            convert_anim_asset['id'], convert_anim_asset['type']
                        )
                        if anim_data:
                            rig = detect_rig(anim_data)
                            target_rig = 'R6' if rig == 'R15' else 'R15'
                            target_rig_label = (
                                tr('cache.rig.r6') if target_rig == 'R6' else tr('cache.rig.r15')
                            )
                            convert_anim_action = menu.addAction(
                                tr(
                                    'ui.cache.cache_viewer.convert_to_value',
                                    value0=target_rig_label,
                                )
                            )

        menu.addSeparator()
        delete_action = menu.addAction(tr('ui.cache.cache_viewer.delete_selected'))

        # Execute menu
        action = menu.exec(self.table.viewport().mapToGlobal(position))

        if action == send_replace_action:
            self._add_selected_to_replacer()
        elif action == send_replace_with_action:
            self._add_latest_as_replace_with()
        elif action in export_actions:
            fmt_ = export_actions[action]
            if fmt_ == 'slot_ktx2':
                # Export slot KTX2 files for all selected TexturePack rows
                for row_index in selected_rows:
                    row_ = row_index.row()
                    item_ = self.table.item(row_, 1)
                    if item_:
                        asset_ = item_.data(Qt.ItemDataRole.UserRole)
                        if asset_ and asset_.get('type') == 63:
                            self._export_texpack_slot_ktx2(str(asset_['id']))
            elif fmt_ in {'converted_modified_rbxm', 'converted_modified_rbxmx'} and modified_asset:
                path = self._export_modified_rbxm_asset(modified_asset, fmt_)
                if path:
                    self._show_export_complete_message(
                        tr('cache.export.complete_title'),
                        tr('cache.export.modified_rbxm_location', location=path.parent),
                        [path],
                    )
            else:
                self._export_selected_multiple(export_format=fmt_)
        elif action == delete_action:
            self._delete_selected()
        elif action == copy_hash_action:
            self._copy_column(1)  # Hash/Name
        elif action == copy_id_action:
            self._copy_column(3)  # Asset ID (shifted by Creator col)
        elif action == copy_url_action:
            self._copy_column(_COL_KEY_TO_IDX['url'])
        elif action == copy_creator_name_action:
            self._copy_creator_info('name')
        elif action == copy_creator_id_action:
            self._copy_creator_info('id')
        elif action == open_creator_action:
            self._open_creator_in_browser()
        elif copy_converted_action is not None and action == copy_converted_action:
            self._copy_converted()
        elif action == copy_dump_action:
            self._export_as_game_dump()
        elif (
            action == convert_anim_action
            and convert_anim_asset is not None
            and target_rig is not None
        ):
            self._convert_animation_rig(convert_anim_asset, target_rig)

    def _convert_animation_rig(self, asset: _AssetRecord, target: str) -> None:
        """Convert an animation between R6 and R15 and save to a user-chosen path."""

        asset_id = asset.get('id', 'animation')
        default_name = f'{asset_id}_{target.lower()}.rbxmx'
        out_str, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.cache.cache_viewer.save_converted_animation'),
            default_name,
            tr('ui.cache.cache_viewer.roblox_animation_rbxmx_all_files'),
        )
        if not out_str:
            return

        def _do_convert() -> None:
            try:
                rbxm_to_rbxmx = cast(
                    'Callable[[bytes], bytes]',
                    _lazy_attr('fleasion.utils.anim_converter', 'rbxm_to_rbxmx'),
                )
                r6_joints = cast('JointMap', _lazy_attr('fleasion.utils.rig_data', 'R6_JOINTS'))
                r6_parts = cast('PartMap', _lazy_attr('fleasion.utils.rig_data', 'R6_PARTS'))
                r15_joints = cast('JointMap', _lazy_attr('fleasion.utils.rig_data', 'R15_JOINTS'))
                r15_parts = cast('PartMap', _lazy_attr('fleasion.utils.rig_data', 'R15_PARTS'))
            except (AttributeError, ImportError) as exc:
                QMessageBox.warning(
                    self,
                    tr('ui.cache.cache_viewer.convert_error'),
                    tr('ui.cache.cache_viewer.failed_to_load_converter_value', value0=exc),
                )
                return

            anim_data = self.cache_manager.get_asset(asset['id'], asset['type'])
            if not anim_data:
                QMessageBox.warning(
                    self,
                    tr('ui.cache.cache_viewer.convert_error'),
                    tr('ui.cache.cache_viewer.could_not_load_asset_data'),
                )
                return

            if anim_data.startswith(b'<roblox!'):
                try:
                    anim_data = rbxm_to_rbxmx(anim_data)
                except (
                    EOFError,
                    OSError,
                    RuntimeError,
                    SyntaxError,
                    TypeError,
                    ValueError,
                    zlib.error,
                ) as exc:
                    QMessageBox.warning(
                        self,
                        tr('ui.cache.cache_viewer.convert_error'),
                        tr('ui.cache.cache_viewer.rbxm_conversion_failed_value', value0=exc),
                    )
                    return

            def _write_converted_xml() -> None:
                convert_keyframe_r6_to_r15 = cast(
                    'Callable[[object, PartMap, JointMap, PartMap, JointMap], None]',
                    _lazy_attr('fleasion.utils.r15_to_r6', 'convert_keyframe_r6_to_r15'),
                )
                convert_keyframe_r15_to_r6 = cast(
                    'Callable[[object, PartMap, JointMap, PartMap, JointMap], None]',
                    _lazy_attr('fleasion.utils.r15_to_r6', 'convert_keyframe_r15_to_r6'),
                )
                sanitize_xml = cast(
                    'Callable[[bytes], str]',
                    _lazy_attr('fleasion.utils.r15_to_r6', 'sanitize_xml'),
                )
                root = DefusedElementTree.fromstring(sanitize_xml(anim_data))
                ks = root.find("Item[@class='KeyframeSequence']")
                if ks is None:
                    QMessageBox.warning(
                        self,
                        tr('ui.cache.cache_viewer.convert_error'),
                        tr('ui.cache.cache_viewer.no_keyframesequence_found'),
                    )
                    return
                keyframes = ks.findall("Item[@class='Keyframe']")
                if not keyframes:
                    QMessageBox.warning(
                        self,
                        tr('ui.cache.cache_viewer.convert_error'),
                        tr('ui.cache.cache_viewer.no_keyframes_found'),
                    )
                    return
                converter = (
                    convert_keyframe_r15_to_r6 if target == 'R6' else convert_keyframe_r6_to_r15
                )
                for keyframe in keyframes:
                    converter(keyframe, r6_parts, r6_joints, r15_parts, r15_joints)
                Path(out_str).write_bytes(
                    DefusedElementTree.tostring(root, encoding='utf-8', xml_declaration=True)
                )

            try:
                _write_converted_xml()
            except (
                AttributeError,
                ImportError,
                OSError,
                RuntimeError,
                SyntaxError,
                TypeError,
                ValueError,
            ) as exc:
                QMessageBox.warning(
                    self,
                    tr('ui.cache.cache_viewer.convert_error'),
                    tr('ui.cache.cache_viewer.conversion_failed_value', value0=exc),
                )

        threading.Thread(target=_do_convert, daemon=True).start()

    def _export_as_game_dump(self) -> None:
        """Export selected assets as a game dump JSON grouped by type.

        Produces {TypeName: {AssetName: assetId, ...}, ...} — the same format
        that the PreJsons Browser accepts when importing a custom dump.
        """
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        by_type: dict[str, dict[str, int]] = {}
        name_counts: dict[tuple[str, str], int] = {}

        for idx in sorted(selected_rows, key=lambda x: x.row()):
            row = idx.row()
            item = self.table.item(row, 1)  # col 1 carries the asset dict in UserRole
            if not item:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if not isinstance(asset, dict):
                continue
            asset_record = cast('_AssetRecord', asset)

            raw_asset_id = asset_record.get('id') or asset_record.get('asset_id')
            try:
                asset_id = int(cast('str | int', raw_asset_id))
            except TypeError, ValueError:
                continue

            name = item.text() or 'Unknown'
            type_name = self.cache_manager.get_type_name_for_asset(
                cast('str', asset_id),
                cast('int', cast('object', asset_record.get('type', ''))),
            )

            bucket = by_type.setdefault(type_name, {})
            key = name
            count_key = (type_name, name)
            if key in bucket:
                name_counts[count_key] = name_counts.get(count_key, 1) + 1
                key = f'{name} ({name_counts[count_key]})'
            bucket[key] = asset_id

        if not by_type:
            return

        result = {t: by_type[t] for t in sorted(by_type)}

        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.cache.cache_viewer.export_game_dump'),
            'game_dump.json',
            tr('ui.cache.cache_viewer.json_files_json_all_files'),
        )
        if not path:
            return
        Path(path).write_text(json.dumps(result, indent=2), encoding='utf-8')
        total = sum(len(v) for v in result.values())
        log_buffer.log('Scraper', f'Exported game dump ({total} assets) to {path}')

        self._show_export_complete_message(
            tr('cache.export.complete_title'),
            tr(
                'cache.export.game_dump_one_asset'
                if total == 1
                else 'cache.export.game_dump_assets',
                count=total,
                path=path,
            ),
            [Path(path)],
        )

    def _copy_column(self, column: int) -> None:
        """Copy column contents for selected rows."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        values: list[str] = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, column)
            if item:
                values.append(item.text())

        if values:
            clipboard = QApplication.clipboard()
            clipboard.setText('\n'.join(values))
            log_buffer.log('Scraper', f'Copied {format_count(values, "value")} to clipboard')

    def _copy_creator_info(self, mode: str) -> None:
        """Copy creator name or creator ID for selected rows.

        Args:
            mode: 'name' to copy creator display name, 'id' to copy creator ID.
        """

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        values: list[str] = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)  # Hash/Name carries UserRole asset data
            if not item:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if not asset:
                continue
            info = self._asset_info.get(asset['id'])
            if not info:
                continue
            if mode == 'name':
                val = info.get('creator_name') or ''
            else:
                val = str(info.get('creator_id') or '')
            if val:
                values.append(val)

        if values:
            QApplication.clipboard().setText('\n'.join(values))
            label = 'creator ID' if mode == 'id' else f'creator {mode}'
            log_buffer.log('Scraper', f'Copied {format_count(values, label)} to clipboard')

    def _open_creator_in_browser(self) -> None:
        """Open the creator pages for the selected assets in the default browser."""

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        opened = 0
        seen: set[tuple[str, str]] = set()
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if not item:
                continue
            asset = item.data(Qt.ItemDataRole.UserRole)
            if not asset:
                continue
            info = self._asset_info.get(asset['id'])
            if not info:
                continue
            creator_id = info.get('creator_id')
            creator_type_val = info.get('creator_type')
            if isinstance(creator_type_val, int):
                is_group = creator_type_val == 2
            else:
                creator_type_text = str(creator_type_val).lower()
                is_group = 'group' in creator_type_text or 'community' in creator_type_text
            if not creator_id:
                continue
            key = (('group' if is_group else 'user'), str(creator_id))
            if key in seen:
                continue
            seen.add(key)
            url = (
                f'https://www.roblox.com/communities/{creator_id}'
                if is_group
                else f'https://www.roblox.com/users/{creator_id}'
            )
            try:
                webbrowser.open(url)
            except OSError, webbrowser.Error:
                log_buffer.log('Scraper', f'Failed to open creator {creator_id} in browser')
            else:
                opened += 1

        if opened:
            log_buffer.log('Scraper', f'Opened {format_count(opened, "creator page")} in browser')

    def _selected_asset_for_copy(self) -> _AssetRecord | None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        item = self.table.item(selected_rows[0].row(), 1)
        if item is None:
            return None
        asset = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(asset, dict):
            return None
        return cast('_AssetRecord', asset)

    def _copy_mesh_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        try:
            obj_content = mesh_processing.convert(data)
            if not obj_content:
                QMessageBox.warning(
                    self,
                    tr('ui.cache.cache_viewer.error'),
                    tr('ui.cache.cache_viewer.failed_to_convert_mesh_to_obj'),
                )
                return None
            temp_file = temp_dir / f'{safe_base}.obj'
            temp_file.write_text(obj_content, encoding='utf-8')
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.mesh_conversion_error_value', value0=exc),
            )
            return None
        return temp_file

    @staticmethod
    def _image_export_data(data: bytes) -> bytes:
        if data[:12] not in {
            b'\xabKTX 11\xbb\r\n\x1a\n',
            b'\xabKTX 20\xbb\r\n\x1a\n',
        }:
            return data
        ktx_convert = cast(
            'Callable[[bytes], bytes | None]',
            _lazy_attr('.tools.ktx_to_png', 'convert'),
        )
        return ktx_convert(data) or data

    def _copy_image_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        temp_file = temp_dir / f'{safe_base}.png'
        try:
            export_data = self._image_export_data(data)
            temp_file.write_bytes(export_data)
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.image_save_error_value', value0=exc),
            )
            return None
        return temp_file

    def _copy_audio_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        ext = 'mp3' if data.startswith((b'ID3', b'\xff\xfb')) else 'ogg'
        temp_file = temp_dir / f'{safe_base}.{ext}'
        try:
            temp_file.write_bytes(data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.audio_save_error_value', value0=exc),
            )
            return None
        return temp_file

    def _copy_animation_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        temp_file = temp_dir / f'{safe_base}.rbxmx'
        try:
            export_data = gzip_module.decompress(data) if data.startswith(b'\x1f\x8b') else data
            temp_file.write_bytes(export_data)
        except (EOFError, OSError, zlib.error) as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.animation_save_error_value', value0=exc),
            )
            return None
        return temp_file

    def _copy_texturepack_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        temp_file = temp_dir / f'{safe_base}_texturepack.xml'
        try:
            temp_file.write_bytes(data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.texturepack_save_error_value', value0=exc),
            )
            return None
        return temp_file

    @staticmethod
    def _solidmodel_obj_content(data: bytes) -> str:
        deserialize_rbxm = cast(
            'Callable[[bytes], object]',
            _lazy_attr('.tools.solidmodel_converter.converter', 'deserialize_rbxm'),
        )
        export_obj_from_doc = cast(
            '_ExportObjCallable',
            _lazy_attr('.tools.solidmodel_converter.converter', 'export_obj_from_doc'),
        )
        decompressed = gzip_module.decompress(data) if data.startswith(b'\x1f\x8b') else data
        doc = deserialize_rbxm(decompressed)
        with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as handle:
            temp_obj_path = Path(handle.name)
        try:
            export_obj_from_doc(doc, temp_obj_path, decompose=False)
            return temp_obj_path.read_text(encoding='utf-8')
        finally:
            with contextlib.suppress(OSError):
                temp_obj_path.unlink(missing_ok=True)

    def _copy_solidmodel_temp(self, data: bytes, temp_dir: Path, safe_base: str) -> Path | None:
        temp_file = temp_dir / f'{safe_base}.obj'

        def copy_temp() -> Path:
            obj_content = self._solidmodel_obj_content(data)
            temp_file.write_text(obj_content, encoding='utf-8')
            return temp_file

        return _ui_boundary(
            copy_temp,
            fallback=None,
            on_error=lambda exc: QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.solidmodel_conversion_error_value', value0=exc),
            ),
        )

    @staticmethod
    def _document_export_payload(data: bytes, asset_type: int) -> tuple[bytes, str] | None:
        formats = get_roblox_document_export_formats(data, asset_type=asset_type)
        if not formats:
            return None
        export_format = (
            'converted_document_rbxl'
            if 'converted_document_rbxl' in formats
            else 'converted_document_rbxmx'
        )
        return export_roblox_document(data, export_format, asset_type=asset_type)

    def _copy_document_temp(
        self,
        data: bytes,
        asset_type: int,
        temp_dir: Path,
        safe_base: str,
    ) -> Path | None:
        payload = _ui_boundary(
            lambda: self._document_export_payload(data, asset_type),
            fallback=None,
            on_error=lambda exc: QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.roblox_document_save_error_value', value0=exc),
            ),
        )
        if payload is None:
            return None
        export_data, ext = payload
        temp_file = temp_dir / f'{safe_base}{ext}'
        try:
            temp_file.write_bytes(export_data)
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.roblox_document_save_error_value', value0=exc),
            )
            return None
        return temp_file

    def _converted_temp_file(
        self,
        data: bytes,
        asset_type: int,
        temp_dir: Path,
        safe_base: str,
    ) -> Path | None:
        if mesh_processing.is_mesh_data(data):
            temp_file = self._copy_mesh_temp(data, temp_dir, safe_base)
        elif asset_type in {1, 13}:
            temp_file = self._copy_image_temp(data, temp_dir, safe_base)
        elif asset_type == 3:
            temp_file = self._copy_audio_temp(data, temp_dir, safe_base)
        elif asset_type == 24:
            temp_file = self._copy_animation_temp(data, temp_dir, safe_base)
        elif asset_type == 63:
            temp_file = self._copy_texturepack_temp(data, temp_dir, safe_base)
        elif asset_type == 39:
            temp_file = self._copy_solidmodel_temp(data, temp_dir, safe_base)
        else:
            temp_file = self._copy_document_temp(data, asset_type, temp_dir, safe_base)
        return temp_file

    def _copy_converted_impl(self, asset: _AssetRecord) -> Path | None:
        asset_id = asset['id']
        asset_type = asset['type']
        data = self.cache_manager.get_asset(asset_id, asset_type)
        if not data:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.failed_to_load_asset_value', value0=asset_id),
            )
            return None
        temp_dir = Path(tempfile.gettempdir()) / 'fleasion_clipboard'
        temp_dir.mkdir(exist_ok=True)
        resolved_name = self._asset_info.get(asset_id, {}).get('resolved_name')
        safe_base = self._sanitize_filename(resolved_name or asset_id)
        temp_file = self._converted_temp_file(data, asset_type, temp_dir, safe_base)
        if temp_file is None or not temp_file.exists():
            return None
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(str(temp_file))])
        QApplication.clipboard().setMimeData(mime_data)
        return temp_file

    def _copy_converted(self) -> None:
        """Copy the selected asset's converted file to the clipboard."""
        asset = self._selected_asset_for_copy()
        if asset is None:
            return
        try:
            temp_file = self._copy_converted_impl(asset)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.error'),
                tr('ui.cache.cache_viewer.copy_error_value', value0=exc),
            )
            return
        if temp_file is None:
            return
        log_buffer.log('Scraper', f'Copied file to clipboard: {temp_file.name}')
        QMessageBox.information(
            self,
            tr('ui.cache.cache_viewer.success'),
            tr(
                'ui.cache.cache_viewer.file_copied_to_clipboard_value_you_can',
                value0=temp_file.name,
            ),
        )

    def _export_selected_multiple(self, export_format: str = 'converted') -> None:
        """Export multiple selected assets."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.no_selection'),
                tr('ui.cache.cache_viewer.please_select_assets_to_export'),
            )
            return

        # Collect assets to export
        assets_to_export: list[_AssetRecord] = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 1)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    assets_to_export.append(asset)

        if not assets_to_export:
            return

        # Export all with sanitized resolved names
        exported_count = 0
        exported_paths: list[Path] = []
        for asset in assets_to_export:
            asset_id = asset['id']
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')
            safe_name = self._sanitize_filename(resolved_name) if resolved_name else None

            export_path = self.cache_manager.export_asset(
                asset['id'],
                asset['type'],
                resolved_name=safe_name,
                export_format=export_format,
            )
            if export_path:
                exported_count += 1
                exported_paths.append(export_path)

        log_buffer.log(
            'Scraper',
            f'Exported {exported_count}/{len(assets_to_export)} assets as {export_format}',
        )
        location, _ = self._get_export_open_target(exported_paths)
        self._show_export_complete_message(
            tr('cache.export.complete_title'),
            tr(
                'cache.export.one_asset_as_format'
                if exported_count == 1
                else 'cache.export.assets_as_format',
                count=exported_count,
                export_format=_export_format_label(export_format),
                location=location,
            ),
            exported_paths,
        )

    @staticmethod
    def _rbxm_asset_key(asset: _AssetRecord) -> tuple[str, object]:
        return (str(asset.get('id', '')), asset.get('type'))

    @staticmethod
    def _rbxm_asset_cached_at(asset: _AssetRecord) -> str:
        return str(asset.get('cached_at') or '')

    def _remember_current_rbxm_draft(self) -> None:
        key = getattr(self, '_rbxm_preview_asset_key', None)
        if key is None or not hasattr(self, 'rbxm_viewer'):
            return
        document = getattr(self.rbxm_viewer, 'document', None)
        if document is None or not self.rbxm_viewer.is_modified():
            return
        self._modified_rbxm_drafts[key] = {
            'cached_at': getattr(self, '_rbxm_preview_cached_at', ''),
            'document': document,
        }

    def _get_modified_rbxm_draft(self, asset: _AssetRecord) -> PreviewDocument | None:
        self._remember_current_rbxm_draft()
        key = self._rbxm_asset_key(asset)
        draft = self._modified_rbxm_drafts.get(key)
        if not draft:
            return None
        cached_at = self._rbxm_asset_cached_at(asset)
        if draft.get('cached_at', '') != cached_at:
            self._modified_rbxm_drafts.pop(key, None)
            if key == getattr(self, '_rbxm_preview_asset_key', None):
                self._rbxm_preview_asset_key = None
                self._rbxm_preview_cached_at = ''
                with contextlib.suppress(Exception):
                    _set_rbxm_dirty(self.rbxm_viewer, dirty=False)
            return None
        return draft.get('document')

    def _export_modified_rbxm_asset_impl(
        self,
        asset: _AssetRecord,
        export_format: str,
    ) -> Path | None:
        asset_id = str(asset.get('id', ''))
        asset_type = cast('int | str | None', cast('object', asset.get('type')))
        draft_document = self._get_modified_rbxm_draft(asset)
        if draft_document is None:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.no_changes'),
                tr('ui.cache.cache_viewer.the_selected_asset_has_no_in_memory'),
            )
            return None

        type_name = (
            self.cache_manager.get_asset_type_name(asset_type)
            if isinstance(asset_type, int)
            else 'RBXM'
        )
        export_dir = self.cache_manager.export_dir / 'converted' / type_name
        export_dir.mkdir(parents=True, exist_ok=True)
        resolved_name = (
            self._asset_info[asset_id].get('resolved_name')
            if asset_id in self._asset_info
            else None
        )
        base_name = self._sanitize_filename(resolved_name) if resolved_name else asset_id
        suffix = '_MODIFIED'
        if export_format == 'converted_modified_rbxm':
            data = self.rbxm_viewer.export_rbxm_bytes(draft_document)
            output_path = export_dir / f'{base_name}{suffix}.rbxm'
        else:
            data = self.rbxm_viewer.export_rbxmx_bytes(draft_document)
            output_path = export_dir / f'{base_name}{suffix}.rbxmx'
        output_path.write_bytes(data)
        log_buffer.log('Scraper', f'Exported modified RBXM/RBXMX to {output_path}')
        return output_path

    def _export_modified_rbxm_asset(self, asset: _AssetRecord, export_format: str) -> Path | None:
        try:
            return self._export_modified_rbxm_asset_impl(asset, export_format)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.cache.cache_viewer.export_error'),
                tr('ui.cache.cache_viewer.failed_to_export_modified_rbxm_rbxmx_value', value0=exc),
            )
            log_buffer.log('Scraper', f'Failed to export modified RBXM/RBXMX: {exc}')
            return None

    def _add_selected_to_replacer(self) -> None:
        """Add selected asset IDs to replacer."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        asset_ids: list[str] = []
        for row_index in selected_rows:
            row = row_index.row()
            # Asset ID is in column 3 (columns: 0 marker, 1 Hash/Name, 2 Creator, 3 Asset ID)
            id_item = self.table.item(row, 3)
            if id_item:
                asset_ids.append(id_item.text())

        if not asset_ids:
            return

        replacer_window = getattr(self, '_replacer_window_ref', None)
        if replacer_window:
            # Add to existing IDs if there are any
            current_text = replacer_window.replace_entry.text().strip()
            if current_text:
                new_text = current_text + ', ' + ', '.join(asset_ids)
            else:
                new_text = ', '.join(asset_ids)
            replacer_window.replace_entry.setText(new_text)

            log_buffer.log('Scraper', f'Added {format_count(asset_ids, "asset ID")} to replacer')
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.added_to_replacer'),
                tr(
                    'ui.cache.cache_viewer.added_value_to_replacer_value_value',
                    value0=tr_count(asset_ids, 'count.asset_id.one', 'count.asset_id.other'),
                    value1=', '.join(asset_ids[:5]),
                    value2='...' if len(asset_ids) > 5 else '',
                ),
            )
        else:
            # Fallback: copy to clipboard if not in replacer window

            clipboard = QApplication.clipboard()
            clipboard.setText(', '.join(asset_ids))

            log_buffer.log('Scraper', f'Copied {format_count(asset_ids, "asset ID")} to clipboard')
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.copied_to_clipboard'),
                tr(
                    'ui.cache.cache_viewer.copied_value_to_clipboard_value_value',
                    value0=tr_count(asset_ids, 'count.asset_id.one', 'count.asset_id.other'),
                    value1=', '.join(asset_ids[:5]),
                    value2='...' if len(asset_ids) > 5 else '',
                ),
            )

    def _add_latest_as_replace_with(self) -> None:
        """Send the latest selected asset ID to the Replace With field."""
        row = self.table.currentIndex().row()
        if row < 0:
            return

        id_item = self.table.item(row, 3)
        if not id_item:
            return
        asset_id = id_item.text()

        replacer_window = getattr(self, '_replacer_window_ref', None)
        if replacer_window:
            replacer_window.replacement_entry.setText(asset_id)
            log_buffer.log('Scraper', f'Set Replace With to asset ID {asset_id}')
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.replace_with_set'),
                tr('ui.cache.cache_viewer.replace_with_set_to_asset_id_value', value0=asset_id),
            )
        else:
            QApplication.clipboard().setText(asset_id)
            log_buffer.log(
                'Scraper',
                f'Copied asset ID {asset_id} to clipboard (no replacer window found)',
            )
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.copied_to_clipboard'),
                tr('ui.cache.cache_viewer.copied_asset_id_value_to_clipboard', value0=asset_id),
            )

    def _stop_preview(self) -> None:
        """Stop current preview and hide button."""
        self._selected_asset_id = None
        self._clear_preview()
        self.stop_preview_btn.hide()
        self.table.clearSelection()
        self.table.setCurrentItem(cast('QTableWidgetItem', None))
        # Show default preview message
        self.image_label.setText(tr('ui.cache.cache_viewer.select_an_asset_to_preview'))
        self.image_label.show()

    def _clear_preview(self) -> None:
        """Clear all preview widgets and stop any running loaders."""
        self._remember_current_rbxm_draft()

        # Stop all worker threads first
        self._stop_all_loaders()

        # Hide and clear UI widgets
        if self.obj_viewer is not None:
            self.obj_viewer.hide()
            self.obj_viewer.clear()
        self.image_label.clear()

        # Completely hide the preview window as requested
        self.preview_panel.hide()
        # Reset preview group title back to default
        with contextlib.suppress(Exception):
            if hasattr(self, 'preview_group'):
                self.preview_title_label.setText(tr('ui.cache.cache_viewer.preview'))

        # Deselect currently tracked asset in tree/internal state
        self._selected_asset_id = None
        self.table.clearSelection()
        self.table.setCurrentItem(cast('QTableWidgetItem', None))

        self._current_pixmap: QPixmap | None = None
        self.audio_wrapper.hide()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        if self.animation_viewer is not None:
            self.animation_viewer.hide()
            self.animation_viewer.clear()
        self.text_viewer.hide()
        self.text_viewer.clear()
        self.rbxm_viewer.hide()
        self.rbxm_viewer.clear()
        self._rbxm_preview_asset_key = None
        self._rbxm_preview_cached_at = ''
        self.rbxm_view_btn.hide()

        # Clean up texture pack widgets
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

    def _stop_all_loaders(self) -> None:
        """Stop all running preview loader threads."""
        if self._image_loader is not None:
            self._image_loader.stop()
            self._image_loader.quit()
            self._image_loader.wait()
            self._image_loader = None

        if self._mesh_loader is not None:
            self._mesh_loader.stop()
            self._mesh_loader.quit()
            self._mesh_loader.wait()
            self._mesh_loader = None

        if self._animation_loader is not None:
            self._animation_loader.stop()
            self._animation_loader.quit()
            self._animation_loader.wait()
            self._animation_loader = None

        if self._texturepack_loader is not None:
            self._texturepack_loader.stop()
            self._texturepack_loader.quit()
            self._texturepack_loader.wait()
            self._texturepack_loader = None

    @override
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Global event filter to catch space key and toggle audio play/pause."""
        with contextlib.suppress(Exception):
            if event.type() == QEvent.Type.KeyPress:
                # Space toggles play/pause when audio preview is active
                key_event = cast('QKeyEvent', event)
                if key_event.key() == Qt.Key.Key_Space:
                    focus_widget = QApplication.focusWidget()
                    if isinstance(focus_widget, (QLineEdit, QTextEdit, QPlainTextEdit)):
                        return super().eventFilter(obj, event)

                    if self.audio_player and self.audio_wrapper.isVisible():
                        with contextlib.suppress(Exception):
                            # Toggle play/pause on the audio widget
                            _toggle_audio_play_pause(self.audio_player)
                        return True
        return super().eventFilter(obj, event)

    def _remove_audio_key_filter(self) -> None:
        """Remove global audio key event filter if installed."""
        with contextlib.suppress(Exception):
            if self._audio_key_filter_installed:
                app = cast('QApplication | None', QApplication.instance())
                if app is not None:
                    app.removeEventFilter(self)
                    self._audio_key_filter_installed = False

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        """Handle splitter resize to rescale image."""
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def _show_loading(self) -> None:
        """Show loading indicator."""
        self.loading_label.show()

    def _hide_loading(self) -> None:
        """Hide loading indicator."""
        self.loading_label.hide()

    def _swap_to_rbxm_view(self) -> None:
        """Switch the current Animation/SolidModel preview to raw RBXM structure."""
        asset = self._get_selected_asset()
        if not asset:
            return
        asset_type = asset.get('type')
        if asset_type not in {24, 39}:
            return
        asset_id = asset.get('id')
        data = self.cache_manager.get_asset(asset_id, asset_type)
        if not data or not is_rbx_model_data(data):
            return

        self._stop_all_loaders()
        if self.obj_viewer is not None:
            self.obj_viewer.hide()
        self.image_label.hide()
        self.loading_label.hide()
        self.audio_wrapper.hide()
        if self.animation_viewer is not None:
            self.animation_viewer.hide()
            self.animation_viewer.stop()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.font_wrapper.hide()
        self.rbxm_view_btn.hide()

        title = (
            tr('cache.preview.animation_structure')
            if asset_type == 24
            else tr('cache.preview.solidmodel_structure')
        )
        self._preview_rbxm(data, asset, title_prefix=title)

    def _preview_mesh(self, data: bytes, asset_id: str) -> None:
        """Preview a mesh asset in 3D using background thread."""
        # Track which asset this loader is for so we can ignore stale results
        self._mesh_loader_asset_id = asset_id
        self._mesh_loader = MeshLoaderThread(data, asset_id)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)

        def on_mesh_error(error: str) -> None:
            self._show_text_preview(tr('cache.preview.mesh_error', error=error))

        self._mesh_loader.error.connect(on_mesh_error)
        self._mesh_loader.start()

    def _on_mesh_ready(self, obj_content: str) -> None:
        """Handle mesh loaded from background thread."""
        # Ignore if selection has changed since loader started
        with contextlib.suppress(Exception):
            if getattr(self, '_mesh_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale mesh result ignored')
                return

        self._hide_loading()
        try:
            obj_viewer = self._ensure_obj_viewer()
            obj_viewer.load_obj(obj_content, '')
            obj_viewer.show()
            self.stop_preview_btn.show()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log_buffer.log(
                'OpenGL',
                f'Could not create/load OBJ preview: {type(exc).__name__}: {exc}',
            )
            self._show_text_preview(tr('cache.preview.mesh_error', error=exc))

    def _preview_solidmodel(self, data: bytes, asset_id: str) -> None:
        """Preview a SolidModel asset in 3D using background thread."""
        # Track which asset this loader is for so we can ignore stale results
        self._mesh_loader_asset_id = asset_id
        self._mesh_loader = SolidModelLoaderThread(data, asset_id)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)

        def on_solidmodel_error(error: str, data_: bytes = data, asset_id_: str = asset_id) -> None:
            self._on_solidmodel_preview_error(data_, asset_id_, error)

        self._mesh_loader.error.connect(on_solidmodel_error)
        self._mesh_loader.start()

    def _on_solidmodel_preview_error(self, data: bytes, asset_id: str, error: str) -> None:
        """Fallback from 3D SolidModel preview to the RBXM structure viewer."""
        if getattr(self, '_selected_asset_id', None) != asset_id:
            log_buffer.log('Preview', 'Stale SolidModel error ignored')
            return
        if is_rbx_model_data(data):
            self._preview_rbxm(
                data,
                {'id': asset_id, 'type': 39, 'type_name': 'SolidModel'},
                title_prefix=tr('cache.preview.solidmodel_structure'),
            )
            return
        self._show_text_preview(tr('cache.preview.solidmodel_error', error=error))

    def _preview_image(self, data: bytes) -> None:
        """Preview an image asset using background thread."""
        # Track current selection so we ignore stale image results
        self._image_loader_asset_id = getattr(self, '_selected_asset_id', None)
        self._image_loader = ImageLoaderThread(data)
        self._image_loader.image_ready.connect(self._on_image_ready)

        def on_image_error(error: str) -> None:
            self._show_text_preview(tr('cache.preview.image_error', error=error))

        self._image_loader.error.connect(on_image_error)
        self._image_loader.start()

    def _on_image_ready(self, pixmap: QPixmap) -> None:
        """Handle image loaded from background thread."""
        # Ignore if selection has changed since loader started
        with contextlib.suppress(Exception):
            if getattr(self, '_image_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale image result ignored')
                return

        self._hide_loading()
        self._current_pixmap = pixmap
        self._scale_and_show_image(pixmap)
        self.image_label.show()
        self.stop_preview_btn.show()

    def _scale_and_show_image(self, pixmap: QPixmap) -> None:
        """Scale pixmap to fit container and display it."""
        container_width = self.preview_scroll.viewport().width() - 20
        container_height = self.preview_scroll.viewport().height() - 20

        if container_width < 100:
            container_width = 400
        if container_height < 100:
            container_height = 400

        # Scale to fit within container while maintaining aspect ratio
        scaled = pixmap.scaled(
            container_width,
            container_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

        self.image_label.setPixmap(scaled)

    def _show_image_context_menu(self, pos: QPoint) -> None:
        """Show context menu for image preview."""
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        menu = QMenu(self)
        copy_action = menu.addAction(tr('ui.cache.cache_viewer.copy_image'))

        action = menu.exec(self.image_label.mapToGlobal(pos))
        if action == copy_action:
            copy_pixmap_to_clipboard(self._current_pixmap)

    def _preview_texturepack_impl(self, data: bytes, asset_id: str) -> None:
        # Clean up previous texture pack if any
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None
        if self._texturepack_loader is not None:
            self._texturepack_loader.stop()
            self._texturepack_loader.quit()
            self._texturepack_loader.wait()
            self._texturepack_loader = None

        # Parse XML to get texture map IDs
        xml_text = data.decode('utf-8', errors='replace')
        self._texturepack_xml = xml_text  # Store for context menu
        root = DefusedElementTree.fromstring(xml_text)

        # Extract texture map IDs using the GLOBAL fixed index system.
        # The index for each map type is constant regardless of the asset's
        # XML tag ordering:
        #   0 = Color, 1 = Normal, 2 = Metalness, 3 = Roughness,
        #   4 = Emissive, 5 = Height
        # These global indices are what the user puts in replace_ids
        # (e.g. "108049038086346:3" for roughness on ANY pack).
        # GI ≥ 2 are routed through the ORM compositor on the backend.
        tag_to_global_index = {
            'color': 0,
            'albedo': 0,
            'diffuse': 0,
            'basecolor': 0,
            'normal': 1,
            'normalmap': 1,
            'bumpmap': 1,
            'metalness': 2,
            'orm': 2,
            'roughness': 3,
            'emissive': 4,
            'emissivemap': 4,
            'height': 5,
            'heightmap': 5,
            'displacement': 5,
        }
        map_display_names = {
            'color': tr('cache.texture_map.color'),
            'albedo': tr('cache.texture_map.albedo'),
            'diffuse': tr('cache.texture_map.diffuse'),
            'basecolor': tr('cache.texture_map.basecolor'),
            'normal': tr('cache.texture_map.normal'),
            'normalmap': tr('cache.texture_map.normalmap'),
            'bumpmap': tr('cache.texture_map.bumpmap'),
            'metalness': tr('cache.texture_map.metalness'),
            'orm': tr('cache.texture_map.orm'),
            'roughness': tr('cache.texture_map.roughness'),
            'emissive': tr('cache.texture_map.emissive'),
            'emissivemap': tr('cache.texture_map.emissivemap'),
            'height': tr('cache.texture_map.height'),
            'heightmap': tr('cache.texture_map.heightmap'),
            'displacement': tr('cache.texture_map.displacement'),
        }
        maps: dict[str, str] = {}  # display_name -> map_id_str
        maps_indices: dict[str, int] = {}  # display_name -> global_index (fixed, asset-independent)
        for child in root:
            tag_lower = child.tag.lower().lstrip('{').split('}')[-1]
            global_idx = tag_to_global_index.get(tag_lower)
            if global_idx is None:
                continue
            text = (child.text or '').strip()
            if text.isdigit() and text != '0':
                display_name = map_display_names[tag_lower]
                maps[display_name] = text
                maps_indices[display_name] = global_idx

        if not maps:
            self._show_text_preview(tr('cache.preview.texturepack_no_maps', asset_id=asset_id))
            return

        # Clear texture data storage
        self._texturepack_data = {}

        # Create container widget for texture pack preview
        self.texturepack_widget = QWidget()
        tp_layout = QVBoxLayout()
        tp_layout.setContentsMargins(0, 0, 0, 0)
        tp_layout.setSpacing(10)

        # Store references for async loading
        self._tp_image_labels: dict[str, QLabel] = {}
        self._tp_pixmaps: dict[str, QPixmap] = {}  # Store pixmaps for copy

        # Create placeholder for each texture map
        for map_name, map_id in maps.items():
            map_index = maps_indices.get(map_name, '?')
            slot_key = f'{asset_id}:{map_index}'
            # Header: Name  |  sub-asset ID  |  slot X  (slot X is what goes in replace_ids)
            header = QLabel(
                tr(
                    'ui.cache.cache_viewer.value_value_value',
                    value0=map_name,
                    value1=asset_id,
                    value2=map_index,
                )
            )
            header.setStyleSheet('font-weight: bold; color: #888; padding: 5px;')
            header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tp_layout.addWidget(header)

            # Image placeholder with context menu
            img_label = QLabel(tr('ui.cache.cache_viewer.loading'))
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_label.setStyleSheet(
                'background-color: palette(base); padding: 10px; min-height: 100px;'
            )
            img_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            img_label.setProperty('map_name', map_name)
            img_label.setProperty('map_id', map_id)
            img_label.setProperty('map_index', map_index)
            img_label.setProperty('slot_key', slot_key)

            def on_context_menu(pos: QPoint, label_: QLabel = img_label) -> None:
                self._show_texturepack_context_menu(pos, label_)

            img_label.customContextMenuRequested.connect(on_context_menu)
            tp_layout.addWidget(img_label)
            self._tp_image_labels[map_name] = img_label

        tp_layout.addStretch()
        self.texturepack_widget.setLayout(tp_layout)
        self.preview_container_layout.addWidget(self.texturepack_widget)
        self.texturepack_widget.show()
        self.stop_preview_btn.show()

        # Start async loading of textures
        self._texturepack_loader = TexturePackLoaderThread(
            cast('dict[str, str | int]', maps),
            self.cache_manager,
            self.cache_scraper,
        )
        self._texturepack_loader.texture_loaded.connect(self._on_texturepack_texture_loaded)
        self._texturepack_loader.texture_error.connect(self._on_texturepack_texture_error)
        self._texturepack_loader.start()

    def _preview_texturepack(self, data: bytes, asset_id: str) -> None:
        """Preview a texture pack by showing all texture maps."""
        try:
            self._preview_texturepack_impl(data, asset_id)
        except (OSError, RuntimeError, SyntaxError, TypeError, ValueError) as exc:
            self._show_text_preview(tr('cache.preview.texturepack_error', error=exc))

    def _on_texturepack_texture_loaded_impl(
        self, map_name: str, map_id: str, hash_val: str, data: bytes
    ) -> None:
        if map_name not in self._tp_image_labels:
            return

        img_label = self._tp_image_labels[map_name]

        # Check if widget still exists
        try:
            _ = img_label.isVisible()
        except RuntimeError:
            return

        # Store texture data for context menu
        self._texturepack_data[map_name] = {
            'id': map_id,
            'hash': hash_val,
            'data': data,
        }
        # Update label property with hash
        img_label.setProperty('map_hash', hash_val)

        # Load image
        image = Image.open(io.BytesIO(data))
        if image.mode not in {'RGB', 'RGBA'} or image.mode == 'RGB':
            image = image.convert('RGBA')

        # Scale up small images to 512x512 minimum
        min_size = 512
        if image.width < min_size or image.height < min_size:
            # Scale to at least 512 on the smaller dimension
            scale_factor = max(min_size / image.width, min_size / image.height)
            new_width = int(image.width * scale_factor)
            new_height = int(image.height * scale_factor)
            image = image.resize((new_width, new_height), Image.Resampling.NEAREST)

        qimage = QImage(
            image.tobytes(),
            image.width,
            image.height,
            QImage.Format.Format_RGBA8888,
        )
        pixmap = QPixmap.fromImage(qimage)

        # Store original pixmap for copy
        self._tp_pixmaps[map_name] = pixmap

        # Scale to fit container
        container_width = self.preview_scroll.viewport().width() - 30
        if container_width < 100:
            container_width = 400

        if pixmap.width() > container_width:
            scaled = pixmap.scaledToWidth(
                container_width, Qt.TransformationMode.SmoothTransformation
            )
        else:
            scaled = pixmap

        img_label.setPixmap(scaled)
        img_label.setStyleSheet('')

    def _on_texturepack_texture_loaded(
        self, map_name: str, map_id: str, hash_val: str, data: bytes
    ) -> None:
        """Handle loaded texture from texture pack."""
        # Hide loading on first texture
        self._hide_loading()

        try:
            self._on_texturepack_texture_loaded_impl(map_name, map_id, hash_val, data)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._on_texturepack_texture_error(map_name, str(exc))

    def _on_texturepack_texture_error(self, map_name: str, error: str) -> None:
        """Handle texture load error."""
        with contextlib.suppress(Exception):
            if map_name not in self._tp_image_labels:
                return

            img_label = self._tp_image_labels[map_name]

            try:
                _ = img_label.isVisible()
            except RuntimeError:
                return

            img_label.setText(tr('ui.cache.cache_viewer.error_value', value0=error))
            img_label.setStyleSheet('color: #ff6b6b; padding: 10px;')

    def _show_texturepack_context_menu(self, pos: QPoint, label: QLabel) -> None:
        """Show context menu for texturepack image."""

        map_name = label.property('map_name')
        map_id = label.property('map_id')
        map_hash = label.property('map_hash') or ''
        slot_key = label.property('slot_key') or ''

        menu = QMenu(self)

        # Copy image
        copy_image_action = menu.addAction(tr('ui.cache.cache_viewer.copy_image'))

        menu.addSeparator()

        # Copy name/slot-key/sub-asset-id/hash
        copy_name_action = menu.addAction(
            tr('ui.cache.cache_viewer.copy_name_value', value0=map_name)
        )
        # "Copy ID" intentionally copies slot key, because this is the exact
        # value users should paste into replace_ids for per-slot replacement.
        copy_id_action = menu.addAction(
            tr('ui.cache.cache_viewer.copy_id_value_use_this_for_replacer', value0=slot_key)
        )
        copy_subasset_action = menu.addAction(
            tr('ui.cache.cache_viewer.copy_sub_asset_id_value_cannot_be', value0=map_id)
        )
        copy_hash_action = None
        if map_hash:
            copy_hash_action = menu.addAction(
                tr('ui.cache.cache_viewer.copy_hash_value', value0=map_hash[:16])
            )

        menu.addSeparator()

        # Copy XML
        copy_xml_action = menu.addAction(tr('ui.cache.cache_viewer.copy_texturepack_xml'))

        menu.addSeparator()

        # Export raw per-slot KTX2 files captured during game session.
        export_slot_ktx2_action = menu.addAction(tr('ui.cache.cache_viewer.export_slot_ktx2_files'))

        action = menu.exec(label.mapToGlobal(pos))

        if action == copy_image_action:
            pixmap = self._tp_pixmaps.get(map_name)
            if pixmap and not pixmap.isNull():
                copy_pixmap_to_clipboard(pixmap)
        elif action == copy_name_action:
            QApplication.clipboard().setText(map_name)
        elif action == copy_id_action:
            QApplication.clipboard().setText(slot_key)
        elif action == copy_subasset_action:
            QApplication.clipboard().setText(str(map_id))
        elif action == copy_hash_action and map_hash:
            QApplication.clipboard().setText(map_hash)
        elif action == copy_xml_action:
            QApplication.clipboard().setText(self._texturepack_xml)
        elif action == export_slot_ktx2_action:
            self._export_texpack_slot_ktx2(slot_key.split(':')[0] if ':' in slot_key else '')

    def _export_texpack_slot_ktx2(self, asset_id: str) -> None:
        """Export canonical per-slot KTX2s plus every captured Roblox mip pack."""
        if not asset_id:
            return

        slot_dir = self.cache_manager.get_texturepack_slot_dir()
        slot_names = {0: 'Color', 1: 'Normal', 2: 'ORM'}
        found: list[tuple[Path, str]] = []
        for slot, name in slot_names.items():
            src = slot_dir / f'{asset_id}_slot{slot}.ktx2'
            if src.exists():
                found.append((src, f'{asset_id}_slot{slot}_{name}.ktx2'))
            for pack_src in self.cache_manager.get_texturepack_slot_pack_paths(asset_id, slot):
                pack_suffix = pack_src.name.split(f'{asset_id}_slot{slot}_', 1)[-1]
                found.append((pack_src, f'{asset_id}_slot{slot}_{name}_{pack_suffix}'))

        if not found:
            QMessageBox.information(
                self,
                tr('ui.cache.cache_viewer.no_slot_ktx2_files'),
                tr('ui.cache.cache_viewer.no_slot_ktx2_files_found_for_pack', value0=asset_id),
            )
            return

        dest_dir_str = QFileDialog.getExistingDirectory(
            self,
            tr('ui.cache.cache_viewer.export_slot_ktx2_for_value', value0=asset_id),
            str(self.cache_manager.export_dir),
        )
        if not dest_dir_str:
            return

        dest_dir = Path(dest_dir_str)
        exported: list[str] = []
        for src, dest_name in found:
            dst = dest_dir / dest_name

            shutil.copy2(str(src), str(dst))
            exported.append(dest_name)
        self._show_export_complete_message(
            tr('cache.export.complete_title'),
            tr(
                'cache.export.one_slot_ktx2'
                if len(exported) == 1
                else 'cache.export.slot_ktx2_files',
                count=len(exported),
                destination=dest_dir,
                files='\n'.join(exported),
            ),
            [dest_dir / name for name in exported],
        )

    def _install_audio_key_filter(self) -> None:
        app = cast('QApplication | None', QApplication.instance())
        if app is None:
            self._audio_key_filter_installed = False
            return
        app.installEventFilter(self)
        self._audio_key_filter_installed = True

    def _preview_audio_impl(self, data: bytes, asset_id: str) -> None:
        # Track asset id for this audio preview to avoid stale UI updates
        self._audio_preview_asset_id = asset_id

        # If selection changed since request, abort
        if getattr(self, '_selected_asset_id', None) != asset_id:
            log_buffer.log('Preview', 'Ignored stale audio preview request')
            return
        # Create temporary file for audio
        temp_dir = Path(tempfile.gettempdir()) / 'fleasion_audio'
        temp_dir.mkdir(exist_ok=True)

        # Determine file extension.
        ext = _detect_cache_extension(self.cache_manager, data, 3)
        if ext not in {'.ogg', '.mp3', '.wav', '.flac'}:
            ext = '.mp3'
        temp_file = temp_dir / f'{asset_id}{ext}'

        # Write audio data to temp file
        Path(temp_file).write_bytes(data)

        # Create audio player with config manager for volume persistence
        self.audio_player = AudioPlayerWidget(str(temp_file), self, self.config_manager)

        # Clear previous audio widgets
        while self.audio_container_layout.count():
            child = self.audio_container_layout.takeAt(0)
            if child is not None:
                child_widget = child.widget()
                if child_widget is not None:
                    child_widget.deleteLater()

        # Add new audio player
        # Ensure selection is still the same asset before showing
        if getattr(self, '_selected_asset_id', None) != asset_id:
            with contextlib.suppress(Exception):
                self.audio_player.deleteLater()
            # Ensure we don't keep a dangling reference
            with contextlib.suppress(Exception):
                self.audio_player = None
            log_buffer.log('Preview', 'Aborted adding audio widget for stale selection')
            return

        self.audio_container_layout.addWidget(self.audio_player)
        self._hide_loading()
        self.audio_wrapper.show()
        self.stop_preview_btn.show()

        # Install global event filter to catch Space for play/pause while audio preview is active.
        try:
            self._install_audio_key_filter()
        except RuntimeError:
            self._audio_key_filter_installed = False

        # When audio stops or widget is deleted, remove the event filter
        with contextlib.suppress(Exception):
            self.audio_player.stopped.connect(self._remove_audio_key_filter)

    def _preview_audio(self, data: bytes, asset_id: str) -> None:
        """Preview an audio asset."""

        try:
            self._preview_audio_impl(data, asset_id)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._show_text_preview(tr('cache.preview.audio_error', error=exc))
            log_buffer.log('Scraper', f'Audio preview error: {exc}')

    def _preview_animation(self, data: bytes, asset_id: str) -> None:
        """Preview an animation asset (RBXM XML format) using background thread."""
        # Track which asset this animation loader is for
        self._animation_loader_asset_id = asset_id
        self._show_loading()
        self._animation_loader = AnimationLoaderThread(data, asset_id)
        self._animation_loader.animation_ready.connect(self._on_animation_ready)

        def on_animation_error(error: str) -> None:
            self._show_text_preview(tr('cache.preview.animation_error', error=error))

        self._animation_loader.error.connect(on_animation_error)
        self._animation_loader.start()

    def _on_animation_ready_impl(self, data: bytes) -> None:
        # Load in the animation viewer (must be on main thread for OpenGL)
        animation_viewer = self._ensure_animation_viewer()
        if animation_viewer.load_animation(data):
            animation_viewer.show()
            self.stop_preview_btn.show()
            return

        if is_rbx_model_data(data):
            asset_id = getattr(self, '_animation_loader_asset_id', '') or ''
            self._preview_rbxm(
                data,
                {'id': asset_id, 'type': 24, 'type_name': 'Animation'},
                title_prefix=tr('cache.preview.animation_structure'),
            )
            return

        # Fallback: try to decode as XML for text display
        text = data.decode('utf-8', errors='replace')

        # Check if it's XML
        if text.strip().startswith('<'):
            # Format XML for display
            try:
                DefusedElementTree.fromstring(data)
                # Pretty print XML
                dom = safe_minidom.parseString(cast('str', data))
                pretty_xml = dom.toprettyxml(indent='  ')
                # Remove extra blank lines
                lines = [line for line in pretty_xml.split('\n') if line.strip()]
                self._show_text_preview('\n'.join(lines[:500]))  # Limit lines
            except (
                DefusedXmlException,
                DefusedElementTree.ParseError,
                ExpatError,
                TypeError,
                ValueError,
            ):
                # Fallback to raw text
                self._show_text_preview(
                    tr(
                        'cache.preview.animation_data',
                        size=self._format_size(len(data)),
                        content=text[:5000],
                    )
                )
        else:
            # Binary format, show hex
            reason = tr('cache.preview.animation_unsupported_format')
            self._preview_hex(data, {'id': '', 'type_name': 'Animation'}, reason=reason)

    def _on_animation_ready(self, data: bytes) -> None:
        """Handle animation data ready from background thread."""
        # Ignore if selection changed since loader started
        with contextlib.suppress(Exception):
            if getattr(self, '_animation_loader_asset_id', None) != self._selected_asset_id:
                log_buffer.log('Preview', 'Stale animation result ignored')
                return

        self._hide_loading()
        try:
            self._on_animation_ready_impl(data)
        except (
            AttributeError,
            DefusedXmlException,
            EOFError,
            DefusedElementTree.ParseError,
            ExpatError,
            ImportError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            zlib.error,
        ) as exc:
            self._show_text_preview(tr('cache.preview.animation_error', error=exc))

    def _preview_font_impl(self, data: bytes) -> None:
        log_buffer.log('Preview', f'Loading font ({len(data)} bytes)')

        # Create font viewer widget
        font_viewer = FontViewerWidget(data, self)

        # Clear previous font widgets
        while self.font_container_layout.count():
            child = self.font_container_layout.takeAt(0)
            if child is not None:
                child_widget = child.widget()
                if child_widget is not None:
                    child_widget.deleteLater()

        # Add new font viewer
        self.font_container_layout.addWidget(font_viewer)
        self.font_wrapper.show()
        self.stop_preview_btn.show()

    def _preview_font(self, data: bytes) -> None:
        """Preview a font asset (TTF, OTF, TTC)."""
        try:
            self._preview_font_impl(data)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._show_text_preview(tr('cache.preview.font_error', error=exc))
            log_buffer.log('Preview', f'Font preview error: {exc}')

    def _is_json_data(self, data: bytes) -> tuple[bool, JsonValue | None]:
        """
        Detect if binary data is valid JSON.

        Returns:
            tuple: (is_json: bool, parsed_data: dict|list|None)
        """
        if not data or len(data) < 2:
            return False, None

        # Check for gzip compression
        if data[:2] == b'\x1f\x8b':
            try:
                data = gzip_module.decompress(data)
            except EOFError, OSError, zlib.error:
                return False, None

        # Try UTF-8 decoding first (most common)
        for encoding in ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be']:
            try:
                text = data.decode(encoding)
                parsed = require_json_value(json.loads(text))
            except UnicodeDecodeError, json.JSONDecodeError:
                continue
            else:
                return True, parsed

        return False, None

    def _preview_rbxm_impl(
        self, data: bytes, asset: _AssetRecord, title_prefix: str | None = None
    ) -> None:
        if title_prefix is None:
            title_prefix = tr('cache.preview.rbxm_structure')
        asset_id = str(asset.get('id', ''))
        asset_type = cast('int | str | None', cast('object', asset.get('type')))
        raw_type_name = asset.get('type_name') or (
            self.cache_manager.get_asset_type_name(asset_type)
            if isinstance(asset_type, int)
            else 'RBXM/RBXMX'
        )
        type_name = _localized_asset_type_name(asset_type, raw_type_name)
        asset_label = f'{type_name} {asset_id}'.strip()
        asset_key = self._rbxm_asset_key(asset)
        cached_at = self._rbxm_asset_cached_at(asset)
        if not cached_at:
            selected_asset = self._get_selected_asset()
            if selected_asset and self._rbxm_asset_key(selected_asset) == asset_key:
                cached_at = self._rbxm_asset_cached_at(selected_asset)
                asset = cast('_AssetRecord', dict(asset))
                asset['cached_at'] = cached_at

        draft_document = self._get_modified_rbxm_draft(asset)
        if draft_document is not None:
            self.rbxm_viewer.load_document(draft_document, asset_label=asset_label, dirty=True)
        else:
            self.rbxm_viewer.load_bytes(data, asset_label=asset_label)
        self._rbxm_preview_asset_key = asset_key
        self._rbxm_preview_cached_at = cached_at

        # Persist detected type only after a successful parse, matching JSON detection behavior.
        with contextlib.suppress(Exception):
            if isinstance(asset_type, int) and self.cache_manager.get_asset_type_name(
                asset_type
            ).startswith('Unknown'):
                self.cache_manager.set_detected_type(asset_id, asset_type, 'RBXM/RBXMX')
                current_row = self.table.currentRow()
                if current_row >= 0:
                    type_item = self.table.item(current_row, 4)
                    if type_item:
                        type_item.setText(tr('ui.cache.cache_viewer.rbxm_rbxmx'))

        self._hide_loading()
        self.rbxm_viewer.show()
        self.stop_preview_btn.show()
        with contextlib.suppress(Exception):
            self.preview_title_label.setText(title_prefix)

    def _preview_rbxm(
        self, data: bytes, asset: _AssetRecord, title_prefix: str | None = None
    ) -> None:
        """Display an RBXM/RBXMX instance/property structure preview."""
        _ui_boundary(
            lambda: self._preview_rbxm_impl(data, asset, title_prefix),
            fallback=None,
            on_error=lambda exc: self._preview_hex(
                data,
                {
                    'id': asset.get('id', ''),
                    'type_name': asset.get('type_name', 'RBXM/RBXMX'),
                },
                reason=tr('cache.preview.rbxm_parse_failed', error=exc),
            ),
        )

    def _preview_json(self, data: bytes, asset: _AssetRecord) -> None:
        """Display JSON data in the JSON viewer."""
        is_json, parsed_data = self._is_json_data(data)

        if not is_json or parsed_data is None:
            # Fallback to hex dump
            self._preview_hex(data, asset)
            return

        # Persist the detected JSON type to the cache index
        asset_id = asset['id']
        asset_type = asset['type']
        with contextlib.suppress(Exception):
            self.cache_manager.set_detected_type(asset_id, asset_type, 'Json')

            # Update the table type display immediately to show "Json"
            current_row = self.table.currentRow()
            if current_row >= 0:
                type_item = self.table.item(current_row, 4)  # Type column is index 4
                if type_item:
                    # Update to 'Json' (now persistent)
                    type_item.setText(tr('ui.cache.cache_viewer.json'))

        # Load and display in JSON viewer
        self.json_viewer.load_json(parsed_data)
        self._hide_loading()
        self.json_viewer.show()
        self.stop_preview_btn.show()

    def _preview_hex(
        self,
        data: bytes,
        asset: _AssetRecord | dict[str, str],
        reason: str | None = None,
    ) -> None:
        """Show hex dump preview."""
        # Show first 1KB as hex dump
        preview_size = min(1024, len(data))
        hex_lines: list[str] = []

        hex_lines.append(tr('cache.preview.hex.asset_id', asset_id=asset['id']))
        display_type_name = _localized_asset_type_name(asset.get('type'), asset.get('type_name'))
        hex_lines.extend(
            (
                tr('cache.preview.hex.type', type_name=display_type_name),
                tr('cache.preview.hex.size', size=self._format_size(len(data))),
            )
        )
        if reason:
            hex_lines.append(tr('cache.preview.hex.reason', reason=reason))
        hex_lines.append(tr('cache.preview.hex.first_bytes', count=preview_size))

        for i in range(0, preview_size, 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i : i + 16])
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i : i + 16])
            hex_lines.append(f'{i:08x}  {hex_part:<48}  {ascii_part}')

        if len(data) > preview_size:
            hex_lines.append(tr('cache.preview.hex.more_bytes', count=len(data) - preview_size))

        self._show_text_preview('\n'.join(hex_lines))
        mono_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.text_viewer.setFont(mono_font)
        self.text_viewer.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)

    def _show_text_preview(self, text: str) -> None:
        """Show text in the text viewer."""
        self._hide_loading()
        self.text_viewer.setFont(self._text_viewer_default_font)
        self.text_viewer.setLineWrapMode(self._text_viewer_default_wrap)
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()
        self.stop_preview_btn.show()

    # Load Asset dialog

    def _show_blacklist_dialog(self) -> None:
        """Show a dialog for managing blacklisted asset IDs."""

        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.cache.cache_viewer.blacklist_ids_2'))
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(tr('ui.cache.cache_viewer.blacklisted_asset_ids'))
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        hint = QLabel(tr('ui.cache.cache_viewer.enter_asset_ids_separated_by_commas_spaces'))
        hint.setStyleSheet('color: gray; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlaceholderText(tr('ui.cache.cache_viewer.e_g_1818_1234567890_9876543210'))

        # Populate with current blacklist
        if self._blacklisted_ids:
            text_edit.setPlainText(', '.join(sorted(self._blacklisted_ids, key=int)))
        layout.addWidget(text_edit)

        # Search row
        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_edit = QLineEdit()
        search_edit.setPlaceholderText(tr('ui.cache.cache_viewer.search_ids'))
        search_layout.addWidget(search_edit)
        # Push the following widgets to the right side
        search_layout.addStretch()
        status_label = QLabel('')
        status_label.setStyleSheet('color: #888; font-size: 9pt;')
        search_layout.addWidget(status_label)
        apply_btn = QPushButton(tr('ui.cache.cache_viewer.apply_blacklist'))
        search_layout.addWidget(apply_btn)
        layout.addLayout(search_layout)

        dialog.setLayout(layout)

        last_search_query = ['']

        def _search_id() -> None:
            query = search_edit.text().strip()
            if not query:
                return
            # If query changed, reset search to start from beginning
            if query != last_search_query[0]:
                last_search_query[0] = query
                text_edit.moveCursor(text_edit.textCursor().MoveOperation.Start)
            # Search from current cursor position forward
            cursor = text_edit.document().find(query, text_edit.textCursor())
            if cursor.isNull():
                # Wrap around to start
                cursor = text_edit.document().find(query)
            if not cursor.isNull():
                text_edit.setTextCursor(cursor)
                text_edit.ensureCursorVisible()
                status_label.setText('')
            else:
                status_label.setText(tr('ui.cache.cache_viewer.id_value_not_found', value0=query))
                status_label.setStyleSheet('color: #cc5555; font-size: 9pt;')

        search_edit.returnPressed.connect(_search_id)
        search_edit.textChanged.connect(lambda: status_label.setText(''))

        def _apply() -> None:
            content = text_edit.toPlainText().strip()
            content = content.replace('\n', ',').replace(';', ',').replace(' ', ',')
            ids: list[str] = []
            for raw_part in content.split(','):
                part = raw_part.strip()
                if part:
                    with contextlib.suppress(ValueError):
                        ids.append(str(int(part)))
            self._blacklisted_ids = set(ids)
            if self.config_manager is not None:
                self.config_manager.scraper_blacklist = ids
            # Fast path: hide/show rows in-place rather than full repopulate
            for row in range(self.table.rowCount()):
                id_item = self.table.item(row, 1)
                if id_item is None:
                    continue
                asset_data = id_item.data(Qt.ItemDataRole.UserRole)
                asset_record = (
                    cast('_AssetRecord', asset_data) if isinstance(asset_data, dict) else None
                )
                asset_id = asset_record.get('id') if asset_record is not None else None
                self.table.setRowHidden(row, asset_id in self._blacklisted_ids)
            count = len(self._blacklisted_ids)
            status_label.setText(
                tr(
                    'ui.cache.cache_viewer.blacklist_applied_value',
                    value0=tr_count(count, 'count.id.one', 'count.id.other'),
                )
            )
            status_label.setStyleSheet('color: #55cc55; font-size: 9pt;')
            if self._blacklisted_ids:
                log_buffer.log(
                    'Scraper',
                    f'Blacklist updated: {format_count(count, "ID")} active — {", ".join(sorted(self._blacklisted_ids, key=lambda x: int(x) if x.isdigit() else 0))}',
                )
            else:
                log_buffer.log('Scraper', 'Blacklist cleared')

        apply_btn.clicked.connect(_apply)

        dialog.exec()

    def _show_load_asset_dialog(self) -> None:
        """Show a dialog for manually entering asset IDs to download from Roblox."""

        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.cache.cache_viewer.load_assets'))
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(tr('ui.cache.cache_viewer.load_assets_from_roblox'))
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        hint = QLabel(tr('ui.cache.cache_viewer.enter_asset_ids_separated_by_commas_spaces'))
        hint.setStyleSheet('color: gray; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlaceholderText(tr('ui.cache.cache_viewer.e_g_1818_1234567890_9876543210'))
        layout.addWidget(text_edit)

        status_label = QLabel('')
        status_label.setStyleSheet('color: #888; font-size: 9pt;')

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_layout.addWidget(status_label)

        load_btn = QPushButton(tr('ui.cache.cache_viewer.load_asset_ids'))
        btn_layout.addWidget(load_btn)
        layout.addLayout(btn_layout)

        dialog.setLayout(layout)

        def on_load_clicked() -> None:
            content = text_edit.toPlainText().strip()
            if not content:
                return

            # Parse IDs: support commas, spaces, newlines, semicolons as separators
            content = content.replace('\n', ',').replace(';', ',').replace(' ', ',')
            raw_ids: list[int] = []
            for raw_part in content.split(','):
                part = raw_part.strip()
                if part:
                    try:
                        raw_ids.append(int(part))
                    except ValueError:
                        pass  # Skip non-numeric entries

            if not raw_ids:
                status_label.setText(tr('ui.cache.cache_viewer.no_valid_asset_ids_found'))
                status_label.setStyleSheet('color: #cc5555; font-size: 9pt;')
                return

            # Deduplicate while preserving order
            seen: set[int] = set()
            asset_ids: list[int] = []
            for aid in raw_ids:
                if aid not in seen:
                    seen.add(aid)
                    asset_ids.append(aid)

            # Disable button while loading
            load_btn.setEnabled(False)
            text_edit.setReadOnly(True)
            status_label.setText(
                tr(
                    'ui.cache.cache_viewer.loading_value',
                    value0=tr_count(asset_ids, 'count.asset.one', 'count.asset.other'),
                )
            )
            status_label.setStyleSheet('color: #888; font-size: 9pt;')

            log_buffer.log(
                'Scraper',
                f'[Load Asset] Starting load of {format_count(asset_ids, "asset ID")}',
            )

            # Stop any existing loader
            if self._asset_loader is not None:
                self._asset_loader.stop()
                self._asset_loader.quit()
                self._asset_loader.wait()
                self._asset_loader = None

            self._asset_loader = AssetLoaderThread(
                asset_ids, self.cache_manager, self.cache_scraper
            )

            def on_status(msg: str) -> None:
                status_label.setText(msg)

            def on_finished(loaded: int, failed: int) -> None:
                self._on_load_assets_complete(
                    loaded, failed, dialog, load_btn, text_edit, status_label
                )

            self._asset_loader.status_message.connect(on_status)
            self._asset_loader.finished_loading.connect(on_finished)
            self._asset_loader.start()

        load_btn.clicked.connect(on_load_clicked)

        # Handle cleanup when dialog is closed (by user or programmatically)
        def on_dialog_finished() -> None:
            if self._asset_loader is not None:
                self._asset_loader.stop()
                self._asset_loader.quit()
                self._asset_loader.wait()
                self._asset_loader = None

        dialog.finished.connect(on_dialog_finished)

        # Non-modal: use show() so the rest of the app remains interactive
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.show()

    def _on_load_assets_complete(
        self,
        loaded: int,
        failed: int,
        _dialog: QDialog,
        load_btn: QPushButton,
        text_edit: QTextEdit,
        status_label: QLabel,
    ) -> None:
        """Handle completion of the asset loading thread."""
        # Merge resolved metadata into _asset_info so names/creators show immediately
        if self._asset_loader is not None:
            resolved = getattr(self._asset_loader, '_resolved_metadata', {})
            creator_names = getattr(self._asset_loader, '_resolved_creator_names', {})
            for asset_id, meta in resolved.items():
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': '',
                        'resolved_name': None,
                        'creator_id': None,
                        'creator_name': None,
                        'creator_type': None,
                        'created_at': None,
                        'updated_at': None,
                        'row': None,
                    }
                info = self._asset_info[asset_id]
                info['resolved_name'] = meta.get('name')
                info['creator_id'] = meta.get('creator_id')
                info['creator_type'] = meta.get('creator_type')
                info['created_at'] = meta.get('created_at') or ''
                info['updated_at'] = meta.get('updated_at') or ''
                cid = meta.get('creator_id')
                if cid is not None and cid in creator_names:
                    info['creator_name'] = creator_names[cid]
                elif meta.get('creator_name'):
                    info['creator_name'] = meta['creator_name']

                # Persist to index
                self._save_resolved_name_to_index(asset_id, meta.get('name', ''))
                self._save_resolved_creator_to_index(
                    asset_id,
                    meta.get('creator_id'),
                    info.get('creator_name'),
                    meta.get('creator_type'),
                )
                self._save_resolved_timestamps_to_index(
                    asset_id,
                    meta.get('created_at'),
                    meta.get('updated_at'),
                )

            # Save index once
            with contextlib.suppress(Exception):
                _save_cache_index(self.cache_manager)

        # Re-enable UI
        load_btn.setEnabled(True)
        text_edit.setReadOnly(False)

        if failed == 0:
            status_label.setText(
                tr(
                    'ui.cache.cache_viewer.done_loaded_value',
                    value0=tr_count(loaded, 'count.asset.one', 'count.asset.other'),
                )
            )
            status_label.setStyleSheet('color: #55cc66; font-size: 9pt;')
        else:
            status_label.setText(
                tr(
                    'ui.cache.cache_viewer.done_loaded_value_failed_value',
                    value0=loaded,
                    value1=failed,
                )
            )
            status_label.setStyleSheet('color: #ccaa55; font-size: 9pt;')

        log_buffer.log('Scraper', f'[Load Asset] Finished: {loaded} loaded, {failed} failed')

        # Refresh the table to show newly loaded assets
        self._refresh_assets()
