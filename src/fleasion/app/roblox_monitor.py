"""Roblox process monitoring and launch lifecycle handling."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from fleasion.localization import tr
from fleasion.proxy import check_and_patch_running_roblox_ca
from fleasion.utils import (
    delete_cache,
    get_icon_path,
    get_roblox_player_exe_path,
    get_roblox_process_identity,
    get_roblox_studio_exe_path,
    is_roblox_running,
    is_studio_running,
    log_buffer,
    run_in_thread,
)
from fleasion.utils.platform_linux import (
    selected_linux_client_app_id,
    selected_linux_client_display_name,
)

if sys.platform == 'darwin':
    from fleasion.utils.platform_macos import (
        MacOSRobloxUriInterceptor as _MacOSRobloxUriInterceptor,
    )
else:
    _MacOSRobloxUriInterceptor = None

if sys.platform == 'win32':
    from fleasion.utils import platform_windows as _platform_windows
else:
    _platform_windows = None

if TYPE_CHECKING:
    from fleasion.config import ConfigManager
    from fleasion.modifications import ModificationManager
    from fleasion.proxy.env_lifecycle import EnvProxyLifecycleController
    from fleasion.proxy.master import ProxyMaster
    from fleasion.utils.platform_macos import (
        MacOSRobloxPlayerLaunch,
        MacOSRobloxUriInterceptor,
    )


_MACOS_PLAIN_LAUNCH_CLASSIFICATION_SECONDS = 2.0


class RobloxExitMonitor(QObject):
    """Monitors Roblox process and triggers cache deletion on exit."""

    _studio_detected = Signal()
    player_status_changed = Signal(
        bool
    )  # Emitted when RobloxPlayerBeta opens/closes (True = running)

    def __init__(
        self,
        config_manager: ConfigManager,
        proxy_master: ProxyMaster | None = None,
        mod_manager: ModificationManager | None = None,
        env_lifecycle: EnvProxyLifecycleController | None = None,
    ) -> None:
        super().__init__()
        self.config_manager = config_manager
        self._proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.env_lifecycle = env_lifecycle
        self._status_lock = threading.Lock()
        adopted_running = bool(env_lifecycle and env_lifecycle.owns_player)
        self.was_running = adopted_running
        self._player_was_running = adopted_running
        self._suppress_next_player_exit_cache_delete = False
        self._studio_was_running = False
        self._studio_notified = False
        self._studio_suppress_session = False
        self._macos_uri_interceptor: MacOSRobloxUriInterceptor | None = None
        self._macos_plain_launch_lock = threading.Lock()
        self._macos_plain_launches: dict[int, Path] = {}
        macos_env_proxy_enabled = (
            sys.platform == 'darwin'
            and env_lifecycle is not None
            and proxy_master is not None
            and config_manager.proxy_mode == 'env'
            and config_manager.proxy_features_enabled
        )
        if (
            macos_env_proxy_enabled
            and _MacOSRobloxUriInterceptor is not None
            and callable(getattr(proxy_master, 'wait_for_env_proxy_ready', None))
        ):
            try:
                self._macos_uri_interceptor = _MacOSRobloxUriInterceptor(
                    is_armed=self._macos_uri_interception_armed,
                    on_intercepted=self._handle_macos_uri_interception,
                )
                self._macos_uri_interceptor.start()
            except (
                AttributeError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log_buffer.log(
                    'Launcher',
                    f'Could not start macOS Roblox URI watcher: {type(exc).__name__}: {exc}',
                )
        self._studio_detected.connect(self._on_studio_detected)

    def mark_player_running_at_startup(self) -> None:
        """Record a Player that was already running before monitoring began."""
        self.was_running = True
        self._player_was_running = True

    def is_player_running(self) -> bool:
        """Return whether Roblox Player is currently running."""
        return is_roblox_running()

    def stop(self) -> None:
        """Release the macOS URI watcher during normal application shutdown."""
        with self._macos_plain_launch_lock:
            self._macos_plain_launches.clear()
        interceptor = self._macos_uri_interceptor
        if interceptor is not None:
            interceptor.stop()

    def _macos_uri_interception_armed(self) -> bool:
        """Return whether it is safe to stop and replay a browser URI launch."""
        if sys.platform != 'darwin' or self._proxy_master is None or self.env_lifecycle is None:
            return False
        if (
            self.config_manager.proxy_mode != 'env'
            or not self.config_manager.proxy_features_enabled
            or self.env_lifecycle.owns_player
            or self.env_lifecycle.operation_in_progress
        ):
            return False
        ready = getattr(self._proxy_master, 'wait_for_env_proxy_ready', None)
        if not callable(ready):
            return False
        try:
            return bool(ready(timeout=0.0))
        except OSError, RuntimeError, TypeError, ValueError:
            return False

    def _handle_macos_uri_interception(self, launch: MacOSRobloxPlayerLaunch, target: str) -> None:
        """Run after the watcher has already SIGKILLed the original Player."""
        if self.env_lifecycle is None:
            return
        self._suppress_next_player_exit_cache_delete = True
        with self._macos_plain_launch_lock:
            self._macos_plain_launches.pop(int(launch.pid), None)
        self.env_lifecycle.handle_intercepted_player_launch(Path(launch.executable_path), target)

    def _schedule_macos_plain_launch_fallback(self, exe_path: Path) -> None:
        """Keep ordinary Dock/Finder launches on the existing Env Proxy path."""
        if self._macos_uri_interceptor is None:
            return
        identity = get_roblox_process_identity()
        if not isinstance(identity, tuple) or not identity:
            return
        try:
            pid = int(identity[0])
        except TypeError, ValueError:
            return
        with self._macos_plain_launch_lock:
            if pid in self._macos_plain_launches:
                return
            self._macos_plain_launches[pid] = Path(exe_path)

        def _fallback() -> None:
            time.sleep(_MACOS_PLAIN_LAUNCH_CLASSIFICATION_SECONDS)
            with self._macos_plain_launch_lock:
                pending_exe = self._macos_plain_launches.pop(pid, None)
            if pending_exe is None or self.env_lifecycle is None:
                return
            if (
                self._macos_uri_interceptor is not None
                and self._macos_uri_interceptor.has_claimed_pid(pid)
            ):
                return
            current_identity = get_roblox_process_identity()
            if not isinstance(current_identity, tuple) or not current_identity:
                return
            try:
                if int(current_identity[0]) != pid:
                    return
            except TypeError, ValueError:
                return
            if not self._macos_uri_interception_armed():
                return
            self._suppress_next_player_exit_cache_delete = True
            self.env_lifecycle.handle_player_launch(pending_exe)

        threading.Thread(
            target=_fallback,
            name='FleasionMacOSPlainLaunchFallback',
            daemon=True,
        ).start()

    @run_in_thread
    def check_roblox_status(self) -> None:
        """Coalesce timer ticks so process edges are handled exactly once."""
        if not self._status_lock.acquire(blocking=False):
            return
        try:
            self._check_roblox_status_locked()
        finally:
            self._status_lock.release()

    def _handle_player_launch_detected(self) -> None:
        if sys.platform.startswith('linux'):
            exe_path = Path(selected_linux_client_app_id())
            if self._mod_manager is not None:
                self._mod_manager.refresh_roblox_dirs(reapply_if_changed=True)
            proxy_features_enabled = self.config_manager.proxy_features_enabled
            if (
                self.config_manager.proxy_mode == 'env'
                and self._proxy_master is not None
                and proxy_features_enabled
            ):
                if self.env_lifecycle is not None:
                    run_in_thread(self.env_lifecycle.handle_adopted_player_launch)(exe_path)
            elif self._proxy_master is not None and proxy_features_enabled:
                run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
            elif self._proxy_master is None and proxy_features_enabled:
                run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
            elif not proxy_features_enabled:
                log_buffer.log(
                    'Certificate',
                    f'{selected_linux_client_display_name()} launch detected: proxy features '
                    'disabled, skipping proxy CA refresh',
                )
        else:
            exe_path = get_roblox_player_exe_path()
            if exe_path is None:
                # Process may still be initializing — retry for up to 10 s
                for _ in range(10):
                    time.sleep(1.0)
                    exe_path = get_roblox_player_exe_path()
                    if exe_path is not None:
                        break
            if exe_path is not None:
                proxy_features_enabled = self.config_manager.proxy_features_enabled
                if self._mod_manager is not None:
                    self._mod_manager.refresh_roblox_dirs(reapply_if_changed=True)
                if (
                    sys.platform == 'win32'
                    and self.config_manager.proxy_mode == 'env'
                    and self._proxy_master is not None
                    and proxy_features_enabled
                ):
                    platform_windows = _platform_windows
                    if platform_windows is None:
                        log_buffer.log(
                            'Launcher',
                            'Windows platform support is unavailable; leaving Player untouched',
                        )
                        return

                    if platform_windows.is_roblox_gdk_exe_path(exe_path):
                        gdk_env_proxy_armed = (
                            platform_windows.is_roblox_gdk_env_proxy_armed()
                            or platform_windows.is_gdk_env_proxy_activation_in_progress()
                        )
                        if gdk_env_proxy_armed and self.env_lifecycle is not None:
                            log_buffer.log(
                                'Launcher',
                                'Xbox/GDK Env Proxy package activation supplied the '
                                'initial Player; handing it to Env Proxy lifecycle monitoring',
                            )
                            self._suppress_next_player_exit_cache_delete = True
                            run_in_thread(self.env_lifecycle.handle_adopted_player_launch)(exe_path)
                        else:
                            log_buffer.log(
                                'Launcher',
                                'Xbox/GDK Env Proxy package activation is unavailable; '
                                'leaving the initial package Player untouched',
                            )
                            self._suppress_next_player_exit_cache_delete = False
                    elif platform_windows.is_env_proxy_relaunched_player_running():
                        log_buffer.log(
                            'Launcher',
                            'Roblox Env Proxy Player already running; skipping duplicate launch handling',
                        )
                        self._suppress_next_player_exit_cache_delete = False
                    else:
                        self._suppress_next_player_exit_cache_delete = True

                        def _handle_env_proxy_player_launch() -> None:
                            lifecycle = self.env_lifecycle
                            started = bool(
                                lifecycle is not None and lifecycle.handle_player_launch(exe_path)
                            )
                            if not started:
                                self._suppress_next_player_exit_cache_delete = False

                        run_in_thread(_handle_env_proxy_player_launch)()
                elif (
                    self.config_manager.proxy_mode == 'env'
                    and self._proxy_master is not None
                    and proxy_features_enabled
                ):
                    if self.env_lifecycle is not None:
                        if sys.platform == 'darwin':
                            self._schedule_macos_plain_launch_fallback(exe_path)
                        else:
                            run_in_thread(self.env_lifecycle.handle_player_launch)(exe_path)
                elif self._proxy_master is not None and proxy_features_enabled:
                    run_in_thread(self._proxy_master.refresh_and_restart_roblox)(exe_path)
                elif self._proxy_master is None and proxy_features_enabled:
                    run_in_thread(check_and_patch_running_roblox_ca)(exe_path)
                elif not proxy_features_enabled:
                    log_buffer.log(
                        'Certificate',
                        'Roblox launch detected: proxy features disabled, skipping proxy CA refresh',
                    )
            else:
                log_buffer.log(
                    'Certificate',
                    'Roblox launch detected but could not resolve exe path for CA check',
                )

    def _check_roblox_status_locked(self) -> None:
        """Check if Roblox has exited and trigger cache deletion if needed."""
        is_running = is_roblox_running()
        player_status_observed_at = time.monotonic()
        intentional_player_exit = False

        # Roblox Player: player status changed signal
        if self._player_was_running != is_running:
            self.player_status_changed.emit(is_running)
            if self._proxy_master is not None:
                self._proxy_master.set_roblox_player_running(is_running)
            if self._player_was_running and not is_running and self.env_lifecycle is not None:
                intentional_player_exit = self.env_lifecycle.consume_intentional_player_exit(
                    player_status_observed_at
                )
                if not intentional_player_exit:
                    self.env_lifecycle.note_unexpected_player_exit()

        # Roblox Player: launch detection - check CA cert on new launch
        if not self._player_was_running and is_running:
            self._handle_player_launch_detected()
        self._player_was_running = is_running

        # Roblox Player: auto cache deletion on exit
        if self.config_manager.auto_delete_cache_on_exit:
            if self.was_running and not is_running:
                if intentional_player_exit or self._suppress_next_player_exit_cache_delete:
                    self._suppress_next_player_exit_cache_delete = False
                    log_buffer.log(
                        'Cache',
                        'Roblox exited during env-proxy relaunch; skipping auto cache deletion',
                    )
                else:
                    log_buffer.log('Cache', 'Roblox exited, deleting cache...')
                    run_in_thread(self._delete_cache_background)()
            self.was_running = is_running
        else:
            self.was_running = False

        # Roblox Studio: Env Proxy deliberately leaves Studio untouched
        studio_running = is_studio_running()

        if not self._studio_was_running and studio_running:
            env_proxy_mode = self.config_manager.proxy_mode == 'env'
            if env_proxy_mode:
                log_buffer.log(
                    'Launcher',
                    'Studio launch detected in Env Proxy mode; leaving Studio untouched',
                )
                studio_exe_path = None
            else:
                studio_exe_path = get_roblox_studio_exe_path()
                if studio_exe_path is None:
                    for _ in range(10):
                        time.sleep(1.0)
                        studio_exe_path = get_roblox_studio_exe_path()
                        if studio_exe_path is not None:
                            break

            if (
                not env_proxy_mode
                and studio_exe_path is not None
                and self.config_manager.proxy_features_enabled
            ):
                if sys.platform == 'darwin':
                    log_buffer.log(
                        'Certificate',
                        'Studio launch detected on macOS: skipping proxy CA refresh',
                    )
                else:
                    run_in_thread(check_and_patch_running_roblox_ca)(studio_exe_path)
            elif not env_proxy_mode and studio_exe_path is not None:
                log_buffer.log(
                    'Certificate',
                    'Studio launch detected: proxy features disabled, skipping proxy CA refresh',
                )
            elif not env_proxy_mode:
                log_buffer.log(
                    'Certificate',
                    'Studio launch detected but could not resolve exe path for CA check',
                )

            if (
                not env_proxy_mode
                and not self._studio_suppress_session
                and not self._studio_notified
            ):
                self._studio_notified = True
                self._studio_detected.emit()

        if self._studio_was_running and not studio_running:
            self._studio_notified = False

        self._studio_was_running = studio_running

    def _on_studio_detected(self) -> None:
        """Show the Roblox Studio warning dialog (called on the main thread via signal)."""
        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        dialog = QDialog(parent)
        if on_top:
            dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        dialog.setWindowTitle(tr('app.roblox_studio_detected'))

        layout = QVBoxLayout(dialog)
        layout.setSpacing(12)

        label = QLabel(tr('app.roblox_studio_is_currently_open_asset_modification'))
        label.setWordWrap(True)
        layout.addWidget(label)

        btn_layout = QHBoxLayout()
        suppress_btn = QPushButton(tr('app.don_t_show_for_session'))
        ok_btn = QPushButton(tr('app.ok'))
        ok_btn.setDefault(True)
        ok_btn.setFixedWidth(80)

        btn_layout.addWidget(suppress_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        layout.addLayout(btn_layout)

        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        ok_btn.clicked.connect(dialog.accept)

        def _suppress() -> None:
            self._studio_suppress_session = True
            dialog.accept()

        suppress_btn.clicked.connect(_suppress)
        dialog.exec()

    def _delete_cache_background(self) -> None:
        """Delete cache in background thread."""
        messages = delete_cache()
        for msg in messages:
            log_buffer.log('Cache', msg)
