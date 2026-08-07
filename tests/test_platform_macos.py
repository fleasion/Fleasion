import sys
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
    states = iter([True, False])

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append(args)
        return Result()

    monkeypatch.setattr(platform_macos, "ROBLOX_APP_CANDIDATES", (app,))
    monkeypatch.setattr(platform_macos, "ROBLOX_PROCESS", "RobloxPlayer")
    monkeypatch.setattr(platform_macos, "is_roblox_running", lambda: next(states))
    monkeypatch.setattr(platform_macos.subprocess, "run", fake_run)

    assert platform_macos.terminate_roblox() is True
    assert calls[0] == ["osascript", "-e", 'tell application "Roblox" to quit']
    assert calls[1] == ["pkill", "-TERM", "-x", "RobloxPlayer"]


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


def test_discovers_only_valid_froststrap_mod_restore_snapshots(tmp_path, monkeypatch):
    root = tmp_path / "Froststrap" / "ModBackup"
    invalid = root / "version-invalid"
    valid = root / "version-valid"
    invalid.mkdir(parents=True)
    (valid / "ssl").mkdir(parents=True)
    (valid / "ssl" / "cacert.pem").write_text("certs", encoding="utf-8")
    monkeypatch.setattr(platform_macos, "FROSTSTRAP_MOD_BACKUP_DIR", root)

    assert platform_macos.find_froststrap_mod_backup_resource_dirs() == [valid]


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

    assert platform_macos.relaunch_roblox_with_proxy_env(proxy_url)

    assert calls[:3] == [("wait", proxy_url), "terminate", "wait_for_exit"]
    args = calls[3][1]
    assert args[0] == "open"
    assert f"HTTPS_PROXY={proxy_url}" in args
    assert f"HTTP_PROXY={proxy_url}" in args
    assert "FLEASION_PROXY_RELAUNCHED=1" in args
    assert args[-2:] == ["-a", str(app)]
    assert calls[4] == ("wait_for_start", 15.0)


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
