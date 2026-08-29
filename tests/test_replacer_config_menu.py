import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QPushButton

from fleasion.config import manager as manager_module
from fleasion.gui import replacer_config as replacer_config_module
from fleasion.gui.replacer_config import (
    ReplacerConfigWindow,
    _ScrollableConfigMenu,
)


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_scrollable_config_menu_constrains_height_and_scrolls():
    app = _qapp()
    popup = _ScrollableConfigMenu(checkable=True)
    popup.set_entries(
        [{'name': f'Config {i:02d}', 'checked': False} for i in range(40)],
        minimum_width=120,
    )
    natural_height = popup._natural_content_size.height()

    popup.constrain_to_available_geometry(QRect(0, 0, 500, 240), anchor_y=20)

    assert natural_height > popup.scroll_area.height()
    assert popup.scroll_area.height() <= 240
    assert popup.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert popup.sizeHint().width() == popup.scroll_area.width()
    assert popup.actionGeometry(popup.actions()[0]).width() == popup.scroll_area.width()
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_scrollable_config_menu_ignores_opening_release():
    app = _qapp()
    popup = _ScrollableConfigMenu(checkable=True)
    popup.set_entries([{'name': 'Default', 'checked': False}], minimum_width=120)
    toggles = []
    popup.item_toggled.connect(lambda name, checked: toggles.append((name, checked)))
    row = popup.item_widgets['Default']

    popup._guard_opening_mouse_release()
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(8, 8),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(row, event)

    assert not row.isChecked()
    assert toggles == []

    row.click()

    assert row.isChecked()
    assert toggles == [('Default', True)]
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_scrollable_config_menu_toggles_from_full_row_width():
    app = _qapp()
    popup = _ScrollableConfigMenu(checkable=True)
    popup.set_entries([{'name': 'a', 'checked': False}], minimum_width=260)
    toggles = []
    popup.item_toggled.connect(lambda name, checked: toggles.append((name, checked)))
    row = popup.item_widgets['a']
    row.resize(260, row.height())

    for event_type in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        event = QMouseEvent(
            event_type,
            QPointF(250, row.height() / 2),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(row, event)

    assert row.isChecked()
    assert toggles == [('a', True)]
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_open_configs_uses_config_manager_active_folder(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    opened = []
    monkeypatch.setattr(replacer_config_module, 'open_folder', lambda path: opened.append(path))
    config_manager = manager_module.ConfigManager()
    window = ReplacerConfigWindow(config_manager)
    try:
        open_buttons = [
            button
            for button in window.findChildren(QPushButton)
            if button.text() == 'Open Configs'
        ]
        assert len(open_buttons) == 1
        open_buttons[0].click()
        assert opened == [configs_dir]
    finally:
        window.close()
    assert app is not None


def test_enabled_menu_button_press_loads_new_config_file_from_disk(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    config_manager.set_config_enabled('Default', True)
    window = ReplacerConfigWindow(config_manager)
    try:
        assert list(window.enabled_menu.item_widgets) == ['Default']

        (configs_dir / 'z copy.json').write_text(
            json.dumps({'replacement_rules': []}),
            encoding='utf-8',
        )

        window._rebuild_enabled_menu()
        app.processEvents()

        assert 'z copy' in window.config_manager.config_names
        assert list(window.enabled_menu.item_widgets) == ['Default', 'z copy']
        assert not window.enabled_menu.item_widgets['z copy'].isChecked()
        assert window.enabled_menu_btn.text() == 'Default'
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_enabled_menu_uses_one_enabled_config_snapshot_per_rebuild(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    names = [f'Config {i:03d}' for i in range(120)]
    for name in names:
        (configs_dir / f'{name}.json').write_text(
            json.dumps({'replacement_rules': []}),
            encoding='utf-8',
        )
    config_manager.refresh_config_names()
    config_manager.enabled_configs = names[::2]

    is_enabled_calls = []
    original_is_config_enabled = config_manager.is_config_enabled

    def record_is_config_enabled(name):
        is_enabled_calls.append(name)
        return original_is_config_enabled(name)

    monkeypatch.setattr(config_manager, 'is_config_enabled', record_is_config_enabled)
    window = ReplacerConfigWindow(config_manager)
    try:
        is_enabled_calls.clear()
        monkeypatch.setattr(window, '_update_editing_button_style', lambda: None)
        window._rebuild_enabled_menu()

        assert not is_enabled_calls
        assert window.enabled_menu.item_widgets['Config 000'].isChecked()
        assert not window.enabled_menu.item_widgets['Config 001'].isChecked()
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_config_menu_buttons_do_not_rebuild_during_mouse_press(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    class SpyReplacerConfigWindow(ReplacerConfigWindow):
        def __init__(self, *args, **kwargs):
            self.editing_rebuilds = 0
            self.enabled_rebuilds = 0
            super().__init__(*args, **kwargs)

        def _rebuild_editing_menu(self, *args, **kwargs):
            self.editing_rebuilds += 1
            return super()._rebuild_editing_menu(*args, **kwargs)

        def _rebuild_enabled_menu(self, *args, **kwargs):
            self.enabled_rebuilds += 1
            return super()._rebuild_enabled_menu(*args, **kwargs)

    config_manager = manager_module.ConfigManager()
    window = SpyReplacerConfigWindow(config_manager)
    try:
        window.editing_rebuilds = 0
        window.enabled_rebuilds = 0
        window.config_menu_btn.pressed.emit()
        window.enabled_menu_btn.pressed.emit()
        assert window.editing_rebuilds == 0
        assert window.enabled_rebuilds == 0

        window.config_menu.aboutToShow.emit()
        assert window.editing_rebuilds == 1
        assert window.enabled_rebuilds == 1

        window.enabled_rebuilds = 0
        window.enabled_menu.aboutToShow.emit()
        assert window.enabled_rebuilds == 1
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_replace_ids_parser_splits_multiline_pastes(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    window = ReplacerConfigWindow(config_manager)
    try:
        assert window._parse_ids('101\n202\t303;404,505 606') == [101, 202, 303, 404, 505, 606]

        window.name_entry.setText('Multiline IDs')
        window.replace_entry.setText('101\n202\n303')
        window.replacement_entry.clear()
        window._add_rule()

        assert config_manager.replacement_rules[0]['replace_ids'] == [101, 202, 303]
        assert window.tree.topLevelItem(0).text(3) == '3 IDs'
    finally:
        window.close()
    assert app is not None


def test_replacer_explains_portable_configs_asset_path(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    window = ReplacerConfigWindow(config_manager)
    try:
        assert '/ExampleOBJ/Example.obj' in window.replacement_entry.placeholderText()
        tooltip = window.replacement_entry.toolTip()
        assert 'Configs/ExampleOBJ/Example.obj' in tooltip
        assert '10 folders deep' in tooltip
        if sys.platform == 'win32':
            assert r'C:\Mods\file.ext' in tooltip
        elif sys.platform == 'darwin':
            assert '/Users/name/Mods/file.ext' in tooltip
            assert r'C:\Mods\file.ext' not in tooltip
        else:
            assert '/home/name/Mods/file.ext' in tooltip
            assert r'C:\Mods\file.ext' not in tooltip
    finally:
        window.close()
    assert app is not None
