"""Windows-specific utilities."""

import ctypes
import ctypes.wintypes
import os
import re
import subprocess
import time
import winreg
from pathlib import Path
from typing import Optional, cast
from urllib.parse import parse_qs, unquote, urlparse

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
                win32file.CloseHandle(cast(int, handle))
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


def _is_roblox_launch_uri(target_str: str) -> bool:
    """Return True when target looks like a Roblox protocol URI."""
    lowered = target_str.lower()
    return lowered.startswith(('roblox://', 'roblox-player:', 'roblox:'))


def _extract_exe_from_command(command: str) -> Optional[Path]:
    """Extract executable path from a shell/open command string."""
    command = (command or '').replace('\x00', '').strip()
    if not command:
        return None
    if command.startswith('"'):
        end_quote = command.find('"', 1)
        if end_quote <= 1:
            return None
        exe_path = command[1:end_quote]
    else:
        exe_path = command.split()[0]
    if not exe_path:
        return None
    return Path(exe_path)


def _scan_for_player_exes(root: Path, max_depth: int) -> list[Path]:
    """Return Roblox player executables found under a root folder."""
    results: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        results.append(path)

    def _has_player(path: Path) -> bool:
        return (path / ROBLOX_PROCESS).is_file()

    if root.is_dir() and _has_player(root):
        _add(root / ROBLOX_PROCESS)

    def _recurse(path: Path, depth: int) -> None:
        try:
            for entry in os.scandir(path):
                if not entry.is_dir():
                    continue
                entry_path = Path(entry.path)
                if _has_player(entry_path):
                    _add(entry_path / ROBLOX_PROCESS)
                if depth < max_depth:
                    _recurse(entry_path, depth + 1)
        except OSError:
            pass

    if root.is_dir():
        _recurse(root, 0)
    return results


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _resolve_roblox_player_exe_for_launch() -> Optional[Path]:
    """Resolve best Roblox executable path for URI launches with fallbacks."""
    candidates: list[tuple[int, float, Path]] = []
    seen: set[str] = set()

    def _add(path: Path, priority: int) -> None:
        if not path.is_file():
            return
        key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        candidates.append((priority, _safe_mtime(path), path))

    # 1) Running client path (highest confidence)
    running_exe = get_roblox_player_exe_path()
    if running_exe is not None:
        _add(running_exe, 300)

    # 2) Registry shell/open command (lowest confidence; can be stale)
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Classes\roblox-player\shell\open\command',
        ) as key:
            command, _ = winreg.QueryValueEx(key, '')
            exe_path = _extract_exe_from_command(command)
            if exe_path is not None:
                _add(exe_path, 200)
    except OSError:
        pass

    # 3) %LocalAppData%\Roblox\Versions
    local_versions = Path(os.path.expandvars(r'%LocalAppData%')) / 'Roblox' / 'Versions'
    for exe_path in _scan_for_player_exes(local_versions, 1):
        _add(exe_path, 260)

    # 4) C:\Program Files (x86)\Roblox\Versions
    pf_versions = Path(r'C:\Program Files (x86)\Roblox\Versions')
    for exe_path in _scan_for_player_exes(pf_versions, 2):
        _add(exe_path, 240)

    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return candidates[0][2]


def resolve_roblox_player_exe_for_launch() -> Optional[Path]:
    """Public wrapper for Roblox executable resolution used by launch callers."""
    return _resolve_roblox_player_exe_for_launch()


def _extract_launch_metadata(target_str: str) -> dict[str, str]:
    """Extract place/game identifiers from Roblox launch targets for diagnostics."""
    if not _is_roblox_launch_uri(target_str):
        return {}

    metadata: dict[str, str] = {}
    keys = ('placeId', 'gameId', 'linkCode', 'accessCode')

    # Direct URI query parsing (roblox://...)
    try:
        parsed = urlparse(target_str)
        query = parse_qs(parsed.query)
        for key in keys:
            values = query.get(key)
            if values and values[0]:
                metadata[key] = values[0]
    except Exception:
        pass

    # Direct key=value scans (covers non-standard forms too)
    for key in keys:
        if key in metadata:
            continue
        m = re.search(rf'{re.escape(key)}=([^&+]+)', target_str, re.IGNORECASE)
        if m:
            metadata[key] = m.group(1)

    # roblox-player URI embeds encoded PlaceLauncher URL in placelauncherurl
    if 'placelauncherurl:' in target_str:
        encoded_url = target_str.split('placelauncherurl:', 1)[1].split('+', 1)[0]
        decoded_url = unquote(encoded_url)
        try:
            parsed = urlparse(decoded_url)
            query = parse_qs(parsed.query)
            for key in keys:
                if key in metadata:
                    continue
                values = query.get(key)
                if values and values[0]:
                    metadata[key] = values[0]
        except Exception:
            pass

    return metadata


def _format_launch_metadata(metadata: dict[str, str]) -> str:
    """Format launch metadata for concise logs."""
    if not metadata:
        return 'no identifiers parsed'
    ordered = ('placeId', 'gameId', 'linkCode', 'accessCode')
    parts = [f'{key}={metadata[key]}' for key in ordered if key in metadata]
    for key, value in metadata.items():
        if key not in ordered:
            parts.append(f'{key}={value}')
    return ', '.join(parts)


