"""Fleasion process discovery and instance termination."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

from PySide6.QtNetwork import QLocalSocket

from fleasion.app.error_details import (
    get_int_detail as _get_int_detail,
    is_error_details as _is_error_details,
)
from fleasion.utils import log_buffer

SINGLE_INSTANCE_CONTROL_SERVER = 'FleasionSingleInstanceControl'


def resolve_executable(name: str) -> str:
    """Resolve an executable name or raise when it is unavailable."""
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(name)
    return path


def run_trusted_text_command(
    args: list[str],
    *,
    timeout: float,
    creationflags: int = 0,
    encoding: str | None = None,
    errors: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a trusted argument list and capture decoded output."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=errors,
        timeout=timeout,
        creationflags=creationflags,
        check=False,
        shell=False,
    )


def _run_trusted_command(
    args: list[str], *, timeout: float, creationflags: int = 0
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        capture_output=True,
        timeout=timeout,
        creationflags=creationflags,
        check=False,
        shell=False,
    )


def _looks_like_fleasion_gui_command(command: str) -> bool:
    """Return whether a process command is a Fleasion GUI app/dev launch."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    if not tokens or '--linux-proxy-helper' in tokens:
        return False
    if any(Path(argument).name == 'linux_proxy_helper_daemon.py' for argument in tokens):
        return False

    executable = Path(tokens[0]).name.lower()
    if executable == 'fleasion' or executable.startswith('fleasion-v'):
        return True

    # ``uv run fleasion`` and virtual-environment entry points are executed as
    # ``python …/bin/fleasion``.  The old check only considered argv[0], so
    # Linux's Kill Others action missed the already-running GUI and a second
    # instance later failed on the proxy backend port.
    if any(Path(argument).name.lower() == 'fleasion' for argument in tokens[1:]):
        return True

    return any(
        argument == '-m' and index + 1 < len(tokens) and tokens[index + 1].lower() == 'fleasion'
        for index, argument in enumerate(tokens)
    )


def _parse_posix_fleasion_pids(output: str, safe_pids: set[int]) -> list[int]:
    pids: list[int] = []
    for raw in output.splitlines():
        try:
            pid_text, _ppid_text, command = raw.strip().split(None, 2)
            pid = int(pid_text)
        except ValueError, TypeError:
            continue
        if pid not in safe_pids and _looks_like_fleasion_gui_command(command):
            pids.append(pid)
    return pids


def _parse_tasklist_pids(output: str, safe_pids: set[int]) -> list[int]:
    pids: list[int] = []
    for raw_line in output.strip().splitlines():
        line = raw_line.strip().strip('"')
        parts = line.split('","')
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[1])
        except ValueError, IndexError:
            continue
        if pid not in safe_pids:
            pids.append(pid)
    return pids


def _parse_powershell_fleasion_pids(output: str, safe_pids: set[int]) -> list[int]:
    try:
        raw_data: object = json.loads(output)
    except json.JSONDecodeError, TypeError, ValueError:
        return []
    if isinstance(raw_data, dict):
        records: list[object] = [raw_data]
    elif isinstance(raw_data, list):
        records = cast('list[object]', raw_data)
    else:
        return []

    pids: list[int] = []
    for record in records:
        if not _is_error_details(record):
            continue
        pid = _get_int_detail(record, 'ProcessId', 0)
        cmdline = str(record.get('CommandLine') or '').lower()
        if pid not in safe_pids and pid != 0 and 'fleasion' in cmdline:
            pids.append(pid)
    return pids


def other_fleasion_pids() -> list[int]:
    """Return PIDs of other Fleasion GUI processes (excludes current process and its parent)."""
    safe_pids = {os.getpid(), os.getppid()}
    exe_name = Path(sys.executable).name

    if sys.platform != 'win32':
        try:
            result = run_trusted_text_command(
                [resolve_executable('ps'), '-axo', 'pid=,ppid=,command='],
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
            return []
        return _parse_posix_fleasion_pids(result.stdout, safe_pids)

    if exe_name.lower() not in {'python.exe', 'python3.exe'}:
        try:
            result = run_trusted_text_command(
                [
                    resolve_executable('tasklist'),
                    '/FI',
                    f'IMAGENAME eq {exe_name}',
                    '/FO',
                    'CSV',
                    '/NH',
                ],
                encoding='utf-8',
                errors='replace',
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
            return []
        return _parse_tasklist_pids(result.stdout, safe_pids)

    ps_cmd = (
        'Get-CimInstance Win32_Process -Filter "Name=\'python.exe\'" | '
        'Select-Object ProcessId, CommandLine | ConvertTo-Json -Depth 1'
    )
    try:
        result = run_trusted_text_command(
            [resolve_executable('powershell'), '-NoProfile', '-Command', ps_cmd],
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('App', f'Could not inspect running Fleasion processes: {exc}')
        return []
    return _parse_powershell_fleasion_pids(result.stdout, safe_pids)


def send_running_instance_command(payload: str, timeout_ms: int) -> bool:
    """Send a command to the running Fleasion instance."""
    socket = QLocalSocket()
    socket.connectToServer(SINGLE_INSTANCE_CONTROL_SERVER)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(payload.encode())
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    socket.waitForDisconnected(1000)
    return True


def _request_running_instance_exit(
    timeout_ms: int = 2000,
    *,
    preserve_env_proxy_player: bool = False,
) -> bool:
    """Ask the already-running Fleasion instance to exit through its Qt event loop."""
    command = 'quit-preserve-env-player' if preserve_env_proxy_player else 'quit'
    try:
        return send_running_instance_command(f'{command}\n', timeout_ms)
    except OSError, RuntimeError:
        return False


def _wait_for_other_fleasion_instances_to_exit(timeout_seconds: float = 8.0) -> bool:
    """Wait until no other Fleasion processes remain."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if not other_fleasion_pids():
            return True
        time.sleep(0.1)
    return not other_fleasion_pids()


def request_other_fleasion_instances_exit(
    timeout_seconds: float = 8.0,
    *,
    preserve_env_proxy_player: bool = False,
) -> bool:
    """Return True if other instances were asked to exit and disappeared."""
    if not other_fleasion_pids():
        return True
    if not _request_running_instance_exit(preserve_env_proxy_player=preserve_env_proxy_player):
        return False
    return _wait_for_other_fleasion_instances_to_exit(timeout_seconds)


def _terminate_other_fleasion_pid(pid: int) -> None:
    if sys.platform != 'win32':
        os.kill(pid, signal.SIGTERM)
        return

    taskkill = resolve_executable('taskkill')
    _run_trusted_command(
        [taskkill, '/PID', str(pid)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=5,
    )
    if _wait_for_other_fleasion_instances_to_exit(2.0):
        return
    _run_trusted_command(
        [taskkill, '/F', '/PID', str(pid)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=5,
    )


def kill_other_fleasion_instances() -> None:
    """Kill all other Fleasion instances except the current process."""

    if request_other_fleasion_instances_exit():
        return

    for pid in other_fleasion_pids():
        try:
            _terminate_other_fleasion_pid(pid)
        except (OSError, subprocess.SubprocessError) as exc:
            log_buffer.log('App', f'Could not terminate Fleasion process {pid}: {exc}')
