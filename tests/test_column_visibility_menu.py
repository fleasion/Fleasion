import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from typing import cast

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from fleasion.cache.cache_viewer import SCRAPER_COLUMNS, ColumnVisibilityMenu


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def _release(menu: ColumnVisibilityMenu, button: Qt.MouseButton, pos: QPoint) -> None:
    event = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(pos),
        QPointF(menu.mapToGlobal(pos)),
        button,
        button,
        Qt.KeyboardModifier.NoModifier,
    )
    menu.mouseReleaseEvent(event)


def test_column_visibility_menu_ignores_right_button_release() -> None:
    app = _qapp()
    visibility = {key: default for key, _label, default, _width in SCRAPER_COLUMNS}
    menu = ColumnVisibilityMenu(visibility)
    menu.adjustSize()

    action = menu.actions()[0]
    pos = menu.actionGeometry(action).center()
    _release(menu, Qt.MouseButton.RightButton, pos)

    assert action.isChecked()
    assert app is not None


def test_column_visibility_menu_toggles_on_left_button_release() -> None:
    app = _qapp()
    visibility = {key: default for key, _label, default, _width in SCRAPER_COLUMNS}
    menu = ColumnVisibilityMenu(visibility)
    menu.adjustSize()

    action = menu.actions()[0]
    pos = menu.actionGeometry(action).center()
    _release(menu, Qt.MouseButton.LeftButton, pos)

    assert not action.isChecked()
    assert app is not None
