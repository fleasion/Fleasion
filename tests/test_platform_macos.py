import json
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Never, Protocol, cast

import pytest

from fleasion.utils import platform_macos

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='macOS-only platform tests')


class _LaunchUriParserLike(Protocol):
    def feed(self, data: bytes) -> str | None: ...


def _appleblox_custom_app_path() -> Path | None:
    callback = cast(
        'Callable[[], Path | None]',
        platform_macos.__dict__['_appleblox_custom_app_path'],
    )
    return callback()


def _launch_uri_parser() -> _LaunchUriParserLike:
    parser_type = cast('type[object]', platform_macos.__dict__['_IncrementalRobloxLaunchUriParser'])
    return cast('_LaunchUriParserLike', parser_type())


def _intercept(
    interceptor: platform_macos.MacOSRobloxUriInterceptor,
    launch: platform_macos.MacOSRobloxPlayerLaunch,
    target: str,
) -> None:
    callback = cast(
        'Callable[[platform_macos.MacOSRobloxUriInterceptor, platform_macos.MacOSRobloxPlayerLaunch, str], None]',
        platform_macos.MacOSRobloxUriInterceptor.__dict__['_intercept'],
    )
    callback(interceptor, launch, target)


def _first_pid_none(_name: str) -> None:
    return None


def _first_pid_456(_name: str) -> int:
    return 456


def _process_command_for(exe: Path) -> Callable[[int], Path]:
    def process_command(_pid: int) -> Path:
        return exe

    return process_command


def _always_true() -> bool:
    return True


def _always_false() -> bool:
    return False


def _always_true_url(_url: str, timeout: float = 10.0) -> bool:
    del timeout
    return True


def _always_false_url(_url: str, timeout: float = 10.0) -> bool:
    del timeout
    return False


def _wait_window_true(timeout: float = 60.0) -> bool:
    del timeout
    return True


def _raise_unknown_user(_path: Path) -> Never:
    msg = 'unknown user'
    raise RuntimeError(msg)


def _no_process_scan(_name: str) -> Never:
    msg = 'no process scan in hot path'
    raise AssertionError(msg)


def _preserve_exe_failure() -> Never:
    msg = 'must preserve original executable'
    raise AssertionError(msg)


def _running_failure() -> Never:
    msg = 'original Player is already stopped'
    raise AssertionError(msg)


def _no_sleep(_seconds: float) -> None:
    return None


def _pid_snapshot_callback(snapshots: Iterator[list[int]]) -> Callable[[str], list[int]]:
    def callback(_name: str) -> list[int]:
        return next(snapshots, list[int]())

    return callback


def _signal_callback(
    signals: list[tuple[int, object]],
) -> Callable[[int, object], bool]:
    def callback(pid: int, sig: object) -> bool:
        signals.append((pid, sig))
        return True

    return callback


def _identity_callback(identities: dict[int, str | None]) -> Callable[[int], str | None]:
    def callback(pid: int) -> str | None:
        return identities.get(pid)

    return callback


def _wait_callback(
    waits: Iterator[set[int]],
) -> Callable[[set[int], float], set[int]]:
    def callback(_pids: set[int], _timeout: float) -> set[int]:
        return next(waits)

    return callback


def _wait_all_exit(_pids: set[int], _timeout: float) -> set[int]:
    return set[int]()


def _log_callback(logs: list[tuple[str, str]]) -> Callable[[str, str], None]:
    def callback(category: str, message: str) -> None:
        logs.append((category, message))

    return callback


def _make_player_app(path: Path) -> Path:
    resources = path / 'Contents' / 'Resources'
    macos = path / 'Contents' / 'MacOS'
    resources.mkdir(parents=True)
    macos.mkdir(parents=True)
    (macos / 'RobloxPlayer').write_text('#!/bin/sh\n', encoding='utf-8')
    return resources


