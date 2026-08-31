import json
import sys
import threading
from pathlib import Path

import pytest

from fleasion.utils import platform_macos


pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason='macOS-only platform tests')


def _make_player_app(path: Path) -> Path:
    resources = path / "Contents" / "Resources"
    macos = path / "Contents" / "MacOS"
    resources.mkdir(parents=True)
    macos.mkdir(parents=True)
    (macos / "RobloxPlayer").write_text("#!/bin/sh\n", encoding="utf-8")
    return resources


def test_terminate_roblox_requests_app_bundle_quit_before_signal(tmp_path, monkeypatch):
    app = tmp_path / "Roblox.app"
    app.mkdir()
    calls = []
    signals = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", (app,))
    monkeypatch.setattr(platform_macos, "ROBLOX_PROCESS", "RobloxPlayer")
    pid_snapshots = iter([[321], [], [], []])
    monkeypatch.setattr(platform_macos, "_process_pids", lambda _name: next(pid_snapshots, []))
    monkeypatch.setattr(platform_macos.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, "monotonic", lambda: next(ticks, 4.0))
    monkeypatch.setattr(
        platform_macos,
        "_signal_process",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(platform_macos, "_wait_for_pids_exit", lambda pids, _timeout: set())
    monkeypatch.setattr(platform_macos.subprocess, "run", fake_run)

    assert platform_macos.terminate_roblox() is True
    assert calls[0] == ["osascript", "-e", 'tell application "Roblox" to quit']
    assert signals == [(321, platform_macos.signal.SIGTERM)]


def test_terminate_roblox_escalates_only_captured_pids(monkeypatch):
    pid_snapshots = iter([[101, 202], [], [], []])
    signals = []
    waits = iter([{202}, set()])

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", ())
    monkeypatch.setattr(platform_macos, "_process_pids", lambda _name: next(pid_snapshots, []))
    monkeypatch.setattr(
        platform_macos,
        "_signal_process",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(platform_macos, "_wait_for_pids_exit", lambda _pids, _timeout: next(waits))
    monkeypatch.setattr(platform_macos.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.0, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, "monotonic", lambda: next(ticks, 4.0))

    assert platform_macos.terminate_roblox() is True
    assert signals == [
        (101, platform_macos.signal.SIGTERM),
        (202, platform_macos.signal.SIGTERM),
        (202, platform_macos.signal.SIGKILL),
    ]


def test_terminate_roblox_chases_background_replacement(monkeypatch):
    pid_snapshots = iter([[101], [303], [], []])
    signals = []
    waits = iter([set(), set()])
    logs = []

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", ())
    monkeypatch.setattr(platform_macos, "_process_pids", lambda _name: next(pid_snapshots, []))
    monkeypatch.setattr(
        platform_macos,
        "_signal_process",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(platform_macos, "_wait_for_pids_exit", lambda _pids, _timeout: next(waits))
    monkeypatch.setattr(platform_macos.time, "sleep", lambda _seconds: None)
    ticks = iter([0.0, 0.0, 0.0, 0.1, 4.0, 4.0])
    monkeypatch.setattr(platform_macos.time, "monotonic", lambda: next(ticks, 4.0))
    monkeypatch.setattr(
        platform_macos.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert platform_macos.terminate_roblox() is True
    assert signals == [
        (101, platform_macos.signal.SIGTERM),
        (303, platform_macos.signal.SIGTERM),
    ]
    assert any("replacement/background Player process(es)" in message for _, message in logs)


def test_terminate_roblox_reports_pid_that_survives_sigkill(monkeypatch):
    signals = []
    waits = iter([{404}, {404}])
    logs = []

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", ())
    monkeypatch.setattr(platform_macos, "_process_pids", lambda _name: [404])
    monkeypatch.setattr(
        platform_macos,
        "_signal_process",
        lambda pid, sig: signals.append((pid, sig)) or True,
    )
    monkeypatch.setattr(platform_macos, "_wait_for_pids_exit", lambda _pids, _timeout: next(waits))
    monkeypatch.setattr(
        platform_macos.log_buffer,
        "log",
        lambda category, message: logs.append((category, message)),
    )

    assert platform_macos.terminate_roblox() is False
    assert signals[-1] == (404, platform_macos.signal.SIGKILL)
    assert any("remained alive after SIGKILL: 404" in message for _, message in logs)


def test_discovers_froststrap_versions_and_appleblox_custom_path(tmp_path, monkeypatch):
    froststrap_versions = tmp_path / "Froststrap" / "Versions"
    froststrap_resources = _make_player_app(
        froststrap_versions / "version-abc123" / "RobloxPlayer.app"
    )
    custom_app = tmp_path / "Custom Roblox.app"
    custom_resources = _make_player_app(custom_app)
    config = tmp_path / "AppleBlox" / "config" / "roblox.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"installation": {"custom_path": "' + str(custom_app) + '"}}',
        encoding="utf-8",
    )

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", ())
    monkeypatch.setattr(platform_macos, "FROSTSTRAP_VERSIONS_DIR", froststrap_versions)
    monkeypatch.setattr(platform_macos, "APPLEBLOX_ROBLOX_CONFIG", config)
    monkeypatch.setattr(platform_macos, "_first_process_pid", lambda _name: None)

    assert platform_macos.find_roblox_resource_dirs(include_studio=False) == [
        custom_resources,
        froststrap_resources,
    ]
    assert platform_macos.resolve_roblox_player_exe_for_launch() == (
        custom_app / "Contents" / "MacOS" / "RobloxPlayer"
    )


def test_discovers_only_valid_appleblox_mod_restore_snapshot(tmp_path, monkeypatch):
    backup = tmp_path / "AppleBlox" / "cache" / "mods" / "Resources"
    backup.mkdir(parents=True)
    monkeypatch.setattr(platform_macos, "APPLEBLOX_MOD_BACKUP_RESOURCES", backup)

    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == []

    (backup / "content").mkdir()
    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == [backup]


def test_appleblox_mod_restore_snapshot_respects_data_dir_env(tmp_path, monkeypatch):
    data_dir = tmp_path / "AppleBlox Test Data"
    backup = data_dir / "cache" / "mods" / "Resources"
    (backup / "content").mkdir(parents=True)
    monkeypatch.setenv("APPLEBLOX_DATA_DIR", str(data_dir))

    assert platform_macos.appleblox_data_dir() == data_dir
    assert platform_macos.find_appleblox_mod_backup_resource_dirs() == [backup]


def test_appleblox_config_path_is_not_redirected_by_data_dir_env(tmp_path, monkeypatch):
    normal_config = tmp_path / "Application Support" / "AppleBlox" / "config" / "roblox.json"
    custom_app = tmp_path / "Custom Roblox.app"
    _make_player_app(custom_app)
    normal_config.parent.mkdir(parents=True)
    normal_config.write_text(
        '{"installation": {"custom_path": "' + str(custom_app) + '"}}',
        encoding="utf-8",
    )
    override = tmp_path / "Override"
    override_config = override / "config" / "roblox.json"
    override_config.parent.mkdir(parents=True)
    override_config.write_text('{"installation": {"custom_path": null}}', encoding="utf-8")

    monkeypatch.setenv("APPLEBLOX_DATA_DIR", str(override))
    monkeypatch.setattr(platform_macos, "APPLEBLOX_ROBLOX_CONFIG", normal_config)

    assert platform_macos._appleblox_custom_app_path() == custom_app


def test_appleblox_custom_app_path_ignores_non_object_config_shapes(tmp_path, monkeypatch):
    config = tmp_path / "roblox.json"
    monkeypatch.setattr(platform_macos, "APPLEBLOX_ROBLOX_CONFIG", config)

    for payload in ([], {"installation": []}, {"installation": "invalid"}):
        config.write_text(json.dumps(payload), encoding="utf-8")
        assert platform_macos._appleblox_custom_app_path() is None


def test_appleblox_custom_app_path_ignores_unexpandable_user_path(tmp_path, monkeypatch):
    config = tmp_path / "roblox.json"
    config.write_text(
        json.dumps({"installation": {"custom_path": "~missing-user/Roblox.app"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(platform_macos, "APPLEBLOX_ROBLOX_CONFIG", config)
    monkeypatch.setattr(
        Path,
        "expanduser",
        lambda _path: (_ for _ in ()).throw(RuntimeError("unknown user")),
    )

    assert platform_macos._appleblox_custom_app_path() is None


def test_appleblox_data_dir_ignores_relative_override(monkeypatch):
    monkeypatch.setenv("APPLEBLOX_DATA_DIR", "relative/appleblox")

    assert platform_macos.appleblox_data_dir() == platform_macos.APPLEBLOX_DATA_DIR


def test_discovers_only_valid_froststrap_mod_restore_snapshots(tmp_path, monkeypatch):
    root = tmp_path / "Froststrap" / "ModBackup"
    invalid = root / "version-invalid"
    valid = root / "version-valid"
    invalid.mkdir(parents=True)
    (valid / "ssl").mkdir(parents=True)
    (valid / "ssl" / "cacert.pem").write_text("certs", encoding="utf-8")
    monkeypatch.setattr(platform_macos, "FROSTSTRAP_MOD_BACKUP_DIR", root)

    assert platform_macos.find_froststrap_mod_backup_resource_dirs() == [valid]


def test_incremental_uri_parser_waits_for_complete_argument_block():
    parser = platform_macos._IncrementalRobloxLaunchUriParser()
    expected = (
        "roblox-player:1+launchmode:play+gameinfo:ticket+launchtime:123+"
        "placelauncherurl:https%3A%2F%2Fexample.test+browsertrackerid:1+"
        "robloxLocale:en_us+gameLocale:en_us+channel:test+LaunchExp:InApp"
    )

    assert parser.feed(
        b"[FLog::MacLuaApp] (AppDelegate) application:openURLs:(\n"
        b'    "roblox-player:1+launchmode:play+gameinfo:ticket\n'
        b"[FLog::MacLuaApp] (AppDelegate) Argument 1 = launchmode:play\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 2 = game"
    ) is None
    assert parser.feed(
        b"info:ticket\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 3 = launchtime:123\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 4 = "
        b"placelauncherurl:https%3A%2F%2Fexample.test\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 5 = browsertrackerid:1\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 6 = robloxLocale:en_us\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 7 = gameLocale:en_us\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 8 = channel:test\n"
        b"[FLog::MacLuaApp] (AppDelegate) Argument 9 = LaunchExp:InApp\n"
    ) is None
    assert parser.feed(
        b"[FLog::MacLuaApp] (AppDelegate) application:openURLs cold launch. "
        b"Defer web launch until after initialization is complete.\n"
    ) == expected


def test_uri_interceptor_kills_known_pid_before_handoff(tmp_path, monkeypatch):
    app = tmp_path / "Custom Roblox.app"
    _make_player_app(app)
    launch = platform_macos.MacOSRobloxPlayerLaunch(
        pid=123,
        executable_path=app / "Contents" / "MacOS" / "RobloxPlayer",
        app_path=app,
        log_path=tmp_path / "Player_last.log",
        detected_at=1.0,
    )
    calls = []
    handoffs = []
    logs = []
    interceptor = platform_macos.MacOSRobloxUriInterceptor(
        is_armed=lambda: True,
        on_intercepted=lambda received_launch, target: handoffs.append(
            (received_launch, target)
        ),
    )

    def fake_kill(pid, sig):
        calls.append((pid, sig))
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(platform_macos.os, "kill", fake_kill)
    monkeypatch.setattr(
        platform_macos.log_buffer,
        "log",
        lambda *args: logs.append(args),
    )
    monkeypatch.setattr(
        platform_macos,
        "_first_process_pid",
        lambda *_args: (_ for _ in ()).throw(AssertionError("no process scan in hot path")),
    )

    target = "roblox-player:1+launchmode:play+gameinfo:test"
    interceptor._intercept(launch, target)

    assert calls[0] == (123, platform_macos.signal.SIGKILL)
    assert handoffs == [(launch, target)]
    assert all(target not in " ".join(map(str, entry)) for entry in logs)


@pytest.mark.skipif(
    not hasattr(platform_macos.select, "kqueue"),
    reason="requires macOS kqueue",
)
def test_uri_interceptor_watches_new_log_and_reads_existing_bytes_once(tmp_path, monkeypatch):
    log_dir = tmp_path / "RobloxLogs"
    log_dir.mkdir()
    app = tmp_path / "Froststrap" / "Versions" / "current" / "RobloxPlayer.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    handoffs = []
    complete = threading.Event()

    monkeypatch.setattr(platform_macos, "_first_process_pid", lambda _name: 456)
    monkeypatch.setattr(platform_macos, "_process_command", lambda _pid: exe)

    def fake_kill(_pid, sig):
        if sig == 0:
            raise ProcessLookupError

    monkeypatch.setattr(platform_macos.os, "kill", fake_kill)
    interceptor = platform_macos.MacOSRobloxUriInterceptor(
        is_armed=lambda: True,
        on_intercepted=lambda launch, target: (handoffs.append((launch, target)), complete.set()),
        log_dir=log_dir,
    )
    assert interceptor.start()
    try:
        log_path = log_dir / "2026_Player_test_last.log"
        log_path.write_text(
            "[FLog::MacLuaApp] application:openURLs:(\n"
            "[FLog::MacLuaApp] Argument 1 = launchmode:play\n"
            "[FLog::MacLuaApp] Argument 2 = gameinfo:test\n"
            "[FLog::MacLuaApp] Argument 3 = launchtime:123\n"
            "[FLog::MacLuaApp] Argument 4 = placelauncherurl:https%3A%2F%2Fexample.test\n"
            "[FLog::MacLuaApp] Argument 5 = LaunchExp:InApp\n"
            "[FLog::MacLuaApp] application:openURLs cold launch. Defer web launch.\n",
            encoding="utf-8",
        )
        assert complete.wait(2.0)
    finally:
        interceptor.stop()

    assert handoffs[0][0].pid == 456
    assert handoffs[0][0].app_path == app
    assert handoffs[0][1].startswith("roblox-player:1+launchmode:play+gameinfo:")


def _reset_env_proxy_relaunch_state(monkeypatch):
    monkeypatch.setattr(platform_macos, "_env_proxy_relaunch_at", None)
    monkeypatch.setattr(platform_macos, "_env_proxy_relaunch_in_progress", False)


def test_relaunch_roblox_with_env_proxy_uses_detected_bundle_and_open_env(
    tmp_path, monkeypatch
):
    calls = []
    proxy_url = "http://127.0.0.1:58443"
    app = tmp_path / "Froststrap" / "Versions" / "version-current" / "RobloxPlayer.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(
        platform_macos,
        "_wait_for_local_proxy",
        lambda url: calls.append(("wait", url)) or True,
    )
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: True)
    monkeypatch.setattr(
        platform_macos,
        "terminate_roblox",
        lambda: calls.append("terminate") or True,
    )
    monkeypatch.setattr(
        platform_macos,
        "wait_for_roblox_exit",
        lambda timeout=10.0: calls.append("wait_for_exit") or True,
    )
    monkeypatch.setattr(
        platform_macos,
        "wait_for_roblox_window",
        lambda timeout=60.0: calls.append(("wait_for_start", timeout)) or True,
    )

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        platform_macos.subprocess,
        "run",
        lambda args, **kwargs: calls.append(("run", args, kwargs)) or Result(),
    )

    assert platform_macos.relaunch_roblox_with_proxy_env(
        proxy_url,
        prepare_launch=lambda path: calls.append(("prepare", path)) or True,
    )

    assert calls[:4] == [
        ("wait", proxy_url),
        "terminate",
        "wait_for_exit",
        ("prepare", exe),
    ]
    args = calls[4][1]
    assert args[0] == "open"
    assert f"HTTPS_PROXY={proxy_url}" in args
    assert f"HTTP_PROXY={proxy_url}" in args
    assert "FLEASION_PROXY_RELAUNCHED=1" in args
    assert args[-2:] == ["-a", str(app)]
    assert calls[5] == ("wait_for_start", 15.0)


def test_relaunch_roblox_with_env_proxy_preserves_launch_target(tmp_path, monkeypatch):
    calls = []
    app = tmp_path / "Roblox.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(platform_macos, "_wait_for_local_proxy", lambda *_args: True)
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: False)
    monkeypatch.setattr(platform_macos, "wait_for_roblox_window", lambda **_kwargs: True)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        platform_macos.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args) or Result(),
    )

    target = "roblox://experiences/start?placeId=121814103864070"
    assert platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443", target
    )
    assert calls[0][-1] == target


