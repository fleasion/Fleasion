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
    QCoreApplication,
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
from PySide6.QtWidgets import QApplication

from .config import ConfigFolderWatcher, ConfigManager
from .helper_modes import dispatch_helper_mode
from .modifications import ModificationManager
from .prejsons import download_prejsons
from .proxy import ProxyMaster
from .proxy.env_lifecycle import EnvProxyLifecycleController
from .qml_api import registration as _registration
from .qml_api.context import AppContext
from .qml_api.image_provider import CacheImageProvider
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
            Path('org.vinegarhq.Sober')
            if sys.platform.startswith('linux')
            else get_roblox_player_exe_path()
        )
        if executable is None:
            log_buffer.log('Launcher', 'Player launch detected but its executable is unavailable')
            return
        run_in_thread(self._lifecycle.handle_player_launch)(executable)

    def _handle_exit(self) -> None:
        intentional = self._lifecycle.consume_intentional_player_exit()
        if not intentional:
            self._lifecycle.note_unexpected_player_exit()
        self._modifications.apply_pending_modifications()
        if self._config.auto_delete_cache_on_exit and not intentional:
            run_in_thread(delete_cache)()


class QmlRuntime(QObject):
    """Own application services, the QML engine, and ordered shutdown."""

    _integrationResult = Signal(str, bool, bool)
    _runtimeError = Signal(str)

    def __init__(self, app: QApplication, args: argparse.Namespace) -> None:
        super().__init__(app)
        self._app = app
        self._args = args
        self._shutting_down = False
        self._startup_reapply_thread: threading.Thread | None = None
        self._proxy_lifecycle_lock = threading.RLock()
        self._config = ConfigManager()
        time_tracker.init(self._config.time_wasted_seconds)
        self._pending_errors: list[str] = []
        self._proxy = ProxyMaster(self._config, on_proxy_start_error=self._on_proxy_error)
        self._modifications = ModificationManager(
            cache_scraper=getattr(self._proxy, 'cache_scraper', None),
            read_only_lock_enabled=self._config.lock_roblox_files_read_only,
        )
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
        self._engine = QQmlApplicationEngine(self)
        self._control_server = QLocalServer(self)
        self._image_provider = CacheImageProvider(self._proxy.cache_manager)
        self._engine.addImageProvider('fleasion-cache', self._image_provider)
        self._roots: list[QObject] = []
        self._integrationResult.connect(self._apply_integration_result)
        self._runtimeError.connect(self._deliver_error, Qt.ConnectionType.QueuedConnection)
        self._wire_runtime()

    @property
    def context(self) -> AppContext:
        return self._context

    def start(self) -> bool:
        self._start_control_server()
        self._startup_reapply_thread = run_in_thread(self._modifications.reapply_all)()
        run_in_thread(download_prejsons)()
        if self._config.proxy_features_enabled:
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
        QTimer.singleShot(0, self._flush_pending_errors)
        return True

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
        if self._config.proxy_mode == 'env' and self._config.close_env_proxy_roblox_on_exit:
            self._lifecycle.close_owned_player_for_exit(timeout=5.0)
        else:
            self._lifecycle.cancel()

    def _stop_proxy_for_shutdown(self) -> None:
        with self._proxy_lifecycle_lock:
            self._proxy.stop()

    def _wire_runtime(self) -> None:
        self._context.quitRequested.connect(self._app.quit)
        self._context.cacheCleanupRequested.connect(self._clear_cache)
        self._context._settings.proxyFeaturesChanged.connect(self._sync_proxy_enabled)
        self._context._settings.proxyRestartRequested.connect(self._restart_proxy)
        self._context.restartRequested.connect(self._restart_proxy)
        self._proxy.register_module_interceptor(self._context._utilities.interceptor())
        self._context._settings.changed.connect(self._on_setting_changed)
        self._context.setupCompleted.connect(self._sync_startup_integrations)
        self._context._startup_repair.retryRequested.connect(self._restart_proxy)
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
        if (
            (sys.platform.startswith('linux') or sys.platform == 'darwin')
            and self._config.proxy_mode == 'env'
            and self._config.proxy_features_enabled
        ):
            executable = (
                Path('org.vinegarhq.Sober')
                if sys.platform.startswith('linux')
                else get_roblox_player_exe_path()
            )
            launched = self._lifecycle.handle_player_launch(executable, target)
        else:
            launched = launch_as_standard_user(target)
        if not launched and not self._shutting_down:
            self._queue_error('Roblox could not be launched from the received Play link.')

    def _on_setting_changed(self, key: str) -> None:
        if key == 'run_on_boot':
            self._sync_integration(key, self._config.run_on_boot)
        elif key == 'desktop_integration':
            self._sync_integration(key, self._config.desktop_integration)

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
        label = 'Run on boot' if key == 'run_on_boot' else 'Desktop integration'
        self._context.errorOccurred.emit(
            f'{label} could not be {"enabled" if enabled else "disabled"}. '
            'The previous setting was restored; details are available in Logs.'
        )

    def _start_proxy(self) -> None:
        with self._proxy_lifecycle_lock:
            if self._shutting_down:
                return
            try:
                self._proxy.start()
                if self._config.lock_roblox_files_read_only:
                    self._modifications.protect_managed_files()
            except Exception as exc:
                self._queue_error(f'Proxy could not start: {exc}')

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
            summary = messages[-1] if messages else 'Roblox cache cleared'
            self._context.notificationRequested.emit('Cache cleared', summary, 'success')

        run_in_thread(clear)()

    def _on_proxy_error(self, code: str, details: dict[str, Any]) -> None:
        if self._context._startup_repair.report_error(code, details):
            return
        detail = str(details.get('error') or details.get('message') or code)
        self._queue_error(f'Proxy error: {detail}')

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
                )

            terminator = close_roblox_for_env_lifecycle
        elif sys.platform == 'darwin':
            from .utils.platform_macos import relaunch_roblox_with_proxy_env

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
                )

            terminator = terminate_roblox
        else:
            from .utils.platform_linux import relaunch_roblox_with_proxy_env

            def relaunch(
                proxy_url: str,
                target: str | None,
                force: bool,
                cancel_event: threading.Event,
                _source_exe: Path | None,
                _already_stopped: bool,
            ) -> bool:
                return relaunch_roblox_with_proxy_env(
                    proxy_url,
                    target,
                    force=force,
                    cancel_event=cancel_event,
                )

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
            max_repairs=2,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument('--no-dashboard', action='store_true')
    parser.add_argument('--proxy-debug', '-proxy-debug', action='store_true')
    parser.add_argument('--proxy-debug-mode', choices=['a', 'b', 'c', 'd', 'e', 'full'])
    args, _unknown = parser.parse_known_args()
    return args


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
        except (TypeError, ValueError):
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