def test_terminate_roblox_requests_app_bundle_quit_before_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / 'Roblox.app'
    app.mkdir()
    calls: list[list[str]] = []
    signals: list[tuple[int, object]] = []

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', (app,))
    monkeypatch.setattr(platform_macos, 'ROBLOX_PROCESS', 'RobloxPlayer')
    pid_snapshots = iter([[321], [], [], []])
    monkeypatch.setattr(platform_macos, '_process_pids', _pid_snapshot_callback(pid_snapshots))
    monkeypatch.setattr(platform_macos.time, 'sleep', _no_sleep)
    ticks = iter([0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, 'monotonic', lambda: next(ticks, 4.0))
    monkeypatch.setattr(platform_macos, '_process_identity', _identity_callback({321: 'start-a'}))
    monkeypatch.setattr(platform_macos, '_signal_process', _signal_callback(signals))
    monkeypatch.setattr(platform_macos, '_wait_for_pids_exit', _wait_all_exit)
    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    assert platform_macos.terminate_roblox() is True
    assert calls[0] == ['osascript', '-e', 'tell application "Roblox" to quit']
    assert signals == [(321, platform_macos.signal.SIGTERM)]


def test_terminate_roblox_escalates_only_captured_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_snapshots = iter([[101, 202], [], [], []])
    signals: list[tuple[int, object]] = []
    waits = iter([{202}, set[int]()])

    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', ())
    monkeypatch.setattr(platform_macos, '_process_pids', _pid_snapshot_callback(pid_snapshots))
    monkeypatch.setattr(
        platform_macos,
        '_process_identity',
        _identity_callback({101: 'start-a', 202: 'start-b'}),
    )
    monkeypatch.setattr(platform_macos, '_signal_process', _signal_callback(signals))
    monkeypatch.setattr(platform_macos, '_wait_for_pids_exit', _wait_callback(waits))
    monkeypatch.setattr(platform_macos.time, 'sleep', _no_sleep)
    ticks = iter([0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, 'monotonic', lambda: next(ticks, 4.0))

    assert platform_macos.terminate_roblox() is True
    assert signals == [
        (101, platform_macos.signal.SIGTERM),
        (202, platform_macos.signal.SIGTERM),
        (202, platform_macos.signal.SIGKILL),
    ]


def test_terminate_roblox_chases_background_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid_snapshots = iter([[101], [303], [], []])
    signals: list[tuple[int, object]] = []
    waits = iter([set[int](), set[int]()])
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', ())
    monkeypatch.setattr(platform_macos, '_process_pids', _pid_snapshot_callback(pid_snapshots))
    monkeypatch.setattr(
        platform_macos,
        '_process_identity',
        _identity_callback({101: 'start-a', 303: 'start-c'}),
    )
    monkeypatch.setattr(platform_macos, '_signal_process', _signal_callback(signals))
    monkeypatch.setattr(platform_macos, '_wait_for_pids_exit', _wait_callback(waits))
    monkeypatch.setattr(platform_macos.time, 'sleep', _no_sleep)
    ticks = iter([0.0, 0.0, 0.0, 0.1, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, 'monotonic', lambda: next(ticks, 4.0))
    monkeypatch.setattr(platform_macos.log_buffer, 'log', _log_callback(logs))

    assert platform_macos.terminate_roblox() is True
    assert signals == [
        (101, platform_macos.signal.SIGTERM),
        (303, platform_macos.signal.SIGTERM),
    ]
    assert any('replacement/background Player process(es)' in message for _, message in logs)


def test_terminate_roblox_skips_sigkill_when_pid_identity_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, object]] = []
    logs: list[tuple[str, str]] = []
    identity_reads = iter(['Mon Aug 31 17:00:00 2026 /Applications/Roblox.app/RobloxPlayer',
                           'Mon Aug 31 17:00:02 2026 /usr/bin/unrelated'])

    def changing_identity(_pid: int) -> str | None:
        return next(identity_reads, 'Mon Aug 31 17:00:02 2026 /usr/bin/unrelated')

    pid_snapshots = iter([[505], [], [], []])
    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', ())
    monkeypatch.setattr(platform_macos, '_process_pids', _pid_snapshot_callback(pid_snapshots))
    monkeypatch.setattr(platform_macos, '_process_identity', changing_identity)
    def still_present(_pids: set[int], _timeout: float) -> set[int]:
        return {505}

    monkeypatch.setattr(platform_macos, '_signal_process', _signal_callback(signals))
    monkeypatch.setattr(platform_macos, '_wait_for_pids_exit', still_present)
    monkeypatch.setattr(platform_macos.time, 'sleep', _no_sleep)
    ticks = iter([0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, 'monotonic', lambda: next(ticks, 4.0))
    monkeypatch.setattr(platform_macos.log_buffer, 'log', _log_callback(logs))

    assert platform_macos.terminate_roblox() is True
    assert signals == [(505, platform_macos.signal.SIGTERM)]
    assert any('numeric PID was reused by a different process' in message for _, message in logs)


def test_terminate_roblox_reports_pid_that_survives_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals: list[tuple[int, object]] = []
    waits = iter([{404}, {404}])
    logs: list[tuple[str, str]] = []

    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', ())
    monkeypatch.setattr(platform_macos, '_process_pids', _pid_snapshot_callback(iter([[404]])))
    monkeypatch.setattr(platform_macos, '_process_identity', _identity_callback({404: 'start-d'}))
    monkeypatch.setattr(platform_macos, '_signal_process', _signal_callback(signals))
    monkeypatch.setattr(platform_macos, '_wait_for_pids_exit', _wait_callback(waits))
    monkeypatch.setattr(platform_macos.log_buffer, 'log', _log_callback(logs))

    assert platform_macos.terminate_roblox() is False
    assert signals[-1] == (404, platform_macos.signal.SIGKILL)
    assert any('remained alive after SIGKILL: 404' in message for _, message in logs)


def test_discovers_froststrap_versions_and_appleblox_custom_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    froststrap_versions = tmp_path / 'Froststrap' / 'Versions'
    froststrap_resources = _make_player_app(
        froststrap_versions / 'version-abc123' / 'RobloxPlayer.app'
    )
    custom_app = tmp_path / 'Custom Roblox.app'
    custom_resources = _make_player_app(custom_app)
    config = tmp_path / 'AppleBlox' / 'config' / 'roblox.json'
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"installation": {"custom_path": "' + str(custom_app) + '"}}',
        encoding='utf-8',
    )

    monkeypatch.setattr(platform_macos, 'ROBLOX_APP_CANDIDATES', ())
    monkeypatch.setattr(platform_macos, 'FROSTSTRAP_VERSIONS_DIR', froststrap_versions)
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', config)
    monkeypatch.setattr(platform_macos, '_first_process_pid', _first_pid_none)

    assert platform_macos.find_roblox_resource_dirs(include_studio=False) == [
        custom_resources,
        froststrap_resources,
    ]
    assert platform_macos.resolve_roblox_player_exe_for_launch() == (
        custom_app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    )


