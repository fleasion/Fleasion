"""Termination signals exit the Qt loop so shutdown cleanup still runs."""

from __future__ import annotations

import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import signal
from typing import TYPE_CHECKING, cast

import pytest
from PySide6.QtWidgets import QApplication

from fleasion.app import qt_runtime

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import FrameType

_app: QApplication | None = None


def _qapp() -> QApplication:
    global _app
    instance = QApplication.instance()
    _app = cast('QApplication', instance) if instance is not None else QApplication([])
    return _app


@pytest.mark.parametrize('name', ['SIGINT', 'SIGTERM', 'SIGHUP'])
def test_signal_exits_the_event_loop(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(signal, name):
        pytest.skip(f'{name} does not exist on this platform')
    signum = getattr(signal, name)
    handlers: dict[int, Callable[[int, FrameType | None], object]] = {}
    exit_codes: list[int] = []

    def register(sig: int, handler: Callable[[int, FrameType | None], object]) -> None:
        handlers[sig] = handler

    monkeypatch.setattr(qt_runtime.signal, 'signal', register)
    app = _qapp()
    monkeypatch.setattr(app, 'exit', exit_codes.append)

    timer = qt_runtime.install_gui_sigint_handler(app)
    try:
        handlers[signum](signum, None)
    finally:
        timer.stop()

    assert exit_codes == [128 + signum]
