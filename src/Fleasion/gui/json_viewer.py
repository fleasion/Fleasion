"""JSON tree viewer widget."""

import gzip as gzip_module
import io

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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

from ..utils import get_icon_path


class JsonSearchWorker(QThread):
    """Worker thread for searching JSON tree without blocking UI."""

    results_ready = pyqtSignal(list)  # List of matching items
    progress = pyqtSignal(int, int)  # Current, total

    def __init__(self, root_items: list, query: str):
        super().__init__()
        self.root_items = root_items
        self.query = query.lower().strip()
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        """Search tree items in background."""
        if not self.query or self._stop_requested:
            return

        matches = []
        total_items = 0

        # First, count total items for progress
        def count_items(item):
            count = 1
            for i in range(item.childCount()):
                count += count_items(item.child(i))
            return count

        for root_item in self.root_items:
            total_items += count_items(root_item)

        # Now search with progress reporting
        processed = 0
        batch_size = 50  # Report progress every 50 items

        def search_item(item):
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
            for i in range(item.childCount()):
                if not search_item(item.child(i)):
                    return False

            return True

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

    data_ready = pyqtSignal(bytes)
    error = pyqtSignal(str)

    # Class-level scraper reference — set once by ProxyMaster/app startup.
    # Avoids threading it through every call site (replacer_config has no scraper ref).
    _scraper = None

    @classmethod
    def set_scraper(cls, scraper) -> None:
        """Called by ProxyMaster after the scraper is ready."""
        cls._scraper = scraper

    def __init__(self, asset_id_or_url):
        super().__init__()
        self._asset = asset_id_or_url
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def _get_roblosecurity(self) -> str | None:
        """Get .ROBLOSECURITY cookie from Roblox local storage."""
        import os
        import json
        import base64
        import re

        try:
            import win32crypt
        except ImportError:
            return None

        path = os.path.expandvars(r'%LocalAppData%/Roblox/LocalStorage/RobloxCookies.dat')
        try:
            if not os.path.exists(path):
                return None
            with open(path, 'r') as f:
                data = json.load(f)
            cookies_data = data.get('CookiesData')
            if not cookies_data:
                return None
            enc = base64.b64decode(cookies_data)
            dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
            s = dec.decode(errors='ignore')
            m = re.search(r'\.ROBLOSECURITY\s+([^\s;]+)', s)
            return m.group(1) if m else None
        except Exception:
            return None

    def run(self):
        try:
            val = self._asset
            scraper = self.__class__._scraper

            if isinstance(val, int) or (isinstance(val, str) and str(val).strip().lstrip('-').isdigit()):
                cookie = self._get_roblosecurity()
                extra = {'Cookie': f'.ROBLOSECURITY={cookie};' } if cookie else None
                status = None

                if scraper is not None:
                    data, status = scraper._fetch_asset_with_place_id_retry(
                        str(val), extra_headers=extra,
                    )
                else:
                    import requests as _req
                    headers = {'User-Agent': 'Roblox/WinInet', 'Accept-Encoding': 'gzip, deflate'}
                    if extra:
                        headers.update(extra)
                    r = _req.get(f'https://assetdelivery.roblox.com/v1/asset/?id={val}',
                                 headers=headers, timeout=15)
                    status = r.status_code
                    data = r.content if r.status_code == 200 else None
                    # Attempt place-ID retry on 403 via inline logic
                    if data is None and r.status_code == 403:
                        try:
                            info_r = _req.get(
                                f'https://develop.roblox.com/v1/assets?assetIds={val}',
                                headers={'Accept': 'application/json',
                                         **(extra or {})},
                                timeout=10,
                            )
                            if info_r.status_code == 200:
                                items = info_r.json().get('data', [])
                                if items:
                                    cr = items[0].get('creator') or {}
                                    cid = cr.get('targetId') or items[0].get('creatorTargetId')
                                    ctype = cr.get('typeId') or items[0].get('creatorType')
                                    if cid is not None and ctype is not None:
                                        cid, ctype = int(cid), int(ctype)
                                        g_paths = ([f'/v2/users/{cid}/games?sortOrder=Asc&limit=100']
                                                    if ctype == 1 else
                                                    [f'/v2/groups/{cid}/gamesV2?accessFilter=2&limit=100&sortOrder=Asc',
                                                     f'/v2/groups/{cid}/gamesV2?accessFilter=1&limit=100&sortOrder=Asc'])
                                        seen_pids = set()
                                        for g_path in g_paths:
                                            g_r = _req.get(f'https://games.roblox.com{g_path}',
                                                           headers={'Accept': 'application/json'},
                                                           timeout=10)
                                            if g_r.status_code == 200:
                                                games = g_r.json().get('data', [])
                                                for game in games:
                                                    rp = game.get('rootPlace')
                                                    if rp and rp.get('id'):
                                                        pid = int(rp['id'])
                                                        if pid in seen_pids:
                                                            continue
                                                        seen_pids.add(pid)
                                                        retry_h = {**headers, 'Roblox-Place-Id': str(pid)}
                                                        r2 = _req.get(
                                                            f'https://assetdelivery.roblox.com/v1/asset/?id={val}',
                                                            headers=retry_h, timeout=15)
                                                        status = r2.status_code
                                                        if r2.status_code == 200 and r2.content:
                                                            data = r2.content
                                                            break  # Found working place ID
                                            if data:
                                                break  # Stop trying paths
                        except Exception:
                            pass

                if self._stop_requested:
                    return
                if data:
                    self.data_ready.emit(data)
                elif status == 404:
                    self.error.emit('Asset not found (deleted or invalid ID)')
                elif status == 403:
                    self.error.emit('Asset is privated (could not bypass)')
                else:
                    self.error.emit('No data returned (Asset may be deleted or privated)')

            elif isinstance(val, str) and (val.startswith('http://') or val.startswith('https://')):
                from urllib.parse import urlparse as _up
                parsed = _up(val)
                hostname = (parsed.hostname or '').lower()
                path = parsed.path + ('?' + parsed.query if parsed.query else '')
                is_roblox = 'roblox.com' in hostname

                cookie = self._get_roblosecurity() if is_roblox else None
                extra = {'Cookie': f'.ROBLOSECURITY={cookie};'} if cookie else None

                if scraper is not None and is_roblox:
                    data = scraper._https_get(hostname, path, extra_headers=extra)
                else:
                    import requests as _req
                    headers = {'User-Agent': 'Roblox/WinInet', 'Accept-Encoding': 'gzip, deflate'}
                    if extra:
                        headers.update(extra)
                    r = _req.get(val, headers=headers, timeout=15)
                    data = r.content if r.status_code == 200 else None

                if self._stop_requested:
                    return
                if data:
                    self.data_ready.emit(data)
                else:
                    self.error.emit('No data returned (Asset may be deleted or privated)')
            else:
                self.error.emit(f'Cannot fetch: {val}')
        except Exception as e:
            if not self._stop_requested:
                self.error.emit(str(e))


