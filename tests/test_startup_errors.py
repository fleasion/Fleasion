"""Startup errors caused by Windows application control get an explanation."""

from __future__ import annotations

import sys
import types

import pytest

from fleasion import startup_errors

SIP_BLOCK = (
    'DLL load failed while importing sip: An Application Control policy has blocked this file.'
)


def test_detects_the_import_error_python_raises_for_a_blocked_module():
    error = ImportError(SIP_BLOCK, name='PyQt6.sip')

    assert startup_errors.blocked_by_application_control(error)
    assert startup_errors.blocked_file(error) == 'PyQt6.sip'
    assert 'PyQt6.sip' in startup_errors.application_control_message(error)


def test_detects_the_windows_error_code_through_the_exception_chain():
    blocked = OSError(None, 'blocked', 'libportaudio64bit.dll')
    blocked.winerror = 4551
    try:
        try:
            raise blocked
        except OSError as exc:
            raise ImportError('could not import sounddevice') from exc
    except ImportError as exc:
        assert startup_errors.blocked_by_application_control(exc)
        assert startup_errors.blocked_file(exc) == 'libportaudio64bit.dll'


def test_unrelated_errors_are_not_reported():
    error = ImportError('No module named numpy', name='numpy')

    assert not startup_errors.blocked_by_application_control(error)
    assert not startup_errors.report_blocked_startup(error)


@pytest.mark.parametrize(('platform', 'shown'), [('win32', True), ('linux', False)])
def test_report_shows_one_message_box_on_windows_only(monkeypatch, platform, shown):
    calls = []
    windll = types.SimpleNamespace(
        user32=types.SimpleNamespace(MessageBoxW=lambda *args: calls.append(args))
    )
    monkeypatch.setattr(sys, 'platform', platform)
    monkeypatch.setattr('ctypes.windll', windll, raising=False)

    assert startup_errors.report_blocked_startup(ImportError(SIP_BLOCK)) is shown

    assert len(calls) == int(shown)
    if shown:
        _parent, text, title, _flags = calls[0]
        assert title == 'Windows blocked Fleasion'
        assert startup_errors.STATUS_URL in text
