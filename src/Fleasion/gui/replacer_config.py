"""Replacer config window."""

import json
from copy import deepcopy
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..utils import CONFIGS_FOLDER, PREJSONS_DIR, get_icon_path, log_buffer, open_folder
from .json_viewer import JsonTreeViewer


class UndoManager:
    """Undo history manager."""

    def __init__(self, max_history: int = 50):
        self.history: list[list] = []
        self.max_history = max_history

    def save_state(self, rules: list):
        """Save a state to history."""
        self.history.append(deepcopy(rules))
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def undo(self) -> list | None:
        """Undo to previous state."""
        if len(self.history) > 1:
            self.history.pop()
            return deepcopy(self.history[-1])
        if len(self.history) == 1:
            return deepcopy(self.history[0])
        return None

    def clear(self):
        """Clear history."""
        self.history.clear()


class ReplacerConfigWindow(QDialog):
    """Replacer configuration window with tabs."""

    def __init__(self, config_manager, proxy_master=None):
        super().__init__()
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self.undo_manager = UndoManager()
        self.undo_manager.save_state(self.config_manager.replacement_rules)
        self.config_enabled_vars = {}

        self.setWindowTitle(f'{self.config_manager.settings.get("app_name", "FleasionNT")} - Config')
        self.resize(900, 750)
        self.setMinimumSize(800, 650)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._setup_ui()
        self._set_icon()
        self._refresh_tree()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI with tabs."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create Replacer tab
        replacer_tab = self._create_replacer_tab()
        self.tab_widget.addTab(replacer_tab, 'Replacer')

        # Create Cache tab if proxy_master is available
        if self.proxy_master and hasattr(self.proxy_master, 'cache_manager'):
            cache_tab = self._create_cache_tab()
            self.tab_widget.addTab(cache_tab, 'Cache')

        main_layout.addWidget(self.tab_widget)

        self.setLayout(main_layout)

        # Setup keyboard shortcuts
        from PyQt6.QtGui import QKeySequence, QShortcut

        undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        undo_shortcut.activated.connect(self._do_undo)

        delete_shortcut = QShortcut(QKeySequence('Delete'), self)
        delete_shortcut.activated.connect(self._delete_selected)

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
        return CacheViewerTab(
            self.proxy_master.cache_manager,
            cache_scraper,
            self,
            config_manager=self.config_manager
        )

    def _create_config_section(self, parent_layout):
        """Create the configuration selector section."""
        config_group = QGroupBox('Configuration')
        config_layout = QVBoxLayout()

        # Row 1: Config editing selector
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Editing:'))

        self.config_combo = QComboBox()
        self.config_combo.addItems(self.config_manager.config_names)
        self.config_combo.setCurrentText(self.config_manager.last_config)
        self.config_combo.currentTextChanged.connect(self._on_config_change)
        row1.addWidget(self.config_combo)

        for text, action in [
            ('New', 'new'),
            ('Duplicate', 'dup'),
            ('Rename', 'rename'),
            ('Delete', 'delete'),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, a=action: self._config_action(a))
            row1.addWidget(btn)

        # No Textures checkbox
        self.strip_var = QCheckBox('No Textures')
        self.strip_var.setChecked(self.config_manager.strip_textures)
        self.strip_var.stateChanged.connect(self._on_strip_change)
        row1.addWidget(self.strip_var)

        row1.addStretch()
        config_layout.addLayout(row1)

        # Row 2: Enabled configs selector
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Enabled Configs:'))

        self.enabled_menu_btn = QPushButton('Select configs...')
        self.enabled_menu = QMenu(self.enabled_menu_btn)
        self.enabled_menu_btn.setMenu(self.enabled_menu)
        row2.addWidget(self.enabled_menu_btn)

        self._rebuild_enabled_menu()

        row2.addStretch()
        config_layout.addLayout(row2)

        config_group.setLayout(config_layout)
        parent_layout.addWidget(config_group)

    def _create_tree_section(self, parent_layout):
        """Create the rules tree section."""
        # Label
        label_layout = QHBoxLayout()
        title_label = QLabel('Replacement Profiles:')
        title_label.setStyleSheet('font-weight: bold;')
        label_layout.addWidget(title_label)

        hint_label = QLabel('(Ctrl+Z to undo)')
        hint_label.setStyleSheet('color: gray;')
        label_layout.addWidget(hint_label)
        label_layout.addStretch()
        parent_layout.addLayout(label_layout)

        # Tree
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Status', 'Profile Name', 'Action', 'Asset IDs', 'Replace With'])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

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
        edit_layout = QVBoxLayout()

        # Profile name
        name_layout = QHBoxLayout()
        name_layout.addWidget(QLabel('Profile Name:'))
        self.name_entry = QLineEdit()
        name_layout.addWidget(self.name_entry)
        edit_layout.addLayout(name_layout)

        # Action radio buttons
        action_layout = QHBoxLayout()
        action_layout.addWidget(QLabel('Action:'))
        self.replace_radio = QRadioButton('Replace with ID')
        self.replace_radio.setChecked(True)
        self.replace_radio.toggled.connect(self._on_action_change)
        action_layout.addWidget(self.replace_radio)

        self.remove_radio = QRadioButton('Remove entirely')
        action_layout.addWidget(self.remove_radio)
        action_layout.addStretch()
        edit_layout.addLayout(action_layout)

        # Asset IDs
        ids_layout = QHBoxLayout()
        ids_layout.addWidget(QLabel('Asset IDs:'))
        self.replace_entry = QLineEdit()
        ids_layout.addWidget(self.replace_entry)
        edit_layout.addLayout(ids_layout)

        # Replace with ID
        with_layout = QHBoxLayout()
        with_layout.addWidget(QLabel('Replace With ID:'))
        self.with_entry = QLineEdit()
        with_layout.addWidget(self.with_entry)
        with_layout.addStretch()
        edit_layout.addLayout(with_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        for text, callback in [
            ('Add New', self._add_rule),
            ('Load Selected', self._load_selected),
            ('Update Selected', self._update_selected),
            ('Delete Selected', self._delete_selected),
            ('Enable Selected', self._enable_selected),
            ('Disable Selected', self._disable_selected),
            ('Import JSON', self._open_json),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(callback)
            btn_layout.addWidget(btn)
        edit_layout.addLayout(btn_layout)

        edit_group.setLayout(edit_layout)
        parent_layout.addWidget(edit_group)

    def _create_footer(self, parent_layout):
        """Create the footer section."""
        footer_layout = QHBoxLayout()

        path_label = QLabel(f'Configs: {CONFIGS_FOLDER}')
        path_label.setStyleSheet('color: gray; font-size: 8pt;')
        footer_layout.addWidget(path_label)

        footer_layout.addStretch()

        configs_btn = QPushButton('Open Configs')
        configs_btn.clicked.connect(lambda: open_folder(CONFIGS_FOLDER))
        footer_layout.addWidget(configs_btn)

        prejsons_btn = QPushButton('Open PreJsons')
        prejsons_btn.clicked.connect(lambda: open_folder(PREJSONS_DIR))
        footer_layout.addWidget(prejsons_btn)

        undo_btn = QPushButton('Undo (Ctrl+Z)')
        undo_btn.clicked.connect(self._do_undo)
        footer_layout.addWidget(undo_btn)

        parent_layout.addLayout(footer_layout)

    def _rebuild_enabled_menu(self):
        """Rebuild the enabled configs menu."""
        self.enabled_menu.clear()
        self.config_enabled_vars.clear()

        for name in self.config_manager.config_names:
            action = self.enabled_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_config_enabled(name))
            action.triggered.connect(
                lambda checked, n=name: self._on_config_toggle(n, checked)
            )
            self.config_enabled_vars[name] = action

        self._update_enabled_menu_text()

    def _update_enabled_menu_text(self):
        """Update the enabled menu button text."""
        enabled = self.config_manager.enabled_configs
        if not enabled:
            self.enabled_menu_btn.setText('None selected')
        elif len(enabled) == 1:
            self.enabled_menu_btn.setText(enabled[0])
        else:
            self.enabled_menu_btn.setText(f'{len(enabled)} configs enabled')

    def _on_config_toggle(self, name: str, checked: bool):
        """Handle config toggle."""
        self.config_manager.set_config_enabled(name, checked)
        self._update_enabled_menu_text()
        status = 'Enabled' if checked else 'Disabled'
        log_buffer.log('Config', f'{status}: {name}')

    def _refresh_tree(self):
        """Refresh the tree view."""
        self.tree.clear()
        for i, rule in enumerate(self.config_manager.replacement_rules):
            name = rule.get('name', f'Profile {i + 1}')
            is_rm = rule.get('remove', False)
            enabled = rule.get('enabled', True)

            item = QTreeWidgetItem(
                [
                    'On' if enabled else 'Off',
                    name,
                    'Remove' if is_rm else 'Replace',
                    f"{len(rule.get('replace_ids', []))} ID(s)",
                    '-' if is_rm else str(rule.get('with_id', '')),
                ]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            self.tree.addTopLevelItem(item)

    def _refresh_combo(self):
        """Refresh the config combo box."""
        current = self.config_combo.currentText()
        self.config_combo.clear()
        self.config_combo.addItems(self.config_manager.config_names)
        self.config_combo.setCurrentText(self.config_manager.last_config)
        self._rebuild_enabled_menu()

    def _on_config_change(self):
        """Handle config selection change."""
        self.config_manager.last_config = self.config_combo.currentText()
        self.undo_manager.clear()
        self.undo_manager.save_state(self.config_manager.replacement_rules)
        self._refresh_tree()
        log_buffer.log('Config', f'Now editing: {self.config_combo.currentText()}')

    def _on_strip_change(self):
        """Handle strip textures change."""
        self.config_manager.strip_textures = self.strip_var.isChecked()

    def _on_action_change(self):
        """Handle action radio button change."""
        self.with_entry.setEnabled(self.replace_radio.isChecked())

    def _config_action(self, action: str):
        """Handle config management actions."""
        current = self.config_manager.last_config

        if action == 'new':
            name, ok = QInputDialog.getText(self, 'New Config', 'Name:')
            if ok and name and self.config_manager.create_config(name.strip()):
                self.config_manager.last_config = name.strip()
                self.undo_manager.clear()
                self.undo_manager.save_state(self.config_manager.replacement_rules)
                self._refresh_combo()
                self._refresh_tree()

        elif action == 'dup':
            name, ok = QInputDialog.getText(
                self, 'Duplicate', f"Copy of '{current}':"
            )
            if ok and name and self.config_manager.duplicate_config(current, name.strip()):
                self.config_manager.last_config = name.strip()
                self.undo_manager.clear()
                self.undo_manager.save_state(self.config_manager.replacement_rules)
                self._refresh_combo()
                self._refresh_tree()

        elif action == 'rename':
            name, ok = QInputDialog.getText(
                self, 'Rename', 'New name:', text=current
            )
            if (
                ok
                and name
                and name.strip() != current
                and self.config_manager.rename_config(current, name.strip())
            ):
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
            column = self.tree.columnAt(pos.x())

            if column == 0:  # Status
                enabled = rule.get('enabled', True)
                text = 'Disable Profile' if enabled else 'Enable Profile'
                menu.addAction(text, lambda: self._toggle_profile(idx))
            elif column == 1:  # Name
                menu.addAction('Rename Profile', lambda: self._rename_profile(idx))
            elif column == 3:  # Asset IDs
                menu.addAction('Edit Asset IDs', lambda: self._edit_asset_ids(idx))
            elif column == 4:  # Replace With
                if not rule.get('remove'):
                    menu.addAction('Edit Replace With ID', lambda: self._edit_replace_with(idx))

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
        text_edit.setPlainText('\n'.join(str(i) for i in ids))
        layout.addWidget(text_edit)

        def save_ids():
            content = text_edit.toPlainText().strip()
            new_ids = []
            for line in content.split('\n'):
                for part in line.replace(',', ' ').replace(';', ' ').split():
                    try:
                        new_ids.append(int(part.strip()))
                    except ValueError:
                        pass
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

        save_btn = QPushButton('Save & Close')
        save_btn.clicked.connect(lambda: (save_ids(), dialog.accept()))
        btn_layout.addWidget(save_btn)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        dialog.show()

    def _edit_replace_with(self, idx: int):
        """Edit replace with ID."""
        rules = self.config_manager.replacement_rules
        if idx >= len(rules):
            return

        rule = rules[idx]
        old_id = rule.get('with_id', '')
        new_id, ok = QInputDialog.getText(
            self, 'Edit Replace With', 'New ID:', text=str(old_id)
        )
        if ok and new_id and new_id.strip():
            try:
                rules_copy = [r.copy() for r in rules]
                rules_copy[idx]['with_id'] = int(new_id.strip())
                self._save_with_undo(rules_copy)
                self._refresh_tree()
            except ValueError:
                QMessageBox.critical(self, 'Error', 'Invalid ID - must be a number')

    def _parse_ids(self, text: str) -> list[int]:
        """Parse IDs from text."""
        ids = []
        for part in text.replace(';', ',').replace(' ', ',').split(','):
            if part.strip():
                try:
                    ids.append(int(part.strip()))
                except ValueError:
                    pass
        return ids

    def _clear_entries(self):
        """Clear input fields."""
        self.name_entry.clear()
        self.replace_entry.clear()
        self.with_entry.clear()
        self.replace_radio.setChecked(True)

    def _get_rule_from_entries(self) -> dict | None:
        """Get rule from input fields."""
        ids = self._parse_ids(self.replace_entry.text())
        if not ids:
            QMessageBox.critical(self, 'Error', 'Enter at least one asset ID')
            return None

        is_rm = self.remove_radio.isChecked()
        rule = {
            'name': self.name_entry.text().strip()
            or f'Profile {len(self.config_manager.replacement_rules) + 1}',
            'replace_ids': ids,
            'remove': is_rm,
            'enabled': True,
        }

        if not is_rm:
            try:
                rule['with_id'] = int(self.with_entry.text().strip())
            except ValueError:
                QMessageBox.critical(self, 'Error', 'Invalid Replace With ID')
                return None

        return rule

    def _add_rule(self):
        """Add a new rule."""
        if rule := self._get_rule_from_entries():
            rules = self.config_manager.replacement_rules.copy()
            rules.append(rule)
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()
            log_buffer.log('Config', f"Added profile: {rule['name']}")

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

        if rule.get('remove'):
            self.remove_radio.setChecked(True)
        else:
            self.with_entry.setText(str(rule.get('with_id', '')))

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

    def _open_json(self):
        """Open JSON file for import."""
        initial_dir = str(PREJSONS_DIR) if PREJSONS_DIR.exists() else None
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            'Open JSON',
            initial_dir,
            'JSON Files (*.json);;All Files (*.*)',
        )

        if not file_path:
            return

        try:
            with Path(file_path).open(encoding='utf-8') as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            QMessageBox.critical(self, 'Error', f'Failed: {e}')
            return

        def on_ids(ids):
            cur = self.replace_entry.text()
            self.replace_entry.setText(
                (cur + ', ' if cur.strip() else '') + ', '.join(str(x) for x in ids)
            )

        def on_repl(val):
            self.with_entry.setText(str(val))
            self.replace_radio.setChecked(True)

        viewer = JsonTreeViewer(self, data, Path(file_path).name, on_ids, on_repl)
        viewer.show()
