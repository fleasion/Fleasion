from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion import app as app_module
from fleasion import qml_runtime as runtime_module
from fleasion.qml_runtime import QmlRuntime


class _SignalStub:
    def __init__(self) -> None:
        self.values: list[tuple[object, ...]] = []

    def emit(self, *values: object) -> None:
        self.values.append(values)


def _restart_args(**overrides: object) -> argparse.Namespace:
    values = {
        'restart_handoff_token': None,
        'restart_handoff_parent_pid': None,
        'kill_others': False,
        'preserve_env_proxy_player': False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_restart_child_enters_prepared_release_gate_before_single_instance_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(runtime_module.sys, 'platform', 'linux')
    monkeypatch.setattr(
        app_module,
        '_join_restart_handoff',
        lambda token, parent_pid: calls.append((token, parent_pid)) or True,
    )

    assert runtime_module._enter_restart_handoff(
        _restart_args(restart_handoff_token='token', restart_handoff_parent_pid=123)
    )
    assert calls == [('token', 123)]


def test_restart_child_rejects_incomplete_or_kill_others_handoff() -> None:
    assert not runtime_module._enter_restart_handoff(_restart_args(restart_handoff_token='token'))
    assert not runtime_module._enter_restart_handoff(
        _restart_args(
            restart_handoff_token='token',
            restart_handoff_parent_pid=123,
            kill_others=True,
        )
    )


def test_restart_child_publishes_ready_only_after_configured_proxy_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[str] = []
    published: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._args = _restart_args(
        restart_handoff_token='token',
        restart_handoff_parent_pid=123,
    )
    runtime._control_server = SimpleNamespace(isListening=lambda: True)
    runtime._config = SimpleNamespace(proxy_features_enabled=True, proxy_mode='hosts')
    runtime._proxy = SimpleNamespace(
        wait_for_hosts_proxy_ready=lambda **_kwargs: waits.append('hosts') or True,
        wait_for_env_proxy_ready=lambda **_kwargs: waits.append('env') or True,
    )
    monkeypatch.setattr(app_module, '_restart_abort_requested', lambda *_args: False)
    monkeypatch.setattr(
        app_module,
        '_publish_restart_handoff',
        lambda token: published.append(token) or True,
    )

    assert runtime.complete_restart_handoff()
    assert waits == ['hosts']
    assert published == ['token']


def test_restart_child_does_not_publish_ready_when_proxy_never_becomes_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._args = _restart_args(
        restart_handoff_token='token',
        restart_handoff_parent_pid=123,
    )
    runtime._control_server = SimpleNamespace(isListening=lambda: True)
    runtime._config = SimpleNamespace(proxy_features_enabled=True, proxy_mode='env')
    runtime._proxy = SimpleNamespace(wait_for_env_proxy_ready=lambda **_kwargs: False)
    monkeypatch.setattr(app_module, '_restart_abort_requested', lambda *_args: False)
    monkeypatch.setattr(
        app_module,
        '_publish_restart_handoff',
        lambda token: published.append(token) or True,
    )

    assert not runtime.complete_restart_handoff()
    assert published == []


def test_control_quit_preserve_marks_player_before_quitting() -> None:
    quit_calls: list[str] = []
    disconnected: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._preserve_player_on_shutdown = False
    runtime._app = SimpleNamespace(quit=lambda: quit_calls.append('quit'))
    connection = SimpleNamespace(
        readAll=lambda: SimpleNamespace(toStdString=lambda: 'quit-preserve-env-player'),
        disconnectFromServer=lambda: disconnected.append('disconnect'),
    )

    runtime._read_control_command(connection)

    assert runtime._preserve_player_on_shutdown is True
    assert quit_calls == ['quit']
    assert disconnected == ['disconnect']


def _mode_runtime(*, restart_result: bool | None) -> tuple[QmlRuntime, list[str], _SignalStub]:
    events: list[str] = []
    errors = _SignalStub()
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._config = SimpleNamespace(
        proxy_mode='hosts',
        proxy_features_enabled=True,
        run_on_boot=False,
    )
    runtime._proxy = SimpleNamespace(
        can_live_switch_to_hosts=lambda: False,
        restart_for_mode_switch=lambda: events.append('restart-proxy'),
    )
    runtime._restart_for_proxy_mode_switch = lambda: restart_result
    runtime._sync_autostart_proxy_mode = lambda mode: events.append(f'autostart:{mode}') or True
    runtime._context = SimpleNamespace(
        errorOccurred=errors,
        _settings=SimpleNamespace(
            proxyModeChanged=_SignalStub(),
            valuesChanged=_SignalStub(),
        ),
    )
    runtime._app = SimpleNamespace(quit=lambda: events.append('quit'))
    runtime._force_close_player_on_shutdown = False
    return runtime, events, errors


def test_env_to_hosts_confirmed_restart_failure_rolls_back_mode_and_autostart() -> None:
    runtime, events, errors = _mode_runtime(restart_result=False)

    runtime._transition_proxy_mode('env', 'hosts')

    assert runtime._config.proxy_mode == 'env'
    assert events == ['autostart:hosts', 'autostart:env']
    assert errors.values
    assert 'restored' in str(errors.values[-1][0]).casefold()


def test_env_to_hosts_uncertain_restart_keeps_hosts_selected() -> None:
    runtime, events, errors = _mode_runtime(restart_result=None)

    runtime._transition_proxy_mode('env', 'hosts')

    assert runtime._config.proxy_mode == 'hosts'
    assert events == ['autostart:hosts']
    assert errors.values
    assert 'could not confirm' in str(errors.values[-1][0]).casefold()


def test_env_to_hosts_verified_restart_closes_parent_and_player() -> None:
    runtime, events, _errors = _mode_runtime(restart_result=True)

    runtime._transition_proxy_mode('env', 'hosts')

    assert runtime._config.proxy_mode == 'hosts'
    assert runtime._force_close_player_on_shutdown is True
    assert events == ['autostart:hosts', 'quit']


def test_linux_client_switch_keeps_main_transaction_order(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    linux_client_changed = _SignalStub()
    values_changed = _SignalStub()
    setting_changed = _SignalStub()
    proxy = SimpleNamespace(is_running=True)
    proxy.stop = lambda: events.append('stop')
    proxy.start = lambda: events.append('start')
    modifications = SimpleNamespace(
        restore_all=lambda: events.append('restore'),
        refresh_roblox_dirs=lambda **_kwargs: events.append('refresh'),
    )
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._proxy = proxy
    runtime._modifications = modifications
    runtime._config = SimpleNamespace(linux_client='sober')
    runtime._context = SimpleNamespace(
        errorOccurred=_SignalStub(),
        _settings=SimpleNamespace(
            linuxClientChanged=linux_client_changed,
            valuesChanged=values_changed,
            changed=setting_changed,
        ),
    )
    monkeypatch.setattr(runtime_module.sys, 'platform', 'linux')
    from fleasion.utils import platform_linux

    monkeypatch.setattr(
        platform_linux,
        'set_linux_client_preference',
        lambda value: events.append(f'preference:{value}'),
    )

    runtime._transition_linux_client('sober', 'future-client')

    assert runtime._config.linux_client == 'future-client'
    assert events == ['stop', 'restore', 'preference:future-client', 'refresh', 'start']
    assert setting_changed.values == [('linux_client',)]


def test_qml_runtime_keeps_main_opengl_startup_policy() -> None:
    source = Path(runtime_module.__file__).read_text(encoding='utf-8')

    assert 'setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)' not in source
    assert "os.environ.setdefault('QT_OPENGL', 'desktop')" in source
    assert 'install_qt_message_logging()' in source


def test_qml_runtime_main_passes_claimed_single_instance_lock_to_runtime() -> None:
    source = Path(runtime_module.__file__).read_text(encoding='utf-8')

    assert 'shared_memory=shared_memory' in source
    assert 'if not _enter_restart_handoff(args):' in source
    assert 'if not runtime.complete_restart_handoff():' in source


def test_runtime_delivers_auth_warning_to_qml_once() -> None:
    dashboard = _SignalStub()
    warnings = _SignalStub()
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._auth_warning_shown = False
    runtime._shutting_down = False
    runtime._context = SimpleNamespace(
        dashboardVisibilityRequested=dashboard,
        authWarningRequested=warnings,
    )
    payload = {
        'title': 'Token warning',
        'message': 'No token',
        'detail': 'Diagnostics',
        'can_open_login': True,
        'continue_text': 'Continue',
        'login_text': 'Login',
        'exit_text': 'Exit',
    }

    runtime._deliver_auth_warning(payload)
    runtime._deliver_auth_warning(payload)

    assert dashboard.values == [(True,)]
    assert warnings.values == [
        ('Token warning', 'No token', 'Diagnostics', True, 'Continue', 'Login', 'Exit')
    ]


def test_first_run_language_setting_retranslates_qml_engine() -> None:
    from fleasion import localization

    events: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._config = SimpleNamespace(first_time_setup_complete=False, language='es')
    runtime._engine = SimpleNamespace(retranslate=lambda: events.append('retranslate'))
    runtime._modifications = SimpleNamespace(set_read_only_lock_enabled=lambda _enabled: None)
    runtime._manual_upstream_credentials_timer = SimpleNamespace(
        start=lambda: None, stop=lambda: None
    )
    previous = localization.get_language()
    try:
        runtime._on_setting_changed('language')
        assert events == ['retranslate']
    finally:
        localization.set_language(previous)


def test_commit_data_request_runs_qml_runtime_cleanup_contract() -> None:
    calls: list[str] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime.shutdown = lambda: calls.append('shutdown')  # type: ignore[method-assign]

    runtime._on_commit_data_request(object())

    assert calls == ['shutdown']


def test_windows_permission_denials_are_forwarded_to_startup_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reports: list[tuple[str, dict[str, object]]] = []
    runtime = QmlRuntime.__new__(QmlRuntime)
    runtime._shutting_down = False
    runtime._modifications = SimpleNamespace(
        take_permission_denied_dirs=lambda: [Path(r'C:\Roblox\Versions\version-123')]
    )
    runtime._context = SimpleNamespace(
        _startup_repair=SimpleNamespace(
            report_error=lambda code, details: reports.append((code, details)) or True
        )
    )
    monkeypatch.setattr(runtime_module.sys, 'platform', 'win32')

    runtime._poll_modification_permission_failures()

    assert reports == [
        (
            'roblox_permission_denied',
            {'paths': [str(Path(r'C:\Roblox\Versions\version-123'))]},
        )
    ]


def test_live_traffic_panel_keeps_privacy_warning_at_sensitive_surface() -> None:
    panel = (
        Path(runtime_module.__file__).with_name('qml') / 'screens' / 'proxy' / 'TrafficPanel.qml'
    ).read_text(encoding='utf-8')

    assert 'trafficPrivacyWarning' in panel
    assert 'Theme.warningSubtle' in panel
