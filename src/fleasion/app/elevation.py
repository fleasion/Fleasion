"""Administrator relaunch commands for Windows and macOS."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import importlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.app.process_control import (
    resolve_executable as _resolve_executable,
    run_trusted_text_command as _run_trusted_text_command,
)
from fleasion.app.restart_handoff import run_restart_handoff_parent, strip_restart_handoff_args
from fleasion.utils import (
    CONFIG_DIR,
    log_buffer,
)

if TYPE_CHECKING:
    from fleasion.app.compatibility import RelaunchCompletion


WINDOWS_WAIT_TIMEOUT = 0x102


def is_admin() -> bool:
    """Return True if the current process has administrator/root privileges."""
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0

    try:
        if TYPE_CHECKING:
            shell32 = ctypes.CDLL('shell32')
        else:
            shell32 = ctypes.windll.shell32
        return bool(shell32.IsUserAnAdmin())
    except AttributeError, OSError:
        return False


def append_windows_requesting_user_args(existing_args: list[str]) -> bool:
    """Carry the pre-UAC desktop identity into a one-shot elevated child."""
    if sys.platform != 'win32':
        return True
    if any(arg.startswith('--fleasion-requesting-user-sid=') for arg in existing_args):
        return True
    try:
        windows_permissions = importlib.import_module('fleasion.utils.windows_permissions')
        sid, _account_name = windows_permissions.current_windows_user_identity()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log('UAC', f'Could not capture requesting Windows identity: {exc}')
        return False
    existing_args.extend(
        [
            f'--fleasion-requesting-user-sid={sid}',
        ]
    )
    return True


def relaunch_as_admin_macos(*, extra_args: str, wait_for_completion: bool) -> bool:
    existing_args = strip_restart_handoff_args(list(sys.argv[1:]))
    if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
        existing_args.append(f'--fleasion-user-localappdata={CONFIG_DIR.parent}')
    if extra_args.strip():
        existing_args.extend(extra_args.strip().split())

    if getattr(sys, 'frozen', False):
        launch = [sys.executable, *existing_args]
        redirect = ' >/tmp/fleasion-admin.log 2>&1'
        if not wait_for_completion:
            redirect += ' &'
        shell_cmd = (
            f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} {shlex.join(launch)}{redirect}'
        )
    else:
        project_root = Path(__file__).resolve().parents[3]
        python_exe = Path(sys.executable)
        launch = [str(python_exe), '-m', 'fleasion', *existing_args]
        redirect = ' >/tmp/fleasion-admin.log 2>&1'
        if not wait_for_completion:
            redirect += ' &'
        shell_cmd = (
            f'cd {shlex.quote(str(project_root))} && '
            f'FLEASION_USER_HOME={shlex.quote(str(Path.home()))} '
            f'PYTHONPATH={shlex.quote(str(project_root / "src"))} '
            f'{shlex.join(launch)}{redirect}'
        )

    script = 'do shell script ' + json.dumps(shell_cmd) + ' with administrator privileges'
    try:
        result = _run_trusted_text_command(
            [_resolve_executable('osascript'), '-e', script],
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('UAC', f'macOS administrator relaunch failed: {exc}')
        return False

    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        log_buffer.log(
            'UAC',
            f'macOS administrator relaunch was cancelled or failed: {err or result.returncode}',
        )
        return False
    return True


def relaunch_as_admin_windows(
    extra_args: str,
    parent_hwnd: int | None,
    *,
    wait_for_completion: bool,
    wait_timeout_ms: int,
    completion: RelaunchCompletion | None,
    restart_handoff_token: str | None,
    restart_handoff_parent_pid: int | None,
) -> bool:
    existing_args = strip_restart_handoff_args(list(sys.argv[1:]))
    if restart_handoff_token:
        existing_args = [arg for arg in existing_args if arg != '--kill-others']
        existing_args.extend(
            [
                '--restart-handoff-token',
                restart_handoff_token,
                '--restart-handoff-parent-pid',
                str(restart_handoff_parent_pid),
            ]
        )
    if not any(arg.startswith('--fleasion-user-localappdata=') for arg in existing_args):
        local_appdata = os.environ.get('LOCALAPPDATA') or str(CONFIG_DIR.parent)
        existing_args.append(f'--fleasion-user-localappdata={local_appdata}')
    if extra_args.strip():
        existing_args.extend(extra_args.strip().split())
    requesting_identity_captured = append_windows_requesting_user_args(existing_args)
    if extra_args.strip().startswith(
        ('--repair-autostart', '--repair-roblox-permissions')
    ) and not (requesting_identity_captured):
        return False
    # Normal elevation asks the old process to exit before claiming the slot
    # Verified restart handoffs are different: the parent keeps its working
    # proxy alive and explicitly transfers the single-instance slot only after
    # this final elevated child reaches the prepared gate
    if not restart_handoff_token and '--kill-others' not in existing_args:
        existing_args.append('--kill-others')

    frozen = bool(getattr(sys, 'frozen', False))
    if frozen:
        # Compiled .exe — sys.executable is the .exe itself
        exe = sys.executable
        params = subprocess.list2cmdline(existing_args) if existing_args else None
    else:
        # Dev / uv run — locate the uv executable and replay the original
        # invocation through it.  Running the Python interpreter directly in
        # the elevated process would miss the uv-managed virtualenv entirely,
        # causing import failures and a silent crash

        uv_exe = shutil.which('uv') or shutil.which('uv.exe')
        if uv_exe:
            # Reconstruct:  uv run fleasion  (the original entry-point)
            exe = uv_exe
            # Pass the project directory so uv finds pyproject.toml correctly
            cwd = str(Path(__file__).resolve().parents[3])
            # ShellExecuteW doesn't let us set cwd directly for the child, but
            # we can pass --project to tell uv where to look
            params = subprocess.list2cmdline(['--project', cwd, 'run', 'fleasion', *existing_args])
        else:
            # Fallback: plain interpreter (may fail if venv is not activated,
            # but it's the best we can do without uv)
            exe = sys.executable
            params = subprocess.list2cmdline([sys.argv[0], *existing_args])

    # Use ShellExecuteExW with SEE_MASK_NO_CONSOLE so the elevated process
    # (which may be uv.exe, a console app) never spawns a visible cmd window

    see_mask_no_console = 0x00008000
    see_mask_nocloseprocess = 0x00000040

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ('cbSize', ctypes.wintypes.DWORD),
            ('fMask', ctypes.wintypes.ULONG),
            ('hwnd', ctypes.wintypes.HWND),
            ('lpVerb', ctypes.wintypes.LPCWSTR),
            ('lpFile', ctypes.wintypes.LPCWSTR),
            ('lpParameters', ctypes.wintypes.LPCWSTR),
            ('lpDirectory', ctypes.wintypes.LPCWSTR),
            ('nShow', ctypes.c_int),
            ('hInstApp', ctypes.wintypes.HINSTANCE),
            ('lpIDList', ctypes.c_void_p),
            ('lpClass', ctypes.wintypes.LPCWSTR),
            ('hkeyClass', ctypes.wintypes.HKEY),
            ('dwHotKey', ctypes.wintypes.DWORD),
            ('hIconOrMonitor', ctypes.wintypes.HANDLE),
            ('hProcess', ctypes.wintypes.HANDLE),
        ]

    sei = _SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(_SHELLEXECUTEINFOW)
    sei.fMask = see_mask_no_console | see_mask_nocloseprocess
    sei.hwnd = parent_hwnd
    sei.lpVerb = 'runas'
    sei.lpFile = exe
    sei.lpParameters = params
    sei.lpDirectory = str(Path(exe).resolve().parent)
    # SW_HIDE (0) for dev/uv mode: hides the uv.exe console wrapper
    # SW_SHOWNORMAL (1) for compiled .exe: the exe IS the app, we need windows to show
    sei.nShow = 1 if frozen else 0
    sei.hInstApp = None

    if TYPE_CHECKING:
        shell32 = ctypes.CDLL('shell32')
    else:
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    shell32.ShellExecuteExW.restype = ctypes.wintypes.BOOL

    reset_env_key = 'PYINSTALLER_RESET_ENVIRONMENT'
    old_reset_env = os.environ.get(reset_env_key)
    if frozen:
        os.environ[reset_env_key] = '1'
    try:
        ok = shell32.ShellExecuteExW(ctypes.byref(sei))
    finally:
        if frozen:
            if old_reset_env is None:
                os.environ.pop(reset_env_key, None)
            else:
                os.environ[reset_env_key] = old_reset_env
    if not ok:
        if TYPE_CHECKING:
            err = ctypes.get_errno()
            error_text = os.strerror(err)
        else:
            err = ctypes.get_last_error()
            error_text = ctypes.FormatError(err)
        if err == 1223:  # ERROR_CANCELLED: user declined UAC
            log_buffer.log('UAC', 'Administrator relaunch was cancelled by the user')
        else:
            log_buffer.log(
                'UAC',
                f'Administrator relaunch failed: WinError {err}: {error_text}',
            )
        return False

    if TYPE_CHECKING:
        kernel32 = ctypes.CDLL('kernel32')
    else:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = ctypes.wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    kernel32.GetExitCodeProcess.restype = ctypes.wintypes.BOOL
    kernel32.GetProcessId.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.GetProcessId.restype = ctypes.wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [ctypes.wintypes.HANDLE, ctypes.wintypes.UINT]
    kernel32.TerminateProcess.restype = ctypes.wintypes.BOOL
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

    if restart_handoff_token:
        child_pid = int(kernel32.GetProcessId(sei.hProcess))
        if child_pid <= 0:
            log_buffer.log('Restart', 'Could not identify elevated replacement process')
            kernel32.CloseHandle(sei.hProcess)
            return False

        def _elevated_child_alive() -> bool:
            return kernel32.WaitForSingleObject(sei.hProcess, 0) == WINDOWS_WAIT_TIMEOUT

        def _terminate_elevated_child() -> None:
            if not _elevated_child_alive():
                return
            if kernel32.TerminateProcess(sei.hProcess, 1):
                kernel32.WaitForSingleObject(sei.hProcess, 2_000)

        try:
            return run_restart_handoff_parent(
                restart_handoff_token,
                child_pid,
                is_launcher_alive=_elevated_child_alive,
                terminate_launcher=_terminate_elevated_child,
            )
        finally:
            kernel32.CloseHandle(sei.hProcess)

    if not wait_for_completion:
        kernel32.CloseHandle(sei.hProcess)
        return True

    wait_result = kernel32.WaitForSingleObject(sei.hProcess, wait_timeout_ms)
    exit_code = ctypes.wintypes.DWORD()
    exit_code_read = False
    if wait_result != WINDOWS_WAIT_TIMEOUT:
        exit_code_read = bool(kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(exit_code)))
    completed = wait_result == 0 and exit_code_read and exit_code.value == 0
    kernel32.CloseHandle(sei.hProcess)
    if completion is not None:
        completion.update(
            {
                'wait_result': wait_result,
                'exit_code_read': exit_code_read,
                'exit_code': exit_code.value if exit_code_read else None,
            }
        )
    if not completed:
        if wait_result == WINDOWS_WAIT_TIMEOUT:
            log_buffer.log(
                'UAC',
                f'Elevated child is still running after {wait_timeout_ms / 1000:.0f}s; '
                'the synchronous wait timed out',
            )
        else:
            log_buffer.log(
                'UAC',
                f'Elevated child did not complete successfully (wait={wait_result}, '
                f'exit={exit_code.value if exit_code_read else "unknown"})',
            )
    return completed


def relaunch_as_admin(
    extra_args: str = '',
    parent_hwnd: int | None = None,
    *,
    wait_for_completion: bool = False,
    wait_timeout_ms: int = 120_000,
    completion: RelaunchCompletion | None = None,
    restart_handoff_token: str | None = None,
    restart_handoff_parent_pid: int | None = None,
) -> bool:
    """Silently attempt to relaunch elevated via the platform prompt.

    Shows only the standard Windows UAC or macOS administrator prompt.
    Returns True if the elevated process was spawned (caller should exit), or,
    when ``wait_for_completion`` is set, if the elevated child completed with
    exit code zero. ``completion`` receives the native wait and exit-code
    details for synchronous callers that need a more specific failure reason.
    Returns False if the user declined or the relaunch failed. A restart
    handoff token enables the verified parent/child protocol used by mode
    switches; it cannot be combined with ``wait_for_completion``.
    """
    if restart_handoff_token and (
        sys.platform != 'win32'
        or wait_for_completion
        or not restart_handoff_parent_pid
        or restart_handoff_parent_pid <= 0
    ):
        log_buffer.log('Restart', 'Invalid administrator restart handoff request')
        return False

    if sys.platform == 'darwin':
        return relaunch_as_admin_macos(
            extra_args=extra_args, wait_for_completion=wait_for_completion
        )

    if sys.platform.startswith('linux'):
        log_buffer.log(
            'UAC',
            'Linux administrator relaunch skipped: proxy uses the privileged helper instead',
        )
        return False

    return relaunch_as_admin_windows(
        extra_args,
        parent_hwnd,
        wait_for_completion=wait_for_completion,
        wait_timeout_ms=wait_timeout_ms,
        completion=completion,
        restart_handoff_token=restart_handoff_token,
        restart_handoff_parent_pid=restart_handoff_parent_pid,
    )


def attempt_silent_elevation(  # pyright: ignore[reportUnusedFunction] - retained compatibility helper
    extra_args: str = '', parent_hwnd: int | None = None
) -> bool:
    """Try to elevate silently on startup.

    If already admin, returns True immediately.
    Otherwise fires the UAC prompt. If the user accepts, the elevated
    copy launches and this function calls sys.exit(0) to close the
    non-elevated instance.  If the user declines, returns False so the
    caller continues in read-only mode — no extra dialog shown.
    """
    if is_admin():
        return True

    success = relaunch_as_admin(extra_args=extra_args, parent_hwnd=parent_hwnd)
    if success:
        # Elevated copy is now starting up — close this instance silently
        sys.exit(0)

    # User clicked "No" on UAC — stay open in read-only mode
    return False
