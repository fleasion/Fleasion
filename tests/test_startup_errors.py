"""Startup errors caused by Windows application control get an explanation."""

from __future__ import annotations

import sys
import types

import pytest

from fleasion import startup_errors

SHIBOKEN_BLOCK = (
    'DLL load failed while importing Shiboken: '
    'An Application Control policy has blocked this file.'
)


class _WindowsError(OSError):
    """An ``OSError`` carrying the ``winerror`` Windows adds to load failures."""

    def __init__(self, winerror: int, filename: str) -> None:
        super().__init__(None, 'blocked', filename)
        self.winerror = winerror


def test_detects_the_import_error_python_raises_for_a_blocked_module() -> None:
    error = ImportError(SHIBOKEN_BLOCK, name='shiboken6.Shiboken')

    assert startup_errors.blocked_by_application_control(error)
    assert startup_errors.blocked_file(error) == 'shiboken6.Shiboken'
    assert 'shiboken6.Shiboken' in startup_errors.application_control_message(error)


def test_detects_the_windows_error_code_through_the_exception_chain() -> None:
    blocked = _WindowsError(4551, 'libportaudio64bit.dll')
    try:
        try:
            raise blocked
        except OSError as exc:
            msg = 'could not import sounddevice'
            raise ImportError(msg) from exc
    except ImportError as exc:
        assert startup_errors.blocked_by_application_control(exc)
        assert startup_errors.blocked_file(exc) == 'libportaudio64bit.dll'


def test_unrelated_errors_are_not_reported() -> None:
    error = ImportError('No module named numpy', name='numpy')

    assert not startup_errors.blocked_by_application_control(error)
    assert not startup_errors.report_blocked_startup(error)


@pytest.mark.parametrize(('platform', 'shown'), [('win32', True), ('linux', False)])
def test_report_shows_one_message_box_on_windows_only(
    monkeypatch: pytest.MonkeyPatch, platform: str, *, shown: bool
) -> None:
    calls: list[tuple[object, str, str, int]] = []

    def message_box(parent: object, text: str, title: str, flags: int) -> int:
        calls.append((parent, text, title, flags))
        return 1

    windll = types.SimpleNamespace(user32=types.SimpleNamespace(MessageBoxW=message_box))
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr('ctypes.windll', windll, raising=False)

    assert startup_errors.report_blocked_startup(ImportError(SHIBOKEN_BLOCK)) is shown

    assert len(calls) == int(shown)
    if shown:
        _parent, text, title, _flags = calls[0]
        assert title == 'Windows blocked Fleasion'
        assert startup_errors.STATUS_URL in text
