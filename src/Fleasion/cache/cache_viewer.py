"""Cache viewer tab - simplified version for viewing cached assets."""

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QLabel, QComboBox, QLineEdit, QMessageBox,
    QHeaderView, QFileDialog, QGroupBox, QSplitter, QTextEdit, QCheckBox,
    QMenu, QScrollArea
)
from PyQt6.QtGui import QPixmap, QImage
from PIL import Image
import io
import threading
import gzip as gzip_module

from .cache_manager import CacheManager
from .obj_viewer import ObjViewerPanel
from .audio_player import AudioPlayerWidget
from .animation_viewer import AnimationViewerPanel
from . import mesh_processing
from ..utils import log_buffer, open_folder


class SearchWorkerThread(QThread):
    '''Worker thread for filtering assets without blocking UI.'''

    results_ready = pyqtSignal(list)

    def __init__(self, assets: list, search_text: str, asset_info: dict):
        super().__init__()
        self.assets = assets
        self.search_text = search_text.strip().lower()
        self.asset_info = asset_info
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        '''Filter assets in background thread.'''
        if not self.search_text or self._stop_requested:
            self.results_ready.emit(self.assets)
            return

        filtered = []
        batch_size = 100  # Process in batches to allow interruption

        for i in range(0, len(self.assets), batch_size):
            if self._stop_requested:
                return

            batch = self.assets[i:i + batch_size]

            for a in batch:
                if self._stop_requested:
                    return

                asset_id = a['id']

                # Fast path: check ID first
                if self.search_text in asset_id.lower():
                    filtered.append(a)
                    continue

                # Check type name
                if self.search_text in a['type_name'].lower():
                    filtered.append(a)
                    continue

                # Check resolved name if available
                if asset_id in self.asset_info:
                    name = self.asset_info[asset_id].get('resolved_name')
                    if name and self.search_text in name.lower():
                        filtered.append(a)
                        continue

                # Check other fields
                url = a.get('url', '').lower()
                hash_val = a.get('hash', '').lower()
                cached_at = a.get('cached_at', '').lower()

                if (self.search_text in url or
                    self.search_text in hash_val or
                    self.search_text in cached_at):
                    filtered.append(a)

        if not self._stop_requested:
            self.results_ready.emit(filtered)


def _get_roblosecurity() -> str | None:
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


class ImageLoaderThread(QThread):
    """Worker thread for loading and processing images."""

    image_ready = pyqtSignal(QPixmap)
    error = pyqtSignal(str)

    def __init__(self, data: bytes):
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            log_buffer.log('Preview', f'Loading image ({len(self.data)} bytes)')

            image = Image.open(io.BytesIO(self.data))

            if self._stop_requested:
                return

            # Convert to RGBA
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
                QImage.Format.Format_RGBA8888
            )
            pixmap = QPixmap.fromImage(qimage)

            if not self._stop_requested:
                log_buffer.log('Preview', f'Image loaded: {image.width}x{image.height}')
                self.image_ready.emit(pixmap)

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Image load error: {e}')
                self.error.emit(str(e))


class MeshLoaderThread(QThread):
    """Worker thread for loading and converting meshes."""

    mesh_ready = pyqtSignal(str)  # OBJ content
    error = pyqtSignal(str)

    def __init__(self, data: bytes, asset_id: str):
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            log_buffer.log('Preview', f'Loading mesh {self.asset_id} ({len(self.data)} bytes)')

            # Decompress if gzip
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
                log_buffer.log('Preview', f'Decompressed mesh: {len(decompressed)} bytes')

            if self._stop_requested:
                return

            # Convert to OBJ
            obj_content = mesh_processing.convert(decompressed)

            if self._stop_requested:
                return

            if obj_content:
                log_buffer.log('Preview', f'Mesh converted successfully')
                self.mesh_ready.emit(obj_content)
            else:
                self.error.emit('Failed to convert mesh to OBJ format')

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Mesh conversion error: {e}')
                self.error.emit(str(e))


class AnimationLoaderThread(QThread):
    """Worker thread for loading animation data asynchronously."""

    animation_ready = pyqtSignal(bytes)  # Animation data ready to load into viewer
    error = pyqtSignal(str)

    def __init__(self, data: bytes, asset_id: str):
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            # Decompress if gzip
            decompressed = self.data
            if self.data.startswith(b'\x1f\x8b'):
                decompressed = gzip_module.decompress(self.data)
                log_buffer.log('Preview', f'Decompressed animation: {len(decompressed)} bytes')

            if self._stop_requested:
                return

            # Emit the data for the main thread to load into the viewer
            # The actual animation loading must happen on main thread due to OpenGL context
            self.animation_ready.emit(decompressed)

        except Exception as e:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Animation load error: {e}')
                self.error.emit(str(e))


class TexturePackLoaderThread(QThread):
    """Worker thread for loading texture pack images asynchronously."""

    texture_loaded = pyqtSignal(str, str, str, bytes)  # map_name, map_id, hash, image_data
    texture_error = pyqtSignal(str, str)  # map_name, error_message
    finished_loading = pyqtSignal()

    def __init__(self, maps: dict, cache_manager: 'CacheManager'):
        super().__init__()
        self.maps = maps
        self.cache_manager = cache_manager
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        import requests
        from urllib.parse import urlparse

        log_buffer.log('Preview', f'Loading texture pack with {len(self.maps)} maps')

        for map_name, map_id in self.maps.items():
            if self._stop_requested:
                return

            try:
                data = self.cache_manager.get_asset(str(map_id), 1)
                hash_val = ''

                if data:
                    asset_info = self.cache_manager.get_asset_info(str(map_id), 1)
                    hash_val = asset_info.get('hash', '') if asset_info else ''
                    log_buffer.log('Preview', f'Loaded {map_name} from cache')
                else:
                    if self._stop_requested:
                        return

                    api_url = f'https://assetdelivery.roblox.com/v1/asset/?id={map_id}'
                    headers = {'User-Agent': 'Roblox/WinInet'}

                    cookie = _get_roblosecurity()
                    if cookie:
                        headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

                    log_buffer.log('Preview', f'Fetching {map_name} from API')
                    response = requests.get(api_url, headers=headers, timeout=10, allow_redirects=True)
                    if response.status_code == 200 and response.content:
                        data = response.content
                        # Extract hash from final URL (after redirects)
                        final_url = response.url
                        parsed = urlparse(final_url)
                        # Hash is the last part of the path (e.g., /v2/asset/.../hash)
                        path_parts = parsed.path.rsplit('/', 1)
                        if len(path_parts) > 1 and path_parts[-1]:
                            hash_val = path_parts[-1]
                            log_buffer.log('Preview', f'Got hash from URL: {hash_val}')
                    else:
                        self.texture_error.emit(map_name, f'API error: {response.status_code}')
                        continue

                if self._stop_requested:
                    return

                self.texture_loaded.emit(map_name, str(map_id), hash_val, data)

            except Exception as e:
                if not self._stop_requested:
                    log_buffer.log('Preview', f'Texture {map_name} error: {e}')
                    self.texture_error.emit(map_name, str(e))

        if not self._stop_requested:
            log_buffer.log('Preview', 'Texture pack loading complete')
            self.finished_loading.emit()


