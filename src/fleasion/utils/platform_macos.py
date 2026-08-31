"""macOS-specific desktop utilities."""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import re
import select
import signal
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
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

# This module may be imported by cross-platform tests and tooling; keep its
# process names macOS-specific rather than inheriting the host platform's names.
ROBLOX_PROCESS = 'RobloxPlayer'
ROBLOX_STUDIO_PROCESS = 'RobloxStudio'

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
ROBLOX_PLAYER_LOG_DIR = USER_HOME / 'Library' / 'Logs' / 'Roblox'
APPLEBLOX_DATA_DIR = USER_HOME / 'Library' / 'Application Support' / 'AppleBlox'
# AppleBlox's settings layer always stores roblox.json under the normal macOS
# application-data path, even when getDataDir() is overridden for cache/mod data.
APPLEBLOX_ROBLOX_CONFIG = APPLEBLOX_DATA_DIR / 'config' / 'roblox.json'
APPLEBLOX_MOD_BACKUP_RESOURCES = APPLEBLOX_DATA_DIR / 'cache' / 'mods' / 'Resources'

_ENV_PROXY_RELAUNCH_TTL_SECONDS = 45.0
_LOCAL_PROXY_HOSTS = frozenset({'127.0.0.1', '::1', 'localhost'})
_URI_LOG_CLASSIFICATION_SECONDS = 2.0
_URI_PID_DISCOVERY_POLL_SECONDS = 0.01
_env_proxy_relaunch_lock = threading.Lock()
_env_proxy_relaunch_in_progress = False
_env_proxy_relaunch_at: float | None = None

_NS_APPLICATION_ACTIVATION_POLICY_REGULAR = 0
_NS_APPLICATION_ACTIVATION_POLICY_ACCESSORY = 1
_CF_STRING_ENCODING_UTF8 = 0x08000100


def _launch_services_framework():
    if sys.platform != 'darwin':
        return None
    try:
        return ctypes.CDLL(
            '/System/Library/Frameworks/CoreServices.framework/CoreServices'
        )
    except OSError:
        return None


def _cf_string(core_foundation, value: str):
    create = core_foundation.CFStringCreateWithCString
    create.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    create.restype = ctypes.c_void_p
    return create(None, value.encode('utf-8'), _CF_STRING_ENCODING_UTF8)


def _cf_string_value(core_foundation, value_ref) -> str | None:
    if not value_ref:
        return None
    get_cstring = core_foundation.CFStringGetCString
    get_cstring.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    get_cstring.restype = ctypes.c_bool
    buffer = ctypes.create_string_buffer(4096)
    if not get_cstring(value_ref, buffer, len(buffer), _CF_STRING_ENCODING_UTF8):
        return None
    return buffer.value.decode('utf-8', errors='replace')


def _cf_release(core_foundation, value_ref) -> None:
    if value_ref:
        release = core_foundation.CFRelease
        release.argtypes = [ctypes.c_void_p]
        release.restype = None
        release(value_ref)


def get_default_url_handler(scheme: str) -> str | None:
    """Return macOS's current preferred bundle for a URL scheme."""
    launch_services = _launch_services_framework()
    if launch_services is None:
        return None
    core_foundation = ctypes.CDLL(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
    )
    scheme_ref = _cf_string(core_foundation, scheme)
    if not scheme_ref:
        return None
    try:
        copy_default = launch_services.LSCopyDefaultHandlerForURLScheme
        copy_default.argtypes = [ctypes.c_void_p]
        copy_default.restype = ctypes.c_void_p
        handler_ref = copy_default(scheme_ref)
        try:
            return _cf_string_value(core_foundation, handler_ref)
        finally:
            _cf_release(core_foundation, handler_ref)
    except (AttributeError, OSError):
        return None
    finally:
        _cf_release(core_foundation, scheme_ref)


