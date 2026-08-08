"""Windows-specific utilities."""

import ctypes
import ctypes.wintypes
import hashlib
import os
import re
import stat
import subprocess
import sys
import threading
import time
import winreg
import xml.etree.ElementTree as ET
import uuid
from pathlib import Path
from typing import Any, Callable, Optional, cast
from urllib.parse import parse_qs, unquote, urlparse

from .logging import log_buffer
from .paths import LOCAL_APPDATA, ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS, STORAGE_DB, STORAGE_DB_GDK

_ENV_PROXY_RELAUNCH_TTL_SECONDS = 45.0
_env_proxy_relaunches: dict[str, float] = {}
_env_proxy_owned_process: tuple[int, str] | None = None
_env_proxy_gdk_activation_in_progress = False
_gdk_env_proxy_armed_package: tuple[str, str] | None = None

_COINIT_APARTMENTTHREADED = 0x2
_COINIT_CHANGED_MODE = -2147417850  # RPC_E_CHANGED_MODE
_CLSCTX_ALL = 0x17
_AO_NONE = 0
_THREAD_SUSPEND_RESUME = 0x0002
_GDK_DEBUGGER_SWITCH = '--fleasion-gdk-debugger'


class _GUID(ctypes.Structure):
    _fields_ = [
        ('Data1', ctypes.wintypes.DWORD),
        ('Data2', ctypes.wintypes.WORD),
        ('Data3', ctypes.wintypes.WORD),
        ('Data4', ctypes.wintypes.BYTE * 8),
    ]


def _guid(value: str) -> _GUID:
    parsed = uuid.UUID(value)
    data1, data2, data3 = (
        int.from_bytes(parsed.bytes_le[0:4], 'little'),
        int.from_bytes(parsed.bytes_le[4:6], 'little'),
        int.from_bytes(parsed.bytes_le[6:8], 'little'),
    )
    return _GUID(data1, data2, data3, (ctypes.wintypes.BYTE * 8).from_buffer_copy(parsed.bytes[8:]))


_CLSID_PACKAGE_DEBUG_SETTINGS = _guid('B1AEC16F-2383-4852-B0E9-8F0B1DC66B4D')
_IID_PACKAGE_DEBUG_SETTINGS = _guid('F27C3930-8029-4AD1-94E3-3DBA417810C1')
_CLSID_APPLICATION_ACTIVATION_MANAGER = _guid('45BA127D-10A8-46EA-8AB7-56EA9078943C')
_IID_APPLICATION_ACTIVATION_MANAGER = _guid('2E941141-7F97-4756-BA1D-9DECDE894A3D')


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


def run_gdk_debugger_command_line(arguments: list[str] | None = None) -> int:
    """Resume the suspended package thread used by GDK activation.

    ``IPackageDebugSettings.EnableDebugging`` starts the activated package
    suspended and invokes the configured debugger with ``-p <pid> -tid
    <thread-id>``.  This intentionally tiny debugger command is also exposed
    through the main executable so the standalone build does not need another
    shipped binary.
    """
    args = list(arguments if arguments is not None else sys.argv[1:])
    try:
        thread_id_index = next(
            index
            for index, value in enumerate(args)
            if value.casefold() in {'-tid', '--tid'}
        )
        thread_id = int(args[thread_id_index + 1])
    except (StopIteration, IndexError, TypeError, ValueError):
        return 2
    if thread_id <= 0:
        return 2

    kernel32 = ctypes.windll.kernel32
    kernel32.OpenThread.argtypes = [
        ctypes.wintypes.DWORD,
        ctypes.wintypes.BOOL,
        ctypes.wintypes.DWORD,
    ]
    kernel32.OpenThread.restype = ctypes.wintypes.HANDLE
    kernel32.ResumeThread.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.ResumeThread.restype = ctypes.wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.wintypes.BOOL

    thread_handle = kernel32.OpenThread(_THREAD_SUSPEND_RESUME, False, thread_id)
    if not thread_handle:
        return 1
    try:
        previous_count = kernel32.ResumeThread(thread_handle)
        if previous_count == 0xFFFFFFFF:
            return 1
        # A debugger can be attached while another diagnostic tool has also
        # suspended the thread.  Fully resume it without looping forever.
        for _ in range(min(int(previous_count), 8)):
            if kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
                return 1
        return 0
    finally:
        kernel32.CloseHandle(thread_handle)


