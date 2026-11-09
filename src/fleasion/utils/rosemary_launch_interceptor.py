"""Intercept unproxied direct Rosemary Player launches without touching Nutmeg.

Nutmeg-launched Player processes are never interrupted or restarted.
"""

from __future__ import annotations

import os
import signal
import stat
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .linux_clients import RosemarySession, read_rosemary_session, running_rosemary_players
from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable

_MAX_CMDLINE_BYTES = 128 * 1024
_MAX_ENV_BYTES = 512 * 1024
_POLL_SECONDS = 0.12
_INACTIVE_POLL_SECONDS = 0.5
_REPLAY_MARKER = 'FLEASION_ROSEMARY_ENV_REPLAY'


@dataclass(frozen=True, slots=True)
class RosemaryLaunchSnapshot:
    """Verified original argv, working directory and environment."""

    session: RosemarySession
    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: Path
    stdout_log: Path | None = None


def _decode_nul_items(raw: bytes) -> list[str] | None:
    if not raw.endswith(b'\0'):
        return None
    try:
        return [item.decode('utf-8') for item in raw.split(b'\0') if item]
    except UnicodeDecodeError:
        return None


def _nutmeg_stdout_log(proc: Path, home: Path) -> Path | None:
    """Preserve Nutmeg's log output if it was originally a safe regular file."""
    try:
        link = (proc / 'fd' / '1').readlink()
        path = Path(link)
        root = home / '.local/state/nutmeg/logs'
        metadata = path.lstat()
        if (
            not path.is_relative_to(root)
            or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or metadata.st_nlink != 1
            or path.is_symlink()
        ):
            return None
    except (OSError, ValueError):
        return None
    else:
        return path


def _launched_by_nutmeg(proc: Path) -> bool:
    """Recognize Nutmeg's direct child even when its log path is customized."""
    try:
        status = (proc / 'status').read_text(encoding='utf-8')
        parent = next(
            int(line.split(':', 1)[1].strip())
            for line in status.splitlines()
            if line.startswith('PPid:')
        )
        command = (proc.parent / str(parent) / 'cmdline').read_bytes().split(b'\0')
        names = [Path(part.decode('utf-8', errors='replace')).name for part in command if part]
    except (OSError, ValueError, StopIteration):
        return False
    return 'nutmeg' in names or any(
        names[i:i + 2] == ['-m', 'nutmeg'] for i in range(len(names) - 1)
    )


def capture_rosemary_launch(  # ruff: ignore[too-many-return-statements]
    session: RosemarySession,
    *,
    proc_root: Path = Path('/proc'),
    home: Path | None = None,
) -> RosemaryLaunchSnapshot | None:
    """Read exact process launch data; reject incomplete or mismatched captures."""
    proc = proc_root / str(session.pid)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if proc.stat().st_uid != os.getuid():
            return None
        if read_rosemary_session(proc) != session:
            return None
        cmdline = (proc / 'cmdline').read_bytes()
        environment = (proc / 'environ').read_bytes()
        if not (
            0 < len(cmdline) <= _MAX_CMDLINE_BYTES
            and 0 < len(environment) <= _MAX_ENV_BYTES
        ):
            return None
        argv = _decode_nul_items(cmdline)
        variables = _decode_nul_items(environment)
        if argv is None or variables is None or len(argv) < 3:
            return None
        if argv[0] != str(session.launcher) or argv[1] != 'run':
            return None
        if not session.launcher.is_file() or not os.access(session.launcher, os.X_OK):
            return None
        # The pathname and /proc/pid/exe must refer to the same executable,
        # not merely another program claiming to be named "rosemary".
        if proc_root == Path('/proc') and not (proc / 'exe').samefile(session.launcher):
            return None
        cwd = (proc / 'cwd').readlink()
        if not cwd.is_dir():
            return None
        env: dict[str, str] = {}
        for entry in variables:
            if '=' not in entry:
                return None
            name, value = entry.split('=', 1)
            if not name or name in env or '\0' in value:
                return None
            env[name] = value
    except (OSError, ValueError, PermissionError):
        return None
    owner_home = Path.home() if home is None else home
    nutmeg_log = _nutmeg_stdout_log(proc, owner_home) if proc_root == Path('/proc') else None
    if nutmeg_log is not None:
        # Roblox itself changes cwd to its app Resources during bootstrap.
        # Nutmeg originally starts rosemary at <prefix>/root; recover that
        # launch directory when we can positively identify Nutmeg's log.
        prefix = env.get('ROSEMARY_PREFIX', '')
        if prefix:
            nutmeg_cwd = Path(prefix) / 'root'
            if nutmeg_cwd.is_dir():
                cwd = nutmeg_cwd
    return RosemaryLaunchSnapshot(
        session=session,
        argv=tuple(argv),
        env=env,
        cwd=cwd,
        stdout_log=nutmeg_log,
    )


