"""macOS-specific desktop utilities."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .logging import log_buffer
from .paths import APP_CACHE_DIR, ROBLOX_PROCESS, ROBLOX_STUDIO_PROCESS, STORAGE_DB, STORAGE_DB_GDK, USER_HOME


ROBLOX_APP_CANDIDATES = (
    Path('/Applications/Roblox.app'),
    USER_HOME / 'Applications' / 'Roblox.app',
)
ROBLOX_STUDIO_APP_CANDIDATES = (
    Path('/Applications/RobloxStudio.app'),
    USER_HOME / 'Applications' / 'RobloxStudio.app',
)

_NS_APPLICATION_ACTIVATION_POLICY_REGULAR = 0
_NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1


def set_application_icon(icon_path: Path) -> bool:
    """Set the Dock tile image from Fleasion's transparent runtime icon."""
    try:
        icon_path = Path(icon_path)
        if not icon_path.is_file():
            log_buffer.log('App', f'macOS application icon not found: {icon_path}')
            return False

        appkit_path = ctypes.util.find_library('AppKit') or '/System/Library/Frameworks/AppKit.framework/AppKit'
        ctypes.CDLL(appkit_path)

        objc_path = ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib'
        objc = ctypes.CDLL(objc_path)
        objc_get_class = objc.objc_getClass
        objc_get_class.argtypes = [ctypes.c_char_p]
        objc_get_class.restype = ctypes.c_void_p
        sel_register_name = objc.sel_registerName
        sel_register_name.argtypes = [ctypes.c_char_p]
        sel_register_name.restype = ctypes.c_void_p

        msg_send_object = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(('objc_msgSend', objc))
        msg_send_cstring = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_char_p,
        )(('objc_msgSend', objc))
        msg_send_object_arg = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(('objc_msgSend', objc))
        msg_send_void_object = ctypes.CFUNCTYPE(
            None,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(('objc_msgSend', objc))

        ns_application = objc_get_class(b'NSApplication')
        ns_string = objc_get_class(b'NSString')
        ns_image = objc_get_class(b'NSImage')
        shared_application = msg_send_object(
            ns_application,
            sel_register_name(b'sharedApplication'),
        )

        icon_path_string = msg_send_cstring(
            ns_string,
            sel_register_name(b'stringWithUTF8String:'),
            str(icon_path).encode('utf-8'),
        )
        if not icon_path_string:
            log_buffer.log('App', f'Failed to create NSString for macOS application icon: {icon_path}')
            return False

        image_alloc = msg_send_object(ns_image, sel_register_name(b'alloc'))
        image = msg_send_object_arg(
            image_alloc,
            sel_register_name(b'initWithContentsOfFile:'),
            icon_path_string,
        )
        if not image:
            log_buffer.log('App', f'Failed to load macOS application icon image: {icon_path}')
            return False

        msg_send_void_object(
            shared_application,
            sel_register_name(b'setApplicationIconImage:'),
            image,
        )
        return True
    except Exception as exc:
        log_buffer.log('App', f'Failed to update macOS application icon: {type(exc).__name__}: {exc}')
        return False


def set_application_foreground_mode(enabled: bool) -> bool:
    """Show normal app windows while active, or return to menu-bar-only mode."""
    try:
        objc_path = ctypes.util.find_library('objc') or '/usr/lib/libobjc.A.dylib'
        objc = ctypes.CDLL(objc_path)
        objc_get_class = objc.objc_getClass
        objc_get_class.argtypes = [ctypes.c_char_p]
        objc_get_class.restype = ctypes.c_void_p
        sel_register_name = objc.sel_registerName
        sel_register_name.argtypes = [ctypes.c_char_p]
        sel_register_name.restype = ctypes.c_void_p

        msg_send_object = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(('objc_msgSend', objc))
        msg_send_policy = ctypes.CFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_long,
        )(('objc_msgSend', objc))
        msg_send_integer = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(('objc_msgSend', objc))

        ns_application = objc_get_class(b'NSApplication')
        shared_application = msg_send_object(
            ns_application,
            sel_register_name(b'sharedApplication'),
        )
        policy = (
            _NS_APPLICATION_ACTIVATION_POLICY_REGULAR
            if enabled
            else _NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY
        )
        if msg_send_integer(
            shared_application,
            sel_register_name(b'activationPolicy'),
        ) == policy:
            return True
        return bool(
            msg_send_policy(
                shared_application,
                sel_register_name(b'setActivationPolicy:'),
                policy,
            )
        )
    except Exception as exc:
        log_buffer.log('App', f'Failed to update macOS activation policy: {type(exc).__name__}: {exc}')
        return False


