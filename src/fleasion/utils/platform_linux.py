"""Linux desktop utilities for registered Roblox clients."""

from __future__ import annotations

import json
import os

try:
    import pwd
except ModuleNotFoundError:  # pragma: no cover - exercised by Windows collection
    pwd = None
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator  # ruff: ignore[typing-only-standard-library-import]
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, overload

from fleasion.localization import tr

from .linux_clients import (
    LINUX_CLIENTS_BY_KEY,
    SOBER_CLIENT,
    LinuxClientDescriptor,
    LinuxClientInstallation,
    get_linux_client,
)

if TYPE_CHECKING:

    def _detect_installed_clients(*, home: str | Path) -> tuple[LinuxClientInstallation, ...]: ...

    def _select_linux_client(
        selection: str,
        *,
        installed: tuple[LinuxClientInstallation, ...],
        home: str | Path,
    ) -> LinuxClientInstallation | None: ...
else:
    from .linux_clients import detect_installed_clients, select_linux_client

    def _detect_installed_clients(*, home: str | Path) -> tuple[LinuxClientInstallation, ...]:
        return detect_installed_clients(home=home)

    def _select_linux_client(
        selection: str,
        *,
        installed: tuple[LinuxClientInstallation, ...],
        home: str | Path,
    ) -> LinuxClientInstallation | None:
        return select_linux_client(selection, installed=installed, home=home)


import contextlib

from .logging import log_buffer
from .metadata import APP_NAME
from .paths import (
    APP_CACHE_DIR,
    CONFIG_DIR,
    CONFIG_FILE,
    USER_HOME,
    get_icon_path,
)

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class DetachedPopenKwargs(TypedDict):
    stdin: int
    stdout: int
    stderr: int
    start_new_session: bool


class DesktopInstallResult(TypedDict):
    desktop_entry: str
    launcher: str
    installed_app: str | None
    installed_icon: str | None
    removed_deprecated_entries: list[str]
    sober_uri_handler_restored: bool
    roblox_uri_handler_restored: bool


if TYPE_CHECKING:

    def _json_object(value: object) -> JsonObject | None: ...

    def _client_key(value: object) -> str | None: ...
else:

    def _json_object(value: object) -> JsonObject | None:
        return value if isinstance(value, dict) else None

    def _client_key(value: object) -> str | None:
        return value


SOBER_APP_ID = SOBER_CLIENT.app_id
SOBER_FLATPAK_ROOT = USER_HOME / '.var' / 'app' / SOBER_APP_ID
SOBER_DATA_DIR = SOBER_FLATPAK_ROOT / 'data' / 'sober'
SOBER_CONFIG_FILE = SOBER_FLATPAK_ROOT / 'config' / 'sober' / 'config.json'
SOBER_ASSET_OVERLAY_DIR = SOBER_DATA_DIR / 'asset_overlay'
SOBER_LEGACY_EXE_DIR = SOBER_DATA_DIR / 'exe'
SOBER_CGROUP_MARKER = SOBER_CLIENT.cgroup_marker
LINUX_PROXY_OVERRIDE_STATE = CONFIG_DIR / 'linux_proxy_override.json'
# Sober fetches its update/feature manifest before the Roblox engine starts.
# That bootstrap client uses certificate pinning and does not trust Fleasion's
# generated CA, so these connections must remain tunneled when the Proxy tab's
# intercept-all switch is enabled. Roblox traffic is still intercepted normally.
SOBER_ENV_PROXY_PASSTHROUGH_HOSTS = SOBER_CLIENT.proxy_passthrough_hosts
PROC_ROOT = Path('/proc')
_ROBLOX_URI_SCHEMES = ('x-scheme-handler/roblox', 'x-scheme-handler/roblox-player')
_linux_client_preference: str | None = None
_active_linux_proxy_client_key: str | None = None
_FLEASION_ROBLOX_URI_HANDLER_IDS = frozenset(
    {
        'fleasion.desktop',
        'fleasion-non-admin.desktop',
        'fleasion-read-only.desktop',
        'fleasion-proxy.desktop',
    }
)
DESKTOP_OPENERS = (
    ('xdg-open', ()),
    ('gio', ('open',)),
    ('kde-open5', ()),
    ('kde-open', ()),
    ('gnome-open', ()),
)
_DESKTOP_OPENER_STARTUP_TIMEOUT_SEC = 0.35

