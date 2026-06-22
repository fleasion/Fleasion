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


def test_linux_autostart_quotes_exec_tokens(tmp_path, monkeypatch):
    autostart_path = tmp_path / ".config" / "autostart" / "fleasion.desktop"
    project = tmp_path / "Project Folder"

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(autostart, "LINUX_AUTOSTART_PATH", autostart_path)
    monkeypatch.setattr(autostart, "_linux_installed_launcher", lambda: None)

    assert autostart._create_task(
        {
            "mode": "python",
            "path": "/opt/Fleasion Python",
            "project": str(project),
        }
    )

    desktop_entry = autostart_path.read_text(encoding="utf-8")
    assert 'Exec="/opt/Fleasion Python" "' in desktop_entry
    assert 'launcher.py" --no-dashboard' in desktop_entry


def test_linux_autostart_prefers_installed_launcher(tmp_path, monkeypatch):
    autostart_path = tmp_path / ".config" / "autostart" / "fleasion.desktop"
    launcher = tmp_path / ".local" / "bin" / "fleasion-launch"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setattr(autostart.sys, "platform", "linux")
    monkeypatch.setattr(autostart, "LINUX_AUTOSTART_PATH", autostart_path)
    monkeypatch.setattr(autostart, "_linux_installed_launcher", lambda: launcher)

    launch_info = autostart._get_launch_info()
    assert launch_info == {
        "mode": "linux-launcher",
        "path": str(launcher),
        "_fmt": autostart._TASK_FORMAT_VERSION,
    }

    assert autostart._create_task(launch_info)

    desktop_entry = autostart_path.read_text(encoding="utf-8")
    assert f"Exec={launcher} --no-dashboard" in desktop_entry
    assert f"Path={launcher.parent}" in desktop_entry
    assert "Project Folder" not in desktop_entry
