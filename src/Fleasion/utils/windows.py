"""Windows-specific utilities."""

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .paths import ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS, STORAGE_DB, STORAGE_DB_GDK
from .logging import log_buffer


def run_cmd(args: list[str]) -> str:
    """Run a Windows command and return its output."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout


# CreateToolhelp32Snapshot goes directly to the kernel and does not touch WMI.

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE_VALUE = ctypes.wintypes.HANDLE(-1).value


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ('dwSize',              ctypes.wintypes.DWORD),
        ('cntUsage',            ctypes.wintypes.DWORD),
        ('th32ProcessID',       ctypes.wintypes.DWORD),
        ('th32DefaultHeapID',   ctypes.c_size_t),   # ULONG_PTR — 8 bytes on x64
        ('th32ModuleID',        ctypes.wintypes.DWORD),
        ('cntThreads',          ctypes.wintypes.DWORD),
        ('th32ParentProcessID', ctypes.wintypes.DWORD),
        ('pcPriClassBase',      ctypes.c_long),
        ('dwFlags',             ctypes.wintypes.DWORD),
        ('szExeFile',           ctypes.c_char * 260),
    ]


def _iter_processes():
    """Yield (pid, exe_name_lower) for every running process.

    Uses CreateToolhelp32Snapshot — no subprocess, no WMI.
    """
    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if snap == _INVALID_HANDLE_VALUE:
        return
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if k32.Process32First(snap, ctypes.byref(entry)):
            while True:
                yield entry.th32ProcessID, entry.szExeFile.decode('utf-8', errors='replace').lower()
                if not k32.Process32Next(snap, ctypes.byref(entry)):
                    break
    finally:
        k32.CloseHandle(snap)


def _find_pid(exe_name: str) -> Optional[int]:
    """Return the PID of the first process matching exe_name (case-insensitive)."""
    target = exe_name.lower()
    for pid, name in _iter_processes():
        if name == target:
            return pid
    return None


def _query_exe_path(pid: int) -> Optional[Path]:
    """Return the full executable path for a given PID via QueryFullProcessImageNameW."""
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(32768)
        size = ctypes.wintypes.DWORD(32768)
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return Path(buf.value)
        return None
    finally:
        k32.CloseHandle(handle)


_WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)


def wait_for_roblox_window(timeout: float = 60.0) -> bool:
    """Wait until RobloxPlayerBeta has a visible top-level window."""
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        pid = _find_pid(ROBLOX_PROCESS)
        if pid is not None:
            found = []

            def _cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    lp = ctypes.wintypes.DWORD(0)
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lp))
                    if lp.value == pid:
                        found.append(hwnd)
                        return False
                return True

            user32.EnumWindows(_WNDENUMPROC(_cb), 0)
            if found:
                return True
        time.sleep(0.25)
    return False


def is_roblox_running() -> bool:
    """Check if Roblox is currently running."""
    return _find_pid(ROBLOX_PROCESS) is not None


def is_studio_running() -> bool:
    """Check if Roblox Studio is currently running."""
    return _find_pid(ROBLOX_STUDIO_PROCESS) is not None


def get_roblox_player_exe_path() -> Optional[Path]:
    """Return the full executable path of the running RobloxPlayerBeta.exe, or None."""
    pid = _find_pid(ROBLOX_PROCESS)
    return _query_exe_path(pid) if pid is not None else None


def get_roblox_studio_exe_path() -> Optional[Path]:
    """Return the full executable path of the running RobloxStudioBeta.exe, or None."""
    pid = _find_pid(ROBLOX_STUDIO_PROCESS)
    return _query_exe_path(pid) if pid is not None else None


def terminate_roblox() -> bool:
    """Terminate Roblox if it's running. Returns True if it was running."""
    if not is_roblox_running():
        return False
    run_cmd(['taskkill', '/F', '/IM', ROBLOX_PROCESS])
    return True


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Roblox to exit. Returns True if it exited before timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)
    return False


def _delete_db_file(db_path: Path, messages: list, label: str = 'Storage database') -> None:
    """Delete a single rbx-storage.db file, attempting win32 unlock on PermissionError."""
    if not db_path.exists():
        messages.append(f'{label} not found')
        return
    try:
        db_path.unlink()
        messages.append(f'{label} deleted successfully')
    except PermissionError:
        messages.append(f'{label}: Permission denied - attempting to unlock...')
        try:
            import win32file
            import win32con
            import pywintypes

            try:
                handle = win32file.CreateFile(
                    str(db_path),
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    win32con.FILE_SHARE_DELETE,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None
                )
                win32file.CloseHandle(handle)
            except pywintypes.error:
                pass

            db_path.unlink()
            messages.append(f'{label}: unlocked and deleted successfully')
        except ImportError:
            messages.append(f'{label}: Failed: pywin32 not available for unlock')
        except Exception as e:
            messages.append(f'{label}: Failed to unlock: {e}')
    except OSError as e:
        messages.append(f'{label}: Failed: {e}')


