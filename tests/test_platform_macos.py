from pathlib import Path

from Fleasion.utils import platform_macos


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