def set_default_url_handler(scheme: str, bundle_id: str) -> bool:
    """Set a macOS URL scheme's preferred bundle handler."""
    launch_services = _launch_services_framework()
    if launch_services is None:
        return False
    core_foundation = ctypes.CDLL(
        '/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation'
    )
    scheme_ref = _cf_string(core_foundation, scheme)
    bundle_ref = _cf_string(core_foundation, bundle_id)
    if not scheme_ref or not bundle_ref:
        _cf_release(core_foundation, scheme_ref)
        _cf_release(core_foundation, bundle_ref)
        return False
    try:
        set_default = launch_services.LSSetDefaultHandlerForURLScheme
        set_default.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        set_default.restype = ctypes.c_int32
        return set_default(scheme_ref, bundle_ref) == 0
    except (AttributeError, OSError):
        return False
    finally:
        _cf_release(core_foundation, scheme_ref)
        _cf_release(core_foundation, bundle_ref)


def appleblox_data_dir() -> Path:
    """Return AppleBlox's cache/mod data root when Fleasion can infer it safely.

    AppleBlox accepts ``APPLEBLOX_DATA_DIR`` as an override for getDataDir().
    Only absolute overrides are usable here: a relative path would be resolved
    against AppleBlox's working directory, which Fleasion cannot assume matches
    its own. CLI-only ``--data-dir`` overrides are intentionally not guessed
    from ``ps`` command text because paths containing spaces are ambiguous there.
    """
    raw_override = os.environ.get('APPLEBLOX_DATA_DIR', '').strip()
    if raw_override and '\x00' not in raw_override:
        try:
            override = Path(raw_override)
        except (TypeError, ValueError):
            override = None
        if override is not None and override.is_absolute():
            return override
    return APPLEBLOX_DATA_DIR


def _appleblox_custom_app_path() -> Path | None:
    """Return AppleBlox's configured Roblox bundle, when present and valid."""
    try:
        payload = json.loads(APPLEBLOX_ROBLOX_CONFIG.read_text(encoding='utf-8'))
        if not isinstance(payload, dict):
            return None
        installation = payload.get('installation')
        if not isinstance(installation, dict):
            return None
        raw_path = installation.get('custom_path')
        if not isinstance(raw_path, str) or not raw_path.strip() or '\x00' in raw_path:
            return None
        app_path = Path(raw_path).expanduser()
        return app_path if app_path.suffix == '.app' else None
    except (OSError, RuntimeError, ValueError, TypeError, json.JSONDecodeError):
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


class _IncrementalRobloxLaunchUriParser:
    """Recover a browser launch URI from appended Player.log bytes only.

    The combined URI line is truncated by current macOS Roblox builds, but the
    following ``Argument N`` records are complete.  Roblox emits its cold-launch
    marker after the final argument, which gives us an exact boundary without
    guessing how many arguments a future client version will use.
    """

    _COMPLETE_URI_RE = re.compile(r'^\s*"((?:roblox|roblox-player):[^"\r\n]+)"')
    _ARGUMENT_RE = re.compile(r'Argument (\d+) = (.+)$')

    def __init__(self) -> None:
        self._partial_line = ''
        self._inside_open_urls = False
        self._arguments: dict[int, str] = {}

    def feed(self, data: bytes) -> str | None:
        """Consume appended bytes and return a complete URI once, if present."""
        if not data:
            return None
        text = self._partial_line + data.decode('utf-8', errors='replace')
        lines = text.splitlines(keepends=True)
        self._partial_line = ''
        if lines and not lines[-1].endswith(('\n', '\r')):
            self._partial_line = lines.pop()

        for raw_line in lines:
            target = self._consume_line(raw_line.rstrip('\r\n'))
            if target:
                return target
        return None

    def _consume_line(self, line: str) -> str | None:
        if 'application:openURLs:' in line:
            self._inside_open_urls = True
            self._arguments.clear()
            return None
        if not self._inside_open_urls:
            return None

        complete_uri = self._COMPLETE_URI_RE.match(line)
        if complete_uri:
            return complete_uri.group(1).strip()

        argument = self._ARGUMENT_RE.search(line)
        if argument:
            self._arguments[int(argument.group(1))] = argument.group(2).strip()
            return None

        # Current Roblox writes this after every argument record for a browser
        # URI.  Waiting for it avoids replaying a prefix that omits a trailing
        # launch field such as LaunchExp.
        if 'application:openURLs cold launch' in line:
            return self._reconstructed_uri()
        return None

    def _reconstructed_uri(self) -> str | None:
        if not self._arguments:
            return None
        numbers = sorted(self._arguments)
        if numbers != list(range(1, numbers[-1] + 1)):
            return None
        parts = [self._arguments[number] for number in numbers]
        if not any(part.startswith('gameinfo:') for part in parts):
            return None
        if not any(part.startswith('placelauncherurl:') for part in parts):
            return None
        return 'roblox-player:1+' + '+'.join(parts)


