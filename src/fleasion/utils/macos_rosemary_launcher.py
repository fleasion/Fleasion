"""macOS Roblox update, proxy readiness, and managed Rosemary launch lifecycle."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import (
    macos_rosemary_gate,
    macos_rosemary_installer,
    macos_rosemary_memory,
    macos_rosemary_update_monitor,
    platform_macos,
)
from fleasion.utils.logging import log_buffer
from fleasion.utils.paths import CONFIG_DIR

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster

_ROBLOX_APP = Path('/Applications/Roblox.app')
_UPDATE_TIMEOUT = 180.0
_MANAGED_PROBE_TIMEOUT = 90.0
_MANAGED_SETTLE_SECONDS = 8.0
_STABLE_STARTUP_SECONDS = 4.0
_NATIVE_RELAUNCH_DELAY = 8.0
_NATIVE_MAX_LAUNCHES = 3
_PROXY_TIMEOUT = 25.0
_STARTUP_TIMEOUT = 25.0
_LOCAL_EXCLUSIONS = 'localhost,127.0.0.1,::1'


class ManagedLaunchError(RuntimeError):
    """Roblox could not be launched through the verified managed environment."""


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


def _launch_native_player(app: Path) -> None:
    """Launch the untouched installed Roblox bundle through LaunchServices."""
    try:
        result = subprocess.run(
            ['/usr/bin/open', '-a', str(app)],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = 'Could not open native Roblox to complete its update'
        raise ManagedLaunchError(msg) from exc
    if result.returncode:
        msg = 'The official Roblox updater could not start natively'
        raise ManagedLaunchError(msg)


def _native_update_handoff(app: Path, *, announce: Callable[[str], None] | None = None) -> None:
    """Let native Roblox update until its own new player reports no pending update."""
    if announce:
        announce('Roblox requested an update; running its native updater...')
    log_buffer.log('Launcher', 'Managed Rosemary requested an update; handing off to native Roblox')
    # A Rosemary-launched guest can spawn a native player before the handoff
    if platform_macos.is_roblox_running() and (
        not platform_macos.terminate_roblox() or not platform_macos.wait_for_roblox_exit(10)
    ):
        msg = 'Could not close the native Roblox spawned during the update transition'
        raise ManagedLaunchError(msg)
    deadline = time.monotonic() + _UPDATE_TIMEOUT
    attempted = 0
    probe = macos_rosemary_update_monitor.PlayerLogProbe()
    installer_probe = macos_rosemary_update_monitor.NativeInstallerLogProbe()
    last_launch = 0.0
    stable_at: float | None = None
    stable_log: Path | None = None
    installer_seen = False
    update_requested = False
    while time.monotonic() < deadline:
        now = time.monotonic()
        if attempted == 0:
            _launch_native_player(app)
            attempted += 1
            last_launch = now
        logs = probe.poll()
        latest = logs[-1] if logs else None
        installer_active = _native_installer_running()
        native_running = platform_macos.is_roblox_running()
        installer_seen |= installer_active
        update_requested |= any(state.updating or state.updater_started for state in logs)
        if installer_probe.completed() and not installer_active:
            if native_running and (
                not platform_macos.terminate_roblox() or not platform_macos.wait_for_roblox_exit(10)
            ):
                msg = 'Could not close Roblox after its official installer completed'
                raise ManagedLaunchError(msg)
            if _native_installer_running():
                msg = 'The native installer restarted during the completed update handoff'
                raise ManagedLaunchError(msg)
            log_buffer.log('Launcher', 'Native Roblox installer finished; relaunching in Rosemary')
            return
        if (
            latest is not None
            and latest.current
            and not latest.updater_started
            and not installer_active
            and native_running
        ):
            if stable_log != latest.path:
                stable_at, stable_log = now, latest.path
            if stable_at is not None and now - stable_at >= _STABLE_STARTUP_SECONDS:
                if not platform_macos.terminate_roblox() or not platform_macos.wait_for_roblox_exit(
                    10
                ):
                    msg = 'Could not close the updated native Roblox before relaunching Rosemary'
                    raise ManagedLaunchError(msg)
                if _native_installer_running():
                    msg = 'The native Roblox installer restarted during handoff'
                    raise ManagedLaunchError(msg)
                log_buffer.log(
                    'Launcher', 'Native Roblox reported no pending update; relaunching Rosemary'
                )
                return
        else:
            stable_at = None
        if (
            not native_running
            and not installer_active
            and now - last_launch >= _NATIVE_RELAUNCH_DELAY
        ):
            if not (installer_seen or update_requested):
                msg = 'Native Roblox exited without showing an updater or a successful update check'
                raise ManagedLaunchError(msg)
            if attempted >= _NATIVE_MAX_LAUNCHES:
                msg = 'Native Roblox repeatedly exited during its update; refusing to retry'
                raise ManagedLaunchError(msg)
            _launch_native_player(app)
            attempted += 1
            last_launch = now
            stable_at = None
        time.sleep(0.25)
    msg = 'Native Roblox did not finish its own updater and stable startup checks'
    raise ManagedLaunchError(msg)


def _managed_updater_requested(
    session: macos_rosemary_memory.RosemarySession,
    runtime: Path,
    app: Path,
    probe: macos_rosemary_update_monitor.PlayerLogProbe,
) -> bool:
    """Wait for Roblox's real update decision, or its trusted installer process."""
    deadline = time.monotonic() + _MANAGED_PROBE_TIMEOUT
    current_since: float | None = None
    exited_since: float | None = None
    while time.monotonic() < deadline:
        logs = probe.poll()
        updates = [log for log in logs if log.updater_started]
        if updates:
            log_buffer.log('Launcher', f'Roblox requested an updater in {updates[-1].path.name}')
            return True
        installer_running = macos_rosemary_update_monitor.managed_installer_running(runtime, app)
        if installer_running and not session.running:
            log_buffer.log('Launcher', 'Rosemary exited after launching a verified installer')
            return True
        now = time.monotonic()
        latest = logs[-1] if logs else None
        if latest is not None and latest.current and not latest.updating:
            if current_since is None:
                current_since = now
            if now - current_since >= _MANAGED_SETTLE_SECONDS:
                return False
        if not session.running:
            if exited_since is None:
                exited_since = now
            if now - exited_since >= 3:
                msg = 'Rosemary exited before Roblox completed its update check'
                raise ManagedLaunchError(msg)
        time.sleep(0.25)
    msg = 'Roblox did not report an update decision through Rosemary'
    raise ManagedLaunchError(msg)