def delete_cache() -> list[str]:
    """Delete Roblox cache with cleanup. Returns list of status messages."""
    messages = []

    if is_roblox_running():
        messages.append('Roblox is running, terminating...')
        terminate_roblox()
        if wait_for_roblox_exit():
            messages.append('Roblox terminated successfully')
        else:
            messages.extend(['Roblox termination timed out', 'Cache deletion aborted'])
            return messages
    else:
        messages.append('Roblox was closed')

    # Delete rbx-storage.db (standard install)
    _delete_db_file(STORAGE_DB, messages, 'Storage database')

    # Delete rbx-storage.db (Microsoft Store / GDK install) if it exists
    if STORAGE_DB_GDK.parent.exists():
        _delete_db_file(STORAGE_DB_GDK, messages, 'Storage database (GDK)')

    # Delete rbx-storage folder
    import shutil
    storage_folder = STORAGE_DB.parent / 'rbx-storage'
    if storage_folder.exists():
        try:
            shutil.rmtree(storage_folder)
            messages.append('Storage folder deleted successfully')
        except PermissionError:
            messages.append('Failed to delete storage folder: Permission denied')
        except OSError as e:
            messages.append(f'Failed to delete storage folder: {e}')
    else:
        messages.append('Storage folder not found')

    # Delete Fleasion APP_CACHE_DIR (preserve predownloaded/ and texpack_slots/)
    from .paths import APP_CACHE_DIR
    if APP_CACHE_DIR.exists():
        try:
            _preserve_set = {APP_CACHE_DIR / 'predownloaded', APP_CACHE_DIR / 'texpack_slots'}
            for child in APP_CACHE_DIR.iterdir():
                if child in _preserve_set:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            messages.append('Fleasion obj cache deleted successfully')
        except PermissionError:
            messages.append('Failed to delete obj cache: Permission denied')
        except OSError as e:
            messages.append(f'Failed to delete obj cache: {e}')

    return messages


def _is_process_elevated() -> bool:
    """Return True when the current process is running elevated on Windows."""
    if not hasattr(ctypes, 'windll'):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_TOKEN_ASSIGN_PRIMARY = 0x0001
_TOKEN_DUPLICATE = 0x0002
_TOKEN_QUERY = 0x0008
_TOKEN_ADJUST_DEFAULT = 0x0080
_TOKEN_ADJUST_SESSIONID = 0x0100
_SECURITY_IMPERSONATION = 2
_TOKEN_PRIMARY = 1
_STARTF_USESHOWWINDOW = 0x00000001
_SW_SHOWNORMAL = 1
_LOGON_WITH_PROFILE = 0x00000001


class _STARTUPINFOW(ctypes.Structure):
    _fields_ = [
        ('cb', ctypes.wintypes.DWORD),
        ('lpReserved', ctypes.wintypes.LPWSTR),
        ('lpDesktop', ctypes.wintypes.LPWSTR),
        ('lpTitle', ctypes.wintypes.LPWSTR),
        ('dwX', ctypes.wintypes.DWORD),
        ('dwY', ctypes.wintypes.DWORD),
        ('dwXSize', ctypes.wintypes.DWORD),
        ('dwYSize', ctypes.wintypes.DWORD),
        ('dwXCountChars', ctypes.wintypes.DWORD),
        ('dwYCountChars', ctypes.wintypes.DWORD),
        ('dwFillAttribute', ctypes.wintypes.DWORD),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('wShowWindow', ctypes.wintypes.WORD),
        ('cbReserved2', ctypes.wintypes.WORD),
        ('lpReserved2', ctypes.POINTER(ctypes.c_ubyte)),
        ('hStdInput', ctypes.wintypes.HANDLE),
        ('hStdOutput', ctypes.wintypes.HANDLE),
        ('hStdError', ctypes.wintypes.HANDLE),
    ]


class _PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ('hProcess', ctypes.wintypes.HANDLE),
        ('hThread', ctypes.wintypes.HANDLE),
        ('dwProcessId', ctypes.wintypes.DWORD),
        ('dwThreadId', ctypes.wintypes.DWORD),
    ]


def _close_handle(handle) -> None:
    """Close a Win32 handle if it is valid."""
    raw = getattr(handle, 'value', handle)
    if raw:
        ctypes.windll.kernel32.CloseHandle(raw)


