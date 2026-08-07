import base64
import json
import plistlib
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.utils import autostart


def test_windows_autostart_task_runs_at_least_privilege(monkeypatch):
    script_text = []

    def fake_run(args, **_kwargs):
        if "-EncodedCommand" in args:
            encoded = args[args.index("-EncodedCommand") + 1]
            script_text.append(base64.b64decode(encoded).decode("utf-16-le"))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "TestUser")
    monkeypatch.setenv("USERDOMAIN", "TestDomain")
    monkeypatch.setattr(autostart.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    assert autostart._create_task({"mode": "exe", "path": r"C:\Fleasion\Fleasion.exe"})
    assert "$principal.LogonType = 3" in script_text[0]
    assert "$principal.RunLevel = 0" in script_text[0]
    assert "RegisterTaskDefinition" in script_text[0]


def test_windows_autostart_hint_distinguishes_proxy_mode():
    assert "normal per-user task" in autostart.windows_autostart_privilege_hint("env")
    assert "requires administrator permission" in autostart.windows_autostart_privilege_hint(
        "hosts"
    )


def test_windows_autostart_refreshes_when_proxy_mode_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(
        autostart,
        "_get_launch_info",
        lambda: {"mode": "exe", "path": r"C:\Fleasion\Fleasion.exe", "_fmt": 8},
    )
    monkeypatch.setattr(autostart, "_task_exists", lambda: True)
    (tmp_path / "autostart_info.json").write_text(
        json.dumps(
            {
                "mode": "exe",
                "path": r"C:\Fleasion\Fleasion.exe",
                "_fmt": 8,
                "proxy_mode": "hosts",
            }
        ),
        encoding="utf-8",
    )
    created = []
    monkeypatch.setattr(
        autostart,
        "_create_task",
        lambda info, **kwargs: created.append((info, kwargs)) or True,
    )

    assert autostart.sync_autostart(True, tmp_path, proxy_mode="env")
    assert created == [
        (
            {
                "mode": "exe",
                "path": r"C:\Fleasion\Fleasion.exe",
                "_fmt": 8,
                "proxy_mode": "env",
            },
            {"windows_user_id": None},
        )
    ]


def test_elevated_windows_autostart_targets_requesting_user(monkeypatch):
    xml_text = []

    def fake_run(args, **_kwargs):
        if "/Create" in args:
            xml_path = Path(args[args.index("/XML") + 1])
            xml_text.append(xml_path.read_text(encoding="utf-16"))
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setenv("USERNAME", "ElevatedAdmin")
    monkeypatch.setenv("USERDOMAIN", "AdminDomain")
    monkeypatch.setattr(autostart.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    assert autostart._create_task(
        {"mode": "exe", "path": r"C:\Fleasion\Fleasion.exe"},
        windows_user_id=r"DesktopDomain\OriginalUser",
    )
    assert r"DesktopDomain\OriginalUser" in xml_text[0]
    assert "ElevatedAdmin" not in xml_text[0]


def test_windows_uv_launch_info_uses_checkout_project_root(monkeypatch):
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.delattr(autostart.sys, 'frozen', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda _name: r'C:\Tools\uv.exe')

    launch_info = autostart._get_launch_info()

    assert launch_info['mode'] == 'uv'
    assert launch_info['path'] == r'C:\Tools\uv.exe'
    assert Path(launch_info['project']) == Path(__file__).resolve().parents[1]


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


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux desktop-entry path semantics')
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


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux desktop-entry path semantics')
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
