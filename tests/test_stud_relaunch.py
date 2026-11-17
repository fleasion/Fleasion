"""Stud's existing Env lifecycle relaunch callback and verified shutdown tests."""

from __future__ import annotations

import signal
import subprocess
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import platform_linux, stud_relaunch
from fleasion.utils.linux_clients import ROSEMARY_CLIENT, LinuxClientInstallation, RosemarySession

if TYPE_CHECKING:
    import pytest

PROXY = 'http://127.0.0.1:58443'


def _installed(home: Path) -> LinuxClientInstallation:
    bundle = home / '.local/share/stud/client/version-example/RobloxPlayer.app'
    executable = bundle / 'Contents/MacOS/RobloxPlayer'
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b'macOS')
    cert = bundle / 'Contents/Resources/ssl/cacert.pem'
    cert.parent.mkdir(parents=True)
    cert.write_text('test')
    command = home / 'bin/stud'
    command.parent.mkdir(parents=True)
    command.write_bytes(b'stud')
    command.chmod(0o755)
    return LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=home),
        executable=None, app_bundle=bundle, stud_managed=True,
        stud_command=(str(command),),
    )


def _stub_running(monkeypatch: pytest.MonkeyPatch, *, session: RosemarySession | None) -> None:
    def running(*, proc_root: Path) -> tuple[RosemarySession, ...]:
        del proc_root
        return (session,) if session is not None else ()

    monkeypatch.setattr(platform_linux, 'running_rosemary_players', running)


def test_stud_lifecycle_relaunch_stops_and_opens_the_launcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(tmp_path)
    assert installation.app_bundle is not None
    session = RosemarySession(123, installation.app_bundle, tmp_path / 'rosemary', 45)
    _stub_running(monkeypatch, session=session)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    calls: list[str] = []

    def stopped(_session: RosemarySession, *, prefix: Path, cancel: threading.Event,
                launcher_command: tuple[str, ...], proc_root: Path) -> bool:
        assert launcher_command == installation.stud_command
        assert _session == session
        assert prefix == tmp_path / '.local/share/stud/prefix'
        assert proc_root == platform_linux.PROC_ROOT
        assert not cancel.is_set()
        calls.append('shutdown')
        return True

    def launched(_installation: LinuxClientInstallation, command: list[str]) -> None:
        assert _installation is installation
        assert command == [str(tmp_path / 'bin/stud'), '--roblox']
        calls.append('launch')

    monkeypatch.setattr(stud_relaunch, 'stop_stud_session', stopped)
    monkeypatch.setattr(platform_linux, '_launch_rosemary_command', launched)
    assert platform_linux.relaunch_stud_with_proxy_env(
        PROXY, None, threading.Event(),
        installation.app_bundle / 'Contents/MacOS/RobloxPlayer', False,
    )
    assert calls == ['shutdown', 'launch']


def test_stud_relaunch_refuses_shutdown_failure_or_wrong_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(tmp_path)
    assert installation.app_bundle is not None
    session = RosemarySession(123, installation.app_bundle, tmp_path / 'rosemary', 45)
    _stub_running(monkeypatch, session=session)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    launched: list[list[str]] = []

    def refused(_session: RosemarySession, *, prefix: Path, cancel: threading.Event,
                launcher_command: tuple[str, ...], proc_root: Path) -> bool:
        del _session, prefix, cancel, launcher_command, proc_root
        return False

    def launched_stub(_installation: LinuxClientInstallation, command: list[str]) -> None:
        del _installation
        launched.append(command)

    monkeypatch.setattr(stud_relaunch, 'stop_stud_session', refused)
    monkeypatch.setattr(platform_linux, '_launch_rosemary_command', launched_stub)
    assert not platform_linux.relaunch_stud_with_proxy_env(PROXY, None, threading.Event(), None, False)
    assert not platform_linux.relaunch_stud_with_proxy_env(
        PROXY, None, threading.Event(), tmp_path / 'wrong.exe', False,
    )
    interrupted = threading.Event()
    interrupted.set()
    assert not platform_linux.relaunch_stud_with_proxy_env(PROXY, None, interrupted, None, False)
    assert not launched


def test_stud_relaunch_respects_already_stopped_and_forwards_deep_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(tmp_path)
    _stub_running(monkeypatch, session=None)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    command_seen: list[list[str]] = []

    def launched_stub(_installation: LinuxClientInstallation, command: list[str]) -> None:
        del _installation
        command_seen.append(command)

    monkeypatch.setattr(platform_linux, '_launch_rosemary_command', launched_stub)
    assert platform_linux.relaunch_stud_with_proxy_env(
        PROXY, 'roblox-player:synthetic', threading.Event(), None, True,
    )
    assert command_seen == [[str(tmp_path / 'bin/stud'), 'roblox-player:synthetic']]


