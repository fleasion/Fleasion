"""Roblox URI routing and platform-specific Env Proxy launch callbacks."""

from __future__ import annotations

import atexit
import importlib
import sys
from typing import TYPE_CHECKING, cast, override

from PySide6.QtCore import QEvent, QObject, Signal

from fleasion.app.process_control import (
    send_running_instance_command as _send_running_instance_command,
)
from fleasion.proxy.env_lifecycle import EnvProxyLifecycleController
from fleasion.utils import (
    get_roblox_player_exe_path,
    get_roblox_process_identity,
    is_roblox_running,
    launch_as_standard_user,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
)

if TYPE_CHECKING:
    import threading
    from pathlib import Path

    from PySide6.QtGui import QFileOpenEvent

    from fleasion.app.tray import SystemTray
    from fleasion.config import ConfigManager
    from fleasion.proxy import ProxyMaster


class RobloxUrlEventFilter(QObject):
    """Receive Roblox URL open events delivered to the macOS app bundle."""

    roblox_uri_received = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[str] = []

    @staticmethod
    def _event_target(event: QEvent) -> str | None:
        if event.type() != QEvent.Type.FileOpen:
            return None
        file_open_event = cast('QFileOpenEvent', event)
        try:
            url = file_open_event.url()
            target = url.toString() if url.isValid() else ''
        except AttributeError:
            target = ''
        target = str(target).strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            return target
        return None

    @override
    def eventFilter(self, _watched: QObject | None, event: QEvent) -> bool:
        target = self._event_target(event)
        if target is not None:
            if self._ready:
                self.roblox_uri_received.emit(target)
            else:
                self._pending.append(target)
        return False

    def start(self) -> None:
        self._ready = True
        pending = self._pending
        self._pending = []
        for target in pending:
            self.roblox_uri_received.emit(target)


def roblox_uri_from_argv() -> str | None:
    """Return a Roblox deeplink passed by a desktop URI handler, if any."""
    for argument in sys.argv[1:]:
        target = str(argument).strip()
        if target.startswith(('roblox:', 'roblox-player:')):
            return target
    return None


def request_running_instance_launch(target: str, timeout_ms: int = 5000) -> bool:
    """Forward a Roblox deeplink to the already-running Fleasion instance."""
    try:
        return _send_running_instance_command(f'launch-roblox\n{target}\n', timeout_ms)
    except OSError, RuntimeError:
        return False


def launch_roblox_uri_for_instance(tray: SystemTray, target: str) -> bool:
    """Launch a URI through the active proxy mode."""
    if sys.platform.startswith('linux'):
        # Flatpak supplies Fleasion's Env Proxy variables to the selected client while the
        # proxy is active; do not replace a one-time URI with a synthetic launch
        return launch_as_standard_user(target)

    config = tray.config_manager
    if sys.platform == 'darwin' and config.proxy_mode == 'env' and config.proxy_features_enabled:
        monitor = tray.roblox_monitor
        lifecycle = monitor.env_lifecycle if monitor is not None else None
        if lifecycle is not None:
            exe_path = get_roblox_player_exe_path()
            return lifecycle.handle_player_launch(exe_path, target)

    return launch_as_standard_user(target)


def arm_windows_gdk_env_proxy_when_ready(proxy_master: ProxyMaster, timeout: float = 15.0) -> bool:
    """Arm Store/GDK activation with the proxy's finalized loopback port."""
    if not proxy_master.wait_for_env_proxy_ready(timeout=timeout):
        log_buffer.log(
            'Launcher',
            'Xbox/GDK Env Proxy activation was not armed because the proxy did not become ready',
        )
        return False

    platform_windows = importlib.import_module('fleasion.utils.platform_windows')
    if not platform_windows.arm_roblox_gdk_env_proxy(proxy_master.roblox_env_proxy_url()):
        return False
    atexit.register(platform_windows.disarm_roblox_gdk_env_proxy)
    return True


def create_env_proxy_lifecycle(
    config_manager: ConfigManager, proxy_master: ProxyMaster, *, adopted_player: bool = False
) -> EnvProxyLifecycleController:
    """Build the Player lifecycle with callbacks for the current platform."""
    if sys.platform == 'win32':
        from fleasion.utils import platform_windows  # ruff: ignore[import-outside-top-level]

        def _prepare_env_proxy_launch(path: Path) -> bool:
            result = proxy_master.ensure_env_proxy_roblox_ca(path, settle=False)
            if not result.get('success'):
                return False
            proxy_master.rearm_custom_fflag_delivery_for_player_launch()
            return True

        def _relaunch_env_player(
            proxy_url: str,
            _target: str | None,
            force: bool,
            cancel_event: threading.Event,
            _source_exe_path: Path | None,
            _player_already_stopped: bool,
        ) -> bool:
            return platform_windows.relaunch_roblox_with_proxy_env(
                proxy_url,
                force=force,
                cancel_event=cancel_event,
                prepare_launch=_prepare_env_proxy_launch,
            )

        terminate_env_player = platform_windows.close_roblox_for_env_lifecycle

    elif sys.platform == 'darwin':
        from fleasion.utils import platform_macos  # ruff: ignore[import-outside-top-level]

        def _prepare_env_proxy_launch(_path: Path) -> bool:
            proxy_master.rearm_custom_fflag_delivery_for_player_launch()
            return True

        def _relaunch_env_player(  # ruff: ignore[too-many-positional-arguments]
            proxy_url: str,
            target: str | None,
            force: bool,
            cancel_event: threading.Event,
            source_exe_path: Path | None,
            player_already_stopped: bool,
        ) -> bool:
            return platform_macos.relaunch_roblox_with_proxy_env(
                proxy_url,
                target,
                force=force,
                cancel_event=cancel_event,
                source_exe_path=source_exe_path,
                player_already_stopped=player_already_stopped,
                prepare_launch=_prepare_env_proxy_launch,
            )

        terminate_env_player = terminate_roblox

    else:

        def _relaunch_env_player(
            _proxy_url: str,
            _target: str | None,
            _force: bool,
            _cancel_event: threading.Event,
            _source_exe_path: Path | None,
            _player_already_stopped: bool,
        ) -> bool:
            log_buffer.log(
                'Launcher',
                'Linux client Env Proxy is supplied by the client launcher; '
                'synthetic relaunch skipped',
            )
            return False

        terminate_env_player = terminate_roblox

    return EnvProxyLifecycleController(
        config_manager=config_manager,
        proxy_master=proxy_master,
        resolve_player_exe=get_roblox_player_exe_path,
        relaunch_player=_relaunch_env_player,
        is_player_running=is_roblox_running,
        get_player_identity=get_roblox_process_identity,
        terminate_player=terminate_env_player,
        wait_for_player_exit=wait_for_roblox_exit,
        adopted_player=adopted_player,
        max_repairs=2,
    )
