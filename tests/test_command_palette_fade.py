"""The command palette only fades where the platform supports window opacity."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QAbstractAnimation, QtMsgType, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QWidget

from fleasion.gui import command_palette

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PySide6.QtCore import QMessageLogContext


@pytest.fixture
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def palette(app: QApplication) -> Iterator[command_palette.CommandPalette]:
    parent = QWidget()
    parent.resize(800, 600)
    widget = command_palette.CommandPalette(parent, list)
    yield widget
    widget.close()
    parent.deleteLater()
    app.processEvents()


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    messages: list[str] = []

    def record(kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        if kind == QtMsgType.QtWarningMsg:
            messages.append(message)

    previous = qInstallMessageHandler(record)
    yield messages
    qInstallMessageHandler(previous)


def _platform(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setattr(
        command_palette, 'QApplication', SimpleNamespace(platformName=lambda: name), raising=False
    )


def test_palette_appears_without_opacity_warnings_on_wayland(
    monkeypatch: pytest.MonkeyPatch,
    app: QApplication,
    palette: command_palette.CommandPalette,
    warnings: list[str],
) -> None:
    _platform(monkeypatch, 'wayland')

    palette.popup()
    app.processEvents()

    assert palette.isVisible()
    assert palette._fade is None  # pyright: ignore[reportPrivateUsage]
    assert not [message for message in warnings if 'window opacity' in message]


def test_palette_fades_in_where_window_opacity_works(
    monkeypatch: pytest.MonkeyPatch,
    app: QApplication,
    palette: command_palette.CommandPalette,
    warnings: list[str],
) -> None:
    _platform(monkeypatch, 'windows')

    palette.popup()

    fade = palette._fade  # pyright: ignore[reportPrivateUsage]
    assert fade is not None
    assert fade.state() == QAbstractAnimation.State.Running
    fade.stop()
