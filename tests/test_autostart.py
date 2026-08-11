import base64
import json
import plistlib
import shutil
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.utils import autostart


def _fake_winreg(monkeypatch):
    values = {}
    open_accesses = []
    module = types.ModuleType('winreg')
    module.HKEY_CURRENT_USER = object()
    module.REG_SZ = 1
    module.KEY_QUERY_VALUE = 0x0001
    module.KEY_SET_VALUE = 0x0002

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def open_key(_root, _path, _reserved=0, access=0):
        open_accesses.append(access)
        if not values:
            raise FileNotFoundError
        return Key()

    module.OpenKey = open_key

    def create_key_ex(_root, _path, _reserved=0, access=0):
        open_accesses.append(access)
        return Key()

    module.CreateKeyEx = create_key_ex
    module.QueryValueEx = lambda _key, name: values[name]
    module.SetValueEx = lambda _key, name, _reserved, kind, value: values.__setitem__(
        name, (value, kind)
    )

    def delete_value(_key, name):
        try:
            del values[name]
        except KeyError as exc:
            raise FileNotFoundError from exc

    module.DeleteValue = delete_value
    monkeypatch.setitem(sys.modules, 'winreg', module)
    return values, open_accesses


def test_packaged_windows_autostart_uses_native_run_key(monkeypatch, tmp_path):
    values, open_accesses = _fake_winreg(monkeypatch)
    legacy_cleanup = []
    launch_info = {
        'mode': 'exe',
        'path': r'C:\Program Files\Fleasion\Fleasion.exe',
        '_fmt': autostart._TASK_FORMAT_VERSION,
    }

    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_get_launch_info', lambda: launch_info.copy())
    monkeypatch.setattr(
        autostart,
        '_delete_legacy_windows_task_async',
        lambda config_dir: legacy_cleanup.append(config_dir),
    )
    monkeypatch.setattr(
        autostart.subprocess,
        'run',
        lambda *_args, **_kwargs: pytest.fail('packaged autostart must not start a subprocess'),
    )

    assert autostart.sync_autostart(True, tmp_path)
    assert values['Fleasion'] == (
        r'"C:\Program Files\Fleasion\Fleasion.exe" --no-dashboard',
        1,
    )
    assert legacy_cleanup == [tmp_path]
    assert json.loads((tmp_path / 'autostart_info.json').read_text()) == launch_info
    assert open_accesses == [0x0001, 0x0002]

    assert autostart.sync_autostart(True, tmp_path)
    assert legacy_cleanup == [tmp_path, tmp_path]
    assert open_accesses[-1] == 0x0001


def test_disabling_windows_autostart_removes_native_run_entry(monkeypatch, tmp_path):
    values, open_accesses = _fake_winreg(monkeypatch)
    values['Fleasion'] = (r'C:\Fleasion\Fleasion.exe --no-dashboard', 1)
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_task_exists', lambda: False)

    assert autostart.sync_autostart(False, tmp_path)
    assert 'Fleasion' not in values
    assert open_accesses == [0x0002]


def test_overlong_windows_run_command_falls_back_to_scheduled_task(monkeypatch, tmp_path):
    values, _open_accesses = _fake_winreg(monkeypatch)
    launch_info = {
        'mode': 'exe',
        'path': 'C:\\' + ('nested\\' * 40) + 'Fleasion.exe',
        '_fmt': autostart._TASK_FORMAT_VERSION,
    }
    created = []
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_get_launch_info', lambda: launch_info.copy())
    monkeypatch.setattr(autostart, '_task_exists', lambda: False)
    monkeypatch.setattr(
        autostart,
        '_create_task',
        lambda info, **kwargs: created.append((info, kwargs)) or True,
    )

    assert len(autostart._windows_run_command(launch_info)) > 260
    assert autostart.sync_autostart(True, tmp_path)
    assert values == {}
    assert created == [(launch_info, {'windows_user_id': None})]


def test_switching_to_existing_windows_dev_task_removes_packaged_run_entry(
    monkeypatch, tmp_path
):
    values, open_accesses = _fake_winreg(monkeypatch)
    values['Fleasion'] = (r'C:\Fleasion.exe --no-dashboard', 1)
    launch_info = {
        'mode': 'uv',
        'path': r'C:\Tools\uv.exe',
        'project': r'C:\Fleasion',
        '_fmt': autostart._TASK_FORMAT_VERSION,
        'log': str(tmp_path / 'autostart_launch_error.log'),
    }
    (tmp_path / 'autostart_info.json').write_text(json.dumps(launch_info))
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(
        autostart,
        '_get_launch_info',
        lambda: {key: value for key, value in launch_info.items() if key != 'log'},
    )
    monkeypatch.setattr(autostart, '_task_exists', lambda: True)
    monkeypatch.setattr(
        autostart,
        '_create_task',
        lambda *_args, **_kwargs: pytest.fail('current task should not be recreated'),
    )

    assert autostart.sync_autostart(True, tmp_path)
    assert values == {}
    assert open_accesses == [0x0002]