LINUX_APPLICATIONS_DIR = USER_HOME / '.local' / 'share' / 'applications'
LINUX_INSTALL_DIR = USER_HOME / '.local' / 'share' / APP_NAME
LINUX_BIN_DIR = USER_HOME / '.local' / 'bin'
LINUX_DESKTOP_ENTRY_PATH = LINUX_APPLICATIONS_DIR / 'fleasion.desktop'
LINUX_LAUNCHER_PATH = LINUX_BIN_DIR / 'fleasion-launch'
LINUX_INSTALLED_APP_PATH = LINUX_INSTALL_DIR / APP_NAME
LINUX_INSTALLED_ICON_PATH = LINUX_INSTALL_DIR / 'fleasionlogoHR.ico'
LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS = (
    LINUX_APPLICATIONS_DIR / 'fleasion-non-admin.desktop',
    LINUX_APPLICATIONS_DIR / 'fleasion-read-only.desktop',
    LINUX_APPLICATIONS_DIR / 'fleasion-proxy.desktop',
)
OS_RELEASE_PATH = Path('/etc/os-release')
ARCH_LINUX_IDS = frozenset(
    {
        'arch',
        'cachyos',
        'endeavouros',
        'garuda',
        'manjaro',
        'steamos',
    }
)
ARCH_LINUX_GUI_PACKAGES = ('qt6-base',)


def _os_release_ids(path: Path = OS_RELEASE_PATH) -> set[str]:
    """Return normalized distro IDs from os-release."""
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return set()

    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition('=')
        if not separator or key not in {'ID', 'ID_LIKE'}:
            continue
        values[key] = value.strip().strip('"').strip("'")

    return {
        distro_id.casefold()
        for value in values.values()
        for distro_id in value.split()
        if distro_id
    }


def missing_linux_gui_packages(
    *,
    os_release_path: Path = OS_RELEASE_PATH,
) -> list[str]:
    """Return missing native GUI packages on supported package-managed distros."""
    if not (_os_release_ids(os_release_path) & ARCH_LINUX_IDS):
        return []
    pacman = shutil.which('pacman')
    if pacman is None:
        return []

    missing: list[str] = []
    for package in ARCH_LINUX_GUI_PACKAGES:
        try:
            result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
                [pacman, '-Q', package],
                capture_output=True,
                text=True,
                timeout=5,
                # PyInstaller adds its Ubuntu-built shared libraries to
                # LD_LIBRARY_PATH.  pacman must instead load the Arch host
                # libraries, or an ABI/load failure looks like a missing
                # package to this check.
                env=_host_subprocess_env(),
            )
        except Exception:  # ruff: ignore[blind-except, try-except-continue]
            # A failed package query should not block an otherwise working
            # desktop when its package manager cannot be inspected.
            continue
        if result.returncode != 0:
            diagnostics = (result.stderr or result.stdout).strip()
            detail = f' Details: {diagnostics}' if diagnostics else ''
            log_buffer.log(
                'Linux GUI',
                f'Arch package query reports {package} as unavailable '
                f'(pacman exit {result.returncode}).{detail}',
            )
            missing.append(package)
    return missing


def run_cmd(args: list[str]) -> str:
    """Run a command and return stdout."""
    return subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
        args,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
    ).stdout


def _normalized_linux_client_preference(value: object) -> str:
    normalized = str(value or 'auto').strip().casefold()
    return normalized if normalized == 'auto' or normalized in LINUX_CLIENTS_BY_KEY else 'auto'


def set_linux_client_preference(value: str | None) -> None:
    """Set the in-process Linux client preference from ``ConfigManager``."""
    global _linux_client_preference  # ruff: ignore[global-statement]
    _linux_client_preference = _normalized_linux_client_preference(value)


def _configured_linux_client_preference() -> str:
    if _linux_client_preference is not None:
        return _linux_client_preference
    try:
        payload: object = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        payload_map = _json_object(payload)
        value = str(payload_map.get('linux_client', 'auto') if payload_map is not None else 'auto')
    except OSError, UnicodeDecodeError, json.JSONDecodeError:
        value = 'auto'
    return _normalized_linux_client_preference(value)


def linux_client_installations() -> tuple[LinuxClientInstallation, ...]:
    """Return installed clients using metadata-only discovery."""
    return _detect_installed_clients(home=USER_HOME)


def get_selected_linux_client_installation() -> LinuxClientInstallation | None:
    """Resolve the configured/desktop-selected installed Linux Roblox client."""
    preference = _configured_linux_client_preference()
    installed = linux_client_installations()
    return _select_linux_client(preference, installed=installed, home=USER_HOME)


def selected_linux_client_key() -> str:
    selected = get_selected_linux_client_installation()
    return selected.key if selected is not None else _configured_linux_client_preference()


def _configured_linux_client_descriptor() -> LinuxClientDescriptor | None:
    return LINUX_CLIENTS_BY_KEY.get(_configured_linux_client_preference())


def selected_linux_client_display_name() -> str:
    selected = get_selected_linux_client_installation()
    configured = _configured_linux_client_descriptor()
    if selected is not None:
        return selected.display_name
    return (
        configured.display_name
        if configured is not None
        else tr('platform_linux.roblox_client_fallback')
    )


def selected_linux_client_app_id() -> str:
    selected = get_selected_linux_client_installation()
    configured = _configured_linux_client_descriptor()
    if selected is not None:
        return selected.app_id
    return configured.app_id if configured is not None else SOBER_APP_ID