def test_discovers_only_valid_appleblox_mod_restore_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backup = tmp_path / 'AppleBlox' / 'cache' / 'mods' / 'Resources'
    backup.mkdir(parents=True)
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_MOD_BACKUP_RESOURCES', backup)

    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == []

    (backup / 'content').mkdir()
    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == [backup]


def test_appleblox_mod_restore_snapshot_respects_data_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / 'AppleBlox Test Data'
    backup = data_dir / 'cache' / 'mods' / 'Resources'
    (backup / 'content').mkdir(parents=True)
    monkeypatch.setenv('APPLEBLOX_DATA_DIR', str(data_dir))

    assert platform_macos.appleblox_data_dir() == data_dir
    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == [backup]


def test_appleblox_config_path_is_not_redirected_by_data_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    normal_config = tmp_path / 'Application Support' / 'AppleBlox' / 'config' / 'roblox.json'
    custom_app = tmp_path / 'Custom Roblox.app'
    _make_player_app(custom_app)
    normal_config.parent.mkdir(parents=True)
    normal_config.write_text(
        '{"installation": {"custom_path": "' + str(custom_app) + '"}}',
        encoding='utf-8',
    )
    override = tmp_path / 'Override'
    override_config = override / 'config' / 'roblox.json'
    override_config.parent.mkdir(parents=True)
    override_config.write_text('{"installation": {"custom_path": null}}', encoding='utf-8')

    monkeypatch.setenv('APPLEBLOX_DATA_DIR', str(override))
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', normal_config)

    assert _appleblox_custom_app_path() == custom_app


def test_appleblox_custom_app_path_ignores_non_object_config_shapes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / 'roblox.json'
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', config)

    payloads: tuple[object, ...] = (
        list[object](),
        {'installation': list[object]()},
        {'installation': 'invalid'},
    )
    for payload in payloads:
        config.write_text(json.dumps(payload), encoding='utf-8')
        assert _appleblox_custom_app_path() is None