def main() -> None:
    """Start the Fluent QML desktop application."""
    if (helper_exit_code := dispatch_helper_mode(sys.argv[1:])) is not None:
        raise SystemExit(helper_exit_code)
    if platform.system() not in {'Windows', 'Darwin', 'Linux'}:
        raise SystemExit('Fleasion supports Windows, macOS, and Linux.')
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QQuickStyle.setStyle('FluentWinUI3')
    QQuickStyle.setFallbackStyle('Fusion')
    # Qt.labs.platform implements desktop tray icons and native menus through
    # Qt Widgets, while every Fleasion-owned visual remains a QML component
    app = QApplication(sys.argv)
    url_event_filter = RobloxUrlEventFilter(app)
    app.installEventFilter(url_event_filter)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if icon_path := get_icon_path():
        app.setWindowIcon(QIcon(str(icon_path)))
    args = _parse_args()
    pending_roblox_uri = _roblox_uri_from_argv(sys.argv[1:])
    shared_memory = QSharedMemory(_SINGLE_INSTANCE_KEY, app)
    if not shared_memory.create(1):
        if _running_instance_available(
            show_dashboard=not args.no_dashboard and pending_roblox_uri is None,
            roblox_uri=pending_roblox_uri,
        ):
            log_buffer.log('App', 'Another Fleasion instance is already running')
            raise SystemExit(0)
        reclaimed = (
            _claim_after_stale_single_instance(app)
            if shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists
            else None
        )
        if reclaimed is None:
            log_buffer.log('App', 'Another Fleasion instance is already running')
            raise SystemExit(0)
        shared_memory = reclaimed
    runtime = QmlRuntime(app, args)
    if not runtime.start():
        runtime.shutdown()
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
    shared_memory.detach()
    raise SystemExit(exit_code)


__all__ = ['main']


if __name__ == '__main__':
    main()