def _process_pids(name: str) -> list[int]:
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            ['pgrep', '-x', name],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # ruff: ignore[blind-except]
        return []
    pids: list[int] = []
    for raw in result.stdout.splitlines():
        with contextlib.suppress(ValueError):
            pids.append(int(raw.strip()))
    return pids


def sober_main_process() -> tuple[int, float] | None:
    """Return the Sober Roblox engine PID and its boot-time start timestamp.

    ``Main`` is the Android/Roblox engine process created by Sober.  Pairing
    the PID with the kernel start timestamp makes a close/reopen detectable
    even when the process monitor does not observe a moment with no process.
    """
    try:
        ticks_per_second = float(os.sysconf('SC_CLK_TCK'))
    except AttributeError, OSError, ValueError:
        return None
    if ticks_per_second <= 0:
        return None

    for pid in _process_pids('Main'):
        process_dir = PROC_ROOT / str(pid)
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            cgroup = (process_dir / 'cgroup').read_text(encoding='utf-8', errors='replace')
            if SOBER_CGROUP_MARKER not in cgroup:
                continue
            stat_text = (process_dir / 'stat').read_text(encoding='utf-8', errors='replace')
            # Field 2 (comm) is parenthesized and can contain spaces.  The
            # remaining fields begin at field 3, so starttime (field 22) is 19.
            fields = stat_text.rsplit(')', 1)[1].split()
            start_ticks = int(fields[19])
        except IndexError, OSError, ValueError:
            continue
        return pid, start_ticks / ticks_per_second
    return None


def linux_client_main_process(
    installation: LinuxClientInstallation | None = None,
) -> tuple[int, float] | None:
    installation = installation or get_selected_linux_client_installation()
    if installation is None or installation.key != SOBER_CLIENT.key:
        return None
    return sober_main_process()


def _client_pids(installation: LinuxClientInstallation) -> list[int]:
    """Return only process IDs owned by the installation's Flatpak cgroup."""
    found: list[int] = []
    seen: set[int] = set()
    for name in installation.client.process_names:
        for pid in _process_pids(name):
            if pid in seen:
                continue
            marker = installation.client.cgroup_marker
            if marker:
                try:
                    cgroup = (PROC_ROOT / str(pid) / 'cgroup').read_text(
                        encoding='utf-8', errors='replace'
                    )
                except OSError:
                    continue
                if marker not in cgroup:
                    continue
            seen.add(pid)
            found.append(pid)
    return found


def _first_client_pid(installation: LinuxClientInstallation | None = None) -> int | None:
    installation = installation or get_selected_linux_client_installation()
    if installation is None:
        return None
    pids = _client_pids(installation)
    return pids[0] if pids else None


def _process_command(pid: int) -> Path | None:
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            ['ps', '-p', str(pid), '-o', 'comm='],  # ruff: ignore[start-process-with-partial-path]
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:  # ruff: ignore[blind-except]
        return None
    value = result.stdout.strip()
    return Path(value) if value else None


if TYPE_CHECKING:
    _ = _process_command