def _build_launch_command(target_str: str) -> tuple[str, Optional[str]]:
    """Build command line + cwd for token-based process creation."""
    is_uri = '://' in target_str or target_str.startswith(('roblox-player:', 'roblox:'))
    if is_uri:
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        rundll = system_root / 'System32' / 'rundll32.exe'
        cmdline = f'"{rundll}" url.dll,FileProtocolHandler "{target_str}"'
        return cmdline, None

    target_path = Path(target_str)
    cwd = str(target_path.parent) if target_path.exists() else None
    return f'"{target_str}"', cwd


def _launch_with_shell_token(target_str: str) -> bool:
    """Launch target with the desktop shell's primary token (non-elevated)."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    advapi32 = ctypes.windll.advapi32

    advapi32.OpenProcessToken.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.HANDLE),
    ]
    advapi32.OpenProcessToken.restype = ctypes.wintypes.BOOL

    advapi32.DuplicateTokenEx.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(ctypes.wintypes.HANDLE),
    ]
    advapi32.DuplicateTokenEx.restype = ctypes.wintypes.BOOL

    advapi32.CreateProcessWithTokenW.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.wintypes.DWORD,
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.LPWSTR,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOW),
        ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    advapi32.CreateProcessWithTokenW.restype = ctypes.wintypes.BOOL

    shell_hwnd = user32.GetShellWindow()
    if not shell_hwnd:
        log_buffer.log('Launcher', 'Could not get shell window for unelevated launch')
        return False

    shell_pid = ctypes.wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(shell_hwnd, ctypes.byref(shell_pid))
    if not shell_pid.value:
        log_buffer.log('Launcher', 'Could not resolve shell process id for unelevated launch')
        return False

    shell_process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, shell_pid.value)
    if not shell_process:
        err = kernel32.GetLastError()
        log_buffer.log('Launcher', f'OpenProcess(shell) failed: WinError {err}')
        return False

    shell_token = ctypes.wintypes.HANDLE()
    primary_token = ctypes.wintypes.HANDLE()
    proc_info = _PROCESS_INFORMATION()
    try:
        open_access = _TOKEN_DUPLICATE | _TOKEN_ASSIGN_PRIMARY | _TOKEN_QUERY
        if not advapi32.OpenProcessToken(shell_process, open_access, ctypes.byref(shell_token)):
            err = kernel32.GetLastError()
            log_buffer.log('Launcher', f'OpenProcessToken(shell) failed: WinError {err}')
            return False

        dup_access = (
            _TOKEN_ASSIGN_PRIMARY |
            _TOKEN_DUPLICATE |
            _TOKEN_QUERY |
            _TOKEN_ADJUST_DEFAULT |
            _TOKEN_ADJUST_SESSIONID
        )
        if not advapi32.DuplicateTokenEx(
            shell_token,
            dup_access,
            None,
            _SECURITY_IMPERSONATION,
            _TOKEN_PRIMARY,
            ctypes.byref(primary_token),
        ):
            err = kernel32.GetLastError()
            log_buffer.log('Launcher', f'DuplicateTokenEx(shell) failed: WinError {err}')
            return False

        cmdline, cwd = _build_launch_command(target_str)
        startup = _STARTUPINFOW()
        startup.cb = ctypes.sizeof(_STARTUPINFOW)
        startup.dwFlags = _STARTF_USESHOWWINDOW
        startup.wShowWindow = _SW_SHOWNORMAL

        cmd_buf = ctypes.create_unicode_buffer(cmdline)
        created = advapi32.CreateProcessWithTokenW(
            primary_token,
            _LOGON_WITH_PROFILE,
            None,
            cmd_buf,
            0,
            None,
            cwd,
            ctypes.byref(startup),
            ctypes.byref(proc_info),
        )
        if not created:
            err = kernel32.GetLastError()
            log_buffer.log('Launcher', f'CreateProcessWithTokenW failed: WinError {err}')
            return False

        return True
    finally:
        _close_handle(proc_info.hThread)
        _close_handle(proc_info.hProcess)
        _close_handle(primary_token)
        _close_handle(shell_token)
        _close_handle(shell_process)


def launch_as_standard_user(target: str | Path) -> bool:
    """Launch a URI/path as a standard user when Fleasion is elevated."""
    target_str = str(target).strip()
    if not target_str:
        return False

    # os.startfile inherits the current process token. If Fleasion is elevated,
    # using it here would reintroduce the exact admin-inheritance bug.
    if _is_process_elevated():
        launched = _launch_with_shell_token(target_str)
        if launched:
            return True
        log_buffer.log('Launcher', f'Elevated shell launch failed, falling back to os.startfile: {target_str}')

    try:
        os.startfile(target_str)
        return True
    except OSError as exc:
        log_buffer.log('Launcher', f'Fallback launch failed: {exc}')
        return False


def open_folder(path: Path):
    """Open a folder in Windows Explorer."""
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a Windows message box."""
    ctypes.windll.user32.MessageBoxW(0, message, title, icon)