def test_intercepted_relaunch_uses_original_bundle_without_second_termination(
    tmp_path, monkeypatch
):
    calls = []
    app = tmp_path / "Froststrap" / "Versions" / "version-current" / "RobloxPlayer.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "_wait_for_local_proxy", lambda *_args: True)
    monkeypatch.setattr(
        platform_macos,
        "get_roblox_player_exe_path",
        lambda: (_ for _ in ()).throw(AssertionError("must preserve original executable")),
    )
    monkeypatch.setattr(
        platform_macos,
        "is_roblox_running",
        lambda: (_ for _ in ()).throw(AssertionError("original Player is already stopped")),
    )
    monkeypatch.setattr(platform_macos, "wait_for_roblox_window", lambda **_kwargs: True)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        platform_macos.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args) or Result(),
    )

    target = "roblox-player:1+launchmode:play+gameinfo:test"
    assert platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443",
        target,
        source_exe_path=exe,
        player_already_stopped=True,
    )
    assert calls[0][-3:] == ["-a", str(app), target]


def test_relaunch_roblox_with_env_proxy_retries_launchservices_600(
    tmp_path, monkeypatch
):
    calls = []
    app = tmp_path / "Roblox.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(platform_macos, "_wait_for_local_proxy", lambda *_args: True)
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: False)
    monkeypatch.setattr(platform_macos, "wait_for_roblox_window", lambda **_kwargs: True)
    monkeypatch.setattr(platform_macos.time, "sleep", lambda delay: calls.append(("sleep", delay)))

    class Result:
        def __init__(self, returncode, stderr=""):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = stderr

    results = iter(
        [
            Result(1, "_LSOpenURLsWithCompletionHandler() failed with error -600."),
            Result(0),
        ]
    )
    monkeypatch.setattr(
        platform_macos.subprocess,
        "run",
        lambda args, **kwargs: calls.append(("run", args)) or next(results),
    )

    assert platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443"
    )
    assert [call[0] for call in calls] == ["run", "sleep", "run"]
    assert calls[1] == ("sleep", 0.5)


