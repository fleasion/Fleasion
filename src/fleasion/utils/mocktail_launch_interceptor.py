"""Intercept and replay new native Mocktail Player launches with a scoped proxy.

The executable is identified by /proc/exe rather than a substring match,
so updater, webview, and error-dialog helper processes are never targeted.
"""

from __future__ import annotations

import os
import select
import signal
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .linux_clients import MocktailSession, read_mocktail_session, running_mocktail_players
from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable

_POLL_SECONDS = 0.12
_MAX_CMDLINE = 128 * 1024
_MAX_ENVIRONMENT = 512 * 1024
_REPLAY_MARKER = 'FLEASION_MOCKTAIL_ENV_REPLAY'


@dataclass(frozen=True, slots=True)
class MocktailLaunchSnapshot:
    """Original native Mocktail launch arguments and process environment."""

    session: MocktailSession
    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


def capture_mocktail_launch(  # ruff: ignore[too-many-return-statements]
    session: MocktailSession, *, proc_root: Path = Path('/proc')
) -> MocktailLaunchSnapshot | None:
    """Capture an unchanged, user-owned Mocktail binary and launch inputs."""
    proc = proc_root / str(session.pid)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if read_mocktail_session(proc) != session or proc.stat().st_uid != os.getuid():
            return None
        executable = (proc / 'exe').resolve(strict=True)
        if not executable.is_file() or not os.access(executable, os.X_OK):
            return None
        argv_bytes = (proc / 'cmdline').read_bytes()
        env_bytes = (proc / 'environ').read_bytes()
        if not (
            argv_bytes.endswith(b'\0')
            and env_bytes.endswith(b'\0')
            and 0 < len(argv_bytes) <= _MAX_CMDLINE
            and 0 < len(env_bytes) <= _MAX_ENVIRONMENT
        ):
            return None
        argv = tuple(os.fsdecode(item) for item in argv_bytes.split(b'\0') if item)
        if not argv or Path(argv[0]).name != 'mocktail':
            return None
        variables: dict[str, str] = {}
        for item in env_bytes.split(b'\0'):
            if not item:
                continue
            name, separator, value = item.partition(b'=')
            if not separator or not name:
                return None
            key = os.fsdecode(name)
            if key in variables:
                return None
            variables[key] = os.fsdecode(value)
        cwd = (proc / 'cwd').readlink()
        if not cwd.is_dir():
            return None
    except (OSError, ValueError, UnicodeError):
        return None
    return MocktailLaunchSnapshot(
        session=session, executable=executable, argv=argv, cwd=cwd, env=variables
    )


def _mocktail_child_pids(
    snapshot: MocktailLaunchSnapshot, *, proc_root: Path
) -> tuple[int, ...]:
    """Find only exact-binary children owned by the original engine."""
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return ()
    pids: list[int] = []
    for entry in entries:
        if not entry.name.isdecimal() or int(entry.name) == snapshot.session.pid:
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if entry.stat().st_uid != os.getuid():
                continue
            fields = (entry / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[1]) != snapshot.session.pid:
                continue
            if (entry / 'comm').read_text(encoding='utf-8').strip() != 'mocktail':
                continue
            if not (entry / 'exe').samefile(snapshot.executable):
                continue
            pids.append(int(entry.name))
        except (OSError, IndexError, ValueError):
            continue
    return tuple(sorted(pids))


def _wait_pid_exit(fd: int, timeout_ms: int) -> bool:
    watcher = select.poll()
    watcher.register(fd, select.POLLIN)
    return bool(watcher.poll(timeout_ms))


