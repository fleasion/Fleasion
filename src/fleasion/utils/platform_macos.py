"""macOS-specific desktop utilities."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from .logging import log_buffer
from .paths import (
    APP_CACHE_DIR,
    ROBLOX_PROCESS,
    ROBLOX_STUDIO_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    USER_HOME,
)

ROBLOX_APP_CANDIDATES = (
    Path('/Applications/Roblox.app'),
    USER_HOME / 'Applications' / 'Roblox.app',
)
ROBLOX_STUDIO_APP_CANDIDATES = (
    Path('/Applications/RobloxStudio.app'),
    USER_HOME / 'Applications' / 'RobloxStudio.app',
)
FROSTSTRAP_VERSIONS_DIR = (
    USER_HOME / 'Library' / 'Application Support' / 'Froststrap' / 'Versions'
)
FROSTSTRAP_MOD_BACKUP_DIR = (
    USER_HOME / 'Library' / 'Application Support' / 'Froststrap' / 'ModBackup'
)
APPLEBLOX_DATA_DIR = USER_HOME / 'Library' / 'Application Support' / 'AppleBlox'
APPLEBLOX_ROBLOX_CONFIG = APPLEBLOX_DATA_DIR / 'config' / 'roblox.json'
APPLEBLOX_MOD_BACKUP_RESOURCES = APPLEBLOX_DATA_DIR / 'cache' / 'mods' / 'Resources'

_ENV_PROXY_RELAUNCH_TTL_SECONDS = 45.0
_LOCAL_PROXY_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost'})
_env_proxy_relaunch_lock = threading.Lock()
_env_proxy_relaunch_in_progress = False
_env_proxy_relaunch_at: float | None = None

_NS_APPLICATION_ACTIVATION_POLICY_REGULAR = 0
_NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1


def _appleblox_custom_app_path() -> Path | None:
    """Return AppleBlox's configured Roblox bundle, when present and valid."""
    try:
        payload = json.loads(APPLEBLOX_ROBLOX_CONFIG.read_text(encoding='utf-8'))
        raw_path = payload.get('installation', {}).get('custom_path')
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        app_path = Path(raw_path).expanduser()
        return app_path if app_path.suffix == '.app' else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _froststrap_player_apps() -> list[Path]:
    if not FROSTSTRAP_VERSIONS_DIR.is_dir():
        return []
    apps = list(FROSTSTRAP_VERSIONS_DIR.glob('version-*/RobloxPlayer.app'))
    try:
        apps.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:
        apps.sort(reverse=True)
    return apps


def set_application_icon(icon_path: Path) -> bool:
    """Set the Dock tile image from Fleasion's transparent runtime icon."""
    try:
        icon_path = Path(icon_path)
        if not icon_path.is_file():
            log_buffer.log('App', f'macOS application icon not found: {icon_path}')
            return False

        appkit_path = (
            ctypes.util.find_library('AppKit')
            or '/System/Library/Frameworks/AppKit.framework/AppKit'
        )
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
            log_buffer.log(
                'App',
                f'Failed to create NSString for macOS application icon: {icon_path}',
            )
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
        log_buffer.log(
            'App',
            f'Failed to update macOS application icon: {type(exc).__name__}: {exc}',
        )
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
        if (
            msg_send_integer(
                shared_application,
                sel_register_name(b'activationPolicy'),
            )
            == policy
        ):
            return True
        return bool(
            msg_send_policy(
                shared_application,
                sel_register_name(b'setActivationPolicy:'),
                policy,
            )
        )
    except Exception as exc:
        log_buffer.log(
            'App',
            f'Failed to update macOS activation policy: {type(exc).__name__}: {exc}',
        )
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
    candidates = list(ROBLOX_APP_CANDIDATES)
    custom_appleblox_app = _appleblox_custom_app_path()
    if custom_appleblox_app is not None:
        candidates.append(custom_appleblox_app)
    candidates.extend(_froststrap_player_apps())
    for app_path in candidates:
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
        log_buffer.log(
            'App',
            f'Failed to request macOS quit for {app_name}: {type(exc).__name__}: {exc}',
        )
        return False

    if result.returncode != 0:
        err = (result.stderr or result.stdout or '').strip()
        log_buffer.log(
            'App',
            f'macOS quit request for {app_name} failed: {err or result.returncode}',
        )
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


def find_appleblox_mod_backup_resource_dirs() -> list[Path]:
    """Return AppleBlox's live Resources snapshot, if its mod cycle created one."""
    backup = APPLEBLOX_MOD_BACKUP_RESOURCES
    if not backup.is_dir():
        return []
    # AppleBlox copies the complete Resources tree and later replaces the live
    # app Resources directory with it. Require characteristic Roblox content
    # so an unrelated directory cannot enter Fleasion's modification set.
    if not ((backup / 'ssl' / 'cacert.pem').is_file() or (backup / 'content').is_dir()):
        return []
    return [backup]


def find_froststrap_mod_backup_resource_dirs() -> list[Path]:
    """Return Froststrap's complete per-version Resources snapshots."""
    root = FROSTSTRAP_MOD_BACKUP_DIR
    if not root.is_dir():
        return []

    backups: list[Path] = []
    for backup in root.glob('version-*'):
        if not backup.is_dir():
            continue
        # Froststrap copies the *contents* of Resources directly into the
        # version directory, then restores individual former mod-manifest
        # entries from it. Require characteristic Roblox content so an
        # unrelated directory cannot enter Fleasion's modification set.
        if not ((backup / 'ssl' / 'cacert.pem').is_file() or (backup / 'content').is_dir()):
            continue
        backups.append(backup)

    try:
        backups.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:
        backups.sort(reverse=True)
    return backups


