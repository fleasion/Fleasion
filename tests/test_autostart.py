import base64
import json
import plistlib
import shutil
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, NotRequired, Protocol, Self, TypedDict, cast

import pytest

from fleasion.utils import autostart

if TYPE_CHECKING:
    from collections.abc import Callable


class _CreateTaskInfo(TypedDict):
    mode: str
    path: str
    project: NotRequired[str]


class _CreateTask(Protocol):
    def __call__(
        self,
        launch_info: _CreateTaskInfo,
        *,
        windows_user_id: str | None = None,
    ) -> bool: ...


class _ProjectLaunchInfo(TypedDict):
    project: str


def _private(name: str) -> object:
    return autostart.__dict__[name]


TASK_FORMAT_VERSION = cast('int', _private('_TASK_FORMAT_VERSION'))
LEGACY_TASK_CLEANUP_MARKER = cast('str', _private('_LEGACY_TASK_CLEANUP_MARKER'))
WINDOWS_RUN_COMMAND_MAX = cast('int', _private('_WINDOWS_RUN_COMMAND_MAX'))
windows_run_command = cast(
    'Callable[[autostart.LaunchInfo], str]', _private('_windows_run_command')
)
delete_task = cast('Callable[[], bool]', _private('_delete_task'))
delete_legacy_windows_task_async = cast(
    'Callable[[Path], None]', _private('_delete_legacy_windows_task_async')
)
get_launch_info = cast('Callable[[], autostart.LaunchInfo]', _private('_get_launch_info'))
create_task = cast('_CreateTask', _private('_create_task'))


def _fake_winreg(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, tuple[str, int]], list[int]]:
    values: dict[str, tuple[str, int]] = {}
    open_accesses: list[int] = []
    module = types.ModuleType('winreg')
    module.__dict__['HKEY_CURRENT_USER'] = object()
    module.__dict__['REG_SZ'] = 1
    module.__dict__['KEY_QUERY_VALUE'] = 0x0001
    module.__dict__['KEY_SET_VALUE'] = 0x0002

    class Key:
        def __enter__(self) -> Self:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

    def open_key(_root: object, _path: object, _reserved: object = 0, access: int = 0) -> Key:
        open_accesses.append(access)
        if not values:
            raise FileNotFoundError
        return Key()

    module.__dict__['OpenKey'] = open_key

    def create_key_ex(_root: object, _path: object, _reserved: object = 0, access: int = 0) -> Key:
        open_accesses.append(access)
        return Key()

    module.__dict__['CreateKeyEx'] = create_key_ex

    def query_value_ex(_key: object, name: str) -> tuple[str, int]:
        return values[name]

    def set_value_ex(_key: object, name: str, _reserved: object, kind: int, value: str) -> None:
        values[name] = (value, kind)

    module.__dict__['QueryValueEx'] = query_value_ex
    module.__dict__['SetValueEx'] = set_value_ex

    def delete_value(_key: object, name: str) -> None:
        try:
            del values[name]
        except KeyError as exc:
            raise FileNotFoundError from exc

    module.__dict__['DeleteValue'] = delete_value
    monkeypatch.setattr(autostart, 'winreg', module)
    return values, open_accesses


def test_packaged_windows_autostart_uses_native_run_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, open_accesses = _fake_winreg(monkeypatch)
    legacy_cleanup: list[Path] = []
    launch_info: autostart.LaunchInfo = {
        'mode': 'exe',
        'path': r'C:\Program Files\Fleasion\Fleasion.exe',
        '_fmt': TASK_FORMAT_VERSION,
    }

    def record_legacy_cleanup(config_dir: Path) -> None:
        legacy_cleanup.append(config_dir)

    def forbid_subprocess(*_args: object, **_kwargs: object) -> None:
        pytest.fail('packaged autostart must not start a subprocess')

    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_get_launch_info', lambda: launch_info.copy())
    monkeypatch.setattr(autostart, '_delete_legacy_windows_task_async', record_legacy_cleanup)
    monkeypatch.setattr(autostart.subprocess, 'run', forbid_subprocess)

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