def test_appleblox_custom_app_path_ignores_unexpandable_user_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / 'roblox.json'
    config.write_text(
        json.dumps({'installation': {'custom_path': '~missing-user/Roblox.app'}}),
        encoding='utf-8',
    )
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', config)
    monkeypatch.setattr(
        Path,
        'expanduser',
        _raise_unknown_user,
    )

    assert _appleblox_custom_app_path() is None


def test_appleblox_data_dir_ignores_relative_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('APPLEBLOX_DATA_DIR', 'relative/appleblox')

    assert platform_macos.appleblox_data_dir() == platform_macos.APPLEBLOX_DATA_DIR


def test_discovers_only_valid_froststrap_mod_restore_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'Froststrap' / 'ModBackup'
    invalid = root / 'version-invalid'
    valid = root / 'version-valid'
    invalid.mkdir(parents=True)
    (valid / 'ssl').mkdir(parents=True)
    (valid / 'ssl' / 'cacert.pem').write_text('certs', encoding='utf-8')
    monkeypatch.setattr(platform_macos, 'FROSTSTRAP_MOD_BACKUP_DIR', root)

    assert platform_macos.find_froststrap_mod_backup_resource_dirs() == [valid]


def test_incremental_uri_parser_waits_for_complete_argument_block() -> None:
    parser = _launch_uri_parser()
    expected = (
        'roblox-player:1+launchmode:play+gameinfo:ticket+launchtime:123+'
        'placelauncherurl:https%3A%2F%2Fexample.test+browsertrackerid:1+'
        'robloxLocale:en_us+gameLocale:en_us+channel:test+LaunchExp:InApp'
    )

    assert (
        parser.feed(
            b'[FLog::MacLuaApp] (AppDelegate) application:openURLs:(\n'
            b'    "roblox-player:1+launchmode:play+gameinfo:ticket\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 1 = launchmode:play\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 2 = game'
        )
        is None
    )
    assert (
        parser.feed(
            b'info:ticket\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 3 = launchtime:123\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 4 = '
            b'placelauncherurl:https%3A%2F%2Fexample.test\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 5 = browsertrackerid:1\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 6 = robloxLocale:en_us\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 7 = gameLocale:en_us\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 8 = channel:test\n'
            b'[FLog::MacLuaApp] (AppDelegate) Argument 9 = LaunchExp:InApp\n'
        )
        is None
    )
    assert (
        parser.feed(
            b'[FLog::MacLuaApp] (AppDelegate) application:openURLs cold launch. '
            b'Defer web launch until after initialization is complete.\n'
        )
        == expected
    )


def test_uri_interceptor_kills_known_pid_before_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = tmp_path / 'Custom Roblox.app'
    _make_player_app(app)
    launch = platform_macos.MacOSRobloxPlayerLaunch(
        pid=123,
        executable_path=app / 'Contents' / 'MacOS' / 'RobloxPlayer',
        app_path=app,
        log_path=tmp_path / 'Player_last.log',
        detected_at=1.0,
    )
    calls: list[tuple[int, int]] = []
    handoffs: list[tuple[platform_macos.MacOSRobloxPlayerLaunch, str]] = []
    logs: list[tuple[object, ...]] = []
    interceptor = platform_macos.MacOSRobloxUriInterceptor(
        is_armed=_always_true,
        on_intercepted=lambda received_launch, target: handoffs.append((received_launch, target)),
    )

    def fake_kill(pid: int, sig: int) -> None:
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(platform_macos.os, 'kill', fake_kill)

    def record_log(*args: object) -> None:
        logs.append(args)

    monkeypatch.setattr(platform_macos.log_buffer, 'log', record_log)
    monkeypatch.setattr(
        platform_macos,
        '_first_process_pid',
        _no_process_scan,
    )

    target = 'roblox-player:1+launchmode:play+gameinfo:test'
    _intercept(interceptor, launch, target)

    assert calls[0] == (123, platform_macos.signal.SIGKILL)
    assert handoffs == [(launch, target)]
    assert all(target not in ' '.join(map(str, entry)) for entry in logs)