def _filter_assets_sync(assets: list, search_text: str, asset_info: dict) -> list:
    """Filter assets synchronously with optimized string matching.

    Uses fast path for common cases and precomputed lowercase search.
    """
    if not search_text:
        return assets

    search_lower = search_text.strip().lower()
    if not search_lower:
        return assets

    # Precompute search fragments for better performance
    search_parts = search_lower.split()
    if not search_parts:
        return assets

    filtered = []

    # Process in batches to avoid blocking UI
    for a in assets:
        # Fast path: check most common fields first
        asset_id = a['id']

        # Quick ID check (exact or prefix match)
        if search_lower in asset_id.lower():
            filtered.append(a)
            continue

        # Check type name
        type_name = a['type_name'].lower()
        if search_lower in type_name:
            filtered.append(a)
            continue

        # Check resolved name if available (most common search target)
        if asset_id in asset_info:
            name = asset_info[asset_id].get('resolved_name')
            if name and search_lower in name.lower():
                filtered.append(a)
                continue

        # Slower checks: URL, hash, cached_at
        url = a.get('url', '').lower()
        hash_val = a.get('hash', '').lower()
        cached_at = a.get('cached_at', '').lower()

        if (search_lower in url or
            search_lower in hash_val or
            search_lower in cached_at):
            filtered.append(a)

    return filtered