@dataclass(frozen=True)
class MacOSRobloxPlayerLaunch:
    """The original Player identity captured before URI replay."""

    pid: int
    executable_path: Path
    app_path: Path
    log_path: Path
    detected_at: float


@dataclass
class _TrackedMacOSPlayerLog:
    path: Path
    fd: int
    launch: MacOSRobloxPlayerLaunch | None
    parser: _IncrementalRobloxLaunchUriParser
    opened_at: float
    offset: int = 0
    needs_read: bool = True


def _wait_for_pid_exit(pid: int, timeout: float = 3.0) -> bool:
    """Wait for one known PID without enumerating processes."""
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)


class MacOSRobloxUriInterceptor:
    """Watch new Player logs and convert URI launches before ticket redemption.

    Roblox remains the LaunchServices URI handler.  This watcher observes only
    its newly-created Player log, records the original PID and app bundle before
    parsing credentials, then keeps the URI-to-SIGKILL section deliberately
    minimal.
    """

    def __init__(
        self,
        *,
        is_armed: Callable[[], bool],
        on_intercepted: Callable[[MacOSRobloxPlayerLaunch, str], None],
        log_dir: Path | None = None,
    ) -> None:
        self._is_armed = is_armed
        self._on_intercepted = on_intercepted
        self._log_dir = Path(log_dir or ROBLOX_PLAYER_LOG_DIR)
        self._stop_event = threading.Event()
        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._known_logs: set[Path] = set()
        self._claimed_pid: int | None = None

    def start(self) -> bool:
        """Start one idle kqueue thread.  Unsupported hosts are a no-op."""
        if sys.platform != 'darwin' or not hasattr(select, 'kqueue'):
            return False
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop_event.clear()
            self._known_logs = self._player_logs()
            self._thread = threading.Thread(
                target=self._run,
                name='FleasionMacOSRobloxUriWatcher',
                daemon=True,
            )
            self._thread.start()
        log_buffer.log(
            'Launcher',
            'macOS Roblox URI watcher started; Roblox remains the URI handler',
        )
        return True

    def stop(self, timeout: float = 1.5) -> None:
        """Stop the watcher without leaving a background file descriptor open."""
        self._stop_event.set()
        with self._state_lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))

    def has_claimed_pid(self, pid: int) -> bool:
        """Return whether a URI handoff already owns this original PID."""
        with self._state_lock:
            return self._claimed_pid == pid

    def _player_logs(self) -> set[Path]:
        try:
            return {
                path
                for path in self._log_dir.glob('*_Player_*_last.log')
                if path.is_file()
            }
        except OSError:
            return set()

    @staticmethod
    def _vnode_event(fd: int):
        return select.kevent(
            fd,
            filter=select.KQ_FILTER_VNODE,
            flags=select.KQ_EV_ADD | select.KQ_EV_ENABLE | select.KQ_EV_CLEAR,
            fflags=(
                select.KQ_NOTE_WRITE
                | select.KQ_NOTE_EXTEND
                | select.KQ_NOTE_RENAME
                | select.KQ_NOTE_DELETE
            ),
        )

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                directory_fd = os.open(self._log_dir, os.O_RDONLY)
            except OSError:
                self._stop_event.wait(0.5)
                continue

            tracked: _TrackedMacOSPlayerLog | None = None
            queue = None
            try:
                queue = select.kqueue()
                queue.control([self._vnode_event(directory_fd)], 0, 0)
                tracked = self._discover_new_log(queue, tracked)
                while not self._stop_event.is_set():
                    timeout = (
                        _URI_PID_DISCOVERY_POLL_SECONDS
                        if tracked is not None and tracked.launch is None
                        else 0.5
                    )
                    events = queue.control(None, 8, timeout)
                    directory_changed = any(event.ident == directory_fd for event in events)
                    if directory_changed:
                        tracked = self._discover_new_log(queue, tracked)

                    if tracked is None:
                        continue
                    tracked_events = [event for event in events if event.ident == tracked.fd]
                    if tracked_events:
                        if any(
                            event.fflags & (select.KQ_NOTE_RENAME | select.KQ_NOTE_DELETE)
                            for event in tracked_events
                        ):
                            tracked = self._close_tracked(tracked)
                            continue
                        tracked.needs_read = True

                    if tracked.launch is None:
                        tracked.launch = self._capture_player_launch(tracked.path)
                    if tracked.launch is not None and tracked.needs_read:
                        target = self._read_appended_target(tracked)
                        if target:
                            self._intercept(tracked.launch, target)
                            target = ''
                            tracked = self._close_tracked(tracked)
                            continue
                    if time.monotonic() - tracked.opened_at >= _URI_LOG_CLASSIFICATION_SECONDS:
                        tracked = self._close_tracked(tracked)
            except (OSError, ValueError):
                # A Roblox updater can replace the log directory while the
                # watcher is armed.  Reopen it rather than retaining a stale FD.
                pass
            finally:
                if tracked is not None:
                    self._close_tracked(tracked)
                if queue is not None:
                    try:
                        queue.close()
                    except OSError:
                        pass
                try:
                    os.close(directory_fd)
                except OSError:
                    pass

    def _discover_new_log(
        self,
        queue,
        tracked: _TrackedMacOSPlayerLog | None,
    ) -> _TrackedMacOSPlayerLog | None:
        logs = self._player_logs()
        new_logs = logs - self._known_logs
        self._known_logs.update(logs)
        if tracked is not None or not new_logs or not self._is_armed():
            return tracked
        try:
            path = max(new_logs, key=lambda candidate: candidate.stat().st_mtime_ns)
            fd = os.open(path, os.O_RDONLY)
            queue.control([self._vnode_event(fd)], 0, 0)
        except (OSError, ValueError):
            return None
        return _TrackedMacOSPlayerLog(
            path=path,
            fd=fd,
            launch=self._capture_player_launch(path),
            parser=_IncrementalRobloxLaunchUriParser(),
            opened_at=time.monotonic(),
        )

    @staticmethod
    def _close_tracked(
        tracked: _TrackedMacOSPlayerLog | None,
    ) -> None:
        if tracked is None:
            return None
        try:
            os.close(tracked.fd)
        except OSError:
            pass
        return None

    def _capture_player_launch(self, log_path: Path) -> MacOSRobloxPlayerLaunch | None:
        """Resolve the original PID and bundle before touching URI log bytes."""
        pid = _first_process_pid(ROBLOX_PROCESS)
        if pid is None:
            return None
        executable = _process_command(pid)
        app_path = _app_for_executable(executable) if executable is not None else None
        if executable is None or app_path is None or not app_path.is_dir():
            return None
        return MacOSRobloxPlayerLaunch(
            pid=pid,
            executable_path=executable,
            app_path=app_path,
            log_path=log_path,
            detected_at=time.monotonic(),
        )

    def _read_appended_target(self, tracked: _TrackedMacOSPlayerLog) -> str | None:
        try:
            os.lseek(tracked.fd, tracked.offset, os.SEEK_SET)
            chunks: list[bytes] = []
            while True:
                chunk = os.read(tracked.fd, 65536)
                if not chunk:
                    break
                chunks.append(chunk)
                tracked.offset += len(chunk)
        except OSError:
            return None
        tracked.needs_read = False
        for chunk in chunks:
            target = tracked.parser.feed(chunk)
            if target:
                return target
        return None

    def _intercept(self, launch: MacOSRobloxPlayerLaunch, target: str) -> None:
        """Claim, kill, then hand off.  Never add work before ``os.kill``."""
        captured_at = time.perf_counter_ns()
        with self._state_lock:
            if self._claimed_pid is not None or not self._is_armed():
                return
            self._claimed_pid = launch.pid
        try:
            os.kill(launch.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            with self._state_lock:
                if self._claimed_pid == launch.pid:
                    self._claimed_pid = None
            return

        kill_latency_us = (time.perf_counter_ns() - captured_at) / 1000.0
        if not _wait_for_pid_exit(launch.pid):
            log_buffer.log(
                'Launcher',
                'macOS URI interception stopped: original Roblox PID did not exit after SIGKILL',
            )
            with self._state_lock:
                if self._claimed_pid == launch.pid:
                    self._claimed_pid = None
            return

        # The credential is never logged.  The only persistent diagnostics are
        # non-secret timing and length metadata.
        log_buffer.log(
            'Launcher',
            'macOS Roblox URI captured and original Player terminated '
            f'(pid={launch.pid}, uri_length={len(target)}, '
            f'capture_to_kill_us={kill_latency_us:.0f})',
        )
        try:
            self._on_intercepted(launch, target)
        except Exception as exc:
            log_buffer.log(
                'Launcher',
                f'macOS URI interception handoff failed: {type(exc).__name__}: {exc}',
            )
        finally:
            # Drop the credential after the replacement launch has been initiated.
            target = ''
            with self._state_lock:
                if self._claimed_pid == launch.pid:
                    self._claimed_pid = None


def get_roblox_process_identity() -> tuple[int, str] | None:
    """Return a token identifying the current Player process."""
    pid = _first_process_pid(ROBLOX_PROCESS)
    if pid is None:
        return None
    command = _process_command(pid)
    return pid, str(command or '')


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


def _signal_process(pid: int, sig: signal.Signals) -> bool:
    """Signal one known process and preserve useful macOS failure diagnostics."""
    try:
        os.kill(pid, sig)
        return True
    except ProcessLookupError:
        # The process exited between discovery and signaling.
        return True
    except PermissionError as exc:
        log_buffer.log(
            'Cache',
            f'Could not signal Roblox pid={pid} with {sig.name}: permission denied ({exc})',
        )
    except OSError as exc:
        log_buffer.log(
            'Cache',
            f'Could not signal Roblox pid={pid} with {sig.name}: {type(exc).__name__}: {exc}',
        )
    return False


def _wait_for_pids_exit(pids: set[int], timeout: float) -> set[int]:
    """Wait for captured PIDs and return the subset that is still alive."""
    remaining = set(pids)
    deadline = time.monotonic() + max(0.0, timeout)
    while remaining:
        for pid in tuple(remaining):
            if _wait_for_pid_exit(pid, timeout=0.0):
                remaining.discard(pid)
        if not remaining or time.monotonic() >= deadline:
            break
        time.sleep(0.05)
    return remaining


def terminate_roblox() -> bool:
    """Terminate the currently-running Roblox Player processes."""
    pids = set(_process_pids(ROBLOX_PROCESS))
    if not pids:
        return False

    for app_path in ROBLOX_APP_CANDIDATES:
        if app_path.exists():
            _quit_app_bundle(app_path)
            break

    for pid in sorted(pids):
        _signal_process(pid, signal.SIGTERM)

    remaining = _wait_for_pids_exit(pids, 2.0)
    if remaining:
        log_buffer.log(
            'Cache',
            'Roblox did not exit after SIGTERM; escalating captured process(es) to SIGKILL: '
            + ', '.join(str(pid) for pid in sorted(remaining)),
        )
        for pid in sorted(remaining):
            _signal_process(pid, signal.SIGKILL)

        remaining = _wait_for_pids_exit(remaining, 3.0)
        if remaining:
            log_buffer.log(
                'Cache',
                'Roblox process(es) remained alive after SIGKILL: '
                + ', '.join(str(pid) for pid in sorted(remaining)),
            )
            return False

    # Current macOS Roblox builds can remain resident in the menu bar and may
    # spawn a replacement RobloxPlayer after the foreground instance exits.
    # Treat that as part of termination rather than declaring success and
    # leaving the caller to time out on the replacement process.
    settle_deadline = time.monotonic() + 3.0
    seen_pids = set(pids)
    while time.monotonic() < settle_deadline:
        replacement_pids = set(_process_pids(ROBLOX_PROCESS)) - seen_pids
        if not replacement_pids:
            time.sleep(0.1)
            continue

        log_buffer.log(
            'Cache',
            'Roblox spawned replacement/background Player process(es); terminating: '
            + ', '.join(str(pid) for pid in sorted(replacement_pids)),
        )
        seen_pids.update(replacement_pids)
        for pid in sorted(replacement_pids):
            _signal_process(pid, signal.SIGTERM)

        remaining = _wait_for_pids_exit(replacement_pids, 0.75)
        if remaining:
            for pid in sorted(remaining):
                _signal_process(pid, signal.SIGKILL)
            remaining = _wait_for_pids_exit(remaining, 1.0)
        if remaining:
            log_buffer.log(
                'Cache',
                'Replacement/background Roblox process(es) remained alive after SIGKILL: '
                + ', '.join(str(pid) for pid in sorted(remaining)),
            )
            return False

    final_pids = set(_process_pids(ROBLOX_PROCESS))
    if final_pids:
        log_buffer.log(
            'Cache',
            'Roblox Player process(es) still present after termination settle period: '
            + ', '.join(str(pid) for pid in sorted(final_pids)),
        )
        return False
    return True


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Roblox to exit. Returns True if it exited before timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)

    remaining = _process_pids(ROBLOX_PROCESS)
    if remaining:
        details = []
        for pid in remaining:
            command = _process_command(pid)
            details.append(f'{pid} ({command or "unknown executable"})')
        log_buffer.log(
            'Cache',
            'Timed out waiting for Roblox exit; remaining Player process(es): '
            + ', '.join(details),
        )
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
    data_dir = appleblox_data_dir()
    backup = (
        APPLEBLOX_MOD_BACKUP_RESOURCES
        if data_dir == APPLEBLOX_DATA_DIR
        else data_dir / 'cache' / 'mods' / 'Resources'
    )
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