def test_stud_shutdown_validates_supervisor_before_signaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RosemarySession(501, tmp_path / 'RobloxPlayer.app', tmp_path / 'rosemary', 123)
    calls: list[object] = []

    def missing(_session: RosemarySession, *, proc_root: Path = Path('/proc')) -> None:
        del _session, proc_root
        return None

    def dont_signal(_pid: int, flags: int) -> int:
        del flags
        calls.append('pidfd')
        return _pid

    monkeypatch.setattr(stud_relaunch, 'stud_launcher_pid', missing)
    monkeypatch.setattr(stud_relaunch.os, 'pidfd_open', dont_signal)
    assert not stud_relaunch.stop_stud_session(
        session, prefix=tmp_path / 'prefix', cancel=threading.Event(),
    )
    assert not calls


def test_stud_shutdown_signals_parent_before_child_and_stops_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RosemarySession(501, tmp_path / 'RobloxPlayer.app', tmp_path / 'rosemary', 123)
    calls: list[object] = []

    def parent(_session: RosemarySession, *, proc_root: Path = Path('/proc')) -> int:
        del _session, proc_root
        return 444

    def pidfd(pid: int, flags: int) -> int:
        assert flags == 0
        return pid

    def current(_proc: Path) -> RosemarySession:
        return session

    def send(fd: int, sig: int) -> None:
        calls.append((fd, sig))

    def readable(fds: list[int], _writable: list[object], _errors: list[object], _seconds: float) -> tuple[list[int], list[object], list[object]]:
        if fds[0] == 501:
            return ([], [], []) if len(calls) < 2 else ([501], [], [])
        return fds, [], []

    def server(cmd: list[str], *, env: dict[str, str], capture_output: bool, timeout: float, check: bool) -> subprocess.CompletedProcess[bytes]:
        assert cmd == [str(session.launcher), 'server', '--stop']
        assert env['ROSEMARY_PREFIX'] == str(tmp_path / 'prefix')
        assert capture_output and timeout == 5 and not check
        calls.append('server')
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(stud_relaunch, 'stud_launcher_pid', parent)
    monkeypatch.setattr(stud_relaunch, 'read_rosemary_session', current)
    monkeypatch.setattr(stud_relaunch.os, 'pidfd_open', pidfd)
    def close_fd(_fd: int) -> None:
        return None

    monkeypatch.setattr(stud_relaunch.os, 'close', close_fd)
    monkeypatch.setattr(stud_relaunch.signal, 'pidfd_send_signal', send, raising=False)
    monkeypatch.setattr(stud_relaunch.select, 'select', readable)
    monkeypatch.setattr(stud_relaunch.subprocess, 'run', server)
    assert stud_relaunch.stop_stud_session(session, prefix=tmp_path / 'prefix', cancel=threading.Event())
    assert calls[0] == (444, signal.SIGTERM)
    assert calls[-1] == 'server'


def test_stud_launcher_pid_requires_verified_parent_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    proc = tmp_path / 'proc'
    player = proc / '501'
    wrapper = proc / '445'
    supervisor = proc / '444'
    for entry in (player, wrapper, supervisor):
        entry.mkdir(parents=True)
    stud_bin = tmp_path / 'bin/stud'
    stud_bin.parent.mkdir()
    stud_bin.write_bytes(b'Binary')
    gamescope_bin = tmp_path / 'bin/gamescope'
    gamescope_bin.write_bytes(b'Binary')
    (supervisor / 'exe').symlink_to(stud_bin)
    (wrapper / 'exe').symlink_to(gamescope_bin)
    (supervisor / 'cmdline').write_bytes(str(stud_bin).encode() + b'\0--roblox\0')
    (wrapper / 'cmdline').write_bytes(str(gamescope_bin).encode() + b'\0--\0')
    (player / 'stat').write_text('501 (Main) S 445 0 0 0 0 0')
    (wrapper / 'stat').write_text('445 (gamescope) S 444 0 0 0 0 0')
    session = RosemarySession(501, tmp_path / 'RobloxPlayer.app', tmp_path / 'rosemary', 100)

    def verified(_proc: Path) -> RosemarySession:
        return session

    monkeypatch.setattr(stud_relaunch, 'read_rosemary_session', verified)
    assert stud_relaunch.stud_launcher_pid(session, proc_root=proc) == 444

    (supervisor / 'cmdline').write_bytes(b'/usr/bin/python3\0')
    assert stud_relaunch.stud_launcher_pid(session, proc_root=proc) is None
    (supervisor / 'cmdline').write_bytes(str(stud_bin).encode() + b'\0--roblox\0')
    (supervisor / 'exe').unlink()
    (supervisor / 'exe').symlink_to(gamescope_bin)
    assert stud_relaunch.stud_launcher_pid(session, proc_root=proc) is None


