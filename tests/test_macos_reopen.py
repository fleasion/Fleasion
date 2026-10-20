"""The dashboard comes back when Fleasion is reopened, and Escape keeps it open."""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

from fleasion import app as app_module
from fleasion.gui.replacer_config import ReplacerConfigWindow


_APP = None


def _qapp() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


class _Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def emit(self, value):
        for slot in self.slots:
            slot(value)


class _App:
    def __init__(self, widgets):
        self.applicationStateChanged = _Signal()
        self._widgets = widgets

    def topLevelWidgets(self):
        return self._widgets


class _Tray:
    _exiting = False

    def __init__(self):
        self.shown = 0

    def _show_replacer_config(self):
        self.shown += 1


def test_reopen_with_no_windows_shows_the_dashboard():
    _qapp()
    menu = QMenu()
    menu.show()  # an open tray menu is not a window
    app, tray = _App([menu]), _Tray()
    app_module._install_macos_reopen_handler(app, tray)

    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)

    assert tray.shown == 1
    menu.close()


def test_reopen_leaves_visible_windows_and_exits_alone():
    _qapp()
    window = QWidget()
    window.show()
    app, tray = _App([window]), _Tray()
    app_module._install_macos_reopen_handler(app, tray)

    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    window.hide()
    tray._exiting = True
    app.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)

    assert tray.shown == 0


def test_escape_does_not_dismiss_the_dashboard():
    _qapp()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

    # Escape is consumed before QDialog would turn it into reject()
    ReplacerConfigWindow.keyPressEvent(object(), event)

    assert event.isAccepted()