def find_bootstrapper_restore_resource_dirs() -> list[Path]:
    """Return user-owned Resources snapshots that bootstrapper code restores."""
    return [
        *find_appleblox_mod_backup_resource_dirs(),
        *find_froststrap_mod_backup_resource_dirs(),
    ]


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

    custom_appleblox_app = _appleblox_custom_app_path()
    if custom_appleblox_app is not None:
        exe = _app_executable(custom_appleblox_app, ROBLOX_PROCESS)
        resources = _app_resources(custom_appleblox_app)
        if exe.is_file() and resources.is_dir():
            _add(resources)

    for app_path in _froststrap_player_apps():
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

    for exe_path in (
        get_roblox_player_exe_path(),
        get_roblox_studio_exe_path() if include_studio else None,
    ):
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
            app = Path(*parts[: index + 1])
            return app if app.exists() else None
    return None


def _wait_for_local_proxy(proxy_url: str, timeout: float = 10.0) -> bool:
    """Wait until Fleasion's local explicit proxy accepts connections."""
    try:
        parsed = urlsplit(proxy_url)
        host = parsed.hostname
        port = parsed.port
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


def _claim_env_proxy_relaunch() -> bool:
    """Allow only one macOS env-proxy relaunch per launch window."""
    global _env_proxy_relaunch_in_progress

    now = time.monotonic()
    with _env_proxy_relaunch_lock:
        if _env_proxy_relaunch_in_progress:
            return False
        if (
            _env_proxy_relaunch_at is not None
            and now - _env_proxy_relaunch_at < _ENV_PROXY_RELAUNCH_TTL_SECONDS
        ):
            return False
        _env_proxy_relaunch_in_progress = True
        return True


def _finish_env_proxy_relaunch(success: bool) -> None:
    global _env_proxy_relaunch_at, _env_proxy_relaunch_in_progress

    with _env_proxy_relaunch_lock:
        _env_proxy_relaunch_in_progress = False
        if success:
            _env_proxy_relaunch_at = time.monotonic()


_DETACHED_POPEN_KWARGS = {
    'stdin': subprocess.DEVNULL,
    'stdout': subprocess.DEVNULL,
    'stderr': subprocess.DEVNULL,
    'start_new_session': True,
}


def _detached_popen(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen(args, **_DETACHED_POPEN_KWARGS)


def relaunch_roblox_with_proxy_env(proxy_url: str) -> bool:
    """Relaunch the running macOS Roblox Player through Fleasion's env proxy.

    LaunchServices does not retrofit environment variables onto an existing
    application process. Stop the browser/bootstrapper-started player first,
    then use ``open --env`` so the newly launched app inherits the conventional
    proxy variables while retaining normal macOS bundle launch behavior.
    """
    exe_path = get_roblox_player_exe_path()
    app_path = _app_for_executable(exe_path) if exe_path is not None else None
    if exe_path is None or app_path is None:
        log_buffer.log(
            'Launcher',
            'Roblox Env Proxy relaunch skipped: no macOS Roblox app bundle found',
        )
        return False

    if not _claim_env_proxy_relaunch():
        log_buffer.log('Launcher', 'Roblox Env Proxy relaunch already handled for this launch')
        return False

    success = False
    try:
        if not _wait_for_local_proxy(proxy_url):
            log_buffer.log(
                'Launcher',
                f'Roblox Env Proxy relaunch skipped: local proxy is not ready at {proxy_url}',
            )
            return False

        log_buffer.log(
            'Launcher',
            f'Relaunching Roblox through Fleasion env proxy: {app_path}',
        )
        if is_roblox_running():
            terminate_roblox()
            if not wait_for_roblox_exit():
                log_buffer.log(
                    'Launcher',
                    'Roblox did not exit before macOS env-proxy relaunch',
                )
                return False

        proxy_env = {
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
        open_args = ['open']
        for key, value in proxy_env.items():
            open_args.extend(['--env', f'{key}={value}'])
        open_args.extend(['-a', str(app_path)])

        launch_error = ''
        for attempt in range(3):
            launch_result = subprocess.run(
                open_args,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=10,
            )
            if launch_result.returncode == 0:
                break
            launch_error = (launch_result.stderr or launch_result.stdout or '').strip()
            if '-600' not in launch_error or attempt == 2:
                log_buffer.log(
                    'Launcher',
                    f'Roblox Env Proxy relaunch failed: '
                    f'{launch_error or launch_result.returncode}',
                )
                return False
            time.sleep(0.5)
        else:
            log_buffer.log(
                'Launcher',
                f'Roblox Env Proxy relaunch failed: {launch_error or "LaunchServices error"}',
            )
            return False
        if not wait_for_roblox_window(timeout=15.0):
            log_buffer.log(
                'Launcher',
                'Roblox Env Proxy relaunch failed: Roblox process did not start',
            )
            return False
        success = True
    except (OSError, subprocess.SubprocessError) as exc:
        log_buffer.log('Launcher', f'Roblox Env Proxy relaunch failed: {exc}')
        return False
    finally:
        _finish_env_proxy_relaunch(success)

    log_buffer.log('Launcher', 'Relaunched Roblox through Fleasion env proxy on macOS')
    return True


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
    script = 'display alert ' + json.dumps(title) + ' message ' + json.dumps(message)
    try:
        subprocess.run(['osascript', '-e', script], capture_output=True, timeout=10)
    except Exception:
        log_buffer.log('UI', f'{title}: {message}')
