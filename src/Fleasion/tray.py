"""System tray implementation."""

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .gui import AboutWindow, DeleteCacheWindow, LogsWindow, ReplacerConfigWindow, ThemeManager
from .utils import APP_DISCORD, APP_NAME, APP_VERSION, get_icon_path

APP_KOFI = 'ko-fi.com/fleasion'


class SystemTray:
    """System tray icon with menu."""

    def __init__(self, app: QApplication, config_manager, proxy_master):
        self.app = app
        self.config_manager = config_manager
        self.proxy_master = proxy_master

        # Keep references to open windows to prevent garbage collection
        self.open_windows = []

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
        self.open_dashboard_action = QAction('Open Dashboard on Launch', settings_menu)
        self.open_dashboard_action.setCheckable(True)
        self.open_dashboard_action.setChecked(self.config_manager.open_dashboard_on_launch)
        self.open_dashboard_action.triggered.connect(self._toggle_open_dashboard_on_launch)
        settings_menu.addAction(self.open_dashboard_action)

        self.menu.addMenu(settings_menu)


    def _set_theme(self, theme: str):
        """Set the application theme."""
        # Update checkmarks
        for name, action in self.theme_actions.items():
            action.setChecked(name == theme)

        # Apply theme
        ThemeManager.apply_theme(theme)

        # Save to config
        self.config_manager.theme = theme

    def _toggle_export_naming(self, option: str):
        """Toggle an export naming option."""
        new_state = self.config_manager.toggle_export_naming(option)
        self.export_naming_actions[option].setChecked(new_state)

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

    def _toggle_open_dashboard_on_launch(self):
        """Toggle open dashboard on launch setting."""
        new_state = not self.config_manager.open_dashboard_on_launch
        self.config_manager.open_dashboard_on_launch = new_state
        self.open_dashboard_action.setChecked(new_state)

    def _apply_always_on_top_to_window(self, window):
        """Apply always on top setting to a window."""
        if self.config_manager.always_on_top:
            from PyQt6.QtCore import Qt
            flags = window.windowFlags()
            flags |= Qt.WindowType.WindowStaysOnTopHint
            window.setWindowFlags(flags)

    def _show_about(self):
        """Show About window."""
        window = AboutWindow(self.proxy_master.is_running)
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _show_logs(self):
        """Show Logs window."""
        window = LogsWindow()
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _show_replacer_config(self):
        """Show Replacer Config window."""
        window = ReplacerConfigWindow(self.config_manager, self.proxy_master)
        window.destroyed.connect(lambda: self._remove_window(window))
        self.open_windows.append(window)
        # Note: ReplacerConfigWindow applies always_on_top in its __init__
        window.show()

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
        from PyQt6.QtWidgets import QMessageBox

        QApplication.clipboard().setText(f'https://{APP_DISCORD}')

        msg_box = QMessageBox()
        msg_box.setWindowTitle(APP_NAME)
        msg_box.setText('Discord invite copied!')
        msg_box.setInformativeText(f'https://{APP_DISCORD}')
        msg_box.setIcon(QMessageBox.Icon.Information)
        msg_box.exec()

    def _open_kofi(self):
        """Open Ko-fi page in browser."""
        import webbrowser
        webbrowser.open(f'https://{APP_KOFI}')

    def _exit_app(self):
        """Exit the application."""
        # Stop proxy
        if self.proxy_master.is_running:
            self.proxy_master.stop()

        # Quit Qt app
        self.app.quit()

    def update_status(self):
        """Update the status (called periodically or on proxy state change)."""
        self._update_tooltip()
