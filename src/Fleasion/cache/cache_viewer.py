"""Cache viewer tab - simplified version for viewing cached assets."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QLabel, QComboBox, QLineEdit, QMessageBox,
    QHeaderView, QFileDialog, QGroupBox, QSplitter, QTextEdit, QCheckBox
)
from PyQt6.QtGui import QPixmap, QImage
from PIL import Image
import io

from .cache_manager import CacheManager
from .obj_viewer import ObjViewerPanel
from .audio_player import AudioPlayerWidget
from . import mesh_processing
from ..utils import log_buffer, open_folder


class CacheViewerTab(QWidget):
    """Tab for viewing and managing cached Roblox assets."""

    def __init__(self, cache_manager: CacheManager, cache_scraper=None, parent=None):
        super().__init__(parent)
        self.cache_manager = cache_manager
        self.cache_scraper = cache_scraper
        self._setup_ui()
        self._refresh_timer = QTimer()
        self._refresh_timer.timeout.connect(self._refresh_assets)
        self._refresh_timer.start(2000)  # Refresh every 2 seconds

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Header with stats
        self._create_header(layout)

        # Filters
        self._create_filters(layout)

        # Splitter for table and preview
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left side: Asset table
        table_widget = QWidget()
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        self._create_table(table_layout)
        table_widget.setLayout(table_layout)
        splitter.addWidget(table_widget)

        # Right side: Preview panel
        self.preview_panel = self._create_preview_panel()
        splitter.addWidget(self.preview_panel)

        # Set splitter sizes (table gets more space initially)
        splitter.setSizes([600, 300])

        layout.addWidget(splitter, stretch=1)

        # Actions
        self._create_actions(layout)

        self.setLayout(layout)
        self._refresh_assets()

    def _create_header(self, parent_layout):
        """Create header with statistics."""
        header_group = QGroupBox('Cache Statistics')
        header_layout = QHBoxLayout()

        # Cache scraper toggle (off by default)
        self.scraper_toggle = QCheckBox('Enable Cache Scraper')
        self.scraper_toggle.setChecked(False)
        self.scraper_toggle.stateChanged.connect(self._toggle_scraper)
        header_layout.addWidget(self.scraper_toggle)

        header_layout.addStretch()

        self.stats_label = QLabel('Total: 0 assets | Size: 0 B')
        header_layout.addWidget(self.stats_label)

        header_layout.addStretch()

        refresh_btn = QPushButton('Refresh')
        refresh_btn.clicked.connect(self._refresh_assets)
        header_layout.addWidget(refresh_btn)

        header_group.setLayout(header_layout)
        parent_layout.addWidget(header_group)

    def _create_filters(self, parent_layout):
        """Create filter controls."""
        filter_group = QGroupBox('Filters')
        filter_layout = QHBoxLayout()

        filter_layout.addWidget(QLabel('Type:'))
        self.type_filter = QComboBox()
        self.type_filter.addItem('All Types', None)
        for type_id, type_name in sorted(CacheManager.ASSET_TYPES.items(), key=lambda x: x[1]):
            self.type_filter.addItem(type_name, type_id)
        self.type_filter.currentIndexChanged.connect(self._refresh_assets)
        filter_layout.addWidget(self.type_filter)

        filter_layout.addWidget(QLabel('Search:'))
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText('Search by ID...')
        self.search_box.textChanged.connect(self._refresh_assets)
        filter_layout.addWidget(self.search_box)

        filter_layout.addStretch()

        filter_group.setLayout(filter_layout)
        parent_layout.addWidget(filter_group)

    def _create_table(self, parent_layout):
        """Create asset table."""
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            'Asset ID', 'Type', 'Size', 'Cached At', 'URL', 'Hash'
        ])

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.currentItemChanged.connect(self._on_selection_changed)

        parent_layout.addWidget(self.table)

    def _create_preview_panel(self):
        """Create preview panel for viewing assets."""
        preview_widget = QWidget()
        preview_layout = QVBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)

        preview_group = QGroupBox('Preview')
        preview_group_layout = QVBoxLayout()

        # 3D Viewer for meshes
        self.obj_viewer = ObjViewerPanel()
        preview_group_layout.addWidget(self.obj_viewer)

        # Image viewer (will show/hide as needed)
        self.image_label = QLabel('Select an asset to preview')
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setStyleSheet('QLabel { background-color: #2b2b2b; color: #888; }')
        self.image_label.setMinimumHeight(200)
        preview_group_layout.addWidget(self.image_label)

        # Audio player
        self.audio_player = None  # Created dynamically when needed
        self.audio_container = QWidget()
        self.audio_container_layout = QVBoxLayout()
        self.audio_container_layout.setContentsMargins(0, 0, 0, 0)
        self.audio_container.setLayout(self.audio_container_layout)
        preview_group_layout.addWidget(self.audio_container)

        # Text viewer for other types
        self.text_viewer = QTextEdit()
        self.text_viewer.setReadOnly(True)
        self.text_viewer.setPlaceholderText('Select an asset to preview')
        preview_group_layout.addWidget(self.text_viewer)

        # Initially hide all preview widgets
        self.obj_viewer.hide()
        self.audio_container.hide()
        self.text_viewer.hide()

        preview_group.setLayout(preview_group_layout)
        preview_layout.addWidget(preview_group)

        preview_widget.setLayout(preview_layout)
        return preview_widget

    def _create_actions(self, parent_layout):
        """Create action buttons."""
        actions_layout = QHBoxLayout()

        export_btn = QPushButton('Export Selected')
        export_btn.clicked.connect(self._export_selected)
        actions_layout.addWidget(export_btn)

        export_all_btn = QPushButton('Export All')
        export_all_btn.clicked.connect(self._export_all)
        actions_layout.addWidget(export_all_btn)

        delete_btn = QPushButton('Delete Selected')
        delete_btn.clicked.connect(self._delete_selected)
        actions_layout.addWidget(delete_btn)

        clear_btn = QPushButton('Clear Cache')
        clear_btn.clicked.connect(self._clear_cache)
        actions_layout.addWidget(clear_btn)

        actions_layout.addStretch()

        open_cache_btn = QPushButton('Open Cache Folder')
        open_cache_btn.clicked.connect(lambda: open_folder(self.cache_manager.cache_dir))
        actions_layout.addWidget(open_cache_btn)

        open_export_btn = QPushButton('Open Export Folder')
        open_export_btn.clicked.connect(lambda: open_folder(self.cache_manager.export_dir))
        actions_layout.addWidget(open_export_btn)

        parent_layout.addLayout(actions_layout)

    def _refresh_assets(self):
        """Refresh the asset list."""
        # Get filter type
        filter_type = self.type_filter.currentData()

        # Get assets
        assets = self.cache_manager.list_assets(filter_type)

        # Apply search filter
        search_text = self.search_box.text().strip().lower()
        if search_text:
            assets = [a for a in assets if search_text in a['id'].lower()]

        # Update table
        self.table.setRowCount(len(assets))

        for row, asset in enumerate(assets):
            # Asset ID
            id_item = QTableWidgetItem(asset['id'])
            id_item.setData(Qt.ItemDataRole.UserRole, asset)
            self.table.setItem(row, 0, id_item)

            # Type
            type_item = QTableWidgetItem(asset['type_name'])
            self.table.setItem(row, 1, type_item)

            # Size
            size = asset.get('size', 0)
            size_str = self._format_size(size)
            size_item = QTableWidgetItem(size_str)
            self.table.setItem(row, 2, size_item)

            # Cached At
            cached_at = asset.get('cached_at', '')
            if cached_at:
                # Format datetime
                cached_at = cached_at.split('T')[0] + ' ' + cached_at.split('T')[1].split('.')[0]
            cached_item = QTableWidgetItem(cached_at)
            self.table.setItem(row, 3, cached_item)

            # URL
            url = asset.get('url', '')
            url_item = QTableWidgetItem(url)
            self.table.setItem(row, 4, url_item)

            # Hash
            hash_val = asset.get('hash', '')
            hash_item = QTableWidgetItem(hash_val)
            self.table.setItem(row, 5, hash_item)

        # Update stats
        stats = self.cache_manager.get_cache_stats()
        total_assets = stats['total_assets']
        total_size = self._format_size(stats['total_size'])
        self.stats_label.setText(f'Total: {total_assets} assets | Size: {total_size}')

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

        search_text = self.search_box.text().strip().lower()
        if search_text:
            assets = [a for a in assets if search_text in a['id'].lower()]

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
            if self.cache_manager.export_asset(asset['id'], asset['type']):
                exported_count += 1

        log_buffer.log('Cache', f'Exported {exported_count}/{len(assets)} assets')
        QMessageBox.information(
            self,
            'Export Complete',
            f'Exported {exported_count} asset(s)\n\nLocation: {self.cache_manager.export_dir}'
        )

    def _delete_selected(self):
        """Delete the selected asset."""
        asset = self._get_selected_asset()
        if not asset:
            QMessageBox.warning(self, 'No Selection', 'Please select an asset to delete')
            return

        reply = QMessageBox.question(
            self,
            'Delete Asset',
            f"Delete asset {asset['id']}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            if self.cache_manager.delete_asset(asset['id'], asset['type']):
                log_buffer.log('Cache', f"Deleted asset {asset['id']}")
                self._refresh_assets()
            else:
                QMessageBox.critical(self, 'Error', 'Failed to delete asset')

    def _clear_cache(self):
        """Clear all cached assets."""
        stats = self.cache_manager.get_cache_stats()
        total_assets = stats['total_assets']

        if total_assets == 0:
            QMessageBox.information(self, 'Empty Cache', 'Cache is already empty')
            return

        reply = QMessageBox.question(
            self,
            'Clear Cache',
            f'Delete all {total_assets} cached asset(s)?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            deleted = self.cache_manager.clear_cache()
            log_buffer.log('Cache', f'Cleared cache: {deleted} assets deleted')
            self._refresh_assets()
            QMessageBox.information(self, 'Success', f'Deleted {deleted} asset(s)')

    def _on_selection_changed(self):
        """Handle table selection change to preview asset."""
        asset = self._get_selected_asset()
        if not asset:
            self._clear_preview()
            return

        # Hide all preview widgets first
        self.obj_viewer.hide()
        self.image_label.hide()
        self.audio_container.hide()
        self.text_viewer.hide()

        # Stop any playing audio
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None

        asset_type = asset['type']
        asset_id = asset['id']

        try:
            # Get asset data
            data = self.cache_manager.get_asset(asset_id, asset_type)
            if not data:
                self._show_text_preview(f'Failed to load asset {asset_id}')
                return

            # Preview based on type
            if asset_type == 4:  # Mesh
                self._preview_mesh(data, asset_id)
            elif asset_type in [1, 13]:  # Image, Decal
                self._preview_image(data)
            elif asset_type == 3:  # Audio
                self._preview_audio(data, asset_id)
            else:
                # Show as hex dump for other types
                self._preview_hex(data, asset)

        except Exception as e:
            self._show_text_preview(f'Error previewing asset: {e}')

    def _clear_preview(self):
        """Clear all preview widgets."""
        self.obj_viewer.hide()
        self.obj_viewer.clear()
        self.image_label.hide()
        self.image_label.setText('Select an asset to preview')
        self.audio_container.hide()
        if self.audio_player:
            self.audio_player.stop()
            self.audio_player.deleteLater()
            self.audio_player = None
        self.text_viewer.hide()
        self.text_viewer.clear()

    def _preview_mesh(self, data: bytes, asset_id: str):
        """Preview a mesh asset in 3D."""
        try:
            # Convert mesh to OBJ
            obj_content = mesh_processing.convert(data)
            if obj_content:
                self.obj_viewer.load_obj(obj_content, asset_id)
                self.obj_viewer.show()
            else:
                self._show_text_preview('Failed to convert mesh to OBJ format')
        except Exception as e:
            self._show_text_preview(f'Mesh conversion error: {e}')

    def _preview_image(self, data: bytes):
        """Preview an image asset."""
        try:
            image = Image.open(io.BytesIO(data))
            # Convert to QPixmap
            image = image.convert('RGBA')
            qimage = QImage(
                image.tobytes(),
                image.width,
                image.height,
                QImage.Format.Format_RGBA8888
            )
            pixmap = QPixmap.fromImage(qimage)

            # Scale to fit label while maintaining aspect ratio
            scaled = pixmap.scaled(
                800, 600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )

            self.image_label.setPixmap(scaled)
            self.image_label.show()
        except Exception as e:
            self._show_text_preview(f'Image preview error: {e}')

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

            # Create audio player
            self.audio_player = AudioPlayerWidget(str(temp_file), self)

            # Clear previous audio widgets
            while self.audio_container_layout.count():
                child = self.audio_container_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()

            # Add new audio player
            self.audio_container_layout.addWidget(self.audio_player)
            self.audio_container.show()

        except Exception as e:
            self._show_text_preview(f'Audio preview error: {e}')
            log_buffer.log('Cache', f'Audio preview error: {e}')

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
        self.text_viewer.setPlainText(text)
        self.text_viewer.show()
