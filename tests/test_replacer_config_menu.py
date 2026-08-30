import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QPushButton,
    QScrollArea,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidgetItem,
    QWidget,
)

from fleasion.config import manager as manager_module
from fleasion.gui import replacer_config as replacer_config_module
from fleasion.gui.replacer_config import ReplacerConfigWindow


class _MenuRowLike(Protocol):
    def isChecked(self) -> bool: ...
    def click(self) -> None: ...


_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    app = QApplication.instance()
    _app = cast(QApplication, app) if app is not None else QApplication([])
    return _app


def _new_replacer_tree_item() -> QTreeWidgetItem:
    factory = cast('Callable[[], QTreeWidgetItem]', replacer_config_module.__dict__['ReplacerTreeItem'])
    return factory()


def _new_profile_name_delegate() -> QStyledItemDelegate:
    factory = cast(
        'Callable[[], QStyledItemDelegate]',
        replacer_config_module.__dict__['_ProfileNameDelegate'],
    )
    return factory()


def _draw_group_icon_role() -> int:
    return cast(int, replacer_config_module.__dict__['_ROLE_DRAW_GROUP_ICON'])


def _new_scrollable_menu(*, checkable: bool = False) -> QMenu:
    factory = cast(
        'Callable[..., QMenu]',
        replacer_config_module.__dict__['_ScrollableConfigMenu'],
    )
    return factory(checkable=checkable)


def _menu_set_entries(menu: QMenu, entries: list[dict[str, object]], minimum_width: int) -> None:
    callback = cast('Callable[..., None]', getattr(menu, 'set_entries'))
    callback(entries, minimum_width=minimum_width)


def _menu_natural_size(menu: QMenu) -> QSize:
    return cast(QSize, menu.__dict__['_natural_content_size'])


def _menu_scroll_area(menu: QMenu) -> QScrollArea:
    return cast(QScrollArea, menu.__dict__['scroll_area'])


def _menu_constrain(menu: QMenu, geometry: QRect, *, anchor_y: int) -> None:
    callback = cast('Callable[..., None]', getattr(menu, 'constrain_to_available_geometry'))
    callback(geometry, anchor_y=anchor_y)


def _menu_connect_toggled(menu: QMenu, callback: Callable[[str, bool], None]) -> None:
    signal = menu.__dict__['item_toggled']
    cast('object', signal)
    signal.connect(callback)


def _menu_row(menu: QMenu, name: str) -> QWidget:
    widgets = cast('dict[str, QWidget]', menu.__dict__['item_widgets'])
    return widgets[name]


def _row_like(row: QWidget) -> _MenuRowLike:
    return cast(_MenuRowLike, row)


def _guard_opening_release(menu: QMenu) -> None:
    callback = cast('Callable[[], None]', getattr(menu, '_guard_opening_mouse_release'))
    callback()


def _window(config_manager: object, *args: object) -> ReplacerConfigWindow:
    factory = cast('Callable[..., ReplacerConfigWindow]', ReplacerConfigWindow)
    return factory(config_manager, *args)


def _rebuild_enabled_menu(window: ReplacerConfigWindow) -> None:
    callback = cast(
        'Callable[[], None]',
        getattr(window, '_rebuild_enabled_menu'),
    )
    callback()


def _parse_ids(window: ReplacerConfigWindow, text: str) -> list[int | str]:
    callback = cast(
        'Callable[[str], list[int | str]]',
        getattr(window, '_parse_ids'),
    )
    return callback(text)


def _add_rule(window: ReplacerConfigWindow) -> None:
    callback = cast('Callable[[], None]', getattr(window, '_add_rule'))
    callback()


def _enabled_row_checked(window: ReplacerConfigWindow, name: str) -> bool:
    row = cast(_MenuRowLike, window.enabled_menu.item_widgets[name])
    return row.isChecked()


def _noop() -> None:
    return None


def test_replacer_tree_item_compares_while_unattached() -> None:
    left = _new_replacer_tree_item()
    right = _new_replacer_tree_item()
    left.setText(0, 'alpha')
    right.setText(0, 'beta')

    assert left.treeWidget() is None
    assert right.treeWidget() is None
    assert left < right