def test_disabling_windows_autostart_removes_native_run_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, open_accesses = _fake_winreg(monkeypatch)
    values['Fleasion'] = (r'C:\Fleasion\Fleasion.exe --no-dashboard', 1)
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_task_exists', lambda: False)

    assert autostart.sync_autostart(False, tmp_path)
    assert 'Fleasion' not in values
    assert open_accesses == [0x0002]


def test_disabling_windows_autostart_removes_run_entry_before_legacy_task_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, open_accesses = _fake_winreg(monkeypatch)
    values['Fleasion'] = (r'C:\\Fleasion\\Fleasion.exe --no-dashboard', 1)
    messages: list[str] = []
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_task_exists', lambda: True)
    monkeypatch.setattr(autostart, '_delete_task', lambda: False)
    monkeypatch.setattr(autostart, '_log', messages.append)

    assert not autostart.sync_autostart(False, tmp_path)
    assert 'Fleasion' not in values
    assert open_accesses == [0x0002]
    assert any('legacy scheduled task' in message for message in messages)


def test_windows_task_deletion_logs_scheduler_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    messages: list[str] = []
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)

    def failed_delete(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout=b'', stderr=b'ERROR: Access is denied.')

    monkeypatch.setattr(autostart.subprocess, 'run', failed_delete)
    monkeypatch.setattr(autostart, '_log', messages.append)

    assert not delete_task()
    assert messages == [
        "Failed to delete scheduled task 'Fleasion_Autostart' (rc=1): ERROR: Access is denied."
    ]


def test_overlong_windows_run_command_falls_back_to_scheduled_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, _open_accesses = _fake_winreg(monkeypatch)
    launch_info: autostart.LaunchInfo = {
        'mode': 'exe',
        'path': 'C:\\' + ('nested\\' * 40) + 'Fleasion.exe',
        '_fmt': TASK_FORMAT_VERSION,
    }
    created: list[tuple[autostart.LaunchInfo, dict[str, object]]] = []

    def record_created(info: autostart.LaunchInfo, **kwargs: object) -> bool:
        created.append((info, kwargs))
        return True

    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(autostart, '_get_launch_info', lambda: launch_info.copy())
    monkeypatch.setattr(autostart, '_task_exists', lambda: False)
    monkeypatch.setattr(autostart, '_create_task', record_created)

    assert len(windows_run_command(launch_info)) > 260
    assert autostart.sync_autostart(True, tmp_path)
    assert values == {}
    assert created == [(launch_info, {'windows_user_id': None})]


def test_switching_to_existing_windows_dev_task_removes_packaged_run_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values, open_accesses = _fake_winreg(monkeypatch)
    values['Fleasion'] = (r'C:\Fleasion.exe --no-dashboard', 1)
    launch_info: autostart.LaunchInfo = {
        'mode': 'uv',
        'path': r'C:\Tools\uv.exe',
        'project': r'C:\Fleasion',
        '_fmt': TASK_FORMAT_VERSION,
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

    def fail_create(*_args: object, **_kwargs: object) -> None:
        pytest.fail('current task should not be recreated')

    monkeypatch.setattr(autostart, '_create_task', fail_create)

    assert autostart.sync_autostart(True, tmp_path)
    assert values == {}
    assert open_accesses == [0x0002]


@pytest.mark.parametrize(
    'returncodes, marker_text, expected_call_count',
    [
        ([1], 'legacy task absent\n', 1),
        ([0, 0], 'legacy task deleted\n', 2),
    ],
)
def test_legacy_windows_task_cleanup_is_background_and_persistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    returncodes: list[int],
    marker_text: str,
    expected_call_count: int,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], object], name: str, daemon: bool) -> None:
            assert name == 'FleasionAutostartMigration'
            assert daemon is True
            self._target: Callable[[], object] = target

        def start(self) -> None:
            self._target()

    def fake_run(args: list[str], **kwargs: object) -> SimpleNamespace:
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=returncodes[len(calls) - 1])

    monkeypatch.setattr(autostart, '_legacy_task_cleanup_started', False)
    monkeypatch.setattr(autostart.threading, 'Thread', ImmediateThread)
    monkeypatch.setattr(autostart.subprocess, 'run', fake_run)
    monkeypatch.setattr(autostart.subprocess, 'CREATE_NO_WINDOW', 0x08000000, raising=False)

    delete_legacy_windows_task_async(tmp_path)

    marker = tmp_path / LEGACY_TASK_CLEANUP_MARKER
    assert marker.read_text() == marker_text
    assert len(calls) == expected_call_count
    assert all(call[1]['timeout'] == 30 for call in calls)
    assert all(call[1]['creationflags'] == 0x08000000 for call in calls)

    delete_legacy_windows_task_async(tmp_path)
    assert len(calls) == expected_call_count