def test_stud_flatpak_shutdown_uses_sandboxed_rosemary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = RosemarySession(501, tmp_path / 'RobloxPlayer.app', tmp_path / 'rosemary', 100)
    commands: list[list[str]] = []

    def parent(_session: RosemarySession, *, proc_root: Path) -> int:
        return 444

    def pidfd(pid: int, flags: int) -> int:
        assert flags == 0
        return pid

    def current(_proc: Path) -> RosemarySession:
        return session

    def ready(fds: list[int], _writable: list[object], _errors: list[object], _seconds: float) -> tuple[list[int], list[object], list[object]]:
        return fds, [], []

    def server(command: list[str], *, env: dict[str, str], capture_output: bool,
               timeout: float, check: bool) -> subprocess.CompletedProcess[bytes]:
        assert env['ROSEMARY_PREFIX'] == str(tmp_path / 'prefix')
        assert capture_output and timeout == 5 and not check
        commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    def close_fd(_fd: int) -> None:
        return None

    def signalled(_fd: int, _signal: int) -> None:
        return None

    monkeypatch.setattr(stud_relaunch, 'stud_launcher_pid', parent)
    monkeypatch.setattr(stud_relaunch, 'read_rosemary_session', current)
    monkeypatch.setattr(stud_relaunch.os, 'pidfd_open', pidfd)
    monkeypatch.setattr(stud_relaunch.os, 'close', close_fd)
    monkeypatch.setattr(stud_relaunch.signal, 'pidfd_send_signal', signalled, raising=False)
    monkeypatch.setattr(stud_relaunch.select, 'select', ready)
    monkeypatch.setattr(stud_relaunch.subprocess, 'run', server)
    assert stud_relaunch.stop_stud_session(
        session, prefix=tmp_path / 'prefix', cancel=threading.Event(),
        launcher_command=('/usr/bin/flatpak', 'run', 'io.github.catpieleaf.Stud'),
    )
    assert commands == [[
        '/usr/bin/flatpak', 'run', '--command=/app/libexec/stud/rosemary',
        f'--env=ROSEMARY_PREFIX={tmp_path / "prefix"}',
        'io.github.catpieleaf.Stud', 'server', '--stop',
    ]]


def test_external_stud_relaunch_preserves_one_use_join_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _installed(tmp_path)
    assert installation.app_bundle is not None
    session = RosemarySession(123, installation.app_bundle, tmp_path / 'rosemary', 45)
    proc = tmp_path / 'proc'
    proc.mkdir()
    process = proc / '123'
    process.mkdir()
    target = 'roblox-player:1+gameinfo:single-use-token'
    command = [str(session.launcher), 'run', str(installation.app_bundle / 'Contents/MacOS/RobloxPlayer'), '-protocolString', target]
    (process / 'cmdline').write_bytes(b'\0'.join(item.encode() for item in command) + b'\0')

    def original(_proc: Path) -> RosemarySession:
        return session

    monkeypatch.setattr(stud_relaunch, 'read_rosemary_session', original)
    assert stud_relaunch.stud_player_launch_uri(session, proc_root=proc) == target
    (process / 'cmdline').write_bytes(b'\0'.join(item.encode() for item in command[:-1]) + b'\0')
    assert stud_relaunch.stud_player_launch_uri(session, proc_root=proc) is None
    (process / 'cmdline').write_bytes(b'\0'.join(item.encode() for item in [*command[:-1], 'roblox-studio:test']) + b'\0')
    assert stud_relaunch.stud_player_launch_uri(session, proc_root=proc) is None
    (process / 'cmdline').write_bytes(b'\0'.join(item.encode() for item in command) + b'\0')
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    def running_sessions(*, proc_root: Path) -> tuple[RosemarySession, ...]:
        del proc_root
        return (session,)

    monkeypatch.setattr(platform_linux, 'running_rosemary_players', running_sessions)
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', proc)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    launches: list[list[str]] = []

    def finished(_session: RosemarySession, **_kwargs: object) -> bool:
        return True

    def start(_installation: LinuxClientInstallation, argv: list[str]) -> None:
        launches.append(argv)

    monkeypatch.setattr(stud_relaunch, 'stop_stud_session', finished)
    monkeypatch.setattr(platform_linux, '_launch_rosemary_command', start)
    assert platform_linux.relaunch_stud_with_proxy_env(PROXY, None, threading.Event(), None, False)
    assert launches == [[str(tmp_path / 'bin/stud'), target]]
