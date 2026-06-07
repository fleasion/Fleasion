import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, QRect, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from Fleasion.gui.replacer_config import _ScrollableConfigMenu


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