def run_cmd(args: list[str]) -> str:
    """Run a command and return stdout."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    ).stdout


def _process_pids(name: str) -> list[int]:
    try:
        result = subprocess.run(
            ['pgrep', '-x', name],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    pids: list[int] = []
    for raw in result.stdout.splitlines():
        try:
            pids.append(int(raw.strip()))
        except ValueError:
            pass
    return pids


def _first_process_pid(name: str) -> int | None:
    pids = _process_pids(name)
    return pids[0] if pids else None


def _app_executable(app_path: Path, executable_name: str) -> Path:
    return app_path / 'Contents' / 'MacOS' / executable_name


def _app_resources(app_path: Path) -> Path:
    return app_path / 'Contents' / 'Resources'


def _resource_root_from_executable(exe_path: Path) -> Path | None:
    try:
        macos_dir = exe_path.parent
        contents_dir = macos_dir.parent
        resources = contents_dir / 'Resources'
        if macos_dir.name == 'MacOS' and resources.is_dir():
            return resources
    except Exception:
        pass
    return None


def _known_player_executable() -> Path | None:
    for app_path in ROBLOX_APP_CANDIDATES:
        exe = _app_executable(app_path, ROBLOX_PROCESS)
        if exe.is_file():
            return exe
    return None


def _known_studio_executable() -> Path | None:
    for app_path in ROBLOX_STUDIO_APP_CANDIDATES:
        exe = _app_executable(app_path, ROBLOX_STUDIO_PROCESS)
        if exe.is_file():
            return exe
    return None


def _process_command(pid: int) -> Path | None:
    try:
        result = subprocess.run(
            ['ps', '-p', str(pid), '-o', 'comm='],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


def _quit_app_bundle(app_path: Path) -> bool:
    """Ask a macOS app bundle to quit via AppleScript."""
    app_name = app_path.stem
    try:
        result = subprocess.run(
            ['osascript', '-e', f'tell application "{app_name}" to quit'],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        log_buffer.log('App', f'Failed to request macOS quit for {app_name}: {type(exc).__name__}: {exc}')
        return False

    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        log_buffer.log('App', f'macOS quit request for {app_name} failed: {err or result.returncode}')
        return False
    return True


def wait_for_roblox_window(timeout: float = 60.0) -> bool:
    """Wait until Roblox's player process is running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_roblox_running():
            return True
        time.sleep(0.25)
    return False


def is_roblox_running() -> bool:
    """Check if Roblox Player is currently running."""
    return _first_process_pid(ROBLOX_PROCESS) is not None


def is_studio_running() -> bool:
    """Check if Roblox Studio is currently running."""
    return _first_process_pid(ROBLOX_STUDIO_PROCESS) is not None


def get_roblox_player_exe_path() -> Optional[Path]:
    """Return the running or installed Roblox Player executable path."""
    pid = _first_process_pid(ROBLOX_PROCESS)
    if pid is not None:
        command = _process_command(pid)
        if command and command.is_file():
            return command
    return _known_player_executable()


def get_roblox_studio_exe_path() -> Optional[Path]:
    """Return the running or installed Roblox Studio executable path."""
    pid = _first_process_pid(ROBLOX_STUDIO_PROCESS)
    if pid is not None:
        command = _process_command(pid)
        if command and command.is_file():
            return command
    return _known_studio_executable()


def terminate_roblox() -> bool:
    """Terminate Roblox if it is running. Returns True if it was running."""
    if not is_roblox_running():
        return False

    for app_path in ROBLOX_APP_CANDIDATES:
        if app_path.exists():
            _quit_app_bundle(app_path)
            break

    try:
        subprocess.run(['pkill', '-TERM', '-x', ROBLOX_PROCESS], capture_output=True, timeout=5)
    except Exception:
        pass

    deadline = time.time() + 2.0
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.1)

    try:
        subprocess.run(['pkill', '-KILL', '-x', ROBLOX_PROCESS], capture_output=True, timeout=5)
    except Exception:
        pass
    return not is_roblox_running()


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Roblox to exit. Returns True if it exited before timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)
    return False