def wait_for_roblox_window(timeout: float = 60.0) -> bool:
    """Wait until the selected Linux Roblox client is running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_roblox_running():
            return True
        time.sleep(0.25)
    return False


def is_roblox_running() -> bool:
    """Check if the selected Linux Roblox client is currently running."""
    return _first_client_pid() is not None


def get_roblox_process_identity() -> tuple[str, int, float] | tuple[str, int] | None:
    """Return the engine generation, falling back to the launcher PID."""
    installation = get_selected_linux_client_installation()
    if installation is None:
        return None
    engine = linux_client_main_process(installation)
    if engine is not None:
        return 'engine', engine[0], engine[1]
    pid = _first_client_pid(installation)
    return ('launcher', pid) if pid is not None else None


def is_studio_running() -> bool:
    """Roblox Studio is not supported through Sober."""
    return False


def get_roblox_player_exe_path() -> Path | None:
    """Linux clients do not expose a stable RobloxPlayerBeta.exe path.

    The running process is the Flatpak launcher or wrapper, not the resource
    root that callers need for cert/modification discovery. Returning a
    fabricated path would send downstream code to the wrong directory, so we
    intentionally return ``None`` and let callers use the Linux resource-root
    discovery helpers instead.
    """
    return None


def get_roblox_studio_exe_path() -> Path | None:
    """Roblox Studio is not supported through Sober."""
    return None


def terminate_roblox() -> bool:
    """Terminate only the selected Linux Roblox client."""
    installation = get_selected_linux_client_installation()
    if installation is None:
        return False
    pids = _client_pids(installation)
    if not pids:
        return False

    flatpak = shutil.which('flatpak')
    if flatpak:
        try:
            result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
                [flatpak, 'kill', installation.app_id],
                env=_host_subprocess_env(),
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass

    signalled = False
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            signalled = True
        except ProcessLookupError:
            signalled = True
        except OSError:
            pass
    return signalled or not is_roblox_running()


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for the selected Linux client to exit."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not is_roblox_running():
            return True
        time.sleep(0.5)
    return False


def _delete_path(path: Path, messages: list[str], label: str) -> None:
    if not path.exists():
        messages.append(f'{label} already deleted')
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
    """Delete cache files for only the selected Linux client and Fleasion."""
    messages: list[str] = []
    installation = get_selected_linux_client_installation()
    if installation is None:
        return [
            'Selected Linux Roblox client is not installed',
            'Cache deletion aborted',
        ]
    client_name = installation.display_name

    if is_roblox_running():
        messages.append(f'{client_name} is running, terminating...')
        terminate_roblox()
        if wait_for_roblox_exit():
            messages.append(f'{client_name} terminated successfully')
        else:
            messages.extend([f'{client_name} termination timed out', 'Cache deletion aborted'])
            return messages
    else:
        messages.append(f'{client_name} was closed')

    storage_db = installation.paths.storage_db
    storage_folder: Path | None = None
    if storage_db is not None:
        _delete_path(storage_db, messages, 'Storage database')
        for suffix in ('-wal', '-shm'):
            sidecar = Path(str(storage_db) + suffix)
            if sidecar.exists():
                _delete_path(sidecar, messages, f'Storage database {suffix}')

        storage_folder = storage_db.parent / 'rbx-storage'
        _delete_path(storage_folder, messages, 'Storage folder')
    cache_storage = installation.paths.cache_storage_dir
    if cache_storage is not None and cache_storage != storage_folder:
        _delete_path(cache_storage, messages, 'Cache storage folder')

    if APP_CACHE_DIR.exists():
        try:  # ruff: ignore[too-many-statements-in-try-clause]
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


def find_roblox_resource_dirs(include_studio: bool = True) -> list[Path]:  # ruff: ignore[unused-function-argument]
    """Return resource roots for only the selected Linux client."""
    found: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not path.exists():
            return
        key = str(path.resolve()).lower()
        if key in seen:
            return
        seen.add(key)
        found.append(path)

    installation = get_selected_linux_client_installation()
    if installation is None:
        return found

    resource_roots = installation.paths.resource_roots
    if installation.paths.data_root.exists() and resource_roots:
        resource_roots[0].mkdir(parents=True, exist_ok=True)
    for resource_root in resource_roots:
        _add(resource_root)
    return found


def is_sober_resource_dir(path: Path) -> bool:
    """Return True when *path* is one of Sober's resource roots."""
    try:
        resolved = path.resolve()
        return resolved in {
            SOBER_ASSET_OVERLAY_DIR.resolve(),
            SOBER_LEGACY_EXE_DIR.resolve(),
        }
    except OSError:
        return False


def find_linux_global_settings_dirs() -> list[Path]:
    """Return the selected client's data root for Roblox settings XML."""
    installation = get_selected_linux_client_installation()
    if installation is None or not installation.paths.data_root.exists():
        return []
    return [installation.paths.data_root]


def resolve_roblox_player_exe_for_launch() -> Path | None:
    """Return the selected client's Flatpak launcher path."""
    installation = get_selected_linux_client_installation()
    return installation.executable if installation is not None else None


_DETACHED_POPEN_KWARGS: DetachedPopenKwargs = {
    'stdin': subprocess.DEVNULL,
    'stdout': subprocess.DEVNULL,
    'stderr': subprocess.DEVNULL,
    'start_new_session': True,
}


def _host_subprocess_env() -> dict[str, str]:
    """Run host desktop tools without PyInstaller's private library path."""
    env = os.environ.copy()
    original_library_path = env.pop('LD_LIBRARY_PATH_ORIG', None)
    if original_library_path is not None:
        if original_library_path:
            env['LD_LIBRARY_PATH'] = original_library_path
        else:
            env.pop('LD_LIBRARY_PATH', None)
        return env

    bundle_root = getattr(sys, '_MEIPASS', None)
    library_path = env.get('LD_LIBRARY_PATH')
    if bundle_root and library_path:
        bundle_path = Path(bundle_root).resolve()
        entries = [
            entry
            for entry in library_path.split(os.pathsep)
            if entry and Path(entry).resolve() != bundle_path
        ]
        if entries:
            env['LD_LIBRARY_PATH'] = os.pathsep.join(entries)
        else:
            env.pop('LD_LIBRARY_PATH', None)
    return env


def _standard_user_popen(args: list[str]) -> subprocess.Popen[bytes]:
    env = _host_subprocess_env()
    if os.geteuid() != 0:
        return subprocess.Popen(args, env=env, **_DETACHED_POPEN_KWARGS)  # ruff: ignore[subprocess-without-shell-equals-true]

    user_home = Path(os.environ.get('FLEASION_USER_HOME') or USER_HOME)
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        stat = user_home.stat()
        uid = stat.st_uid
        gid = stat.st_gid
        if pwd is None:
            raise KeyError(uid)
        pw_entry = pwd.getpwuid(uid)
    except Exception:  # ruff: ignore[blind-except]
        return subprocess.Popen(args, env=env, **_DETACHED_POPEN_KWARGS)  # ruff: ignore[subprocess-without-shell-equals-true]

    env.update(
        {
            'HOME': str(user_home),
            'USER': pw_entry.pw_name,
            'LOGNAME': pw_entry.pw_name,
            'XDG_RUNTIME_DIR': f'/run/user/{uid}',
        }
    )

    def _demote() -> None:
        os.setgid(gid)
        os.setuid(uid)

    return subprocess.Popen(args, env=env, preexec_fn=_demote, **_DETACHED_POPEN_KWARGS)  # ruff: ignore[subprocess-popen-preexec-fn, subprocess-without-shell-equals-true]


