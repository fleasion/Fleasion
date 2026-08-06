"""Windows-specific utilities."""

import ctypes
import ctypes.wintypes
import hashlib
import os
import re
import socket
import stat
import subprocess
import threading
import time
import winreg
from pathlib import Path
from typing import Any, Optional, cast
from urllib.parse import parse_qs, unquote, urlparse, urlsplit

from .logging import log_buffer
from .paths import LOCAL_APPDATA, ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS, STORAGE_DB, STORAGE_DB_GDK

_ENV_PROXY_RELAUNCH_TTL_SECONDS = 45.0
_env_proxy_relaunches: dict[str, float] = {}
_env_proxy_owned_process: tuple[int, str] | None = None
_LOCAL_PROXY_HOSTS = {'127.0.0.1', 'localhost', '::1'}


def _wait_for_local_proxy(proxy_url: str, timeout: float = 10.0) -> bool:
    try:
        parsed = urlsplit(proxy_url)
        host, port = parsed.hostname, parsed.port
    except ValueError:
        return False
    if host not in _LOCAL_PROXY_HOSTS:
        return True
    if port is None:
        return False
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = deadline - time.monotonic()
        if remaining < 0:
            return False
        try:
            with socket.create_connection(
                (host, port), timeout=min(0.5, max(0.1, remaining))
            ):
                return True
        except OSError:
            if remaining <= 0:
                return False
            time.sleep(min(0.1, remaining))


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
        ('dwSize', ctypes.wintypes.DWORD),
        ('cntUsage', ctypes.wintypes.DWORD),
        ('th32ProcessID', ctypes.wintypes.DWORD),
        ('th32DefaultHeapID', ctypes.c_size_t),  # ULONG_PTR — 8 bytes on x64
        ('th32ModuleID', ctypes.wintypes.DWORD),
        ('cntThreads', ctypes.wintypes.DWORD),
        ('th32ParentProcessID', ctypes.wintypes.DWORD),
        ('pcPriClassBase', ctypes.c_long),
        ('dwFlags', ctypes.wintypes.DWORD),
        ('szExeFile', ctypes.c_char * 260),
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
                yield (
                    entry.th32ProcessID,
                    entry.szExeFile.decode('utf-8', errors='replace').lower(),
                )
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


