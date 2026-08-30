import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from collections.abc import Callable
from typing import Protocol, cast

from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTextBrowser, QWidget

from fleasion import app as app_module


class _FirstTimeDialogLike(Protocol):
    ok_button: QPushButton

    def setText(self, text: str) -> None: ...
    def show(self) -> None: ...
    def screen(self) -> QScreen: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def accept(self) -> None: ...
    def isVisible(self) -> bool: ...
    def allow_accept(self) -> None: ...
    def result(self) -> int: ...


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast(QApplication, app) if app is not None else QApplication([])


def _new_dialog() -> _FirstTimeDialogLike:
    factory = cast(
        'Callable[[], _FirstTimeDialogLike]',
        app_module.__dict__['_FirstTimeSetupDialog'],
    )
    return factory()


def _body(dialog: object) -> QTextBrowser:
    return cast(QTextBrowser, dialog.__dict__['_body'])


def test_first_time_setup_dialog_keeps_acknowledgement_on_screen() -> None:
    app = _qapp()
    dialog = _new_dialog()
    dialog.setText(('Welcome to Fleasion!\n\n' + 'Long setup instructions ' * 12 + '\n\n') * 40)
    dialog.ok_button.setText('OK (15s)')
    dialog.ok_button.setEnabled(False)

    dialog.show()
    app.processEvents()

    available = dialog.screen().availableGeometry()
    button_bottom = dialog.ok_button.mapTo(
        cast(QWidget, dialog), dialog.ok_button.rect().bottomRight()
    ).y()

    assert dialog.width() <= int(available.width() * 0.90)
    assert dialog.height() <= int(available.height() * 0.85)
    assert _body(dialog).verticalScrollBar().maximum() > 0
    assert dialog.ok_button.isVisible()
    assert button_bottom < dialog.height()

    dialog.accept()
    assert dialog.isVisible()

    dialog.allow_accept()
    dialog.ok_button.setEnabled(True)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
