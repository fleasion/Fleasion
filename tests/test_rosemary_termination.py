"""Rosemary cache shutdown only signals verified Player process generations."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    ROSEMARY_CLIENT,
    LinuxClientInstallation,
    RosemarySession,
    rosemary_guest_home,
)

def _session_for_child(pid: int) -> RosemarySession:
    proc = Path('/proc') / str(pid)
    stat = (proc / 'stat').read_text()
    ticks = int(stat.rsplit(')', 1)[1].split()[19])
    return RosemarySession(
        pid,
        Path('/tmp/fleasion-unittest/Roblox.app'),
        Path('/usr/bin/rosemary'),
        ticks,
    )


def _child(*, ignore_term: bool) -> subprocess.Popen[str]:
    script = (
        'import signal,time; '
        + ('signal.signal(signal.SIGTERM, signal.SIG_IGN); ' if ignore_term else '')
        + 'print("ready", flush=True); time.sleep(60)'
    )
    return subprocess.Popen(
        [sys.executable, '-u', '-c', script],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )


@pytest.mark.parametrize('ignore_term', [True, False])
def test_rosemary_player_uses_pidfd_grace_then_escalation(
    monkeypatch: pytest.MonkeyPatch, ignore_term: bool,
) -> None:
    """Run against a disposable local process without touching installed Roblox."""
    if not hasattr(os, 'pidfd_open') or not hasattr(signal, 'pidfd_send_signal'):
        pytest.skip('Linux pidfd required')
    child = _child(ignore_term=ignore_term)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == 'ready'
        expected = _session_for_child(child.pid)
        def same_identity(_proc: Path) -> RosemarySession:
            return expected

        monkeypatch.setattr(platform_linux, 'read_rosemary_session', same_identity)
        assert platform_linux._terminate_verified_rosemary_player(expected)  # pyright: ignore[reportPrivateUsage]
        assert child.wait(timeout=3) == -(signal.SIGKILL if ignore_term else signal.SIGTERM)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        if child.stdout is not None:
            child.stdout.close()


def test_changed_player_identity_refuses_to_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrelated PID cannot be killed even if a previous Player owned it."""
    if not hasattr(os, 'pidfd_open'):
        pytest.skip('Linux pidfd required')
    child = _child(ignore_term=False)
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == 'ready'
        expected = _session_for_child(child.pid)
        def replaced_identity(_proc: Path) -> None:
            return None

        monkeypatch.setattr(platform_linux, 'read_rosemary_session', replaced_identity)
        assert not platform_linux._terminate_verified_rosemary_player(expected)  # pyright: ignore[reportPrivateUsage]
        assert child.poll() is None
    finally:
        if child.poll() is None:
            child.terminate()
            child.wait(timeout=3)
        if child.stdout is not None:
            child.stdout.close()


def test_termination_stops_only_selected_player_and_rechecks_replacements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        Path('/usr/bin/rosemary'), tmp_path / 'Roblox.app',
    )
    expected_bundle = installation.app_bundle
    assert expected_bundle is not None
    original = RosemarySession(1001, expected_bundle, Path('/usr/bin/rosemary'), 10)
    replacement = RosemarySession(1002, expected_bundle, Path('/usr/bin/rosemary'), 20)
    studio = RosemarySession(9001, tmp_path / 'RobloxStudio.app', Path('/usr/bin/rosemary'), 30)
    snapshots = iter([(original, studio), (replacement, studio), (studio,)])
    killed: list[RosemarySession] = []

    def live(*, proc_root: Path) -> tuple[RosemarySession, ...]:
        del proc_root
        return next(snapshots)

    def terminate_one(session: RosemarySession) -> bool:
        killed.append(session)
        return True

    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, 'running_rosemary_players', live)
    monkeypatch.setattr(platform_linux, '_terminate_verified_rosemary_player', terminate_one)
    assert platform_linux.terminate_roblox()
    assert killed == [original, replacement]


def test_termination_reports_failure_instead_of_deleting_live_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        Path('/usr/bin/rosemary'), tmp_path / 'Roblox.app',
    )
    assert installation.app_bundle is not None
    stubborn = RosemarySession(1001, installation.app_bundle, Path('/usr/bin/rosemary'), 10)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    def live(*, proc_root: Path) -> tuple[RosemarySession, ...]:
        del proc_root
        return (stubborn,)

    def refuse(_session: RosemarySession) -> bool:
        return False

    monkeypatch.setattr(platform_linux, 'running_rosemary_players', live)
    monkeypatch.setattr(platform_linux, '_terminate_verified_rosemary_player', refuse)
    assert not platform_linux.terminate_roblox()


def test_invalid_pidfd_cannot_fall_back_to_unsafe_pid_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = RosemarySession(9999999, Path('/tmp/Roblox.app'), Path('/usr/bin/rosemary'), 1)
    called: list[str] = []

    def bad_open(_pid: int, _flags: int) -> int:
        raise PermissionError('pidfd denied')

    def unexpected_signal(_pid: int, _sig: int) -> None:
        called.append('unsafe signal')

    monkeypatch.setattr(platform_linux.os, 'pidfd_open', bad_open)
    monkeypatch.setattr(platform_linux.os, 'kill', unexpected_signal)
    assert not platform_linux._terminate_verified_rosemary_player(fake)  # pyright: ignore[reportPrivateUsage]
    assert not called


def test_cache_deletion_refuses_unterminated_rosemary_player(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The database must remain intact if verified Player shutdown fails."""
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        Path('/usr/bin/rosemary'), tmp_path / 'Roblox.app',
    )
    for name in ('ROSEMARY_PREFIX', 'ROSEMARY_PREFIX_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    guest_home = rosemary_guest_home(installation)
    assert guest_home is not None
    storage = guest_home / 'Library' / 'Roblox'
    storage.mkdir(parents=True)
    database = storage / 'rbx-storage.db'
    database.write_bytes(b'cache-that-must-remain')
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation',
        lambda: installation,
    )

    def no_studios(*, proc_root: Path) -> tuple[int, ...]:
        del proc_root
        return ()

    def still_running(_installation: object = None) -> bool:
        return True

    def cannot_terminate(_installation: object = None) -> bool:
        return False

    def cannot_wait(
        _timeout: float = 10.0, *, installation: object = None
    ) -> bool:
        return False

    monkeypatch.setattr(platform_linux, 'running_rosemary_studios', no_studios)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', still_running)
    monkeypatch.setattr(platform_linux, 'terminate_roblox', cannot_terminate)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_exit', cannot_wait)
    messages = platform_linux.delete_cache()
    assert 'Rosemary (macOS Roblox) termination timed out' in messages
    assert 'Cache deletion aborted' in messages
    assert database.read_bytes() == b'cache-that-must-remain'