@pytest.mark.skipif(
    not hasattr(platform_macos.select, 'kqueue'),
    reason='requires macOS kqueue',
)
def test_uri_interceptor_watches_new_log_and_reads_existing_bytes_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = tmp_path / 'RobloxLogs'
    log_dir.mkdir()
    app = tmp_path / 'Froststrap' / 'Versions' / 'current' / 'RobloxPlayer.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    handoffs: list[tuple[platform_macos.MacOSRobloxPlayerLaunch, str]] = []
    complete = threading.Event()

    monkeypatch.setattr(platform_macos, '_first_process_pid', _first_pid_456)
    monkeypatch.setattr(platform_macos, '_process_command', _process_command_for(exe))

    def fake_kill(_pid: int, sig: int) -> None:
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(platform_macos.os, 'kill', fake_kill)

    def on_intercepted(launch: platform_macos.MacOSRobloxPlayerLaunch, target: str) -> None:
        handoffs.append((launch, target))
        complete.set()

    interceptor = platform_macos.MacOSRobloxUriInterceptor(
        is_armed=_always_true,
        on_intercepted=on_intercepted,
        log_dir=log_dir,
    )
    assert interceptor.start()
    try:
        log_path = log_dir / '2026_Player_test_last.log'
        log_path.write_text(
            '[FLog::MacLuaApp] application:openURLs:(\n'
            '[FLog::MacLuaApp] Argument 1 = launchmode:play\n'
            '[FLog::MacLuaApp] Argument 2 = gameinfo:test\n'
            '[FLog::MacLuaApp] Argument 3 = launchtime:123\n'
            '[FLog::MacLuaApp] Argument 4 = placelauncherurl:https%3A%2F%2Fexample.test\n'
            '[FLog::MacLuaApp] Argument 5 = LaunchExp:InApp\n'
            '[FLog::MacLuaApp] application:openURLs cold launch. Defer web launch.\n',
            encoding='utf-8',
        )
        assert complete.wait(2.0)
    finally:
        interceptor.stop()

    assert handoffs[0][0].pid == 456
    assert handoffs[0][0].app_path == app
    assert handoffs[0][1].startswith('roblox-player:1+launchmode:play+gameinfo:')


def _reset_env_proxy_relaunch_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_macos, '_env_proxy_relaunch_at', None)
    monkeypatch.setattr(platform_macos, '_env_proxy_relaunch_in_progress', False)


def test_relaunch_roblox_with_env_proxy_uses_detected_bundle_and_open_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[object] = []
    proxy_url = 'http://127.0.0.1:58443'
    app = tmp_path / 'Froststrap' / 'Versions' / 'version-current' / 'RobloxPlayer.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)

    def wait_proxy(url: str, timeout: float = 10.0) -> bool:
        del timeout
        calls.append(('wait', url))
        return True

    def terminate() -> bool:
        calls.append('terminate')
        return True

    def wait_exit(timeout: float = 10.0) -> bool:
        del timeout
        calls.append('wait_for_exit')
        return True

    def wait_window(timeout: float = 60.0) -> bool:
        calls.append(('wait_for_start', timeout))
        return True

    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', wait_proxy)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _always_true)
    monkeypatch.setattr(platform_macos, 'terminate_roblox', terminate)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_exit', wait_exit)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', wait_window)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args: list[str], **kwargs: object) -> Result:
        calls.append(('run', args, kwargs))
        return Result()

    def prepare_launch(path: Path) -> bool:
        calls.append(('prepare', path))
        return True

    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    assert platform_macos.relaunch_roblox_with_proxy_env(
        proxy_url,
        prepare_launch=prepare_launch,
    )

    assert calls[:4] == [
        ('wait', proxy_url),
        'terminate',
        'wait_for_exit',
        ('prepare', exe),
    ]
    run_call = cast('tuple[str, list[str], dict[str, object]]', calls[4])
    args = run_call[1]
    assert args[0] == 'open'
    assert f'HTTPS_PROXY={proxy_url}' in args
    assert f'HTTP_PROXY={proxy_url}' in args
    assert 'FLEASION_PROXY_RELAUNCHED=1' in args
    assert args[-2:] == ['-a', str(app)]
    assert calls[5] == ('wait_for_start', 15.0)


