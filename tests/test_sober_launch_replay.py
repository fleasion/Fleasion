"""Verify Sober Auto handoff capture and instance-restricted relaunch."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import sober_launch_replay as replay
from fleasion.utils.linux_clients import SOBER_CLIENT

if TYPE_CHECKING:
    import pytest


_SCOPE = 'app-flatpak-org.vinegarhq.Sober-3320972443.scope'
_URI = 'roblox-player:1+launchmode:play+gameinfo:private-one-use-ticket'


def _launcher(root: Path, *, pid: int, uri: str | None = _URI, ticks: int = 500) -> Path:
    path = root / str(pid)
    path.mkdir(parents=True)
    (path / 'comm').write_text('sober\n', encoding='utf-8')
    (path / 'cgroup').write_text(f'0::/user.slice/{_SCOPE}\n', encoding='utf-8')
    (path / 'stat').write_text(
        f'{pid} (sober) ' + ' '.join(['S', *['0'] * 18, str(ticks)]), encoding='utf-8'
    )
    (path / 'exe').symlink_to('/app/bin/sober')
    args = [b'/proc/self/exe', b'--']
    if uri is not None:
        args.append(uri.encode('utf-8'))
    (path / 'cmdline').write_bytes(b'\0'.join(args) + b'\0')
    (path / 'cwd').symlink_to(root.parent, target_is_directory=True)
    return path


def test_sober_capture_keeps_exact_uri_without_persisting_or_logging(tmp_path: Path) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321)
    _launcher(root, pid=322, ticks=510)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot == replay.SoberLaunchSnapshot(_SCOPE, 321, 500, (_URI,), tmp_path)
    assert repr(snapshot).count(_URI) == 1


def test_sober_capture_preserves_empty_initial_arguments(tmp_path: Path) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321, uri=None)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    assert snapshot.args == ()
    assert snapshot.cwd == tmp_path


def test_sober_capture_rejects_ambiguous_generations_and_unknown_invocation(tmp_path: Path) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    first = _launcher(root, pid=321)
    _launcher(root, pid=322, uri='roblox-player:another-uri', ticks=510)
    assert replay.capture_sober_launch(proc_root=root) is None
    (root / '322' / 'cmdline').write_bytes(b'/proc/self/exe\0--\0' + _URI.encode() + b'\0')
    (first / 'cmdline').write_bytes(b'/proc/self/exe\0--unknown-wrapper\0' + _URI.encode() + b'\0')
    assert replay.capture_sober_launch(proc_root=root) is None
    (first / 'cmdline').write_bytes(b'/proc/self/exe\0--\0' + _URI.encode())
    assert replay.capture_sober_launch(proc_root=root) is None


def test_sober_replay_targets_exact_instance_and_preserves_launch_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    alive = True
    commands: list[list[str]] = []
    launches: list[tuple[list[str], Path | None]] = []
    logs: list[str] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal alive
        commands.append(command)
        if command[1] == 'ps':
            text = f'3320972443\t{SOBER_CLIENT.app_id}\n' if alive else ''
            return subprocess.CompletedProcess(command, 0, text, '')
        assert command == ['/usr/bin/flatpak', 'kill', '3320972443']
        alive = False
        return subprocess.CompletedProcess(command, 0, '', '')

    def start(command: list[str], *, cwd: Path | None = None) -> object:
        launches.append((command, cwd))
        return object()

    def scope(*, proc_root: Path) -> str | None:
        assert proc_root == root
        return _SCOPE if alive else None

    def flatpak_executable(_name: str) -> str:
        return '/usr/bin/flatpak'

    monkeypatch.setattr(replay.shutil, 'which', flatpak_executable)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_popen', start)
    monkeypatch.setattr(replay, 'sober_flatpak_session_identity', scope)
    def record_message(_category: str, message: str) -> None:
        logs.append(message)

    monkeypatch.setattr(replay.log_buffer, 'log', record_message)
    assert replay.replay_sober_launch(snapshot, proc_root=root)
    assert commands[0] == ['/usr/bin/flatpak', 'ps', '--columns=instance,application']
    assert any(command[1] == 'kill' for command in commands)
    assert launches == [(['/usr/bin/flatpak', 'run', SOBER_CLIENT.app_id, _URI], tmp_path)]
    assert all(_URI not in message for message in logs)


def test_sober_replay_does_not_kill_after_original_argv_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    launcher = _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    calls: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        assert command[1] == 'ps'
        return subprocess.CompletedProcess(
            command, 0, f'3320972443\t{SOBER_CLIENT.app_id}\n', ''
        )

    def flatpak_executable(_name: str) -> str:
        return '/usr/bin/flatpak'

    monkeypatch.setattr(replay.shutil, 'which', flatpak_executable)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_run', run)
    (launcher / 'cmdline').write_bytes(
        b'/proc/self/exe\0--\0roblox-player:changed-context\0'
    )
    assert not replay.replay_sober_launch(snapshot, proc_root=root)
    assert len(calls) == 1


def test_sober_replay_waits_for_engine_after_flatpak_entry_disappears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    state = {'alive': True, 'pending': 2}
    launched: list[list[str]] = []

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] == 'kill':
            state['alive'] = False
            return subprocess.CompletedProcess(command, 0, '', '')
        rows = f'3320972443\t{SOBER_CLIENT.app_id}\n' if state['alive'] else ''
        return subprocess.CompletedProcess(command, 0, rows, '')

    def scope(*, proc_root: Path) -> str | None:
        assert proc_root == root
        if state['alive']:
            return _SCOPE
        if state['pending']:
            state['pending'] -= 1
            return _SCOPE
        return None

    def flatpak_executable(_name: str) -> str:
        return '/usr/bin/flatpak'

    def launch(command: list[str], *, cwd: Path | None = None) -> object:
        assert cwd == tmp_path
        launched.append(command)
        return object()

    monkeypatch.setattr(replay.shutil, 'which', flatpak_executable)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_popen', launch)
    monkeypatch.setattr(replay, 'sober_flatpak_session_identity', scope)
    def no_delay(_interval: float) -> None:
        return None

    monkeypatch.setattr(replay.time, 'sleep', no_delay)
    assert replay.replay_sober_launch(snapshot, proc_root=root)
    assert launched == [['/usr/bin/flatpak', 'run', SOBER_CLIENT.app_id, _URI]]
    assert state['pending'] == 0


def test_sober_replay_does_not_spawn_if_another_instance_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    state = {'killed': False}

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[1] == 'kill':
            state['killed'] = True
            return subprocess.CompletedProcess(command, 0, '', '')
        instance = '888' if state['killed'] else '3320972443'
        return subprocess.CompletedProcess(command, 0, f'{instance}\t{SOBER_CLIENT.app_id}\n', '')

    def flatpak_executable(_name: str) -> str:
        return '/usr/bin/flatpak'

    def launch(_command: list[str], *, cwd: Path | None = None) -> object:
        msg = 'Never start a duplicate Sober instance'
        raise AssertionError(msg)

    monkeypatch.setattr(replay.shutil, 'which', flatpak_executable)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_run', run)
    monkeypatch.setattr(replay.platform_linux, '_standard_user_popen', launch)
    assert not replay.replay_sober_launch(snapshot, proc_root=root)
    assert state['killed']


def test_sober_replay_refuses_different_or_multiple_instances(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None
    seen: list[list[str]] = []
    def flatpak_executable(_name: str) -> str:
        return '/usr/bin/flatpak'

    monkeypatch.setattr(replay.shutil, 'which', flatpak_executable)
    for rows in (
        f'777\t{SOBER_CLIENT.app_id}\n',
        f'3320972443\t{SOBER_CLIENT.app_id}\n888\t{SOBER_CLIENT.app_id}\n',
    ):
        def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append(command)
            assert command[1] == 'ps'
            return subprocess.CompletedProcess(command, 0, rows, '')

        monkeypatch.setattr(replay.platform_linux, '_standard_user_run', run)
        assert not replay.replay_sober_launch(snapshot, proc_root=root)
    assert len(seen) == 2


def test_sober_replay_refuses_replaced_launcher_or_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    launcher = _launcher(root, pid=321)
    snapshot = replay.capture_sober_launch(proc_root=root)
    assert snapshot is not None

    def forbidden(*_args: object, **_kwargs: object) -> None:
        msg = 'Unverified Sober must not be killed'
        raise AssertionError(msg)

    monkeypatch.setattr(replay.platform_linux, '_standard_user_run', forbidden)
    (launcher / 'stat').write_text(
        '321 (sober) ' + ' '.join(['S', *['0'] * 18, '999']), encoding='utf-8'
    )
    assert not replay.replay_sober_launch(snapshot, proc_root=root)
    (launcher / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-replacement.scope\n', encoding='utf-8'
    )
    assert not replay.replay_sober_launch(snapshot, proc_root=root)


def test_sober_capture_rejects_launcher_from_a_different_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / 'proc'
    root.mkdir()
    launcher = _launcher(root, pid=321)
    def current_scope(*, proc_root: Path) -> str:
        assert proc_root == root
        return _SCOPE

    monkeypatch.setattr(replay, 'sober_flatpak_session_identity', current_scope)
    (launcher / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-other.scope\n'
    )
    assert replay.capture_sober_launch(proc_root=root) is None