@overload
def _standard_user_run(
    args: list[str],
    *,
    capture_output: bool = False,
    text: Literal[True],
    timeout: float,
) -> subprocess.CompletedProcess[str]: ...


@overload
def _standard_user_run(
    args: list[str],
    *,
    capture_output: bool = False,
    text: Literal[False] = False,
    timeout: float,
) -> subprocess.CompletedProcess[bytes]: ...


def _standard_user_run(
    args: list[str],
    *,
    capture_output: bool = False,
    text: bool = False,
    timeout: float,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    """Run a host command as the desktop user, including under elevation."""
    env = _host_subprocess_env()
    user_home = Path(os.environ.get('FLEASION_USER_HOME') or USER_HOME)
    preexec_fn = None
    if os.geteuid() == 0:
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            stat = user_home.stat()
            uid = stat.st_uid
            gid = stat.st_gid
            if pwd is None:
                raise KeyError(uid)
            pw_entry = pwd.getpwuid(uid)
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass
        else:
            env.update(
                {
                    'HOME': str(user_home),
                    'USER': pw_entry.pw_name,
                    'LOGNAME': pw_entry.pw_name,
                    'XDG_RUNTIME_DIR': f'/run/user/{uid}',
                }
            )

            def _demote() -> None:
                os.setgid(gid)
                os.setuid(uid)

            preexec_fn = _demote

    if text:
        return subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            args,
            env=env,
            preexec_fn=preexec_fn,
            capture_output=capture_output,
            text=True,
            timeout=timeout,
        )
    return subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
        args,
        env=env,
        preexec_fn=preexec_fn,
        capture_output=capture_output,
        text=False,
        timeout=timeout,
    )


def _desktop_open_commands(target: str) -> Iterator[list[str]]:
    """Yield available desktop opener commands in fallback order."""
    for executable, extra_args in DESKTOP_OPENERS:
        resolved = shutil.which(executable)
        if resolved:
            yield [resolved, *extra_args, target]


def _open_with_desktop_handler(target: str, label: str) -> bool:
    opener_found = False
    for command in _desktop_open_commands(target):
        opener_found = True
        try:
            process = _standard_user_popen(command)
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log(
                'Launch',
                f'Failed to start {Path(command[0]).name} for {label}: {exc}',
            )
            continue

        try:
            return_code = process.wait(timeout=_DESKTOP_OPENER_STARTUP_TIMEOUT_SEC)
        except subprocess.TimeoutExpired:
            # Some openers remain alive while the desktop application starts. A
            # process that survives the startup window is treated as successfully
            # handed off so we do not launch the target more than once.
            return True
        except Exception as exc:  # ruff: ignore[blind-except]
            # The child process started successfully; if its status cannot be
            # observed, preserve the previous detached-launch behavior.
            log_buffer.log(
                'Launch',
                f'Could not verify {Path(command[0]).name} for {label}: {exc}',
            )
            return True

        if return_code == 0:
            return True

        log_buffer.log(
            'Launch',
            f'{Path(command[0]).name} failed to open {label} (exit {return_code}); '
            'trying another desktop opener',
        )

    if not opener_found:
        log_buffer.log('Launch', f'Cannot open {label}: no desktop opener found')
    else:
        log_buffer.log('Launch', f'Failed to open {label}: all desktop openers failed')
    return False


def _launch_target_for_log(target: str) -> str:
    """Return a useful label without exposing URI credentials or tickets."""
    if target.startswith(('roblox:', 'roblox-player:')):
        return 'Roblox URI'
    if target.startswith(('http://', 'https://')):
        return 'URL'
    return target


def launch_as_standard_user(target: str | Path) -> bool:  # ruff: ignore[too-many-return-statements]
    """Launch a URI or the selected Linux client explicitly."""
    target_str = str(target).strip()
    if not target_str:
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if target_str.startswith(('roblox:', 'roblox-player:')):
            installation = get_selected_linux_client_installation()
            if installation is None or installation.executable is None:
                log_buffer.log('Launch', 'Cannot launch Roblox URI: selected client not found')
                return False
            command = installation.launch_command(target_str)
            if command is None:
                log_buffer.log('Launch', 'Cannot launch Roblox URI: launcher not found')
                return False
            _standard_user_popen(command)
            return True

        if target_str.startswith(('http://', 'https://')):
            return _open_with_desktop_handler(target_str, 'URL')

        path = Path(target_str)
        installation = get_selected_linux_client_installation()
        flatpak = shutil.which('flatpak')
        if (
            installation is not None
            and flatpak
            and (path.name == 'flatpak' or target_str == installation.app_id)
        ):
            _standard_user_popen([flatpak, 'run', installation.app_id])
            return True

        if path.exists():
            return _open_with_desktop_handler(str(path), 'path')
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Launch', f'Failed to launch {_launch_target_for_log(target_str)}: {exc}')
        return False

    log_buffer.log('Launch', f'Launch target not found: {_launch_target_for_log(target_str)}')
    return False


