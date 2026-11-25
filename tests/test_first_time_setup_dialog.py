import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, cast

from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTextBrowser, QWidget

from fleasion.app.dialogs import common as dialogs_common_module

if TYPE_CHECKING:
    from fleasion.app.tray import SystemTray
    from fleasion.config import ConfigManager


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
    return cast('QApplication', app) if app is not None else QApplication([])


def _new_dialog() -> _FirstTimeDialogLike:
    factory = cast(
        'Callable[[], _FirstTimeDialogLike]',
        dialogs_common_module.__dict__['FirstTimeSetupDialog'],
    )
    return factory()


def _body(dialog: object) -> QTextBrowser:
    return cast('QTextBrowser', dialog.__dict__['_body'])


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
        cast('QWidget', dialog), dialog.ok_button.rect().bottomRight()
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


def test_linux_first_client_choice_preselects_saved_preference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.app.dialogs import startup
    from fleasion.utils import platform_linux
    from fleasion.utils.linux_clients import ROSEMARY_CLIENT, SOBER_CLIENT, LinuxClientInstallation

    installed = tuple(
        LinuxClientInstallation(client, client.paths(home=tmp_path), Path('/usr/bin/flatpak'))
        for client in (SOBER_CLIENT, ROSEMARY_CLIENT)
    )
    choices: list[tuple[str, int]] = []

    def capture_choice(
        _parent: object, _title: str, _label: str, names: list[str],
        initial: int, *, editable: bool,
    ) -> tuple[str, bool]:
        assert not editable
        choices.append((names[initial], initial))
        return '', False

    monkeypatch.setattr(platform_linux, 'linux_client_installations', lambda: installed)
    monkeypatch.setattr(startup, 'visible_parent_widget', lambda: None)
    monkeypatch.setattr(startup, 'QInputDialog', SimpleNamespace(getItem=capture_choice))
    for preference in ('auto', 'rosemary'):
        config = SimpleNamespace(linux_client=preference)
        startup.prompt_first_linux_client_choice(
            cast('ConfigManager', config), cast('SystemTray', SimpleNamespace())
        )
    assert choices[0][1] == 0
    assert choices[1][1] == 2
