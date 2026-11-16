"""Managed Rosemary update decisions, native handoff and retry safety."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.utils import macos_rosemary_launcher as launcher

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def enable_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', True)


def _installed_app(root: Path) -> Path:
    bundle = root / 'Roblox.app'
    executable = bundle / 'Contents/MacOS/RobloxPlayer'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'fixture executable')
    return bundle


def test_managed_process_observes_update_event_from_new_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils.macos_rosemary_update_monitor import PlayerLogProbe

    probe = PlayerLogProbe(tmp_path)
    player = tmp_path / 'test_Player_new_last.log'
    player.write_bytes(b'MacUpdateController::launchFinalizeUpdate\n')
    monkeypatch.setattr(launcher.macos_rosemary_update_monitor, 'managed_installer_running', lambda *_: False)
    session = SimpleNamespace(running=False)
    assert launcher._managed_updater_requested(session, tmp_path / 'rosemary', tmp_path, probe)


def test_managed_process_accepts_a_stable_no_update_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils.macos_rosemary_update_monitor import PlayerLogProbe

    probe = PlayerLogProbe(tmp_path)
    player = tmp_path / 'test_Player_new_last.log'
    player.write_bytes(b'Update check thread: updateRequired FALSE\n')
    clock = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(clock)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(launcher, '_MANAGED_SETTLE_SECONDS', 2)
    monkeypatch.setattr(launcher.macos_rosemary_update_monitor, 'managed_installer_running', lambda *_: False)
    session = SimpleNamespace(running=True)
    assert not launcher._managed_updater_requested(session, tmp_path / 'rosemary', tmp_path, probe)


def test_running_managed_client_leaves_background_installer_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils.macos_rosemary_update_monitor import PlayerLogProbe

    probe = PlayerLogProbe(tmp_path)
    (tmp_path / 'test_Player_new_last.log').write_bytes(
        b'Update check thread: updateRequired FALSE\n'
    )
    clock = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(clock)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(launcher, '_MANAGED_SETTLE_SECONDS', 2)
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'managed_installer_running', lambda *_: True
    )
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'terminate_managed_installers',
        lambda *_args: pytest.fail('normal background installer must not be terminated'),
    )
    assert not launcher._managed_updater_requested(
        SimpleNamespace(running=True), tmp_path / 'rosemary', tmp_path, probe
    )


def test_managed_exits_without_update_signal_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils.macos_rosemary_update_monitor import PlayerLogProbe

    clock = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(clock)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(launcher.macos_rosemary_update_monitor, 'managed_installer_running', lambda *_: False)
    session = SimpleNamespace(running=False)
    with pytest.raises(launcher.ManagedLaunchError, match='exited before'):
        launcher._managed_updater_requested(
            session, tmp_path / 'rosemary', tmp_path, PlayerLogProbe(tmp_path)
        )


def _managed_fixture(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, list[str], object, object]:
    app = _installed_app(tmp_path)
    runtime = tmp_path / 'Rosemary/bin/rosemary'
    events: list[str] = []
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    monkeypatch.setattr(launcher, '_ROBLOX_APP', app)
    monkeypatch.setattr(launcher.macos_rosemary_installer, 'install_pinned_runtime', lambda: runtime)
    monkeypatch.setattr(launcher, 'ensure_managed_prefix', lambda *_a, **_kw: events.append('prefix-ready'))
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'active_session', lambda: None)
    monkeypatch.setattr(launcher.macos_rosemary_update_monitor, 'PlayerLogProbe', lambda: object())

    class Session:
        running = True
        process = SimpleNamespace(pid=2146)
        channel = SimpleNamespace(ping=lambda: None)

    def spawn(command: list[str], *, env: dict[str, str]) -> Session:
        assert command == [str(runtime), 'run', '--native', str(app)]
        assert env['ROSEMARY_OBJC'] == 'builtin'
        assert env['FLEASION_MANAGED_ROSEMARY'] == '1'
        assert 'HTTPS_PROXY=http://127.0.0.1:58443' in env['ROSEMARY_ENVIRON']
        events.append('managed-launch')
        return Session()

    monkeypatch.setattr(launcher.macos_rosemary_memory, 'launch_with_private_memory', spawn)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'set_active_session', lambda _session: events.append('registered'))

    class Proxy:
        is_running = False

        def start(self) -> None:
            events.append('proxy-start')
            self.is_running = True

        def wait_for_env_proxy_ready(self, timeout: float) -> bool:
            assert timeout > 0
            events.append('proxy-ready')
            return True

        def roblox_env_proxy_url(self) -> str:
            return 'http://127.0.0.1:58443'

    config = SimpleNamespace(macos_rosemary_enabled=False, proxy_mode='hosts', proxy_features_enabled=False)
    return app, events, Proxy(), config


def test_no_update_launch_does_not_query_versions_or_open_native_player(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _app, events, proxy, config = _managed_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, '_check_managed_session', lambda *_args: False)
    monkeypatch.setattr(launcher, '_native_update_handoff', lambda *_a, **_kw: pytest.fail('unexpected native updater'))
    monkeypatch.setattr(
        launcher.subprocess, 'run',
        lambda command, **_kwargs: pytest.fail(f'unexpected external command: {command}'),
    )
    result = launcher.launch_managed(cast('object', config), cast('object', proxy))
    assert result.running
    assert events == ['prefix-ready', 'proxy-start', 'proxy-ready', 'managed-launch', 'registered']
    assert config.proxy_mode == 'env'
    assert config.proxy_features_enabled


def test_update_handoff_stops_owned_guest_uses_native_and_retries_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, events, proxy, config = _managed_fixture(monkeypatch, tmp_path)
    decisions = iter((True, False))
    monkeypatch.setattr(launcher, '_check_managed_session', lambda *_args: next(decisions))
    monkeypatch.setattr(launcher, '_stop_owned_updating_guest', lambda *_args: events.append('stop-guest'))
    monkeypatch.setattr(launcher, '_native_update_handoff', lambda path, **_kw: events.append('native-upgrade') if path == app else None)
    result = launcher.launch_managed(cast('object', config), cast('object', proxy))
    assert result.running
    assert events == [
        'prefix-ready', 'proxy-start', 'proxy-ready',
        'managed-launch', 'registered', 'stop-guest', 'native-upgrade',
        'managed-launch', 'registered',
    ]


def test_repeated_updater_aborts_without_second_native_handoff(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _app, events, proxy, config = _managed_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, '_check_managed_session', lambda *_args: True)
    monkeypatch.setattr(launcher, '_stop_owned_updating_guest', lambda *_args: events.append('stop-guest'))
    monkeypatch.setattr(launcher, '_native_update_handoff', lambda *_a, **_kw: events.append('native-upgrade'))
    with pytest.raises(launcher.ManagedLaunchError, match='still requests an update'):
        launcher.launch_managed(cast('object', config), cast('object', proxy))
    assert events.count('native-upgrade') == 1
    assert events.count('stop-guest') == 2


def test_native_fallback_waits_for_stable_current_client_without_version_comparison(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installed_app(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(launcher, '_launch_native_player', lambda _app: events.append('native-open'))
    monkeypatch.setattr(launcher, '_native_installer_running', lambda: False)
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(launcher.platform_macos, 'terminate_roblox', lambda: events.append('native-close') or True)
    monkeypatch.setattr(launcher.platform_macos, 'wait_for_roblox_exit', lambda _timeout: True)
    monkeypatch.setattr(launcher, '_STABLE_STARTUP_SECONDS', 3)
    tick = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(tick)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    current = SimpleNamespace(path=tmp_path / 'new_Player_last.log', current=True, updater_started=False, updating=False)
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'PlayerLogProbe',
        lambda: SimpleNamespace(poll=lambda: [current]),
    )
    launcher._native_update_handoff(app)
    assert events == ['native-close', 'native-open', 'native-close']


def test_native_installer_success_triggers_handoff_without_remote_version_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installed_app(tmp_path)
    events: list[str] = []
    monkeypatch.setattr(launcher, '_launch_native_player', lambda _app: events.append('native-open'))
    monkeypatch.setattr(launcher, '_native_installer_running', lambda: False)
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(launcher.platform_macos, 'terminate_roblox', lambda: events.append('native-close') or True)
    monkeypatch.setattr(launcher.platform_macos, 'wait_for_roblox_exit', lambda _timeout: True)
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'PlayerLogProbe',
        lambda: SimpleNamespace(poll=lambda: []),
    )
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'NativeInstallerLogProbe',
        lambda: SimpleNamespace(completed=lambda: True),
    )
    tick = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(tick)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    launcher._native_update_handoff(app)
    assert events == ['native-close', 'native-open', 'native-close']


def test_native_update_never_succeeds_without_confirmed_player_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installed_app(tmp_path)
    monkeypatch.setattr(launcher, '_launch_native_player', lambda _app: None)
    monkeypatch.setattr(launcher, '_native_installer_running', lambda: True)
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(launcher, '_UPDATE_TIMEOUT', 4)
    tick = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(tick)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(
        launcher.macos_rosemary_update_monitor, 'PlayerLogProbe',
        lambda: SimpleNamespace(poll=lambda: []),
    )
    with pytest.raises(launcher.ManagedLaunchError, match='did not finish'):
        launcher._native_update_handoff(app)


def test_proxy_not_ready_never_spawns_managed_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _app, _events, proxy, config = _managed_fixture(monkeypatch, tmp_path)
    proxy.wait_for_env_proxy_ready = lambda timeout: False  # type: ignore[method-assign]
    monkeypatch.setattr(
        launcher.macos_rosemary_memory, 'launch_with_private_memory',
        lambda *_a, **_kw: pytest.fail('must not launch without proxy'),
    )
    with pytest.raises(launcher.ManagedLaunchError, match='did not become ready'):
        launcher.launch_managed(cast('object', config), cast('object', proxy))


def test_managed_launch_disabled_before_download_or_process_changes(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    monkeypatch.setattr(launcher.macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', False)
    monkeypatch.setattr(
        launcher.macos_rosemary_installer, 'install_pinned_runtime',
        lambda: pytest.fail('disabled launch downloaded Rosemary'),
    )
    with pytest.raises(launcher.ManagedLaunchError, match='disabled until'):
        launcher.launch_managed(cast('object', object()), cast('object', object()))


def test_prefix_provisioning_is_verified_before_any_guest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(launcher, 'managed_prefix_directory', lambda: tmp_path / 'prefix')
    commands: list[list[str]] = []

    def record(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr=b'')

    monkeypatch.setattr(launcher.subprocess, 'run', record)
    launcher.ensure_managed_prefix(tmp_path / 'Rosemary/bin/rosemary')
    assert commands == [[str(tmp_path / 'Rosemary/bin/rosemary'), 'prefix', 'create', str(tmp_path / 'prefix')]]