def test_relaunch_roblox_with_env_proxy_does_not_repeat_recent_launch(
    tmp_path, monkeypatch
):
    calls = []
    app = tmp_path / "Roblox.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_macos,
        "_env_proxy_relaunch_at",
        platform_macos.time.monotonic(),
    )
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(
        platform_macos,
        "_wait_for_local_proxy",
        lambda *_args: calls.append("wait") or True,
    )
    monkeypatch.setattr(
        platform_macos,
        "wait_for_roblox_window",
        lambda *_args: calls.append("popen"),
    )

    assert not platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443"
    )
    assert calls == []


def test_explicit_roblox_uri_launch_can_replace_recent_env_proxy_launch(tmp_path, monkeypatch):
    calls = []
    app = tmp_path / "Roblox.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "_env_proxy_relaunch_at", platform_macos.time.monotonic())
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(platform_macos, "_wait_for_local_proxy", lambda *_args: True)
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: False)
    monkeypatch.setattr(platform_macos, "wait_for_roblox_window", lambda **_kwargs: True)

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        platform_macos.subprocess,
        "run",
        lambda args, **_kwargs: calls.append(args) or Result(),
    )

    target = "roblox://experiences/start?placeId=2"
    assert platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443", target
    )
    assert calls[0][-1] == target


def test_relaunch_roblox_with_env_proxy_does_not_kill_when_proxy_is_not_ready(
    tmp_path, monkeypatch
):
    calls = []
    app = tmp_path / "Roblox.app"
    _make_player_app(app)
    exe = app / "Contents" / "MacOS" / "RobloxPlayer"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(platform_macos, "get_roblox_player_exe_path", lambda: exe)
    monkeypatch.setattr(platform_macos, "_wait_for_local_proxy", lambda *_args: False)
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: True)
    monkeypatch.setattr(
        platform_macos,
        "terminate_roblox",
        lambda: calls.append("terminate") or True,
    )

    assert not platform_macos.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443"
    )
    assert calls == []


def test_appleblox_custom_path_ignores_embedded_null(tmp_path, monkeypatch):
    config = tmp_path / 'AppleBlox' / 'config' / 'roblox.json'
    config.parent.mkdir(parents=True)
    config.write_text(
        '{"installation": {"custom_path": "/tmp/Roblox\\u0000.app"}}',
        encoding='utf-8',
    )
    monkeypatch.setattr(platform_macos, 'APPLEBLOX_ROBLOX_CONFIG', config)

    assert platform_macos._appleblox_custom_app_path() is None