def _stop_mocktail_children(
    snapshot: MocktailLaunchSnapshot, *, proc_root: Path
) -> bool:
    """Close lock-holding Mocktail children before replacing their parent."""
    for child_pid in _mocktail_child_pids(snapshot, proc_root=proc_root):
        try:
            child_fd = os.pidfd_open(child_pid, 0)
        except OSError:
            return False
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            # Recheck after obtaining a pidfd; never signal a reused PID.
            if child_pid not in _mocktail_child_pids(snapshot, proc_root=proc_root):
                continue
            signal.pidfd_send_signal(child_fd, signal.SIGTERM)
            if not _wait_pid_exit(child_fd, 300):
                signal.pidfd_send_signal(child_fd, signal.SIGKILL)
                if not _wait_pid_exit(child_fd, 300):
                    return False
        except OSError:
            return False
        finally:
            os.close(child_fd)
    return True


def replay_mocktail_launch(  # ruff: ignore[too-many-return-statements]
    snapshot: MocktailLaunchSnapshot,
    *,
    proxy_url: str,
    proxy_environment: dict[str, str],
    proc_root: Path = Path('/proc'),
) -> bool:
    """Kill only the verified native Mocktail PID, then replay its launch."""
    if not proxy_url.startswith('http://127.0.0.1:'):
        return False
    session = snapshot.session
    proc = proc_root / str(session.pid)
    try:
        fd = os.pidfd_open(session.pid, 0)
    except (AttributeError, OSError):
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if (
            read_mocktail_session(proc, executable=snapshot.executable) != session
            or proc.stat().st_uid != os.getuid()
        ):
            return False
        if not hasattr(signal, 'pidfd_send_signal'):
            return False
        if not _stop_mocktail_children(snapshot, proc_root=proc_root):
            log_buffer.log('Launcher', 'Mocktail replay skipped: original child lock did not close')
            return False
        environment = {
            **snapshot.env,
            **proxy_environment,
            _REPLAY_MARKER: '1',
        }
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        if not _wait_pid_exit(fd, 400):
            log_buffer.log('Launcher', 'Mocktail replay skipped: original engine did not exit')
            return False
        subprocess.Popen(
            list(snapshot.argv),
            executable=str(snapshot.executable),
            env=environment,
            cwd=snapshot.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log(
            'Launcher',
            f'Could not replay intercepted Mocktail Player: {type(exc).__name__}: {exc}',
        )
        return False
    finally:
        os.close(fd)
    log_buffer.log(
        'Launcher',
        'Replayed native Mocktail Player preserving command line and working directory '
        'with Fleasion proxy variables',
    )
    return True


class MocktailLaunchInterceptor:
    """Monitor only newly started Mocktail Player processes."""

    def __init__(
        self,
        *,
        is_armed: Callable[[], bool],
        proxy_url: Callable[[], str],
        proxy_environment: Callable[[], dict[str, str]],
        on_replay: Callable[[], None] | None = None,
        proc_root: Path = Path('/proc'),
    ) -> None:
        self._is_armed = is_armed
        self._proxy_url = proxy_url
        self._proxy_environment = proxy_environment
        self._on_replay = on_replay
        self._proc_root = proc_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen = {
            (session.pid, session.start_ticks)
            for session in running_mocktail_players(proc_root=proc_root)
        }

    def _poll_once(self) -> None:
        sessions = running_mocktail_players(proc_root=self._proc_root)
        new = [
            session for session in sessions
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
            snapshot = capture_mocktail_launch(session, proc_root=self._proc_root)
            if snapshot is None or snapshot.env.get(_REPLAY_MARKER) == '1':
                continue
            if any(
                snapshot.env.get(name) == url
                for name in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')
            ):
                continue
            if self._on_replay is not None:
                self._on_replay()
            replay_mocktail_launch(
                snapshot,
                proxy_url=url,
                proxy_environment=self._proxy_environment(),
                proc_root=self._proc_root,
            )

    def _run(self) -> None:
        while not self._stop.wait(_POLL_SECONDS):
            try:
                self._poll_once()
            except (OSError, ValueError, RuntimeError) as exc:
                log_buffer.log(
                    'Launcher',
                    f'Mocktail watcher failed: {type(exc).__name__}: {exc}',
                )

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name='FleasionMocktailLaunchInterceptor', daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
