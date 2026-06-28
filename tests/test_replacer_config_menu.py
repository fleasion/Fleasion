import json
import os
from pathlib import Path
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication, QTreeWidget

from Fleasion.config import manager as manager_module
from Fleasion.gui.replacer_config import (
    _GROUP_GUIDE_STEP_PX,
    ReplacerConfigWindow,
    ReplacerTreeItem,
    _ProfileNameDelegate,
    _ROLE_DRAW_GROUP_ICON,
    _ROLE_GROUP_ICON_INDENT,
    _ROLE_SORT_BASE,
    _ScrollableConfigMenu,
)


def _qapp():
    return QApplication.instance() or QApplication([])


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
    assert app is not None


def test_scrollable_config_menu_recalculates_geometry_when_entries_change():
    app = _qapp()
    popup = _ScrollableConfigMenu(checkable=True)

    popup.set_entries(
        [{'name': 'Default', 'checked': True}, {'name': 'z', 'checked': False}],
        minimum_width=160,
    )
    two_row_height = popup.sizeHint().height()

    popup.set_entries(
        [
            {'name': 'Default', 'checked': True},
            {'name': 'z', 'checked': False},
            {'name': 'z copy', 'checked': False},
            {'name': 'z copy 2', 'checked': False},
        ],
        minimum_width=160,
    )
    four_row_height = popup.sizeHint().height()

    popup.set_entries(
        [
            {'name': 'Default', 'checked': True},
            {'name': 'z', 'checked': False},
            {'name': 'z copy', 'checked': False},
        ],
        minimum_width=160,
    )
    three_row_height = popup.sizeHint().height()

    assert four_row_height > three_row_height > two_row_height
    assert popup.scroll_area.height() == popup._natural_content_size.height()
    assert app is not None


def test_replacer_tree_item_sort_handles_mixed_numeric_and_text_keys():
    app = _qapp()
    tree = QTreeWidget()
    tree.setColumnCount(5)
    tree.setSortingEnabled(True)
    tree.sortItems(4, Qt.SortOrder.AscendingOrder)

    group_item = ReplacerTreeItem(['On', 'Group', 'Group', '0 IDs', '1 profile'])
    profile_item = ReplacerTreeItem(['On', 'Profile', 'Local', '1 ID', 'replacement.png'])
    group_item.setData(4, _ROLE_SORT_BASE, 1)
    profile_item.setData(4, _ROLE_SORT_BASE, 'replacement.png')

    tree.addTopLevelItem(group_item)
    tree.addTopLevelItem(profile_item)
    tree.sortItems(4, Qt.SortOrder.AscendingOrder)

    assert tree.topLevelItem(0) in (group_item, profile_item)
    tree.close()
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

        window.enabled_menu_btn.click()
        app.processEvents()

        assert 'z copy' in window.config_manager.config_names
        assert list(window.enabled_menu.item_widgets) == ['Default', 'z copy']
        assert not window.enabled_menu.item_widgets['z copy'].isChecked()
        assert window.enabled_menu_btn.text() == 'Default'
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_window_titles_do_not_embed_application_name():
    source_root = Path(__file__).resolve().parents[1] / 'src' / 'Fleasion'
    repeated_title_setters = []
    pattern = re.compile(r'setWindowTitle\([^\n]*(APP_NAME|Fleasion)')

    for path in source_root.rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                repeated_title_setters.append(f'{path.relative_to(source_root)}:{line_number}')

    assert repeated_title_setters == []


def test_group_rows_use_painted_folder_icon_not_unicode_text(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'New Group',
            'children': [
                {'name': 'Profile 1', 'enabled': True, 'replace_ids': []},
            ],
        }
    ]
    window = ReplacerConfigWindow(config_manager)
    try:
        group_item = window.tree.topLevelItem(0)

        assert group_item.text(1) == 'New Group'
        assert group_item.data(1, _ROLE_DRAW_GROUP_ICON) is True
        assert isinstance(window.tree.itemDelegateForColumn(1), _ProfileNameDelegate)
    finally:
        window.close()
    assert app is not None


def test_nested_group_rows_use_guide_step_icon_indent_without_text_padding(tmp_path, monkeypatch):
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'Group1',
            'children': [
                {
                    'type': 'group',
                    'name': 'Group2',
                    'children': [
                        {
                            'type': 'group',
                            'name': 'Group3',
                            'children': [],
                        },
                    ],
                },
            ],
        }
    ]
    window = ReplacerConfigWindow(config_manager)
    try:
        group1 = window.tree.topLevelItem(0)
        group2 = group1.child(0)
        group3 = group2.child(0)

        assert group1.text(1) == 'Group1'
        assert group2.text(1) == 'Group2'
        assert group3.text(1) == 'Group3'
        assert group1.data(1, _ROLE_DRAW_GROUP_ICON) is True
        assert group2.data(1, _ROLE_DRAW_GROUP_ICON) is True
        assert group3.data(1, _ROLE_DRAW_GROUP_ICON) is True
        assert group1.data(1, _ROLE_GROUP_ICON_INDENT) == 0
        assert group2.data(1, _ROLE_GROUP_ICON_INDENT) == _GROUP_GUIDE_STEP_PX
        assert group3.data(1, _ROLE_GROUP_ICON_INDENT) == _GROUP_GUIDE_STEP_PX * 2
    finally:
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