def test_profile_name_delegate_paints_without_option_widget() -> None:
    app = _qapp()
    model = QStandardItemModel()
    item = QStandardItem('Group')
    model.appendRow(item)
    index = model.index(0, 0)
    model.setData(index, True, _draw_group_icon_role())

    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 160, 30)
    assert option.widget is None

    image = QImage(200, 60, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    try:
        _new_profile_name_delegate().paint(painter, option, index)
    finally:
        painter.end()

    assert not image.isNull()
    assert app is not None


def test_scrollable_config_menu_constrains_height_and_scrolls() -> None:
    app = _qapp()
    popup = _new_scrollable_menu(checkable=True)
    _menu_set_entries(
        popup,
        [{'name': f'Config {i:02d}', 'checked': False} for i in range(40)],
        minimum_width=120,
    )
    natural_height = _menu_natural_size(popup).height()

    _menu_constrain(popup, QRect(0, 0, 500, 240), anchor_y=20)

    assert natural_height > _menu_scroll_area(popup).height()
    assert _menu_scroll_area(popup).height() <= 240
    assert (
        _menu_scroll_area(popup).verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert popup.sizeHint().width() == _menu_scroll_area(popup).width()
    assert popup.actionGeometry(popup.actions()[0]).width() == _menu_scroll_area(popup).width()
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_scrollable_config_menu_ignores_opening_release() -> None:
    app = _qapp()
    popup = _new_scrollable_menu(checkable=True)
    _menu_set_entries(popup, [{'name': 'Default', 'checked': False}], minimum_width=120)
    toggles: list[tuple[str, bool]] = []

    def record_toggle(name: str, checked: bool) -> None:
        toggles.append((name, checked))

    _menu_connect_toggled(popup, record_toggle)
    row = _menu_row(popup, 'Default')

    _guard_opening_release(popup)
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(8, 8),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(row, event)

    assert not _row_like(row).isChecked()
    assert toggles == []

    _row_like(row).click()

    assert _row_like(row).isChecked()
    assert toggles == [('Default', True)]
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_scrollable_config_menu_toggles_from_full_row_width() -> None:
    app = _qapp()
    popup = _new_scrollable_menu(checkable=True)
    _menu_set_entries(popup, [{'name': 'a', 'checked': False}], minimum_width=260)
    toggles: list[tuple[str, bool]] = []

    def record_toggle(name: str, checked: bool) -> None:
        toggles.append((name, checked))

    _menu_connect_toggled(popup, record_toggle)
    row = _menu_row(popup, 'a')
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

    assert _row_like(row).isChecked()
    assert toggles == [('a', True)]
    popup.close()
    popup.deleteLater()
    app.processEvents()
    assert app is not None


def test_open_configs_uses_config_manager_active_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    opened: list[Path] = []

    def record_open(path: Path) -> None:
        opened.append(path)

    monkeypatch.setattr(replacer_config_module, 'open_folder', record_open)
    config_manager = manager_module.ConfigManager()
    window = _window(config_manager)
    try:
        open_buttons = [
            button for button in window.findChildren(QPushButton) if button.text() == 'Open Configs'
        ]
        assert len(open_buttons) == 1
        open_buttons[0].click()
        assert opened == [configs_dir]
    finally:
        window.close()
    assert app is not None


def test_enabled_menu_button_press_loads_new_config_file_from_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    config_manager.set_config_enabled('Default', True)
    window = _window(config_manager)
    try:
        assert list(window.enabled_menu.item_widgets) == ['Default']

        (configs_dir / 'z copy.json').write_text(
            json.dumps({'replacement_rules': []}),
            encoding='utf-8',
        )

        _rebuild_enabled_menu(window)
        app.processEvents()

        assert 'z copy' in window.config_manager.config_names
        assert list(window.enabled_menu.item_widgets) == ['Default', 'z copy']
        assert not _enabled_row_checked(window, 'z copy')
        assert window.enabled_menu_btn.text() == 'Default'
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_enabled_menu_uses_one_enabled_config_snapshot_per_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    is_enabled_calls: list[str] = []
    original_is_config_enabled = config_manager.is_config_enabled

    def record_is_config_enabled(name: str) -> bool:
        is_enabled_calls.append(name)
        return original_is_config_enabled(name)

    monkeypatch.setattr(config_manager, 'is_config_enabled', record_is_config_enabled)
    window = _window(config_manager)
    try:
        is_enabled_calls.clear()
        monkeypatch.setattr(window, '_update_editing_button_style', _noop)
        _rebuild_enabled_menu(window)

        assert not is_enabled_calls
        assert _enabled_row_checked(window, 'Config 000')
        assert not _enabled_row_checked(window, 'Config 001')
    finally:
        window.enabled_menu.hide()
        window.close()
    assert app is not None


def test_config_menu_buttons_do_not_rebuild_during_mouse_press(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    class SpyReplacerConfigWindow(ReplacerConfigWindow):
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.editing_rebuilds = 0
            self.enabled_rebuilds = 0
            initializer = cast('Callable[..., None]', super().__init__)
            initializer(*args, **kwargs)

        def _rebuild_editing_menu(self, *args: object, **kwargs: object) -> None:
            self.editing_rebuilds += 1
            super()._rebuild_editing_menu(*args, **kwargs)

        def _rebuild_enabled_menu(self, *args: object, **kwargs: object) -> None:
            self.enabled_rebuilds += 1
            super()._rebuild_enabled_menu(*args, **kwargs)

    config_manager = manager_module.ConfigManager()
    spy_factory = cast('Callable[..., SpyReplacerConfigWindow]', SpyReplacerConfigWindow)
    window = spy_factory(config_manager)
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


def test_replace_ids_parser_splits_multiline_pastes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    window = _window(config_manager)
    try:
        assert _parse_ids(window, '101\n202\t303;404,505 606') == [101, 202, 303, 404, 505, 606]

        window.name_entry.setText('Multiline IDs')
        window.replace_entry.setText('101\n202\n303')
        window.replacement_entry.clear()
        _add_rule(window)

        first_rule = cast('dict[str, object]', config_manager.replacement_rules[0])
        assert first_rule['replace_ids'] == [101, 202, 303]
        top_item = window.tree.topLevelItem(0)
        assert top_item is not None
        assert top_item.text(3) == '3 IDs'
    finally:
        window.close()
    assert app is not None


def test_replacer_explains_portable_configs_asset_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    config_dir = tmp_path / 'FleasionNT'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)

    config_manager = manager_module.ConfigManager()
    window = _window(config_manager)
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