@pytest.mark.parametrize(
    ('returncodes', 'marker_text', 'expected_call_count'),
    [
        ([1], 'legacy task absent\n', 1),
        ([0, 0], 'legacy task deleted\n', 2),
    ],
)
def test_legacy_windows_task_cleanup_is_background_and_persistent(
    monkeypatch,
    tmp_path,
    returncodes,
    marker_text,
    expected_call_count,
):
    calls = []

    class ImmediateThread:
        def __init__(self, *, target, name, daemon):
            assert name == 'FleasionAutostartMigration'
            assert daemon is True
            self._target = target

        def start(self):
            self._target()

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncodes[len(calls) - 1])

    monkeypatch.setattr(autostart, '_legacy_task_cleanup_started', False)
    monkeypatch.setattr(autostart.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(autostart.subprocess, 'run', fake_run)
    monkeypatch.setattr(autostart.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)

    autostart._delete_legacy_windows_task_async(tmp_path)

    marker = tmp_path / autostart._LEGACY_TASK_CLEANUP_MARKER
    assert marker.read_text() == marker_text
    assert len(calls) == expected_call_count
    assert all(call[1]['timeout'] == 30 for call in calls)
    assert all(call[1]['creationflags'] == 0x08000000 for call in calls)

    autostart._delete_legacy_windows_task_async(tmp_path)
    assert len(calls) == expected_call_count


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
    assert "normal per-user autostart" in autostart.windows_autostart_privilege_hint("env")
    assert "requires administrator permission" in autostart.windows_autostart_privilege_hint(
        "hosts"
    )


def test_windows_autostart_refreshes_when_proxy_mode_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(
        autostart,
        "_get_launch_info",
        lambda: {
            "mode": "uv",
            "path": r"C:\Tools\uv.exe",
            "project": r"C:\Fleasion",
            "_fmt": autostart._TASK_FORMAT_VERSION,
        },
    )
    monkeypatch.setattr(autostart, "_task_exists", lambda: True)
    (tmp_path / "autostart_info.json").write_text(
        json.dumps(
            {
                "mode": "uv",
                "path": r"C:\Tools\uv.exe",
                "project": r"C:\Fleasion",
                "_fmt": autostart._TASK_FORMAT_VERSION,
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
                "mode": "uv",
                "path": r"C:\Tools\uv.exe",
                "project": r"C:\Fleasion",
                "_fmt": autostart._TASK_FORMAT_VERSION,
                "log": str(tmp_path / "autostart_launch_error.log"),
                "proxy_mode": "env",
            },
            {"windows_user_id": None},
        )
    ]


def test_elevated_windows_autostart_targets_requesting_user(monkeypatch):
    xml_text = []
    acl_script = []

    def fake_run(args, **_kwargs):
        if "/Create" in args:
            xml_path = Path(args[args.index("/XML") + 1])
            xml_text.append(xml_path.read_text(encoding="utf-16"))
        elif "-EncodedCommand" in args:
            encoded = args[args.index("-EncodedCommand") + 1]
            acl_script.append(base64.b64decode(encoded).decode("utf-16-le"))
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
    assert "SetSecurityDescriptor" in acl_script[0]
    assert "DesktopDomain\\OriginalUser" in acl_script[0]


def test_windows_uv_launch_info_uses_checkout_project_root(monkeypatch):
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.delattr(autostart.sys, 'frozen', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda _name: r'C:\Tools\uv.exe')

    launch_info = autostart._get_launch_info()

    assert launch_info['mode'] == 'uv'
    assert launch_info['path'] == r'C:\Tools\uv.exe'
    assert Path(launch_info['project']) == Path(__file__).resolve().parents[1]


def test_windows_uv_launch_info_finds_the_per_user_install_when_path_lookup_fails(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.delattr(autostart.sys, 'frozen', raising=False)
    monkeypatch.setattr(shutil, 'which', lambda _name: None)
    installed_uv = tmp_path / '.local' / 'bin' / 'uv.exe'
    installed_uv.parent.mkdir(parents=True)
    installed_uv.write_bytes(b'uv')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))

    launch_info = autostart._get_launch_info()

    assert launch_info['path'] == str(installed_uv)


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