class ImageLoaderThread(QThread):
    """Load image bytes into a QPixmap in a background thread."""

    image_ready = pyqtSignal(QPixmap)
    error = pyqtSignal(str)

    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        from PIL import Image

        try:
            image = Image.open(io.BytesIO(self.data))
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            elif image.mode == 'RGB':
                image = image.convert('RGBA')
            if self._stop_requested:
                return
            qimage = QImage(
                image.tobytes(),
                image.width,
                image.height,
                QImage.Format.Format_RGBA8888,
            )
            pixmap = QPixmap.fromImage(qimage)
            if not self._stop_requested:
                self.image_ready.emit(pixmap)
        except Exception as e:
            if not self._stop_requested:
                self.error.emit(str(e))


class MeshLoaderThread(QThread):
    """Convert raw mesh bytes to OBJ string in a background thread."""

    mesh_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        from ..cache import mesh_processing

        try:
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
            if self._stop_requested:
                return
            obj_content = mesh_processing.convert(decompressed)
            if self._stop_requested:
                return
            if obj_content:
                self.mesh_ready.emit(obj_content)
            else:
                self.error.emit('Failed to convert mesh to OBJ format')
        except Exception as e:
            if not self._stop_requested:
                self.error.emit(str(e))


class SolidModelLoaderThread(QThread):
    """Convert raw SolidModel (CSG) bytes to OBJ string in a background thread."""

    mesh_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            import tempfile
            from pathlib import Path
            from ..cache.tools.solidmodel_converter.converter import deserialize_rbxm, _export_obj_from_doc

            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)

            if self._stop_requested:
                return

            doc = deserialize_rbxm(decompressed)

            with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as f:
                temp_obj_path = Path(f.name)

            try:
                _export_obj_from_doc(doc, temp_obj_path, decompose=False)
                obj_content = temp_obj_path.read_text(encoding='utf-8')
            finally:
                if temp_obj_path.exists():
                    temp_obj_path.unlink()

            if self._stop_requested:
                return

            if obj_content:
                self.mesh_ready.emit(obj_content)
            else:
                self.error.emit('Failed to convert SolidModel to OBJ format')
        except Exception as e:
            if not self._stop_requested:
                self.error.emit(str(e))