def _installation_for_client_key(key: str) -> LinuxClientInstallation | None:
    """Build a deterministic installation for scoped override cleanup."""
    try:
        client = get_linux_client(key)
    except ValueError:
        return None
    flatpak = shutil.which('flatpak')
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=USER_HOME),
        executable=Path(flatpak) if flatpak else None,
    )


def _proxy_environment_for_installation(
    installation: LinuxClientInstallation,
    proxy_url: str,
) -> dict[str, str]:
    bypass = 'localhost,127.0.0.1,::1'
    return {
        name: bypass if name.casefold() == 'no_proxy' else proxy_url
        for name in installation.client.proxy_environment_names
    }


def _read_linux_proxy_override_state() -> str | None:
    try:
        if LINUX_PROXY_OVERRIDE_STATE.is_symlink():
            return None
        payload: object = json.loads(LINUX_PROXY_OVERRIDE_STATE.read_text(encoding='utf-8'))
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    payload_map = _json_object(payload)
    key = _client_key(payload_map.get('client') if payload_map is not None else None)
    return key if key in LINUX_CLIENTS_BY_KEY else None


def _write_linux_proxy_override_state(key: str | None) -> None:
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if key is None:
            LINUX_PROXY_OVERRIDE_STATE.unlink(missing_ok=True)
            return
        LINUX_PROXY_OVERRIDE_STATE.parent.mkdir(parents=True, exist_ok=True)
        temporary = LINUX_PROXY_OVERRIDE_STATE.with_name(
            f'.{LINUX_PROXY_OVERRIDE_STATE.name}.{os.getpid()}.tmp'
        )
        try:
            temporary.write_text(json.dumps({'client': key}) + '\n', encoding='utf-8')
            temporary.chmod(0o600)
            temporary.replace(LINUX_PROXY_OVERRIDE_STATE)
        finally:
            temporary.unlink(missing_ok=True)
    except OSError as exc:
        log_buffer.log('Launcher', f'Could not persist Linux Env Proxy ownership: {exc}')


