"""PySide6 and QML application runtime for Fleasion."""

from __future__ import annotations

import argparse
import atexit
import os
import platform
import shlex
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import (
    QEvent,
    QObject,
    QSharedMemory,
    QTimer,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMessageBox

from .config import ConfigFolderWatcher, ConfigManager
from .helper_modes import dispatch_helper_mode
from .localization import set_language, tr, verbatim
from .modifications import ModificationManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .proxy.env_lifecycle import EnvProxyLifecycleController
from .qml_api import registration as _registration
from .qml_api.context import AppContext
from .qml_api.image_provider import CacheImageProvider
from .startup_migrations import prepare_env_proxy_migration, restore_read_only_guard_state
from .utils import (
    APP_NAME,
    CONFIG_DIR,
    delete_cache,
    get_icon_path,
    get_roblox_player_exe_path,
    get_roblox_process_identity,
    is_roblox_running,
    launch_as_standard_user,
    log_buffer,
    run_in_thread,
    terminate_roblox,
    time_tracker,
    wait_for_roblox_exit,
)

_SINGLE_INSTANCE_KEY: Final = 'FleasionSingleInstance'
_SINGLE_INSTANCE_SERVER: Final = 'FleasionSingleInstanceControl'
_MAX_ROBLOX_URI_LENGTH: Final = 8192


def _linux_client_launch_path() -> Path:
    """Return the configured Linux Roblox client's stable launch identity."""
    from .utils.platform_linux import selected_linux_client_app_id

    return Path(selected_linux_client_app_id())


def _linux_client_display_name() -> str:
    """Return the configured Linux Roblox client's display label."""
    from .utils.platform_linux import selected_linux_client_display_name

    return selected_linux_client_display_name()


def _normalized_roblox_uri(value: object) -> str | None:
    target = str(value).strip()
    if (
        not target.startswith(('roblox:', 'roblox-player:'))
        or len(target) > _MAX_ROBLOX_URI_LENGTH
        or any(character in target for character in '\r\n\x00')
    ):
        return None
    return target


def _roblox_uri_from_argv(arguments: list[str]) -> str | None:
    for argument in arguments:
        if target := _normalized_roblox_uri(argument):
            return target
    return None


class RobloxUrlEventFilter(QObject):
    """Queue macOS URL-open events until the runtime can handle them."""

    robloxUriReceived = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ready = False
        self._pending: list[str] = []

    def eventFilter(self, _watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.FileOpen:
            return False
        try:
            url = event.url()  # type: ignore[attr-defined]
            target = _normalized_roblox_uri(url.toString() if url.isValid() else '')
        except AttributeError:
            target = None
        if target is None:
            return False
        if self._ready:
            self.robloxUriReceived.emit(target)
        else:
            self._pending.append(target)
        return True

    def start(self) -> None:
        self._ready = True
        pending, self._pending = self._pending, []
        for target in pending:
            self.robloxUriReceived.emit(target)


class RuntimeMonitor(QObject):
    """Coordinate Player launch edges without coupling them to a visual page."""

    playerRunningChanged = Signal(bool)

    def __init__(
        self,
        config_manager: ConfigManager,
        proxy_master: ProxyMaster,
        modification_manager: ModificationManager,
        lifecycle: EnvProxyLifecycleController,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy = proxy_master
        self._modifications = modification_manager
        self._lifecycle = lifecycle
        self._running = is_roblox_running()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        running = is_roblox_running()
        if running == self._running:
            return
        previous = self._running
        self._running = running
        self.playerRunningChanged.emit(running)
        self._proxy.set_roblox_player_running(running)
        if running:
            self._handle_launch()
        elif previous:
            self._handle_exit()

    def _handle_launch(self) -> None:
        self._modifications.refresh_roblox_dirs(reapply_if_changed=True)
        if not self._config.proxy_features_enabled:
            return
        if self._config.proxy_mode != 'env':
            return
        executable = (
            _linux_client_launch_path()
            if sys.platform.startswith('linux')
            else get_roblox_player_exe_path()
        )
        if executable is None:
            log_buffer.log('Launcher', 'Player launch detected but its executable is unavailable')
            return
        if sys.platform.startswith('linux'):
            run_in_thread(self._lifecycle.handle_adopted_player_launch)(executable)
        else:
            run_in_thread(self._lifecycle.handle_player_launch)(executable)

    def _handle_exit(self) -> None:
        intentional = self._lifecycle.consume_intentional_player_exit()
        if not intentional:
            self._lifecycle.note_unexpected_player_exit()
        self._modifications.apply_pending_modifications()
        if self._config.auto_delete_cache_on_exit and not intentional:
            run_in_thread(delete_cache)()


class _QmlRestartAdapter:
    """Expose the runtime state expected by the legacy restart handoff helpers."""

    def __init__(self, runtime: QmlRuntime) -> None:
        self._runtime = runtime

    @property
    def config_manager(self) -> ConfigManager:
        return self._runtime._config

    @property
    def proxy_master(self) -> ProxyMaster:
        return self._runtime._proxy

    @property
    def roblox_monitor(self) -> _QmlRestartAdapter:
        return self

    @property
    def env_lifecycle(self) -> EnvProxyLifecycleController:
        return self._runtime._lifecycle

    def _exit_app(
        self,
        *,
        preserve_roblox: bool = False,
        force_close_roblox: bool = False,
    ) -> None:
        self._runtime._preserve_player_on_shutdown = preserve_roblox
        self._runtime._force_close_player_on_shutdown = force_close_roblox
        self._runtime._app.quit()

    def _show_replacer_config(self) -> None:
        self._runtime._context.dashboardVisibilityRequested.emit(True)


class QmlRuntime(QObject):
    """Own application services, the QML engine, and ordered shutdown."""

    _integrationResult = Signal(str, bool, bool)
    _runtimeError = Signal(str)
    _authWarningReady = Signal(object)

    def __init__(
        self,
        app: QApplication,
        args: argparse.Namespace,
        *,
        shared_memory: QSharedMemory | None = None,
        config_manager: ConfigManager | None = None,
        env_proxy_migration_pending: bool | None = None,
        start_proxy_on_launch: bool = True,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._args = args
        self._shared_memory = shared_memory
        self._start_proxy_on_launch = start_proxy_on_launch
        self._shutting_down = False
        self._preserve_player_on_shutdown = False
        self._force_close_player_on_shutdown = False
        self._auth_check_scheduled = False
        self._auth_warning_shown = False
        self._startup_reapply_thread: threading.Thread | None = None
        self._proxy_lifecycle_lock = threading.RLock()
        self._config = config_manager or ConfigManager()
        set_language(self._config.language)
        self._env_proxy_migration_pending = (
            prepare_env_proxy_migration(self._config)
            if env_proxy_migration_pending is None
            else env_proxy_migration_pending
        )
        if sys.platform.startswith('linux'):
            from .utils.platform_linux import set_linux_client_preference

            set_linux_client_preference(self._config.linux_client)
        self._config.settings['_runtime_proxy_debug'] = bool(args.proxy_debug)
        self._config.settings['_runtime_proxy_debug_mode'] = args.proxy_debug_mode or 'full'
        time_tracker.init(self._config.time_wasted_seconds)
        self._pending_errors: list[str] = []
        self._proxy = ProxyMaster(self._config, on_proxy_start_error=self._on_proxy_error)
        self._modifications = ModificationManager(
            cache_scraper=getattr(self._proxy, 'cache_scraper', None),
            read_only_lock_enabled=self._config.lock_roblox_files_read_only,
        )
        restore_read_only_guard_state(self._config, self._modifications)
        self._lifecycle = self._create_lifecycle()
        self._monitor = RuntimeMonitor(
            self._config,
            self._proxy,
            self._modifications,
            self._lifecycle,
            self,
        )
        self._context = AppContext(
            self._config,
            self._proxy,
            self._modifications,
            show_dashboard=not args.no_dashboard and self._config.open_dashboard_on_launch,
            parent=self,
        )
        self._config_watcher = ConfigFolderWatcher(self._config, self)
        self._config_watcher.configs_changed.connect(self._context._replacer.refresh)
        self._config_watcher.import_warning.connect(self._on_config_import_warning)
        self._manual_upstream_credentials_timer = QTimer(self)
        self._manual_upstream_credentials_timer.setSingleShot(True)
        self._manual_upstream_credentials_timer.setInterval(10_000)
        self._manual_upstream_credentials_timer.timeout.connect(
            self._revert_uncredentialed_manual_upstream
        )
        self._permission_denied_timer = QTimer(self)
        self._permission_denied_timer.setInterval(500)
        self._permission_denied_timer.timeout.connect(self._poll_modification_permission_failures)
        if sys.platform == 'win32':
            self._permission_denied_timer.start()
        self._engine = QQmlApplicationEngine(self)
        self._control_server = QLocalServer(self)
        self._image_provider = CacheImageProvider(self._proxy.cache_manager)
        self._engine.addImageProvider('fleasion-cache', self._image_provider)
        self._roots: list[QObject] = []
        self._integrationResult.connect(self._apply_integration_result)
        self._runtimeError.connect(self._deliver_error, Qt.ConnectionType.QueuedConnection)
        self._authWarningReady.connect(
            self._deliver_auth_warning, Qt.ConnectionType.QueuedConnection
        )
        self._wire_runtime()
        self._sync_manual_upstream_credentials_timer()

    @property
    def context(self) -> AppContext:
        return self._context

    def start(self) -> bool:
        self._start_control_server()
        self._startup_reapply_thread = run_in_thread(self._modifications.reapply_all)()
        run_in_thread(download_prejsons)()
        if self._config.proxy_features_enabled and self._start_proxy_on_launch:
            run_in_thread(self._start_proxy)()
        if self._config.clear_cache_on_launch:
            self._clear_cache()
        if self._config.first_time_setup_complete:
            self._sync_startup_integrations()
        qml_root = Path(__file__).with_name('qml')
        self._engine.addImportPath(str(qml_root))
        self._engine.rootContext().setContextProperty('App', self._context)
        self._engine.objectCreationFailed.connect(self._on_object_creation_failed)
        self._engine.load(QUrl.fromLocalFile(str(qml_root / 'Main.qml')))
        self._roots = list(self._engine.rootObjects())
        if not self._roots:
            return False
        if self._config.first_time_setup_complete and not self._env_proxy_migration_pending:
            QTimer.singleShot(1500, self._schedule_auth_check)
        if self._env_proxy_migration_pending:
            self._context.dashboardVisibilityRequested.emit(True)
            player_running = bool(self._monitor._running)
            can_apply_now = bool(player_running and self._config.proxy_features_enabled)
            details = tr('app.env_proxy_migration.details')
            if player_running:
                if self._config.proxy_features_enabled:
                    details += (
                        tr(
                            'app.env_proxy_migration.linux_future',
                            client_name=_linux_client_display_name(),
                        )
                        if sys.platform.startswith('linux')
                        else tr('app.env_proxy_migration.player_running')
                    )
                else:
                    details += tr('app.env_proxy_migration.features_disabled')
            self._context.envProxyMigrationRequested.emit(
                tr('app.new_default_roblox_env_proxy'),
                '\n\n'.join((tr('app.fleasion_has_switched_your_saved_proxy_mode'), details)),
                can_apply_now,
                (
                    tr('app.apply_for_future_launches')
                    if sys.platform.startswith('linux')
                    else tr('app.restart_roblox_now')
                ),
                (
                    tr('app.apply_later')
                    if sys.platform.startswith('linux')
                    else tr('app.restart_roblox_later')
                ),
            )
        QTimer.singleShot(0, self._flush_pending_errors)
        return True

    def complete_restart_handoff(self) -> bool:
        """Publish child readiness for a verified restart after proxy startup."""
        token = str(self._args.restart_handoff_token or '')
        parent_pid = int(self._args.restart_handoff_parent_pid or 0)
        if not token and not parent_pid:
            return True
        if not token or parent_pid <= 0:
            log_buffer.log('Restart', 'Replacement has incomplete restart handoff credentials')
            return False

        from . import app as app_module

        cancelled = lambda: app_module._restart_abort_requested(token, parent_pid)
        replacement_ready = self._control_server.isListening()
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement could not claim the single-instance control endpoint',
            )
        if replacement_ready and self._config.proxy_features_enabled:
            if self._config.proxy_mode == 'env':
                replacement_ready = self._proxy.wait_for_env_proxy_ready(
                    timeout=30.0,
                    cancelled=cancelled,
                )
            else:
                replacement_ready = self._proxy.wait_for_hosts_proxy_ready(
                    timeout=30.0,
                    cancelled=cancelled,
                )
        if replacement_ready and cancelled():
            replacement_ready = False
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement did not establish the configured proxy before final handoff',
            )
            return False
        return app_module._publish_restart_handoff(token)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        steps: tuple[tuple[str, Callable[[], Any]], ...] = (
            ('stop runtime monitor', self._monitor.stop),
            ('close instance server', self._control_server.close),
            (
                'remove instance endpoint',
                lambda: QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER),
            ),
            ('stop config watcher', self._config_watcher.stop),
            ('stop upstream credentials timer', self._manual_upstream_credentials_timer.stop),
            ('stop permission repair timer', self._permission_denied_timer.stop),
            ('stop replacer controller', self._context._replacer.shutdown),
            ('stop log controller', self._context._logs.dispose),
            ('stop cache controller', self._context._cache.shutdown),
            ('stop modifications controller', self._context._modifications.shutdown),
            ('stop proxy controller', self._context._proxy.shutdown),
            ('stop settings controller', self._context._settings.shutdown),
            ('stop subplaces controller', self._context._subplaces.shutdown),
            ('stop utilities controller', self._context._utilities.shutdown),
            ('stop update controller', self._context._updates.shutdown),
            ('stop startup repair controller', self._context._startup_repair.shutdown),
            ('cancel pending modification work', self._modifications.cancel_pending_operations),
            ('join startup modification reapply', self._join_startup_reapply),
            ('close owned Roblox player', self._close_owned_player),
            ('clear managed read-only flags', self._modifications.clear_managed_file_read_only),
            ('stop and restore proxy integration', self._stop_proxy_for_shutdown),
            ('restore modified Roblox files', self._modifications.restore_all),
            ('save runtime duration', lambda: time_tracker.save(self._config)),
        )
        for label, operation in steps:
            self._run_shutdown_step(label, operation)

    @staticmethod
    def _run_shutdown_step(label: str, operation: Callable[[], Any]) -> None:
        try:
            operation()
        except Exception as exc:
            log_buffer.log('App', f'Shutdown step failed ({label}): {exc}')

    def _join_startup_reapply(self) -> None:
        startup_reapply = self._startup_reapply_thread
        if startup_reapply is not None and startup_reapply.is_alive():
            startup_reapply.join(timeout=10.0)
        self._startup_reapply_thread = None

    def _close_owned_player(self) -> None:
        if self._preserve_player_on_shutdown:
            self._lifecycle.preserve_owned_player_for_restart()
            return
        if self._force_close_player_on_shutdown or (
            self._config.proxy_mode == 'env' and self._config.close_env_proxy_roblox_on_exit
        ):
            self._lifecycle.close_owned_player_for_exit(timeout=5.0)
            return
        self._lifecycle.cancel()

    def _stop_proxy_for_shutdown(self) -> None:
        with self._proxy_lifecycle_lock:
            self._proxy.stop()

    @Slot(object)
    def _on_commit_data_request(self, _session: object) -> None:
        """Run the full cleanup contract during Windows logoff/session shutdown."""
        self.shutdown()

    @Slot()
    def _poll_modification_permission_failures(self) -> None:
        """Surface protected Windows Roblox installs recorded by modification writes."""
        if self._shutting_down or sys.platform != 'win32':
            return
        denied_dirs = self._modifications.take_permission_denied_dirs()
        if not denied_dirs:
            return
        self._context._startup_repair.report_error(
            'roblox_permission_denied',
            {'paths': [str(path) for path in denied_dirs]},
        )

    @Slot()
    def _reapply_modifications_after_permission_repair(self) -> None:
        """Retry the normal modification write path after the one-shot ACL repair."""
        if self._shutting_down:
            return

        def reapply() -> None:
            try:
                self._modifications.reapply_all()
            except Exception as exc:
                self._queue_error(f'{tr("modifications.error.failed_to_apply")}: {exc}')

        run_in_thread(reapply)()

    def _wire_runtime(self) -> None:
        self._context.quitRequested.connect(self._app.quit)
        self._context.cacheCleanupRequested.connect(self._clear_cache)
        self._context._settings.proxyFeaturesChanged.connect(self._sync_proxy_enabled)
        self._context._settings.proxyRestartRequested.connect(self._restart_proxy)
        self._context._settings.proxyModeTransitionRequested.connect(self._transition_proxy_mode)
        self._context._settings.linuxClientTransitionRequested.connect(
            self._transition_linux_client
        )
        self._context.envProxyMigrationAcknowledged.connect(self._finalize_env_proxy_migration)
        self._proxy.register_module_interceptor(self._context._utilities.interceptor())
        self._context._settings.changed.connect(self._on_setting_changed)
        self._context.setupCompleted.connect(self._sync_startup_integrations)
        self._context.setupCompleted.connect(self._schedule_auth_check)
        self._context._startup_repair.retryRequested.connect(self._restart_proxy)
        self._context._startup_repair.permissionRepairCompleted.connect(
            self._reapply_modifications_after_permission_repair
        )
        self._app.commitDataRequest.connect(self._on_commit_data_request)
        self._app.aboutToQuit.connect(self.shutdown)

    def _start_control_server(self) -> None:
        if not self._control_server.listen(_SINGLE_INSTANCE_SERVER):
            QLocalServer.removeServer(_SINGLE_INSTANCE_SERVER)
            if not self._control_server.listen(_SINGLE_INSTANCE_SERVER):
                log_buffer.log('App', 'The single-instance control endpoint could not start')
                return
        self._control_server.newConnection.connect(self._accept_control_connections)

    @Slot()
    def _accept_control_connections(self) -> None:
        while self._control_server.hasPendingConnections():
            connection = self._control_server.nextPendingConnection()
            if connection is None:
                continue
            connection.readyRead.connect(
                lambda socket=connection: self._read_control_command(socket)
            )
            if connection.bytesAvailable() > 0:
                self._read_control_command(connection)

    def _read_control_command(self, connection: QLocalSocket) -> None:
        command = connection.readAll().toStdString().strip()
        if command == 'show':
            self._context.dashboardVisibilityRequested.emit(True)
        elif command == 'quit':
            self._app.quit()
        elif command == 'quit-preserve-env-player':
            self._preserve_player_on_shutdown = True
            self._app.quit()
        elif command.startswith('launch-roblox\n'):
            self.launchRobloxUri(command.split('\n', 1)[1])
        connection.disconnectFromServer()

    @Slot(str)
    def launchRobloxUri(self, value: str) -> None:  # noqa: N802
        target = _normalized_roblox_uri(value)
        if target is not None and not self._shutting_down:
            run_in_thread(self._launch_roblox_uri)(target)

    def _launch_roblox_uri(self, target: str) -> None:
        if self._shutting_down:
            return
        if sys.platform.startswith('linux'):
            # The selected Flatpak receives Fleasion's Env Proxy override directly;
            # preserve the one-time Roblox URI and let the registered client open it.
            launched = launch_as_standard_user(target)
        elif (
            sys.platform == 'darwin'
            and self._config.proxy_mode == 'env'
            and self._config.proxy_features_enabled
        ):
            launched = self._lifecycle.handle_player_launch(get_roblox_player_exe_path(), target)
        else:
            launched = launch_as_standard_user(target)
        if not launched and not self._shutting_down:
            self._queue_error(tr('qml.dynamic.runtime.play_link_launch_failed'))

    @Slot()
    def _schedule_auth_check(self) -> None:
        """Run the potentially prompting Roblox login discovery off the UI thread."""
        if self._auth_check_scheduled or self._auth_warning_shown or self._shutting_down:
            return
        self._auth_check_scheduled = True

        def check() -> None:
            try:
                from .startup_auth import build_auth_warning
                from .utils.roblox_auth import get_auth_failure_details, get_roblosecurity

                cookie = get_roblosecurity(
                    include_keychain_browsers=sys.platform == 'darwin'
                    or sys.platform.startswith('linux')
                )
                if cookie or self._shutting_down:
                    return
                self._authWarningReady.emit(
                    build_auth_warning(get_auth_failure_details(), platform_name=sys.platform)
                )
            except Exception as exc:
                log_buffer.log(
                    'Auth',
                    f'Unexpected error during startup auth check: {type(exc).__name__}: {exc}',
                )

        run_in_thread(check)()

    @Slot(object)
    def _deliver_auth_warning(self, payload: object) -> None:
        """Present startup auth diagnostics after QML has connected to AppContext."""
        if self._auth_warning_shown or self._shutting_down or not isinstance(payload, dict):
            return
        self._auth_warning_shown = True
        self._context.dashboardVisibilityRequested.emit(True)
        self._context.authWarningRequested.emit(
            str(payload.get('title') or ''),
            str(payload.get('message') or ''),
            str(payload.get('detail') or ''),
            bool(payload.get('can_open_login')),
            str(payload.get('continue_text') or ''),
            str(payload.get('login_text') or ''),
            str(payload.get('exit_text') or ''),
        )

    @Slot(bool)
    def _finalize_env_proxy_migration(self, apply_now: bool) -> None:
        """Finish migration acknowledgement and optionally apply it to a running Player."""
        self._env_proxy_migration_pending = False
        QTimer.singleShot(1500, self._schedule_auth_check)
        if not apply_now or not self._config.proxy_features_enabled or not self._monitor._running:
            return
        executable = (
            _linux_client_launch_path()
            if sys.platform.startswith('linux')
            else get_roblox_player_exe_path()
        )
        if executable is None:
            return
        handler = (
            self._lifecycle.handle_adopted_player_launch
            if sys.platform.startswith('linux')
            else self._lifecycle.handle_player_launch
        )
        run_in_thread(handler)(executable)

    def _on_setting_changed(self, key: str) -> None:
        if key == 'run_on_boot':
            self._sync_integration(key, self._config.run_on_boot)
        elif key == 'desktop_integration':
            self._sync_integration(key, self._config.desktop_integration)
        elif key == 'lock_roblox_files_read_only':
            self._modifications.set_read_only_lock_enabled(self._config.lock_roblox_files_read_only)
        elif key == 'language' and not self._config.first_time_setup_complete:
            set_language(self._config.language)
            self._engine.retranslate()
        elif key in {'upstream_transport', 'upstream_transport_mode'}:
            self._sync_manual_upstream_credentials_timer()

    def _manual_upstream_credentials_missing(self) -> bool:
        mode = self._config.upstream_transport_mode
        if mode == 'http_connect':
            return not bool(
                self._config.upstream_http_connect_username.strip()
                or self._config.upstream_http_connect_password
            )
        if mode == 'socks5':
            return not bool(
                self._config.upstream_socks5_username.strip()
                or self._config.upstream_socks5_password
            )
        return False

    def _sync_manual_upstream_credentials_timer(self) -> None:
        if self._manual_upstream_credentials_missing():
            self._manual_upstream_credentials_timer.start()
        else:
            self._manual_upstream_credentials_timer.stop()

    def _revert_uncredentialed_manual_upstream(self) -> None:
        if not self._manual_upstream_credentials_missing():
            return
        previous_mode = self._config.upstream_transport_mode
        self._config.upstream_transport_mode = 'auto'
        log_buffer.log(
            'Proxy',
            f'Reset upstream transport from {previous_mode} to auto after '
            '10 seconds without credentials',
        )
        self._context._settings.refresh()
        if self._proxy.is_running:
            self._restart_proxy()

    def _sync_autostart_proxy_mode(self, mode: str) -> bool:
        """Refresh an existing autostart entry after the proxy mode changes."""
        if not self._config.run_on_boot:
            return True
        try:
            from .utils.autostart import sync_autostart

            succeeded = sync_autostart(True, CONFIG_DIR, proxy_mode=mode)
        except Exception as exc:
            log_buffer.log('Autostart', f'Run on Boot mode refresh failed: {exc}')
            succeeded = False
        if not succeeded:
            self._context.errorOccurred.emit(
                tr('qml.dynamic.runtime.autostart_proxy_mode_update_failed')
            )
        return succeeded

    @Slot(str, str)
    def _transition_proxy_mode(self, previous_mode: str, new_mode: str) -> None:
        """Apply a proxy-mode change without discarding main's safe handoff semantics."""
        if new_mode == previous_mode or new_mode not in {'env', 'hosts'}:
            return
        self._sync_autostart_proxy_mode(new_mode)

        if new_mode == 'env':
            if not self._config.proxy_features_enabled:
                return
            self._proxy.restart_for_mode_switch()
            if sys.platform == 'win32':
                from .app import _arm_windows_gdk_env_proxy_when_ready

                run_in_thread(_arm_windows_gdk_env_proxy_when_ready)(self._proxy)
            if self._monitor._running:
                executable = (
                    _linux_client_launch_path()
                    if sys.platform.startswith('linux')
                    else get_roblox_player_exe_path()
                )
                if executable is not None:
                    handler = (
                        self._lifecycle.handle_adopted_player_launch
                        if sys.platform.startswith('linux')
                        else self._lifecycle.handle_player_launch
                    )
                    run_in_thread(handler)(executable)
            return

        if not self._config.proxy_features_enabled:
            return
        if self._proxy.can_live_switch_to_hosts():
            self._proxy.restart_for_mode_switch()
            return

        restart_result = self._restart_for_proxy_mode_switch()
        if restart_result is True:
            self._force_close_player_on_shutdown = True
            self._app.quit()
            return
        if restart_result is None:
            self._context.errorOccurred.emit(
                tr('qml.dynamic.runtime.hosts_replacement_exit_unconfirmed')
            )
            return

        self._config.proxy_mode = previous_mode
        self._context._settings.proxyModeChanged.emit()
        self._context._settings.valuesChanged.emit()
        self._sync_autostart_proxy_mode(previous_mode)
        self._context.errorOccurred.emit(tr('qml.dynamic.runtime.hosts_replacement_start_failed'))

    def _restart_for_proxy_mode_switch(self) -> bool | None:
        """Use main's verified restart handoff while adapting QML single-instance ownership."""
        if self._shared_memory is None:
            log_buffer.log('Restart', 'QML runtime has no single-instance lock to transfer')
            return False

        from . import app as app_module

        adapter = _QmlRestartAdapter(self)
        app_module._single_instance_shared_memory = self._shared_memory
        app_module._single_instance_control_server = self._control_server
        app_module._single_instance_app = self._app
        app_module._single_instance_tray = adapter
        require_admin = bool(
            sys.platform == 'win32'
            and self._config.proxy_mode == 'hosts'
            and not app_module._is_admin()
        )
        try:
            return app_module.restart_fleasion_normally(
                verify_startup=True,
                require_admin=require_admin,
            )
        except app_module.RestartHandoffUncertain as exc:
            log_buffer.log('Restart', f'Replacement termination is uncertain: {exc}')
            return None
        finally:
            if app_module._single_instance_shared_memory is not None:
                self._shared_memory = app_module._single_instance_shared_memory
            if app_module._single_instance_control_server is not None:
                self._control_server = app_module._single_instance_control_server

    @Slot(str, str)
    def _transition_linux_client(self, previous_client: str, new_client: str) -> None:
        """Move Linux proxy/modification state from the old client to the new selection."""
        if not sys.platform.startswith('linux') or previous_client == new_client:
            return
        from .utils.platform_linux import set_linux_client_preference

        proxy_was_running = bool(self._proxy.is_running)
        switched = False
        try:
            if proxy_was_running:
                self._proxy.stop()
            self._modifications.restore_all()
            self._config.linux_client = new_client
            set_linux_client_preference(new_client)
            self._modifications.refresh_roblox_dirs(reapply_if_changed=True)
            if proxy_was_running:
                self._proxy.start()
            switched = True
        except Exception as exc:
            log_buffer.log('Settings', f'Linux Roblox client switch failed: {exc}')
            self._config.linux_client = previous_client
            set_linux_client_preference(previous_client)
            try:
                self._modifications.refresh_roblox_dirs(reapply_if_changed=True)
                if proxy_was_running and not self._proxy.is_running:
                    self._proxy.start()
            except Exception as rollback_exc:
                log_buffer.log('Settings', f'Linux Roblox client rollback failed: {rollback_exc}')
            self._context.errorOccurred.emit(tr('qml.dynamic.runtime.linux_client_change_failed'))
        finally:
            self._context._settings.linuxClientChanged.emit()
            self._context._settings.valuesChanged.emit()
            if switched:
                self._context._settings.changed.emit('linux_client')

    def _sync_startup_integrations(self) -> None:
        desktop_enabled = self._config.desktop_integration
        autostart_enabled = self._config.run_on_boot

        def sync_all() -> None:
            try:
                from .utils.desktop_integration import sync_desktop_integration

                desktop_result = sync_desktop_integration(desktop_enabled)
            except Exception:
                desktop_result = False
            self._integrationResult.emit('desktop_integration', desktop_enabled, desktop_result)
            try:
                from .utils.autostart import sync_autostart

                autostart_result = sync_autostart(
                    autostart_enabled,
                    CONFIG_DIR,
                    proxy_mode=self._config.proxy_mode,
                )
            except Exception:
                autostart_result = False
            self._integrationResult.emit('run_on_boot', autostart_enabled, autostart_result)

        run_in_thread(sync_all)()

    def _sync_integration(self, key: str, enabled: bool) -> None:
        def sync() -> None:
            try:
                if key == 'run_on_boot':
                    from .utils.autostart import sync_autostart

                    result = sync_autostart(
                        enabled,
                        CONFIG_DIR,
                        proxy_mode=self._config.proxy_mode,
                    )
                else:
                    from .utils.desktop_integration import sync_desktop_integration

                    result = sync_desktop_integration(enabled)
            except Exception:
                result = False
            self._integrationResult.emit(key, enabled, result)

        run_in_thread(sync)()

    @Slot(str, bool, bool)
    def _apply_integration_result(self, key: str, enabled: bool, succeeded: bool) -> None:
        if succeeded or bool(getattr(self._config, key)) != enabled:
            return
        setattr(self._config, key, not enabled)
        self._context._settings.refresh()
        identifier = (
            'qml.dynamic.runtime.run_on_boot_enable_failed'
            if key == 'run_on_boot' and enabled
            else 'qml.dynamic.runtime.run_on_boot_disable_failed'
            if key == 'run_on_boot'
            else 'qml.dynamic.runtime.desktop_integration_enable_failed'
            if enabled
            else 'qml.dynamic.runtime.desktop_integration_disable_failed'
        )
        self._context.errorOccurred.emit(tr(identifier))

    def _start_proxy(self) -> None:
        with self._proxy_lifecycle_lock:
            if self._shutting_down:
                return
            try:
                self._proxy.start()
                if self._config.lock_roblox_files_read_only:
                    self._modifications.protect_managed_files()
                if (
                    sys.platform == 'win32'
                    and self._config.proxy_mode == 'env'
                    and self._proxy.is_running
                ):
                    from .app import _arm_windows_gdk_env_proxy_when_ready

                    _arm_windows_gdk_env_proxy_when_ready(self._proxy)
            except Exception as exc:
                self._queue_error(tr('qml.dynamic.runtime.proxy_start_failed', error=exc))

    def _sync_proxy_enabled(self) -> None:
        if self._config.proxy_features_enabled:
            run_in_thread(self._start_proxy)()
        else:
            run_in_thread(self._proxy.stop)()

    def _restart_proxy(self) -> None:
        def restart() -> None:
            with self._proxy_lifecycle_lock:
                self._proxy.stop()
                if not self._shutting_down and self._config.proxy_features_enabled:
                    self._start_proxy()

        run_in_thread(restart)()

    def _clear_cache(self) -> None:
        def clear() -> None:
            try:
                messages = delete_cache()
            except Exception as exc:
                self._queue_error(str(exc))
                return
            detail = (
                verbatim('\n'.join(messages)) if messages else tr('ui.gui.delete_cache.clear_cache')
            )
            lowered = detail.casefold()
            severity = (
                'error'
                if any(
                    marker in lowered
                    for marker in ('failed', 'aborted', 'timed out', 'permission denied', 'error')
                )
                else 'success'
            )
            self._context.notificationRequested.emit(
                tr('ui.gui.delete_cache.clear_cache'),
                detail,
                severity,
            )

        run_in_thread(clear)()

    def _on_proxy_error(self, code: str, details: dict[str, Any]) -> None:
        if self._context._startup_repair.report_error(code, details):
            return
        detail = str(details.get('error') or details.get('message') or code)
        self._queue_error(tr('qml.dynamic.runtime.proxy_error', error=detail))

    def _on_config_import_warning(self, message: str, names: list[str]) -> None:
        self._queue_error(message)
        self._config_watcher.acknowledge_import_warning(names)

    def _queue_error(self, message: str) -> None:
        self._runtimeError.emit(message)

    @Slot(str)
    def _deliver_error(self, message: str) -> None:
        if self._roots:
            self._context.errorOccurred.emit(message)
        else:
            self._pending_errors.append(message)

    def _flush_pending_errors(self) -> None:
        for message in self._pending_errors:
            self._context.errorOccurred.emit(message)
        self._pending_errors.clear()

    def _on_object_creation_failed(self, url: QUrl) -> None:
        log_buffer.log('QML', f'Could not create QML root object from {url.toString()}')

    def _create_lifecycle(self) -> EnvProxyLifecycleController:
        if sys.platform == 'win32':
            from .utils.platform_windows import (
                close_roblox_for_env_lifecycle,
                relaunch_roblox_with_proxy_env,
            )

            def prepare_launch(path: Path) -> bool:
                result = self._proxy.ensure_env_proxy_roblox_ca(path, settle=False)
                if not result.get('success'):
                    return False
                self._proxy.rearm_custom_fflag_delivery_for_player_launch()
                return True

            def relaunch(
                proxy_url: str,
                _target: str | None,
                force: bool,
                cancel_event: threading.Event,
                _source_exe: Path | None,
                _already_stopped: bool,
            ) -> bool:
                return relaunch_roblox_with_proxy_env(
                    proxy_url,
                    force=force,
                    cancel_event=cancel_event,
                    prepare_launch=prepare_launch,
                )

            terminator = close_roblox_for_env_lifecycle
        elif sys.platform == 'darwin':
            from .utils.platform_macos import relaunch_roblox_with_proxy_env

            def prepare_launch(_path: Path) -> bool:
                self._proxy.rearm_custom_fflag_delivery_for_player_launch()
                return True

            def relaunch(
                proxy_url: str,
                target: str | None,
                force: bool,
                cancel_event: threading.Event,
                source_exe: Path | None,
                already_stopped: bool,
            ) -> bool:
                return relaunch_roblox_with_proxy_env(
                    proxy_url,
                    target,
                    force=force,
                    cancel_event=cancel_event,
                    source_exe_path=source_exe,
                    player_already_stopped=already_stopped,
                    prepare_launch=prepare_launch,
                )

            terminator = terminate_roblox
        else:

            def relaunch(
                _proxy_url: str,
                _target: str | None,
                _force: bool,
                _cancel_event: threading.Event,
                _source_exe: Path | None,
                _already_stopped: bool,
            ) -> bool:
                log_buffer.log(
                    'Launcher',
                    'Linux client Env Proxy is supplied by the client launcher; '
                    'synthetic relaunch skipped',
                )
                return False

            terminator = terminate_roblox
        return EnvProxyLifecycleController(
            config_manager=self._config,
            proxy_master=self._proxy,
            resolve_player_exe=get_roblox_player_exe_path,
            relaunch_player=relaunch,
            is_player_running=is_roblox_running,
            get_player_identity=get_roblox_process_identity,
            terminate_player=terminator,
            wait_for_player_exit=wait_for_roblox_exit,
            adopted_player=bool(self._args.preserve_env_proxy_player),
            max_repairs=2,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--no-dashboard', action='store_true')
    parser.add_argument('--kill-others', action='store_true')
    parser.add_argument('--proxy-debug', '-proxy-debug', action='store_true')
    parser.add_argument('--proxy-debug-mode', choices=['a', 'b', 'c', 'd', 'e', 'full'])
    parser.add_argument('--microprofile', action='store_true')
    parser.add_argument('--preserve-env-proxy-player', action='store_true')
    parser.add_argument('--restart-handoff-token')
    parser.add_argument('--restart-handoff-parent-pid', type=int)
    args, _unknown = parser.parse_known_args()
    return args


def _restart_handoff_requested(args: argparse.Namespace) -> bool:
    return bool(args.restart_handoff_token or args.restart_handoff_parent_pid)


def _enter_restart_handoff(args: argparse.Namespace) -> bool:
    """Enter main's verified child gate before touching single-instance state."""
    if not _restart_handoff_requested(args):
        return True
    token = str(args.restart_handoff_token or '')
    parent_pid = int(args.restart_handoff_parent_pid or 0)
    if not token or parent_pid <= 0 or args.kill_others:
        log_buffer.log('Restart', 'Verified replacement received invalid handoff arguments')
        return False

    from . import app as app_module

    if sys.platform == 'win32' and not app_module._is_admin():
        log_buffer.log('Restart', 'Verified Windows replacement did not retain elevation')
        return False
    return app_module._join_restart_handoff(token, parent_pid)


def _retire_other_instances_for_relaunch(args: argparse.Namespace) -> None:
    """Honor the legacy --kill-others contract used by normal/elevated relaunches."""
    if not args.kill_others:
        return
    from . import app as app_module

    if app_module._request_other_fleasion_instances_exit(
        preserve_env_proxy_player=bool(args.preserve_env_proxy_player)
    ):
        return
    app_module.kill_other_fleasion_instances()


def _running_instance_available(
    *,
    show_dashboard: bool,
    roblox_uri: str | None = None,
) -> bool:
    connection = QLocalSocket()
    connection.connectToServer(_SINGLE_INSTANCE_SERVER)
    if not connection.waitForConnected(500):
        return False
    target = _normalized_roblox_uri(roblox_uri) if roblox_uri is not None else None
    command = f'launch-roblox\n{target}'.encode() if target else b'show' if show_dashboard else b''
    if command:
        connection.write(command)
        connection.flush()
        connection.waitForBytesWritten(500)
    connection.disconnectFromServer()
    return True


def _other_fleasion_process_exists() -> bool:
    """Return whether another desktop Fleasion process owns a startup race."""
    if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
        return True
    try:
        completed = subprocess.run(
            ['ps', '-axo', 'pid=,ppid=,command='],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except OSError, subprocess.SubprocessError:
        return True
    ignored = {os.getpid(), os.getppid()}
    for line in completed.stdout.splitlines():
        try:
            pid_text, _parent_text, command = line.strip().split(None, 2)
            pid = int(pid_text)
            tokens = shlex.split(command)
        except TypeError, ValueError:
            continue
        if pid in ignored or not tokens:
            continue
        if '--linux-proxy-helper' in tokens or any(
            Path(token).name == 'linux_proxy_helper_daemon.py' for token in tokens
        ):
            continue
        if any(Path(token).name.casefold() == 'fleasion' for token in tokens):
            return True
        if any(Path(token).name == 'launcher.py' for token in tokens):
            return True
        if any(
            token == '-m'
            and index + 1 < len(tokens)
            and tokens[index + 1].casefold() in {'fleasion', 'fleasion.qml_runtime'}
            for index, token in enumerate(tokens)
        ):
            return True
    return False


def _claim_after_stale_single_instance(app: QApplication) -> QSharedMemory | None:
    """Detach a crash-left Unix shared-memory segment and claim it again."""
    if not (sys.platform == 'darwin' or sys.platform.startswith('linux')):
        return None
    if _running_instance_available(show_dashboard=False) or _other_fleasion_process_exists():
        return None
    stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
    if stale.attach():
        stale.detach()
    claimed = QSharedMemory(_SINGLE_INSTANCE_KEY, app)
    return claimed if claimed.create(1) else None


def _check_linux_gui_dependencies() -> bool:
    """Preserve main's native tray dependency preflight for the QML shell."""
    if not sys.platform.startswith('linux'):
        return True
    from .utils.platform_linux import missing_linux_gui_packages

    missing = missing_linux_gui_packages()
    if not missing:
        return True
    package_list = ' '.join(missing)
    install_command = verbatim(f'sudo pacman -S --needed {package_list}')
    log_buffer.log(
        'Linux GUI',
        'A required Arch Linux GUI package is missing.\n'
        f'  Package: {package_list}\n'
        '  Impact: Fleasion cannot reliably publish its system tray icon.\n'
        f'  Install: {install_command}',
    )
    QMessageBox.critical(
        None,
        tr('app.value_system_package_required', value0=APP_NAME),
        tr(
            'app.fleasion_needs_a_system_package_before_its',
            value0=package_list,
            value1=install_command,
        ),
        QMessageBox.StandardButton.Ok,
    )
    return False


def _claim_single_instance(
    app: QApplication,
    args: argparse.Namespace,
    pending_roblox_uri: str | None,
) -> QSharedMemory | None:
    """Claim the desktop singleton after restart/elevation handoffs have settled."""
    if sys.platform == 'win32' and args.kill_others:
        from . import app as app_module

        if app_module._is_admin():
            stale = QSharedMemory(_SINGLE_INSTANCE_KEY)
            if stale.attach():
                stale.detach()

    shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY, app)
    if shared_memory.create(1):
        return shared_memory
    if _running_instance_available(
        show_dashboard=not args.no_dashboard and pending_roblox_uri is None,
        roblox_uri=pending_roblox_uri,
    ):
        log_buffer.log('App', 'Another Fleasion instance is already running')
        return None
    reclaimed = (
        _claim_after_stale_single_instance(app)
        if shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists
        else None
    )
    if reclaimed is None:
        log_buffer.log('App', 'Another Fleasion instance is already running')
    return reclaimed


def main() -> None:
    """Start the Fluent QML desktop application."""
    if (helper_exit_code := dispatch_helper_mode(sys.argv[1:])) is not None:
        raise SystemExit(helper_exit_code)
    if platform.system() not in {'Windows', 'Darwin', 'Linux'}:
        raise SystemExit('Fleasion supports Windows, macOS, and Linux.')

    # Keep Qt startup diagnostics and OpenGL policy aligned with main. In
    # particular, do not enable AA_ShareOpenGLContexts: the preview renderers
    # own independent contexts and Windows startup must stay GL-free.
    from .utils.qt_diagnostics import install_qt_message_logging

    install_qt_message_logging()
    if sys.platform.startswith('linux'):
        os.environ.setdefault('QT_OPENGL', 'desktop')
    log_buffer.log(
        'App',
        'Runtime '
        f'{platform.system()} {platform.release()} ({platform.version()}) '
        f'{platform.machine()}; Python {platform.python_version()}; '
        f'frozen={bool(getattr(sys, "frozen", False))}',
    )
    log_buffer.log(
        'Qt',
        'Graphics env: '
        f'QT_OPENGL={os.environ.get("QT_OPENGL", "<unset>")}, '
        f'QT_QPA_PLATFORM={os.environ.get("QT_QPA_PLATFORM", "<unset>")}, '
        f'QT_ANGLE_PLATFORM={os.environ.get("QT_ANGLE_PLATFORM", "<unset>")}',
    )

    QQuickStyle.setStyle('FluentWinUI3')
    QQuickStyle.setFallbackStyle('Fusion')
    # Qt.labs.platform implements desktop tray icons and native menus through
    # Qt Widgets, while every Fleasion-owned visual remains a QML component.
    app = QApplication(sys.argv)
    url_event_filter = RobloxUrlEventFilter(app)
    app.installEventFilter(url_event_filter)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if icon_path := get_icon_path():
        app.setWindowIcon(QIcon(str(icon_path)))

    args = _parse_args()
    from .utils.microprofiler import start_microprofiler

    microprofiler = start_microprofiler(enabled=args.microprofile)
    if microprofiler is not None:
        log_buffer.log('MicroProfiler', f'Writing diagnostics to {microprofiler.output_path}')

    # Verified replacements must publish "prepared" and wait for the old
    # process to release singleton ownership before trying to claim it.
    if not _enter_restart_handoff(args):
        raise SystemExit(1)
    _retire_other_instances_for_relaunch(args)

    pending_roblox_uri = _roblox_uri_from_argv(sys.argv[1:])
    shared_memory = _claim_single_instance(app, args, pending_roblox_uri)
    if shared_memory is None:
        raise SystemExit(1 if _restart_handoff_requested(args) else 0)

    config = ConfigManager()
    set_language(config.language)
    env_proxy_migration_pending = prepare_env_proxy_migration(config)

    if not _check_linux_gui_dependencies():
        if shared_memory.isAttached():
            shared_memory.detach()
        raise SystemExit(1)

    from . import app as app_module

    if sys.platform == 'darwin' and app_module._is_admin():
        QMessageBox.critical(
            None,
            tr('app.do_not_run_with_sudo'),
            tr('app.run_fleasion_as_your_normal_macos_user_2'),
            QMessageBox.StandardButton.Ok,
        )
        if shared_memory.isAttached():
            shared_memory.detach()
        raise SystemExit(1)

    # Hosts mode on Windows still requires an elevated process. Preserve main's
    # one-time UAC relaunch rather than letting the QML runtime start a proxy
    # that cannot own port 443 or mutate hosts.
    start_proxy_on_launch = True
    admin_prompt_denied = False
    if (
        sys.platform == 'win32'
        and config.proxy_features_enabled
        and config.proxy_mode == 'hosts'
        and not app_module._is_admin()
    ):
        if app_module._relaunch_as_admin():
            if shared_memory.isAttached():
                shared_memory.detach()
            raise SystemExit(0)
        start_proxy_on_launch = False
        admin_prompt_denied = True

    runtime = QmlRuntime(
        app,
        args,
        shared_memory=shared_memory,
        config_manager=config,
        env_proxy_migration_pending=env_proxy_migration_pending,
        start_proxy_on_launch=start_proxy_on_launch,
    )
    if admin_prompt_denied:
        runtime._queue_error(tr('app.windows_did_not_start_fleasion_with_administrator'))
    if not runtime.start():
        runtime.shutdown()
        if shared_memory.isAttached():
            shared_memory.detach()
        raise SystemExit(1)
    if not runtime.complete_restart_handoff():
        runtime.shutdown()
        if shared_memory.isAttached():
            shared_memory.detach()
        raise SystemExit(1)

    url_event_filter.robloxUriReceived.connect(runtime.launchRobloxUri)
    url_event_filter.start()
    if pending_roblox_uri is not None:
        QTimer.singleShot(
            0,
            lambda target=pending_roblox_uri: runtime.launchRobloxUri(target),
        )
    atexit.register(runtime.shutdown)
    signal.signal(signal.SIGINT, lambda _signum, _frame: app.quit())
    exit_code = app.exec()
    if shared_memory.isAttached():
        shared_memory.detach()
    raise SystemExit(exit_code)


__all__ = ['main']


if __name__ == '__main__':
    main()
