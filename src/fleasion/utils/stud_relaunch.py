"""Verified Stud launcher shutdown, leaving session relaunch to its public CLI."""

from __future__ import annotations

import os
import select
import signal
import subprocess
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING

from .linux_clients import RosemarySession, read_rosemary_session
from .logging import log_buffer
from .stud_linux import STUD_APP_ID

if TYPE_CHECKING:
    import threading

_STUD_NAMES = frozenset({'stud', 'stud-ui'})
_MAX_ANCESTORS = 8


def _parent_pid(proc: Path) -> int | None:
    try:
        fields = (proc / 'stat').read_text(encoding='utf-8').rsplit(')', 1)[1].split()
        return int(fields[1]) if len(fields) > 1 else None
    except OSError, IndexError, ValueError:
        return None


def stud_launcher_pid(  # ruff: ignore[too-many-return-statements]
    session: RosemarySession, *, proc_root: Path = Path('/proc')
) -> int | None:
    """Locate the owning Stud process through a verified Player ancestry chain."""
    if read_rosemary_session(proc_root / str(session.pid)) != session:
        return None
    pid = session.pid
    visited = {pid}
    for _ in range(_MAX_ANCESTORS):
        parent = _parent_pid(proc_root / str(pid))
        if parent is None or parent <= 1 or parent in visited:
            return None
        visited.add(parent)
        proc = proc_root / str(parent)
        try:
            if proc.stat().st_uid != os.getuid():
                return None
            exe = (proc / 'exe').resolve(strict=True)
            command = (proc / 'cmdline').read_bytes().split(b'\0')
            name = exe.name
        except OSError:
            return None
        if name in _STUD_NAMES:
            if command and Path(command[0].decode('utf-8', errors='replace')).name in _STUD_NAMES:
                return parent
            return None
        pid = parent
    return None


def stop_stud_session(
    session: RosemarySession,
    *,
    prefix: Path,
    cancel: threading.Event,
    launcher_command: tuple[str, ...] = (),
    proc_root: Path = Path('/proc'),
) -> bool:
    """Stop the verified Stud supervisor first, then its Rosemary guest and services."""
    if proc_root != Path('/proc') or cancel.is_set():
        return False
    parent = stud_launcher_pid(session, proc_root=proc_root)
    if parent is None or not prefix.is_absolute():
        log_buffer.log('Launcher', 'Stud relaunch refused: owning Stud process not verified')
        return False
    try:
        with ExitStack() as cleanup:
            parent_fd = os.pidfd_open(parent, 0)
            cleanup.callback(os.close, parent_fd)
            player_fd = os.pidfd_open(session.pid, 0)
            cleanup.callback(os.close, player_fd)
            return _stop_verified_stud_session(
                session,
                prefix,
                cancel,
                parent=parent,
                parent_fd=parent_fd,
                player_fd=player_fd,
                proc_root=proc_root,
                launcher_command=launcher_command,
            )
    except (AttributeError, OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Launcher', f'Stud shutdown failed: {type(exc).__name__}: {exc}')
        return False


def _stop_verified_stud_session(
    session: RosemarySession,
    prefix: Path,
    cancel: threading.Event,
    *,
    parent: int,
    parent_fd: int,
    player_fd: int,
    proc_root: Path,
    launcher_command: tuple[str, ...],
) -> bool:
    """Stop only live, validated identities using the already-open pidfds."""
    if (
        cancel.is_set()
        or stud_launcher_pid(session, proc_root=proc_root) != parent
        or read_rosemary_session(proc_root / str(session.pid)) != session
        or not hasattr(signal, 'pidfd_send_signal')
    ):
        return False
    # Stop the Stud supervisor before its child, avoiding the crash dialog
    signal.pidfd_send_signal(parent_fd, signal.SIGTERM)
    if not select.select([parent_fd], [], [], 4.0)[0]:
        log_buffer.log('Launcher', 'Stud supervisor did not exit; relaunch aborted')
        return False
    if not select.select([player_fd], [], [], 1.5)[0]:
        signal.pidfd_send_signal(player_fd, signal.SIGTERM)
        if not select.select([player_fd], [], [], 2.5)[0]:
            signal.pidfd_send_signal(player_fd, signal.SIGKILL)
            if not select.select([player_fd], [], [], 2.0)[0]:
                log_buffer.log('Launcher', 'Stud Player did not exit; relaunch aborted')
                return False
    if cancel.is_set():
        return False
    # Stud normally asks Rosemary's process server to stop on session exit
    flatpak_executable = launcher_command[0] if launcher_command else None
    is_flatpak = (
        len(launcher_command) >= 3
        and Path(launcher_command[0]).name == 'flatpak'
        and launcher_command[1:3] == ('run', STUD_APP_ID)
    )
    command = (
        [
            flatpak_executable or 'flatpak',
            'run',
            '--command=/app/libexec/stud/rosemary',
            f'--env=ROSEMARY_PREFIX={prefix}',
            STUD_APP_ID,
            'server',
            '--stop',
        ]
        if is_flatpak
        else [str(session.launcher), 'server', '--stop']
    )
    result = subprocess.run(
        command,
        env={**os.environ, 'ROSEMARY_PREFIX': str(prefix)},
        capture_output=True,
        timeout=5,
        check=False,
    )
    if result.returncode != 0:
        log_buffer.log('Launcher', 'Stud prefix services could not be stopped')
        return False
    return True


def stud_player_launch_uri(  # ruff: ignore[too-many-return-statements]
    session: RosemarySession, *, proc_root: Path = Path('/proc')
) -> str | None:
    """Recover a live Player's exact one-time URI for immediate Stud relaunch."""
    proc = proc_root / str(session.pid)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if proc.stat().st_uid != os.getuid() or read_rosemary_session(proc) != session:
            return None
        with (proc / 'cmdline').open('rb') as stream:
            argv = stream.read(32769)
        if len(argv) > 32768 or not argv.endswith(b'\0'):
            return None
        tokens = argv.split(b'\0')[:-1]
        if len(tokens) < 5 or tokens[0].decode('utf-8', 'strict') != str(session.launcher):
            return None
        if tokens.count(b'-protocolString') != 1:
            return None
        index = tokens.index(b'-protocolString')
        if index + 1 >= len(tokens):
            return None
        target = tokens[index + 1].decode('utf-8', 'strict')
    except OSError, UnicodeError, ValueError:
        return None
    # Never replay arbitrary command-line arguments or Studio operations
    if len(target) <= 16384 and target.startswith(('roblox:', 'roblox-player:')):
        return target
    return None