def _launch_roblox_uri_direct(target_str: str) -> bool:
    """Launch Roblox URI by executing resolved RobloxPlayerBeta.exe directly."""
    exe_path = _resolve_roblox_player_exe_for_launch()
    if exe_path is None:
        log_buffer.log('Launcher', 'Direct Roblox URI launch skipped: no Roblox executable resolved')
        return False
    try:
        subprocess.Popen(
            [str(exe_path), target_str],
            cwd=str(exe_path.parent),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        metadata = _extract_launch_metadata(target_str)
        log_buffer.log(
            'Launcher',
            f'Direct Roblox launch via {exe_path} ({_format_launch_metadata(metadata)})',
        )
        return True
    except OSError as exc:
        log_buffer.log('Launcher', f'Direct Roblox launch failed via {exe_path}: {exc}')
        return False


def _build_launch_command(target_str: str, prefer_direct_roblox_uri: bool = False) -> tuple[str, Optional[str]]:
    """Build command line + cwd for token-based process creation."""
    is_uri = '://' in target_str or target_str.startswith(('roblox-player:', 'roblox:'))
    if is_uri:
        if prefer_direct_roblox_uri and _is_roblox_launch_uri(target_str):
            exe_path = _resolve_roblox_player_exe_for_launch()
            if exe_path is not None:
                metadata = _extract_launch_metadata(target_str)
                log_buffer.log(
                    'Launcher',
                    f'Using direct executable for Roblox URI launch: {exe_path} ({_format_launch_metadata(metadata)})',
                )
                return f'"{exe_path}" "{target_str}"', str(exe_path.parent)
            log_buffer.log('Launcher', 'Roblox URI executable resolution failed; using protocol fallback')
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        rundll = system_root / 'System32' / 'rundll32.exe'
        cmdline = f'"{rundll}" url.dll,FileProtocolHandler "{target_str}"'
        return cmdline, None

    target_path = Path(target_str)
    cwd = str(target_path.parent) if target_path.exists() else None
    return f'"{target_str}"', cwd


def _launch_with_shell_token(target_str: str, prefer_direct_roblox_uri: bool = False) -> bool:
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

        cmdline, cwd = _build_launch_command(
            target_str,
            prefer_direct_roblox_uri=prefer_direct_roblox_uri,
        )
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


def _wait_for_roblox_process_start(timeout: float = 6.0) -> bool:
    """Wait briefly for RobloxPlayerBeta.exe to appear after a launch request."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_roblox_running():
            return True
        time.sleep(0.2)
    return is_roblox_running()


def launch_as_standard_user(target: str | Path) -> bool:
    """Launch a URI/path as a standard user when Fleasion is elevated."""
    target_str = str(target).strip()
    if not target_str:
        log_buffer.log('Launcher', 'Launch aborted: empty target')
        return False

    is_roblox_uri = _is_roblox_launch_uri(target_str)
    launch_meta = _extract_launch_metadata(target_str) if is_roblox_uri else {}
    if is_roblox_uri:
        log_buffer.log('Launcher', f'Launch request (Roblox URI): {_format_launch_metadata(launch_meta)}')
    else:
        log_buffer.log('Launcher', f'Launch request (path): {target_str}')

    was_running_before = is_roblox_running() if is_roblox_uri else False

    def _roblox_launch_confirmed(launch_started: bool, method: str) -> bool:
        if not launch_started:
            return False
        if was_running_before:
            log_buffer.log('Launcher', f'{method} dispatched while Roblox was already running')
            return True
        if _wait_for_roblox_process_start():
            log_buffer.log('Launcher', f'{method} confirmed Roblox process start')
            return True
        log_buffer.log('Launcher', f'{method} did not start Roblox process within timeout')
        return False

    if is_roblox_uri:
        # Protocol first, raw executable fallback only if protocol launch does not start Roblox.
        if _is_process_elevated():
            protocol_started = _launch_with_shell_token(target_str, prefer_direct_roblox_uri=False)
            if _roblox_launch_confirmed(protocol_started, 'Protocol launch (shell token)'):
                return True

            log_buffer.log('Launcher', 'Protocol launch failed to start Roblox; falling back to direct executable launch')
            direct_started = _launch_with_shell_token(target_str, prefer_direct_roblox_uri=True)
            if _roblox_launch_confirmed(direct_started, 'Direct executable launch (shell token)'):
                return True

            log_buffer.log('Launcher', 'Direct executable fallback via shell token failed')
            return False

        try:
            os.startfile(target_str)
            protocol_started = True
            log_buffer.log('Launcher', 'Protocol launch dispatched via os.startfile')
        except OSError as exc:
            protocol_started = False
            log_buffer.log('Launcher', f'Protocol launch via os.startfile failed: {exc}')

        if _roblox_launch_confirmed(protocol_started, 'Protocol launch (os.startfile)'):
            return True

        log_buffer.log('Launcher', 'Protocol launch failed to start Roblox; falling back to direct executable launch')
        direct_started = _launch_roblox_uri_direct(target_str)
        if _roblox_launch_confirmed(direct_started, 'Direct executable launch'):
            return True

        log_buffer.log('Launcher', 'Direct executable fallback failed')
        return False

    # os.startfile inherits the current process token. If Fleasion is elevated,
    # using it here would reintroduce the exact admin-inheritance bug.
    if _is_process_elevated():
        launched = _launch_with_shell_token(target_str, prefer_direct_roblox_uri=False)
        if launched:
            log_buffer.log('Launcher', 'Launch succeeded via shell token')
            return True
        log_buffer.log('Launcher', f'Elevated shell launch failed, falling back to os.startfile: {target_str}')

    try:
        os.startfile(target_str)
        log_buffer.log('Launcher', 'Launch succeeded via os.startfile')
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