class CacheViewerTab(QWidget):
    """Tab for viewing and managing cached Roblox assets."""

    def __init__(self, cache_manager: CacheManager, cache_scraper=None, parent=None, config_manager=None):
        super().__init__(parent)
        self.cache_manager = cache_manager
        self.cache_scraper = cache_scraper
        self.config_manager = config_manager
        self._last_asset_count = 0  # Track for change detection
        self._selected_asset_id: str | None = None  # Track selected asset by ID
        self._show_names = True  # Show names instead of hashes (on by default)
        self._asset_info: dict[str, dict] = {}  # asset_id -> {resolved_name, hash, row}
        self._current_pixmap = None  # Store current image for resize

        # Worker threads for async preview loading
        self._image_loader: ImageLoaderThread | None = None
        self._mesh_loader: MeshLoaderThread | None = None
        self._animation_loader: AnimationLoaderThread | None = None
        self._texturepack_loader: TexturePackLoaderThread | None = None

        # Search worker thread
        self._search_worker: SearchWorkerThread | None = None
        self._pending_search_text: str = ''
        self._is_searching: bool = False

        # Texturepack data for context menu
        self._texturepack_data: dict = {}  # map_name -> {id, hash, data}
        self._texturepack_xml: str = ''  # Original XML

        self._setup_ui()
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._check_for_updates)
        self._refresh_timer.start(3000)  # Check every 3 seconds

        # Search debounce timer (longer delay to batch rapid keystrokes)
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._do_search)

        # Load persisted resolved names from index
        self._load_persisted_names()

        # Refresh to show persisted names
        QTimer.singleShot(0, self._refresh_assets)

        # Start name resolver daemon thread
        threading.Thread(target=self._name_resolver_loop, daemon=True).start()

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

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

        # Connect splitter moved to rescale image
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

        layout.addWidget(self.splitter, stretch=1)

        # Actions
        self._create_actions(layout)

        self.setLayout(layout)
        self._refresh_assets()

    def _create_filters(self, parent_layout):
        """Create filter controls."""
        filter_group = QGroupBox('Filters')
        filter_layout = QHBoxLayout()

        # Search box first
        filter_layout.addWidget(QLabel('Search:'))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search all columns...')
        self.search_box.textChanged.connect(self._on_search_text_changed)
        filter_layout.addWidget(self.search_box)

        # Type selector second
        filter_layout.addWidget(QLabel('Type:'))
        self.type_filter = QComboBox()
        self.type_filter.addItem('All Types', None)
        for type_id, type_name in sorted(CacheManager.ASSET_TYPES.items(), key=lambda x: x[1]):
            self.type_filter.addItem(type_name, type_id)
        self.type_filter.currentIndexChanged.connect(self._refresh_assets)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addStretch()

        # Show names toggle (on by default)
        self.show_names_toggle = QCheckBox('Show Names')
        self.show_names_toggle.setChecked(True)
        self.show_names_toggle.toggled.connect(self._on_show_names_toggled)
        filter_layout.addWidget(self.show_names_toggle)

        filter_layout.addWidget(QLabel('|'))

        # Cache scraper toggle on right side (off by default)
        self.scraper_toggle = QCheckBox('Enable Cache Scraper')
        self.scraper_toggle.setChecked(False)
        self.scraper_toggle.stateChanged.connect(self._toggle_scraper)
        filter_layout.addWidget(self.scraper_toggle)

        filter_layout.addWidget(QLabel('|'))

        # Stats label
        self.stats_label = QLabel('Total: 0 assets | Size: 0 B')
        filter_layout.addWidget(self.stats_label)

        filter_group.setLayout(filter_layout)
        parent_layout.addWidget(filter_group)

    def _create_table(self, parent_layout):
        """Create asset table."""
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            'Hash/Name', 'Asset ID', 'Type', 'Size', 'Cached At', 'URL'
        ])

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.currentItemChanged.connect(self._on_selection_changed)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

        parent_layout.addWidget(self.table)

    def _create_preview_panel(self):
        """Create preview panel for viewing assets."""
        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        preview_group = QGroupBox('Preview')
        preview_group_layout = QVBoxLayout()

        # Scrollable container for all preview content
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.preview_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # Container widget inside scroll area
        self.preview_container = QWidget()
        self.preview_container_layout = QVBoxLayout()
        self.preview_container_layout.setContentsMargins(5, 5, 5, 5)

        # 3D Viewer for meshes
        self.obj_viewer = ObjViewerPanel()
        self.preview_container_layout.addWidget(self.obj_viewer)

        # Loading indicator
        self.loading_label = QLabel('Loading...')
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.loading_label.setStyleSheet('QLabel { background-color: #2b2b2b; color: #aaa; font-size: 14px; padding: 20px; }')
        self.preview_container_layout.addWidget(self.loading_label)
        self.loading_label.hide()

        # Image viewer (will show/hide as needed)
        self.image_label = QLabel('Select an asset to preview')
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: #2b2b2b; color: #888; }')
        self.image_label.setScaledContents(False)
        self.image_label.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.image_label.customContextMenuRequested.connect(self._show_image_context_menu)
        self.preview_container_layout.addWidget(self.image_label)

        # Audio player
        self.audio_player = None  # Created dynamically when needed
        self.audio_container = QWidget()
        self.audio_container_layout = QVBoxLayout()
        self.audio_container_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_container.setLayout(self.audio_container_layout)
        self.preview_container_layout.addWidget(self.audio_container)

        # Animation viewer
        self.animation_viewer = AnimationViewerPanel()
        self.preview_container_layout.addWidget(self.animation_viewer)

        # Text viewer for other types
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText('Select an asset to preview')
        self.preview_container_layout.addWidget(self.text_viewer)

        # Texture pack container (dynamically created)
        self.texturepack_widget = None

        # Set up scroll area
        self.preview_container.setLayout(self.preview_container_layout)
        self.preview_scroll.setWidget(self.preview_container)
        preview_group_layout.addWidget(self.preview_scroll)

        # Initially hide all preview widgets
        self.obj_viewer.hide()
        self.audio_container.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()

        preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(preview_group)

        preview_widget.setLayout(preview_layout)
        return preview_widget

    def _create_actions(self, parent_layout):
        """Create action buttons."""
        actions_layout = QHBoxLayout()

        delete_db_btn = QPushButton('Delete DB')
        delete_db_btn.clicked.connect(self._clear_cache)
        actions_layout.addWidget(delete_db_btn)

        delete_cache_btn = QPushButton('Delete Cache')
        delete_cache_btn.clicked.connect(self._delete_roblox_cache)
        actions_layout.addWidget(delete_cache_btn)

        self.stop_preview_btn = QPushButton('Stop Preview')
        self.stop_preview_btn.clicked.connect(self._stop_preview)
        self.stop_preview_btn.hide()
        actions_layout.addWidget(self.stop_preview_btn)

        actions_layout.addStretch()

        open_cache_btn = QPushButton('Open Cache Folder')
        open_cache_btn.clicked.connect(lambda: open_folder(self.cache_manager.cache_dir))
        actions_layout.addWidget(open_cache_btn)

        open_export_btn = QPushButton('Open Export Folder')
        open_export_btn.clicked.connect(lambda: open_folder(self.cache_manager.export_dir))
        actions_layout.addWidget(open_export_btn)

        parent_layout.addLayout(actions_layout)

    def _check_for_updates(self):
        """Check if cache has new assets and update stats only."""
        try:
            stats = self.cache_manager.get_cache_stats()
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])
            self.stats_label.setText(f'Total: {total_assets} assets | Size: {total_size}')

            # Only refresh table if asset count changed
            if total_assets != self._last_asset_count:
                self._last_asset_count = total_assets
                self._refresh_assets()
        except Exception:
            pass  # Ignore errors during background refresh

    def _refresh_assets(self):
        '''Refresh the asset list using search worker for all searches.'''
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Get filter type
        filter_type = self.type_filter.currentData()

        # Get assets
        assets = self.cache_manager.list_assets(filter_type)

        # Get search text
        search_text = self.search_box.text()

        # For empty search, show all immediately
        if not search_text.strip():
            self._populate_table(assets)
            return

        # Always use worker thread for searches to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(assets, search_text, self._asset_info)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _populate_table(self, assets: list):
        """Populate the table with assets."""
        # Disable updates while populating (major performance boost)
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)

        # Track row to restore selection
        row_to_select: int | None = None

        try:
            # Update table
            self.table.setRowCount(len(assets))

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
                        'row': row,
                    }
                else:
                    self._asset_info[asset_id]['row'] = row

                # Hash/Name (column 0) - show resolved name or hash based on toggle
                info = self._asset_info[asset_id]
                if self._show_names and info.get('resolved_name'):
                    display_val = info['resolved_name']
                else:
                    display_val = hash_val
                name_item = QTableWidgetItem(display_val)
                name_item.setData(Qt.ItemDataRole.UserRole, asset)
                self.table.setItem(row, 0, name_item)

                # Asset ID (column 1)
                id_item = QTableWidgetItem(asset_id)
                self.table.setItem(row, 1, id_item)

                # Type (column 2)
                type_item = QTableWidgetItem(asset['type_name'])
                self.table.setItem(row, 2, type_item)

                # Size (column 3)
                size = asset.get('size', 0)
                size_str = self._format_size(size)
                size_item = QTableWidgetItem(size_str)
                self.table.setItem(row, 3, size_item)

                # Cached At (column 4)
                cached_at = asset.get('cached_at', '')
                if cached_at:
                    # Format datetime
                    try:
                        cached_at = cached_at.split('T')[0] + ' ' + cached_at.split('T')[1].split('.')[0]
                    except (IndexError, AttributeError):
                        pass
                cached_item = QTableWidgetItem(cached_at)
                self.table.setItem(row, 4, cached_item)

                # URL (column 5)
                url = asset.get('url', '')
                url_item = QTableWidgetItem(url)
                self.table.setItem(row, 5, url_item)
        finally:
            # Re-enable updates
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)

        # Restore selection if the asset still exists
        if row_to_select is not None:
            self.table.blockSignals(True)
            self.table.selectRow(row_to_select)
            self.table.blockSignals(False)

        # Update stats
        try:
            stats = self.cache_manager.get_cache_stats()
            total_assets = stats['total_assets']
            total_size = self._format_size(stats['total_size'])
            self.stats_label.setText(f'Total: {total_assets} assets | Size: {total_size}')
            self._last_asset_count = total_assets
        except Exception:
            pass

    def _format_size(self, size_bytes: int) -> str:
        """Format size in bytes to human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB']:
            if size_bytes < 1024.0:
                return f'{size_bytes:.1f} {unit}'
            size_bytes /= 1024.0
        return f'{size_bytes:.1f} TB'

    def _toggle_scraper(self, state):
        """Toggle cache scraper on/off."""
        if self.cache_scraper:
            enabled = bool(state)
            self.cache_scraper.set_enabled(enabled)

    def _on_search_text_changed(self):
        '''Handle search text change - debounce to avoid too many searches.'''
        self._search_debounce.stop()
        self._search_debounce.start(300)  # 300ms debounce

    def _do_search(self):
        '''Execute the actual search after debounce using worker thread.'''
        # Stop any existing search
        if self._search_worker is not None:
            self._search_worker.stop()
            self._search_worker.quit()
            self._search_worker.wait()
            self._search_worker = None

        # Get filter type and assets
        filter_type = self.type_filter.currentData()
        assets = self.cache_manager.list_assets(filter_type)
        search_text = self.search_box.text()

        # For empty search, show all immediately
        if not search_text.strip():
            self._populate_table(assets)
            self.search_box.setEnabled(True)
            return

        # Disable search box while searching to prevent UI freezing
        self.search_box.setEnabled(False)

        # Always use worker thread to prevent UI freezing
        self._is_searching = True
        self._search_worker = SearchWorkerThread(assets, search_text, self._asset_info)
        self._search_worker.results_ready.connect(self._on_search_complete)
        self._search_worker.finished.connect(self._on_search_finished)
        self._search_worker.start()

    def _on_search_complete(self, filtered_assets: list):
        '''Handle search results from worker thread.'''
        self._populate_table(filtered_assets)

    def _on_search_finished(self):
        '''Handle search worker thread finished.'''
        self._is_searching = False
        self.search_box.setEnabled(True)

    def _load_persisted_names(self):
        """Load persisted resolved names from index.json."""
        for asset_key, asset_data in self.cache_manager.index['assets'].items():
            asset_id = asset_data['id']
            resolved_name = asset_data.get('resolved_name')
            if resolved_name:
                if asset_id not in self._asset_info:
                    self._asset_info[asset_id] = {
                        'hash': asset_data.get('hash', ''),
                        'resolved_name': resolved_name,
                        'row': None,
                    }
                else:
                    self._asset_info[asset_id]['resolved_name'] = resolved_name

    def _on_show_names_toggled(self, checked: bool):
        """Handle Show Names toggle."""
        self._show_names = checked

        # Disable updates for performance
        self.table.setUpdatesEnabled(False)
        try:
            # Update all rows to show either resolved name or hash
            for asset_id, info in self._asset_info.items():
                row = info.get('row')
                if row is None:
                    continue
                if row >= self.table.rowCount():
                    continue

                if checked and info.get('resolved_name'):
                    display_val = info['resolved_name']
                else:
                    display_val = info.get('hash', '')

                item = self.table.item(row, 0)
                if item:
                    item.setText(display_val)
        finally:
            # Re-enable updates
            self.table.setUpdatesEnabled(True)

    def _update_row_name(self, asset_id: str, name: str):
        """Update a single row's name cell (thread-safe via QTimer)."""
        info = self._asset_info.get(asset_id)
        if not info:
            return
        row = info.get('row')
        if row is None or row >= self.table.rowCount():
            return
        # Only update if Show Names is enabled
        if self._show_names:
            item = self.table.item(row, 0)
            if item:
                item.setText(name)

    def _save_resolved_name_to_index(self, asset_id: str, name: str):
        """Save resolved name to index.json for persistence."""
        # Find the asset key in index (format: {type}_{id})
        # Use list() to get a snapshot of keys to avoid dictionary changed during iteration
        asset_keys = list(self.cache_manager.index['assets'].keys())
        for asset_key in asset_keys:
            # Check if key still exists (in case it was deleted)
            if asset_key not in self.cache_manager.index['assets']:
                continue
            asset_data = self.cache_manager.index['assets'][asset_key]
            if asset_data['id'] == asset_id:
                # Update the resolved_name field
                asset_data['resolved_name'] = name
                # Don't save on every update - too slow
                # Let periodic saves or user actions handle persistence
                break

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

    def _fetch_asset_names(self, asset_ids: list[str], cookie: str | None) -> dict[str, str] | None:
        """Fetch asset names from Roblox Develop API (batch up to 50)."""
        import requests

        if not asset_ids:
            return None

        # Build session with auth
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update({
            'User-Agent': 'Roblox/WinInet',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Referer': 'https://www.roblox.com/',
            'Origin': 'https://www.roblox.com',
        })
        if cookie:
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'

        # Build query: assetIds=123,456,789
        query = ','.join(str(aid) for aid in asset_ids)
        url = f'https://develop.roblox.com/v1/assets?assetIds={query}'

        try:
            response = sess.get(url, timeout=10)
            response.raise_for_status()
        except Exception as e:
            log_buffer.log('Cache', f'[Name Resolver] Failed to fetch names: {e}')
            return None

        data = response.json().get('data', [])
        result = {}
        for item in data:
            aid = item.get('id')
            name = item.get('name', 'Unknown')
            if aid is not None:
                result[str(aid)] = name

        return result

    def _name_resolver_loop(self):
        """Background thread to resolve asset names."""
        import time

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

            # Build pending list - assets without resolved names
            pending = [
                asset_id
                for asset_id, info in self._asset_info.items()
                if info.get('resolved_name') is None and info.get('row') is not None
            ]

            if not pending:
                time.sleep(0.2)
                continue

            # Batch size and delay
            batch_size = 50
            delay = 0.2 if len(pending) > 50 else 0.5

            # Take the first batch
            batch = pending[:batch_size]

            # Fetch names
            try:
                names = self._fetch_asset_names(batch, cookie)
            except Exception as e:
                log_buffer.log('Cache', f'[Name Resolver] Fetch failed: {e}')
                time.sleep(delay)
                continue

            if not names:
                time.sleep(delay)
                continue

            # Update cache and UI
            for asset_id, name in names.items():
                info = self._asset_info.get(asset_id)
                if not info:
                    continue

                # Store resolved name in memory
                info['resolved_name'] = name

                # Save to index.json for persistence
                self._save_resolved_name_to_index(asset_id, name)

                # Update UI on main thread
                if self._show_names:
                    QTimer.singleShot(0, lambda aid=asset_id, n=name: self._update_row_name(aid, n))

            # Save index after batch update (less frequent saves)
            try:
                self.cache_manager._save_index()
            except Exception as e:
                log_buffer.log('Cache', f'[Name Resolver] Failed to save index: {e}')

            time.sleep(delay)

    def _get_selected_asset(self) -> dict | None:
        """Get the currently selected asset."""
        current_row = self.table.currentRow()
        if current_row < 0:
            return None

        id_item = self.table.item(current_row, 0)
        if not id_item:
            return None

        return id_item.data(Qt.ItemDataRole.UserRole)

    def _export_selected(self):
        """Export the selected asset."""
        asset = self._get_selected_asset()
        if not asset:
            QMessageBox.warning(self, 'No Selection', 'Please select an asset to export')
            return

        # Ask for export location
        default_name = f"{asset['id']}.bin"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            'Export Asset',
            default_name,
            'All Files (*.*)'
        )

        if not file_path:
            return

        from pathlib import Path
        export_path = self.cache_manager.export_asset(
            asset['id'],
            asset['type'],
            Path(file_path)
        )

        if export_path:
            log_buffer.log('Cache', f"Exported asset {asset['id']} to {export_path}")
            QMessageBox.information(self, 'Success', f'Asset exported to:\n{export_path}')
        else:
            QMessageBox.critical(self, 'Error', 'Failed to export asset')

    def _export_all(self):
        """Export all visible assets."""
        # Get current filter
        filter_type = self.type_filter.currentData()
        assets = self.cache_manager.list_assets(filter_type)

        # Apply search filter across all columns (same as _refresh_assets)
        search_text = self.search_box.text().strip().lower()
        if search_text:
            filtered = []
            for a in assets:
                asset_id = a['id'].lower()
                type_name = a['type_name'].lower()
                url = a.get('url', '').lower()
                hash_val = a.get('hash', '').lower()
                size_str = self._format_size(a.get('size', 0)).lower()
                cached_at = a.get('cached_at', '').lower()

                resolved_name = ''
                if asset_id in self._asset_info:
                    name = self._asset_info[asset_id].get('resolved_name')
                    resolved_name = name.lower() if name else ''

                if (search_text in asset_id or
                    search_text in type_name or
                    search_text in url or
                    search_text in hash_val or
                    search_text in resolved_name or
                    search_text in size_str or
                    search_text in cached_at):
                    filtered.append(a)
            assets = filtered

        if not assets:
            QMessageBox.warning(self, 'No Assets', 'No assets to export')
            return

        reply = QMessageBox.question(
            self,
            'Export All',
            f'Export {len(assets)} asset(s) to the export folder?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        exported_count = 0
        for asset in assets:
            asset_id = asset['id']
            # Get resolved name if available
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')

            if self.cache_manager.export_asset(asset['id'], asset['type'], resolved_name=resolved_name):
                exported_count += 1

        log_buffer.log('Cache', f'Exported {exported_count}/{len(assets)} assets')
        QMessageBox.information(
            self,
            'Export Complete',
            f'Exported {exported_count} asset(s)\n\nLocation: {self.cache_manager.export_dir}'
        )

    def _delete_selected(self):
        """Delete the selected asset(s)."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, 'No Selection', 'Please select asset(s) to delete')
            return

        # Collect assets to delete
        assets_to_delete = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 0)
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
            'Delete Assets',
            f"Delete {count} asset(s)?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            deleted_count = 0
            for asset in assets_to_delete:
                if self.cache_manager.delete_asset(asset['id'], asset['type']):
                    deleted_count += 1
                    log_buffer.log('Cache', f"Deleted asset {asset['id']}")

            self._refresh_assets()

            if deleted_count == count:
                QMessageBox.information(self, 'Success', f'Deleted {deleted_count} asset(s)')
            else:
                QMessageBox.warning(
                    self,
                    'Partial Success',
                    f'Deleted {deleted_count}/{count} asset(s). Some assets failed to delete.'
                )

    def _clear_cache(self):
        """Delete the entire cache database and files (old Delete DB functionality)."""
        reply = QMessageBox.question(
            self,
            'Delete Database',
            'This will delete all cached assets AND the database index.\nThis cannot be undone. Continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                import shutil
                cache_dir = self.cache_manager.cache_dir
                if cache_dir.exists():
                    shutil.rmtree(cache_dir)
                    cache_dir.mkdir(parents=True, exist_ok=True)
                # Reset the index
                self.cache_manager.index = {'assets': {}}
                self.cache_manager._save_index()
                self._last_asset_count = 0
                self._asset_info.clear()
                # Clear scraper tracking so assets can be re-scraped
                if self.cache_scraper:
                    self.cache_scraper.clear_tracking()
                self._refresh_assets()
                log_buffer.log('Cache', 'Database deleted and reset')
                QMessageBox.information(self, 'Success', 'Database deleted successfully')
            except Exception as e:
                QMessageBox.critical(self, 'Error', f'Failed to delete database: {e}')

    def _delete_roblox_cache(self):
        """Delete Roblox cache using system tray method."""
        from ..gui import DeleteCacheWindow

        window = DeleteCacheWindow()
        window.show()

    def _on_selection_changed(self):
        """Handle table selection change to preview asset."""
        asset = self._get_selected_asset()
        if not asset:
            self._selected_asset_id = None
            self._clear_preview()
            return

        # Track selected asset ID for persistence across refreshes
        self._selected_asset_id = asset['id']

        # Stop all loaders first
        self._stop_all_loaders()

        # Hide all preview widgets first
        self.obj_viewer.hide()
        self.image_label.hide()
        self.loading_label.hide()
        self.audio_container.hide()
        self.animation_viewer.hide()
        self.text_viewer.hide()

        # Clean up texture pack widget
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

        # Stop any playing audio
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None

        # Stop animation playback
        self.animation_viewer.stop()

        asset_type = asset['type']
        asset_id = asset['id']

        try:
            # Get asset data
            data = self.cache_manager.get_asset(asset_id, asset_type)
            if not data:
                self._show_text_preview(f'Failed to load asset {asset_id}')
                return

            # Show loading for async previews
            if asset_type in [4, 1, 13, 63]:  # Mesh, Image, Decal, TexturePack
                self._show_loading()

            # Preview based on type
            if asset_type == 4:  # Mesh
                self._preview_mesh(data, asset_id)
            elif asset_type == 39:  # SolidModel
                self._show_text_preview(
                    f'SolidModel (Type 39) preview is not currently supported.\n\n'
                    f'Asset ID: {asset_id}\n'
                    f'Size: {len(data):,} bytes\n\n'
                    f'SolidModels contain CSG (Constructive Solid Geometry) data\n'
                    f'which cannot be converted to standard mesh format.'
                )
            elif asset_type in [1, 13]:  # Image, Decal
                self._preview_image(data)
            elif asset_type == 3:  # Audio
                self._preview_audio(data, asset_id)
            elif asset_type == 24:  # Animation
                self._preview_animation(data, asset_id)
            elif asset_type == 63:  # TexturePack
                self._preview_texturepack(data, asset_id)
            else:
                # Show as hex dump for other types
                self._preview_hex(data, asset)

        except Exception as e:
            self._show_text_preview(f'Error previewing asset: {e}')

    def _show_context_menu(self, position):
        """Show right-click context menu."""
        menu = QMenu(self)

        # Get selected rows
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        # Add actions
        add_to_replacer_action = menu.addAction('Add IDs to Replacer')

        # Export submenu with format options
        export_menu = menu.addMenu('Export Selected')

        # Get asset types from selection to determine available formats
        asset_types = set()
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 0)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    asset_types.add(asset['type'])

        # Determine available formats (intersection of all selected types)
        available_formats = None
        for asset_type in asset_types:
            formats = set(self.cache_manager.get_available_export_formats(asset_type))
            if available_formats is None:
                available_formats = formats
            else:
                available_formats &= formats

        if not available_formats:
            available_formats = {'raw', 'bin'}

        # Add format options
        export_actions = {}
        format_labels = {
            'converted': 'Converted (best format)',
            'bin': 'Binary (decompressed)',
            'raw': 'Raw (original cache)',
        }
        for fmt in ['converted', 'bin', 'raw']:
            if fmt in available_formats:
                action = export_menu.addAction(format_labels[fmt])
                export_actions[action] = fmt

        menu.addSeparator()

        # Copy submenu
        copy_menu = menu.addMenu('Copy')
        copy_hash_action = copy_menu.addAction('Hash/Name')
        copy_id_action = copy_menu.addAction('Asset ID')
        copy_url_action = copy_menu.addAction('URL')

        # Add "Copy Converted" if at least one selected asset supports conversion
        copy_converted_action = None
        if 'converted' in available_formats:
            copy_menu.addSeparator()
            copy_converted_action = copy_menu.addAction('Converted Data')

        menu.addSeparator()
        delete_action = menu.addAction('Delete Selected')

        # Execute menu
        action = menu.exec(self.table.viewport().mapToGlobal(position))

        if action == add_to_replacer_action:
            self._add_selected_to_replacer()
        elif action in export_actions:
            self._export_selected_multiple(export_format=export_actions[action])
        elif action == delete_action:
            self._delete_selected()
        elif action == copy_hash_action:
            self._copy_column(0)
        elif action == copy_id_action:
            self._copy_column(1)
        elif action == copy_url_action:
            self._copy_column(5)
        elif action == copy_converted_action:
            self._copy_converted()

    def _copy_column(self, column: int):
        """Copy column contents for selected rows."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        values = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, column)
            if item:
                values.append(item.text())

        if values:
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText('\n'.join(values))
            log_buffer.log('Cache', f'Copied {len(values)} value(s) to clipboard')

    def _copy_converted(self):
        """Copy converted files to clipboard as Windows file objects."""
        import tempfile
        from pathlib import Path
        from PyQt6.QtCore import QUrl, QMimeData
        from PyQt6.QtWidgets import QApplication

        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        # Only process first selected asset
        row = selected_rows[0].row()
        item = self.table.item(row, 0)
        if not item:
            return

        asset = item.data(Qt.ItemDataRole.UserRole)
        if not asset:
            return

        asset_id = asset['id']
        asset_type = asset['type']

        # Get resolved name if available
        resolved_name = None
        if asset_id in self._asset_info:
            resolved_name = self._asset_info[asset_id].get('resolved_name')

        try:
            # Get asset data
            data = self.cache_manager.get_asset(asset_id, asset_type)
            if not data:
                QMessageBox.warning(self, 'Error', f'Failed to load asset {asset_id}')
                return

            # Create temp directory for converted files
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_clipboard'
            temp_dir.mkdir(exist_ok=True)

            temp_file = None

            # Convert based on type and save to temp file
            if asset_type == 4:  # Mesh - save as OBJ file
                from . import mesh_processing
                try:
                    obj_content = mesh_processing.convert(data)
                    if obj_content:
                        filename = f'{resolved_name or asset_id}.obj'
                        temp_file = temp_dir / filename
                        temp_file.write_text(obj_content, encoding='utf-8')
                    else:
                        QMessageBox.warning(self, 'Error', 'Failed to convert mesh to OBJ')
                        return
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Mesh conversion error: {e}')
                    return

            elif asset_type in (1, 13):  # Image, Decal - save as PNG
                try:
                    filename = f'{resolved_name or asset_id}.png'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Image save error: {e}')
                    return

            elif asset_type == 3:  # Audio - save as OGG/MP3
                try:
                    # Determine extension
                    if data.startswith(b'OggS'):
                        ext = 'ogg'
                    elif data.startswith(b'ID3') or data.startswith(b'\xFF\xFB'):
                        ext = 'mp3'
                    else:
                        ext = 'ogg'

                    filename = f'{resolved_name or asset_id}.{ext}'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Audio save error: {e}')
                    return

            elif asset_type == 24:  # Animation - save as RBXMX
                try:
                    # Decompress if needed
                    if data.startswith(b'\x1f\x8b'):
                        data = gzip_module.decompress(data)

                    filename = f'{resolved_name or asset_id}.rbxmx'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'Animation save error: {e}')
                    return

            elif asset_type == 63:  # TexturePack - save XML
                try:
                    filename = f'{resolved_name or asset_id}_texturepack.xml'
                    temp_file = temp_dir / filename
                    temp_file.write_bytes(data)
                except Exception as e:
                    QMessageBox.warning(self, 'Error', f'TexturePack save error: {e}')
                    return

            # Copy file to clipboard
            if temp_file and temp_file.exists():
                mime_data = QMimeData()
                mime_data.setUrls([QUrl.fromLocalFile(str(temp_file))])
                QApplication.clipboard().setMimeData(mime_data)
                log_buffer.log('Cache', f'Copied file to clipboard: {temp_file.name}')
                QMessageBox.information(self, 'Success', f'File copied to clipboard:\n{temp_file.name}\n\nYou can now paste it anywhere.')

        except Exception as e:
            QMessageBox.warning(self, 'Error', f'Copy error: {e}')

    def _export_selected_multiple(self, export_format: str = 'converted'):
        """Export multiple selected assets."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, 'No Selection', 'Please select asset(s) to export')
            return

        # Collect assets to export
        assets_to_export = []
        for row_index in selected_rows:
            row = row_index.row()
            item = self.table.item(row, 0)
            if item:
                asset = item.data(Qt.ItemDataRole.UserRole)
                if asset:
                    assets_to_export.append(asset)

        if not assets_to_export:
            return

        # Export all with resolved names
        exported_count = 0
        for asset in assets_to_export:
            asset_id = asset['id']
            # Get resolved name if available
            resolved_name = None
            if asset_id in self._asset_info:
                resolved_name = self._asset_info[asset_id].get('resolved_name')

            if self.cache_manager.export_asset(
                asset['id'], asset['type'],
                resolved_name=resolved_name,
                export_format=export_format
            ):
                exported_count += 1

        # Determine export location based on format
        format_dir = self.cache_manager.export_dir / export_format
        log_buffer.log('Cache', f'Exported {exported_count}/{len(assets_to_export)} assets as {export_format}')
        QMessageBox.information(
            self,
            'Export Complete',
            f'Exported {exported_count} asset(s) as {export_format}\n\nLocation: {format_dir}'
        )

    def _add_selected_to_replacer(self):
        """Add selected asset IDs to replacer."""
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            return

        asset_ids = []
        for row_index in selected_rows:
            row = row_index.row()
            id_item = self.table.item(row, 1)  # Asset ID is column 1
            if id_item:
                asset_ids.append(id_item.text())

        if not asset_ids:
            return

        # Try to find the replacer entry field (walk up parent chain)
        replacer_window = None
        widget = self
        while widget is not None:
            if hasattr(widget, 'replace_entry'):
                replacer_window = widget
                break
            widget = widget.parent() if hasattr(widget, 'parent') else None

        if replacer_window:
            # Add to existing IDs if there are any
            current_text = replacer_window.replace_entry.text().strip()
            if current_text:
                new_text = current_text + ', ' + ', '.join(asset_ids)
            else:
                new_text = ', '.join(asset_ids)
            replacer_window.replace_entry.setText(new_text)

            log_buffer.log('Cache', f'Added {len(asset_ids)} asset ID(s) to replacer')
            QMessageBox.information(
                self,
                'Added to Replacer',
                f'Added {len(asset_ids)} asset ID(s) to replacer:\n{", ".join(asset_ids[:5])}{"..." if len(asset_ids) > 5 else ""}'
            )
        else:
            # Fallback: copy to clipboard if not in replacer window
            from PyQt6.QtWidgets import QApplication
            clipboard = QApplication.clipboard()
            clipboard.setText(', '.join(asset_ids))

            log_buffer.log('Cache', f'Copied {len(asset_ids)} asset ID(s) to clipboard')
            QMessageBox.information(
                self,
                'Copied to Clipboard',
                f'Copied {len(asset_ids)} asset ID(s) to clipboard:\n{", ".join(asset_ids[:5])}{"..." if len(asset_ids) > 5 else ""}'
            )

    def _stop_preview(self):
        """Stop current preview and hide button."""
        self._selected_asset_id = None
        self._clear_preview()
        self.stop_preview_btn.hide()
        self.table.clearSelection()
        # Show default preview message
        self.image_label.setText('Select an asset to preview')
        self.image_label.show()

    def _clear_preview(self):
        """Clear all preview widgets and stop any running loaders."""
        # Stop all worker threads first
        self._stop_all_loaders()

        # Hide and clear UI widgets
        self.obj_viewer.hide()
        self.obj_viewer.clear()
        self.image_label.hide()
        self.image_label.clear()
        self.image_label.setText('Select an asset to preview')
        self._current_pixmap = None
        self.audio_container.hide()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        self.animation_viewer.hide()
        self.animation_viewer.clear()
        self.text_viewer.hide()
        self.text_viewer.clear()

        # Clean up texture pack widgets
        if self.texturepack_widget is not None:
            self.texturepack_widget.deleteLater()
            self.texturepack_widget = None

    def _stop_all_loaders(self):
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

    def _on_splitter_moved(self, pos: int, index: int):
        """Handle splitter resize to rescale image."""
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def _show_loading(self):
        """Show loading indicator."""
        self.loading_label.show()

    def _hide_loading(self):
        """Hide loading indicator."""
        self.loading_label.hide()

    def _preview_mesh(self, data: bytes, asset_id: str):
        """Preview a mesh asset in 3D using background thread."""
        self._mesh_loader = MeshLoaderThread(data, asset_id)
        self._mesh_loader.mesh_ready.connect(self._on_mesh_ready)
        self._mesh_loader.error.connect(lambda e: self._show_text_preview(f'Mesh error: {e}'))
        self._mesh_loader.start()

    def _on_mesh_ready(self, obj_content: str):
        """Handle mesh loaded from background thread."""
        self._hide_loading()
        self.obj_viewer.load_obj(obj_content, '')
        self.obj_viewer.show()
        self.stop_preview_btn.show()

    def _preview_image(self, data: bytes):
        """Preview an image asset using background thread."""
        self._image_loader = ImageLoaderThread(data)
        self._image_loader.image_ready.connect(self._on_image_ready)
        self._image_loader.error.connect(lambda e: self._show_text_preview(f'Image error: {e}'))
        self._image_loader.start()

    def _on_image_ready(self, pixmap: QPixmap):
        """Handle image loaded from background thread."""
        self._hide_loading()
        self._current_pixmap = pixmap
        self._scale_and_show_image(pixmap)
        self.image_label.show()
        self.stop_preview_btn.show()

    def _scale_and_show_image(self, pixmap: QPixmap):
        """Scale pixmap to fit container and display it."""
        container_width = self.preview_scroll.viewport().width() - 20
        container_height = self.preview_scroll.viewport().height() - 20

        if container_width < 100:
            container_width = 400
        if container_height < 100:
            container_height = 400

        # Scale to fit within container while maintaining aspect ratio
        scaled = pixmap.scaled(
            container_width, container_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        self.image_label.setPixmap(scaled)

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

    def _preview_texturepack(self, data: bytes, asset_id: str):
        """Preview a texture pack by showing all texture maps."""
        import xml.etree.ElementTree as ET

        try:
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
            root = ET.fromstring(xml_text)

            # Extract texture map IDs in order
            map_order = ['color', 'normal', 'metalness', 'roughness']
            maps = {}
            for elem in map_order:
                node = root.find(elem)
                if node is not None and node.text:
                    maps[elem.capitalize()] = node.text

            if not maps:
                self._show_text_preview(f'No texture maps found in texture pack {asset_id}')
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
            self._tp_pixmaps = {}  # Store pixmaps for copy

            # Create placeholder for each texture map
            for map_name, map_id in maps.items():
                # Header with name and id
                header = QLabel(f'{map_name}  |  {map_id}')
                header.setStyleSheet('font-weight: bold; color: #888; padding: 5px;')
                header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                tp_layout.addWidget(header)

                # Image placeholder with context menu
                img_label = QLabel('Loading...')
                img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                img_label.setStyleSheet('background-color: #333; padding: 10px; min-height: 100px;')
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
            self.stop_preview_btn.show()

            # Start async loading of textures
            self._texturepack_loader = TexturePackLoaderThread(maps, self.cache_manager)
            self._texturepack_loader.texture_loaded.connect(self._on_texturepack_texture_loaded)
            self._texturepack_loader.texture_error.connect(self._on_texturepack_texture_error)
            self._texturepack_loader.start()

        except Exception as e:
            self._show_text_preview(f'Texture pack preview error: {e}')

    def _on_texturepack_texture_loaded(self, map_name: str, map_id: str, hash_val: str, data: bytes):
        """Handle loaded texture from texture pack."""
        # Hide loading on first texture
        self._hide_loading()

        try:
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
                'data': data
            }
            # Update label property with hash
            img_label.setProperty('map_hash', hash_val)

            # Load image
            image = Image.open(io.BytesIO(data))
            if image.mode not in ('RGB', 'RGBA'):
                image = image.convert('RGBA')
            elif image.mode == 'RGB':
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
                QImage.Format.Format_RGBA8888
            )
            pixmap = QPixmap.fromImage(qimage)

            # Store original pixmap for copy
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
        """Handle texture load error."""
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

    def _show_texturepack_context_menu(self, pos, label: QLabel):
        """Show context menu for texturepack image."""
        from PyQt6.QtWidgets import QApplication

        map_name = label.property('map_name')
        map_id = label.property('map_id')
        map_hash = label.property('map_hash') or ''

        menu = QMenu(self)

        # Copy image
        copy_image_action = menu.addAction('Copy Image')

        menu.addSeparator()

        # Copy name/id/hash
        copy_name_action = menu.addAction(f'Copy Name ({map_name})')
        copy_id_action = menu.addAction(f'Copy ID ({map_id})')
        copy_hash_action = None
        if map_hash:
            copy_hash_action = menu.addAction(f'Copy Hash ({map_hash[:16]}...)')

        menu.addSeparator()

        # Copy XML
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
        elif action == copy_hash_action and map_hash:
            QApplication.clipboard().setText(map_hash)
        elif action == copy_xml_action:
            QApplication.clipboard().setText(self._texturepack_xml)

    def _preview_audio(self, data: bytes, asset_id: str):
        """Preview an audio asset."""
        import tempfile
        from pathlib import Path

        try:
            # Create temporary file for audio
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_audio'
            temp_dir.mkdir(exist_ok=True)

            # Determine file extension (default to mp3)
            temp_file = temp_dir / f'{asset_id}.mp3'

            # Write audio data to temp file
            with open(temp_file, 'wb') as f:
                f.write(data)

            # Create audio player with config manager for volume persistence
            self.audio_player = AudioPlayerWidget(str(temp_file), self, self.config_manager)

            # Clear previous audio widgets
            while self.audio_container_layout.count():
                child = self.audio_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Add new audio player
            self.audio_container_layout.addWidget(self.audio_player)
            self.audio_container.show()
            self.stop_preview_btn.show()

        except Exception as e:
            self._show_text_preview(f'Audio preview error: {e}')
            log_buffer.log('Cache', f'Audio preview error: {e}')

    def _preview_animation(self, data: bytes, asset_id: str):
        """Preview an animation asset (RBXM XML format) using background thread."""
        self._show_loading()
        self._animation_loader = AnimationLoaderThread(data, asset_id)
        self._animation_loader.animation_ready.connect(self._on_animation_ready)
        self._animation_loader.error.connect(lambda e: self._show_text_preview(f'Animation error: {e}'))
        self._animation_loader.start()

    def _on_animation_ready(self, data: bytes):
        """Handle animation data ready from background thread."""
        self._hide_loading()
        try:
            # Load in the animation viewer (must be on main thread for OpenGL)
            if self.animation_viewer.load_animation(data):
                self.animation_viewer.show()
                self.stop_preview_btn.show()
                return

            # Fallback: try to decode as XML for text display
            text = data.decode('utf-8', errors='replace')

            # Check if it's XML
            if text.strip().startswith('<'):
                # Format XML for display
                import xml.etree.ElementTree as ET
                try:
                    ET.fromstring(data)
                    # Pretty print XML
                    import xml.dom.minidom
                    dom = xml.dom.minidom.parseString(data)
                    pretty_xml = dom.toprettyxml(indent='  ')
                    # Remove extra blank lines
                    lines = [line for line in pretty_xml.split('\n') if line.strip()]
                    self._show_text_preview('\n'.join(lines[:500]))  # Limit lines
                except Exception:
                    # Fallback to raw text
                    self._show_text_preview(f'Animation data\nSize: {self._format_size(len(data))}\n\n{text[:5000]}')
            else:
                # Binary format, show hex
                self._preview_hex(data, {'id': '', 'type_name': 'Animation'})

        except Exception as e:
            self._show_text_preview(f'Animation preview error: {e}')

    def _preview_hex(self, data: bytes, asset: dict):
        """Show hex dump preview."""
        # Show first 1KB as hex dump
        preview_size = min(1024, len(data))
        hex_lines = []

        hex_lines.append(f"Asset ID: {asset['id']}")
        hex_lines.append(f"Type: {asset['type_name']}")
        hex_lines.append(f"Size: {self._format_size(len(data))}")
        hex_lines.append(f"\nFirst {preview_size} bytes (hex dump):\n")

        for i in range(0, preview_size, 16):
            hex_part = ' '.join(f'{b:02x}' for b in data[i:i+16])
            ascii_part = ''.join(
                chr(b) if 32 <= b < 127 else '.'
                for b in data[i:i+16]
            )
            hex_lines.append(f'{i:08x}  {hex_part:<48}  {ascii_part}')

        if len(data) > preview_size:
            hex_lines.append(f'\n... ({len(data) - preview_size} more bytes)')

        self._show_text_preview('\n'.join(hex_lines))

    def _show_text_preview(self, text: str):
        """Show text in the text viewer."""
        self._hide_loading()
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()
