"""Launch replacement application processes and verify their startup."""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from fleasion.app.dialogs.common import visible_parent_widget, window_handle
from fleasion.app.elevation import is_admin, relaunch_as_admin
from fleasion.app.restart_handoff import (
    cleanup_restart_handoff,
    run_restart_handoff_parent,
    strip_restart_handoff_args,
)
from fleasion.utils import (
    log_buffer,
)


def spawn_trusted_command(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    start_new_session: bool = False,
    creationflags: int = 0,
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        args,
        shell=False,
        env=env,
        start_new_session=start_new_session,
        creationflags=creationflags,
    )


class ProcessHandle(Protocol):
    pid: int

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


def terminate_popen_child(process: ProcessHandle) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2.0)
        except OSError, subprocess.TimeoutExpired:
            pass
    except OSError:
        pass


def restart_fleasion_normally(
    *,
    preserve_env_proxy_player: bool = False,
    verify_startup: bool = False,
    require_admin: bool = False,
) -> bool:
    """Relaunch Fleasion and optionally verify the final replacement.

    Verified restarts use a three-phase handoff. The child first proves it
    survived imports/elevation (``prepared``), then waits while the parent
    releases only the single-instance slot (the working proxy stays alive).
    The parent exits only after the child has claimed that slot and published
    ``ready`` after Hosts-mode proxy startup succeeds.
    """
    existing_args = strip_restart_handoff_args(list(sys.argv[1:]))
    existing_args = [arg for arg in existing_args if arg != '--preserve-env-proxy-player']

    handoff_token = secrets.token_hex(16) if verify_startup else ''
    handoff_parent_pid = os.getpid() if handoff_token else 0
    if handoff_token:
        cleanup_restart_handoff(handoff_token)
        existing_args = [arg for arg in existing_args if arg != '--kill-others']
        existing_args.extend(
            [
                '--restart-handoff-token',
                handoff_token,
                '--restart-handoff-parent-pid',
                str(handoff_parent_pid),
            ]
        )
    elif '--kill-others' not in existing_args:
        existing_args.append('--kill-others')

    if preserve_env_proxy_player:
        existing_args.append('--preserve-env-proxy-player')

    if require_admin:
        if sys.platform != 'win32':
            log_buffer.log(
                'Restart', 'Administrator restart was requested on a non-Windows platform'
            )
            return False
        if is_admin():
            require_admin = False
        else:
            if not handoff_token:
                log_buffer.log('Restart', 'Refusing unverified administrator restart')
                return False
            return relaunch_as_admin(
                extra_args=('--preserve-env-proxy-player' if preserve_env_proxy_player else ''),
                parent_hwnd=window_handle(visible_parent_widget()),
                restart_handoff_token=handoff_token,
                restart_handoff_parent_pid=handoff_parent_pid,
            )

    creationflags = 0
    child_env: dict[str, str] | None = None
    start_new_session = False

    if getattr(sys, 'frozen', False):
        launch = [sys.executable, *existing_args]
        # PyInstaller one-file children must start a fresh extraction/runtime
        # environment. Reusing the current bootloader environment can make an
        # independent relaunch import from the old temporary directory and die
        # with missing stdlib/native modules after the parent exits
        child_env = os.environ.copy()
        child_env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        if sys.platform != 'win32':
            start_new_session = True
    elif sys.platform == 'win32':
        uv_exe = shutil.which('uv') or shutil.which('uv.exe')
        if uv_exe:
            cwd = str(Path(__file__).resolve().parents[3])
            launch = [uv_exe, '--project', cwd, 'run', 'fleasion', *existing_args]
        else:
            launch = [sys.executable, sys.argv[0], *existing_args]
        creationflags = subprocess.CREATE_NO_WINDOW
    else:
        launch = [sys.executable, '-m', 'fleasion', *existing_args]
        start_new_session = True

    try:
        process = spawn_trusted_command(
            launch,
            env=child_env,
            start_new_session=start_new_session,
            creationflags=creationflags,
        )
    except OSError as exc:
        log_buffer.log('Restart', f'Failed to relaunch Fleasion: {exc}')
        return False

    if handoff_token and not run_restart_handoff_parent(
        handoff_token,
        process.pid,
        is_launcher_alive=lambda: process.poll() is None,
        terminate_launcher=lambda: terminate_popen_child(process),
    ):
        return False

    log_buffer.log('Restart', 'Relaunching Fleasion to apply a setting change')
    return True