def _claim_env_proxy_relaunch(*, force: bool = False) -> bool:
    """Allow only one macOS env-proxy relaunch per launch window."""
    global _env_proxy_relaunch_in_progress

    now = time.monotonic()
    with _env_proxy_relaunch_lock:
        if _env_proxy_relaunch_in_progress:
            return False
        if (
            not force
            and
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


def relaunch_roblox_with_proxy_env(
    proxy_url: str,
    launch_target: str | None = None,
    *,
    force: bool = False,
    cancel_event: threading.Event | None = None,
    source_exe_path: Path | None = None,
    player_already_stopped: bool = False,
    prepare_launch: Callable[[Path], bool] | None = None,
) -> bool:
    """Relaunch the running macOS Roblox Player through Fleasion's env proxy.

    LaunchServices does not retrofit environment variables onto an existing
    application process. Stop the browser/bootstrapper-started player first,
    then use ``open --env`` so the newly launched app inherits the conventional
    proxy variables while retaining normal macOS bundle launch behavior.
    """
    exe_path = (
        Path(source_exe_path)
        if source_exe_path is not None
        else get_roblox_player_exe_path()
    )
    app_path = _app_for_executable(exe_path) if exe_path is not None else None
    if exe_path is None or app_path is None:
        log_buffer.log(
            'Launcher',
            'Roblox Env Proxy relaunch skipped: no macOS Roblox app bundle found',
        )
        return False

    if not _claim_env_proxy_relaunch(force=bool(launch_target) or force):
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
        if cancel_event is not None and cancel_event.is_set():
            return False

        log_buffer.log(
            'Launcher',
            f'Relaunching Roblox through Fleasion env proxy: {app_path} '
            f'({"with launch target" if launch_target else "without launch target"})',
        )
        if not player_already_stopped and is_roblox_running():
            terminate_roblox()
            if not wait_for_roblox_exit():
                log_buffer.log(
                    'Launcher',
                    'Roblox did not exit before macOS env-proxy relaunch',
                )
                return False
        if cancel_event is not None and cancel_event.is_set():
            return False
        if prepare_launch is not None:
            try:
                if not prepare_launch(exe_path):
                    log_buffer.log(
                        'Launcher',
                        'Roblox Env Proxy relaunch skipped: launch preparation failed',
                    )
                    return False
            except Exception as exc:
                log_buffer.log(
                    'Launcher',
                    f'Roblox Env Proxy launch preparation failed: {type(exc).__name__}: {exc}',
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
        if launch_target:
            open_args.append(str(launch_target))

        launch_error = ''
        for attempt in range(3):
            if cancel_event is not None and cancel_event.is_set():
                return False
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
