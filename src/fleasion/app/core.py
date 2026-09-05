"""Desktop application assembly and startup."""

from __future__ import annotations

import atexit
import contextlib
import importlib
import os
import platform
import sys
import time
from typing import TYPE_CHECKING

from PySide6.QtCore import QSharedMemory, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from fleasion import __version__
from fleasion.app.cli import parse_application_args
from fleasion.app.compatibility import CompatibilityBoundaryError, call_compatibility_boundary
from fleasion.app.dialogs import (
    ProxyErrorInvoker,
    complete_first_time_setup,
    disable_proxy_features_after_start_failure,
    manual_upstream_credentials_missing,
    prepare_env_proxy_migration,
    prompt_first_time_language,
    schedule_startup_auth_check,
    should_sync_autostart_on_launch,
    show_admin_required_dialog,
    show_desktop_integration_failure,
    show_env_proxy_migration,
    show_env_proxy_stale_hosts_dialog,
    show_roblox_permission_failure,
    show_run_on_boot_failure,
    visible_parent_widget,
    window_handle,
)
from fleasion.app.elevation import is_admin, relaunch_as_admin
from fleasion.app.process_control import (
    SINGLE_INSTANCE_CONTROL_SERVER as _SINGLE_INSTANCE_CONTROL_SERVER,
    kill_other_fleasion_instances,
    other_fleasion_pids as _other_fleasion_pids,
    request_other_fleasion_instances_exit as _request_other_fleasion_instances_exit,
)
from fleasion.app.qt_runtime import (
    check_linux_gui_dependencies,
    configure_opengl_for_legacy_viewers,
    install_gui_sigint_handler,
)
from fleasion.app.repairs import (
    cleanup_hosts_once,
    repair_autostart_once,
    repair_roblox_permissions_once,
    repair_windows_firewall_once,
)
from fleasion.app.restart_handoff import (
    join_restart_handoff,
    publish_restart_handoff,
    restart_abort_requested,
)
from fleasion.app.roblox_launch import (
    RobloxUrlEventFilter,
    arm_windows_gdk_env_proxy_when_ready,
    create_env_proxy_lifecycle,
    launch_roblox_uri_for_instance,
    request_running_instance_launch,
    roblox_uri_from_argv,
)
from fleasion.app.roblox_monitor import RobloxExitMonitor
from fleasion.app.single_instance import (
    SINGLE_INSTANCE_KEY,
    should_reclaim_stale_single_instance,
    single_instance_state,
    start_single_instance_control_server,
)
from fleasion.config import ConfigFolderWatcher, ConfigManager
from fleasion.localization import set_language, tr
from fleasion.modifications import ModificationManager
from fleasion.prejsons import download_prejsons
from fleasion.proxy import ProxyMaster
from fleasion.utils import (
    APP_NAME,
    CONFIG_DIR,
    LOG_FILE,
    get_icon_path,
    log_buffer,
    run_in_thread,
    start_update_check,
    time_tracker,
)
from fleasion.utils.microprofiler import start_microprofiler
from fleasion.utils.qt_diagnostics import install_qt_message_logging

if TYPE_CHECKING:
    from PySide6.QtGui import QSessionManager

    from fleasion.app.tray import SystemTray