def _stop_owned_updating_guest(
    session: macos_rosemary_memory.RosemarySession,
    runtime: Path,
    app: Path,
) -> None:
    """Close only the Fleasion-managed group and the verified guest installer."""
    macos_rosemary_update_monitor.terminate_managed_installers(runtime, app)
    macos_rosemary_memory.shutdown_active_session(terminate=True)
    try:
        session.process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if session.running:
            try:
                os.killpg(session.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                session.process.wait(timeout=3)
            except subprocess.TimeoutExpired as exc:
                msg = 'Rosemary could not be stopped safely before the native update'
                raise ManagedLaunchError(msg) from exc
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        macos_rosemary_update_monitor.terminate_managed_installers(runtime, app)
        if not macos_rosemary_update_monitor.managed_installer_running(runtime, app):
            return
        time.sleep(0.1)
    msg = 'The Rosemary-launched updater is still running; refusing native handoff'
    raise ManagedLaunchError(msg)


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


def ensure_managed_prefix(
    runtime_executable: Path, *, announce: Callable[[str], None] | None = None
) -> None:
    """Provision Rosemary's verified Darwin base before the IPC startup deadline."""
    prefix = managed_prefix_directory()
    if announce:
        announce('Preparing the isolated Rosemary prefix and verified MiniDarwin base...')
    env = os.environ.copy()
    env.update(
        {
            'ROSEMARY_PREFIX_SETUP': 'download',
            'ROSEMARY_OBJC': 'builtin',
            'ROSEMARY_LIBC': 'builtin',
        }
    )
    try:
        result = subprocess.run(
            [str(runtime_executable), 'prefix', 'create', str(prefix)],
            env=env,
            capture_output=True,
            check=False,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = 'Could not finish preparing the Rosemary prefix'
        raise ManagedLaunchError(msg) from exc
    if result.returncode:
        detail = result.stderr.decode(errors='replace')[-500:].strip()
        msg = f'Rosemary prefix preparation failed: {detail}'
        raise ManagedLaunchError(msg)


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


def _check_managed_session(
    session: macos_rosemary_memory.RosemarySession,
    executable: Path,
    app: Path,
    probe: macos_rosemary_update_monitor.PlayerLogProbe,
) -> bool:
    """Accept an early closed IPC only when Roblox demonstrably started updating."""
    try:
        session.channel.ping()
    except OSError:
        if not any(log.updater_started for log in probe.poll()) and not (
            not session.running
            and macos_rosemary_update_monitor.managed_installer_running(executable, app)
        ):
            raise
    return _managed_updater_requested(session, executable, app, probe)


def _validate_launch_uri(uri: str | None) -> None:
    """Reject external URLs and control characters before guest or host activity."""
    if uri is not None and (
        not uri.startswith(('roblox://', 'roblox-player:1+'))
        or any(char in uri for char in '\r\n\x00')
    ):
        msg = 'Managed Roblox accepts only Roblox launch protocol payloads'
        raise ManagedLaunchError(msg)


def launch_managed(
    config_manager: ConfigManager,
    proxy_master: ProxyMaster,
    *,
    announce: Callable[[str], None] | None = None,
    launch_uri: str | None = None,
    refresh_launch_uri: Callable[[], str] | None = None,
) -> macos_rosemary_memory.RosemarySession:
    """Launch in Rosemary, handing a detected updater to native Roblox once."""
    _validate_launch_uri(launch_uri)
    if refresh_launch_uri is not None and launch_uri is None:
        msg = 'Cannot refresh a missing Roblox launch URI'
        raise ManagedLaunchError(msg)
    if sys.platform != 'darwin':
        msg = 'Managed macOS Rosemary launches are only available on macOS'
        raise ManagedLaunchError(msg)
    if not macos_rosemary_gate.MACOS_ROSEMARY_AVAILABLE:
        msg = 'Managed macOS Rosemary support is disabled until Roblox rendering works'
        raise ManagedLaunchError(msg)
    if macos_rosemary_memory.active_session() is not None:
        msg = 'Roblox is already running through Fleasion-managed Rosemary'
        raise ManagedLaunchError(msg)
    if announce:
        announce('Verifying the pinned Rosemary runtime...')
    executable = macos_rosemary_installer.install_pinned_runtime()
    app = _ROBLOX_APP
    if not (app / 'Contents/MacOS/RobloxPlayer').is_file():
        msg = f'Roblox is not installed at {app}'
        raise ManagedLaunchError(msg)
    ensure_managed_prefix(executable, announce=announce)
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
    proxy_url = proxy_master.roblox_env_proxy_url()
    for attempt in range(2):
        if attempt and refresh_launch_uri is not None:
            launch_uri = refresh_launch_uri()
            _validate_launch_uri(launch_uri)
        if announce:
            announce('Launching Roblox through pinned Rosemary...')
        probe = macos_rosemary_update_monitor.PlayerLogProbe()
        command = [str(executable), 'run', '--native', str(app)]
        if launch_uri is not None:
            # Pass directly to Rosemary's guest; never invoke the host URL handler
            command.extend(('-protocolString', launch_uri))
        session = macos_rosemary_memory.launch_with_private_memory(
            command,
            env=_rosemary_environment(proxy_url, runtime_executable=executable),
        )
        macos_rosemary_memory.set_active_session(session)
        try:
            update_requested = _check_managed_session(session, executable, app, probe)
        except BaseException:
            _stop_owned_updating_guest(session, executable, app)
            raise
        if not update_requested and session.running:
            log_buffer.log(
                'Launcher', f'Managed macOS Rosemary Roblox launched (PID {session.process.pid})'
            )
            return session
        _stop_owned_updating_guest(session, executable, app)
        if not update_requested:
            msg = 'Rosemary exited after its update check'
            raise ManagedLaunchError(msg)
        if attempt:
            msg = (
                'Roblox still requests an update after native installation; refusing an update loop'
            )
            raise ManagedLaunchError(msg)
        _native_update_handoff(app, announce=announce)
    msg = 'The managed Roblox update handoff exhausted its retry budget'
    raise ManagedLaunchError(msg)
