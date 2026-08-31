"""JSON tree viewer widget."""

from __future__ import annotations

import gzip as gzip_module
import importlib
import io
import json
import tempfile
from collections.abc import Callable, Iterator
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, override
from urllib.parse import urlparse

import requests as _requests
from defusedxml import ElementTree as DefusedElementTree, minidom as defused_minidom
from defusedxml.common import DefusedXmlException
from PIL import Image, UnidentifiedImageError
from PySide6.QtCore import QEvent, QObject, QPoint, QSignalBlocker, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QIcon, QImage, QKeyEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fleasion.cache.rbxm_preview import RbxmPreviewWidget, is_rbx_model_data
from fleasion.localization import tr, tr_count
from fleasion.utils import get_icon_path
from fleasion.utils.clipboard import copy_pixmap_to_clipboard

if TYPE_CHECKING:
    from fleasion.cache.animation_viewer import AnimationViewerPanel
    from fleasion.cache.audio_player import AudioPlayerWidget
    from fleasion.cache.cache_json_viewer import CacheJsonViewer
    from fleasion.cache.font_viewer import FontViewerWidget
    from fleasion.cache.obj_viewer import ObjViewerPanel
    from fleasion.config.manager import ConfigManager


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | dict[str, JsonValue] | list[JsonValue]
type ImportValue = int | str
type ImportIdsCallback = Callable[[list[ImportValue]], object]
type ImportReplacementCallback = Callable[[ImportValue], object]


class _ExportObjFromDoc(Protocol):
    def __call__(self, doc: object, output_path: Path, *, decompose: bool = False) -> None: ...


