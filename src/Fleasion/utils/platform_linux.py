"""Linux/Sober desktop utilities."""

from __future__ import annotations

import os
import pwd
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from .logging import log_buffer
from .paths import APP_CACHE_DIR, APP_NAME, STORAGE_DB, USER_HOME, get_icon_path

SOBER_APP_ID = 'org.vinegarhq.Sober'
SOBER_FLATPAK_ROOT = USER_HOME / '.var' / 'app' / SOBER_APP_ID
SOBER_DATA_DIR = SOBER_FLATPAK_ROOT / 'data' / 'sober'
SOBER_CONFIG_FILE = SOBER_FLATPAK_ROOT / 'config' / 'sober' / 'config.json'
SOBER_ASSET_OVERLAY_DIR = SOBER_DATA_DIR / 'asset_overlay'
SOBER_LEGACY_EXE_DIR = SOBER_DATA_DIR / 'exe'
SOBER_PROCESS_NAMES = ('sober', 'Sober', SOBER_APP_ID)

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


def _first_sober_pid() -> int | None:
    for name in SOBER_PROCESS_NAMES:
        pids = _process_pids(name)
        if pids:
            return pids[0]
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


def wait_for_roblox_window(timeout: float = 60.0) -> bool:
    """Wait until Sober's Roblox runtime process is running."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_roblox_running():
            return True
        time.sleep(0.25)
    return False


def is_roblox_running() -> bool:
    """Check if Sober is currently running."""
    return _first_sober_pid() is not None


def is_studio_running() -> bool:
    """Roblox Studio is not supported through Sober."""
    return False


def get_roblox_player_exe_path() -> Optional[Path]:
    """Linux/Sober does not expose a stable RobloxPlayerBeta.exe path.

    The running process is the Flatpak launcher or wrapper, not the resource
    root that callers need for cert/modification discovery. Returning a
    fabricated path would send downstream code to the wrong directory, so we
    intentionally return ``None`` and let callers use the Sober resource-root
    discovery helpers instead.
    """
    return None


def get_roblox_studio_exe_path() -> Optional[Path]:
    """Roblox Studio is not supported through Sober."""
    return None


def terminate_roblox() -> bool:
    """Terminate Sober if it is running. Returns True if it was running."""
    if not is_roblox_running():
        return False
    terminated = False

    flatpak = shutil.which('flatpak')
    if flatpak:
        try:
            result = subprocess.run(
                [flatpak, 'kill', SOBER_APP_ID],
                capture_output=True,
                text=True,
                timeout=10,
            )
            terminated = result.returncode == 0
        except Exception:
            pass

    for name in SOBER_PROCESS_NAMES:
        try:
            result = subprocess.run(['pkill', '-x', name], capture_output=True, timeout=5)
            terminated = result.returncode == 0 or terminated
        except Exception:
            pass
    return terminated or is_roblox_running()


def wait_for_roblox_exit(timeout: float = 10.0) -> bool:
    """Wait for Sober to exit. Returns True if it exited before timeout."""
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
    """Delete Sober/Roblox cache files and Fleasion's converted-object cache."""
    messages: list[str] = []

    if is_roblox_running():
        messages.append('Sober is running, terminating...')
        terminate_roblox()
        if wait_for_roblox_exit():
            messages.append('Sober terminated successfully')
        else:
            messages.extend(['Sober termination timed out', 'Cache deletion aborted'])
            return messages
    else:
        messages.append('Sober was closed')

    _delete_path(STORAGE_DB, messages, 'Storage database')
    for suffix in ('-wal', '-shm'):
        sidecar = Path(str(STORAGE_DB) + suffix)
        if sidecar.exists():
            _delete_path(sidecar, messages, f'Storage database {suffix}')

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
    """Return Sober resource roots used by patch/modification code."""
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

    if SOBER_DATA_DIR.exists():
        SOBER_ASSET_OVERLAY_DIR.mkdir(parents=True, exist_ok=True)
        _add(SOBER_ASSET_OVERLAY_DIR)

    # Older Sober builds exposed a Roblox-like extracted tree here.
    if SOBER_LEGACY_EXE_DIR.is_dir():
        _add(SOBER_LEGACY_EXE_DIR)

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