class JsonTreeViewer(QDialog):
    """JSON tree viewer dialog."""

    def __init__(
        self, parent, data, filename: str, on_import_ids, on_import_replacement,
        config_manager=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.setWindowTitle(f'JSON - {filename}')
        self.resize(1200, 650)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self.data = data
        self.on_import_ids = on_import_ids
        self.on_import_replacement = on_import_replacement
        self.node_values = {}
        self.node_is_leaf = {}

        # Search worker
        self._search_worker: JsonSearchWorker | None = None
        self._is_searching = False
        self._search_matches: list[QTreeWidgetItem] = []
        self._current_match_index: int = 0

        # Preview state
        self._asset_fetcher: AssetFetcherThread | None = None
        self._image_loader: ImageLoaderThread | None = None
        self._mesh_loader: MeshLoaderThread | None = None
        self._animation_loader = None
        self._solidmodel_loader: SolidModelLoaderThread | None = None
        self._current_pixmap: QPixmap | None = None
        self._previewing_value = None  # track what we started previewing (stale guard)
        self._audio_key_filter_installed = False  # Track if global audio key filter is installed
        self._last_fetched_data: bytes | None = None  # raw bytes for solidmodel fallback

        # Texturepack state
        self.texturepack_widget = None
        self._texturepack_data: dict = {}  # map_name -> {id, data}
        self._texturepack_xml: str = ''
        self._tp_image_labels: dict = {}
        self._tp_pixmaps: dict = {}
        self._tp_fetchers: list = []  # active AssetFetcherThread instances

        self._setup_ui()
        self._populate_tree()
        self._set_icon()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
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
        search_layout.addWidget(QLabel('Search:'))
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self.search_input)

        # Navigation buttons for cycling through matches
        self.prev_match_btn = QPushButton('↑')
        self.prev_match_btn.setFixedWidth(30)
        self.prev_match_btn.setToolTip('Previous match')
        self.prev_match_btn.clicked.connect(self._cycle_to_prev_match)
        self.prev_match_btn.setEnabled(False)
        search_layout.addWidget(self.prev_match_btn)

        self.next_match_btn = QPushButton('↓')
        self.next_match_btn.setFixedWidth(30)
        self.next_match_btn.setToolTip('Next match')
        self.next_match_btn.clicked.connect(self._cycle_to_next_match)
        self.next_match_btn.setEnabled(False)
        search_layout.addWidget(self.next_match_btn)

        clear_btn = QPushButton('Clear')
        clear_btn.clicked.connect(lambda: self.search_input.clear())
        search_layout.addWidget(clear_btn)

        expand_btn = QPushButton('Expand All')
        expand_btn.clicked.connect(self._expand_all)
        search_layout.addWidget(expand_btn)

        collapse_btn = QPushButton('Collapse All')
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

        # Selection label + match navigation indicator
        selection_row = QHBoxLayout()
        self.selection_label = QLabel('Selected: 0 values')
        selection_row.addWidget(self.selection_label)
        self.match_label = QLabel('')
        self.match_label.setStyleSheet('color: #888; font-size: 11px;')
        selection_row.addWidget(self.match_label)
        selection_row.addStretch()
        layout.addLayout(selection_row)

        # Import buttons
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QLabel('Import selected as:'))

        ids_btn = QPushButton('IDs to Replace')
        ids_btn.clicked.connect(self._import_as_replace_ids)
        btn_layout.addWidget(ids_btn)

        repl_btn = QPushButton('Replacement ID')
        repl_btn.clicked.connect(self._import_as_replacement)
        btn_layout.addWidget(repl_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _add_node(self, parent_item, key: str, value) -> QTreeWidgetItem:
        """Add a node to the tree."""
        if isinstance(value, (dict, list)):
            items = value.items() if isinstance(value, dict) else enumerate(value)
            fmt = '{...}' if isinstance(value, dict) else '[...]'
            display = f'{key}: {fmt}' if key else fmt
            item = QTreeWidgetItem(parent_item, [display])
            item.setExpanded(False)
            self.node_is_leaf[id(item)] = False
            for k, v in items:
                self._add_node(item, f'[{k}]' if isinstance(value, list) else k, v)
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

    def _populate_tree(self):
        """Populate the tree with data."""
        self.tree.clear()
        if isinstance(self.data, (dict, list)):
            items = (
                self.data.items() if isinstance(self.data, dict) else enumerate(self.data)
            )
            for k, v in items:
                self._add_node(
                    self.tree, f'[{k}]' if isinstance(self.data, list) else k, v
                )
        else:
            self._add_node(self.tree, '', self.data)

    def _get_all_leaf_descendants(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        """Get all leaf descendants of an item."""
        if self.node_is_leaf.get(id(item)):
            return [item]
        leaves = []
        for i in range(item.childCount()):
            leaves.extend(self._get_all_leaf_descendants(item.child(i)))
        return leaves

    def _is_link_or_path(self, value: str) -> bool:
        """Check if a string is a link or file path."""
        if not isinstance(value, str):
            return False
        value = value.strip()
        # Check for URLs
        if value.startswith(('http://', 'https://', 'ftp://', 'file://')):
            return True
        # Check for absolute paths (Unix and Windows)
        if value.startswith('/') or (len(value) > 2 and value[1] == ':'):
            return True
        # Check for relative paths with directory separators
        if '/' in value or '\\' in value:
            return True
        return False

    def _get_selected_values(self) -> list[int | str]:
        """Get numeric values and links/file paths from selected items."""
        leaves = []
        leaf_ids = set()  # Track IDs to avoid duplicates

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
            if isinstance(val, bool):
                continue
            # Try to parse as integer first
            try:
                values.append(int(val))
            except (ValueError, TypeError):
                # Check if it's a link or file path
                if self._is_link_or_path(val):
                    values.append(val)
        return values

    def _on_selection_change(self):
        """Handle selection change and trigger asset preview."""
        vals = self._get_selected_values()
        self.selection_label.setText(f'Selected: {len(vals)} value(s)')

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
        from ..cache.animation_viewer import AnimationViewerPanel
        from ..cache.audio_player import AudioPlayerWidget  # noqa: F401 - used dynamically
        from ..cache.cache_json_viewer import CacheJsonViewer
        from ..cache.obj_viewer import ObjViewerPanel
        from ..cache.font_viewer import FontViewerWidget  # noqa: F401 - used dynamically

        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        self.preview_group = QGroupBox('Preview')
        preview_group_layout = QVBoxLayout()

        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.preview_container = QWidget()
        self.preview_container_layout = QVBoxLayout()
        self.preview_container_layout.setContentsMargins(5, 5, 5, 5)

        # 3D viewer for meshes
        self.obj_viewer = ObjViewerPanel(config_manager=self.config_manager)
        self.obj_viewer.clear_requested.connect(self._clear_preview)
        self.preview_container_layout.addWidget(self.obj_viewer)

        # Loading indicator
        self.loading_label = QLabel('Loading...')
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet(
            'QLabel { background-color: palette(base); color: #888; font-size: 14px; padding: 20px; }'
        )
        self.preview_container_layout.addWidget(self.loading_label)
        self.loading_label.hide()

        # Image viewer
        self.image_label = QLabel('Select a single asset ID or URL to preview')
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: palette(base); color: #888; }')
        self.image_label.setScaledContents(False)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_image_context_menu)
        self.preview_container_layout.addWidget(self.image_label)

        # Audio player container with centering wrapper
        self.audio_player = None
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
        self.animation_viewer = AnimationViewerPanel(config_manager=self.config_manager)
        self.preview_container_layout.addWidget(self.animation_viewer)

        # Text viewer (hex dump / plain text)
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText('No preview available')
        self.preview_container_layout.addWidget(self.text_viewer)

        # JSON viewer
        self.json_viewer = CacheJsonViewer()
        self.preview_container_layout.addWidget(self.json_viewer)

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
        self.font_wrapper.hide()

        self.preview_container.setLayout(self.preview_container_layout)
        self.preview_scroll.setWidget(self.preview_container)
        preview_group_layout.addWidget(self.preview_scroll)

        self.preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(self.preview_group)
        preview_widget.setLayout(preview_layout)
        return preview_widget

    # ──────────────────────────────────────────────────────────────────────
    # Preview orchestration
    # ──────────────────────────────────────────────────────────────────────

    def _preview_value(self, val):
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
            self.preview_group.setTitle(f'Preview: {display}')
        except Exception:
            pass

        self._asset_fetcher = AssetFetcherThread(val)
        self._asset_fetcher.data_ready.connect(self._on_asset_fetched)
        self._asset_fetcher.error.connect(self._on_fetch_error)
        self._asset_fetcher.start()

    def _on_fetch_error(self, error: str):
        self._show_text_preview(f'Failed to fetch asset:\n{error}')

    def _on_asset_fetched(self, data: bytes):
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
        elif content_type in ('rbxm', 'rbxmx'):
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
            except Exception:
                pass

        # Images
        if working[:4] == b'\x89PNG':
            return 'image'
        if working[:2] == b'\xff\xd8':
            return 'image'
        if working[:4] == b'RIFF' and working[8:12] == b'WEBP':
            return 'image'
        if working[:3] == b'GIF':
            return 'image'

        # Audio
        if working[:4] == b'OggS':
            return 'audio'
        if working[:3] == b'ID3' or working[:2] in (b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'):
            return 'audio'

        # Fonts (TrueType/OpenType)
        if working[:4] == b'\x00\x01\x00\x00':  # TrueType
            return 'font'
        if working[:4] == b'OTTO':  # OpenType (CFF-based)
            return 'font'
        if working[:4] == b'ttcf':  # TrueType Collection
            return 'font'
        if working[:2] == b'\x01\x00':  # Alternative TrueType magic
            return 'font'

        # Roblox mesh (starts with "version")
        if working[:7] == b'version':
            return 'mesh'

        # RBXM binary
        if working[:8] == b'<roblox!':
            return 'rbxm'

        # XML (RBXMX / animation / texturepack)
        if working[:7] == b'<roblox' or working[:5] == b'<?xml':
            # Check for texturepack XML (has color/normal/metalness/roughness/emissive elements)
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(working)
                tp_elems = ['color', 'normal', 'metalness', 'roughness', 'emissive']
                if any(root.find(e) is not None for e in tp_elems):
                    return 'texturepack'
            except Exception:
                pass
            return 'rbxmx'

        # JSON
        try:
            stripped = working.lstrip()
            if stripped[:1] in (b'{', b'['):
                import json
                json.loads(working.decode('utf-8'))
                return 'json'
        except Exception:
            pass

        return 'unknown'

    # ──────────────────────────────────────────────────────────────────────
    # Per-type preview handlers
    # ──────────────────────────────────────────────────────────────────────

    def _preview_image(self, data: bytes):
        self._image_loader = ImageLoaderThread(data)
        self._image_loader.image_ready.connect(self._on_image_ready)
        self._image_loader.error.connect(lambda e: self._show_text_preview(f'Image error: {e}'))
        self._image_loader.start()

    def _on_image_ready(self, pixmap: QPixmap):
        self._hide_loading()
        self._current_pixmap = pixmap
        self._scale_and_show_image(pixmap)
        self.image_label.show()

    def _scale_and_show_image(self, pixmap: QPixmap):
        container_w = self.preview_scroll.viewport().width() - 20
        container_h = self.preview_scroll.viewport().height() - 20
        if container_w < 100:
            container_w = 400
        if container_h < 100:
            container_h = 400
        scaled = pixmap.scaled(
            container_w, container_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def _preview_mesh(self, data: bytes):
        self._mesh_loader = MeshLoaderThread(data)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)
        self._mesh_loader.error.connect(lambda e: self._show_text_preview(f'Mesh error: {e}'))
        self._mesh_loader.start()

    def _on_mesh_ready(self, obj_content: str):
        self._hide_loading()
        self.obj_viewer.load_obj(obj_content, '')
        self.obj_viewer.show()

    def _preview_audio(self, data: bytes):
        import tempfile
        from pathlib import Path

        from ..cache.audio_player import AudioPlayerWidget

        try:
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_audio'
            temp_dir.mkdir(exist_ok=True)
            temp_file = temp_dir / f'preview_{id(self)}.mp3'
            with open(temp_file, 'wb') as f:
                f.write(data)

            self.audio_player = AudioPlayerWidget(str(temp_file), self, self.config_manager)

            while self.audio_container_layout.count():
                child = self.audio_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            self.audio_container_layout.addWidget(self.audio_player)
            self._hide_loading()
            self.audio_wrapper.show()

            # Install global event filter to catch Space for play/pause while audio preview is active
            try:
                from PyQt6.QtWidgets import QApplication
                QApplication.instance().installEventFilter(self)
                self._audio_key_filter_installed = True
            except Exception:
                self._audio_key_filter_installed = False

            # When audio stops or widget is deleted, remove the event filter
            try:
                self.audio_player.stopped.connect(lambda: self._remove_audio_key_filter())
            except Exception:
                pass

        except Exception as e:
            self._show_text_preview(f'Audio error: {e}')

    def _preview_font(self, data: bytes):
        """Preview a font asset (TTF, OTF, TTC)."""
        from ..cache.font_viewer import FontViewerWidget

        try:
            font_viewer = FontViewerWidget(data, self)
            
            # Clear previous font widgets
            while self.font_container_layout.count():
                child = self.font_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            
            # Add new font viewer
            self.font_container_layout.addWidget(font_viewer)
            self._hide_loading()
            self.font_wrapper.show()
            
        except Exception as e:
            self._show_text_preview(f'Font error: {e}')

    def _preview_animation(self, data: bytes):
        """Preview RBXM/RBXMX animation data."""
        try:
            decompressed = data
            if data[:2] == b'\x1f\x8b':
                decompressed = gzip_module.decompress(data)

            if self.animation_viewer.load_animation(decompressed):
                self._hide_loading()
                self.animation_viewer.show()
                return

            # Binary RBXM that isn't an animation — try SolidModel
            if decompressed[:8] == b'<roblox!':
                self._preview_solidmodel(data)
                return

            # Fallback: pretty-print XML
            text = decompressed.decode('utf-8', errors='replace')
            if text.strip().startswith('<'):
                try:
                    import xml.dom.minidom
                    dom = xml.dom.minidom.parseString(decompressed)
                    pretty = dom.toprettyxml(indent='  ')
                    lines = [ln for ln in pretty.split('\n') if ln.strip()]
                    self._show_text_preview('\n'.join(lines[:500]))
                    return
                except Exception:
                    pass
            self._preview_hex(decompressed)
        except Exception as e:
            self._show_text_preview(f'Animation error: {e}')

    def _preview_json(self, data: bytes):
        """Preview JSON data in the embedded JSON viewer."""
        try:
            working = data
            if data[:2] == b'\x1f\x8b':
                working = gzip_module.decompress(data)
            import json
            parsed = json.loads(working.decode('utf-8'))
            self.json_viewer.load_json(parsed)
            self._hide_loading()
            self.json_viewer.show()
        except Exception as e:
            self._show_text_preview(f'JSON error: {e}')

    def _preview_hex(self, data: bytes):
        """Show a hex dump for unrecognised content."""
        preview_size = min(1024, len(data))
        lines = [f'Size: {len(data)} bytes\n\nFirst {preview_size} bytes (hex dump):\n']
        for i in range(0, preview_size, 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i:i + 16])
            ascii_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in data[i:i + 16])
            lines.append(f'{i:08x}  {hex_part:<48}  {ascii_part}')
        if len(data) > preview_size:
            lines.append(f'\n... ({len(data) - preview_size} more bytes)')
        self._show_text_preview('\n'.join(lines))

    def _preview_solidmodel(self, data: bytes):
        """Preview a SolidModel (CSG) asset in 3D using background thread."""
        self._solidmodel_loader = SolidModelLoaderThread(data)
        self._solidmodel_loader.mesh_ready.connect(self._on_mesh_ready)
        self._solidmodel_loader.error.connect(
            lambda e: self._show_text_preview(f'SolidModel error: {e}')
        )
        self._solidmodel_loader.start()

    def _preview_texturepack(self, data: bytes):
        """Preview a texture pack by showing all texture maps."""
        import xml.etree.ElementTree as ET

        try:
            # Clean up previous texture pack if any
            self._cleanup_texturepack()

            # Parse XML to get texture map IDs
            working = data
            if data[:2] == b'\x1f\x8b':
                working = gzip_module.decompress(data)

            xml_text = working.decode('utf-8', errors='replace')
            self._texturepack_xml = xml_text
            root = ET.fromstring(xml_text)

            # Extract texture map IDs in order
            map_order = ['color', 'normal', 'metalness', 'roughness', 'emissive']
            maps = {}
            for elem in map_order:
                node = root.find(elem)
                if node is not None and node.text:
                    maps[elem.capitalize()] = node.text

            if not maps:
                self._show_text_preview('No texture maps found in texture pack')
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
                header = QLabel(f'{map_name}  |  {map_id}')
                header.setStyleSheet('font-weight: bold; color: #888; padding: 5px;')
                header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                tp_layout.addWidget(header)

                img_label = QLabel('Loading...')
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_label.setStyleSheet('background-color: palette(base); padding: 10px; min-height: 100px;')
                img_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
                img_label.setProperty('map_name', map_name)
                img_label.setProperty('map_id', map_id)
                img_label.customContextMenuRequested.connect(
                    lambda pos, lbl=img_label: self._show_texturepack_context_menu(pos, lbl)
                )
                tp_layout.addWidget(img_label)
                self._tp_image_labels[map_name] = img_label

            tp_layout.addStretch()
            self.texturepack_widget.setLayout(tp_layout)
            self.preview_container_layout.addWidget(self.texturepack_widget)
            self.texturepack_widget.show()

            # Fetch each texture map via network
            self._tp_fetchers = []
            for map_name, map_id in maps.items():
                fetcher = AssetFetcherThread(map_id)
                fetcher.data_ready.connect(
                    lambda d, mn=map_name, mid=map_id: self._on_texturepack_texture_fetched(mn, mid, d)
                )
                fetcher.error.connect(
                    lambda e, mn=map_name: self._on_texturepack_texture_error(mn, e)
                )
                self._tp_fetchers.append(fetcher)
                fetcher.start()

            self._hide_loading()

        except Exception as e:
            self._show_text_preview(f'Texture pack preview error: {e}')

    def _on_texturepack_texture_fetched(self, map_name: str, map_id: str, data: bytes):
        """Handle fetched texture data for a texture pack map."""
        from PIL import Image

        try:
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
                except Exception:
                    pass

            image = Image.open(io.BytesIO(working))
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            elif image.mode == 'RGB':
                image = image.convert('RGBA')

            # Scale up small images to 512x512 minimum
            min_size = 512
            if image.width < min_size or image.height < min_size:
                scale_factor = max(min_size / image.width, min_size / image.height)
                new_width = int(image.width * scale_factor)
                new_height = int(image.height * scale_factor)
                image = image.resize((new_width, new_height), Image.Resampling.NEAREST)

            qimage = QImage(
                image.tobytes(), image.width, image.height,
                QImage.Format.Format_RGBA8888,
            )
            pixmap = QPixmap.fromImage(qimage)
            self._tp_pixmaps[map_name] = pixmap

            # Scale to fit container
            container_width = self.preview_scroll.viewport().width() - 30
            if container_width < 100:
                container_width = 400

            if pixmap.width() > container_width:
                scaled = pixmap.scaledToWidth(container_width, Qt.TransformationMode.SmoothTransformation)
            else:
                scaled = pixmap

            img_label.setPixmap(scaled)
            img_label.setStyleSheet('')

        except Exception as e:
            self._on_texturepack_texture_error(map_name, str(e))

    def _on_texturepack_texture_error(self, map_name: str, error: str):
        """Handle texture load error for a texture pack map."""
        try:
            if map_name not in self._tp_image_labels:
                return
            img_label = self._tp_image_labels[map_name]
            try:
                _ = img_label.isVisible()
            except RuntimeError:
                return
            img_label.setText(f'Error: {error}')
            img_label.setStyleSheet('color: #ff6b6b; padding: 10px;')
        except Exception:
            pass

    def _cleanup_texturepack(self):
        """Clean up texture pack state."""
        for fetcher in self._tp_fetchers:
            try:
                fetcher.stop()
                fetcher.quit()
                fetcher.wait()
            except Exception:
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

    def _show_image_context_menu(self, pos):
        """Show context menu for image preview."""
        if self._current_pixmap is None or self._current_pixmap.isNull():
            return

        from PyQt6.QtWidgets import QApplication

        menu = QMenu(self)
        copy_action = menu.addAction('Copy Image')

        action = menu.exec(self.image_label.mapToGlobal(pos))
        if action == copy_action:
            QApplication.clipboard().setPixmap(self._current_pixmap)

    def _show_texturepack_context_menu(self, pos, label: QLabel):
        """Show context menu for texturepack image."""
        from PyQt6.QtWidgets import QApplication

        map_name = label.property('map_name')
        map_id = label.property('map_id')

        menu = QMenu(self)

        copy_image_action = menu.addAction('Copy Image')
        menu.addSeparator()
        copy_name_action = menu.addAction(f'Copy Name ({map_name})')
        copy_id_action = menu.addAction(f'Copy ID ({map_id})')
        menu.addSeparator()
        copy_xml_action = menu.addAction('Copy TexturePack XML')

        action = menu.exec(label.mapToGlobal(pos))

        if action == copy_image_action:
            pixmap = self._tp_pixmaps.get(map_name)
            if pixmap and not pixmap.isNull():
                QApplication.clipboard().setPixmap(pixmap)
        elif action == copy_name_action:
            QApplication.clipboard().setText(map_name)
        elif action == copy_id_action:
            QApplication.clipboard().setText(str(map_id))
        elif action == copy_xml_action:
            QApplication.clipboard().setText(self._texturepack_xml)

    # ──────────────────────────────────────────────────────────────────────
    # Preview utilities
    # ──────────────────────────────────────────────────────────────────────

    def _show_text_preview(self, text: str):
        self._hide_loading()
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()

    def _show_loading(self):
        self.loading_label.show()

    def _hide_loading(self):
        self.loading_label.hide()

    def _hide_all_preview_widgets(self):
        self.obj_viewer.hide()
        self.image_label.hide()
        self.audio_wrapper.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()
        self.json_viewer.hide()
        self.font_wrapper.hide()
        self.loading_label.hide()
        self._current_pixmap = None

        # Clean up texture pack
        self._cleanup_texturepack()

        # Remove audio key filter before cleaning up audio player
        self._remove_audio_key_filter()

        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None

    def _clear_preview(self):
        """Hide the preview panel and stop all loaders."""
        self._stop_all_loaders()
        self._hide_all_preview_widgets()
        self.preview_panel.hide()
        self._previewing_value = None
        try:
            self.preview_group.setTitle('Preview')
        except Exception:
            pass

    def _stop_all_loaders(self):
        for loader in (self._asset_fetcher, self._image_loader, self._mesh_loader,
                       self._animation_loader, self._solidmodel_loader):
            if loader is not None:
                try:
                    loader.stop()
                    loader.quit()
                    loader.wait()
                except Exception:
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
            except Exception:
                pass
        self._tp_fetchers = []

    def _on_splitter_moved(self, pos: int, index: int):
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def eventFilter(self, obj, event):
        """Global event filter to catch space key and toggle audio play/pause."""
        try:
            from PyQt6.QtCore import QEvent
            if event.type() == QEvent.Type.KeyPress:
                # Space toggles play/pause when audio preview is active
                if event.key() == Qt.Key.Key_Space:
                    if self.audio_player and self.audio_wrapper.isVisible():
                        try:
                            # Toggle play/pause on the audio widget
                            self.audio_player._toggle_play_pause()
                        except Exception:
                            pass
                        return True
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def _remove_audio_key_filter(self):
        """Remove global audio key event filter if installed."""
        try:
            if self._audio_key_filter_installed:
                from PyQt6.QtWidgets import QApplication
                QApplication.instance().removeEventFilter(self)
                self._audio_key_filter_installed = False
        except Exception:
            pass

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def _on_search_text_changed(self):
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

    def _do_search(self):
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
        root_items = []
        for i in range(self.tree.topLevelItemCount()):
            root_items.append(self.tree.topLevelItem(i))

        # Always use worker thread to prevent UI freezing
        self._is_searching = True
        self.search_progress_label.setText('Searching...')
        self.search_progress_label.show()

        self._search_worker = JsonSearchWorker(root_items, query)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.progress.connect(self._on_search_progress)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_progress(self, current: int, total: int):
        """Handle search progress update."""
        if total > 0:
            percent = int((current / total) * 100)
            self.search_progress_label.setText(f'Searching... {percent}% ({current:,}/{total:,})')

    def _on_search_complete(self, matches: list):
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
                self.match_label.setText(f'Match 1/{len(matches)} - Use ↑↓ to navigate')
            elif len(matches) == 1:
                self.match_label.setText('Found 1 match')
            else:
                self.match_label.setText('No matches found')

        finally:
            self.tree.setUpdatesEnabled(True)

    def _on_search_finished(self):
        """Handle search worker finished."""
        self._is_searching = False

    def _cycle_to_next_match(self):
        """Cycle to next search match."""
        if not self._search_matches or len(self._search_matches) <= 1:
            return

        # Move to next match (wrap around)
        self._current_match_index = (self._current_match_index + 1) % len(self._search_matches)
        self._select_current_match()

    def _cycle_to_prev_match(self):
        """Cycle to previous search match."""
        if not self._search_matches or len(self._search_matches) <= 1:
            return

        # Move to previous match (wrap around)
        self._current_match_index = (self._current_match_index - 1) % len(self._search_matches)
        self._select_current_match()

    def _select_current_match(self):
        """Select and scroll to the current match, updating the indicator."""
        self.tree.clearSelection()
        current_item = self._search_matches[self._current_match_index]
        current_item.setSelected(True)
        self.tree.scrollToItem(current_item)

        # Update match indicator with current position
        self.match_label.setText(
            f'Match {self._current_match_index + 1}/{len(self._search_matches)} - Use ↑↓ to navigate'
        )

    def _expand_all(self):
        """Expand all items."""
        self.tree.expandAll()

    def _collapse_all(self):
        """Collapse all items."""
        self.tree.collapseAll()

    def _import_as_replace_ids(self):
        """Import selected values as IDs to replace."""
        vals = self._get_selected_values()
        if vals:
            self.on_import_ids(vals)
            self.accept()
        else:
            QMessageBox.information(self, 'Info', 'No valid values selected (numeric or links/paths)')

    def _import_as_replacement(self):
        """Import selected value as replacement ID."""
        vals = self._get_selected_values()
        if not vals:
            QMessageBox.information(self, 'Info', 'No valid values selected (numeric or links/paths)')
            return
        if len(vals) > 1:
            reply = QMessageBox.question(
                self,
                'Multiple Values',
                f'Only the first value ({vals[0]}) will be used. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.on_import_replacement(vals[0])
        self.accept()
