import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import QApplication

from Fleasion.cache.cache_viewer import ColumnVisibilityMenu, SCRAPER_COLUMNS


def _qapp():
    return QApplication.instance() or QApplication([])


def _release(menu: ColumnVisibilityMenu, button: Qt.MouseButton, pos):
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(pos),
        QPointF(menu.mapToGlobal(pos)),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    menu.mouseReleaseEvent(event)


def test_column_visibility_menu_ignores_right_button_release():
    app = _qapp()
    visibility = {key: default for key, _label, default, _width in SCRAPER_COLUMNS}
    menu = ColumnVisibilityMenu(visibility)
    menu.adjustSize()

    action = menu.actions()[0]
    pos = menu.actionGeometry(action).center()
    _release(menu, Qt.MouseButton.RightButton, pos)

    assert action.isChecked()
    assert menu._col_visibility['hash_name']
    assert app is not None


def test_column_visibility_menu_toggles_on_left_button_release():
    app = _qapp()
    visibility = {key: default for key, _label, default, _width in SCRAPER_COLUMNS}
    menu = ColumnVisibilityMenu(visibility)
    menu.adjustSize()

    action = menu.actions()[0]
    pos = menu.actionGeometry(action).center()
    _release(menu, Qt.MouseButton.LeftButton, pos)

    assert not action.isChecked()
    assert not menu._col_visibility['hash_name']
    assert app is not None