class _CacheScraperLike(Protocol):
    """Cache scraper surface used by the JSON viewer."""

    def fetch_asset_with_place_id_retry(
        self,
        asset_id: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> tuple[bytes | None, int | None]: ...

    def https_get(
        self,
        hostname: str,
        path: str,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes | None: ...


def _import_attr(module_name: str, attr_name: str) -> object:
    return vars(importlib.import_module(module_name))[attr_name]


def _convert_mesh(data: bytes) -> str | None:
    convert = cast(
        'Callable[[bytes], str | None]',
        _import_attr('fleasion.cache.mesh_processing', 'convert'),
    )
    return convert(data)


def _creator_game_settings() -> tuple[
    int,
    tuple[int, ...],
    Callable[[int, int, int], list[str]],
]:
    module_name = 'fleasion.proxy.addons.cache_scraper'
    max_scan = cast('int', _import_attr(module_name, 'CREATOR_GAME_MAX_SCAN'))
    page_limits = cast('tuple[int, ...]', _import_attr(module_name, 'CREATOR_GAME_PAGE_LIMITS'))
    base_paths = cast(
        'Callable[[int, int, int], list[str]]',
        _import_attr(module_name, 'creator_game_base_paths'),
    )
    return max_scan, page_limits, base_paths


def _solid_model_tools() -> tuple[Callable[[bytes], object], _ExportObjFromDoc]:
    module_name = 'fleasion.cache.tools.solidmodel_converter.converter'
    deserialize = cast('Callable[[bytes], object]', _import_attr(module_name, 'deserialize_rbxm'))
    export_obj = cast('_ExportObjFromDoc', _import_attr(module_name, '_export_obj_from_doc'))
    return deserialize, export_obj


def _animation_viewer_panel_type() -> type[AnimationViewerPanel]:
    return cast(
        'type[AnimationViewerPanel]',
        _import_attr('fleasion.cache.animation_viewer', 'AnimationViewerPanel'),
    )


def _cache_json_viewer_type() -> type[CacheJsonViewer]:
    return cast(
        'type[CacheJsonViewer]',
        _import_attr('fleasion.cache.cache_json_viewer', 'CacheJsonViewer'),
    )


def _obj_viewer_panel_type() -> type[ObjViewerPanel]:
    return cast(
        'type[ObjViewerPanel]',
        _import_attr('fleasion.cache.obj_viewer', 'ObjViewerPanel'),
    )


def _audio_player_widget_type() -> type[AudioPlayerWidget]:
    return cast(
        'type[AudioPlayerWidget]',
        _import_attr('fleasion.cache.audio_player', 'AudioPlayerWidget'),
    )


def _font_viewer_widget_type() -> type[FontViewerWidget]:
    return cast(
        'type[FontViewerWidget]',
        _import_attr('fleasion.cache.font_viewer', 'FontViewerWidget'),
    )


if TYPE_CHECKING:

    def _scraper_fetch_asset(
        scraper: _CacheScraperLike,
        asset_id: str,
        extra_headers: dict[str, str] | None,
    ) -> tuple[bytes | None, int | None]: ...

    def _scraper_https_get(
        scraper: _CacheScraperLike,
        hostname: str,
        path: str,
        extra_headers: dict[str, str] | None,
    ) -> bytes | None: ...

    def _preserve_object_dict(value: object) -> dict[str, object]: ...

    def _preserve_object_list(value: object) -> list[object]: ...

    def _preserve_int_source(value: object) -> str | int | float: ...

    def _preserve_str(value: object) -> str: ...

    def _tree_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem: ...

    def _top_level_item(tree: QTreeWidget, index: int) -> QTreeWidgetItem: ...

    def _take_layout_item(layout: QVBoxLayout) -> QLayoutItem: ...

    def _require_application(value: object) -> QApplication: ...

    def _key_event(value: QEvent) -> QKeyEvent: ...

    def _toggle_audio_player(player: AudioPlayerWidget) -> None: ...
else:

    def _scraper_fetch_asset(
        scraper: _CacheScraperLike,
        asset_id: str,
        extra_headers: dict[str, str] | None,
    ) -> tuple[bytes | None, int | None]:
        return scraper.fetch_asset_with_place_id_retry(
            asset_id,
            extra_headers=extra_headers,
        )

    def _scraper_https_get(
        scraper: _CacheScraperLike,
        hostname: str,
        path: str,
        extra_headers: dict[str, str] | None,
    ) -> bytes | None:
        return scraper.https_get(hostname, path, extra_headers=extra_headers)

    def _preserve_object_dict(value: object) -> dict[str, object]:
        return value

    def _preserve_object_list(value: object) -> list[object]:
        return value

    def _preserve_int_source(value: object) -> str | int | float:
        return value

    def _preserve_str(value: object) -> str:
        return value

    def _tree_child(item: QTreeWidgetItem, index: int) -> QTreeWidgetItem:
        return item.child(index)

    def _top_level_item(tree: QTreeWidget, index: int) -> QTreeWidgetItem:
        return tree.topLevelItem(index)

    def _take_layout_item(layout: QVBoxLayout) -> QLayoutItem:
        return layout.takeAt(0)

    def _require_application(value: object) -> QApplication:
        return value

    def _key_event(value: QEvent) -> QKeyEvent:
        return value

    def _toggle_audio_player(player: AudioPlayerWidget) -> None:
        player.play_pause_btn.click()


def _coerce_import_value(value: object) -> ImportValue | None:
    """Return a safe replacer value without truncating JSON metadata floats."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


class JsonSearchWorker(QThread):
    """Worker thread for searching JSON tree without blocking UI."""

    results_ready = Signal(list)  # List of matching items
    progress = Signal(int, int)  # Current, total

    def __init__(self, root_items: list[QTreeWidgetItem], query: str) -> None:
        super().__init__()
        self.root_items = root_items
        self.query = query.lower().strip()
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        """Search tree items in background."""
        if not self.query or self._stop_requested:
            return

        matches: list[QTreeWidgetItem] = []
        total_items = 0

        # First, count total items for progress
        def count_items(item: QTreeWidgetItem) -> int:
            count = 1
            for i in range(item.childCount()):
                count += count_items(_tree_child(item, i))
            return count

        for root_item in self.root_items:
            total_items += count_items(root_item)

        # Now search with progress reporting
        processed = 0
        batch_size = 50  # Report progress every 50 items

        def search_item(item: QTreeWidgetItem) -> bool:
            nonlocal processed
            if self._stop_requested:
                return False

            processed += 1

            # Report progress in batches
            if processed % batch_size == 0:
                self.progress.emit(processed, total_items)

            # Check if this item matches
            if self.query in item.text(0).lower():
                matches.append(item)

            # Search children
            return all(search_item(_tree_child(item, i)) for i in range(item.childCount()))

        # Search all root items
        for root_item in self.root_items:
            if not search_item(root_item):
                break

        # Emit final results if not stopped
        if not self._stop_requested:
            self.progress.emit(total_items, total_items)
            self.results_ready.emit(matches)


class AssetFetcherThread(QThread):
    """Fetch raw bytes for a Roblox asset ID or direct URL in a background thread."""

    data_ready = Signal(bytes)
    error = Signal(str)

    # Class-level scraper reference — set once by ProxyMaster/app startup.
    # Avoids threading it through every call site (replacer_config has no scraper ref).
    _scraper: _CacheScraperLike | None = None

    @classmethod
    def set_scraper(cls, scraper: _CacheScraperLike) -> None:
        """Called by ProxyMaster after the scraper is ready."""
        cls._scraper = scraper

    def __init__(self, asset_id_or_url: object) -> None:
        super().__init__()
        self._asset = asset_id_or_url
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _get_roblosecurity(self) -> str | None:
        get_roblosecurity = cast(
            'Callable[[], str | None]',
            _import_attr('fleasion.utils.roblox_auth', 'get_roblosecurity'),
        )
        return get_roblosecurity()

    @staticmethod
    def _request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            'User-Agent': 'Roblox/WinInet',
            'Accept-Encoding': 'gzip, deflate',
        }
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _place_ids_for_game_path(
        game_path: str,
        max_pages: int,
        seen_pids: set[int],
    ) -> Iterator[int]:
        cursor = ''
        for _page in range(max_pages):
            path = game_path + (f'&cursor={cursor}' if cursor else '')
            response = _requests.get(
                f'https://games.roblox.com{path}',
                headers={'Accept': 'application/json'},
                timeout=10,
            )
            if response.status_code != 200:
                break
            response_payload: object = response.json()
            response_json = _preserve_object_dict(response_payload)
            games = _preserve_object_list(response_json.get('data', []))
            for game_value in games:
                game = _preserve_object_dict(game_value)
                root_place_value = game.get('rootPlace')
                root_place = _preserve_object_dict(root_place_value) if root_place_value else {}
                if root_place.get('id'):
                    place_id = int(_preserve_int_source(root_place['id']))
                    if place_id not in seen_pids:
                        seen_pids.add(place_id)
                        yield place_id
            cursor = _preserve_str(response_json.get('nextPageCursor') or '')
            if not cursor:
                break

    @classmethod
    def _creator_place_ids(cls, creator_id: int, creator_type: int) -> Iterator[int]:
        creator_game_max_scan, creator_game_page_limits, creator_game_base_paths = (
            _creator_game_settings()
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
                yield from cls._place_ids_for_game_path(game_path, max_pages, seen_pids)
            if len(seen_pids) > found_before_limit:
                break

    def _retry_asset_for_creator_places_impl(
        self,
        asset_value: int | str,
        headers: dict[str, str],
        extra: dict[str, str] | None,
    ) -> tuple[bytes | None, int | None]:
        info_response = _requests.get(
            f'https://develop.roblox.com/v1/assets?assetIds={asset_value}',
            headers={'Accept': 'application/json', **(extra or {})},
            timeout=10,
        )
        if info_response.status_code != 200:
            return None, None
        info_payload: object = info_response.json()
        info = _preserve_object_dict(info_payload)
        items = _preserve_object_list(info.get('data', []))
        if not items:
            return None, None
        first_item = _preserve_object_dict(items[0])
        creator = _preserve_object_dict(first_item.get('creator') or {})
        creator_id = creator.get('targetId') or first_item.get('creatorTargetId')
        creator_type = creator.get('typeId') or first_item.get('creatorType')
        if creator_id is None or creator_type is None:
            return None, None

        last_status: int | None = None
        for place_id in self._creator_place_ids(
            int(_preserve_int_source(creator_id)),
            int(_preserve_int_source(creator_type)),
        ):
            retry_headers = {**headers, 'Roblox-Place-Id': str(place_id)}
            retry_response = _requests.get(
                f'https://assetdelivery.roblox.com/v1/asset/?id={asset_value}',
                headers=retry_headers,
                timeout=15,
            )
            last_status = retry_response.status_code
            if retry_response.status_code == 200 and retry_response.content:
                return retry_response.content, last_status
        return None, last_status

    def _retry_asset_for_creator_places(
        self,
        asset_value: int | str,
        headers: dict[str, str],
        extra: dict[str, str] | None,
    ) -> tuple[bytes | None, int | None]:
        try:
            return self._retry_asset_for_creator_places_impl(asset_value, headers, extra)
        except (
            ImportError,
            KeyError,
            TypeError,
            ValueError,
            _requests.RequestException,
        ):
            return None, None

    def _fetch_asset_id(
        self,
        asset_value: int | str,
        scraper: _CacheScraperLike | None,
    ) -> tuple[bytes | None, int | None]:
        cookie = self._get_roblosecurity()
        extra = {'Cookie': f'.ROBLOSECURITY={cookie};'} if cookie else None
        if scraper is not None:
            return _scraper_fetch_asset(scraper, str(asset_value), extra)

        headers = self._request_headers(extra)
        response = _requests.get(
            f'https://assetdelivery.roblox.com/v1/asset/?id={asset_value}',
            headers=headers,
            timeout=15,
        )
        status = response.status_code
        data = response.content if status == 200 else None
        if data is None and status == 403:
            retry_data, retry_status = self._retry_asset_for_creator_places(
                asset_value,
                headers,
                extra,
            )
            if retry_status is not None:
                status = retry_status
            if retry_data is not None:
                data = retry_data
        return data, status

    def _fetch_url(
        self,
        value: str,
        scraper: _CacheScraperLike | None,
    ) -> bytes | None:
        parsed = urlparse(value)
        hostname = (parsed.hostname or '').lower()
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        is_roblox = 'roblox.com' in hostname
        cookie = self._get_roblosecurity() if is_roblox else None
        extra = {'Cookie': f'.ROBLOSECURITY={cookie};'} if cookie else None
        if scraper is not None and is_roblox:
            return _scraper_https_get(scraper, hostname, path, extra)
        response = _requests.get(value, headers=self._request_headers(extra), timeout=15)
        return response.content if response.status_code == 200 else None

    def _emit_asset_result(self, data: bytes | None, status: int | None) -> None:
        if self._stop_requested:
            return
        if data:
            self.data_ready.emit(data)
        elif status == 404:
            self.error.emit(tr('json.fetch.asset_not_found'))
        elif status == 403:
            self.error.emit(tr('json.fetch.asset_private'))
        else:
            self.error.emit(tr('json.fetch.no_data'))

    def _run_fetch(self) -> None:
        value = self._asset
        scraper = self._scraper
        if isinstance(value, int) or (
            isinstance(value, str) and value.strip().lstrip('-').isdigit()
        ):
            data, status = self._fetch_asset_id(value, scraper)
            self._emit_asset_result(data, status)
        elif isinstance(value, str) and value.startswith(('http://', 'https://')):
            data = self._fetch_url(value, scraper)
            if not self._stop_requested:
                if data:
                    self.data_ready.emit(data)
                else:
                    self.error.emit(tr('json.fetch.no_data'))
        else:
            self.error.emit(tr('json.fetch.cannot_fetch', value=value))

    def run(self) -> None:
        try:
            self._run_fetch()
        except (
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            _requests.RequestException,
        ) as exc:
            if not self._stop_requested:
                self.error.emit(str(exc))


def _decode_image_pixmap(data: bytes) -> QPixmap:
    image = Image.open(io.BytesIO(data))
    if image.mode != 'RGBA':
        image = image.convert('RGBA')
    qimage = QImage(
        image.tobytes(),
        image.width,
        image.height,
        QImage.Format.Format_RGBA8888,
    )
    return QPixmap.fromImage(qimage)


def _mesh_obj_content(data: bytes) -> str | None:
    working = gzip_module.decompress(data) if data.startswith(b'\x1f\x8b') else data
    return _convert_mesh(working)


def _solid_model_obj_content(data: bytes) -> str:
    deserialize_rbxm, export_obj_from_doc = _solid_model_tools()
    working = gzip_module.decompress(data) if data.startswith(b'\x1f\x8b') else data
    doc = deserialize_rbxm(working)
    with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as file:
        temp_obj_path = Path(file.name)
    try:
        export_obj_from_doc(doc, temp_obj_path, decompose=False)
        return temp_obj_path.read_text(encoding='utf-8')
    finally:
        try:
            temp_obj_path.unlink()
        except FileNotFoundError:
            pass


class ImageLoaderThread(QThread):
    """Load image bytes into a QPixmap in a background thread."""

    image_ready = Signal(QPixmap)
    error = Signal(str)

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            pixmap = _decode_image_pixmap(self.data)
        except (OSError, RuntimeError, TypeError, ValueError, UnidentifiedImageError) as exc:
            if not self._stop_requested:
                self.error.emit(str(exc))
            return
        if not self._stop_requested:
            self.image_ready.emit(pixmap)


class MeshLoaderThread(QThread):
    """Convert raw mesh bytes to OBJ string in a background thread."""

    mesh_ready = Signal(str)
    error = Signal(str)

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            obj_content = _mesh_obj_content(self.data)
        except (EOFError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if not self._stop_requested:
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('json.preview.mesh_conversion_failed'))


class SolidModelLoaderThread(QThread):
    """Convert raw SolidModel (CSG) bytes to OBJ string in a background thread."""

    mesh_ready = Signal(str)
    error = Signal(str)

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:
        try:
            obj_content = _solid_model_obj_content(self.data)
        except (ImportError, OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            if not self._stop_requested:
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('json.preview.solidmodel_conversion_failed'))


class JsonTreeViewer(QDialog):
    """JSON tree viewer dialog."""

    def __init__(  # ruff: ignore[too-many-positional-arguments]
        self,
        parent: QWidget | None,
        data: JsonValue,
        filename: str,
        on_import_ids: ImportIdsCallback,
        on_import_replacement: ImportReplacementCallback,
        config_manager: ConfigManager | None = None,
    ) -> None:
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(tr('ui.gui.json_viewer.json_value', value0=filename))
        self.resize(1200, 650)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        self.data = data
        self.on_import_ids = on_import_ids
        self.on_import_replacement = on_import_replacement
        self.node_values: dict[int, JsonValue] = {}
        self.node_is_leaf: dict[int, bool] = {}

        # Search worker
        self._search_worker: JsonSearchWorker | None = None
        self._is_searching = False
        self._search_matches: list[QTreeWidgetItem] = []
        self._current_match_index: int = 0

        # Preview state
        self._asset_fetcher: AssetFetcherThread | None = None
        self._image_loader: ImageLoaderThread | None = None
        self._mesh_loader: MeshLoaderThread | None = None
        self._animation_loader: None = None
        self._solidmodel_loader: SolidModelLoaderThread | None = None
        self._current_pixmap: QPixmap | None = None
        self._previewing_value: ImportValue | None = (
            None  # track what we started previewing (stale guard)
        )
        self._audio_key_filter_installed = False  # Track if global audio key filter is installed
        self._last_fetched_data: bytes | None = None  # raw bytes for solidmodel fallback

        # Texturepack state
        self.texturepack_widget: QWidget | None = None
        self._texturepack_data: dict[str, dict[str, str | bytes]] = {}  # map_name -> {id, data}
        self._texturepack_xml: str = ''
        self._tp_image_labels: dict[str, QLabel] = {}
        self._tp_pixmaps: dict[str, QPixmap] = {}
        self._tp_fetchers: list[AssetFetcherThread] = []  # active AssetFetcherThread instances

        self._setup_ui()
        self._populate_tree()
        self._set_icon()

    def _set_icon(self) -> None:
        """Set window icon."""
        if icon_path := get_icon_path():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Search debounce timer
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._do_search)

        # ── Splitter: left (search + tree) | right (preview) ──────────────
        self.splitter = QSplitter(Qt.Orientation.Horizontal)

        # ---- Left panel ----
        left_widget = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Search bar
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel(tr('ui.gui.json_viewer.search')))
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self.search_input)

        # Navigation buttons for cycling through matches
        self.prev_match_btn = QPushButton(tr('ui.gui.json_viewer.text'))
        self.prev_match_btn.setFixedWidth(30)
        self.prev_match_btn.setToolTip(tr('ui.gui.json_viewer.previous_match'))
        self.prev_match_btn.clicked.connect(self._cycle_to_prev_match)
        self.prev_match_btn.setEnabled(False)
        search_layout.addWidget(self.prev_match_btn)

        self.next_match_btn = QPushButton(tr('ui.gui.json_viewer.text_2'))
        self.next_match_btn.setFixedWidth(30)
        self.next_match_btn.setToolTip(tr('ui.gui.json_viewer.next_match'))
        self.next_match_btn.clicked.connect(self._cycle_to_next_match)
        self.next_match_btn.setEnabled(False)
        search_layout.addWidget(self.next_match_btn)

        clear_btn = QPushButton(tr('ui.gui.json_viewer.clear'))
        clear_btn.clicked.connect(self.search_input.clear)
        search_layout.addWidget(clear_btn)

        expand_btn = QPushButton(tr('ui.gui.json_viewer.expand_all'))
        expand_btn.clicked.connect(self._expand_all)
        search_layout.addWidget(expand_btn)

        collapse_btn = QPushButton(tr('ui.gui.json_viewer.collapse_all'))
        collapse_btn.clicked.connect(self._collapse_all)
        search_layout.addWidget(collapse_btn)

        left_layout.addLayout(search_layout)

        # Search progress label
        self.search_progress_label = QLabel('')
        self.search_progress_label.setStyleSheet('color: #888; font-size: 11px;')
        self.search_progress_label.hide()
        left_layout.addWidget(self.search_progress_label)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_change)
        left_layout.addWidget(self.tree)

        left_widget.setLayout(left_layout)
        self.splitter.addWidget(left_widget)

        # ---- Right panel (preview) ----
        self.preview_panel = self._create_preview_panel()
        self.preview_panel.hide()
        self.splitter.addWidget(self.preview_panel)

        self.splitter.setSizes([550, 550])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)
        layout.addWidget(self.splitter, stretch=1)

        # Import buttons
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QLabel(tr('ui.gui.json_viewer.import_selected_as')))

        ids_btn = QPushButton(tr('ui.gui.json_viewer.ids_to_replace'))
        ids_btn.clicked.connect(self._import_as_replace_ids)
        btn_layout.addWidget(ids_btn)

        repl_btn = QPushButton(tr('ui.gui.json_viewer.replacement_id'))
        repl_btn.clicked.connect(self._import_as_replacement)
        btn_layout.addWidget(repl_btn)

        btn_layout.addStretch()

        self.match_label = QLabel('')
        self.match_label.setStyleSheet('color: #888; font-size: 11px;')
        btn_layout.addWidget(self.match_label)

        btn_layout.addSpacing(8)

        self.selection_label = QLabel(
            tr(
                'ui.gui.json_viewer.selected_value',
                value0=tr_count(0, 'count.value.one', 'count.value.other'),
            )
        )
        self.selection_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        btn_layout.addWidget(self.selection_label)

        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _add_node(
        self, parent_item: QTreeWidget | QTreeWidgetItem, key: str, value: JsonValue
    ) -> QTreeWidgetItem:
        """Add a node to the tree."""
        if isinstance(value, (dict, list)):
            items = value.items() if isinstance(value, dict) else enumerate(value)
            fmt = '{...}' if isinstance(value, dict) else '[...]'
            display = f'{key}: {fmt}' if key else fmt
            item = QTreeWidgetItem(parent_item, [display])
            item.setExpanded(False)
            self.node_is_leaf[id(item)] = False
            for k, v in items:
                child_key = f'[{k}]' if isinstance(value, list) else str(k)
                self._add_node(item, child_key, v)
        else:
            val_str = (
                'null'
                if value is None
                else str(value).lower()
                if isinstance(value, bool)
                else f'"{value}"'
                if isinstance(value, str)
                else str(value)
            )
            display = f'{key}: {val_str}' if key else val_str
            item = QTreeWidgetItem(parent_item, [display])
            self.node_values[id(item)] = value
            self.node_is_leaf[id(item)] = True
        return item

    def _populate_tree(self) -> None:
        """Populate the tree with data."""
        with QSignalBlocker(self.tree):
            self.tree.setUpdatesEnabled(False)
            try:
                self.tree.clear()
                if isinstance(self.data, (dict, list)):
                    items = (
                        self.data.items() if isinstance(self.data, dict) else enumerate(self.data)
                    )
                    for k, v in items:
                        child_key = f'[{k}]' if isinstance(self.data, list) else str(k)
                        self._add_node(self.tree, child_key, v)
                else:
                    self._add_node(self.tree, '', self.data)
            finally:
                self.tree.setUpdatesEnabled(True)

    def _get_all_leaf_descendants(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        """Get all leaf descendants of an item."""
        if self.node_is_leaf.get(id(item)):
            return [item]
        leaves: list[QTreeWidgetItem] = []
        for i in range(item.childCount()):
            leaves.extend(self._get_all_leaf_descendants(_tree_child(item, i)))
        return leaves

    @staticmethod
    def _is_link_or_path(value: str) -> bool:
        """Check if a string is a link or file path."""
        value = value.strip()
        # Check for URLs
        if value.startswith(('http://', 'https://', 'ftp://', 'file://')):
            return True
        # Check for absolute paths (Unix and Windows)
        if value.startswith('/') or (len(value) > 2 and value[1] == ':'):
            return True
        # Check for relative paths with directory separators
        return bool('/' in value or '\\' in value)

    def _get_selected_values(self) -> list[int | str]:
        """Get numeric values and links/file paths from selected items."""
        leaves: list[QTreeWidgetItem] = []
        leaf_ids: set[int] = set()  # Track IDs to avoid duplicates

        for item in self.tree.selectedItems():
            if self.node_is_leaf.get(id(item)):
                if id(item) not in leaf_ids:
                    leaves.append(item)
                    leaf_ids.add(id(item))
            else:
                for descendant in self._get_all_leaf_descendants(item):
                    if id(descendant) not in leaf_ids:
                        leaves.append(descendant)
                        leaf_ids.add(id(descendant))

        values: list[int | str] = []
        for item in leaves:
            val = self.node_values.get(id(item))
            # JSON floats are metadata surprisingly often (animation Length,
            # EndTime, playback rates, and so on).  int(1.25) silently turning
            # into asset ID 1 caused a config selected from a parent node to
            # target every Image request, so accept only values that were
            # actually encoded as integers.
            coerced = _coerce_import_value(val)
            if coerced is not None:
                values.append(coerced)
            elif isinstance(val, str) and self._is_link_or_path(val):
                values.append(val)
        return values

    def _on_selection_change(self) -> None:
        """Handle selection change and trigger asset preview."""
        vals = self._get_selected_values()
        self.selection_label.setText(
            tr(
                'ui.gui.json_viewer.selected_value',
                value0=tr_count(vals, 'count.value.one', 'count.value.other'),
            )
        )

        # Only preview when exactly one leaf value is selected
        if len(vals) == 1:
            self._preview_value(vals[0])
        else:
            self._clear_preview()

    # ──────────────────────────────────────────────────────────────────────
    # Preview panel creation
    # ──────────────────────────────────────────────────────────────────────

    def _create_preview_panel(self) -> QWidget:
        """Create the right-side preview panel (mirrors cache_viewer's panel)."""
        animation_viewer_type = _animation_viewer_panel_type()
        cache_json_viewer_type = _cache_json_viewer_type()
        obj_viewer_type = _obj_viewer_panel_type()

        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_group = QWidget()
        preview_group_layout = QVBoxLayout()
        preview_group_layout.setContentsMargins(0, 0, 0, 0)
        preview_group_layout.setSpacing(4)

        self.preview_title_label = QLabel(tr('ui.gui.json_viewer.preview'))
        preview_group_layout.addWidget(self.preview_title_label)

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.preview_container = QWidget()
        self.preview_container_layout = QVBoxLayout()
        self.preview_container_layout.setContentsMargins(5, 5, 5, 5)

        # 3D viewer for meshes
        self.obj_viewer = obj_viewer_type(config_manager=self.config_manager)
        self.obj_viewer.clear_requested.connect(self._clear_preview)
        self.preview_container_layout.addWidget(self.obj_viewer)

        # Loading indicator
        self.loading_label = QLabel(tr('ui.gui.json_viewer.loading'))
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet(
            'QLabel { background-color: palette(base); color: #888; font-size: 14px; padding: 20px; }'
        )
        self.preview_container_layout.addWidget(self.loading_label)
        self.loading_label.hide()

        # Image viewer
        self.image_label = QLabel(tr('ui.gui.json_viewer.select_a_single_asset_id_or_url'))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: palette(base); color: #888; }')
        self.image_label.setScaledContents(False)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_image_context_menu)
        self.preview_container_layout.addWidget(self.image_label)

        # Audio player container with centering wrapper
        self.audio_player: AudioPlayerWidget | None = None
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

        # Animation viewer
        self.animation_viewer = animation_viewer_type(config_manager=self.config_manager)
        self.preview_container_layout.addWidget(self.animation_viewer)

        # Text viewer (hex dump / plain text)
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText(tr('ui.gui.json_viewer.no_preview_available'))
        self.preview_container_layout.addWidget(self.text_viewer)

        # JSON viewer
        self.json_viewer = cache_json_viewer_type()
        self.preview_container_layout.addWidget(self.json_viewer)

        # RBXM/RBXMX structure viewer
        self.rbxm_viewer = RbxmPreviewWidget()
        self.preview_container_layout.addWidget(self.rbxm_viewer)

        # Font viewer container with centering wrapper
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

        # Hide all initially
        self.obj_viewer.hide()
        self.audio_wrapper.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.rbxm_viewer.hide()
        self.font_wrapper.hide()

        self.preview_container.setLayout(self.preview_container_layout)
        self.preview_scroll.setWidget(self.preview_container)
        preview_group_layout.addWidget(self.preview_scroll)

        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(4)

        self.stop_preview_btn = QPushButton(tr('ui.gui.json_viewer.stop_preview'))
        self.stop_preview_btn.clicked.connect(self._stop_preview)
        self.stop_preview_btn.hide()
        controls_layout.addWidget(self.stop_preview_btn)

        self.rbxm_view_btn = QPushButton(tr('ui.gui.json_viewer.swap_to_rbxm_view'))
        self.rbxm_view_btn.clicked.connect(self._swap_to_rbxm_view)
        self.rbxm_view_btn.hide()
        controls_layout.addWidget(self.rbxm_view_btn)

        controls_layout.addStretch()
        preview_group_layout.addLayout(controls_layout)

        self.preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(self.preview_group)
        preview_widget.setLayout(preview_layout)
        return preview_widget

    # ──────────────────────────────────────────────────────────────────────
    # Preview orchestration
    # ──────────────────────────────────────────────────────────────────────

    def _preview_value(self, val: ImportValue) -> None:
        """Start preview for a selected asset ID (int) or URL (str)."""
        if val == self._previewing_value:
            return  # Already showing this

        self._stop_all_loaders()
        self._previewing_value = val

        # Show panel
        self.preview_panel.show()
        self._hide_all_preview_widgets()
        self._show_loading()

        # Update group title
        try:
            display = str(val)
            if len(display) > 60:
                display = display[:57] + '...'
            self.preview_title_label.setText(tr('ui.gui.json_viewer.preview_value', value0=display))
        except RuntimeError:
            pass

        self._asset_fetcher = AssetFetcherThread(val)
        self._asset_fetcher.data_ready.connect(self._on_asset_fetched)
        self._asset_fetcher.error.connect(self._on_fetch_error)
        self._asset_fetcher.start()

    def _on_fetch_error(self, error: str) -> None:
        self._show_text_preview(tr('json.preview.fetch_failed', error=error))

    def _on_image_error(self, error: str) -> None:
        self._show_text_preview(tr('json.preview.image_error', error=error))

    def _on_mesh_error(self, error: str) -> None:
        self._show_text_preview(tr('json.preview.mesh_error', error=error))

    def _on_solidmodel_error(self, error: str) -> None:
        self._show_text_preview(tr('json.preview.solidmodel_error', error=error))

    def _on_asset_fetched(self, data: bytes) -> None:
        """Dispatch fetched bytes to the appropriate preview handler."""
        self._last_fetched_data = data
        content_type = self._detect_content_type(data)

        if content_type == 'image':
            self._preview_image(data)
        elif content_type == 'mesh':
            self._preview_mesh(data)
        elif content_type == 'audio':
            self._preview_audio(data)
        elif content_type == 'font':
            self._preview_font(data)
        elif content_type == 'texturepack':
            self._preview_texturepack(data)
        elif content_type in {'rbxm', 'rbxmx'}:
            if is_rbx_model_data(data):
                self.rbxm_view_btn.show()
            self._preview_animation(data)
        elif content_type == 'json':
            self._preview_json(data)
        else:
            self._preview_hex(data)

    def _detect_content_type(self, data: bytes) -> str:
        """Detect content type from magic bytes."""
        working = data
        if data[:2] == b'\x1f\x8b':
            try:
                working = gzip_module.decompress(data)
            except EOFError, OSError:
                pass

        image_signatures = (
            working[:4] == b'\x89PNG',
            working[:2] == b'\xff\xd8',
            working[:4] == b'RIFF' and working[8:12] == b'WEBP',
            working[:3] == b'GIF',
        )
        audio_signatures = (
            working[:4] == b'OggS',
            working[:3] == b'ID3',
            working[:2] in {b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'},
        )
        font_signatures = (
            working[:4] == b'\x00\x01\x00\x00',
            working[:4] == b'OTTO',
            working[:4] == b'ttcf',
            working[:2] == b'\x01\x00',
        )
        content_type = 'unknown'
        if any(image_signatures):
            content_type = 'image'
        elif any(audio_signatures):
            content_type = 'audio'
        elif any(font_signatures):
            content_type = 'font'
        elif working[:7] == b'version':
            content_type = 'mesh'
        elif working[:8] == b'<roblox!':
            content_type = 'rbxm'
        elif working[:7] == b'<roblox' or working[:5] == b'<?xml':
            content_type = 'rbxmx'
            try:
                root = DefusedElementTree.fromstring(working)
                texture_elements = ('color', 'normal', 'metalness', 'roughness', 'emissive')
                if any(root.find(element) is not None for element in texture_elements):
                    content_type = 'texturepack'
            except DefusedXmlException, DefusedElementTree.ParseError:
                pass
        else:
            try:
                stripped = working.lstrip()
                if stripped[:1] in {b'{', b'['}:
                    json.loads(working.decode('utf-8'))
                    content_type = 'json'
            except UnicodeDecodeError, json.JSONDecodeError:
                pass
        return content_type

    # ──────────────────────────────────────────────────────────────────────
    # Per-type preview handlers
    # ──────────────────────────────────────────────────────────────────────

    def _preview_image(self, data: bytes) -> None:
        self._image_loader = ImageLoaderThread(data)
        self._image_loader.image_ready.connect(self._on_image_ready)
        self._image_loader.error.connect(self._on_image_error)
        self._image_loader.start()

    def _on_image_ready(self, pixmap: QPixmap) -> None:
        self._hide_loading()
        self._current_pixmap = pixmap
        self._scale_and_show_image(pixmap)
        self.image_label.show()
        self.stop_preview_btn.show()

    def _scale_and_show_image(self, pixmap: QPixmap) -> None:
        container_w = self.preview_scroll.viewport().width() - 20
        container_h = self.preview_scroll.viewport().height() - 20
        if container_w < 100:
            container_w = 400
        if container_h < 100:
            container_h = 400
        scaled = pixmap.scaled(
            container_w,
            container_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _preview_mesh(self, data: bytes) -> None:
        self._mesh_loader = MeshLoaderThread(data)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)
        self._mesh_loader.error.connect(self._on_mesh_error)
        self._mesh_loader.start()

    def _on_mesh_ready(self, obj_content: str) -> None:
        self._hide_loading()
        self.obj_viewer.load_obj(obj_content, '')
        self.obj_viewer.show()
        self.stop_preview_btn.show()

    def _show_audio_preview(self, data: bytes, audio_player_type: type[AudioPlayerWidget]) -> None:
        temp_dir = Path(tempfile.gettempdir()) / 'fleasion_audio'
        temp_dir.mkdir(exist_ok=True)
        temp_file = temp_dir / f'preview_{id(self)}.mp3'
        temp_file.write_bytes(data)

        self.audio_player = audio_player_type(str(temp_file), self, self.config_manager)
        while self.audio_container_layout.count():
            child = _take_layout_item(self.audio_container_layout)
            child_widget = child.widget()
            if child_widget:
                child_widget.deleteLater()

        self.audio_container_layout.addWidget(self.audio_player)
        self._hide_loading()
        self.audio_wrapper.show()
        self.stop_preview_btn.show()
        try:
            _require_application(QApplication.instance()).installEventFilter(self)
            self._audio_key_filter_installed = True
        except RuntimeError, TypeError:
            self._audio_key_filter_installed = False
        try:
            self.audio_player.stopped.connect(self._remove_audio_key_filter)
        except RuntimeError, TypeError:
            pass

    def _preview_audio(self, data: bytes) -> None:
        audio_player_type = _audio_player_widget_type()
        try:
            self._show_audio_preview(data, audio_player_type)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._show_text_preview(tr('json.preview.audio_error', error=exc))

    def _show_font_preview(self, data: bytes, font_viewer_type: type[FontViewerWidget]) -> None:
        font_viewer = font_viewer_type(data, self)
        while self.font_container_layout.count():
            child = _take_layout_item(self.font_container_layout)
            child_widget = child.widget()
            if child_widget:
                child_widget.deleteLater()
        self.font_container_layout.addWidget(font_viewer)
        self._hide_loading()
        self.font_wrapper.show()
        self.stop_preview_btn.show()

    def _preview_font(self, data: bytes) -> None:
        """Preview a font asset (TTF, OTF, TTC)."""
        font_viewer_type = _font_viewer_widget_type()
        try:
            self._show_font_preview(data, font_viewer_type)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self._show_text_preview(tr('json.preview.font_error', error=exc))

    def _show_animation_preview(self, data: bytes) -> None:
        decompressed = data
        if data[:2] == b'\x1f\x8b':
            decompressed = gzip_module.decompress(data)

        if self.animation_viewer.load_animation(decompressed):
            self._hide_loading()
            self.animation_viewer.show()
            self.stop_preview_btn.show()
            return
        if decompressed[:8] == b'<roblox!':
            self._preview_solidmodel(data)
            return

        text = decompressed.decode('utf-8', errors='replace')
        if text.strip().startswith('<'):
            try:
                dom = defused_minidom.parseString(cast('str', decompressed))
                pretty = dom.toprettyxml(indent='  ')
            except DefusedXmlException, ValueError:
                pass
            else:
                lines = [line for line in pretty.split('\n') if line.strip()]
                self._show_text_preview('\n'.join(lines[:500]))
                return
        self._preview_hex(decompressed)

    def _preview_animation(self, data: bytes) -> None:
        """Preview RBXM/RBXMX animation data."""
        try:
            self._show_animation_preview(data)
        except (EOFError, OSError, RuntimeError, TypeError, ValueError, DefusedXmlException) as exc:
            self._show_text_preview(tr('json.preview.animation_error', error=exc))

    def _show_json_preview(self, data: bytes) -> None:
        working = gzip_module.decompress(data) if data[:2] == b'\x1f\x8b' else data
        parsed = json.loads(working.decode('utf-8'))
        self.json_viewer.load_json(parsed)
        self._hide_loading()
        self.json_viewer.show()
        self.stop_preview_btn.show()

    def _preview_json(self, data: bytes) -> None:
        """Preview JSON data in the embedded JSON viewer."""
        try:
            self._show_json_preview(data)
        except (EOFError, OSError, RuntimeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._show_text_preview(tr('json.preview.json_error', error=exc))

    def _show_rbxm_preview(self, data: bytes, title_prefix: str | None) -> None:
        if title_prefix is None:
            title_prefix = tr('json.preview.rbxm_structure')
        asset_label = '' if self._previewing_value is None else str(self._previewing_value).strip()
        self.rbxm_viewer.load_bytes(data, asset_label=asset_label)
        self._hide_loading()
        self.rbxm_viewer.show()
        self.stop_preview_btn.show()
        display = str(self._previewing_value)
        if len(display) > 60:
            display = display[:57] + '...'
        self.preview_title_label.setText(
            tr('json.preview.title_with_value', title=title_prefix, value=display)
            if display
            else title_prefix
        )

    def _preview_rbxm(self, data: bytes, title_prefix: str | None = None) -> None:
        """Preview raw RBXM/RBXMX structure in the shared RBXM viewer."""
        try:
            self._show_rbxm_preview(data, title_prefix)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            self._preview_hex(data, reason=tr('json.preview.rbxm_parse_failed', error=exc))

    def _preview_hex(self, data: bytes, reason: str | None = None) -> None:
        """Show a hex dump for unrecognised content."""
        preview_size = min(1024, len(data))
        lines = [tr('json.preview.hex.size_bytes', count=len(data))]
        if reason:
            lines.append(tr('json.preview.hex.reason', reason=reason))
        lines.append(tr('json.preview.hex.first_bytes', count=preview_size))
        for i in range(0, preview_size, 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i : i + 16])
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i : i + 16])
            lines.append(f'{i:08x}  {hex_part:<48}  {ascii_part}')
        if len(data) > preview_size:
            lines.append(tr('json.preview.hex.more_bytes', count=len(data) - preview_size))
        self._show_text_preview('\n'.join(lines))

    def _swap_to_rbxm_view(self) -> None:
        """Switch the current animation/solidmodel preview to raw RBXM structure."""
        if self._last_fetched_data is None or not is_rbx_model_data(self._last_fetched_data):
            return

        self._stop_all_loaders()
        self._hide_all_preview_widgets()
        self._preview_rbxm(self._last_fetched_data)

    def _preview_solidmodel(self, data: bytes) -> None:
        """Preview a SolidModel (CSG) asset in 3D using background thread."""
        self._solidmodel_loader = SolidModelLoaderThread(data)
        self._solidmodel_loader.mesh_ready.connect(self._on_mesh_ready)
        self._solidmodel_loader.error.connect(self._on_solidmodel_error)
        self._solidmodel_loader.start()

    def _show_texturepack_preview(self, data: bytes) -> None:
        self._cleanup_texturepack()

        # Parse XML to get texture map IDs
        working = data
        if data[:2] == b'\x1f\x8b':
            working = gzip_module.decompress(data)

        xml_text = working.decode('utf-8', errors='replace')
        self._texturepack_xml = xml_text
        root = DefusedElementTree.fromstring(xml_text)

        # Extract texture map IDs in order
        map_order = ['color', 'normal', 'metalness', 'roughness', 'emissive']
        map_display_names = {
            'color': tr('json.texture_map.color'),
            'normal': tr('json.texture_map.normal'),
            'metalness': tr('json.texture_map.metalness'),
            'roughness': tr('json.texture_map.roughness'),
            'emissive': tr('json.texture_map.emissive'),
        }
        maps: dict[str, str] = {}
        for elem in map_order:
            node = root.find(elem)
            if node is not None and node.text:
                maps[map_display_names[elem]] = node.text

        if not maps:
            self._show_text_preview(tr('json.preview.texturepack_no_maps'))
            return

        # Clear texture data storage
        self._texturepack_data = {}

        # Create container widget for texture pack preview
        self.texturepack_widget = QWidget()
        tp_layout = QVBoxLayout()
        tp_layout.setContentsMargins(0, 0, 0, 0)
        tp_layout.setSpacing(10)

        # Store references for async loading
        self._tp_image_labels = {}
        self._tp_pixmaps = {}

        # Create placeholder for each texture map
        for map_name, map_id in maps.items():
            header = QLabel(tr('ui.gui.json_viewer.value_value', value0=map_name, value1=map_id))
            header.setStyleSheet('font-weight: bold; color: #888; padding: 5px;')
            header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            tp_layout.addWidget(header)

            img_label = QLabel(tr('ui.gui.json_viewer.loading'))
            img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            img_label.setStyleSheet(
                'background-color: palette(base); padding: 10px; min-height: 100px;'
            )
            img_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            img_label.setProperty('map_name', map_name)
            img_label.setProperty('map_id', map_id)
            img_label.customContextMenuRequested.connect(
                partial(self._show_texturepack_context_menu, label=img_label)
            )
            tp_layout.addWidget(img_label)
            self._tp_image_labels[map_name] = img_label

        tp_layout.addStretch()
        self.texturepack_widget.setLayout(tp_layout)
        self.preview_container_layout.addWidget(self.texturepack_widget)
        self.texturepack_widget.show()
        self.stop_preview_btn.show()

        # Fetch each texture map via network
        self._tp_fetchers = []
        for map_name, map_id in maps.items():
            fetcher = AssetFetcherThread(map_id)
            fetcher.data_ready.connect(
                partial(self._on_texturepack_texture_fetched, map_name, map_id)
            )
            fetcher.error.connect(partial(self._on_texturepack_texture_error, map_name))
            self._tp_fetchers.append(fetcher)
            fetcher.start()

        self._hide_loading()

    def _preview_texturepack(self, data: bytes) -> None:
        """Preview a texture pack by showing all texture maps."""
        try:
            self._show_texturepack_preview(data)

        except (EOFError, OSError, RuntimeError, TypeError, ValueError, DefusedXmlException) as exc:
            self._show_text_preview(tr('json.preview.texturepack_error', error=exc))

    def _update_texturepack_texture(self, map_name: str, map_id: str, data: bytes) -> None:
        if map_name not in self._tp_image_labels:
            return

        img_label = self._tp_image_labels[map_name]
        try:
            _ = img_label.isVisible()
        except RuntimeError:
            return

        # Store texture data for context menu
        self._texturepack_data[map_name] = {'id': map_id, 'data': data}

        working = data
        if data[:2] == b'\x1f\x8b':
            try:
                working = gzip_module.decompress(data)
            except EOFError, OSError:
                pass

        image = Image.open(io.BytesIO(working))
        if image.mode not in {'RGB', 'RGBA'} or image.mode == 'RGB':
            image = image.convert('RGBA')

        # Scale up small images to 512x512 minimum
        min_size = 512
        if image.width < min_size or image.height < min_size:
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

    def _on_texturepack_texture_fetched(self, map_name: str, map_id: str, data: bytes) -> None:
        """Handle fetched texture data for a texture pack map."""
        try:
            self._update_texturepack_texture(map_name, map_id, data)

        except (OSError, RuntimeError, TypeError, ValueError, UnidentifiedImageError) as exc:
            self._on_texturepack_texture_error(map_name, str(exc))

    def _show_texturepack_texture_error(self, map_name: str, error: str) -> None:
        if map_name not in self._tp_image_labels:
            return
        img_label = self._tp_image_labels[map_name]
        try:
            _ = img_label.isVisible()
        except RuntimeError:
            return
        img_label.setText(tr('ui.gui.json_viewer.error_value', value0=error))
        img_label.setStyleSheet('color: #ff6b6b; padding: 10px;')

    def _on_texturepack_texture_error(self, map_name: str, error: str) -> None:
        """Handle texture load error for a texture pack map."""
        try:
            self._show_texturepack_texture_error(map_name, error)
        except RuntimeError:
            pass

    def _cleanup_texturepack(self) -> None:
        """Clean up texture pack state."""
        for fetcher in self._tp_fetchers:
            try:
                fetcher.stop()
                fetcher.quit()
                fetcher.wait()
            except RuntimeError:
                pass
        self._tp_fetchers = []

        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None
        self._texturepack_data = {}
        self._texturepack_xml = ''
        self._tp_image_labels = {}
        self._tp_pixmaps = {}

    # ──────────────────────────────────────────────────────────────────────
    # Context menus
    # ──────────────────────────────────────────────────────────────────────

    def _show_image_context_menu(self, pos: QPoint) -> None:
        """Show context menu for image preview."""
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        menu = QMenu(self)
        copy_action = menu.addAction(tr('ui.gui.json_viewer.copy_image'))

        action = menu.exec(self.image_label.mapToGlobal(pos))
        if action == copy_action:
            copy_pixmap_to_clipboard(self._current_pixmap)

    def _show_texturepack_context_menu(self, pos: QPoint, label: QLabel) -> None:
        """Show context menu for texturepack image."""
        map_name = _preserve_str(label.property('map_name'))
        map_id = label.property('map_id')

        menu = QMenu(self)

        copy_image_action = menu.addAction(tr('ui.gui.json_viewer.copy_image'))
        menu.addSeparator()
        copy_name_action = menu.addAction(tr('ui.gui.json_viewer.copy_name_value', value0=map_name))
        copy_id_action = menu.addAction(tr('ui.gui.json_viewer.copy_id_value', value0=map_id))
        menu.addSeparator()
        copy_xml_action = menu.addAction(tr('ui.gui.json_viewer.copy_texturepack_xml'))

        action = menu.exec(label.mapToGlobal(pos))

        if action == copy_image_action:
            pixmap = self._tp_pixmaps.get(map_name)
            if pixmap and not pixmap.isNull():
                copy_pixmap_to_clipboard(pixmap)
        elif action == copy_name_action:
            QApplication.clipboard().setText(map_name)
        elif action == copy_id_action:
            QApplication.clipboard().setText(str(map_id))
        elif action == copy_xml_action:
            QApplication.clipboard().setText(self._texturepack_xml)

    # ──────────────────────────────────────────────────────────────────────
    # Preview utilities
    # ──────────────────────────────────────────────────────────────────────

    def _show_text_preview(self, text: str) -> None:
        self._hide_loading()
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()
        self.stop_preview_btn.show()

    def _show_loading(self) -> None:
        self.loading_label.show()

    def _hide_loading(self) -> None:
        self.loading_label.hide()

    def _stop_preview(self) -> None:
        """Stop the current preview and hide the preview panel controls."""
        self._stop_all_loaders()
        self._hide_all_preview_widgets()
        try:
            self.rbxm_viewer.clear()
        except RuntimeError:
            pass
        self.preview_panel.hide()
        self._previewing_value = None
        try:
            self.preview_title_label.setText(tr('ui.gui.json_viewer.preview'))
        except RuntimeError:
            pass

    def _hide_all_preview_widgets(self) -> None:
        self.obj_viewer.hide()
        self.image_label.hide()
        self.audio_wrapper.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.rbxm_viewer.hide()
        self.font_wrapper.hide()
        self.loading_label.hide()
        self.stop_preview_btn.hide()
        self.rbxm_view_btn.hide()
        self._current_pixmap = None

        # Clean up texture pack
        self._cleanup_texturepack()

        # Remove audio key filter before cleaning up audio player
        self._remove_audio_key_filter()

        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None

    def _clear_preview(self) -> None:
        """Hide the preview panel and stop all loaders."""
        self._stop_preview()

    def _stop_all_loaders(self) -> None:
        for loader in (
            self._asset_fetcher,
            self._image_loader,
            self._mesh_loader,
            self._animation_loader,
            self._solidmodel_loader,
        ):
            if loader is not None:
                try:
                    loader.stop()
                    loader.quit()
                    loader.wait()
                except RuntimeError:
                    pass
        self._asset_fetcher = None
        self._image_loader = None
        self._mesh_loader = None
        self._animation_loader = None
        self._solidmodel_loader = None

        # Stop texturepack fetchers
        for fetcher in self._tp_fetchers:
            try:
                fetcher.stop()
                fetcher.quit()
                fetcher.wait()
            except RuntimeError:
                pass
        self._tp_fetchers = []

    def _on_splitter_moved(self, _pos: int, _index: int) -> None:
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def _handle_audio_key_event(self, event: QEvent) -> bool:
        if event.type() != QEvent.Type.KeyPress:
            return False
        key_event = _key_event(event)
        if (
            key_event.key() != Qt.Key.Key_Space
            or not self.audio_player
            or not self.audio_wrapper.isVisible()
        ):
            return False
        try:
            _toggle_audio_player(self.audio_player)
        except RuntimeError:
            pass
        return True

    @override
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        """Global event filter to catch space key and toggle audio play/pause."""
        try:
            if self._handle_audio_key_event(event):
                return True
        except RuntimeError, TypeError:
            pass
        return super().eventFilter(obj, event)

    def _remove_audio_key_filter(self) -> None:
        """Remove global audio key event filter if installed."""
        try:
            if self._audio_key_filter_installed:
                _require_application(QApplication.instance()).removeEventFilter(self)
                self._audio_key_filter_installed = False
        except RuntimeError:
            pass

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle dialog close - cleanup all resources including audio."""
        self._clear_preview()
        self._stop_all_loaders()
        super().closeEvent(event)

    def _on_search_text_changed(self) -> None:
        """Handle search text change with debounce."""
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Reset matches when search text changes
        self._search_matches = []
        self._current_match_index = 0
        self.match_label.setText('')
        # Disable navigation buttons until search completes
        self.prev_match_btn.setEnabled(False)
        self.next_match_btn.setEnabled(False)
        self._search_debounce.stop()
        self._search_debounce.start(400)  # 400ms debounce

    def _do_search(self) -> None:
        """Execute the actual search after debounce using worker thread."""
        query = self.search_input.text().strip()

        # Clear search if empty
        if not query:
            self.tree.clearSelection()
            self.search_progress_label.hide()
            self.match_label.setText('')
            self._search_matches = []
            self._current_match_index = 0
            return

        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Get all root items
        root_items: list[QTreeWidgetItem] = [
            _top_level_item(self.tree, i) for i in range(self.tree.topLevelItemCount())
        ]

        # Always use worker thread to prevent UI freezing
        self._is_searching = True
        self.search_progress_label.setText(tr('ui.gui.json_viewer.searching'))
        self.search_progress_label.show()

        self._search_worker = JsonSearchWorker(root_items, query)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.progress.connect(self._on_search_progress)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_progress(self, current: int, total: int) -> None:
        """Handle search progress update."""
        if total > 0:
            percent = int((current / total) * 100)
            self.search_progress_label.setText(
                tr(
                    'ui.gui.json_viewer.searching_value_value_value',
                    value0=percent,
                    value1=current,
                    value2=total,
                )
            )

    def _on_search_complete(self, matches: list[QTreeWidgetItem]) -> None:
        """Handle search results from worker thread."""
        # Store matches for cycling
        self._search_matches = matches
        self._current_match_index = 0

        # Enable/disable navigation buttons based on match count
        has_matches = len(matches) > 1
        self.prev_match_btn.setEnabled(has_matches)
        self.next_match_btn.setEnabled(has_matches)

        # Disable updates during selection
        self.tree.setUpdatesEnabled(False)

        try:
            # Clear selection
            self.tree.clearSelection()

            # Expand parents for all matches
            if matches:
                for item in matches:
                    # Expand parents
                    parent = item.parent()
                    while parent:
                        parent.setExpanded(True)
                        parent = parent.parent()

                # Select only first match
                matches[0].setSelected(True)
                self.tree.scrollToItem(matches[0])

            # Update labels
            self.search_progress_label.hide()
            if len(matches) > 1:
                self.match_label.setText(
                    tr('ui.gui.json_viewer.match_1_value_use_to_navigate', value0=len(matches))
                )
            elif len(matches) == 1:
                self.match_label.setText(tr('ui.gui.json_viewer.found_1_match'))
            else:
                self.match_label.setText(tr('ui.gui.json_viewer.no_matches_found'))

        finally:
            self.tree.setUpdatesEnabled(True)

    def _on_search_finished(self) -> None:
        """Handle search worker finished."""
        self._is_searching = False

    def _cycle_to_next_match(self) -> None:
        """Cycle to next search match."""
        if not self._search_matches or len(self._search_matches) <= 1:
            return

        # Move to next match (wrap around)
        self._current_match_index = (self._current_match_index + 1) % len(self._search_matches)
        self._select_current_match()

    def _cycle_to_prev_match(self) -> None:
        """Cycle to previous search match."""
        if not self._search_matches or len(self._search_matches) <= 1:
            return

        # Move to previous match (wrap around)
        self._current_match_index = (self._current_match_index - 1) % len(self._search_matches)
        self._select_current_match()

    def _select_current_match(self) -> None:
        """Select and scroll to the current match, updating the indicator."""
        self.tree.clearSelection()
        current_item = self._search_matches[self._current_match_index]
        current_item.setSelected(True)
        self.tree.scrollToItem(current_item)

        # Update match indicator with current position
        self.match_label.setText(
            tr(
                'ui.gui.json_viewer.match_value_value_use_to_navigate',
                value0=self._current_match_index + 1,
                value1=len(self._search_matches),
            )
        )

    def _expand_all(self) -> None:
        """Expand all items."""
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.expandAll()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _collapse_all(self) -> None:
        """Collapse all items."""
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.collapseAll()
        finally:
            self.tree.setUpdatesEnabled(True)

    def _show_replacer_notification(self, title: str, message: str) -> None:
        """Show the replacer success popup unless it is disabled in settings."""
        if self.config_manager is not None and not getattr(
            self.config_manager, 'show_replacer_notifications', True
        ):
            return

        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setInformativeText(tr('ui.gui.json_viewer.you_can_turn_this_off_in_settings'))
        dialog.setMinimumWidth(640)
        dialog.setStyleSheet(
            'QLabel#qt_msgbox_informativelabel { color: palette(window-text); font-size: 7pt; }'
        )
        dialog.setStandardButtons(QMessageBox.StandardButton.Ok)
        dialog.exec()

    def _maybe_close_after_replace(self) -> None:
        if self.config_manager is None or getattr(
            self.config_manager, 'close_viewer_on_replace', True
        ):
            self.close()

    def _import_as_replace_ids(self) -> None:
        """Import selected values as IDs to replace."""
        vals = self._get_selected_values()
        if vals:
            self.on_import_ids(vals)
            all_asset_ids = all(isinstance(v, int) for v in vals)
            preview = f'{", ".join(str(v) for v in vals[:5])}{"..." if len(vals) > 5 else ""}'
            if all_asset_ids:
                message = tr(
                    'json.replacer.added_one_asset_id'
                    if len(vals) == 1
                    else 'json.replacer.added_asset_ids',
                    count=len(vals),
                    preview=preview,
                )
            else:
                message = tr(
                    'json.replacer.added_one_value'
                    if len(vals) == 1
                    else 'json.replacer.added_values',
                    count=len(vals),
                    preview=preview,
                )
            self._show_replacer_notification(
                tr('json.replacer.added_title'),
                message,
            )
            self._maybe_close_after_replace()
        else:
            QMessageBox.information(
                self,
                tr('ui.gui.json_viewer.info'),
                tr('ui.gui.json_viewer.no_valid_values_selected_numeric_or_links'),
            )

    def _import_as_replacement(self) -> None:
        """Import selected value as replacement ID."""
        vals = self._get_selected_values()
        if not vals:
            QMessageBox.information(
                self,
                tr('ui.gui.json_viewer.info'),
                tr('ui.gui.json_viewer.no_valid_values_selected_numeric_or_links'),
            )
            return
        if len(vals) > 1:
            reply = QMessageBox.question(
                self,
                tr('ui.gui.json_viewer.multiple_values'),
                tr('ui.gui.json_viewer.only_the_first_value_value_will_be', value0=vals[0]),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        selected_value = vals[0]
        self.on_import_replacement(selected_value)
        if isinstance(selected_value, int):
            result_text = tr('json.replacer.replace_with_asset_id', value=selected_value)
        else:
            result_text = tr('json.replacer.replace_with_value', value=selected_value)
        self._show_replacer_notification(tr('json.replacer.replace_with_title'), result_text)
        self._maybe_close_after_replace()