def _gdk_debugger_command_line() -> str:
    """Return the command line Windows will use for the package dummy debugger."""
    if getattr(sys, 'frozen', False):
        command = [sys.executable, _GDK_DEBUGGER_SWITCH]
    else:
        command = [sys.executable, '-m', 'fleasion.app', _GDK_DEBUGGER_SWITCH]
    return subprocess.list2cmdline(command)


def _hresult_failed(value: int) -> bool:
    return int(value) < 0


def _format_hresult(value: int) -> str:
    return f'0x{int(value) & 0xFFFFFFFF:08X}'


def _com_method(interface: ctypes.c_void_p, index: int, restype, *argtypes):
    vtable = ctypes.cast(
        interface,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
    ).contents
    function_type = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    return function_type(vtable[index])


def _create_com_instance(clsid: _GUID, iid: _GUID) -> ctypes.c_void_p | None:
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(_GUID),
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.POINTER(_GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    interface = ctypes.c_void_p()
    result = ole32.CoCreateInstance(
        ctypes.byref(clsid),
        None,
        _CLSCTX_ALL,
        ctypes.byref(iid),
        ctypes.byref(interface),
    )
    if _hresult_failed(result):
        return None
    return interface


def _release_com_instance(interface: ctypes.c_void_p | None) -> None:
    if interface:
        _com_method(interface, 2, ctypes.c_ulong)(interface)


def _package_environment_block(environment: dict[str, str]) -> ctypes.Array:
    """Build the double-NUL-terminated UTF-16 block required by PZZWSTR."""
    entries = [f'{key}={value}' for key, value in sorted(environment.items()) if '\x00' not in key and '\x00' not in value]
    return ctypes.create_unicode_buffer('\x00'.join(entries) + '\x00')


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


def _pid_is_running(pid: int, exe_name: str) -> bool:
    target = exe_name.casefold()
    return any(process_pid == pid and name == target for process_pid, name in _iter_processes())


def _env_proxy_owned_pid_if_running() -> int | None:
    """Return the PID of the current Env Proxy-owned Player, if it is alive."""
    global _env_proxy_owned_process

    if _env_proxy_owned_process is None:
        return None
    owned_pid = _env_proxy_owned_process[0]
    if _pid_is_running(owned_pid, ROBLOX_PROCESS):
        return owned_pid
    # A crashed/failed proxy relaunch must not poison the next real launch.
    _env_proxy_owned_process = None
    return None


def is_env_proxy_relaunched_player_running() -> bool:
    """Return whether the current Player is the process Fleasion relaunched."""
    owned_pid = _env_proxy_owned_pid_if_running()
    return owned_pid is not None and _find_pid(ROBLOX_PROCESS) == owned_pid


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
        # Poll tightly after taskkill; this is only process-exit detection, not
        # a grace period for Roblox to finish writing session state.
        time.sleep(0.01)
    return True


def _force_close_process_immediately(
    pid: int,
    exe_name: str,
    *,
    label: str,
    timeout: float = 8.0,
    cancel_event: threading.Event | None = None,
) -> bool:
    """Kill the exact Player immediately, then wait only for process exit."""
    if cancel_event is not None and cancel_event.is_set():
        return False

    try:
        log_buffer.log('Launcher', f'{label} forcing exact Player exit immediately')
        run_cmd(['taskkill', '/F', '/PID', str(pid)])
        return _wait_for_pid_exit(pid, exe_name, timeout, cancel_event)
    except Exception as exc:
        log_buffer.log(
            'Launcher',
            f'{label} forced Player exit failed: {type(exc).__name__}',
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


def is_roblox_gdk_exe_path(exe_path: Path | str | None) -> bool:
    """Return whether *exe_path* is the Xbox/Store GDK Roblox client.

    The GDK client is an AppX/Xbox-launched game.  Its package launcher does
    not propagate a caller's scoped environment to the Player child, so Env
    Proxy uses package-aware activation for it instead of a direct child launch.
    """
    if not exe_path:
        return False
    normalized = str(exe_path).replace('/', '\\').casefold()
    return (
        ('\\windowsapps\\' in normalized and 'robloxgdk' in normalized)
        or normalized.endswith('\\xboxgames\\roblox\\content\\robloxplayerbeta.exe')
    )


def is_gdk_env_proxy_activation_in_progress() -> bool:
    """Return whether Fleasion is currently activating a GDK client with Env Proxy."""
    return _env_proxy_gdk_activation_in_progress


def _get_roblox_gdk_package_identity(exe_path: Path) -> tuple[str, str] | None:
    """Return ``(package_full_name, AUMID)`` for a packaged Roblox executable."""
    package_root = exe_path.parent
    package_full_name = package_root.name
    if not package_full_name or '__' not in package_full_name:
        return None

    try:
        package_name = package_full_name.split('_', 1)[0]
        publisher_id = package_full_name.split('__', 1)[1]
        application_id = 'Game'
        manifest = ET.parse(package_root / 'AppxManifest.xml').getroot()
        for application in manifest.iter():
            if application.tag.rsplit('}', 1)[-1] != 'Application':
                continue
            candidate = application.attrib.get('Id')
            executable = application.attrib.get('Executable', '')
            if candidate and (candidate == 'Game' or executable.casefold() == 'gamelaunchhelper.exe'):
                application_id = candidate
                break
        return package_full_name, f'{package_name}_{publisher_id}!{application_id}'
    except (ET.ParseError, OSError, UnicodeError, ValueError):
        return None


def _find_installed_roblox_gdk_package_identity() -> tuple[str, str] | None:
    """Find the installed Store package before its Player process exists."""
    try:
        result = subprocess.run(
            [
                'powershell.exe',
                '-NoProfile',
                '-NonInteractive',
                '-ExecutionPolicy',
                'Bypass',
                '-Command',
                "(Get-AppxPackage -Name 'ROBLOXCorporation.RobloxGDK' | "
                "Sort-Object Version -Descending | Select-Object -First 1 -ExpandProperty InstallLocation)",
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    install_location = next(
        (line.strip() for line in result.stdout.splitlines() if line.strip()),
        '',
    )
    if result.returncode != 0 or not install_location:
        return None
    return _get_roblox_gdk_package_identity(Path(install_location) / ROBLOX_PROCESS)


def _enable_gdk_package_debugging(
    package_full_name: str,
    environment_block: ctypes.Array,
    debugger_command_line: str,
    label: str,
) -> bool:
    """Arm package-aware activation and immediately release temporary COM state."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    init_result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if _hresult_failed(init_result) and int(init_result) != _COINIT_CHANGED_MODE:
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy GDK activation could not initialize COM: {_format_hresult(init_result)}',
        )
        return False
    com_initialized = not _hresult_failed(init_result)
    debug_settings = None
    try:
        debug_settings = _create_com_instance(
            _CLSID_PACKAGE_DEBUG_SETTINGS,
            _IID_PACKAGE_DEBUG_SETTINGS,
        )
        if debug_settings is None:
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK activation could not create PackageDebugSettings',
            )
            return False
        enable_debugging = _com_method(
            debug_settings,
            3,
            ctypes.c_long,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.c_void_p,
        )
        result = enable_debugging(
            debug_settings,
            package_full_name,
            debugger_command_line,
            ctypes.cast(environment_block, ctypes.c_void_p),
        )
        if _hresult_failed(result):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK activation could not enable package debugging: {_format_hresult(result)}',
            )
            return False
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy GDK activation failed while enabling package debugging: '
            f'{type(exc).__name__}: {exc}',
        )
        return False
    finally:
        _release_com_instance(debug_settings)
        if com_initialized:
            ole32.CoUninitialize()


def _disable_gdk_package_debugging(package_full_name: str, label: str) -> bool:
    """Remove package debugging after Env Proxy is no longer active."""
    ole32 = ctypes.windll.ole32
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    init_result = ole32.CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
    if _hresult_failed(init_result) and int(init_result) != _COINIT_CHANGED_MODE:
        return False
    com_initialized = not _hresult_failed(init_result)
    debug_settings = None
    try:
        debug_settings = _create_com_instance(
            _CLSID_PACKAGE_DEBUG_SETTINGS,
            _IID_PACKAGE_DEBUG_SETTINGS,
        )
        if debug_settings is None:
            return False
        disable_debugging = _com_method(
            debug_settings,
            4,
            ctypes.c_long,
            ctypes.c_wchar_p,
        )
        result = disable_debugging(debug_settings, package_full_name)
        if _hresult_failed(result):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK package debugging cleanup failed: {_format_hresult(result)}',
            )
            return False
        return True
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy GDK package debugging cleanup failed: {type(exc).__name__}: {exc}',
        )
        return False
    finally:
        _release_com_instance(debug_settings)
        if com_initialized:
            ole32.CoUninitialize()


def is_roblox_gdk_env_proxy_armed() -> bool:
    """Return whether future Store Roblox activations inherit Env Proxy."""
    return _gdk_env_proxy_armed_package is not None


def arm_roblox_gdk_env_proxy(proxy_url: str) -> bool:
    """Arm package-aware Env Proxy before the user activates Store Roblox."""
    global _gdk_env_proxy_armed_package

    identity = _find_installed_roblox_gdk_package_identity()
    if identity is None:
        return False
    if _gdk_env_proxy_armed_package == identity:
        return True
    if _gdk_env_proxy_armed_package is not None:
        disarm_roblox_gdk_env_proxy()

    if not _enable_gdk_package_debugging(
        identity[0],
        _package_environment_block(_proxy_environment(proxy_url)),
        _gdk_debugger_command_line(),
        'Roblox',
    ):
        return False
    _gdk_env_proxy_armed_package = identity
    log_buffer.log(
        'Launcher',
        f'Xbox/GDK Env Proxy package activation armed before launch: {identity[1]}',
    )
    return True


def disarm_roblox_gdk_env_proxy() -> None:
    """Restore normal Store package activation after Env Proxy shuts down."""
    global _gdk_env_proxy_armed_package

    identity = _gdk_env_proxy_armed_package
    _gdk_env_proxy_armed_package = None
    if identity is not None:
        _disable_gdk_package_debugging(identity[0], 'Roblox')


def _proxy_environment(proxy_url: str) -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
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
    return environment


def _activate_roblox_gdk_with_proxy_env(
    proxy_url: str,
    *,
    label: str,
    pid: int,
    exe_path: Path,
    launch_arg: str,
    query_processes,
    prepare_launch: Callable[[Path], bool] | None,
    cancel_event: threading.Event | None,
) -> tuple[int, str] | None:
    """Activate the Store package with a real package-scoped environment block."""
    global _env_proxy_gdk_activation_in_progress

    identity = _get_roblox_gdk_package_identity(exe_path)
    if identity is None and is_roblox_gdk_exe_path(exe_path):
        # Xbox/GDK may expose the active executable through the user-facing
        # C:\XboxGames tree instead of its WindowsApps package root. Resolve
        # the registered package for repair/relaunch activation in that case.
        identity = _find_installed_roblox_gdk_package_identity()
    if identity is None:
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy relaunch skipped: could not read the Xbox/GDK package manifest',
        )
        return None
    package_full_name, app_user_model_id = identity
    environment = _proxy_environment(proxy_url)
    environment_block = _package_environment_block(environment)
    debugger_command_line = _gdk_debugger_command_line()

    if cancel_event is not None and cancel_event.is_set():
        return None

    _env_proxy_gdk_activation_in_progress = True
    activation_manager = None
    debugging_enabled = False
    try:
        package_is_armed = _gdk_env_proxy_armed_package == identity
        if not package_is_armed:
            if not _enable_gdk_package_debugging(
                package_full_name,
                environment_block,
                debugger_command_line,
                label,
            ):
                return None
            debugging_enabled = True

        if not _pid_is_running(pid, ROBLOX_PROCESS):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK activation continuing after the initial Player exited',
            )

        log_buffer.log(
            'Launcher',
            f'Relaunching {label} through Fleasion env proxy (Xbox/GDK package activation): {app_user_model_id}',
        )
        if not _force_close_process_immediately(
            pid,
            ROBLOX_PROCESS,
            label=label,
            timeout=8.0,
            cancel_event=cancel_event,
        ):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK activation aborted: the original Player did not exit',
            )
            return None

        if prepare_launch is not None:
            try:
                if not prepare_launch(exe_path):
                    log_buffer.log(
                        'Launcher',
                        f'{label} Env Proxy GDK activation skipped: launch CA preparation failed',
                    )
                    return None
                related_exes: list[Path] = []
                package_exe = (
                    Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
                    / 'WindowsApps'
                    / package_full_name
                    / ROBLOX_PROCESS
                )
                xbox_exe = Path(r'C:\XboxGames\Roblox\Content') / ROBLOX_PROCESS
                for candidate in (package_exe, xbox_exe):
                    if (
                        candidate.is_file()
                        and str(candidate).casefold() != str(exe_path).casefold()
                        and str(candidate).casefold()
                        not in {str(path).casefold() for path in related_exes}
                    ):
                        related_exes.append(candidate)
                for related_exe in related_exes:
                    if not prepare_launch(related_exe):
                        log_buffer.log(
                            'Launcher',
                            f'{label} Env Proxy GDK launch CA preparation failed: {related_exe}',
                        )
                        return None
            except Exception as exc:
                log_buffer.log(
                    'Launcher',
                    f'{label} Env Proxy GDK launch CA preparation failed: {type(exc).__name__}: {exc}',
                )
                return None

        activation_manager = _create_com_instance(
            _CLSID_APPLICATION_ACTIVATION_MANAGER,
            _IID_APPLICATION_ACTIVATION_MANAGER,
        )
        if activation_manager is None:
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK activation could not create ApplicationActivationManager',
            )
            return None

        activate_application = _com_method(
            activation_manager,
            3,
            ctypes.c_long,
            ctypes.c_wchar_p,
            ctypes.c_wchar_p,
            ctypes.wintypes.DWORD,
            ctypes.POINTER(ctypes.wintypes.DWORD),
        )
        activated_pid = ctypes.wintypes.DWORD(0)
        result = activate_application(
            activation_manager,
            app_user_model_id,
            launch_arg or None,
            _AO_NONE,
            ctypes.byref(activated_pid),
        )
        if _hresult_failed(result):
            log_buffer.log(
                'Launcher',
                f'{label} Env Proxy GDK package activation failed: {_format_hresult(result)}',
            )
            return None

        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                return None
            for process in query_processes():
                try:
                    candidate_pid = int(process.get('ProcessId') or 0)
                except (TypeError, ValueError):
                    continue
                candidate_path = Path(str(process.get('ExecutablePath') or ''))
                if (
                    candidate_pid > 0
                    and candidate_pid != pid
                    and is_roblox_gdk_exe_path(candidate_path)
                    and candidate_path.name.casefold() == ROBLOX_PROCESS.casefold()
                ):
                    return candidate_pid, str(candidate_path)
            time.sleep(0.2)

        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy GDK activation returned PID {activated_pid.value} but no Player process appeared',
        )
        return None
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        log_buffer.log(
            'Launcher',
            f'{label} Env Proxy GDK activation failed: {type(exc).__name__}: {exc}',
        )
        return None
    finally:
        if debugging_enabled:
            _disable_gdk_package_debugging(package_full_name, label)
        _release_com_instance(activation_manager)
        _env_proxy_gdk_activation_in_progress = False


def get_roblox_process_identity() -> tuple[int, str] | None:
    """Return a stable-enough token for the current Player process."""
    if _env_proxy_owned_process is not None:
        owned_pid = _env_proxy_owned_pid_if_running()
        if owned_pid is not None:
            return owned_pid, _env_proxy_owned_process[1]
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
            timeout=10,
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
    prepare_launch: Callable[[Path], bool] | None = None,
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
        except (TypeError, ValueError):
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
            owned_pid = _env_proxy_owned_pid_if_running()
            if owned_pid == pid:
                log_buffer.log(
                    'Launcher',
                    f'{label} Env Proxy relaunch already handled for this launch',
                )
                return False
            # The previous process is gone, or this is a new process generation
            # from the same executable. Do not carry a crashed launch's guard
            # into the next launch.
            _env_proxy_relaunches.pop(relaunch_key, None)

        if is_roblox_gdk_exe_path(exe_path):
            if not force:
                if (
                    is_roblox_gdk_env_proxy_armed()
                    or is_gdk_env_proxy_activation_in_progress()
                ):
                    _env_proxy_owned_process = (pid, str(exe_path))
                    _env_proxy_relaunches[relaunch_key] = time.monotonic()
                    log_buffer.log(
                        'Launcher',
                        f'{label} Env Proxy GDK package activation already supplied '
                        f'environment; adopting Player PID {pid} for CA monitoring',
                    )
                    return True
                log_buffer.log(
                    'Launcher',
                    f'{label} Env Proxy normal GDK relaunch suppressed; '
                    'package activation must remain untouched after launch',
                )
                return False
            if is_gdk_env_proxy_activation_in_progress():
                log_buffer.log(
                    'Launcher',
                    f'{label} Env Proxy GDK activation already in progress; skipping duplicate handling',
                )
                return False
            activated_process = _activate_roblox_gdk_with_proxy_env(
                proxy_url,
                label=label,
                pid=pid,
                exe_path=exe_path,
                launch_arg=launch_arg,
                query_processes=query_processes,
                prepare_launch=prepare_launch,
                cancel_event=cancel_event,
            )
            if activated_process is None:
                return False
            _env_proxy_owned_process = activated_process
            _env_proxy_relaunches[relaunch_key] = time.monotonic()
            return True

        if not launch_arg:
            launch_kind = 'plain executable'
        else:
            launch_kind = 'deeplink'
        log_buffer.log(
            'Launcher',
            f'Relaunching {label} through Fleasion env proxy ({launch_kind}): {exe_path}',
        )
        if cancel_event is not None and cancel_event.is_set():
            return False

        if not _force_close_process_immediately(
            pid,
            wait_pid_exe_name,
            label=label,
            timeout=8.0,
            cancel_event=cancel_event,
        ):
            return False

        # Fishstrap can finish replacing the active version after the first
        # Player process appears. Repair the bundle only now, after that
        # process is gone, so the replacement starts with the current CA.
        if prepare_launch is not None:
            try:
                if not prepare_launch(exe_path):
                    log_buffer.log(
                        'Launcher',
                        f'{label} Env Proxy relaunch skipped: launch CA preparation failed',
                    )
                    return False
            except Exception as exc:
                log_buffer.log(
                    'Launcher',
                    f'{label} Env Proxy launch CA preparation failed: {type(exc).__name__}: {exc}',
                )
                return False

        env = _proxy_environment(proxy_url)
        try:
            if cancel_event is not None and cancel_event.is_set():
                return False
            args = [str(exe_path), launch_arg] if launch_arg else [str(exe_path)]
            child = subprocess.Popen(
                args,
                cwd=str(exe_path.parent),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # The constant is only defined by the Windows build of
                # ``subprocess``.  Keeping the fallback at zero also lets
                # cross-platform lifecycle tests exercise the Windows launch
                # path without importing a host-specific constant.
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except OSError as exc:
            log_buffer.log('Launcher', f'{label} Env Proxy relaunch failed: {exc}')
            return False
        _env_proxy_owned_process = (int(child.pid), str(exe_path))
        _env_proxy_relaunches[relaunch_key] = time.monotonic()
        return True

    log_buffer.log('Launcher', f'{label} Env Proxy relaunch skipped: no {label} executable found')
    return False


def relaunch_roblox_with_proxy_env(
    proxy_url: str,
    *,
    force: bool = False,
    cancel_event: threading.Event | None = None,
    prepare_launch: Callable[[Path], bool] | None = None,
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
        prepare_launch=prepare_launch,
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
    """Close Env-owned Player normally, with an immediate exact-PID fallback."""
    global _env_proxy_owned_process

    pid = (
        _env_proxy_owned_process[0]
        if _env_proxy_owned_process is not None
        else _find_pid(ROBLOX_PROCESS)
    )
    if pid is None:
        return False

    exe_path = _query_exe_path(pid)
    if is_roblox_gdk_exe_path(exe_path):
        log_buffer.log(
            'Launcher',
            'Env Proxy lifecycle closing the owned Xbox/GDK Roblox client '
            'with the immediate exact-PID path',
        )

    if _request_process_window_close(pid) and _wait_for_pid_exit(
        pid,
        ROBLOX_PROCESS,
        20.0,
    ):
        _env_proxy_owned_process = None
        return True

    forced_result = _force_close_process_immediately(
        pid,
        ROBLOX_PROCESS,
        label='Roblox',
    )
    if forced_result:
        _env_proxy_owned_process = None
    return forced_result


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