def _delete_path(path: Path, messages: list[str], label: str) -> None:
    if not path.exists():
        messages.append(f'{label} not found')
        return
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        messages.append(f'{label} deleted successfully')
    except PermissionError:
        messages.append(f'Failed to delete {label.lower()}: permission denied')
    except OSError as exc:
        messages.append(f'Failed to delete {label.lower()}: {exc}')


def delete_cache() -> list[str]:
    """Delete Roblox cache files and Fleasion's converted-object cache."""
    messages: list[str] = []

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

    _delete_path(STORAGE_DB, messages, 'Storage database')
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(STORAGE_DB) + suffix)
        if sidecar.exists():
            _delete_path(sidecar, messages, f'Storage database {suffix}')

    if STORAGE_DB_GDK.parent.exists():
        _delete_path(STORAGE_DB_GDK, messages, 'Storage database (GDK)')

    storage_folder = STORAGE_DB.parent / 'rbx-storage'
    _delete_path(storage_folder, messages, 'Storage folder')

    if APP_CACHE_DIR.exists():
        try:
            preserve = {APP_CACHE_DIR / 'predownloaded'}
            for child in APP_CACHE_DIR.iterdir():
                if child in preserve:
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            messages.append('Fleasion obj cache deleted successfully')
        except PermissionError:
            messages.append('Failed to delete obj cache: permission denied')
        except OSError as exc:
            messages.append(f'Failed to delete obj cache: {exc}')

    return messages


def find_roblox_resource_dirs(include_studio: bool = True) -> list[Path]:
    """Return Roblox resource roots used by patch/modification code."""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path | None) -> None:
        if path is None or not path.is_dir():
            return
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    for app_path in ROBLOX_APP_CANDIDATES:
        exe = _app_executable(app_path, ROBLOX_PROCESS)
        resources = _app_resources(app_path)
        if exe.is_file() and resources.is_dir():
            _add(resources)

    if include_studio:
        for app_path in ROBLOX_STUDIO_APP_CANDIDATES:
            exe = _app_executable(app_path, ROBLOX_STUDIO_PROCESS)
            resources = _app_resources(app_path)
            if exe.is_file() and resources.is_dir():
                _add(resources)

    for exe_path in (get_roblox_player_exe_path(), get_roblox_studio_exe_path() if include_studio else None):
        if exe_path is not None:
            _add(_resource_root_from_executable(exe_path))

    return found


def resolve_roblox_player_exe_for_launch() -> Optional[Path]:
    """Return the Roblox Player executable path used for launch fallbacks."""
    return get_roblox_player_exe_path()


def _app_for_executable(path: Path) -> Path | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part.endswith('.app'):
            app = Path(*parts[:index + 1])
            return app if app.exists() else None
    return None


_DETACHED_POPEN_KWARGS = {
    'stdin': subprocess.DEVNULL,
    'stdout': subprocess.DEVNULL,
    'stderr': subprocess.DEVNULL,
    'start_new_session': True,
}


def _detached_popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, **_DETACHED_POPEN_KWARGS)


def launch_as_standard_user(target: str | Path) -> bool:
    """Launch a Roblox URI, app bundle, or executable without elevation."""
    target_str = str(target)
    try:
        if target_str.startswith(('roblox:', 'roblox-player:')):
            _detached_popen(['open', target_str])
            return True

        path = Path(target_str)
        if path.suffix == '.app' and path.exists():
            _detached_popen(['open', str(path)])
            return True

        if path.exists():
            app = _app_for_executable(path)
            if app is not None:
                _detached_popen(['open', str(app)])
            else:
                _detached_popen(['open', str(path)])
            return True
    except Exception as exc:
        log_buffer.log('Launch', f'Failed to launch {target_str}: {exc}')
        return False

    log_buffer.log('Launch', f'Launch target not found: {target_str}')
    return False


def open_folder(path: Path):
    """Open a folder in Finder."""
    _detached_popen(['open', str(path)])


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a simple macOS alert."""
    script = (
        'display alert '
        + json.dumps(title)
        + ' message '
        + json.dumps(message)
    )
    try:
        subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
    except Exception:
        log_buffer.log('UI', f'{title}: {message}')
