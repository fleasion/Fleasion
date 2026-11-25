"""Read-only fixture coverage for Rosemary's automatic Nutmeg launch replay."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from fleasion.utils import rosemary_launch_interceptor as interceptor
from fleasion.utils.linux_clients import RosemarySession

if TYPE_CHECKING:
    import pytest


def _fixture(tmp_path: Path, *, pid: int = 4321, suffix: str = 'test') -> tuple[Path, RosemarySession]:
    root = tmp_path / 'proc'
    process = root / str(pid)
    process.mkdir(parents=True, exist_ok=True)
    binary = tmp_path / 'bin/rosemary'
    binary.parent.mkdir(exist_ok=True)
    binary.write_text('stub')
    binary.chmod(0o700)
    bundle = tmp_path / 'nutmeg' / 'Roblox.app'
    executable = bundle / 'Contents/MacOS/RobloxPlayer'
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.write_text('Mach-O')
    ssl_cert = bundle / 'Contents/Resources/ssl/cacert.pem'
    ssl_cert.parent.mkdir(parents=True, exist_ok=True)
    ssl_cert.write_text('unmodified')
    argv = [
        str(binary), 'run', '--emulated', str(bundle),
        '-protocolString', f'roblox-player:synthetic-{suffix}',
    ]
    (process / 'cmdline').write_bytes(b'\0'.join(s.encode() for s in argv) + b'\0')
    environment = {
        'HOME': str(tmp_path),
        'ROSEMARY_PREFIX': str(tmp_path / 'nutmeg'),
        'SDL_VIDEO_DRIVER': 'x11',
        'CUSTOM_LAUNCH_VARIABLE': 'preserve',
    }
    (process / 'environ').write_bytes(
        b'\0'.join(f'{name}={value}'.encode() for name, value in environment.items()) + b'\0'
    )
    # /proc/PID/stat field 22 is element index 19 after the comm field.
    (process / 'stat').write_text(
        f'{pid} (Main) S ' + ' '.join(['0'] * 18 + ['456789'] + ['0'] * 2)
    )
    (process / 'cwd').symlink_to(tmp_path, target_is_directory=True)
    return root, RosemarySession(pid, bundle.resolve(), binary, 456789)


def test_capture_preserves_nutmeg_player_launch_with_uri_and_environment(tmp_path: Path) -> None:
    root, session = _fixture(tmp_path)
    snapshot = interceptor.capture_rosemary_launch(session, proc_root=root)
    assert snapshot is not None
    assert snapshot.argv[-1] == 'roblox-player:synthetic-test'
    assert snapshot.argv[:4] == (
        str(session.launcher), 'run', '--emulated', str(session.bundle),
    )
    assert snapshot.cwd == tmp_path
    assert snapshot.env['ROSEMARY_PREFIX'] == str(tmp_path / 'nutmeg')
    assert snapshot.env['SDL_VIDEO_DRIVER'] == 'x11'
    assert snapshot.env['CUSTOM_LAUNCH_VARIABLE'] == 'preserve'


def test_capture_rejects_modified_generation_and_missing_environment(tmp_path: Path) -> None:
    root, session = _fixture(tmp_path)
    wrong = RosemarySession(
        session.pid, session.bundle, session.launcher, session.start_ticks + 1
    )
    assert interceptor.capture_rosemary_launch(wrong, proc_root=root) is None
    (root / str(session.pid) / 'environ').unlink()
    assert interceptor.capture_rosemary_launch(session, proc_root=root) is None


def test_replay_stops_only_verified_original_pid_and_preserves_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, session = _fixture(tmp_path)
    snapshot = interceptor.capture_rosemary_launch(session, proc_root=root)
    assert snapshot is not None
    signals: list[tuple[int, int]] = []
    spawned: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(
        interceptor.os, 'pidfd_open', lambda pid, flags: os.open('/dev/null', os.O_RDONLY)
    )
    monkeypatch.setattr(
        interceptor.signal,
        'pidfd_send_signal',
        lambda fd, signum: signals.append((fd, signum)),
    )
    monkeypatch.setattr(
        interceptor.subprocess, 'Popen',
        lambda argv, **kwargs: (
            spawned.append((argv, kwargs)) or SimpleNamespace(pid=5678)
        ),
    )
    assert interceptor.replay_rosemary_launch(
        snapshot,
        proxy_url='http://127.0.0.1:58443',
        proxy_environment={
            'HTTP_PROXY': 'http://127.0.0.1:58443',
            'HTTPS_PROXY': 'http://127.0.0.1:58443',
            'https_proxy': 'http://127.0.0.1:58443',
        },
        proc_root=root,
    )
    assert len(signals) == 1
    assert len(spawned) == 1
    argv, kwargs = spawned[0]
    assert argv == list(snapshot.argv)
    assert kwargs['cwd'] == tmp_path
    env = kwargs['env']
    assert env['CUSTOM_LAUNCH_VARIABLE'] == 'preserve'
    assert env['ROSEMARY_PREFIX'] == str(tmp_path / 'nutmeg')
    assert env['https_proxy'] == 'http://127.0.0.1:58443'
    assert env['FLEASION_ROSEMARY_ENV_REPLAY'] == '1'

    (root / str(session.pid) / 'stat').write_text(
        f'{session.pid} (Main) S ' + ' '.join(['0'] * 18 + ['456790'] + ['0'] * 2)
    )
    assert not interceptor.replay_rosemary_launch(
        snapshot,
        proxy_url='http://127.0.0.1:58443',
        proxy_environment={},
        proc_root=root,
    )
    assert len(signals) == 1
    assert len(spawned) == 1


def test_watcher_does_not_replay_preexisting_or_already_proxied_processes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, session = _fixture(tmp_path)
    replays: list[RosemarySession] = []
    monkeypatch.setattr(
        interceptor, 'replay_rosemary_launch',
        lambda snapshot, **_kwargs: replays.append(snapshot.session) or True,
    )
    watcher = interceptor.RosemaryLaunchInterceptor(
        is_armed=lambda: True,
        proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    watcher._poll_once()
    assert not replays  # Never interrupt Player that was open before Fleasion.

    _fixture(tmp_path, pid=4322, suffix='new')
    watcher._poll_once()
    assert len(replays) == 1
    watcher._poll_once()
    assert len(replays) == 1

    _fixture(tmp_path, pid=4323, suffix='proxied')
    with (root / '4323' / 'environ').open('ab') as file:
        file.write(b'https_proxy=http://127.0.0.1:58443\0')
    watcher._poll_once()
    assert len(replays) == 1

    _fixture(tmp_path, pid=4324, suffix='replayed')
    with (root / '4324' / 'environ').open('ab') as file:
        file.write(b'FLEASION_ROSEMARY_ENV_REPLAY=1\0')
    watcher._poll_once()
    assert len(replays) == 1

    watcher.stop()


def test_watcher_only_replays_when_proxy_is_armed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _fixture(tmp_path)
    armed = False
    replays: list[RosemarySession] = []
    monkeypatch.setattr(
        interceptor, 'replay_rosemary_launch',
        lambda snapshot, **_kwargs: replays.append(snapshot.session) or True,
    )
    watcher = interceptor.RosemaryLaunchInterceptor(
        is_armed=lambda: armed,
        proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    _fixture(tmp_path, pid=4330, suffix='not-armed')
    watcher._poll_once()
    assert not replays
    armed = True
    _fixture(tmp_path, pid=4331, suffix='armed')
    watcher._poll_once()
    assert [s.pid for s in replays] == [4331]
    watcher.stop()



def test_nutmeg_launch_is_never_interrupted_or_replayed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    root, session = _fixture(tmp_path)
    original = interceptor.capture_rosemary_launch(session, proc_root=root)
    assert original is not None
    snapshot = replace(original, stdout_log=tmp_path / 'nutmeg.log')
    issued: list[int] = []
    spawned: list[object] = []

    def reject_pidfd(_pid: int, _flags: int) -> int:
        msg = 'pidfd forbidden'
        raise AssertionError(msg)

    monkeypatch.setattr(interceptor.os, 'pidfd_open', reject_pidfd)
    monkeypatch.setattr(
        interceptor.signal, 'pidfd_send_signal',
        lambda _fd, sig: issued.append(sig),
    )
    monkeypatch.setattr(
        interceptor.subprocess, 'Popen',
        lambda *args, **kwargs: spawned.append((args, kwargs)),
    )
    assert not interceptor.replay_rosemary_launch(
        snapshot, proxy_url='http://127.0.0.1:58443',
        proxy_environment={'https_proxy': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    assert not issued
    assert not spawned


def test_watcher_does_not_replay_nutmeg_even_without_proxy_variables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    root, _ = _fixture(tmp_path)
    replays: list[RosemarySession] = []
    original_capture = interceptor.capture_rosemary_launch

    def capture(session: RosemarySession, **kwargs: object) -> interceptor.RosemaryLaunchSnapshot | None:
        original = original_capture(session, **kwargs)
        return None if original is None else replace(original, stdout_log=tmp_path / 'nutmeg.log')

    monkeypatch.setattr(interceptor, 'capture_rosemary_launch', capture)
    monkeypatch.setattr(
        interceptor, 'replay_rosemary_launch',
        lambda snapshot, **_kwargs: replays.append(snapshot.session) or True,
    )
    watcher = interceptor.RosemaryLaunchInterceptor(
        is_armed=lambda: True,
        proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    _fixture(tmp_path, pid=4322, suffix='nutmeg')
    watcher._poll_once()
    assert not replays
    watcher.stop()


def test_replay_does_not_kill_if_cleanup_is_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    root, session = _fixture(tmp_path)
    snapshot = interceptor.capture_rosemary_launch(session, proc_root=root)
    assert snapshot is not None
    signals: list[int] = []
    monkeypatch.setattr(
        interceptor.signal, 'pidfd_send_signal',
        lambda _fd, sig: signals.append(sig),
    )
    stopped = threading.Event()
    stopped.set()
    assert not interceptor.replay_rosemary_launch(
        snapshot, proxy_url='http://127.0.0.1:58443',
        proxy_environment={'https_proxy': 'http://127.0.0.1:58443'},
        proc_root=root, stop=stopped,
    )
    assert not signals


def test_nutmeg_parent_process_is_not_replayed_when_stdout_is_customized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, session = _fixture(tmp_path, pid=7777)
    proc = root / str(session.pid)
    (proc / 'status').write_text('Name:\trosemary\nPPid:\t7776\n', encoding='utf-8')
    (root / '7776').mkdir()
    (root / '7776/cmdline').write_bytes(
        b'/home/test/python\0-E\0/home/test/.local/bin/nutmeg\0launch\0'
    )
    signals: list[int] = []

    def capture_signal(_fd: int, signal_number: int) -> None:
        signals.append(signal_number)

    monkeypatch.setattr(interceptor.signal, 'pidfd_send_signal', capture_signal)
    snapshot = interceptor.capture_rosemary_launch(session, proc_root=root)
    assert snapshot is not None
    assert snapshot.stdout_log is None
    assert not interceptor.replay_rosemary_launch(
        snapshot, proxy_url='http://127.0.0.1:58443',
        proxy_environment={'HTTPS_PROXY': 'http://127.0.0.1:58443'},
        proc_root=root,
    )
    assert not signals


def test_inactive_rosemary_watcher_skips_scans_and_rebases_on_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, _ = _fixture(tmp_path)
    state = {'armed': False}
    watcher = interceptor.RosemaryLaunchInterceptor(
        is_armed=lambda: state['armed'],
        proxy_url=lambda: 'http://127.0.0.1:58443',
        proxy_environment=lambda: {},
        proc_root=root,
    )
    scans: list[str] = []
    real_scan = interceptor.running_rosemary_players

    def scan(*, proc_root: Path) -> object:
        scans.append('scan')
        return real_scan(proc_root=proc_root)

    monkeypatch.setattr(interceptor, 'running_rosemary_players', scan)
    polls: list[str] = []
    monkeypatch.setattr(watcher, '_poll_once', lambda: polls.append('poll'))
    assert not watcher._poll_when_armed(False)
    assert scans == []
    state['armed'] = True
    assert watcher._poll_when_armed(False)
    assert scans == ['scan']
    assert polls == []
    assert watcher._poll_when_armed(True)
    assert polls == ['poll']
    state['armed'] = False
    assert not watcher._poll_when_armed(True)
    state['armed'] = True
    assert watcher._poll_when_armed(False)
    assert scans == ['scan', 'scan']
    assert polls == ['poll']
    watcher.stop()
