"""Replay only new, native Cordial game engines with the active Env Proxy.

Cordial's shell can detach its engine and hold several profile windows. Replay
the exact verified cordial-run generation, never the shared GTK launcher.
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

from .linux_clients import MocktailSession, running_cordial_players
from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable

_POLL_SECONDS = 0.12
_INACTIVE_POLL_SECONDS = 0.5
_REPLAY_MARKER = 'FLEASION_CORDIAL_ENV_REPLAY'
_MAX_ENV = 512 * 1024
_MAX_ARGV = 128 * 1024


@dataclass(frozen=True, slots=True)
class CordialLaunchSnapshot:
    """Exact native Cordial engine launch, including its profile and join URL."""

    session: MocktailSession
    executable: Path
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


def cordial_engine_executable(shell_executable: Path | None) -> Path | None:
    """Accept only the loader installed beside this selected native shell."""
    if shell_executable is None or shell_executable.name != 'cordial-shell':
        return None
    engine = shell_executable.parent / 'cordial-run'
    try:
        if engine.is_file() and os.access(engine, os.X_OK):
            return engine
    except OSError:
        pass
    return None


def capture_cordial_launch(  # ruff: ignore[too-many-return-statements]
    session: MocktailSession, *, executable: Path, proc_root: Path = Path('/proc')
) -> CordialLaunchSnapshot | None:
    """Capture a user-owned, exact-binary Cordial engine after libroblox maps."""
    proc = proc_root / str(session.pid)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if proc.stat().st_uid != os.getuid() or session not in running_cordial_players(
            proc_root=proc_root, executable=executable
        ):
            return None
        # A Flatpak engine must never be restarted by the native interceptor
        cgroup = (proc / 'cgroup').read_text(encoding='utf-8', errors='replace')
        if 'app-flatpak-' in cgroup:
            return None
        original_executable = (proc / 'exe').resolve(strict=True)
        if not original_executable.is_file() or not os.access(original_executable, os.X_OK):
            return None
        argv_bytes = (proc / 'cmdline').read_bytes()
        environment = (proc / 'environ').read_bytes()
        if not (
            argv_bytes.endswith(b'\0')
            and environment.endswith(b'\0')
            and 0 < len(argv_bytes) <= _MAX_ARGV
            and 0 < len(environment) <= _MAX_ENV
        ):
            return None
        argv = tuple(os.fsdecode(arg) for arg in argv_bytes.split(b'\0') if arg)
        if not argv or Path(argv[0]).name != 'cordial-run' or '--profile' not in argv:
            return None
        variables: dict[str, str] = {}
        for item in environment.split(b'\0'):
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
        if not cwd.is_absolute() or not cwd.is_dir():
            return None
    except OSError, ValueError, UnicodeError:
        return None
    return CordialLaunchSnapshot(session, original_executable, argv, cwd, variables)


def _wait_pid_exit(fd: int, timeout_ms: int) -> bool:
    watcher = select.poll()
    watcher.register(fd, select.POLLIN)
    return bool(watcher.poll(timeout_ms))


def replay_cordial_launch(
    snapshot: CordialLaunchSnapshot,
    *,
    proxy_url: str,
    proxy_environment: dict[str, str],
    proc_root: Path = Path('/proc'),
) -> bool:
    """Terminate only the verified generation and replay its exact invocation."""
    if not proxy_url.startswith('http://127.0.0.1:'):
        return False
    try:
        fd = os.pidfd_open(snapshot.session.pid, 0)
    except AttributeError, OSError:
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        current = capture_cordial_launch(
            snapshot.session, executable=snapshot.executable, proc_root=proc_root
        )
        if current != snapshot or not hasattr(signal, 'pidfd_send_signal'):
            return False
        environment = {**snapshot.env, **proxy_environment, _REPLAY_MARKER: '1'}
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        if not _wait_pid_exit(fd, 700):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
            if not _wait_pid_exit(fd, 700):
                log_buffer.log('Launcher', 'Cordial replay aborted: original engine did not exit')
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
        log_buffer.log('Launcher', f'Cordial replay failed: {type(exc).__name__}: {exc}')
        return False
    finally:
        os.close(fd)
    log_buffer.log('Launcher', 'Replayed the original native Cordial engine with Env Proxy')
    return True


class CordialLaunchInterceptor:
    """Watch only new selected native Cordial engines, retaining profile args."""

    def __init__(
        self,
        *,
        is_armed: Callable[[], bool],
        proxy_url: Callable[[], str],
        proxy_environment: Callable[[], dict[str, str]],
        executable: Callable[[], Path | None],
        on_replay: Callable[[], None] | None = None,
        proc_root: Path = Path('/proc'),
    ) -> None:
        self._is_armed = is_armed
        self._proxy_url = proxy_url
        self._proxy_environment = proxy_environment
        self._executable = executable
        self._on_replay = on_replay
        self._proc_root = proc_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # Never replay a Cordial engine that existed before Fleasion
        self._seen = {(s.pid, s.start_ticks) for s in running_cordial_players(proc_root=proc_root)}

    def _poll_once(self) -> None:
        if not self._is_armed():
            return
        executable = self._executable()
        if executable is None:
            return
        sessions = running_cordial_players(proc_root=self._proc_root, executable=executable)
        new = [s for s in sessions if (s.pid, s.start_ticks) not in self._seen]
        self._seen.update((s.pid, s.start_ticks) for s in sessions)
        if not self._is_armed():
            return
        url = self._proxy_url()
        if not url:
            return
        for session in new:
            if self._stop.is_set():
                return
            snapshot = capture_cordial_launch(
                session, executable=executable, proc_root=self._proc_root
            )
            if snapshot is None or snapshot.env.get(_REPLAY_MARKER) == '1':
                continue
            if any(
                snapshot.env.get(name) == url
                for name in ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy')
            ):
                continue
            # Suppress exit cleanup before the termination, not after it
            if self._on_replay is not None:
                self._on_replay()
            replay_cordial_launch(
                snapshot,
                proxy_url=url,
                proxy_environment=self._proxy_environment(),
                proc_root=self._proc_root,
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                armed = self._is_armed()
                if self._stop.wait(_POLL_SECONDS if armed else _INACTIVE_POLL_SECONDS):
                    return
                if armed:
                    self._poll_once()
            except (OSError, RuntimeError, ValueError) as exc:
                log_buffer.log('Launcher', f'Cordial watcher failed: {type(exc).__name__}: {exc}')

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, name='FleasionCordialLaunchInterceptor', daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