def replay_rosemary_launch(  # ruff: ignore[too-many-return-statements]
    snapshot: RosemaryLaunchSnapshot,
    *,
    proxy_url: str,
    proxy_environment: dict[str, str],
    proc_root: Path = Path('/proc'),
    stop: threading.Event | None = None,
) -> bool:
    """Stop exactly one unchanged Player PID, then replay its captured launch."""
    if (
        not proxy_url.startswith('http://127.0.0.1:')
        or snapshot.stdout_log is not None
        or _launched_by_nutmeg(proc_root / str(snapshot.session.pid))
    ):
        return False
    if stop is not None and stop.is_set():
        return False
    session = snapshot.session
    proc = proc_root / str(session.pid)
    try:
        fd = os.pidfd_open(session.pid, 0)
    except AttributeError, OSError:
        log_buffer.log('Launcher', 'Rosemary replay skipped: kernel pidfd unavailable')
        return False
    try:
        if read_rosemary_session(proc) != session or proc.stat().st_uid != os.getuid():
            return False
        if not hasattr(signal, 'pidfd_send_signal'):
            return False
        env = {**snapshot.env, **proxy_environment, _REPLAY_MARKER: '1'}
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        subprocess.Popen(
            list(snapshot.argv),
            executable=str(session.launcher),
            env=env,
            cwd=snapshot.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log(
            'Launcher',
            f'Rosemary intercepted launch could not be replayed: {type(exc).__name__}: {exc}',
        )
        return False
    finally:
        os.close(fd)
    log_buffer.log(
        'Launcher',
        'Replayed Rosemary Player launch with identical arguments, '
        'working directory and original environment plus Fleasion proxy routing',
    )
    return True


class RosemaryLaunchInterceptor:
    """Small non-Qt watcher for new Rosemary Player process generations."""

    def __init__(
        self,
        *,
        is_armed: Callable[[], bool],
        proxy_url: Callable[[], str],
        proxy_environment: Callable[[], dict[str, str]],
        proc_root: Path = Path('/proc'),
    ) -> None:
        self._is_armed = is_armed
        self._proxy_url = proxy_url
        self._proxy_environment = proxy_environment
        self._proc_root = proc_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # A Player open before Fleasion starts is never forcibly restarted.
        self._seen: set[tuple[int, int]] = {
            (session.pid, session.start_ticks)
            for session in running_rosemary_players(proc_root=self._proc_root)
        }

    def _poll_once(self) -> None:
        sessions = running_rosemary_players(proc_root=self._proc_root)
        new = [
            session
            for session in sessions
            if (session.pid, session.start_ticks) not in self._seen
        ]
        self._seen = {(session.pid, session.start_ticks) for session in sessions}
        if not new or not self._is_armed():
            return
        url = self._proxy_url()
        if not url:
            return
        for session in new:
            if self._stop.is_set():
                return
            snapshot = capture_rosemary_launch(session, proc_root=self._proc_root)
            if snapshot is None:
                continue
            if snapshot.stdout_log is not None or _launched_by_nutmeg(
                self._proc_root / str(session.pid)
            ):
                continue
            if snapshot.env.get(_REPLAY_MARKER) == '1':
                continue
            if snapshot.env.get('https_proxy') == url or snapshot.env.get('HTTPS_PROXY') == url:
                continue
            replay_rosemary_launch(
                snapshot,
                proxy_url=url,
                proxy_environment=self._proxy_environment(),
                proc_root=self._proc_root,
                stop=self._stop,
            )

    def _poll_when_armed(self, previously_armed: bool) -> bool:
        """Only scan the process table while Rosemary proxy routing is armed."""
        if not self._is_armed():
            return False
        if not previously_armed:
            # Existing Players must not be replayed after an Auto client switch
            self._seen = {
                (session.pid, session.start_ticks)
                for session in running_rosemary_players(proc_root=self._proc_root)
            }
            return True
        self._poll_once()
        return True

    def _run(self) -> None:
        armed = False
        while not self._stop.wait(_POLL_SECONDS if armed else _INACTIVE_POLL_SECONDS):
            try:
                armed = self._poll_when_armed(armed)
            except (OSError, ValueError, RuntimeError) as exc:
                log_buffer.log(
                    'Launcher',
                    f'Rosemary launch watcher error: {type(exc).__name__}: {exc}',
                )

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._run, name='FleasionRosemaryLaunchInterceptor', daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