def test_windows_autostart_task_runs_at_least_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    script_text: list[str] = []

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        if '-EncodedCommand' in args:
            encoded = args[args.index('-EncodedCommand') + 1]
            script_text.append(base64.b64decode(encoded).decode('utf-16-le'))
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setenv('USERNAME', 'TestUser')
    monkeypatch.setenv('USERDOMAIN', 'TestDomain')
    monkeypatch.setattr(autostart.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(autostart.subprocess, 'run', fake_run)

    assert create_task({'mode': 'exe', 'path': r'C:\Fleasion\Fleasion.exe'})
    assert '$principal.LogonType = 3' in script_text[0]
    assert '$principal.RunLevel = 0' in script_text[0]
    assert 'RegisterTaskDefinition' in script_text[0]


def test_windows_autostart_hint_distinguishes_proxy_mode() -> None:
    assert 'normal per-user autostart' in autostart.windows_autostart_privilege_hint('env')
    assert 'requires administrator permission' in autostart.windows_autostart_privilege_hint(
        'hosts'
    )


def test_windows_autostart_refreshes_when_proxy_mode_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setattr(
        autostart,
        '_get_launch_info',
        lambda: {
            'mode': 'uv',
            'path': r'C:\Tools\uv.exe',
            'project': r'C:\Fleasion',
            '_fmt': TASK_FORMAT_VERSION,
        },
    )
    monkeypatch.setattr(autostart, '_task_exists', lambda: True)
    (tmp_path / 'autostart_info.json').write_text(
        json.dumps(
            {
                'mode': 'uv',
                'path': r'C:\Tools\uv.exe',
                'project': r'C:\Fleasion',
                '_fmt': TASK_FORMAT_VERSION,
                'proxy_mode': 'hosts',
            }
        ),
        encoding='utf-8',
    )
    created: list[tuple[autostart.LaunchInfo, dict[str, object]]] = []

    def record_created(info: autostart.LaunchInfo, **kwargs: object) -> bool:
        created.append((info, kwargs))
        return True

    monkeypatch.setattr(autostart, '_create_task', record_created)

    assert autostart.sync_autostart(True, tmp_path, proxy_mode='env')
    assert created == [
        (
            {
                'mode': 'uv',
                'path': r'C:\Tools\uv.exe',
                'project': r'C:\Fleasion',
                '_fmt': TASK_FORMAT_VERSION,
                'log': str(tmp_path / 'autostart_launch_error.log'),
                'proxy_mode': 'env',
            },
            {'windows_user_id': None},
        )
    ]


def test_elevated_windows_autostart_targets_requesting_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    xml_text: list[str] = []
    acl_script: list[str] = []

    def fake_run(args: list[str], **_kwargs: object) -> SimpleNamespace:
        if '/Create' in args:
            xml_path = Path(args[args.index('/XML') + 1])
            xml_text.append(xml_path.read_text(encoding='utf-16'))
        elif '-EncodedCommand' in args:
            encoded = args[args.index('-EncodedCommand') + 1]
            acl_script.append(base64.b64decode(encoded).decode('utf-16-le'))
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.setenv('USERNAME', 'ElevatedAdmin')
    monkeypatch.setenv('USERDOMAIN', 'AdminDomain')
    monkeypatch.setattr(autostart.subprocess, 'CREATE_NO_WINDOW', 0, raising=False)
    monkeypatch.setattr(autostart.subprocess, 'run', fake_run)

    assert create_task(
        {'mode': 'exe', 'path': r'C:\Fleasion\Fleasion.exe'},
        windows_user_id=r'DesktopDomain\OriginalUser',
    )
    assert r'DesktopDomain\OriginalUser' in xml_text[0]
    assert 'ElevatedAdmin' not in xml_text[0]
    assert 'SetSecurityDescriptor' in acl_script[0]
    assert 'DesktopDomain\\OriginalUser' in acl_script[0]


def test_windows_uv_launch_info_uses_checkout_project_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.delattr(autostart.sys, 'frozen', raising=False)

    def fake_which(_name: str) -> str:
        return r'C:\Tools\uv.exe'

    monkeypatch.setattr(shutil, 'which', fake_which)

    launch_info = get_launch_info()

    assert launch_info['mode'] == 'uv'
    assert launch_info['path'] == r'C:\Tools\uv.exe'
    project_launch_info = cast('_ProjectLaunchInfo', launch_info)
    assert Path(project_launch_info['project']) == Path(__file__).resolve().parents[1]


def test_windows_uv_launch_info_finds_the_per_user_install_when_path_lookup_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(autostart.sys, 'platform', 'win32')
    monkeypatch.delattr(autostart.sys, 'frozen', raising=False)

    def fake_which(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, 'which', fake_which)
    installed_uv = tmp_path / '.local' / 'bin' / 'uv.exe'
    installed_uv.parent.mkdir(parents=True)
    installed_uv.write_bytes(b'uv')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))

    launch_info = get_launch_info()

    assert launch_info['path'] == str(installed_uv)


