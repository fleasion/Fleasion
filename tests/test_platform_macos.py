from pathlib import Path

from fleasion.utils import platform_macos


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
