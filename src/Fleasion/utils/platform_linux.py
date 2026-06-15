"""Linux/Sober desktop utilities."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

from .logging import log_buffer
from .paths import APP_CACHE_DIR, STORAGE_DB, USER_HOME

SOBER_APP_ID = 'org.vinegarhq.Sober'
SOBER_FLATPAK_ROOT = USER_HOME / '.var' / 'app' / SOBER_APP_ID
SOBER_DATA_DIR = SOBER_FLATPAK_ROOT / 'data' / 'sober'
SOBER_CONFIG_FILE = SOBER_FLATPAK_ROOT / 'config' / 'sober' / 'config.json'
SOBER_ASSET_OVERLAY_DIR = SOBER_DATA_DIR / 'asset_overlay'
SOBER_LEGACY_EXE_DIR = SOBER_DATA_DIR / 'exe'
SOBER_PROCESS_NAMES = ('sober', 'Sober', SOBER_APP_ID)


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
    """Return the running Sober executable path when one can be resolved.

    Flatpak's launcher is not the Roblox/Sober runtime path. Returning it here
    makes callers treat /usr/bin as a Roblox install root, which can trigger
    invalid cert repair and restart behavior.
    """
    pid = _first_sober_pid()
    if pid is not None:
        command = _process_command(pid)
        if command and command.is_file():
            return command
    return None


def get_roblox_studio_exe_path() -> Optional[Path]:
    """Roblox Studio is not supported through Sober."""
    return None


def terminate_roblox() -> bool:
    """Terminate Sober if it is running. Returns True if it was running."""
    if not is_roblox_running():
        return False
    for name in SOBER_PROCESS_NAMES:
        try:
            subprocess.run(['pkill', '-x', name], capture_output=True, timeout=5)
        except Exception:
            pass
    return True


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
        messages.extend([
            'Sober is running; close it before deleting cache',
            'Cache deletion aborted',
        ])
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
            preserve = {APP_CACHE_DIR / 'predownloaded', APP_CACHE_DIR / 'texpack_slots'}
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