def test_macos_launch_agent_update_does_not_start_second_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agent_path = tmp_path / 'LaunchAgents' / 'com.fleasion.autostart.plist'
    launch_calls: list[object] = []

    monkeypatch.setattr(autostart.sys, 'platform', 'darwin')
    monkeypatch.setattr(autostart, 'LAUNCH_AGENT_PATH', agent_path)

    def record_launch(*args: object, **_kwargs: object) -> None:
        launch_calls.append(args[0])

    monkeypatch.setattr(autostart.subprocess, 'run', record_launch)

    assert create_task(
        {'mode': 'exe', 'path': '/Applications/Fleasion.app/Contents/MacOS/Fleasion'}
    )

    plist = plistlib.loads(agent_path.read_bytes())
    assert plist['RunAtLoad'] is True
    assert plist['ProgramArguments'][-1] == '--no-dashboard'
    assert launch_calls == []


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux desktop-entry path semantics')
def test_linux_autostart_quotes_exec_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    autostart_path = tmp_path / '.config' / 'autostart' / 'fleasion.desktop'
    project = tmp_path / 'Project Folder'

    monkeypatch.setattr(autostart.sys, 'platform', 'linux')
    monkeypatch.setattr(autostart, 'LINUX_AUTOSTART_PATH', autostart_path)
    monkeypatch.setattr(autostart, '_linux_installed_launcher', lambda: None)

    assert create_task(
        {
            'mode': 'python',
            'path': '/opt/Fleasion Python',
            'project': str(project),
        }
    )

    desktop_entry = autostart_path.read_text(encoding='utf-8')
    assert 'Exec="/opt/Fleasion Python" -m fleasion --no-dashboard' in desktop_entry


@pytest.mark.skipif(sys.platform == 'win32', reason='Linux desktop-entry path semantics')
def test_linux_autostart_prefers_installed_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    autostart_path = tmp_path / '.config' / 'autostart' / 'fleasion.desktop'
    launcher = tmp_path / '.local' / 'bin' / 'fleasion-launch'
    launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\n', encoding='utf-8')

    monkeypatch.setattr(autostart.sys, 'platform', 'linux')
    monkeypatch.setattr(autostart, 'LINUX_AUTOSTART_PATH', autostart_path)
    monkeypatch.setattr(autostart, '_linux_installed_launcher', lambda: launcher)

    launch_info = get_launch_info()
    assert launch_info == {
        'mode': 'linux-launcher',
        'path': str(launcher),
        '_fmt': TASK_FORMAT_VERSION,
    }

    assert create_task(launch_info)

    desktop_entry = autostart_path.read_text(encoding='utf-8')
    assert f'Exec={launcher} --no-dashboard' in desktop_entry
    assert f'Path={launcher.parent}' in desktop_entry
    assert 'Project Folder' not in desktop_entry
