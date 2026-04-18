"""System tray implementation."""

import ctypes
import os
import winreg

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .gui import AboutWindow, DeleteCacheWindow, LogsWindow, ReplacerConfigWindow, ThemeManager
from .utils import APP_DISCORD, APP_NAME, APP_VERSION, get_icon_path, run_in_thread

APP_KOFI = 'ko-fi.com/fleasion'
_NOTIFICATION_APP_ID = f'{APP_NAME}.Notifications'
_TOAST_TEMPLATE = '<toast><visual><binding template="ToastGeneric"></binding></visual></toast>'


class SystemTray:
    """System tray icon with menu."""

    def __init__(self, app: QApplication, config_manager, proxy_master, mod_manager=None, roblox_monitor=None):
        self.app = app
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self.mod_manager = mod_manager
        self.roblox_monitor = roblox_monitor

        # Keep references to open windows to prevent garbage collection
        self.open_windows = []
        self.dashboard_window = None
        self._exiting = False
        self._dashboard_close_notice_shown = False
        self._notification_app_id = None

        # Create tray icon
        self.tray = QSystemTrayIcon()
        self._set_icon()
        self._update_tooltip()

        # Create menu
        self.menu = QMenu()
        self._create_menu()
        self.tray.setContextMenu(self.menu)

        # Apply initial theme
        ThemeManager.apply_theme(self.config_manager.theme)

        # Connect tray activation signal
        self.tray.activated.connect(self._on_tray_activated)

        # Show tray icon
        self.tray.show()

    def _set_icon(self):
        """Set the tray icon."""
        if icon_path := get_icon_path():
            self.tray.setIcon(QIcon(str(icon_path)))
        else:
            # Use a default icon if none is available
            self.tray.setIcon(self.app.style().standardIcon(self.app.style().StandardPixmap.SP_ComputerIcon))

    def _update_tooltip(self):
        """Update the tooltip text based on proxy status."""
        status = 'Running' if self.proxy_master.is_running else 'Stopped'
        self.tray.setToolTip(f'{APP_NAME} - {status}')

    def _create_menu(self):
        """Create the tray menu."""
        # Title (disabled)
        title_action = QAction(f'{APP_NAME} v{APP_VERSION}', self.menu)
        title_action.setEnabled(False)
        self.menu.addAction(title_action)

        self.menu.addSeparator()

        # Main action - Dashboard
        config_action = QAction('Dashboard', self.menu)
        config_action.triggered.connect(self._show_replacer_config)
        self.menu.addAction(config_action)

        # Configs submenu
        self.configs_menu = QMenu('Configs', self.menu)
        self.configs_menu.aboutToShow.connect(self._populate_configs_menu)
        self.menu.addMenu(self.configs_menu)

        self.menu.addSeparator()

        # Windows
        cache_action = QAction('Delete Cache', self.menu)
        cache_action.triggered.connect(self._show_delete_cache)
        self.menu.addAction(cache_action)

        logs_action = QAction('Logs', self.menu)
        logs_action.triggered.connect(self._show_logs)
        self.menu.addAction(logs_action)

        about_action = QAction('About', self.menu)
        about_action.triggered.connect(self._show_about)
        self.menu.addAction(about_action)

        self.menu.addSeparator()

        # Discord copy
        discord_action = QAction('Discord', self.menu)
        discord_action.triggered.connect(self._copy_discord)
        self.menu.addAction(discord_action)

        # Donate
        donate_action = QAction('Donate', self.menu)
        donate_action.triggered.connect(self._open_kofi)
        self.menu.addAction(donate_action)

        self.menu.addSeparator()

        # Settings submenu
        self._create_settings_menu()

        self.menu.addSeparator()

        # Exit
        exit_action = QAction('Exit', self.menu)
        exit_action.triggered.connect(self._exit_app)
        self.menu.addAction(exit_action)

    def _populate_configs_menu(self):
        """Populate the Configs submenu with current configs."""
        self.configs_menu.clear()
        for name in self.config_manager.config_names:
            action = QAction(name, self.configs_menu)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_config_enabled(name))
            action.triggered.connect(lambda checked, n=name: self._toggle_config(n))
            self.configs_menu.addAction(action)

    def _toggle_config(self, name: str):
        """Toggle a config's enabled state."""
        self.config_manager.toggle_config_enabled(name)

    def _create_settings_menu(self):
        """Create the Settings submenu."""
        settings_menu = QMenu('Settings', self.menu)

        # Theme submenu
        theme_menu = QMenu('Theme', settings_menu)

        # Theme actions (radio buttons)
        self.theme_actions = {}
        for theme_name in ['System', 'Light', 'Dark']:
            action = QAction(theme_name, theme_menu)
            action.setCheckable(True)
            action.triggered.connect(lambda checked, t=theme_name: self._set_theme(t))
            theme_menu.addAction(action)
            self.theme_actions[theme_name] = action

        # Set current theme as checked
        current_theme = self.config_manager.theme
        if current_theme in self.theme_actions:
            self.theme_actions[current_theme].setChecked(True)

        settings_menu.addMenu(theme_menu)

        # Export naming submenu
        export_menu = QMenu('Export Naming', settings_menu)

        # Export naming actions (checkboxes)
        self.export_naming_actions = {}
        for option in ['name', 'id', 'hash']:
            action = QAction(option.capitalize(), export_menu)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_export_naming_enabled(option))
            action.triggered.connect(lambda checked, opt=option: self._toggle_export_naming(opt))
            export_menu.addAction(action)
            self.export_naming_actions[option] = action

        settings_menu.addMenu(export_menu)

        # Always on Top toggle
        self.always_on_top_action = QAction('Always on Top', settings_menu)
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.setChecked(self.config_manager.always_on_top)
        self.always_on_top_action.triggered.connect(self._toggle_always_on_top)
        settings_menu.addAction(self.always_on_top_action)

        # Open dashboard on launch
        self.open_dashboard_action = QAction('Open Dashboard on Start', settings_menu)
        self.open_dashboard_action.setCheckable(True)
        self.open_dashboard_action.setChecked(self.config_manager.open_dashboard_on_launch)
        self.open_dashboard_action.triggered.connect(self._toggle_open_dashboard_on_launch)
        settings_menu.addAction(self.open_dashboard_action)

        # Auto delete cache on Roblox exit
        self.auto_delete_cache_action = QAction('Auto-Clear Cache on Exit', settings_menu)
        self.auto_delete_cache_action.setCheckable(True)
        self.auto_delete_cache_action.setChecked(self.config_manager.auto_delete_cache_on_exit)
        self.auto_delete_cache_action.triggered.connect(self._toggle_auto_delete_cache)
        settings_menu.addAction(self.auto_delete_cache_action)

        # Clear cache on launch
        self.clear_cache_action = QAction('Clear Cache on Launch', settings_menu)
        self.clear_cache_action.setCheckable(True)
        self.clear_cache_action.setChecked(self.config_manager.clear_cache_on_launch)
        self.clear_cache_action.triggered.connect(self._toggle_clear_cache_on_launch)
        settings_menu.addAction(self.clear_cache_action)

        # Run on Boot (Task Scheduler, admin required)
        import ctypes
        _admin = bool(ctypes.windll.shell32.IsUserAnAdmin()) if hasattr(ctypes, 'windll') else False
        self.run_on_boot_action = QAction(
            'Run on Boot' if _admin else 'Run on Boot (admin required)',
            settings_menu,
        )
        self.run_on_boot_action.setCheckable(True)
        self.run_on_boot_action.setChecked(self.config_manager.run_on_boot)
        self.run_on_boot_action.setEnabled(_admin)
        self.run_on_boot_action.triggered.connect(self._toggle_run_on_boot)
        settings_menu.addAction(self.run_on_boot_action)

        # Close Scraped Games on Open
        self.close_scraped_games_action = QAction('Close Scraped Games on Open', settings_menu)
        self.close_scraped_games_action.setCheckable(True)
        self.close_scraped_games_action.setChecked(self.config_manager.close_scraped_games_on_open)
        self.close_scraped_games_action.triggered.connect(self._toggle_close_scraped_games)
        settings_menu.addAction(self.close_scraped_games_action)

        # Close to Tray
        self.close_to_tray_action = QAction('Close to Tray', settings_menu)
        self.close_to_tray_action.setCheckable(True)
        self.close_to_tray_action.setChecked(self.config_manager.close_to_tray)
        self.close_to_tray_action.triggered.connect(self._toggle_close_to_tray)
        settings_menu.addAction(self.close_to_tray_action)

        # Show Names
        self.show_names_action = QAction('Show Names', settings_menu)
        self.show_names_action.setCheckable(True)
        self.show_names_action.setChecked(self.config_manager.show_names)
        self.show_names_action.triggered.connect(self._toggle_show_names)
        settings_menu.addAction(self.show_names_action)

        # Show User ID
        self.show_creator_id_action = QAction('Show User ID', settings_menu)
        self.show_creator_id_action.setCheckable(True)
        self.show_creator_id_action.setChecked(self.config_manager.show_creator_id)
        self.show_creator_id_action.triggered.connect(self._toggle_show_creator_id)
        settings_menu.addAction(self.show_creator_id_action)

        self.menu.addMenu(settings_menu)


    def _refresh_settings_tab(self):
        """Push current config state to the Settings tab if the dashboard is open."""
        if self.dashboard_window and hasattr(self.dashboard_window, '_settings_tab'):
            self.dashboard_window._settings_tab.refresh_from_config()

    def _set_theme(self, theme: str):
        """Set the application theme."""
        # Update checkmarks
        for name, action in self.theme_actions.items():
            action.setChecked(name == theme)

        # Apply theme
        ThemeManager.apply_theme(theme)

        # Save to config
        self.config_manager.theme = theme
        self._refresh_settings_tab()

    def _toggle_export_naming(self, option: str):
        """Toggle an export naming option."""
        new_state = self.config_manager.toggle_export_naming(option)
        self.export_naming_actions[option].setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_always_on_top(self):
        """Toggle always on top setting."""
        new_state = not self.config_manager.always_on_top
        self.config_manager.always_on_top = new_state
        self.always_on_top_action.setChecked(new_state)

        # Apply to all open windows (only if they're visible)
        from PyQt6.QtCore import Qt
        for window in self.open_windows:
            if window.isVisible():
                flags = window.windowFlags()
                if new_state:
                    flags |= Qt.WindowType.WindowStaysOnTopHint
                else:
                    flags &= ~Qt.WindowType.WindowStaysOnTopHint
                window.setWindowFlags(flags)
                window.show()
        self._refresh_settings_tab()

    def _toggle_open_dashboard_on_launch(self):
        """Toggle open dashboard on launch setting."""
        new_state = not self.config_manager.open_dashboard_on_launch
        self.config_manager.open_dashboard_on_launch = new_state
        self.open_dashboard_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_auto_delete_cache(self):
        """Toggle auto delete cache on Roblox exit setting."""
        new_state = not self.config_manager.auto_delete_cache_on_exit
        self.config_manager.auto_delete_cache_on_exit = new_state
        self.auto_delete_cache_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_run_on_boot(self):
        """Toggle run-on-boot via Windows Task Scheduler (admin only)."""
        import ctypes
        if not (bool(ctypes.windll.shell32.IsUserAnAdmin()) if hasattr(ctypes, 'windll') else False):
            self.run_on_boot_action.setChecked(not self.run_on_boot_action.isChecked())
            return
        from .utils.autostart import sync_autostart
        from .utils import CONFIG_DIR
        checked = self.run_on_boot_action.isChecked()
        ok = sync_autostart(checked, CONFIG_DIR)
        if ok:
            self.config_manager.run_on_boot = checked
            self._refresh_settings_tab()
        else:
            # Revert UI state and show error dialog with detail
            self.run_on_boot_action.setChecked(not checked)
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QMessageBox, QApplication
            _top = QApplication.topLevelWidgets()
            _parent = next((w for w in _top if w.isVisible()), None)
            _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
            _warn = QMessageBox(_parent)
            _warn.setWindowTitle('Run on Boot Failed')
            _warn.setIcon(QMessageBox.Icon.Warning)
            _warn.setText(
                'Failed to register the autostart task.\n'
                'Check the application log for details (autostart errors are logged at ERROR level).\n\n'
                'Ensure Fleasion is running as Administrator.'
            )
            if _on_top:
                _warn.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            _warn.exec()

    def _toggle_clear_cache_on_launch(self):
        """Toggle clear cache on launch setting."""
        new_state = not self.config_manager.clear_cache_on_launch
        self.config_manager.clear_cache_on_launch = new_state
        self.clear_cache_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_close_scraped_games(self):
        """Toggle close scraped games on open setting."""
        new_state = not self.config_manager.close_scraped_games_on_open
        self.config_manager.close_scraped_games_on_open = new_state
        self.close_scraped_games_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_close_to_tray(self):
        """Toggle close to tray setting."""
        new_state = not self.config_manager.close_to_tray
        self.config_manager.close_to_tray = new_state
        self.close_to_tray_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_show_names(self):
        """Toggle Show Names setting."""
        new_state = not self.config_manager.show_names
        self.config_manager.show_names = new_state
        self.show_names_action.setChecked(new_state)
        if self.dashboard_window:
            tab = getattr(self.dashboard_window, '_cache_viewer_tab', None)
            if tab is not None:
                tab._on_show_names_toggled(new_state)
        self._refresh_settings_tab()

    def _toggle_show_creator_id(self):
        """Toggle Show User ID setting."""
        new_state = not self.config_manager.show_creator_id
        self.config_manager.show_creator_id = new_state
        self.show_creator_id_action.setChecked(new_state)
        if self.dashboard_window:
            tab = getattr(self.dashboard_window, '_cache_viewer_tab', None)
            if tab is not None:
                tab._on_show_creator_id_toggled(new_state)
        self._refresh_settings_tab()

    def _apply_always_on_top_to_window(self, window):
        """Apply always on top setting to a window."""
        if self.config_manager.always_on_top:
            from PyQt6.QtCore import Qt
            flags = window.windowFlags()
            flags |= Qt.WindowType.WindowStaysOnTopHint
            window.setWindowFlags(flags)

    def _show_about(self):
        """Show About window."""
        window = AboutWindow()
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _show_logs(self):
        """Show Logs window — only one instance allowed."""
        for w in self.open_windows:
            if isinstance(w, LogsWindow):
                w.show()
                w.raise_()
                w.activateWindow()
                return
        window = LogsWindow()
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _show_replacer_config(self):
        """Show Replacer Config window (Dashboard)."""
        if self.dashboard_window:
            self.dashboard_window.show()
            self.dashboard_window.raise_()
            self.dashboard_window.activateWindow()
            return

        from PyQt6.QtCore import Qt
        window = ReplacerConfigWindow(self.config_manager, self.proxy_master, self.mod_manager, self.roblox_monitor, system_tray=self)
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._on_dashboard_destroyed)
        self.dashboard_window = window
        self.open_windows.append(window)
        # Note: ReplacerConfigWindow applies always_on_top in its __init__
        window.show()

    def _on_dashboard_destroyed(self):
        """Handle dashboard destruction."""
        if self.dashboard_window in self.open_windows:
            self.open_windows.remove(self.dashboard_window)
        self.dashboard_window = None
        if not self._exiting and not self.config_manager.close_to_tray:
            self._exit_app()

    def _toggle_dashboard(self):
        """Toggle dashboard visibility."""
        if self.dashboard_window and self.dashboard_window.isVisible():
            self.dashboard_window.hide()
        else:
            self._show_replacer_config()

    def notify_dashboard_closed(self):
        """Show the tray notice that the app is still running."""
        if self._dashboard_close_notice_shown:
            return

        self._dashboard_close_notice_shown = True
        title = APP_NAME
        message = 'Fleasion is still running in the system tray. Right click and select the exit option to quit.'
        icon_path = get_icon_path()

        if os.name != 'nt':
            if icon_path is not None:
                self.tray.showMessage(title, message, QIcon(str(icon_path)), 10000)
            else:
                self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.NoIcon, 10000)
            return

        if self._show_windows_notification(title, message, icon_path):
            return

        if icon_path is not None:
            self.tray.showMessage(title, message, QIcon(str(icon_path)), 10000)
        else:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.NoIcon, 10000)

    def _show_windows_notification(self, title: str, message: str, icon_path) -> bool:
        """Show a silent Windows toast with the app icon and app identity."""
        try:
            app_id = self._ensure_notification_app_id()
            if not app_id:
                return False

            from win11toast import notify

            notify(
                title=title,
                body=message,
                icon=str(icon_path) if icon_path is not None else None,
                audio={'silent': 'true'},
                duration='short',
                app_id=app_id,
                xml=_TOAST_TEMPLATE,
            )
            return True
        except Exception:
            return False

    def _ensure_notification_app_id(self) -> str | None:
        """Register and cache the AUMID used for Fleasion notifications."""
        if self._notification_app_id:
            return self._notification_app_id

        if os.name != 'nt':
            return None

        app_id = _NOTIFICATION_APP_ID
        icon_path = get_icon_path()

        try:
            key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Classes\AppUserModelId\{app_id}')
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_EXPAND_SZ, APP_NAME)
            winreg.SetValueEx(key, 'IconBackgroundColor', 0, winreg.REG_SZ, '00000000')
            if icon_path is not None:
                winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon_path))
            winreg.SetValueEx(key, 'ShowInSettings', 0, winreg.REG_DWORD, 1)
            try:
                key.Close()
            except Exception:
                pass
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            return None

        self._notification_app_id = app_id
        return app_id

    def _on_tray_activated(self, reason):
        """Handle tray icon activation (e.g., click)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Trigger is usually left-click
            self._toggle_dashboard()

    def _show_delete_cache(self):
        """Show Delete Cache window."""
        window = DeleteCacheWindow()
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _remove_window(self, window):
        """Remove window from tracking list."""
        if window in self.open_windows:
            self.open_windows.remove(window)

    def _copy_discord(self):
        """Copy Discord invite to clipboard."""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QMessageBox, QApplication

        QApplication.clipboard().setText(f'https://{APP_DISCORD}')

        _top = QApplication.topLevelWidgets()
        _parent = next((w for w in _top if w.isVisible()), None)
        _on_top = any(w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint) for w in _top)
        msg_box = QMessageBox(_parent)
        if _on_top:
            msg_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        msg_box.setWindowTitle(APP_NAME)
        msg_box.setText('Discord invite copied!')
        msg_box.setInformativeText(f'https://{APP_DISCORD}')
        msg_box.setIcon(QMessageBox.Icon.Information)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            msg_box.setWindowIcon(QIcon(str(icon_path)))
        msg_box.exec()

    def _open_kofi(self):
        """Open Ko-fi page in browser."""
        import webbrowser
        webbrowser.open(f'https://{APP_KOFI}')

    def _exit_app(self):
        """Exit the application."""
        self._exiting = True
        # Stop proxy: always attempt to stop so startup failures (e.g., UAC rejected)
        # that leave background threads or waiters won't be skipped.
        try:
            # Stop proxy asynchronously to avoid blocking the UI/tray menu
            run_in_thread(self.proxy_master.stop)()
        except Exception:
            # Fall back to synchronous stop if async invocation fails
            try:
                self.proxy_master.stop()
            except Exception:
                pass

        # Quit Qt app
        self.app.quit()

    def update_status(self):
        """Update the status (called periodically or on proxy state change)."""
        self._update_tooltip()