def resolve_roblox_player_exe_for_launch() -> Optional[Path]:
    """Return a launcher path if Sober can be launched through Flatpak."""
    flatpak = shutil.which('flatpak')
    return Path(flatpak) if flatpak else None


def _standard_user_popen(args: list[str]) -> subprocess.Popen:
    if os.geteuid() != 0:
        return subprocess.Popen(args)

    user_home = Path(os.environ.get('FLEASION_USER_HOME') or USER_HOME)
    try:
        stat = user_home.stat()
        uid = stat.st_uid
        gid = stat.st_gid
        pw_entry = pwd.getpwuid(uid)
    except Exception:
        return subprocess.Popen(args)

    env = os.environ.copy()
    env.update({
        'HOME': str(user_home),
        'USER': pw_entry.pw_name,
        'LOGNAME': pw_entry.pw_name,
        'XDG_RUNTIME_DIR': f'/run/user/{uid}',
    })

    def _demote() -> None:
        os.setgid(gid)
        os.setuid(uid)

    return subprocess.Popen(args, env=env, preexec_fn=_demote)


def launch_as_standard_user(target: str | Path) -> bool:
    """Launch a Roblox URI or Sober itself."""
    target_str = str(target).strip()
    if not target_str:
        return False
    try:
        if target_str.startswith(('roblox:', 'roblox-player:')):
            _standard_user_popen(['xdg-open', target_str])
            return True

        if target_str.startswith(('http://', 'https://')):
            _standard_user_popen(['xdg-open', target_str])
            return True

        path = Path(target_str)
        flatpak = shutil.which('flatpak')
        if flatpak and (path.name == 'flatpak' or target_str == SOBER_APP_ID):
            _standard_user_popen([flatpak, 'run', SOBER_APP_ID])
            return True

        if path.exists():
            _standard_user_popen(['xdg-open', str(path)])
            return True
    except Exception as exc:
        log_buffer.log('Launch', f'Failed to launch {target_str}: {exc}')
        return False

    log_buffer.log('Launch', f'Launch target not found: {target_str}')
    return False


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


def _linux_app_launch_command(installed_app: Path | None = None) -> tuple[list[str], Path | None]:
    """Return the normal-user command that a privileged wrapper should run."""
    if installed_app is not None:
        return [str(installed_app)], installed_app.parent

    if getattr(sys, 'frozen', False):
        return [sys.executable], Path(sys.executable).parent

    project = _find_project_root()
    if project is not None:
        return [sys.executable, str(project / 'launcher.py')], project

    return [sys.executable, '-c', 'from Fleasion import main; main()'], None


def _write_executable_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    path.chmod(0o755)


def install_desktop_entries() -> dict:
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

    launcher = f'''#!/bin/sh
set -eu
export FLEASION_USER_HOME="{USER_HOME}"
{pythonpath}{f'cd {working_dir_literal}' if working_dir is not None else ':'}
exec {command_literal} "$@"
'''
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
        subprocess.run([update_desktop, str(LINUX_APPLICATIONS_DIR)], capture_output=True, timeout=10)

    return {
        'desktop_entry': str(LINUX_DESKTOP_ENTRY_PATH),
        'launcher': str(LINUX_LAUNCHER_PATH),
        'installed_app': str(installed_app) if installed_app is not None else None,
        'installed_icon': str(installed_icon) if installed_icon is not None else None,
        'removed_deprecated_entries': removed,
    }

def open_folder(path: Path):
    """Open a folder in the user's file manager."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(['xdg-open', str(path)])


def show_message_box(title: str, message: str, icon: int = 0x40):
    """Show a simple Linux desktop notification/dialog when available."""
    try:
        subprocess.run(['zenity', '--info', '--title', title, '--text', message], timeout=10)
    except Exception:
        log_buffer.log('UI', f'{title}: {message}')