def run_application() -> None:
    """Assemble and run the Fleasion desktop application."""
    from fleasion.app.tray import SystemTray  # ruff: ignore[import-outside-top-level]

    args = parse_application_args()
    if args.fleasion_gdk_debugger:
        platform_windows = importlib.import_module('fleasion.utils.platform_windows')
        sys.exit(platform_windows.run_gdk_debugger_command_line())
    if args.cleanup_hosts:
        sys.exit(cleanup_hosts_once())
    if args.repair_autostart:
        sys.exit(
            repair_autostart_once(
                args.fleasion_requesting_user_sid,
                enabled=not args.disable_autostart,
            )
        )
    if args.repair_roblox_permissions:
        sys.exit(repair_roblox_permissions_once(args.fleasion_requesting_user_sid))
    if args.repair_firewall:
        sys.exit(repair_windows_firewall_once())
    pending_roblox_uri = roblox_uri_from_argv()
    if args.install_linux_privileged_helper:
        if not sys.platform.startswith('linux'):
            print(
                'Linux privileged helper installation is only supported on Linux.',
                file=sys.stderr,
            )
            sys.exit(1)
        from fleasion.utils.linux_proxy_helper import (  # ruff: ignore[import-outside-top-level]
            install_privileged_helper,
        )

        result = install_privileged_helper(enable_promptless=args.linux_helper_promptless)
        if not result.get('ok'):
            print(
                f'Failed to install Linux privileged helper: {result.get("error") or result}',
                file=sys.stderr,
            )
            sys.exit(1)
        print(f'Installed Linux privileged helper: {result["helper"]}')
        print(f'Installed Polkit policy: {result["policy"]}')
        if result.get('promptless_rule'):
            print(f'Installed promptless Polkit rule: {result["promptless_rule"]}')
        sys.exit(0)

    suppress_dashboard = args.no_dashboard
    log_buffer.log('App', f'Version {__version__}')

    # Frozen GUI builds do not have a useful console for Qt's native warnings
    # Capture warnings/errors in the normal rotating log before Qt/OpenGL setup
    install_qt_message_logging()
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

    current_platform = platform.system()
    if current_platform not in {'Windows', 'Darwin', 'Linux'}:
        app = QApplication(sys.argv)
        QMessageBox.critical(
            None,
            tr('app.unsupported_operating_system'),
            tr('app.fleasion_supports_windows_macos_and_linux_this'),
            QMessageBox.StandardButton.Ok,
        )
        sys.exit(1)

    # The profiler is opt-in so normal releases do not collect diagnostics
    microprofiler = start_microprofiler(enabled=args.microprofile)
    if microprofiler is not None:
        log_buffer.log('MicroProfiler', f'Writing diagnostics to {microprofiler.output_path}')

    configure_opengl_for_legacy_viewers()

    # Create Qt application
    app = QApplication(sys.argv)
    _sigint_timer = install_gui_sigint_handler(app)
    single_instance_state.app = app
    roblox_url_event_filter = RobloxUrlEventFilter(app)
    app.installEventFilter(roblox_url_event_filter)
    # Qt normally follows each desktop's dialog conventions (GNOME/KDE/Windows),
    # which changes the visual order of standard buttons. Fleasion uses the
    # Windows order everywhere so confirmations have a stable layout
    app.setStyleSheet('QDialogButtonBox, QMessageBox { button-layout: 0; }')
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    if icon_path := get_icon_path():
        app.setWindowIcon(QIcon(str(icon_path)))
        if sys.platform == 'darwin':
            platform_macos = importlib.import_module('fleasion.utils.platform_macos')
            platform_macos.set_application_icon(icon_path)

    if not check_linux_gui_dependencies():
        sys.exit(1)

    if sys.platform == 'darwin' and is_admin():
        QMessageBox.critical(
            None,
            tr('app.do_not_run_with_sudo'),
            tr('app.run_fleasion_as_your_normal_macos_user_2'),
        )
        sys.exit(1)

    # Verified replacements use a three-phase protocol. The final child first
    # proves it survived imports/platform initialization (and, on Windows, UAC)
    # but waits here while the old process still owns both its working Env Proxy
    # and the single-instance slot. Only the parent can release that slot
    restart_handoff: tuple[str, int] | None = None
    restart_handoff_token = args.restart_handoff_token
    restart_handoff_parent_pid = args.restart_handoff_parent_pid
    if restart_handoff_token is not None or restart_handoff_parent_pid is not None:
        if restart_handoff_token is None or restart_handoff_parent_pid is None:
            log_buffer.log('Restart', 'Verified replacement could not enter the handoff protocol')
            sys.exit(1)
        if (
            restart_handoff_parent_pid <= 0
            or args.kill_others
            or (sys.platform == 'win32' and not is_admin())
            or not join_restart_handoff(
                restart_handoff_token,
                restart_handoff_parent_pid,
            )
        ):
            log_buffer.log('Restart', 'Verified replacement could not enter the handoff protocol')
            sys.exit(1)
        restart_handoff = (restart_handoff_token, restart_handoff_parent_pid)

    # Gracefully release the existing instance before claiming shared memory
    # The preserve command reaches the old lifecycle controller before its
    # proxy stops, so an Env Player can be adopted by this replacement
    if args.kill_others and _other_fleasion_pids():
        _request_other_fleasion_instances_exit(
            preserve_env_proxy_player=args.preserve_env_proxy_player
        )

    # Single instance check
    # When we've just been relaunched via UAC elevation, the non-elevated
    # instance may not have fully exited yet, leaving stale shared memory
    # If we're admin, forcibly attach-and-detach to clear it so the
    # elevated instance can take over cleanly
    if is_admin():
        # If launched with --kill-others, kill before clearing stale memory so
        # the shared memory slot is freed by the time we try to claim it
        if args.kill_others:
            kill_other_fleasion_instances()

            time.sleep(0.3)
        stale = QSharedMemory(SINGLE_INSTANCE_KEY)
        if stale.attach():
            stale.detach()

    shared_memory = QSharedMemory(SINGLE_INSTANCE_KEY)
    shared_memory_created = shared_memory.create(1)
    if not shared_memory_created and should_reclaim_stale_single_instance(shared_memory.error()):
        # A hard termination can leave Qt's native shared-memory segment behind
        # on Unix-like platforms. Attach/detach removes it when no real
        # Fleasion GUI process still owns it; Linux proxy helpers are ignored by
        # _other_fleasion_pids() because they are not app instances
        stale = QSharedMemory(SINGLE_INSTANCE_KEY)
        if stale.attach():
            stale.detach()
        shared_memory = QSharedMemory(SINGLE_INSTANCE_KEY)
        shared_memory_created = shared_memory.create(1)

    if shared_memory_created:
        single_instance_state.shared_memory = shared_memory

    another_instance_exists = (
        not shared_memory_created
        and shared_memory.error() == QSharedMemory.SharedMemoryError.AlreadyExists
    )
    if another_instance_exists:
        # Another instance is already running
        if pending_roblox_uri and request_running_instance_launch(pending_roblox_uri):
            sys.exit(0)
        if suppress_dashboard:
            sys.exit(0)
        # Non-admin processes cannot use taskkill on elevated processes — it
        # silently does nothing.  Branch on whether WE are admin rather than
        # trying to inspect the other process's token cross-privilege
        msg_box = QMessageBox()
        msg_box.setWindowTitle(tr('app.already_running'))
        msg_box.setText(tr('app.another_instance_of_fleasion_is_already_running'))
        msg_box.setIcon(QMessageBox.Icon.Warning)

        # Set icon if available
        if icon_path := get_icon_path():
            msg_box.setWindowIcon(QIcon(str(icon_path)))

        msg_box.setInformativeText(tr('app.do_you_want_to_run_another_instance'))

        if is_admin() or sys.platform == 'darwin' or sys.platform.startswith('linux'):
            # Already elevated — can kill any process directly
            kill_others_button = msg_box.addButton(
                tr('app.kill_others'), QMessageBox.ButtonRole.AcceptRole
            )
            kill_requires_elevation = False
        else:
            # Not admin — taskkill on an elevated process silently fails
            # A single "Elevate & Kill Others" relaunches as admin with
            # --kill-others so the elevated copy handles it automatically
            kill_others_button = msg_box.addButton(
                tr('app.elevate_kill_others_recommended'),
                QMessageBox.ButtonRole.AcceptRole,
            )
            kill_requires_elevation = True

        msg_box.addButton(tr('app.run_anyway_bad'), QMessageBox.ButtonRole.AcceptRole)
        cancel_button = msg_box.addButton(tr('app.cancel'), QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(cancel_button)

        msg_box.exec()

        if msg_box.clickedButton() == cancel_button:
            sys.exit(0)

        if msg_box.clickedButton() == kill_others_button:
            if kill_requires_elevation:
                # Relaunch elevated with --kill-others.  The elevated copy will
                # kill the running instance before claiming the shared memory
                # slot — no second dialog shown
                launched = relaunch_as_admin(extra_args='--kill-others')
                if launched:
                    sys.exit(0)
                # UAC denied — the existing admin instance is still running
                # There is no point continuing as a read-only copy alongside it,
                # so exit cleanly
                sys.exit(0)
            else:
                kill_other_fleasion_instances()

        # If "Run Anyway" or "Kill Others" (admin path) is clicked, we proceed
        # Note: shared_memory object will be garbage collected or go out of scope,
        # but since we didn't successfully create it, we don't hold the lock

    # Initialize config manager before the elevation gate so the non-elevated
    # process can still build the prompt UI and show a fallback dialog
    config_manager = ConfigManager()
    set_language(config_manager.language)
    if not suppress_dashboard and not config_manager.first_time_setup_complete:
        prompt_first_time_language(config_manager)
    if sys.platform.startswith('linux'):
        platform_linux = importlib.import_module('fleasion.utils.platform_linux')
        platform_linux.set_linux_client_preference(config_manager.linux_client)
    env_proxy_migration_pending = prepare_env_proxy_migration(config_manager)
    config_manager.settings['_runtime_proxy_debug'] = bool(args.proxy_debug)
    config_manager.settings['_runtime_proxy_debug_mode'] = args.proxy_debug_mode or 'full'

    # Gate non-admin launches before opening the usable GUI. Some Windows setups
    # show UAC as a taskbar item instead of foregrounding it, so startup must
    # block here until UAC is accepted, denied, or fails
    admin_prompt_needed = (
        sys.platform == 'win32'
        and config_manager.proxy_features_enabled
        and config_manager.proxy_mode != 'env'
        and not is_admin()
    )
    if sys.platform == 'darwin' and config_manager.proxy_mode != 'env':
        macos_proxy_helper = importlib.import_module('fleasion.utils.macos_proxy_helper')
        start_proxy = config_manager.proxy_features_enabled and macos_proxy_helper.helper_is_ready()
    else:
        start_proxy = config_manager.proxy_features_enabled and not admin_prompt_needed

    # Start tracking time wasted from the stored total
    time_tracker.init(config_manager.time_wasted_seconds)
    save_time_tracker = time_tracker.save
    atexit.register(save_time_tracker, config_manager)

    proxy_error_invoker = ProxyErrorInvoker()
    proxy_error_invoker.show_proxy_error.connect(proxy_error_invoker.handle_proxy_error)
    tray_ref: dict[str, SystemTray | None] = {'tray': None}

    def _refresh_config_surfaces() -> None:
        config_manager.refresh_config_names()
        tray = tray_ref.get('tray')
        dashboard = getattr(tray, 'dashboard_window', None)
        if dashboard is not None:

            def _refresh_dashboard() -> None:
                dashboard.refresh_configs_from_disk()

            try:
                call_compatibility_boundary(_refresh_dashboard)
            except CompatibilityBoundaryError as wrapped:
                log_buffer.log(
                    'Config',
                    f'Failed to refresh Dashboard after config import: {wrapped.cause}',
                )

    config_folder_watcher = ConfigFolderWatcher(
        config_manager,
        parent=app,
        parent_provider=visible_parent_widget,
    )
    config_folder_watcher.configs_changed.connect(_refresh_config_surfaces)
    app.aboutToQuit.connect(config_folder_watcher.stop)

    def _revert_uncredentialed_manual_upstream() -> None:
        if not manual_upstream_credentials_missing(config_manager):
            return
        previous_mode = config_manager.upstream_transport_mode
        config_manager.upstream_transport_mode = 'auto'
        log_buffer.log(
            'Proxy',
            f'Reset upstream transport from {previous_mode} to auto after '
            '10 seconds without credentials',
        )
        tray = tray_ref.get('tray')
        dashboard = getattr(tray, 'dashboard_window', None)
        settings_tab = getattr(dashboard, '_settings_tab', None)
        if settings_tab is not None:
            settings_tab.refresh_from_config()
        if proxy_master.is_running:

            def _restart_proxy() -> None:
                proxy_master.stop()
                proxy_master.start()

            run_in_thread(_restart_proxy)()

    QTimer.singleShot(10_000, _revert_uncredentialed_manual_upstream)

    def _handle_proxy_features_start_failure(reason: str) -> None:
        disable_proxy_features_after_start_failure(config_manager, tray_ref.get('tray'), reason)

    proxy_error_invoker.disable_proxy_features.connect(_handle_proxy_features_start_failure)

    def _on_proxy_start_error(code: str, details: dict[str, object]) -> None:
        if code == 'upstream_connect_failed':
            if sys.platform == 'win32':
                proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code == 'linux_hosts_read_only':
            proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code == 'linux_helper_unavailable':
            proxy_error_invoker.disable_proxy_features.emit(
                tr('app.linux_proxy_helper.start_denied')
            )
            return
        if code == 'tls_self_test_failed':
            proxy_error_invoker.show_proxy_error.emit(code, dict(details))
            return
        if code not in {
            'port_bind_failed',
            'hosts_write_exhausted',
            'hosts_entries_would_exceed_limit',
            'hosts_file_too_large',
            'hosts_file_repair_failed',
            'macos_ca_patch_failed',
            'roblox_ca_patch_failed',
            'macos_ca_trust_failed',
            'macos_relay_failed',
        }:
            return
        proxy_error_invoker.show_proxy_error.emit(code, dict(details))

    # Initialize proxy master
    proxy_master = ProxyMaster(config_manager, on_proxy_start_error=_on_proxy_start_error)
    proxy_error_invoker.retry_proxy.connect(proxy_master.start)

    # Initialize modification manager (pass cache_scraper for asset-id resolution)
    mod_manager = ModificationManager(
        cache_scraper=getattr(proxy_master, 'cache_scraper', None),
        read_only_lock_enabled=config_manager.lock_roblox_files_read_only,
    )
    if not config_manager.lock_roblox_files_read_only:
        if not config_manager.read_only_lock_migration_v1_complete:
            # One-time cleanup for persistent guards left by older builds,
            # including the old cacert.pem lock
            mod_manager.clear_managed_file_read_only(
                (roblox_dir / 'ssl' / 'cacert.pem' for roblox_dir in mod_manager.roblox_dirs),
                clear_untracked=True,
            )
            config_manager.read_only_lock_migration_v1_complete = True
        else:
            # Exact original modes persisted by the new opt-in guard survive a
            # crash and can be restored without changing unrelated files
            mod_manager.clear_managed_file_read_only(clear_untracked=False)
    macos_bootstrapper_bridge = None
    if sys.platform == 'darwin':
        lazy_module = importlib.import_module('fleasion.modifications.macos_bootstrapper_bridge')
        mac_bootstrapper_bridge_cls = lazy_module.MacBootstrapperBridge

        macos_bootstrapper_bridge = mac_bootstrapper_bridge_cls(
            mod_manager,
            app,
            custom_fflag_seed=lambda: proxy_master.prime_custom_fflag_cache(allow_running=True),
            custom_fflag_prepare=proxy_master.prepare_custom_fflags_for_player_launch,
        )
        app.aboutToQuit.connect(macos_bootstrapper_bridge.stop)

    def _refresh_managed_read_only_guard() -> None:
        try:
            if config_manager.lock_roblox_files_read_only:
                call_compatibility_boundary(mod_manager.protect_managed_files)
        except CompatibilityBoundaryError as wrapped:
            log_buffer.log(
                'Modifications',
                f'Read-only guard refresh failed: {wrapped.cause}',
            )

    # Re-apply saved modifications on launch so the GUI state and Roblox files stay in sync
    run_in_thread(mod_manager.reapply_all)()

    # Shutdown guards
    # Graceful Windows shutdown / log-off: Qt fires commitDataRequest before
    # the session ends, giving us a chance to clean up the hosts file
    def _on_commit_data(_session: QSessionManager) -> None:
        with contextlib.suppress(NameError, AttributeError):
            env_lifecycle.cancel()
        mod_manager.clear_managed_file_read_only()
        proxy_master.stop()
        mod_manager.restore_all()

    app.commitDataRequest.connect(_on_commit_data)

    # Normal Python exit (sys.exit, end of main): last-resort fallback so
    # the hosts file is cleaned up even if the tray Exit path was bypassed
    atexit.register(proxy_master.stop)
    atexit.register(mod_manager.clear_managed_file_read_only)
    atexit.register(mod_manager.restore_all)

    # Start PreJsons download in background
    run_in_thread(download_prejsons)()

    # Check for updates in the background
    start_update_check()

    # Sync launch integrations on every launch (updates if launch method changed)
    # All platforms use per-user launch entries and reconcile from the normal
    # non-elevated GUI process
    autostart_launch_sync_failed = False
    desktop_integration_launch_sync_failed = False
    if config_manager.first_time_setup_complete and config_manager.desktop_integration:
        try:
            lazy_module = importlib.import_module('fleasion.utils.desktop_integration')
            sync_desktop_integration = lazy_module.sync_desktop_integration

            desktop_integration_launch_sync_failed = not sync_desktop_integration(enabled=True)
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            desktop_integration_launch_sync_failed = True
            log_buffer.log('DesktopIntegration', f'Launch desktop integration sync failed: {exc}')

    if config_manager.first_time_setup_complete and should_sync_autostart_on_launch(
        config_manager.run_on_boot
    ):
        try:
            autostart = importlib.import_module('fleasion.utils.autostart')
            autostart_launch_sync_failed = not autostart.sync_autostart(
                enabled=True,
                config_dir=CONFIG_DIR,
                proxy_mode=config_manager.proxy_mode,
            )
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            autostart_launch_sync_failed = True
            log_buffer.log('Autostart', f'Launch autostart sync failed: {exc}')

    # Start proxy only if enabled and we have admin rights
    if (
        start_proxy
        and config_manager.proxy_features_enabled
        and config_manager.proxy_mode == 'env'
        and not show_env_proxy_stale_hosts_dialog()
    ):
        start_proxy = False
        log_buffer.log(
            'Proxy',
            'Proxy startup cancelled because the oversized hosts file was not repaired',
        )

    if start_proxy:
        proxy_master.start()
        _refresh_managed_read_only_guard()
    elif not config_manager.proxy_features_enabled:
        log_buffer.log('Proxy', 'Proxy features disabled in settings: proxy not started')
    elif sys.platform == 'darwin':
        log_buffer.log('Proxy', 'Waiting for the macOS proxy helper before starting interception')
    else:
        log_buffer.log('Proxy', 'Read-only mode: proxy not started (no admin rights)')

    if (
        sys.platform == 'win32'
        and start_proxy
        and config_manager.proxy_mode == 'env'
        and config_manager.proxy_features_enabled
    ):
        arm_windows_gdk_env_proxy_when_ready(proxy_master)

    env_lifecycle = create_env_proxy_lifecycle(
        config_manager, proxy_master, adopted_player=args.preserve_env_proxy_player
    )
    atexit.register(env_lifecycle.cancel)

    # Setup Roblox exit monitor for auto cache deletion (before tray to pass to it)
    roblox_monitor = RobloxExitMonitor(config_manager, proxy_master, mod_manager, env_lifecycle)
    app.aboutToQuit.connect(roblox_monitor.stop)

    # Create system tray
    tray = SystemTray(app, config_manager, proxy_master, mod_manager, roblox_monitor)
    tray_ref['tray'] = tray
    single_instance_state.tray = tray
    app.aboutToQuit.connect(tray.cleanup_tray_icon)
    single_instance_control_server = start_single_instance_control_server(app, tray)
    single_instance_state.control_server = single_instance_control_server

    if env_proxy_migration_pending:
        show_env_proxy_migration(config_manager, roblox_monitor)

    def _handle_roblox_uri_event(target: str) -> None:
        run_in_thread(launch_roblox_uri_for_instance)(tray, target)

    roblox_url_event_filter.roblox_uri_received.connect(_handle_roblox_uri_event)
    roblox_url_event_filter.start()
    if pending_roblox_uri:

        def _launch_pending_roblox_uri(target: str = pending_roblox_uri) -> None:
            run_in_thread(launch_roblox_uri_for_instance)(tray, target)

        QTimer.singleShot(0, _launch_pending_roblox_uri)
    log_buffer.log('App', f'Persistent log file: {LOG_FILE}')
    if autostart_launch_sync_failed:
        QTimer.singleShot(
            0,
            lambda: show_run_on_boot_failure(visible_parent_widget(), config_manager.proxy_mode),
        )
    if desktop_integration_launch_sync_failed:
        QTimer.singleShot(0, lambda: show_desktop_integration_failure(visible_parent_widget()))

    def _check_roblox_permission_failures() -> None:
        denied_dirs = mod_manager.take_permission_denied_dirs()
        if denied_dirs:
            show_roblox_permission_failure(visible_parent_widget(), denied_dirs, mod_manager)
        QTimer.singleShot(500, _check_roblox_permission_failures)

    if sys.platform == 'win32':
        QTimer.singleShot(500, _check_roblox_permission_failures)
    admin_prompt_shown = False

    def _request_admin_once() -> None:
        nonlocal admin_prompt_shown
        if admin_prompt_shown or is_admin():
            return
        admin_prompt_shown = True

        gate = QDialog(None)
        gate.setModal(True)
        gate.setWindowTitle(tr('app.administrator_permission_required'))
        gate.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        gate_layout = QVBoxLayout(gate)
        if sys.platform == 'darwin':
            gate_text = tr('app.admin_gate.macos')
        else:
            gate_text = tr('app.admin_gate.windows')
        gate_label = QLabel(gate_text)
        gate_label.setWordWrap(True)
        gate_layout.addWidget(gate_label)
        if icon_path := get_icon_path():
            gate.setWindowIcon(QIcon(str(icon_path)))
        gate.show()
        gate.raise_()
        gate.activateWindow()
        QApplication.processEvents()

        log_buffer.log('UAC', 'Requesting administrator relaunch from GUI startup path')
        if relaunch_as_admin(parent_hwnd=window_handle(gate)):
            gate.close()
            sys.exit(0)

        gate.close()
        show_admin_required_dialog()

    if admin_prompt_needed:
        _request_admin_once()

    def _install_macos_helper_and_start_proxy() -> None:
        if (
            sys.platform != 'darwin'
            or not config_manager.proxy_features_enabled
            or proxy_master.is_running
        ):
            return

        macos_proxy_helper = importlib.import_module('fleasion.utils.macos_proxy_helper')
        helper_is_ready = macos_proxy_helper.helper_is_ready

        if helper_is_ready():
            proxy_master.start()
            _refresh_managed_read_only_guard()
            return
        if suppress_dashboard:
            log_buffer.log(
                'ProxyHelper',
                'Autostart launch skipped helper installation prompt; open Fleasion normally to install it',
            )
            return

        prompt = QMessageBox(visible_parent_widget())
        prompt.setWindowTitle(tr('app.install_proxy_helper'))
        prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setText(tr('app.install_the_fleasion_macos_proxy_helper'))
        prompt.setInformativeText(tr('app.macos_requires_a_small_root_service_to'))
        install_button = prompt.addButton(
            tr('app.install_helper'), QMessageBox.ButtonRole.AcceptRole
        )
        cancel_button = prompt.addButton(tr('app.not_now'), QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(install_button)
        prompt.exec()
        if prompt.clickedButton() == cancel_button:
            log_buffer.log('ProxyHelper', 'macOS proxy helper installation postponed')
            return

        ok, detail = macos_proxy_helper.install_helper()
        if ok:
            proxy_master.start()
            _refresh_managed_read_only_guard()
            return

        log_buffer.log('ProxyHelper', f'macOS proxy helper installation failed: {detail}')
        QMessageBox.warning(
            visible_parent_widget(),
            tr('app.fleasion_proxy_helper_installation_failed'),
            tr('app.fleasion_could_not_install_or_start_the', value0=detail),
        )

    if sys.platform == 'darwin' and config_manager.proxy_features_enabled and not start_proxy:
        _install_macos_helper_and_start_proxy()

    if restart_handoff is not None:
        restart_handoff_token, restart_handoff_parent_pid = restart_handoff

        def restart_cancelled() -> bool:
            return restart_abort_requested(
                restart_handoff_token,
                restart_handoff_parent_pid,
            )

        replacement_ready = single_instance_control_server is not None
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement could not claim the single-instance control endpoint',
            )
        if replacement_ready and config_manager.proxy_features_enabled:
            if config_manager.proxy_mode == 'env':
                replacement_ready = proxy_master.wait_for_env_proxy_ready(
                    timeout=30.0,
                    cancelled=restart_cancelled,
                )
            else:
                replacement_ready = proxy_master.wait_for_hosts_proxy_ready(
                    timeout=30.0,
                    cancelled=restart_cancelled,
                )
        if replacement_ready and restart_cancelled():
            replacement_ready = False
        if not replacement_ready:
            log_buffer.log(
                'Restart',
                'Replacement did not establish the configured proxy before final handoff',
            )
            try:
                call_compatibility_boundary(proxy_master.stop)
            except CompatibilityBoundaryError as wrapped:
                log_buffer.log(
                    'Restart',
                    f'Replacement proxy cleanup failed: {wrapped.cause}',
                )
            if single_instance_control_server is not None:
                single_instance_control_server.close()
                QLocalServer.removeServer(_SINGLE_INSTANCE_CONTROL_SERVER)
            if shared_memory.isAttached():
                shared_memory.detach()
            sys.exit(1)
        if not publish_restart_handoff(restart_handoff_token):
            sys.exit(1)

    # Warn if no Roblox installations can be found (same scan used for cert injection)
    lazy_module = importlib.import_module('fleasion.proxy.master')
    find_roblox_dirs = lazy_module.find_roblox_dirs

    if not find_roblox_dirs():
        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        no_roblox_msg = QMessageBox(parent)
        if on_top:
            no_roblox_msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        no_roblox_msg.setWindowTitle(tr('app.roblox_not_found'))
        no_roblox_msg.setIcon(QMessageBox.Icon.Warning)
        no_roblox_msg.setText(tr('app.roblox_does_not_appear_to_be_installed'))
        no_roblox_msg.setInformativeText(tr('app.fleasion_could_not_find_any_roblox_installations'))
        no_roblox_msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        if icon_path := get_icon_path():
            no_roblox_msg.setWindowIcon(QIcon(str(icon_path)))
        no_roblox_msg.exec()

    # Setup periodic status update
    status_timer = QTimer()
    status_timer.timeout.connect(tray.update_status)
    status_timer.start(1000)  # Update every second

    # Setup Roblox check timer
    roblox_check_timer = QTimer()
    roblox_check_timer.timeout.connect(roblox_monitor.check_roblox_status)
    roblox_check_timer.start(500)  # Check every 0.5 seconds

    managed_read_only_timer = QTimer()
    managed_read_only_timer.timeout.connect(_refresh_managed_read_only_guard)
    managed_read_only_timer.start(1000)
    QTimer.singleShot(250, _refresh_managed_read_only_guard)
    QTimer.singleShot(1500, _refresh_managed_read_only_guard)

    # Show first-time setup guide if this is the first run
    if not suppress_dashboard and not config_manager.first_time_setup_complete:
        complete_first_time_setup(config_manager, tray)
        tray.show_replacer_config()
    elif not suppress_dashboard and config_manager.open_dashboard_on_launch:
        # Open dashboard on launch if enabled (suppressed when started by autostart task)
        tray.show_replacer_config()

    schedule_startup_auth_check(config_manager, tray, app)

    # Run application
    sys.exit(app.exec())
