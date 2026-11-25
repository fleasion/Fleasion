"""Coordinate restart markers and transfer single-instance ownership."""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.wintypes
import os
import sys
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QSharedMemory
from PySide6.QtNetwork import QLocalServer

from fleasion.app.compatibility import RestartHandoffUncertain
from fleasion.app.process_control import (
    SINGLE_INSTANCE_CONTROL_SERVER as _SINGLE_INSTANCE_CONTROL_SERVER,
)
from fleasion.app.single_instance import (
    SINGLE_INSTANCE_KEY,
    single_instance_state,
    start_single_instance_control_server,
)
from fleasion.utils import (
    CONFIG_DIR,
    log_buffer,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


RESTART_HANDOFF_TIMEOUT_SECONDS = 45.0


RESTART_ABORT_TIMEOUT_SECONDS = 10.0


RESTART_HANDOFF_PHASES = frozenset({'prepared', 'release', 'ready', 'abort'})


def restart_handoff_path(token: str, phase: str = 'ready') -> Path | None:
    """Resolve a restart protocol marker without accepting arbitrary paths."""
    token = str(token or '')
    phase = str(phase or '')
    if phase not in RESTART_HANDOFF_PHASES:
        return None
    if len(token) != 32 or any(character not in '0123456789abcdef' for character in token):
        return None
    return CONFIG_DIR / f'.restart-{phase}-{token}'


def cleanup_restart_handoff(token: str, *, preserve_abort: bool = False) -> None:
    for phase in RESTART_HANDOFF_PHASES:
        if preserve_abort and phase == 'abort':
            continue
        marker = restart_handoff_path(token, phase)
        if marker is None:
            continue
        with contextlib.suppress(OSError):
            marker.unlink(missing_ok=True)


def write_restart_marker_file(marker: Path, value: int) -> None:
    marker.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write(str(value))
        handle.flush()
        os.fsync(handle.fileno())


def write_restart_handoff_marker(token: str, phase: str, value: int) -> bool:
    marker = restart_handoff_path(token, phase)
    if marker is None or value <= 0:
        log_buffer.log('Restart', 'Rejected invalid restart handoff marker')
        return False
    try:
        write_restart_marker_file(marker, value)
    except OSError as exc:
        log_buffer.log('Restart', f'Could not publish restart {phase} marker: {exc}')
        return False
    return True


def publish_restart_handoff(token: str) -> bool:
    """Publish final readiness from the replacement process."""
    return write_restart_handoff_marker(token, 'ready', os.getpid())


def unix_pid_is_alive(pid: int) -> bool:
    """Probe a Unix PID without requiring termination rights."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    else:
        return True


def pid_is_alive(pid: int) -> bool:
    """Return whether an application PID is alive without requiring termination rights."""
    if pid <= 0:
        return False
    if sys.platform != 'win32':
        return unix_pid_is_alive(pid)

    process_query_limited_information = 0x1000
    still_active = 259
    if TYPE_CHECKING:
        kernel32 = ctypes.CDLL('kernel32')
    else:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.argtypes = [
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    ]
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

    inherit_handle = False
    handle = kernel32.OpenProcess(process_query_limited_information, inherit_handle, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == still_active
        )
    finally:
        kernel32.CloseHandle(handle)


def read_restart_marker_value(token: str, phase: str) -> int | None:
    marker = restart_handoff_path(token, phase)
    if marker is None or not marker.is_file():
        return None
    try:
        raw_value = marker.read_text(encoding='utf-8').strip()
        value = int(raw_value)
    except OSError, ValueError:
        return None
    return value if value > 0 else None


def wait_for_restart_marker(
    token: str,
    phase: str,
    *,
    is_launcher_alive: Callable[[], bool],
    expected_value: int | None = None,
    timeout: float = RESTART_HANDOFF_TIMEOUT_SECONDS,
) -> int | None:
    """Wait for a token-authenticated protocol marker and return its app PID/value.

    The process created by Popen/ShellExecute is only a launcher-liveness signal.
    PyInstaller one-file builds use a bootloader parent whose PID differs from
    the Python application child, so launcher PID is deliberately not protocol
    identity. The random token identifies this handoff; ``prepared`` reports
    the actual application PID, and later phases can require that same value.
    """
    marker = restart_handoff_path(token, phase)
    if marker is None or (expected_value is not None and expected_value <= 0):
        return None
    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if not is_launcher_alive():
            log_buffer.log('Restart', f'Replacement launcher exited before {phase} handoff')
            return None
        marker_value = read_restart_marker_value(token, phase)
        if marker_value is not None and (expected_value is None or marker_value == expected_value):
            # Re-check the outer launcher after reading to reject a marker that
            # raced with immediate launcher/application teardown
            if not is_launcher_alive():
                log_buffer.log(
                    'Restart',
                    f'Replacement launcher exited immediately after {phase} handoff',
                )
                return None
            return marker_value
        time.sleep(0.05)
    log_buffer.log('Restart', f'Replacement Fleasion timed out before {phase} handoff')
    return None


def restart_abort_requested(token: str, parent_pid: int) -> bool:
    return read_restart_marker_value(token, 'abort') == parent_pid


def request_restart_abort(token: str, parent_pid: int) -> bool:
    """Ask the application child to abandon the handoff and exit cleanly."""
    marker = restart_handoff_path(token, 'abort')
    if marker is None or parent_pid <= 0:
        return False
    existing = read_restart_marker_value(token, 'abort')
    if existing is not None:
        return existing == parent_pid
    return write_restart_handoff_marker(token, 'abort', parent_pid)


def wait_for_restart_release(token: str, parent_pid: int) -> bool:
    """Child-side gate: do not touch single-instance ownership until released."""
    marker = restart_handoff_path(token, 'release')
    if marker is None or parent_pid <= 0:
        return False
    deadline = time.monotonic() + RESTART_HANDOFF_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if restart_abort_requested(token, parent_pid):
            log_buffer.log('Restart', 'Parent aborted restart before ownership transfer')
            abort_marker = restart_handoff_path(token, 'abort')
            if abort_marker is not None:
                with contextlib.suppress(OSError):
                    abort_marker.unlink(missing_ok=True)
            return False
        if marker.is_file():
            try:
                released_by = marker.read_text(encoding='utf-8').strip()
            except OSError:
                released_by = ''
            if released_by == str(parent_pid):
                with contextlib.suppress(OSError):
                    marker.unlink(missing_ok=True)
                return True
        time.sleep(0.05)
    log_buffer.log('Restart', 'Parent never released single-instance ownership')
    return False


def join_restart_handoff(token: str, parent_pid: int) -> bool:
    """Child-side first phase of the verified restart protocol."""
    if not write_restart_handoff_marker(token, 'prepared', os.getpid()):
        return False
    return wait_for_restart_release(token, parent_pid)


def suspend_single_instance_for_handoff() -> bool:
    """Temporarily transfer the single-instance slot while keeping the proxy alive."""
    shared_memory = single_instance_state.shared_memory
    if shared_memory is None or not shared_memory.isAttached():
        log_buffer.log(
            'Restart', 'Cannot transfer restart ownership: single-instance lock is absent'
        )
        return False

    server = single_instance_state.control_server
    if server is not None:
        server.close()
        QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
        single_instance_state.control_server = None

    if shared_memory.detach():
        return True

    log_buffer.log(
        'Restart', f'Could not release single-instance lock: {shared_memory.errorString()}'
    )
    if not resume_single_instance_after_handoff_failure():
        msg = 'Original process could not restore single-instance ownership after release failure'
        raise RestartHandoffUncertain(msg)
    return False


def resume_single_instance_after_handoff_failure() -> bool:
    """Reclaim both single-instance ownership surfaces after a failed restart."""
    shared_memory = single_instance_state.shared_memory
    if shared_memory is None or not shared_memory.isAttached():
        replacement_lock = QSharedMemory(SINGLE_INSTANCE_KEY)
        if not replacement_lock.create(1):
            log_buffer.log(
                'Restart',
                'Could not reclaim single-instance lock after failed restart: '
                f'{replacement_lock.errorString()}',
            )
            return False
        single_instance_state.shared_memory = replacement_lock

    if single_instance_state.app is None or single_instance_state.tray is None:
        log_buffer.log(
            'Restart',
            'Could not restore single-instance control endpoint: application state is unavailable',
        )
        return False

    control_server = start_single_instance_control_server(
        single_instance_state.app,
        single_instance_state.tray,
    )
    if control_server is None:
        log_buffer.log(
            'Restart',
            'Could not restore single-instance control endpoint after failed restart',
        )
        return False
    single_instance_state.control_server = control_server
    return True


def abort_restart_child_and_wait(
    token: str,
    parent_pid: int,
    application_pid: int | None,
    *,
    is_launcher_alive: Callable[[], bool],
    terminate_launcher: Callable[[], None],
    timeout: float = RESTART_ABORT_TIMEOUT_SECONDS,
) -> bool:
    """Abort a failed replacement and prove the Python application is gone.

    The outer launcher may be a PyInstaller one-file bootloader, so launcher
    termination alone is never treated as proof that the application child
    exited. Once ``prepared`` reports an application PID, rollback is allowed
    only after that PID is no longer alive.
    """
    request_restart_abort(token, parent_pid)
    deadline = time.monotonic() + max(0.0, timeout)
    outer_terminated = False
    while time.monotonic() < deadline:
        if application_pid is not None:
            if not pid_is_alive(application_pid):
                return True
        elif not is_launcher_alive():
            return True

        # Give the child a short opportunity to observe the abort marker and
        # unwind itself before terminating the outer launcher as a fallback
        if not outer_terminated and time.monotonic() + 2.0 >= deadline:
            terminate_launcher()
            outer_terminated = True
        time.sleep(0.05)

    if application_pid is not None and not pid_is_alive(application_pid):
        return True
    if application_pid is None and not is_launcher_alive():
        return True

    log_buffer.log(
        'Restart',
        'Replacement application termination could not be confirmed; '
        'single-instance ownership will not be reclaimed',
    )
    return False


def run_restart_handoff_parent(
    token: str,
    launcher_pid: int,
    *,
    is_launcher_alive: Callable[[], bool],
    terminate_launcher: Callable[[], None],
) -> bool:
    """Parent-side prepared -> release -> ready restart state machine.

    ``launcher_pid`` is diagnostic only. PyInstaller one-file creates a
    bootloader parent plus a Python application child, so protocol identity is
    the random token and ``prepared`` supplies the actual application PID.
    """
    del launcher_pid
    parent_pid = os.getpid()
    ownership_released = False
    handoff_succeeded = False
    application_pid: int | None = None
    try:
        prepared_pid = wait_for_restart_marker(
            token,
            'prepared',
            is_launcher_alive=is_launcher_alive,
        )
        if prepared_pid is None or not pid_is_alive(prepared_pid):
            return False
        application_pid = prepared_pid

        if not suspend_single_instance_for_handoff():
            return False
        ownership_released = True

        if not pid_is_alive(application_pid):
            return False

        if not write_restart_handoff_marker(token, 'release', parent_pid):
            return False

        ready_pid = wait_for_restart_marker(
            token,
            'ready',
            is_launcher_alive=lambda: pid_is_alive(application_pid),
            expected_value=application_pid,
        )
        if ready_pid != application_pid:
            return False

        handoff_succeeded = True
        return True
    finally:
        if not handoff_succeeded:
            terminated = abort_restart_child_and_wait(
                token,
                parent_pid,
                application_pid,
                is_launcher_alive=is_launcher_alive,
                terminate_launcher=terminate_launcher,
            )
            if ownership_released:
                if terminated:
                    if not resume_single_instance_after_handoff_failure():
                        log_buffer.log(
                            'Restart',
                            'Replacement exited, but the original process could not restore '
                            'single-instance ownership completely',
                        )
                        cleanup_restart_handoff(token)
                        msg = 'Original process could not restore single-instance ownership'
                        raise RestartHandoffUncertain(msg)
                else:
                    log_buffer.log(
                        'Restart',
                        'Rollback is intentionally incomplete because the replacement '
                        'application may still own single-instance/proxy resources',
                    )
                    cleanup_restart_handoff(token, preserve_abort=True)
                    msg = 'Replacement application may still own restart resources'
                    raise RestartHandoffUncertain(msg)
        cleanup_restart_handoff(token)


def strip_restart_handoff_args(args: list[str]) -> list[str]:
    """Drop stale protocol credentials before constructing a new relaunch."""
    cleaned: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in {'--restart-handoff-token', '--restart-handoff-parent-pid'}:
            skip_next = True
            continue
        if arg.startswith(('--restart-handoff-token=', '--restart-handoff-parent-pid=')):
            continue
        cleaned.append(arg)
    return cleaned
