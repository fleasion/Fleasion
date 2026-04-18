"""Replacer config window."""

import json
import urllib.request
from copy import deepcopy
from pathlib import Path
import time
from typing import Union
from urllib.error import URLError

from PyQt6.QtCore import Qt, QByteArray
from PyQt6.QtGui import QPixmap, QPainter, QColor, QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..utils import APP_NAME, CONFIGS_FOLDER, PREJSONS_DIR, get_icon_path, log_buffer, open_folder
from .json_viewer import JsonTreeViewer


class UndoManager:
    """Undo history manager."""

    def __init__(self, max_history: int = 50):
        self.history: list[list] = []
        self.future: list[list] = []
        self.max_history = max_history

    def save_state(self, rules: list):
        """Save a state to history."""
        self.history.append(deepcopy(rules))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.future.clear()

    def undo(self) -> list | None:
        """Undo to previous state."""
        if len(self.history) > 1:
            self.future.append(self.history.pop())
            return deepcopy(self.history[-1])
        if len(self.history) == 1:
            return deepcopy(self.history[0])
        return None

    def redo(self) -> list | None:
        """Redo a previously undone state."""
        if self.future:
            state = self.future.pop()
            self.history.append(state)
            return deepcopy(state)
        return None

    def clear(self):
        """Clear history."""
        self.history.clear()
        self.future.clear()


