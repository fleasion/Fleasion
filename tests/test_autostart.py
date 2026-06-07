import plistlib

from Fleasion.utils import autostart


def test_macos_launch_agent_update_does_not_start_second_instance(tmp_path, monkeypatch):
    agent_path = tmp_path / "LaunchAgents" / "com.fleasion.autostart.plist"
    launch_calls = []

    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "LAUNCH_AGENT_PATH", agent_path)
    monkeypatch.setattr(autostart.subprocess, "run", lambda *args, **kwargs: launch_calls.append(args[0]))

    assert autostart._create_task({"mode": "exe", "path": "/Applications/Fleasion.app/Contents/MacOS/Fleasion"})

    plist = plistlib.loads(agent_path.read_bytes())
    assert plist["RunAtLoad"] is True
    assert plist["ProgramArguments"][-1] == "--no-dashboard"
    assert launch_calls == []
