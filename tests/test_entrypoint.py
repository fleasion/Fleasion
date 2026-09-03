from __future__ import annotations

import sys
from types import ModuleType

import pytest

from fleasion import __main__ as entrypoint
from fleasion.app.cli import parse_application_args


def test_main_starts_application(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    app_module = ModuleType('fleasion.app')
    app_module.__dict__['run_application'] = lambda: calls.append('application')

    monkeypatch.setattr(entrypoint.sys, 'argv', ['fleasion'])
    monkeypatch.setattr(entrypoint.sys, 'platform', 'linux')
    monkeypatch.setitem(sys.modules, 'fleasion.app', app_module)

    entrypoint.main()

    assert calls == ['application']


def test_main_routes_linux_proxy_helper_without_importing_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    helper_module = ModuleType('fleasion.linux_proxy_helper_daemon')
    helper_module.__dict__['main'] = lambda: calls.append('helper')

    monkeypatch.setattr(
        entrypoint.sys,
        'argv',
        ['fleasion', '--linux-proxy-helper', '--backend-port', '8443'],
    )
    monkeypatch.setitem(sys.modules, 'fleasion.linux_proxy_helper_daemon', helper_module)
    monkeypatch.delitem(sys.modules, 'fleasion.app', raising=False)

    entrypoint.main()

    assert calls == ['helper']
    assert entrypoint.sys.argv == ['fleasion', '--backend-port', '8443']
    assert 'fleasion.app' not in sys.modules


def test_main_preloads_numpy_before_windows_application(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    app_module = ModuleType('fleasion.app')
    app_module.__dict__['run_application'] = lambda: calls.append('application')
    original_import_module = entrypoint.importlib.import_module

    def import_module(name: str) -> ModuleType:
        calls.append(name)
        if name == 'numpy':
            return ModuleType('numpy')
        return original_import_module(name)

    monkeypatch.setattr(entrypoint.sys, 'argv', ['fleasion'])
    monkeypatch.setattr(entrypoint.sys, 'platform', 'win32')
    monkeypatch.setattr(entrypoint.importlib, 'import_module', import_module)
    monkeypatch.setitem(sys.modules, 'fleasion.app', app_module)

    entrypoint.main()

    assert calls == ['numpy', 'application']


def test_application_arguments_ignore_qt_arguments() -> None:
    args = parse_application_args(['--no-dashboard', '--proxy-debug-mode', 'full', '-style', 'Fusion'])

    assert args.no_dashboard is True
    assert args.proxy_debug_mode == 'full'
