"""macOS Roblox update, proxy readiness, and managed Rosemary launch lifecycle."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import macos_rosemary_installer, macos_rosemary_memory, platform_macos
from fleasion.utils.logging import log_buffer
from fleasion.utils.paths import CONFIG_DIR

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster

_ROBLOX_APP = Path('/Applications/Roblox.app')
_VERSION_URL = 'https://clientsettingscdn.roblox.com/v2/client-version/MacPlayer'
_UPDATE_TIMEOUT = 180.0
_PROXY_TIMEOUT = 25.0
_STARTUP_TIMEOUT = 25.0
_LOCAL_EXCLUSIONS = 'localhost,127.0.0.1,::1'


class ManagedLaunchError(RuntimeError):
    """Roblox could not be launched through the verified managed environment."""


def installed_roblox_version(app: Path = _ROBLOX_APP) -> str | None:
    try:
        with (app / 'Contents' / 'Info.plist').open('rb') as source:
            info = plistlib.load(source)
    except OSError, ValueError, plistlib.InvalidFileException:
        return None
    version = info.get('CFBundleShortVersionString') if isinstance(info, dict) else None
    return version if isinstance(version, str) and version.startswith('0.') else None


def latest_roblox_version() -> str:
    request = urllib.request.Request(
        _VERSION_URL, headers={'User-Agent': 'Fleasion-macOS-Roblox-Update'}
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:  # ruff: ignore[suspicious-url-open-usage]
            data = json.load(response)
    except (OSError, ValueError, TimeoutError) as exc:
        msg = f'Could not check Roblox for updates: {exc}'
        raise ManagedLaunchError(msg) from exc
    version = data.get('version') if isinstance(data, dict) else None
    if not isinstance(version, str) or not version.startswith('0.') or len(version) > 50:
        msg = 'Roblox returned an invalid MacPlayer version'
        raise ManagedLaunchError(msg)
    return version


def _native_installer_running() -> bool:
    try:
        result = subprocess.run(
            ['/usr/bin/pgrep', '-x', 'RobloxPlayerInstaller'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
    except OSError, subprocess.TimeoutExpired:
        return True
    return result.returncode == 0


def native_update_if_needed(
    *, app: Path = _ROBLOX_APP, announce: Callable[[str], None] | None = None
) -> Path:
    """Let the official macOS Roblox updater modify only the real installed app."""
    player = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
    if not player.is_file():
        msg = f'Roblox is not installed at {app}'
        raise ManagedLaunchError(msg)
    newest = latest_roblox_version()
    current = installed_roblox_version(app)
    if current == newest:
        log_buffer.log('Launcher', f'Native Roblox installation is current ({current})')
        return app
    if announce:
        announce('Updating Roblox normally before starting Rosemary...')
    log_buffer.log('Launcher', f'Updating native Roblox from {current or "unknown"} to {newest}')
    try:
        result = subprocess.run(
            ['/usr/bin/open', '-a', str(app)],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = 'Could not start Roblox’s native updater'
        raise ManagedLaunchError(msg) from exc
    if result.returncode:
        msg = (
            f'Roblox native updater could not start: {result.stderr.decode(errors="replace")[:250]}'
        )
        raise ManagedLaunchError(msg)

    deadline = time.monotonic() + _UPDATE_TIMEOUT
    stable_since: float | None = None
    while time.monotonic() < deadline:
        if installed_roblox_version(app) == newest:
            if stable_since is None:
                stable_since = time.monotonic()
            # Allow the official updater to finish writing the bundle before quitting Player.
            if time.monotonic() - stable_since >= 3 and not _native_installer_running():
                break
        else:
            stable_since = None
        time.sleep(0.5)
    else:
        msg = f'Roblox did not finish its native update to {newest}'
        raise ManagedLaunchError(msg)
    if platform_macos.is_roblox_running() and (
        not platform_macos.terminate_roblox() or not platform_macos.wait_for_roblox_exit(10)
    ):
        msg = 'The official updater finished, but native Roblox could not be closed'
        raise ManagedLaunchError(msg)
    if installed_roblox_version(app) != newest:
        msg = 'Roblox installation changed while finishing the update'
        raise ManagedLaunchError(msg)
    log_buffer.log('Launcher', f'Native Roblox update verified at {newest}')
    return app


def _proxy_environment(proxy_url: str) -> dict[str, str]:
    return {
        'HTTP_PROXY': proxy_url,
        'HTTPS_PROXY': proxy_url,
        'ALL_PROXY': proxy_url,
        'http_proxy': proxy_url,
        'https_proxy': proxy_url,
        'all_proxy': proxy_url,
        'NO_PROXY': _LOCAL_EXCLUSIONS,
        'no_proxy': _LOCAL_EXCLUSIONS,
    }


def managed_prefix_directory() -> Path:
    """Keep the builtin-runtime guest filesystem separate from the real macOS app."""
    return CONFIG_DIR / 'runtimes' / 'rosemary' / 'prefix'


def _rosemary_environment(proxy_url: str, *, runtime_executable: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(_proxy_environment(proxy_url))
    env.update(
        {
            'ROSEMARY_OBJC': 'builtin',
            'ROSEMARY_LIBC': 'builtin',
            'ROSEMARY_PREFIX': str(managed_prefix_directory()),
            # Reuse the normal Roblox login and settings, not a second account.
            'ROSEMARY_PREFIX_HOME': 'host',
            # The first-run installer consent also covers the verified MiniDarwin base.
            'ROSEMARY_PREFIX_SETUP': 'download',
            'ROSEMARY_BUNDLED': '1',
            'FLEASION_MANAGED_ROSEMARY': '1',
            'ROSEMARY_VULKAN_LIBRARY': str(
                runtime_executable.parent.parent / 'lib' / 'libMoltenVK.dylib'
            ),
            'ROSEMARY_ENVIRON': '\n'.join(
                f'{name}={value}' for name, value in _proxy_environment(proxy_url).items()
            ),
        }
    )
    return env


def launch_managed(
    config_manager: ConfigManager,
    proxy_master: ProxyMaster,
    *,
    announce: Callable[[str], None] | None = None,
) -> macos_rosemary_memory.RosemarySession:
    """Download with consent beforehand, update the native app, then launch via Rosemary."""
    if sys.platform != 'darwin':
        msg = 'Managed macOS Rosemary launches are only available on macOS'
        raise ManagedLaunchError(msg)
    if macos_rosemary_memory.active_session() is not None:
        msg = 'Roblox is already running through Fleasion-managed Rosemary'
        raise ManagedLaunchError(msg)
    if announce:
        announce('Verifying the pinned Rosemary runtime...')
    executable = macos_rosemary_installer.install_pinned_runtime()
    if announce:
        announce('Checking native Roblox updates...')
    app = native_update_if_needed(announce=announce)
    if platform_macos.is_roblox_running() and (
        not platform_macos.terminate_roblox() or not platform_macos.wait_for_roblox_exit(timeout=10)
    ):
        msg = 'Native Roblox must close before Fleasion can launch it through Rosemary'
        raise ManagedLaunchError(msg)
    if not config_manager.macos_rosemary_enabled:
        config_manager.macos_rosemary_enabled = True
    if config_manager.proxy_mode != 'env':
        config_manager.proxy_mode = 'env'
    if not config_manager.proxy_features_enabled:
        config_manager.proxy_features_enabled = True
    if not proxy_master.is_running:
        proxy_master.start()
    if not proxy_master.wait_for_env_proxy_ready(timeout=_PROXY_TIMEOUT):
        msg = 'Fleasion Env Proxy did not become ready; Roblox was not launched'
        raise ManagedLaunchError(msg)
    if announce:
        announce('Launching Roblox through pinned Rosemary...')
    proxy_url = proxy_master.roblox_env_proxy_url()
    session = macos_rosemary_memory.launch_with_private_memory(
        [str(executable), 'run', '--native', str(app)],
        env=_rosemary_environment(proxy_url, runtime_executable=executable),
    )
    try:
        # A successful spawn is insufficient: prove the pinned process actually
        # opened its private IPC channel inside the built-in prefix runtime.
        regions = session.channel.regions()
        if not regions or not session.running:
            msg = 'Rosemary exited before its private memory channel became ready'
            raise ManagedLaunchError(msg)
    except OSError, RuntimeError, ValueError:
        session.close()
        if session.running:
            session.process.terminate()
        raise
    macos_rosemary_memory.set_active_session(session)
    log_buffer.log(
        'Launcher', f'Managed macOS Rosemary Roblox launched (PID {session.process.pid})'
    )
    return session
