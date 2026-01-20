"""System tray implementation."""

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .gui import AboutWindow, DeleteCacheWindow, LogsWindow, ReplacerConfigWindow, ThemeManager
from .utils import APP_DISCORD, APP_NAME, APP_VERSION, get_icon_path


class SystemTray:
    """System tray icon with menu."""

    def __init__(self, app: QApplication, config_manager, proxy_master):
        self.app = app
        self.config_manager = config_manager
        self.proxy_master = proxy_master

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

        # About
        about_action = QAction('About', self.menu)
        about_action.triggered.connect(self._show_about)
        self.menu.addAction(about_action)

        # Logs
        logs_action = QAction('Logs', self.menu)
        logs_action.triggered.connect(self._show_logs)
        self.menu.addAction(logs_action)

        # Replacer Config
        config_action = QAction('Replacer Config', self.menu)
        config_action.triggered.connect(self._show_replacer_config)
        self.menu.addAction(config_action)

        # Delete Cache
        cache_action = QAction('Delete Cache', self.menu)
        cache_action.triggered.connect(self._show_delete_cache)
        self.menu.addAction(cache_action)

        # Copy Discord Invite
        discord_action = QAction('Copy Discord Invite', self.menu)
        discord_action.triggered.connect(self._copy_discord)
        self.menu.addAction(discord_action)

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

    def _show_about(self):
        """Show About window."""
        window = AboutWindow(self.proxy_master.is_running)
        window.exec()

    def _show_logs(self):
        """Show Logs window."""
        window = LogsWindow()
        window.exec()

    def _show_replacer_config(self):
        """Show Replacer Config window."""
        window = ReplacerConfigWindow(self.config_manager)
        window.exec()

    def _show_delete_cache(self):
        """Show Delete Cache window."""
        window = DeleteCacheWindow()
        window.exec()

    def _copy_discord(self):
        """Copy Discord invite to clipboard."""
        QApplication.clipboard().setText(f'https://{APP_DISCORD}')
        from .utils import show_message_box

        show_message_box(
            APP_NAME, f'Discord invite copied!\n\nhttps://{APP_DISCORD}', 0x40
        )

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