class ReplacerConfigWindow(QDialog):
    """Replacer configuration window with tabs."""

    def __init__(self, config_manager, proxy_master=None, mod_manager=None, roblox_monitor=None, system_tray=None):
        super().__init__()
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.roblox_monitor = roblox_monitor
        self._system_tray = system_tray
        self.undo_manager = UndoManager()
        self.undo_manager.save_state(self.config_manager.replacement_rules)
        self.config_enabled_vars = {}
        self._asset_types_popup_last_closed = 0.0
        self._dialog_asset_types_popup_last_closed = 0.0

        self.setWindowTitle(f'{APP_NAME} - Dashboard')
        self.resize(900, 750)
        self.setMinimumSize(800, 650)

        # Set window flags to allow minimize/maximize
        flags = (
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        # Apply always on top if enabled
        if self.config_manager.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self._setup_ui()
        self._set_icon()
        self._refresh_tree()

        # Restore geometry
        geometry_hex = self.config_manager.window_geometry
        if geometry_hex:
            self.restoreGeometry(QByteArray.fromHex(geometry_hex.encode('utf-8')))

    def closeEvent(self, event):
        """Save window geometry on close."""
        self.config_manager.window_geometry = self.saveGeometry().toHex().data().decode('utf-8')
        if (
            self._system_tray is not None
            and self._system_tray.config_manager.close_to_tray
            and not self._system_tray._exiting
        ):
            try:
                self._system_tray.notify_dashboard_closed()
            except Exception:
                pass
        super().closeEvent(event)

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI with tabs."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create Replacer tab
        replacer_tab = self._create_replacer_tab()
        self.tab_widget.addTab(replacer_tab, 'Replacer')

        # Create Cache tab if proxy_master is available
        if self.proxy_master and hasattr(self.proxy_master, 'cache_manager'):
            cache_tab = self._create_cache_tab()
            self.tab_widget.addTab(cache_tab, 'Scraper')

        # Create Modifications tab
        if self._mod_manager is not None:
            from .modifications_tab import ModificationsTab
            modifications_tab = ModificationsTab(self._mod_manager, self.roblox_monitor)
            self.tab_widget.addTab(modifications_tab, 'Modifications')

        # Create Rando Stuff tab
        from .rando_stuff_tab import RandoStuffTab
        self._rando_stuff_tab = RandoStuffTab(config_manager=self.config_manager)

        # Create Subplace Joiner tab
        from .subplace_joiner_tab import SubplaceJoinerTab
        self._subplace_tab = SubplaceJoinerTab(rando_tab=self._rando_stuff_tab)
        self._rando_stuff_tab.selected_account_changed.connect(self._subplace_tab.set_selected_account)
        self.tab_widget.addTab(self._subplace_tab, 'Subplace Joiner')
        if self.proxy_master is not None:
            self.proxy_master.register_module_interceptor(self._subplace_tab)

        self.tab_widget.addTab(self._rando_stuff_tab, 'Miscellaneous')
        if self.proxy_master is not None:
            self.proxy_master.register_module_interceptor(self._rando_stuff_tab)

        # Create Settings tab
        from .settings_tab import SettingsTab
        self._settings_tab = SettingsTab(self.config_manager, system_tray=self._system_tray)
        self.tab_widget.addTab(self._settings_tab, 'Settings')

        main_layout.addWidget(self.tab_widget)

        self.setLayout(main_layout)

        # Setup keyboard shortcuts
        from PyQt6.QtGui import QKeySequence, QShortcut

        undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        undo_shortcut.activated.connect(self._do_undo)

        delete_shortcut = QShortcut(QKeySequence('Delete'), self)
        delete_shortcut.activated.connect(self._delete_selected)

        redo_shortcut = QShortcut(QKeySequence('Ctrl+Y'), self)
        redo_shortcut.activated.connect(self._do_redo)

        escape_shortcut = QShortcut(QKeySequence('Escape'), self)
        escape_shortcut.activated.connect(self.close)

    def _create_replacer_tab(self):
        """Create the replacer configuration tab."""
        replacer_widget = QWidget()
        replacer_layout = QVBoxLayout()
        replacer_layout.setContentsMargins(0, 0, 0, 0)

        # Config selector section
        self._create_config_section(replacer_layout)

        # Rules tree section
        self._create_tree_section(replacer_layout)

        # Edit section
        self._create_edit_section(replacer_layout)

        # Footer
        self._create_footer(replacer_layout)

        replacer_widget.setLayout(replacer_layout)
        return replacer_widget

    def _create_cache_tab(self):
        """Create the cache viewer tab."""
        from ..cache import CacheViewerTab

        cache_scraper = getattr(self.proxy_master, 'cache_scraper', None)
        tab = CacheViewerTab(
            self.proxy_master.cache_manager,
            cache_scraper,
            self,
            config_manager=self.config_manager
        )
        # Store direct reference so Send-to-Replacer can find the entry fields
        # regardless of how Qt re-parents the widget when added to QTabWidget.
        tab._replacer_window_ref = self
        self._cache_viewer_tab = tab
        return tab

    def _create_config_section(self, parent_layout):
        """Create the configuration selector section."""
        config_group = QGroupBox('Configuration')
        config_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        config_layout = QVBoxLayout()

        # Row 1: Configuration controls
        row1 = QHBoxLayout()
        editing_label = QLabel('Editing:')
        editing_label.setFixedWidth(50)
        row1.addWidget(editing_label)

        # Use button with menu (same style as enabled configs)
        # Prepend a single space plus a tiny hair-space to give a subtle gap
        self.config_menu_btn = QPushButton(' \u200A' + self.config_manager.last_config)
        self.config_menu = QMenu(self.config_menu_btn)
        self.config_menu.aboutToShow.connect(self._rebuild_editing_menu)
        self.config_menu_btn.setMenu(self.config_menu)
        row1.addWidget(self.config_menu_btn)

        self._rebuild_editing_menu()

        row1.addSpacing(12)

        enabled_label = QLabel('Enabled:')
        enabled_label.setFixedWidth(50)
        row1.addWidget(enabled_label)

        self.enabled_menu_btn = QPushButton('Select...')
        self.enabled_menu = QMenu(self.enabled_menu_btn)
        # Install event filter to keep menu open on checkbox click
        self.enabled_menu.installEventFilter(self)
        self.enabled_menu.aboutToShow.connect(self._rebuild_enabled_menu)
        self.enabled_menu_btn.setMenu(self.enabled_menu)
        row1.addWidget(self.enabled_menu_btn)

        self._rebuild_enabled_menu()

        row1.addSpacing(8)

        separator = QLabel('|')
        separator.setStyleSheet('padding-bottom: 6px;')
        row1.addWidget(separator)

        row1.addSpacing(8)

        for text, action in [
            ('New', 'new'),
            ('Duplicate', 'dup'),
            ('Rename', 'rename'),
            ('Delete', 'delete'),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, a=action: self._config_action(a))
            row1.addWidget(btn)

        # Removed: No Textures checkbox

        row1.addStretch()
        config_layout.addLayout(row1)

        config_group.setLayout(config_layout)
        parent_layout.addWidget(config_group)

    def _create_tree_section(self, parent_layout):
        """Create the rules tree section."""
        # Label
        label_layout = QHBoxLayout()
        title_label = QLabel('Replacement Profiles:')
        title_label.setStyleSheet('font-weight: bold; padding-left: 5px;')
        label_layout.addWidget(title_label)

        label_layout.addStretch()
        parent_layout.addLayout(label_layout)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Status', 'Profile Name', 'Mode', 'Asset IDs', 'Replacement'])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        self.tree.setSortingEnabled(True)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        parent_layout.addWidget(self.tree)

    def _create_edit_section(self, parent_layout):
        """Create the add/edit profile section."""
        edit_group = QGroupBox('Add/Edit Profile')
        edit_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        edit_layout = QVBoxLayout()
        edit_layout.setSpacing(4)

        # Profile name
        name_layout = QHBoxLayout()
        name_layout.setSpacing(5)
        label0 = QLabel('Profile Name:')
        label0.setFixedWidth(85)
        name_layout.addWidget(label0)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText('Optional profile name')
        name_layout.addWidget(self.name_entry)
        edit_layout.addLayout(name_layout)

        # Asset IDs
        ids_layout = QHBoxLayout()
        ids_layout.setSpacing(5)
        label = QLabel('Asset IDs:')
        label.setFixedWidth(85)
        ids_layout.addWidget(label)
        self.replace_entry = QLineEdit()
        self.replace_entry.setPlaceholderText('IDs or AssetTypes separated by commas, spaces, or semicolons')
        ids_layout.addWidget(self.replace_entry)
        
        # Add Asset Types filter button
        self.asset_types_btn = QPushButton('Asset Types')
        self.asset_types_btn.setFixedWidth(80)
        self.asset_types_btn.clicked.connect(self._show_asset_types_popup)
        from ..cache.cache_viewer import CategoryFilterPopup
        self.asset_types_popup = CategoryFilterPopup(parent=self)
        self.asset_types_popup.filters_changed.connect(self._on_asset_types_changed)
        self.asset_types_popup.aboutToHide.connect(self._mark_asset_types_popup_closed)
        ids_layout.addWidget(self.asset_types_btn)
        
        edit_layout.addLayout(ids_layout)

        # Replacement field (auto-detects mode)
        replace_layout = QHBoxLayout()
        replace_layout.setSpacing(5)
        label2 = QLabel('Replace With:')
        label2.setFixedWidth(85)
        replace_layout.addWidget(label2)
        self.replacement_entry = QLineEdit()
        self.replacement_entry.setPlaceholderText('ID, URL (http://...), path (C:\\...), or empty to remove')
        replace_layout.addWidget(self.replacement_entry)
        browse_btn = QPushButton('Browse...')
        browse_btn.clicked.connect(self._browse_local_file)
        browse_btn.setFixedWidth(80)
        replace_layout.addWidget(browse_btn)
        edit_layout.addLayout(replace_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        for text, callback in [
            ('Add New', self._add_rule),
            ('Load Selected', self._load_selected),
            ('Update Selected', self._update_selected),
        ]:
            btn = QPushButton(text)
            btn.setMinimumWidth(130)
            btn.clicked.connect(callback)
            btn_layout.addWidget(btn)
        btn_layout.addStretch()
        import_btn = QPushButton('Scraped games...')
        import_btn.setMinimumWidth(130)
        import_btn.clicked.connect(self._open_prejsons_browser)
        btn_layout.addWidget(import_btn)
        edit_layout.addLayout(btn_layout)

        edit_group.setLayout(edit_layout)
        # Prevent edit group from expanding vertically
        edit_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        parent_layout.addWidget(edit_group)

    def _create_footer(self, parent_layout):
            """Create the footer section with buttons snapped to the right."""
            footer_widget = QWidget()
            footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            footer_layout = QHBoxLayout()
            footer_layout.setContentsMargins(0, 4, 0, 4)

            footer_layout.addSpacing(12)

            path_label = QLabel(f'Configs: {CONFIGS_FOLDER}')
            path_label.setStyleSheet('color: gray; font-size: 8pt; padding-left: 5px;')
            footer_layout.addWidget(path_label)

            footer_layout.addStretch()

            help_btn = QPushButton('?')
            help_btn.setMaximumWidth(25)
            help_btn.setToolTip('View keybinds')
            help_btn.clicked.connect(self._show_keybinds_help)
            footer_layout.addWidget(help_btn)

            clear_cache_btn = QPushButton('Clear Cache')
            clear_cache_btn.clicked.connect(self._clear_roblox_cache)
            footer_layout.addWidget(clear_cache_btn)

            configs_btn = QPushButton('Open Configs')
            configs_btn.clicked.connect(lambda: open_folder(CONFIGS_FOLDER))
            footer_layout.addWidget(configs_btn)

            undo_btn = QPushButton('Undo (Ctrl+Z)')
            undo_btn.clicked.connect(self._do_undo)
            footer_layout.addWidget(undo_btn)
            footer_layout.addSpacing(12)

            footer_widget.setLayout(footer_layout)
            parent_layout.addWidget(footer_widget)

    def _clear_roblox_cache(self):
        from .delete_cache import DeleteCacheWindow
        window = DeleteCacheWindow()
        window.show()

    def _show_keybinds_help(self):
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle('Keybinds')
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            '<b>All keybinds.</b><br>'
            '- Ctrl+Z — Undo last change<br>'
            '- Ctrl+Y — Redo last change<br>'
            '- Ctrl+A — Select all rows<br>'
            '- Delete — Delete selected row(s)<br>'
            '<br>'
            '<b>Tips.</b><br>'
            '- Right-click a profile to delete, enable, or disable it'
        )
        msg.exec()

    def _open_prejsons_browser(self):
        """Open the PreJsons browser dialog."""
        from .prejsons_dialog import PreJsonsDialog
        dialog = PreJsonsDialog(self)
        dialog.show()

    def eventFilter(self, obj, event):
        """Event filter to keep enabled menu open after clicking checkboxes."""
        from PyQt6.QtCore import QEvent
        if obj == self.enabled_menu and event.type() == QEvent.Type.MouseButtonRelease:
            # Check if click was on a checkable action
            action = self.enabled_menu.actionAt(event.pos())
            if action and action.isCheckable():
                # Toggle the action manually
                action.setChecked(not action.isChecked())
                action.triggered.emit(action.isChecked())
                # Return True to prevent menu from closing
                return True
        return super().eventFilter(obj, event)

    def _rebuild_enabled_menu(self):
        """Rebuild the enabled configs menu."""
        self.enabled_menu.clear()
        self.config_enabled_vars.clear()

        # Clean up enabled configs that no longer exist on disk
        current_configs = self.config_manager.config_names
        enabled = self.config_manager.enabled_configs
        for name in enabled[:]:  # Copy list to allow modification
            if name not in current_configs:
                self.config_manager.set_config_enabled(name, False)

        for name in current_configs:
            action = self.enabled_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_config_enabled(name))
            action.triggered.connect(
                lambda checked, n=name: self._on_config_toggle(n, checked)
            )
            self.config_enabled_vars[name] = action

        self._update_enabled_menu_text()
        # Resize the enabled menu to fit its longest entry (account for checkboxes)
        try:
            from PyQt6.QtGui import QFontMetrics

            fm = QFontMetrics(self.enabled_menu.font())
            max_text_width = 0
            for act in self.enabled_menu.actions():
                text = act.text() or ''
                w = fm.horizontalAdvance(text)
                if w > max_text_width:
                    max_text_width = w

            icon_space = 28
            padding = 30
            target_width = max_text_width + icon_space + padding
            if hasattr(self, 'enabled_menu_btn'):
                target_width = max(target_width, self.enabled_menu_btn.width())
            self.enabled_menu.setFixedWidth(target_width)
        except Exception:
            pass

    def _update_enabled_menu_text(self):
        """Update the enabled menu button text."""
        enabled = self.config_manager.enabled_configs
        if not enabled:
            self.enabled_menu_btn.setText('No Configs Enabled')
        elif len(enabled) == 1:
            self.enabled_menu_btn.setText(enabled[0])
        else:
            self.enabled_menu_btn.setText(f'{len(enabled)} configs enabled')
        # Keep the Editing button styled to reflect whether the currently
        # selected editing profile is enabled or not.
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _on_config_toggle(self, name: str, checked: bool):
        """Handle config toggle."""
        self.config_manager.set_config_enabled(name, checked)
        self._update_enabled_menu_text()
        status = 'Enabled' if checked else 'Disabled'
        log_buffer.log('Config', f'{status}: {name}')
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _refresh_tree(self):
        """Refresh the tree view."""
        self.tree.clear()
        for i, rule in enumerate(self.config_manager.replacement_rules):
            name = rule.get('name', f'Profile {i + 1}')
            enabled = rule.get('enabled', True)

            # Determine mode and display value
            mode = rule.get('mode', 'id')
            # Legacy support
            if 'remove' in rule and 'mode' not in rule:
                mode = 'remove' if rule.get('remove') else 'id'

            if mode == 'id':
                with_id = rule.get('with_id')
                if with_id is not None:
                    action = 'ID'
                    replace_with = str(with_id)
                else:
                    action = 'Remove'
                    replace_with = '-'
            elif mode == 'cdn':
                action = 'CDN'
                cdn_url = rule.get('cdn_url', '')
                # Truncate long URLs
                replace_with = cdn_url[:40] + '...' if len(cdn_url) > 40 else cdn_url
            elif mode == 'local':
                action = 'Local'
                local_path = rule.get('local_path', '')
                # Show just filename
                from pathlib import Path
                replace_with = Path(local_path).name if local_path else ''
            elif mode == 'remove':
                action = 'Remove'
                replace_with = '-'
            else:
                action = mode.upper()
                replace_with = '-'

            item = QTreeWidgetItem(
                [
                    'On' if enabled else 'Off',
                    name,
                    action,
                    f"{len(rule.get('replace_ids', []))} ID(s)",
                    replace_with,
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            self.tree.addTopLevelItem(item)

    def _refresh_combo(self):
        """Refresh the config button text."""
        # Keep one leading space plus a hair-space so icon and text are separated
        self.config_menu_btn.setText(' \u200A' + self.config_manager.last_config)
        self._rebuild_enabled_menu()
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _rebuild_editing_menu(self):
        """Rebuild the editing config menu."""
        self.config_menu.clear()
        current_configs = self.config_manager.config_names

        # If current editing config was deleted, switch to first available
        if self.config_manager.last_config not in current_configs and current_configs:
            self.config_manager.last_config = current_configs[0]
            self.config_menu_btn.setText(' \u200A' + current_configs[0])

        for name in current_configs:
            action = self.config_menu.addAction(name)
            action.triggered.connect(
                lambda checked, n=name: self._on_config_select(n)
            )
            # Add a small subtle red dot icon for profiles that are not enabled.
            try:
                if not self.config_manager.is_config_enabled(name):
                    action.setIcon(self._make_status_icon('#cc5555'))
                else:
                    # Mark enabled profiles with a subtle green dot
                    action.setIcon(self._make_status_icon('#55cc66'))
            except Exception:
                # If querying config state fails, leave icon empty
                action.setIcon(QIcon())
        # Ensure the Editing button reflects the enabled state after rebuild
        try:
            self._update_editing_button_style()
        except Exception:
            pass
        # Resize the editing menu to fit the longest profile name (plus icon and padding)
        try:
            from PyQt6.QtGui import QFontMetrics

            fm = QFontMetrics(self.config_menu.font())
            max_text_width = 0
            for act in self.config_menu.actions():
                text = act.text() or ''
                w = fm.horizontalAdvance(text)
                if w > max_text_width:
                    max_text_width = w

            icon_space = 22
            padding = 30
            target_width = max_text_width + icon_space + padding
            if hasattr(self, 'config_menu_btn'):
                target_width = max(target_width, self.config_menu_btn.width())
            self.config_menu.setFixedWidth(target_width)
        except Exception:
            pass

    def _on_config_select(self, name: str):
        """Handle config selection from menu."""
        if name != self.config_manager.last_config:
            self.config_manager.last_config = name
            # Keep a single space plus a hair-space between icon and text
            self.config_menu_btn.setText(' \u200A' + name)
            self.undo_manager.clear()
            self.undo_manager.save_state(self.config_manager.replacement_rules)
            self._refresh_tree()

        """Handle strip textures change."""
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _update_editing_button_style(self):
        """Color the Editing button text red if the currently edited profile
        is not enabled in the Enabled: menu.
        """
        # Guard if UI not yet created
        if not hasattr(self, 'config_menu_btn') or not hasattr(self, 'config_manager'):
            return

        name = self.config_manager.last_config
        try:
            enabled = self.config_manager.is_config_enabled(name)
        except Exception:
            enabled = False

        # Set the same small colored dot icon on the parent dropdown button
        try:
            color = '#55cc66' if enabled else '#cc5555'
            self.config_menu_btn.setIcon(self._make_status_icon(color))
        except Exception:
            try:
                self.config_menu_btn.setIcon(QIcon())
            except Exception:
                pass
        # Ensure button text color isn't used for state; the dot represents state now.
        self.config_menu_btn.setStyleSheet('')

    def _make_status_icon(self, color: str = '#cc5555', size: int = 12) -> QIcon:
        """Create a small circular QIcon of given color for menu actions.

        This uses native Qt QIcon/QPixmap drawing and avoids custom widget
        widgets so menu entries remain simple QActions.
        """
        try:
            pix = QPixmap(size, size)
            pix.fill(QColor(0, 0, 0, 0))
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(QColor(0, 0, 0, 0))
            margin = 2
            p.drawEllipse(margin, margin, size - margin * 2, size - margin * 2)
            p.end()
            return QIcon(pix)
        except Exception:
            return QIcon()

    def _browse_local_file(self):
        """Open file browser for local file selection."""
        current_val = self.replacement_entry.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            path = Path(current_val)
            if path.parent.exists():
                initial_dir = str(path)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            'Select Local File',
            initial_dir,
            'All Files (*.*)',
        )
        if file_path:
            self.replacement_entry.setText(file_path)

    def _config_action(self, action: str):
        """Handle config management actions."""
        current = self.config_manager.last_config

        if action == 'new':
            name, ok = QInputDialog.getText(self, 'New Config', 'Name:')
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif not self.config_manager.create_config(name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'dup':
            name, ok = QInputDialog.getText(
                self, 'Duplicate', f"Copy of '{current}':"
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif not self.config_manager.duplicate_config(current, name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'rename':
            name, ok = QInputDialog.getText(
                self, 'Rename', 'New name:', text=current
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif name != current and not self.config_manager.rename_config(current, name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                elif name != current:
                    self._refresh_combo()

        elif action == 'delete':
            if len(self.config_manager.config_names) <= 1:
                QMessageBox.critical(self, 'Error', 'Cannot delete last config')
            else:
                reply = QMessageBox.question(
                    self,
                    'Delete',
                    f"Delete '{current}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.config_manager.delete_config(current)
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

    def _save_with_undo(self, rules: list):
        """Save rules with undo tracking."""
        self.undo_manager.save_state(rules)
        self.config_manager.replacement_rules = rules

    def _do_undo(self):
        """Perform undo."""
        if prev := self.undo_manager.undo():
            self.config_manager.replacement_rules = prev
            self._refresh_tree()
            log_buffer.log('Config', 'Undo performed')

    def _do_redo(self):
        """Perform redo."""
        if next_state := self.undo_manager.redo():
            self.config_manager.replacement_rules = next_state
            self._refresh_tree()
            log_buffer.log('Config', 'Redo performed')

    def _show_context_menu(self, pos):
        """Show context menu for tree item."""
        item = self.tree.itemAt(pos)
        if not item:
            return

        selected_items = self.tree.selectedItems()
        rules = self.config_manager.replacement_rules

        menu = QMenu(self)

        # Multi-select operations (available when multiple items selected)
        if len(selected_items) > 1:
            menu.addAction('Enable Selected', self._enable_selected)
            menu.addAction('Disable Selected', self._disable_selected)
            menu.addSeparator()
            menu.addAction('Delete Selected', self._delete_selected)
        else:
            # Single item operations
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx >= len(rules):
                return

            rule = rules[idx]

            enabled = rule.get('enabled', True)
            text = 'Disable Profile' if enabled else 'Enable Profile'
            menu.addAction(text, lambda: self._toggle_profile(idx))
            menu.addAction('Rename Profile', lambda: self._rename_profile(idx))
            menu.addAction('Edit Asset IDs', lambda: self._edit_asset_ids(idx))
            menu.addAction('Edit Replacement', lambda: self._edit_replacement(idx))
            menu.addSeparator()
            menu.addAction('Delete Profile', lambda: self._delete_selected())

        if menu.actions():
            menu.exec(self.tree.mapToGlobal(pos))

    def _toggle_profile(self, idx: int):
        """Toggle profile enabled state."""
        rules = [r.copy() for r in self.config_manager.replacement_rules]
        if idx < len(rules):
            rules[idx]['enabled'] = not rules[idx].get('enabled', True)
            self._save_with_undo(rules)
            self._refresh_tree()

    def _rename_profile(self, idx: int):
        """Rename a profile."""
        rules = self.config_manager.replacement_rules
        if idx >= len(rules):
            return
        rule = rules[idx]
        old_name = rule.get('name', f'Profile {idx + 1}')
        name, ok = QInputDialog.getText(self, 'Rename', 'New name:', text=old_name)
        if ok and name and name.strip():
            rules_copy = [r.copy() for r in rules]
            rules_copy[idx]['name'] = name.strip()
            self._save_with_undo(rules_copy)
            self._refresh_tree()

    def _edit_asset_ids(self, idx: int):
        """Edit asset IDs for a profile."""
        rules = self.config_manager.replacement_rules
        if idx >= len(rules):
            return

        rule = rules[idx]
        name = rule.get('name', f'Profile {idx + 1}')
        ids = rule.get('replace_ids', [])

        dialog = QDialog(self)
        dialog.setWindowTitle(f'Asset IDs - {name}')
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(f'Profile: {name}')
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        count_label = QLabel(f'Total: {len(ids)} asset ID(s)')
        layout.addWidget(count_label)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText('\n'.join(str(i) for i in ids))
        layout.addWidget(text_edit)

        def save_ids():
            content = text_edit.toPlainText().strip()
            # Use robust ID parser to avoid deleting valid string-based asset types
            new_ids = self._parse_ids(content.replace('\n', ','))
            rules_copy = [r.copy() for r in self.config_manager.replacement_rules]
            rules_copy[idx]['replace_ids'] = new_ids
            self._save_with_undo(rules_copy)
            self._refresh_tree()
            count_label.setText(f'Total: {len(new_ids)} asset ID(s)')

        def copy_all():
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(', '.join(str(i) for i in ids))

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton('Copy All')
        copy_btn.clicked.connect(copy_all)
        btn_layout.addWidget(copy_btn)

        save_btn = QPushButton('Save and Close')
        save_btn.clicked.connect(lambda: (save_ids(), dialog.accept()))
        btn_layout.addWidget(save_btn)
        
        # Add Asset Types menu to Edit Asset IDs
        types_btn = QPushButton('Asset Types')
        
        def show_dialog_types_popup():
            def on_filters_changed(filters):
                from ..cache.cache_manager import CacheManager

                curr_content = text_edit.toPlainText().strip()
                curr_ids = self._parse_ids(curr_content.replace('\n', ','))
                new_ids = []
                for item in curr_ids:
                    if isinstance(item, int):
                        new_ids.append(item)
                    elif isinstance(item, str):
                        is_mapped = False
                        if item in _VIRTUAL_ANIM_TYPES:
                            is_mapped = True
                        else:
                            for tid, name in CacheManager.ASSET_TYPES.items():
                                if name.lower() == item.lower():
                                    is_mapped = True
                                    break
                        if not is_mapped:
                            new_ids.append(item)
                for tid in filters:
                    if tid in _VIRTUAL_ANIM_TYPES:
                        new_ids.append(tid)
                    elif tid in CacheManager.ASSET_TYPES:
                        new_ids.append(CacheManager.ASSET_TYPES[tid])

                if new_ids:
                    text_edit.setPlainText('\n'.join(str(i) for i in new_ids))
                else:
                    text_edit.setPlainText('')

            import time as _time
            from ..cache.cache_viewer import CategoryFilterPopup
            from ..cache.cache_manager import CacheManager
            from PyQt6.QtCore import QPoint

            if _time.monotonic() - self._dialog_asset_types_popup_last_closed < 0.25:
                return

            _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

            content = text_edit.toPlainText().strip()
            current_ids = self._parse_ids(content.replace('\n', ','))

            active_filters = set()
            for item in current_ids:
                if isinstance(item, str):
                    if item in _VIRTUAL_ANIM_TYPES:
                        active_filters.add(item)
                        continue
                    for tid, name in CacheManager.ASSET_TYPES.items():
                        if name.lower() == item.lower():
                            active_filters.add(tid)
                            break

            popup = getattr(self, '_dialog_asset_types_popup', None)
            if popup is not None:
                try:
                    if popup.isVisible():
                        popup.close()
                        self._dialog_asset_types_popup_last_closed = _time.monotonic()
                        return
                except RuntimeError:
                    popup = None

            if popup is None:
                popup = CategoryFilterPopup(parent=dialog, active_filters=active_filters)
                popup.filters_changed.connect(on_filters_changed)
                popup.aboutToHide.connect(self._mark_dialog_asset_types_popup_closed)
                self._dialog_asset_types_popup = popup
            else:
                popup.set_active_filters(active_filters)

            # Line up TOP LEFT of our popup menu with TOP LEFT of the Asset Types button
            pos = types_btn.mapToGlobal(types_btn.rect().topLeft())
            popup.popup(pos)

        types_btn.clicked.connect(show_dialog_types_popup)
        btn_layout.addWidget(types_btn)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        dialog.show()

    def _edit_replacement(self, idx: int):
        """Edit replacement value for a profile."""
        rules = self.config_manager.replacement_rules
        if idx >= len(rules):
            return

        rule = rules[idx]
        mode = rule.get('mode', 'id')

        # Get current value based on mode
        if mode == 'cdn':
            old_value = rule.get('cdn_url', '')
        elif mode == 'local':
            old_value = rule.get('local_path', '')
        else:
            old_value = str(rule.get('with_id', '')) if rule.get('with_id') is not None else ''

        dialog = QDialog(self)
        dialog.setWindowTitle('Edit Replacement')
        dialog.resize(400, 100)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()
        label = QLabel('Replacement (ID, URL, file path, or empty to remove):')
        layout.addWidget(label)

        line_edit = QLineEdit()
        line_edit.setText(old_value)
        layout.addWidget(line_edit)

        btn_layout = QHBoxLayout()

        browse_btn = QPushButton('Browse...')
        browse_btn.setFixedWidth(80)
        browse_btn.setAutoDefault(False)
        def _on_browse():
            current_val = line_edit.text().strip(' \t"\'')
            initial_dir = ''
            if current_val:
                path = Path(current_val)
                if path.parent.exists():
                    initial_dir = str(path)

            path, _ = QFileDialog.getOpenFileName(dialog, 'Select Local File', initial_dir, 'All Files (*.*)')
            if path:
                line_edit.setText(path)
                dialog.accept()
        browse_btn.clicked.connect(_on_browse)
        btn_layout.addWidget(browse_btn)

        btn_layout.addStretch()

        ok_btn = QPushButton('OK')
        ok_btn.setFixedWidth(80)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)

        if not dialog.exec():
            return

        new_value = line_edit.text().strip(' \t"\'')
        new_mode, extra = self._detect_mode(new_value)

        if '_raw' in extra:
            QMessageBox.critical(self, 'Error', f"Invalid replacement: '{extra['_raw']}'")
            return

        if new_mode == 'local' and 'local_path' in extra:
            if not Path(extra['local_path']).exists():
                QMessageBox.critical(self, 'Error', f"File not found: {extra['local_path']}")
                return

        rules_copy = [r.copy() for r in rules]
        # Clear old mode fields
        rules_copy[idx].pop('with_id', None)
        rules_copy[idx].pop('cdn_url', None)
        rules_copy[idx].pop('local_path', None)
        # Set new mode and value
        rules_copy[idx]['mode'] = new_mode
        rules_copy[idx].update(extra)
        self._save_with_undo(rules_copy)
        self._refresh_tree()

    def _parse_ids(self, text: str) -> list[Union[int, str]]:
        """Parse IDs from text."""
        ids: list[Union[int, str]] = []
        # Replace common separators with comma
        text = text.replace(';', ',').replace(' ', ',')
        for part in text.split(','):
            part = part.strip()
            if not part:
                continue
            # "parentId:mapIndex" or "TexturePack:N" — keep as-is
            if ':' in part:
                left, right = part.split(':', 1)
                if right.isdigit() and (left.isdigit() or left == 'TexturePack'):
                    ids.append(part)
                    continue
            try:
                ids.append(int(part))
            except ValueError:
                ids.append(part)
        return ids
        
    def _show_asset_types_popup(self):
        """Show the asset types popup menu."""
        from ..cache.cache_manager import CacheManager
        from PyQt6.QtCore import QPoint
        from PyQt6.QtWidgets import QApplication

        if time.monotonic() - self._asset_types_popup_last_closed < 0.25:
            return

        popup = self.asset_types_popup
        if popup.isVisible():
            popup.close()
            self._asset_types_popup_last_closed = time.monotonic()
            return

        _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        # Parse current text to update active filters
        current_text = self.replace_entry.text()
        current_ids = self._parse_ids(current_text)

        active_filters = set()
        for item in current_ids:
            if isinstance(item, str):
                if item in _VIRTUAL_ANIM_TYPES:
                    active_filters.add(item)
                    continue
                # Reverse lookup the asset type integer from name
                for tid, name in CacheManager.ASSET_TYPES.items():
                    if name.lower() == item.lower():
                        active_filters.add(tid)
                        break

        # Reuse the existing popup instance and update its current selection.
        popup = self.asset_types_popup
        popup.set_active_filters(active_filters)

        global_top_right = self.asset_types_btn.mapToGlobal(self.asset_types_btn.rect().topRight())
        popup_size = popup.sizeHint()

        # Ideal: bottom-right of popup aligns with top-right of button
        ideal_x = global_top_right.x() - popup_size.width()
        ideal_y = global_top_right.y() - popup_size.height()

        # Clamp to the screen the button is on so it never teleports to another monitor
        screen = QApplication.screenAt(global_top_right) or QApplication.primaryScreen()
        avail = screen.availableGeometry()
        x = max(avail.left(), min(ideal_x, avail.right() - popup_size.width()))
        y = max(avail.top(), min(ideal_y, avail.bottom() - popup_size.height()))

        popup.popup(QPoint(x, y))

    def _mark_asset_types_popup_closed(self):
        """Remember that the Asset Types popup just closed to debounce reopen clicks."""
        self._asset_types_popup_last_closed = time.monotonic()

    def _mark_dialog_asset_types_popup_closed(self):
        """Remember that the edit dialog Asset Types popup just closed."""
        self._dialog_asset_types_popup_last_closed = time.monotonic()
        
    def _on_asset_types_changed(self, filters):
        """Handle asset types selection change."""
        from ..cache.cache_manager import CacheManager

        _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        current_text = self.replace_entry.text().strip()
        current_ids = self._parse_ids(current_text)

        new_ids = []
        for item in current_ids:
            if isinstance(item, int):
                new_ids.append(item)
            elif isinstance(item, str):
                is_mapped = False
                for tid, name in CacheManager.ASSET_TYPES.items():
                    if name.lower() == item.lower():
                        is_mapped = True
                        break
                # Also treat virtual anim type strings as mapped (so they get removed/re-added cleanly)
                if item in _VIRTUAL_ANIM_TYPES:
                    is_mapped = True
                if not is_mapped:
                    new_ids.append(item)

        # Add the string representations of the selected filters
        for tid in filters:
            if tid in _VIRTUAL_ANIM_TYPES:
                new_ids.append(tid)
            elif tid in CacheManager.ASSET_TYPES:
                new_ids.append(CacheManager.ASSET_TYPES[tid])

        if new_ids:
            self.replace_entry.setText(', '.join(str(i) for i in new_ids).strip(', '))
        else:
            self.replace_entry.setText('')

    def _clear_entries(self):
        """Clear input fields."""
        self.name_entry.clear()
        self.replace_entry.clear()
        self.replacement_entry.clear()

    def _detect_mode(self, value: str) -> tuple[str, dict]:
        """Auto-detect mode from replacement value.

        Returns tuple of (mode, extra_fields).
        """
        value = value.strip()

        if not value:
            # Empty = remove
            return 'id', {}

        if value.startswith(('http://', 'https://')):
            # URL = CDN mode
            return 'cdn', {'cdn_url': value}

        # Check if it's a file path (contains path separators or drive letter)
        if '\\' in value or '/' in value or (len(value) > 2 and value[1] == ':'):
            return 'local', {'local_path': value}

        # Try to parse as integer (asset ID)
        try:
            return 'id', {'with_id': int(value)}
        except ValueError:
            pass

        # Could be a relative file path without separators
        if Path(value).exists():
            return 'local', {'local_path': str(Path(value).resolve())}

        # Default to treating as potential asset ID (will fail validation)
        return 'id', {'_raw': value}

    def _get_rule_from_entries(self) -> dict | None:
        """Get rule from input fields."""
        ids = self._parse_ids(self.replace_entry.text())
        if not ids:
            QMessageBox.critical(self, 'Error', 'Enter at least one asset ID')
            return None

        replacement = self.replacement_entry.text().strip()
        mode, extra = self._detect_mode(replacement)

        rule = {
            'name': self.name_entry.text().strip()
            or f'Profile {len(self.config_manager.replacement_rules) + 1}',
            'replace_ids': ids,
            'mode': mode,
            'enabled': True,
        }

        if mode == 'id':
            if 'with_id' in extra:
                rule['with_id'] = extra['with_id']
            elif '_raw' in extra:
                # Failed to parse as ID
                QMessageBox.critical(self, 'Error', f"Invalid replacement: '{extra['_raw']}'\nMust be an asset ID, URL, or file path")
                return None
            # Empty = remove (no with_id)
        elif mode == 'cdn':
            cdn_url = extra['cdn_url']
            # Validate URL is accessible
            try:
                req = urllib.request.Request(cdn_url, method='HEAD')
                req.add_header('User-Agent', 'Mozilla/5.0')
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status >= 400:
                        QMessageBox.warning(
                            self, 'Warning',
                            f'CDN URL returned status {resp.status}. Adding anyway.'
                        )
            except URLError as e:
                reply = QMessageBox.question(
                    self, 'URL Check Failed',
                    f'Could not verify CDN URL:\n{e}\n\nAdd anyway?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return None
            except Exception:
                pass  # Ignore other errors, allow adding
            rule['cdn_url'] = cdn_url
        elif mode == 'local':
            local_path = extra['local_path']
            if not Path(local_path).exists():
                QMessageBox.critical(self, 'Error', f'File not found: {local_path}')
                return None
            rule['local_path'] = local_path

        return rule

    def _add_rule(self):
        """Add a new rule."""
        if rule := self._get_rule_from_entries():
            rules = self.config_manager.replacement_rules.copy()
            rules.append(rule)
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()
            mode = rule.get('mode', 'id').upper()
            log_buffer.log('Config', f"Added profile: {rule['name']} ({mode})")

    def _load_selected(self):
        """Load selected rule into input fields."""
        items = self.tree.selectedItems()
        if not items:
            return

        idx = items[0].data(0, Qt.ItemDataRole.UserRole)
        rule = self.config_manager.replacement_rules[idx]

        self._clear_entries()
        self.name_entry.setText(rule.get('name', ''))
        self.replace_entry.setText(', '.join(str(x) for x in rule.get('replace_ids', [])))

        # Determine mode and set replacement field
        mode = rule.get('mode', 'id')
        # Legacy support
        if 'remove' in rule and 'mode' not in rule:
            if rule.get('remove'):
                # For legacy remove, leave replacement empty
                return
            mode = 'id'

        if mode == 'id':
            if (with_id := rule.get('with_id')) is not None:
                self.replacement_entry.setText(str(with_id))
        elif mode == 'cdn':
            self.replacement_entry.setText(rule.get('cdn_url', ''))
        elif mode == 'local':
            self.replacement_entry.setText(rule.get('local_path', ''))

    def _update_selected(self):
        """Update selected rule."""
        items = self.tree.selectedItems()
        if not items:
            return

        if rule := self._get_rule_from_entries():
            idx = items[0].data(0, Qt.ItemDataRole.UserRole)
            rules = self.config_manager.replacement_rules.copy()
            rule['enabled'] = rules[idx].get('enabled', True)
            rules[idx] = rule
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()

    def _delete_selected(self):
        """Delete selected rules."""
        items = self.tree.selectedItems()
        if not items:
            return

        indices = sorted([item.data(0, Qt.ItemDataRole.UserRole) for item in items], reverse=True)
        rules = self.config_manager.replacement_rules.copy()
        deleted_names = []

        for idx in indices:
            if idx < len(rules):
                deleted_names.append(rules[idx].get('name', f'Profile {idx + 1}'))
                rules.pop(idx)

        if deleted_names:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f"Deleted {len(deleted_names)} profile(s): {', '.join(deleted_names)}")

    def _enable_selected(self):
        """Enable selected rules."""
        items = self.tree.selectedItems()
        if not items:
            return

        rules = [r.copy() for r in self.config_manager.replacement_rules]
        enabled_count = 0
        for item in items:
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx < len(rules):
                if not rules[idx].get('enabled', True):
                    rules[idx]['enabled'] = True
                    enabled_count += 1

        if enabled_count > 0:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f'Enabled {enabled_count} profile(s)')

    def _disable_selected(self):
        """Disable selected rules."""
        items = self.tree.selectedItems()
        if not items:
            return

        rules = [r.copy() for r in self.config_manager.replacement_rules]
        disabled_count = 0
        for item in items:
            idx = item.data(0, Qt.ItemDataRole.UserRole)
            if idx < len(rules):
                if rules[idx].get('enabled', True):
                    rules[idx]['enabled'] = False
                    disabled_count += 1

        if disabled_count > 0:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f'Disabled {disabled_count} profile(s)')