def set_linux_client_env_proxy_override(
    proxy_url: str,
    *,
    client_key: str | None = None,
    ca_cert_path: Path | None = None,
) -> bool:
    """Arm Env Proxy for exactly one selected Linux client."""
    global _active_linux_proxy_client_key  # ruff: ignore[global-statement]
    # Retained for call-site compatibility; registered clients currently need
    # no client-specific certificate-bundle preparation here.
    _ = ca_cert_path
    installation = (
        _installation_for_client_key(client_key)
        if client_key is not None
        else get_selected_linux_client_installation()
    )
    if installation is None:
        log_buffer.log('Launcher', 'Cannot arm Linux Env Proxy: selected client not installed')
        return False
    previous_key = _active_linux_proxy_client_key or _read_linux_proxy_override_state()
    if previous_key not in {None, installation.key} and not clear_linux_client_env_proxy_override(
        client_key=previous_key
    ):
        log_buffer.log(
            'Launcher',
            f'Refusing to arm {installation.display_name} while the prior Fleasion '
            f'override for {previous_key} could not be cleared',
        )
        return False

    proxy_env = _proxy_environment_for_installation(
        installation,
        proxy_url,
    )

    flatpak = shutil.which('flatpak')
    if not flatpak:
        log_buffer.log(
            'Launcher',
            f'Cannot arm {installation.display_name} Env Proxy: flatpak command not found',
        )
        return False
    try:
        result = _standard_user_run(
            [
                flatpak,
                'override',
                '--user',
                *(f'--env={key}={value}' for key, value in proxy_env.items()),
                installation.app_id,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Launcher', f'Could not arm {installation.display_name} Env Proxy: {exc}')
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log_buffer.log(
            'Launcher',
            f'Could not arm {installation.display_name} Env Proxy'
            + (f': {detail}' if detail else ''),
        )
        return False
    _active_linux_proxy_client_key = installation.key
    _write_linux_proxy_override_state(installation.key)
    log_buffer.log(
        'Launcher', f'Armed {installation.display_name} Env Proxy for normal browser launches'
    )
    return True


def clear_linux_client_env_proxy_override(*, client_key: str | None = None) -> bool:
    """Clear only the exact client environment names managed by Fleasion."""
    global _active_linux_proxy_client_key  # ruff: ignore[global-statement]
    key = client_key or _active_linux_proxy_client_key or _read_linux_proxy_override_state()
    if key is None:
        return True
    installation = _installation_for_client_key(key)
    if installation is None:
        return False
    names = installation.client.proxy_environment_names
    flatpak = shutil.which('flatpak')
    if not flatpak:
        log_buffer.log(
            'Launcher',
            f'Cannot disarm {installation.display_name} Env Proxy: flatpak command not found',
        )
        return False
    try:
        result = _standard_user_run(
            [
                flatpak,
                'override',
                '--user',
                *(f'--unset-env={name}' for name in names),
                installation.app_id,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Launcher', f'Could not disarm {installation.display_name} Env Proxy: {exc}')
        return False
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        log_buffer.log(
            'Launcher',
            f'Could not disarm {installation.display_name} Env Proxy'
            + (f': {detail}' if detail else ''),
        )
        return False
    if _active_linux_proxy_client_key == key:
        _active_linux_proxy_client_key = None
    if _read_linux_proxy_override_state() == key:
        _write_linux_proxy_override_state(None)
    log_buffer.log('Launcher', f'Disarmed {installation.display_name} Env Proxy')
    return True


def recover_stale_linux_client_env_proxy_override() -> bool:
    """Clear a persisted Env Proxy override left by an earlier Fleasion process."""
    if _active_linux_proxy_client_key is not None:
        return True
    key = _read_linux_proxy_override_state()
    if key is None:
        return True
    log_buffer.log(
        'Launcher',
        f'Recovering stale Linux Env Proxy override for {key}',
    )
    return clear_linux_client_env_proxy_override(client_key=key)


def set_sober_env_proxy_override(proxy_url: str) -> bool:
    """Backward-compatible explicit Sober override helper."""
    return set_linux_client_env_proxy_override(proxy_url, client_key='sober')


def clear_sober_env_proxy_override() -> bool:
    """Backward-compatible explicit Sober override cleanup helper."""
    return clear_linux_client_env_proxy_override(client_key='sober')


def _set_default_roblox_uri_handler(desktop_id: str) -> bool:
    """Set the user-session handler for Roblox URI schemes."""
    xdg_mime = shutil.which('xdg-mime')
    if not xdg_mime:
        log_buffer.log('DesktopIntegration', 'xdg-mime not found; Roblox URI handler unchanged')
        return False

    ok = True
    env = _host_subprocess_env()
    env['HOME'] = str(USER_HOME)
    for scheme in _ROBLOX_URI_SCHEMES:
        try:
            result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
                [xdg_mime, 'default', desktop_id, scheme],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log(
                'DesktopIntegration',
                f'Failed to set the {scheme} handler to {desktop_id}: {exc}',
            )
            ok = False
            continue
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            log_buffer.log(
                'DesktopIntegration',
                f'Failed to set the {scheme} handler to {desktop_id}'
                + (f': {detail}' if detail else ''),
            )
            ok = False
    return ok


def _restore_linux_roblox_uri_handler() -> bool:
    """Restore the selected client only when Fleasion owns a Roblox scheme."""
    xdg_mime = shutil.which('xdg-mime')
    if not xdg_mime:
        log_buffer.log('DesktopIntegration', 'xdg-mime not found; Roblox URI handler unchanged')
        return False

    env = _host_subprocess_env()
    env['HOME'] = str(USER_HOME)
    fleasion_handler_detected = False
    for scheme in _ROBLOX_URI_SCHEMES:
        try:
            result = subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
                [xdg_mime, 'query', 'default', scheme],
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('DesktopIntegration', f'Failed to query the {scheme} handler: {exc}')
            continue
        if result.returncode != 0:
            continue
        if result.stdout.strip() in _FLEASION_ROBLOX_URI_HANDLER_IDS:
            fleasion_handler_detected = True

    if not fleasion_handler_detected:
        return False
    selected = get_selected_linux_client_installation()
    if selected is None:
        log_buffer.log(
            'DesktopIntegration',
            'Roblox URI handler was not restored because no selected client is installed',
        )
        return False
    return _set_default_roblox_uri_handler(selected.desktop_id)


def _find_project_root() -> Path | None:
    check = Path(__file__).resolve().parent
    for _ in range(8):
        if (check / 'pyproject.toml').is_file() and (check / 'launcher.py').is_file():
            return check
        if check.parent == check:
            break
        check = check.parent
    return None


def _copy_linux_app_payload() -> tuple[Path | None, Path | None]:
    """Copy frozen Linux app payload into the per-user install directory.

    Source/development launches do not have a self-contained binary to copy, so
    they keep running from the checkout. Frozen builds are copied so the desktop
    entry does not point at a Downloads/tmp path that can disappear.
    """
    if not getattr(sys, 'frozen', False):
        return None, None

    source = Path(sys.executable).resolve()
    if source.is_relative_to(Path('/nix/store')):
        log_buffer.log(
            'App',
            'Linux desktop integration detected a Nix store executable; using it directly instead of copying a stale per-user binary',
        )
        return None, None

    LINUX_INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    installed_app = LINUX_INSTALLED_APP_PATH
    if source != installed_app.resolve():
        shutil.copy2(source, installed_app)
    installed_app.chmod(0o755)

    installed_icon: Path | None = None
    icon_path = get_icon_path()
    if icon_path is not None and icon_path.is_file():
        installed_icon = LINUX_INSTALLED_ICON_PATH
        if icon_path.resolve() != installed_icon.resolve():
            shutil.copy2(icon_path, installed_icon)
        installed_icon.chmod(0o644)

    return installed_app, installed_icon


def _linux_app_launch_command(
    installed_app: Path | None = None,
) -> tuple[list[str], Path | None]:
    """Return the normal-user command that a privileged wrapper should run."""
    if installed_app is not None:
        return [str(installed_app)], installed_app.parent

    if getattr(sys, 'frozen', False):
        return [sys.executable], Path(sys.executable).parent

    project = _find_project_root()
    if project is not None:
        return [sys.executable, str(project / 'launcher.py')], project

    return [sys.executable, '-c', 'from fleasion import main; main()'], None


def _write_executable_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def install_desktop_entries() -> DesktopInstallResult:
    """Install the Linux desktop launcher.

    The installed application entry starts Fleasion as the interactive user.
    Linux/Sober interception starts a small pkexec helper only for /etc/hosts
    and the privileged port-443 relay, keeping the Qt GUI in the user session.
    Legacy non-admin/read-only desktop entries are removed so menus only expose
    the supported proxy-capable launcher.
    """
    installed_app, installed_icon = _copy_linux_app_payload()
    command, working_dir = _linux_app_launch_command(installed_app)
    command_literal = ' '.join(shlex.quote(part) for part in command)
    working_dir_literal = shlex.quote(str(working_dir)) if working_dir is not None else ''
    pythonpath = (
        ''
        if working_dir is None
        else f'export PYTHONPATH={shlex.quote(str(working_dir / "src"))}${{PYTHONPATH:+:$PYTHONPATH}}\n'
    )

    launcher = f"""#!/bin/sh
set -eu
export FLEASION_USER_HOME="{USER_HOME}"
{pythonpath}{f'cd {working_dir_literal}' if working_dir is not None else ':'}
exec {command_literal} "$@"
"""
    _write_executable_script(LINUX_LAUNCHER_PATH, launcher)

    icon_path = installed_icon or get_icon_path()
    icon_line = f'Icon={icon_path}\n' if icon_path is not None else 'Icon=fleasion\n'
    desktop_entry = (
        '[Desktop Entry]\n'
        'Type=Application\n'
        f'Name={APP_NAME}\n'
        'Comment=Roblox asset interceptor and replacer for Sober\n'
        f'Exec={shlex.quote(str(LINUX_LAUNCHER_PATH))}\n'
        f'{icon_line}'
        'Terminal=false\n'
        'Categories=Game;Utility;\n'
        'StartupNotify=true\n'
    )
    LINUX_APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    LINUX_DESKTOP_ENTRY_PATH.write_text(desktop_entry, encoding='utf-8')
    LINUX_DESKTOP_ENTRY_PATH.chmod(0o644)

    removed: list[str] = []
    for path in LINUX_DEPRECATED_DESKTOP_ENTRY_PATHS:
        if path.exists():
            path.unlink()
            removed.append(str(path))

    update_desktop = shutil.which('update-desktop-database')
    if update_desktop:
        subprocess.run(  # ruff: ignore[subprocess-run-without-check, subprocess-without-shell-equals-true]
            [update_desktop, str(LINUX_APPLICATIONS_DIR)],
            capture_output=True,
            timeout=10,
        )

    roblox_uri_handler_restored = _restore_linux_roblox_uri_handler()

    return {
        'desktop_entry': str(LINUX_DESKTOP_ENTRY_PATH),
        'launcher': str(LINUX_LAUNCHER_PATH),
        'installed_app': str(installed_app) if installed_app is not None else None,
        'installed_icon': str(installed_icon) if installed_icon is not None else None,
        'removed_deprecated_entries': removed,
        # Keep the old result key for callers while exposing the generic name.
        'sober_uri_handler_restored': roblox_uri_handler_restored,
        'roblox_uri_handler_restored': roblox_uri_handler_restored,
    }


def open_folder(path: Path) -> bool:
    """Open a folder in the user's file manager."""
    path.mkdir(parents=True, exist_ok=True)
    return _open_with_desktop_handler(str(path), 'folder')


def show_message_box(title: str, message: str, icon: int = 0x40) -> None:  # ruff: ignore[unused-function-argument]
    """Show a simple Linux desktop notification/dialog when available."""
    try:
        subprocess.run(['zenity', '--info', '--title', title, '--text', message], timeout=10)  # ruff: ignore[start-process-with-partial-path, subprocess-run-without-check, subprocess-without-shell-equals-true]
    except Exception:  # ruff: ignore[blind-except]
        log_buffer.log('UI', f'{title}: {message}')