_WM_CLOSE = 0x0010
_FILE_ATTRIBUTE_READONLY = 0x00000001
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def _request_process_window_close(pid: int) -> bool:
    """Ask every top-level window owned by *pid* to close normally."""
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [_WNDENUMPROC, ctypes.wintypes.LPARAM]
    user32.EnumWindows.restype = ctypes.wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.POINTER(ctypes.wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
    user32.PostMessageW.argtypes = [
        ctypes.wintypes.HWND,
        ctypes.wintypes.UINT,
        ctypes.wintypes.WPARAM,
        ctypes.wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = ctypes.wintypes.BOOL
    requested = False

    def _cb(hwnd, _):
        nonlocal requested
        window_pid = ctypes.wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == pid and user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
            requested = True
        return True

    user32.EnumWindows(_WNDENUMPROC(_cb), 0)
    return requested


def _get_windows_file_attributes(path: Path) -> int | None:
    kernel32 = ctypes.windll.kernel32
    kernel32.GetFileAttributesW.argtypes = [ctypes.wintypes.LPCWSTR]
    kernel32.GetFileAttributesW.restype = ctypes.wintypes.DWORD
    value = int(kernel32.GetFileAttributesW(str(path)))
    return None if value == _INVALID_FILE_ATTRIBUTES else value


def _set_windows_file_attributes(path: Path, attributes: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetFileAttributesW.argtypes = [
        ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD,
    ]
    kernel32.SetFileAttributesW.restype = ctypes.wintypes.BOOL
    return bool(kernel32.SetFileAttributesW(str(path), attributes))


def _pid_is_running(pid: int, exe_name: str) -> bool:
    target = exe_name.casefold()
    return any(process_pid == pid and name == target for process_pid, name in _iter_processes())


def _wait_for_pid_exit(
    pid: int,
    exe_name: str,
    timeout: float,
    cancel_event: threading.Event | None = None,
) -> bool:
    deadline = time.monotonic() + max(0.0, timeout)
    while _pid_is_running(pid, exe_name):
        if cancel_event is not None and cancel_event.is_set():
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.2)
    return True


def _guarded_force_close_process_for_env_relaunch(
    pid: int,
    exe_name: str,
    *,
    cancel_event: threading.Event | None = None,
) -> bool | None:
    """Force-close Env-owned Player while protecting its local session state."""
    if cancel_event is not None and cancel_event.is_set():
        return False

    state_file = Path(LOCAL_APPDATA) / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
    original_attributes = _get_windows_file_attributes(state_file)
    if original_attributes is None:
        log_buffer.log(
            'Launcher',
            'Roblox relaunch state guard is unavailable; using a normal window close',
        )
        return None

    guarded_attributes = original_attributes | _FILE_ATTRIBUTE_READONLY
    if not _set_windows_file_attributes(state_file, guarded_attributes):
        log_buffer.log(
            'Launcher',
            'Roblox relaunch state guard could not be armed; using a normal window close',
        )
        return None

    exited = False
    restored = False
    try:
        log_buffer.log('Launcher', 'Roblox relaunch state guard armed for exact Player exit')
        run_cmd(['taskkill', '/F', '/PID', str(pid)])
        exited = _wait_for_pid_exit(pid, exe_name, 15.0)
    except Exception as exc:
        log_buffer.log(
            'Launcher',
            f'Roblox guarded restart request failed: {type(exc).__name__}',
        )
    finally:
        restored = _set_windows_file_attributes(state_file, original_attributes)
        if restored:
            log_buffer.log('Launcher', 'Roblox relaunch state guard restored exact attributes')
        else:
            log_buffer.log(
                'Launcher',
                'Roblox relaunch state guard could not restore exact attributes; refusing relaunch',
            )

    return bool(exited and restored)


def _close_process_for_env_relaunch(
    pid: int,
    exe_name: str,
    *,
    label: str,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Close the exact Player without an unguarded forced exit."""
    guarded_result = _guarded_force_close_process_for_env_relaunch(
        pid,
        exe_name,
        cancel_event=cancel_event,
    )
    if guarded_result is not None:
        if not guarded_result:
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy relaunch failed during guarded Player exit',
            )
        return guarded_result

    window_ready = wait_for_roblox_window(
        timeout=60.0,
        pid=pid,
        cancel_event=cancel_event,
    )
    if not window_ready:
        if cancel_event is not None and cancel_event.is_set():
            return False
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy relaunch skipped: Player window did not appear',
        )
        return False

    if not _request_process_window_close(pid):
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy relaunch skipped: normal Player close was unavailable',
        )
        return False
    if _wait_for_pid_exit(pid, exe_name, 20.0, cancel_event):
        return True

    log_buffer.log(
        'Launcher',
        f'{label} Env Proxy relaunch skipped: Player did not close normally',
    )
    return False


def wait_for_roblox_window(
    timeout: float = 60.0,
    *,
    pid: int | None = None,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Wait until RobloxPlayerBeta has a visible top-level window."""
    user32 = ctypes.windll.user32
    deadline = time.time() + timeout
    while time.time() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return False
        target_pid = pid if pid is not None else _find_pid(ROBLOX_PROCESS)
        if target_pid is not None:
            found = []

            def _cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    lp = ctypes.wintypes.DWORD(0)
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(lp))
                    if lp.value == target_pid:
                        found.append(hwnd)
                        return False
                return True

            user32.EnumWindows(_WNDENUMPROC(_cb), 0)
            if found:
                return True
        if cancel_event is None:
            time.sleep(0.25)
        elif cancel_event.wait(0.25):
            return False
    return False


def is_roblox_running() -> bool:
    """Check if Roblox is currently running."""
    return _find_pid(ROBLOX_PROCESS) is not None


def get_roblox_process_identity() -> tuple[int, str] | None:
    """Return a stable-enough token for the current Player process."""
    if _env_proxy_owned_process is not None:
        owned_pid, owned_path = _env_proxy_owned_process
        if _pid_is_running(owned_pid, ROBLOX_PROCESS):
            return owned_pid, owned_path
    pid = _find_pid(ROBLOX_PROCESS)
    if pid is None:
        return None
    exe_path = _query_exe_path(pid)
    return pid, str(exe_path or '')


def is_studio_running() -> bool:
    """Check if Roblox Studio is currently running."""
    return _find_pid(ROBLOX_STUDIO_PROCESS) is not None


def get_roblox_player_exe_path() -> Optional[Path]:
    """Return the full executable path of the running RobloxPlayerBeta.exe, or None."""
    pid = _find_pid(ROBLOX_PROCESS)
    return _query_exe_path(pid) if pid is not None else None


def _query_roblox_processes(exe_name: str) -> list[dict[str, Any]]:
    ps_script = (
        f"$p=Get-CimInstance Win32_Process -Filter \"Name='{exe_name}'\" | "
        "Select-Object ProcessId,ExecutablePath,CommandLine; "
        "if($p){$p|ConvertTo-Json -Compress}"
    )
    try:
        result = subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                ps_script,
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except Exception as exc:
        log_buffer.log('Launcher', f'Could not query Roblox command line: {exc}')
        return []
    if result.returncode != 0 or not (result.stdout or '').strip():
        return []
    try:
        import json

        data = json.loads(result.stdout)
    except Exception as exc:
        log_buffer.log('Launcher', f'Could not parse Roblox command line query: {exc}')
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [entry for entry in data if isinstance(entry, dict)]
    return []


def _query_roblox_player_processes() -> list[dict[str, Any]]:
    return _query_roblox_processes('RobloxPlayerBeta.exe')


def _extract_roblox_deeplink(command_line: str, marker: str = 'roblox-player:') -> str:
    """Return a Roblox launch URI from a Windows process command line."""
    markers = (marker,)
    if marker == 'roblox-player:':
        markers = ('roblox-player:', 'roblox://', 'roblox:')
    lowered = command_line.lower()
    offsets = [lowered.find(candidate.lower()) for candidate in markers]
    offsets = [offset for offset in offsets if offset >= 0]
    if not offsets:
        return ''
    return command_line[min(offsets) :].strip().strip('"')


def _relaunch_roblox_exe_with_proxy_env(
    proxy_url: str,
    *,
    label: str,
    query_processes,
    extract_launch_arg,
    wait_pid_exe_name: str,
    fallback_exe_path,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Relaunch a browser/shortcut-started Roblox process with proxy environment variables."""
    global _env_proxy_owned_process
    now = time.monotonic()
    for key, timestamp in list(_env_proxy_relaunches.items()):
        if now - timestamp > _ENV_PROXY_RELAUNCH_TTL_SECONDS:
            _env_proxy_relaunches.pop(key, None)

    for proc in query_processes():
        try:
            pid = int(proc.get('ProcessId') or 0)
        except TypeError, ValueError:
            pid = 0
        command_line = str(proc.get('CommandLine') or '')
        launch_arg = extract_launch_arg(command_line)
        exe_text = str(proc.get('ExecutablePath') or '')
        exe_path = Path(exe_text) if exe_text else fallback_exe_path()
        if pid <= 0 or exe_path is None or not exe_path.is_file():
            continue

        relaunch_key = hashlib.sha256(
            f'{str(exe_path).casefold()}\n{launch_arg or "<no-arg>"}'.encode(
                'utf-8', errors='replace'
            )
        ).hexdigest()
        if not force and relaunch_key in _env_proxy_relaunches:
            log_buffer.log(
                'Launcher', f'{label} Env Proxy relaunch already handled for this launch'
            )
            return False

        if not launch_arg:
            launch_kind = 'plain executable'
        else:
            launch_kind = 'deeplink'
        log_buffer.log(
            'Launcher',
            f'Relaunching {label} through Fleasion env proxy ({launch_kind}): {exe_path}',
        )
        if not _wait_for_local_proxy(proxy_url):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy relaunch skipped: local proxy is not ready at {proxy_url}',
            )
            return False
        if cancel_event is not None and cancel_event.is_set():
            return False
        if not _close_process_for_env_relaunch(
            pid,
            wait_pid_exe_name,
            label=label,
            cancel_event=cancel_event,
        ):
            return False

        env = os.environ.copy()
        env.update(
            {
                'ALL_PROXY': proxy_url,
                'HTTPS_PROXY': proxy_url,
                'HTTP_PROXY': proxy_url,
                'all_proxy': proxy_url,
                'https_proxy': proxy_url,
                'http_proxy': proxy_url,
                'NO_PROXY': 'localhost,127.0.0.1,::1',
                'no_proxy': 'localhost,127.0.0.1,::1',
                'FLEASION_PROXY_RELAUNCHED': '1',
            }
        )
        try:
            if cancel_event is not None and cancel_event.is_set():
                return False
            args = [str(exe_path), launch_arg] if launch_arg else [str(exe_path)]
            replacement = subprocess.Popen(
                args,
                cwd=str(exe_path.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            _env_proxy_owned_process = (int(replacement.pid), str(exe_path))
        except OSError as exc:
            log_buffer.log('Launcher', f'{label} Env Proxy relaunch failed: {exc}')
            return False
        launch_deadline = time.monotonic() + 15.0
        while time.monotonic() < launch_deadline:
            if cancel_event is not None and cancel_event.is_set():
                return False
            replacement_pid = _find_pid(wait_pid_exe_name)
            if replacement_pid is not None and replacement_pid != pid:
                break
            time.sleep(0.2)
        else:
            log_buffer.log(
                'Launcher', f'{label} Env Proxy relaunch failed: replacement process did not start'
            )
            return False
        _env_proxy_relaunches[relaunch_key] = time.monotonic()
        return True

    log_buffer.log('Launcher', f'{label} Env Proxy relaunch skipped: no {label} executable found')
    return False


def relaunch_roblox_with_proxy_env(
    proxy_url: str,
    *,
    force: bool = False,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Relaunch a browser-started Roblox Player process with proxy environment variables."""
    return _relaunch_roblox_exe_with_proxy_env(
        proxy_url,
        label='Roblox',
        query_processes=_query_roblox_player_processes,
        extract_launch_arg=lambda cmd: _extract_roblox_deeplink(cmd, 'roblox-player:'),
        wait_pid_exe_name=ROBLOX_PROCESS,
        fallback_exe_path=get_roblox_player_exe_path,
        force=force,
        cancel_event=cancel_event,
    )


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


def close_roblox_for_env_lifecycle() -> bool:
    """Close Env-owned Player normally, with the guarded exact-PID fallback."""
    global _env_proxy_owned_process

    pid = (
        _env_proxy_owned_process[0]
        if _env_proxy_owned_process is not None
        else _find_pid(ROBLOX_PROCESS)
    )
    if pid is None:
        return False

    if _request_process_window_close(pid) and _wait_for_pid_exit(
        pid,
        ROBLOX_PROCESS,
        20.0,
    ):
        _env_proxy_owned_process = None
        return True

    guarded_result = _guarded_force_close_process_for_env_relaunch(
        pid,
        ROBLOX_PROCESS,
    )
    if guarded_result is None:
        log_buffer.log(
            'Launcher',
            'Env-owned Roblox Player could not be closed without an unguarded forced exit',
        )
        return False
    if guarded_result:
        _env_proxy_owned_process = None
    return guarded_result


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Roblox to exit. Returns True if it exited before timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)
    return False


def _clear_read_only(path: Path) -> None:
    """Clear the read-only attribute on an existing path."""
    if not path.exists():
        return
    current_mode = path.stat().st_mode
    if current_mode & stat.S_IWRITE:
        return
    path.chmod(current_mode | stat.S_IWRITE)


def _rmtree_clear_readonly_retry(func, path: str, _exc_info) -> None:
    """Allow shutil.rmtree() to retry after clearing a read-only attribute."""
    target = Path(path)
    _clear_read_only(target)
    func(path)


def _delete_db_file(db_path: Path, messages: list, label: str = 'Storage database') -> None:
    """Delete a single rbx-storage.db file, attempting win32 unlock on PermissionError."""
    if not db_path.exists():
        messages.append(f'{label} not found')
        return
    try:
        _clear_read_only(db_path)
        db_path.unlink()
        messages.append(f'{label} deleted successfully')
    except PermissionError:
        messages.append(f'{label}: Permission denied - attempting to unlock...')
        try:
            import pywintypes
            import win32con
            import win32file

            try:
                handle = win32file.CreateFile(
                    str(db_path),
                    win32con.GENERIC_READ | win32con.GENERIC_WRITE,
                    win32con.FILE_SHARE_DELETE,
                    None,
                    win32con.OPEN_EXISTING,
                    0,
                    None,
                )
                win32file.CloseHandle(cast(int, handle))
            except pywintypes.error:
                pass

            _clear_read_only(db_path)
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
            shutil.rmtree(storage_folder, onerror=_rmtree_clear_readonly_retry)
            messages.append('Storage folder deleted successfully')
        except PermissionError:
            messages.append('Failed to delete storage folder: Permission denied')
        except OSError as e:
            messages.append(f'Failed to delete storage folder: {e}')
    else:
        messages.append('Storage folder not found')

    # Delete Fleasion APP_CACHE_DIR (preserve predownloaded assets only)
    from .paths import APP_CACHE_DIR

    if APP_CACHE_DIR.exists():
        try:
            _preserve_set = {APP_CACHE_DIR / 'predownloaded'}
            for child in APP_CACHE_DIR.iterdir():
                if child in _preserve_set:
                    continue
                if child.is_dir():
                    shutil.rmtree(child, onerror=_rmtree_clear_readonly_retry)
                else:
                    _clear_read_only(child)
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
    match = re.match(r'(.+?\.exe)(?:["\s]|$)', command, re.IGNORECASE)
    if match:
        exe_path = match.group(1).strip('"')
    else:
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = []
        exe_path = parts[0].strip('"') if parts else command.split()[0]
    if not exe_path:
        return None
    return Path(exe_path)


def _is_roblox_player_exe_path(path: Path) -> bool:
    """Return True only for the Roblox player executable, never installers."""
    return path.name.lower() == ROBLOX_PROCESS.lower()


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
    candidates_by_path: dict[str, tuple[int, float, Path]] = {}

    def _add(path: Path, priority: int) -> None:
        if not _is_roblox_player_exe_path(path):
            return
        if not path.is_file():
            return
        key = str(path).lower()
        candidate = (priority, _safe_mtime(path), path)
        existing = candidates_by_path.get(key)
        if existing is not None and (existing[0], existing[1]) >= (
            candidate[0],
            candidate[1],
        ):
            return
        candidates_by_path[key] = candidate

    # 1) Running client path. Useful for custom installs, but do not let a stale
    # running version outrank the current LocalAppData install.
    running_exe = get_roblox_player_exe_path()
    if running_exe is not None:
        _add(running_exe, 250)

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

    if not candidates_by_path:
        return None
    candidates = list(candidates_by_path.values())
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
        log_buffer.log(
            'Launcher',
            'Direct Roblox URI launch skipped: no Roblox executable resolved',
        )
        return False
    try:
        subprocess.Popen(
            [str(exe_path), target_str],
            cwd=str(exe_path.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
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


def _build_launch_command(
    target_str: str, prefer_direct_roblox_uri: bool = False
) -> tuple[str, Optional[str]]:
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
            log_buffer.log(
                'Launcher',
                'Roblox URI executable resolution failed; using protocol fallback',
            )
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        rundll = system_root / 'System32' / 'rundll32.exe'
        cmdline = f'"{rundll}" url.dll,FileProtocolHandler "{target_str}"'
        return cmdline, None

    target_path = Path(target_str)
    if target_path.is_dir():
        system_root = Path(os.environ.get('SystemRoot', r'C:\Windows'))
        explorer = system_root / 'explorer.exe'
        return f'"{explorer}" "{target_str}"', str(target_path)

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
            _TOKEN_ASSIGN_PRIMARY
            | _TOKEN_DUPLICATE
            | _TOKEN_QUERY
            | _TOKEN_ADJUST_DEFAULT
            | _TOKEN_ADJUST_SESSIONID
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
        log_buffer.log(
            'Launcher',
            f'Launch request (Roblox URI): {_format_launch_metadata(launch_meta)}',
        )
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

            log_buffer.log(
                'Launcher',
                'Protocol launch failed to start Roblox; falling back to direct executable launch',
            )
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

        log_buffer.log(
            'Launcher',
            'Protocol launch failed to start Roblox; falling back to direct executable launch',
        )
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
        log_buffer.log(
            'Launcher',
            f'Elevated shell launch failed, falling back to os.startfile: {target_str}',
        )

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
    if _is_process_elevated() and launch_as_standard_user(path):
        return
    os.startfile(path)


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a Windows message box."""
    ctypes.windll.user32.MessageBoxW(0, message, title, icon)