def test_relaunch_roblox_with_env_proxy_preserves_launch_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    app = tmp_path / 'Roblox.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)
    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', _always_true_url)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _always_false)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', _wait_window_true)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    target = 'roblox://experiences/start?placeId=121814103864070'
    assert platform_macos.relaunch_roblox_with_proxy_env('http://127.0.0.1:58443', target)
    assert calls[0][-1] == target


def test_intercepted_relaunch_uses_original_bundle_without_second_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    app = tmp_path / 'Froststrap' / 'Versions' / 'version-current' / 'RobloxPlayer.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', _always_true_url)
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', _preserve_exe_failure)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _running_failure)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', _wait_window_true)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    target = 'roblox-player:1+launchmode:play+gameinfo:test'
    assert platform_macos.relaunch_roblox_with_proxy_env(
        'http://127.0.0.1:58443',
        target,
        source_exe_path=exe,
        player_already_stopped=True,
    )
    assert calls[0][-3:] == ['-a', str(app), target]


def test_relaunch_roblox_with_env_proxy_retries_launchservices_600(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, object]] = []
    app = tmp_path / 'Roblox.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)
    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', _always_true_url)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _always_false)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', _wait_window_true)

    def sleep(delay: float) -> None:
        calls.append(('sleep', delay))

    monkeypatch.setattr(platform_macos.time, 'sleep', sleep)

    class Result:
        def __init__(self, returncode: int, stderr: str = '') -> None:
            self.returncode = returncode
            self.stdout = ''
            self.stderr = stderr

    results = iter(
        [
            Result(1, '_LSOpenURLsWithCompletionHandler() failed with error -600.'),
            Result(0),
        ]
    )

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        calls.append(('run', args))
        return next(results)

    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    assert platform_macos.relaunch_roblox_with_proxy_env('http://127.0.0.1:58443')
    assert [call[0] for call in calls] == ['run', 'sleep', 'run']
    assert calls[1] == ('sleep', 0.5)


def test_relaunch_roblox_with_env_proxy_does_not_repeat_recent_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    app = tmp_path / 'Roblox.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_macos,
        '_env_proxy_relaunch_at',
        platform_macos.time.monotonic(),
    )
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)

    def wait_proxy(_url: str, timeout: float = 10.0) -> bool:
        del timeout
        calls.append('wait')
        return True

    def wait_window(timeout: float = 60.0) -> bool:
        del timeout
        calls.append('popen')
        return True

    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', wait_proxy)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', wait_window)

    assert not platform_macos.relaunch_roblox_with_proxy_env('http://127.0.0.1:58443')
    assert calls == []


def test_explicit_roblox_uri_launch_can_replace_recent_env_proxy_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    app = tmp_path / 'Roblox.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, '_env_proxy_relaunch_at', platform_macos.time.monotonic())
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)
    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', _always_true_url)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _always_false)
    monkeypatch.setattr(platform_macos, 'wait_for_roblox_window', _wait_window_true)

    class Result:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(args: list[str], **_kwargs: object) -> Result:
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos.subprocess, 'run', fake_run)

    target = 'roblox://experiences/start?placeId=2'
    assert platform_macos.relaunch_roblox_with_proxy_env('http://127.0.0.1:58443', target)
    assert calls[0][-1] == target


def test_relaunch_roblox_with_env_proxy_does_not_kill_when_proxy_is_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    app = tmp_path / 'Roblox.app'
    _make_player_app(app)
    exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, 'get_roblox_player_exe_path', lambda: exe)
    monkeypatch.setattr(platform_macos, '_wait_for_local_proxy', _always_false_url)
    monkeypatch.setattr(platform_macos, 'is_roblox_running', _always_true)

    def terminate() -> bool:
        calls.append('terminate')
        return True

    monkeypatch.setattr(platform_macos, 'terminate_roblox', terminate)

    assert not platform_macos.relaunch_roblox_with_proxy_env('http://127.0.0.1:58443')
    assert calls == []


def test_appleblox_custom_path_ignores_embedded_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / 'AppleBlox' / 'config' / 'roblox.json'
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"installation": {"custom_path": "/tmp/Roblox\\u0000.app"}}',
        encoding='utf-8',
    )
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', config)

    assert _appleblox_custom_app_path() is None
