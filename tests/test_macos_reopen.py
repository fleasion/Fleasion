"""The dashboard comes back when Fleasion is reopened, and Escape keeps it open."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication, QMenu, QWidget

from fleasion.app.macos_reopen import install_macos_reopen_handler
from fleasion.gui.replacer_config import ReplacerConfigWindow

if TYPE_CHECKING:
    from collections.abc import Iterable


def _qapp() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


class _App(QObject):
    applicationStateChanged = Signal(Qt.ApplicationState)  # ruff: ignore[invalid-function-name]

    def __init__(self, widgets: Iterable[QWidget]) -> None:
        super().__init__()
        self._widgets = list(widgets)

    def topLevelWidgets(self) -> list[QWidget]:  # ruff: ignore[invalid-function-name]
        return self._widgets


class _Tray:
    def __init__(self) -> None:
        self.shown = 0
        self.exiting = False

    def show_replacer_config(self) -> None:
        self.shown += 1


def test_reopen_with_no_windows_shows_the_dashboard() -> None:
    _qapp()
    menu = QMenu()
    menu.show()  # an open tray menu is not a window
    app, tray = _App([menu]), _Tray()
    install_macos_reopen_handler(cast('QApplication', app), tray)

    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)

    assert tray.shown == 1
    menu.close()


def test_reopen_leaves_visible_windows_and_exits_alone() -> None:
    _qapp()
    window = QWidget()
    window.show()
    app, tray = _App([window]), _Tray()
    install_macos_reopen_handler(cast('QApplication', app), tray)

    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    window.hide()
    tray.exiting = True
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)

    assert tray.shown == 0


def test_escape_does_not_dismiss_the_dashboard() -> None:
    _qapp()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

    # Escape is consumed before QDialog would turn it into reject()
    ReplacerConfigWindow.keyPressEvent(cast('ReplacerConfigWindow', object()), event)

    assert event.isAccepted()
