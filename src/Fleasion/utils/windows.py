"""Windows-specific utilities."""

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

from .paths import ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS, STORAGE_DB, STORAGE_DB_GDK


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
        messages.append('Roblox is not running')

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


def open_folder(path: Path):
    """Open a folder in Windows Explorer."""
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(path)


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a Windows message box."""
    ctypes.windll.user32.MessageBoxW(0, message, title, icon)
