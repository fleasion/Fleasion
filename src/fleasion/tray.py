"""System tray implementation."""

from __future__ import annotations

import contextlib
import ctypes
import importlib
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, override

if sys.platform == 'win32':
    import winreg

from PySide6.QtCore import QRect, QSignalBlocker, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .app import (
    RestartHandoffUncertain,
    _is_admin as _app_is_admin,  # pyright: ignore[reportPrivateUsage]
    _relaunch_as_admin,  # pyright: ignore[reportPrivateUsage]
    _show_run_on_boot_failure,  # pyright: ignore[reportPrivateUsage]
    restart_fleasion_normally,
)
from .gui import (
    AboutWindow,
    DeleteCacheWindow,
    LogsWindow,
    ReplacerConfigWindow,
    ThemeManager,
)
from .localization import tr
from .utils import (
    APP_DISCORD,
    APP_NAME,
    APP_VERSION,
    CONFIG_DIR,
    LOGS_DIR,
    get_icon_path,
    get_roblox_player_exe_path,
    log_buffer,
    open_folder,
    run_in_thread,
)

if TYPE_CHECKING:
    from .app import RobloxExitMonitor
    from .config.manager import ConfigManager
    from .modifications.manager import ModificationManager
    from .proxy.addons.cache_scraper import CacheScraper
    from .proxy.env_lifecycle import EnvProxyLifecycleController
    from .proxy.master import ProxyMaster


class _HotkeyController(Protocol):
    def sync(self) -> None: ...

    def stop(self) -> None: ...


def _register_notification_app_id(app_id: str, icon_path: Path | None) -> bool:
    if sys.platform != 'win32':
        return False

    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Classes\AppUserModelId\{app_id}')
    winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_EXPAND_SZ, APP_NAME)
    winreg.SetValueEx(key, 'IconBackgroundColor', 0, winreg.REG_SZ, '00000000')
    if icon_path is not None:
        winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon_path))
    winreg.SetValueEx(key, 'ShowInSettings', 0, winreg.REG_DWORD, 1)
    try:
        key.Close()
    except OSError:
        log_buffer.log('Tray', 'Failed to close notification registry key')
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # pyright: ignore[reportAttributeAccessIssue]
        app_id
    )
    return True


APP_KOFI = 'ko-fi.com/fleasion'
_NOTIFICATION_APP_ID = f'{APP_NAME}.Notifications'
_TOAST_TEMPLATE = '<toast><visual><binding template="ToastGeneric"></binding></visual></toast>'


def _is_xfce_desktop() -> bool:
    """Return whether the current desktop environment is XFCE."""
    desktop_values = (
        os.environ.get('XDG_CURRENT_DESKTOP', ''),
        os.environ.get('XDG_SESSION_DESKTOP', ''),
        os.environ.get('DESKTOP_SESSION', ''),
    )
    return any('xfce' in value.casefold() for value in desktop_values)


class _XfceTrayNotification(QWidget):
    """A readable tray notification for XFCE's inconsistent native palette."""

    closed = Signal(object)

    def __init__(self, title: str, message: str, icon: QIcon, dark: bool, timeout: int) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # A translucent top-level surface can lose its stylesheet background under XFCE/X11.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName('FleasionXfceTrayNotification')
        self.setWindowTitle(title)
        self.setWindowIcon(icon)

        if dark:
            background = '#2b2b2b'
            foreground = '#f4f4f4'
            secondary = '#d8d8d8'
            border = '#626262'
        else:
            background = '#fffdf2'
            foreground = '#202020'
            secondary = '#353535'
            border = '#b7b7b7'

        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor(background))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(foreground))
        self.setPalette(palette)
        self.setAutoFillBackground(True)

        self.setStyleSheet(
            f"""
            QWidget#FleasionXfceTrayNotification {{
                background-color: {background};
                color: {foreground};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QLabel#FleasionXfceTrayNotificationTitle {{
                color: {foreground};
                background: transparent;
                border: none;
                font-weight: 700;
            }}
            QLabel#FleasionXfceTrayNotificationMessage {{
                color: {secondary};
                background: transparent;
                border: none;
            }}
            QPushButton#FleasionXfceTrayNotificationClose {{
                color: {secondary};
                background: transparent;
                border: none;
                font-size: 16px;
                padding: 0;
            }}
            QPushButton#FleasionXfceTrayNotificationClose:hover {{
                color: {foreground};
                background: {border};
                border-radius: 4px;
            }}
            """
        )

        icon_label = QLabel()
        icon_label.setFixedSize(32, 32)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if not icon.isNull():
            icon_label.setPixmap(icon.pixmap(32, 32))

        title_label = QLabel(title)
        title_label.setObjectName('FleasionXfceTrayNotificationTitle')

        message_label = QLabel(message)
        message_label.setObjectName('FleasionXfceTrayNotificationMessage')
        message_label.setWordWrap(True)
        message_label.setMinimumWidth(320)
        message_label.setMaximumWidth(420)

        close_button = QPushButton(tr('ui.tray.text'))
        close_button.setObjectName('FleasionXfceTrayNotificationClose')
        close_button.setFixedSize(24, 24)
        close_button.setToolTip(tr('ui.tray.close_notification'))
        close_button.clicked.connect(self.close)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(title_label)
        text_layout.addWidget(message_label)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 8, 10)
        layout.setSpacing(8)
        layout.addWidget(icon_label)
        layout.addLayout(text_layout, 1)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignTop)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.close)
        self._timer.start(timeout)

    def show_near_tray(self, tray_geometry: QRect) -> None:
        """Show the notification beside the tray icon without taking focus."""
        self.adjustSize()
        screen = None
        if not tray_geometry.isNull():
            screen = QApplication.screenAt(tray_geometry.center())
        if screen is None:
            screen = QApplication.primaryScreen()

        if screen is not None:  # pyright: ignore[reportUnnecessaryComparison]
            available = screen.availableGeometry()
            width = self.width()
            height = self.height()
            if tray_geometry.isNull():
                x = available.right() - width - 12
                y = available.bottom() - height - 12
            else:
                x = tray_geometry.center().x() - width // 2
                x = max(available.left() + 8, min(x, available.right() - width - 8))
                if tray_geometry.center().y() >= available.center().y():
                    y = max(available.top() + 8, tray_geometry.top() - height - 8)
                else:
                    y = min(available.bottom() - height - 8, tray_geometry.bottom() + 8)
            self.move(x, y)

        self.show()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.closed.emit(self)
        super().closeEvent(event)


def _is_admin() -> bool:
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    try:
        return bool(
            ctypes.windll.shell32.IsUserAnAdmin()  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
        )
    except AttributeError, OSError:
        return False


class SystemTray:
    """System tray icon with menu."""

    def __init__(
        self,
        app: QApplication,
        config_manager: ConfigManager,
        proxy_master: ProxyMaster,
        mod_manager: ModificationManager | None = None,
        roblox_monitor: RobloxExitMonitor | None = None,
    ) -> None:
        self.app = app
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self.mod_manager = mod_manager
        self.roblox_monitor = roblox_monitor

        self.custom_fflag_hotkeys: _HotkeyController | None = None
        hotkey_controller: _HotkeyController | None = None
        if sys.platform == 'win32':
            hotkey_module = importlib.import_module('.gui.windows_hotkeys', __package__)
            hotkey_controller = hotkey_module.WindowsCustomFFlagHotkeyController(
                config_manager, proxy_master, app
            )
        elif sys.platform.startswith('linux'):
            hotkey_module = importlib.import_module('.gui.linux_hotkeys', __package__)
            hotkey_controller = hotkey_module.LinuxCustomFFlagHotkeyController(
                config_manager, proxy_master, app
            )

        self.custom_fflag_hotkeys = hotkey_controller
        if hotkey_controller is not None:
            hotkey_controller.sync()

        # Keep references to open windows to prevent garbage collection
        self.open_windows: list[QWidget] = []
        self.dashboard_window: ReplacerConfigWindow | None = None
        self._exiting = False
        self._dashboard_close_notice_shown = False
        self._notification_app_id: str | None = None
        self._xfce_notification: _XfceTrayNotification | None = None
        self._tray_cleaned_up = False

        # Create tray icon
        self.tray = QSystemTrayIcon()
        self._set_icon()
        self._update_tooltip()

        # Create menu
        self.menu = QMenu()
        self._create_menu()
        self.menu.aboutToShow.connect(self._ensure_exit_action_enabled)
        self.tray.setContextMenu(self.menu)

        # Apply initial theme
        ThemeManager.apply_theme(self.config_manager.theme)

        # Connect tray activation signal
        self.tray.activated.connect(self._on_tray_activated)

        # Show tray icon
        self.tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log_buffer.log('Tray', 'No system tray/menu-bar host is available')
        elif not self.tray.isVisible():
            log_buffer.log('Tray', 'System tray/menu-bar icon did not become visible')
        else:
            log_buffer.log(
                'Tray',
                'System tray/menu-bar host is available and icon visibility was requested',
            )

    def _set_icon(self) -> None:
        """Set the tray icon."""
        if icon_path := get_icon_path():
            self.tray.setIcon(QIcon(str(icon_path)))
        else:
            # Use a default icon if none is available
            self.tray.setIcon(
                self.app.style().standardIcon(self.app.style().StandardPixmap.SP_ComputerIcon)
            )

    def _update_tooltip(self) -> None:
        """Update the tooltip text based on proxy status."""
        status = (
            tr('tray.status.running') if self.proxy_master.is_running else tr('tray.status.stopped')
        )
        self.tray.setToolTip(tr('ui.tray.value_value', value0=APP_NAME, value1=status))

    def _create_menu(self) -> None:
        """Create the tray menu."""
        # Title (disabled)
        title_action = QAction(
            tr('ui.tray.value_v_value', value0=APP_NAME, value1=APP_VERSION), self.menu
        )
        title_action.setEnabled(False)
        self.menu.addAction(title_action)

        self.menu.addSeparator()

        # Main action - Dashboard
        self.dashboard_action = QAction(tr('ui.tray.dashboard'), self.menu)
        self.dashboard_action.triggered.connect(self._toggle_dashboard)
        self.menu.addAction(self.dashboard_action)

        # Configs submenu
        self.configs_menu = QMenu(tr('ui.tray.configs'), self.menu)
        self.configs_menu.aboutToShow.connect(self._populate_configs_menu)
        self.menu.addMenu(self.configs_menu)

        self.menu.addSeparator()

        # Windows
        cache_action = QAction(tr('ui.tray.clear_cache'), self.menu)
        cache_action.triggered.connect(self._show_delete_cache)
        self.menu.addAction(cache_action)

        logs_action = QAction(tr('ui.tray.logs'), self.menu)
        logs_action.triggered.connect(self._show_logs)
        self.menu.addAction(logs_action)

        open_logs_action = QAction(tr('ui.tray.open_log_folder'), self.menu)
        open_logs_action.triggered.connect(lambda: open_folder(LOGS_DIR))
        self.menu.addAction(open_logs_action)

        about_action = QAction(tr('ui.tray.about'), self.menu)
        about_action.triggered.connect(self._show_about)
        self.menu.addAction(about_action)

        self.menu.addSeparator()

        # Discord copy
        discord_action = QAction(tr('ui.tray.discord'), self.menu)
        discord_action.triggered.connect(self._copy_discord)
        self.menu.addAction(discord_action)

        # Donate
        donate_action = QAction(tr('ui.tray.donate'), self.menu)
        donate_action.triggered.connect(self._open_kofi)
        self.menu.addAction(donate_action)

        self.menu.addSeparator()

        # Settings submenu
        self._create_settings_menu()

        self.menu.addSeparator()

        # Exit
        self.exit_action = QAction(tr('ui.tray.exit'), self.menu)
        self.exit_action.setEnabled(True)
        self.exit_action.triggered.connect(self.exit_app)
        self.menu.addAction(self.exit_action)

    def _ensure_exit_action_enabled(self) -> None:
        """Keep quit available even when other tray actions are temporarily disabled."""
        exit_action = getattr(self, 'exit_action', None)
        if exit_action is not None:
            exit_action.setEnabled(True)

    def _populate_configs_menu(self) -> None:
        """Populate the Configs submenu with current configs."""
        self.configs_menu.clear()
        for name in self.config_manager.config_names:
            action = QAction(name, self.configs_menu)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_config_enabled(name))

            def toggle_config(_checked: bool = False, config_name: str = name) -> None:
                self._toggle_config(config_name)

            action.triggered.connect(toggle_config)
            self.configs_menu.addAction(action)

    def _toggle_config(self, name: str) -> None:
        """Toggle a config's enabled state."""
        self.config_manager.toggle_config_enabled(name)

    def _create_settings_menu(self) -> None:
        """Create the Settings submenu."""
        settings_menu = QMenu(tr('ui.tray.settings'), self.menu)

        self.cache_scraper_action = QAction(tr('ui.tray.enable_cache_scraper'), settings_menu)
        self.cache_scraper_action.setCheckable(True)
        self.cache_scraper_action.setChecked(self._is_cache_scraper_enabled())
        self.cache_scraper_action.triggered.connect(self._toggle_cache_scraper)
        settings_menu.addAction(self.cache_scraper_action)
        settings_menu.addSeparator()

        # Theme submenu
        theme_menu = QMenu(tr('ui.tray.theme'), settings_menu)

        # Theme actions (radio buttons)
        self.theme_actions: dict[str, QAction] = {}
        for theme_name, label in [
            ('System', tr('tray.theme.system')),
            ('Light', tr('tray.theme.light')),
            ('Dark', tr('tray.theme.dark')),
        ]:
            action = QAction(label, theme_menu)
            action.setCheckable(True)

            def set_theme(_checked: bool = False, theme: str = theme_name) -> None:
                self._set_theme(theme)

            action.triggered.connect(set_theme)
            theme_menu.addAction(action)
            self.theme_actions[theme_name] = action

        # Set current theme as checked
        current_theme = self.config_manager.theme
        if current_theme in self.theme_actions:
            self.theme_actions[current_theme].setChecked(True)

        settings_menu.addMenu(theme_menu)

        # Export naming submenu
        export_menu = QMenu(tr('ui.tray.export_naming'), settings_menu)

        # Export naming actions (checkboxes)
        self.export_naming_actions: dict[str, QAction] = {}
        for option, label in [
            ('name', tr('tray.export_naming.name')),
            ('id', tr('tray.export_naming.id')),
            ('hash', tr('tray.export_naming.hash')),
        ]:
            action = QAction(label, export_menu)
            action.setCheckable(True)
            action.setChecked(self.config_manager.is_export_naming_enabled(option))

            def toggle_export(_checked: bool = False, export_option: str = option) -> None:
                self._toggle_export_naming(export_option)

            action.triggered.connect(toggle_export)
            export_menu.addAction(action)
            self.export_naming_actions[option] = action

        settings_menu.addMenu(export_menu)

        # Convenience submenu
        convenience_menu = QMenu(tr('ui.tray.convenience'), settings_menu)

        # Always on Top toggle
        self.always_on_top_action = QAction(tr('ui.tray.always_on_top'), convenience_menu)
        self.always_on_top_action.setCheckable(True)
        self.always_on_top_action.setChecked(self.config_manager.always_on_top)
        self.always_on_top_action.triggered.connect(self._toggle_always_on_top)
        convenience_menu.addAction(self.always_on_top_action)

        # Open dashboard on launch
        self.open_dashboard_action = QAction(
            tr('ui.tray.open_dashboard_on_start'), convenience_menu
        )
        self.open_dashboard_action.setCheckable(True)
        self.open_dashboard_action.setChecked(self.config_manager.open_dashboard_on_launch)
        self.open_dashboard_action.triggered.connect(self._toggle_open_dashboard_on_launch)
        convenience_menu.addAction(self.open_dashboard_action)

        # Auto delete cache on Roblox exit
        self.auto_delete_cache_action = QAction(
            tr('ui.tray.auto_clear_cache_on_exit'), convenience_menu
        )
        self.auto_delete_cache_action.setCheckable(True)
        self.auto_delete_cache_action.setChecked(self.config_manager.auto_delete_cache_on_exit)
        self.auto_delete_cache_action.triggered.connect(self._toggle_auto_delete_cache)
        convenience_menu.addAction(self.auto_delete_cache_action)

        # Clear cache on launch
        self.clear_cache_action = QAction(tr('ui.tray.clear_cache_on_launch'), convenience_menu)
        self.clear_cache_action.setCheckable(True)
        self.clear_cache_action.setChecked(self.config_manager.clear_cache_on_launch)
        self.clear_cache_action.triggered.connect(self._toggle_clear_cache_on_launch)
        convenience_menu.addAction(self.clear_cache_action)

        # Run on Boot
        self.run_on_boot_action = QAction(
            tr('ui.tray.run_on_boot'),
            convenience_menu,
        )
        self.run_on_boot_action.setCheckable(True)
        self.run_on_boot_action.setChecked(self.config_manager.run_on_boot)
        self.run_on_boot_action.triggered.connect(self._toggle_run_on_boot)
        convenience_menu.addAction(self.run_on_boot_action)

        self.desktop_integration_action = QAction(
            tr('ui.tray.create_desktop_start_menu_integration_on_boot'), convenience_menu
        )
        self.desktop_integration_action.setCheckable(True)
        self.desktop_integration_action.setChecked(self.config_manager.desktop_integration)
        self.desktop_integration_action.triggered.connect(self._toggle_desktop_integration)
        convenience_menu.addAction(self.desktop_integration_action)

        # Close Roblox on Open
        self.close_scraped_games_action = QAction(
            tr('ui.tray.close_roblox_on_open'), convenience_menu
        )
        self.close_scraped_games_action.setCheckable(True)
        self.close_scraped_games_action.setChecked(self.config_manager.close_scraped_games_on_open)
        self.close_scraped_games_action.triggered.connect(self._toggle_close_scraped_games)
        convenience_menu.addAction(self.close_scraped_games_action)

        # Close to Tray
        self.close_to_tray_action = QAction(tr('ui.tray.close_to_tray'), convenience_menu)
        self.close_to_tray_action.setCheckable(True)
        self.close_to_tray_action.setChecked(self.config_manager.close_to_tray)
        self.close_to_tray_action.triggered.connect(self._toggle_close_to_tray)
        convenience_menu.addAction(self.close_to_tray_action)

        settings_menu.addMenu(convenience_menu)

        # Scraper submenu
        scraper_menu = QMenu(tr('ui.tray.scraper'), settings_menu)

        # Show Names
        self.show_names_action = QAction(tr('ui.tray.show_names'), scraper_menu)
        self.show_names_action.setCheckable(True)
        self.show_names_action.setChecked(self.config_manager.show_names)
        self.show_names_action.triggered.connect(self._toggle_show_names)
        scraper_menu.addAction(self.show_names_action)

        # Show User ID
        self.show_creator_id_action = QAction(tr('ui.tray.show_user_id'), scraper_menu)
        self.show_creator_id_action.setCheckable(True)
        self.show_creator_id_action.setChecked(self.config_manager.show_creator_id)
        self.show_creator_id_action.triggered.connect(self._toggle_show_creator_id)
        scraper_menu.addAction(self.show_creator_id_action)

        settings_menu.addMenu(scraper_menu)

        scraped_games_menu = QMenu(tr('ui.tray.scraped_games'), settings_menu)
        self.show_replacer_notifications_action = QAction(
            tr('ui.tray.show_replacer_notifications'), scraped_games_menu
        )
        self.show_replacer_notifications_action.setCheckable(True)
        self.show_replacer_notifications_action.setChecked(
            self.config_manager.show_replacer_notifications
        )
        self.show_replacer_notifications_action.triggered.connect(
            self._toggle_show_replacer_notifications
        )
        scraped_games_menu.addAction(self.show_replacer_notifications_action)
        self.close_viewer_on_replace_action = QAction(
            tr('ui.tray.close_viewer_on_replace'), scraped_games_menu
        )
        self.close_viewer_on_replace_action.setCheckable(True)
        self.close_viewer_on_replace_action.setChecked(self.config_manager.close_viewer_on_replace)
        self.close_viewer_on_replace_action.triggered.connect(self._toggle_close_viewer_on_replace)
        scraped_games_menu.addAction(self.close_viewer_on_replace_action)
        self.close_scraped_games_menu_on_open_action = QAction(
            tr('ui.tray.close_scraped_games_menu_on_open'), scraped_games_menu
        )
        self.close_scraped_games_menu_on_open_action.setCheckable(True)
        self.close_scraped_games_menu_on_open_action.setChecked(
            self.config_manager.close_scraped_games_menu_on_open
        )
        self.close_scraped_games_menu_on_open_action.triggered.connect(
            self._toggle_close_scraped_games_menu_on_open
        )
        scraped_games_menu.addAction(self.close_scraped_games_menu_on_open_action)
        settings_menu.addMenu(scraped_games_menu)

        self.menu.addMenu(settings_menu)

    def refresh_settings_tab(self) -> None:
        """Push current config state to the Settings tab if the dashboard is open."""
        if self.dashboard_window:
            self.dashboard_window.refresh_settings_tab()

    def _cache_scraper(self) -> CacheScraper:
        return self.proxy_master.cache_scraper

    def _is_cache_scraper_enabled(self) -> bool:
        return self._cache_scraper().enabled

    def _set_cache_scraper_enabled(self, enabled: bool) -> None:
        self._cache_scraper().set_enabled(enabled)

        blocker = QSignalBlocker(self.cache_scraper_action)
        self.cache_scraper_action.setChecked(enabled)
        del blocker

        if self.dashboard_window:
            self.dashboard_window.set_cache_scraper_enabled(enabled=enabled)

    def _toggle_cache_scraper(self, checked: bool) -> None:
        self._set_cache_scraper_enabled(checked)

    def set_proxy_features_enabled(self, enabled: bool) -> None:
        """Persist and apply the top-level proxy feature toggle."""
        self.config_manager.proxy_features_enabled = enabled

        if enabled:
            if self.config_manager.proxy_mode == 'env':
                # Env Proxy binds only a loopback high port. Any protected
                # macOS cacert.pem fallback is requested only if direct patching fails.
                self.proxy_master.start()
                lifecycle = (
                    self.roblox_monitor.env_lifecycle if self.roblox_monitor is not None else None
                )
                if (
                    lifecycle is not None
                    and self.roblox_monitor is not None
                    and self.roblox_monitor.is_player_running()
                ):
                    if sys.platform.startswith('linux'):
                        platform_linux = importlib.import_module(
                            '.utils.platform_linux', __package__
                        )
                        exe_path = Path(platform_linux.selected_linux_client_app_id())
                    else:
                        exe_path = get_roblox_player_exe_path()
                    run_in_thread(lifecycle.handle_player_launch)(exe_path)
            elif sys.platform == 'darwin':
                macos_proxy_helper = importlib.import_module(
                    '.utils.macos_proxy_helper', __package__
                )
                if macos_proxy_helper.helper_is_ready():
                    self.proxy_master.start()
                else:
                    ok, detail = macos_proxy_helper.install_helper()
                    if ok:
                        self.proxy_master.start()
                    else:
                        self.config_manager.proxy_features_enabled = False
                        log_buffer.log(
                            'ProxyHelper',
                            f'macOS proxy helper installation failed: {detail}',
                        )
                        enabled = False
            elif sys.platform.startswith('linux') or _is_admin():
                self.proxy_master.start()
            else:
                log_buffer.log('Proxy', 'Proxy features enabled: requesting administrator relaunch')
                if _relaunch_as_admin():
                    self._exiting = True
                    self.app.quit()
                    return

                self.config_manager.proxy_features_enabled = False
                log_buffer.log(
                    'Proxy',
                    'Administrator relaunch was cancelled; proxy features remain disabled',
                )
                enabled = False
        else:
            try:
                run_in_thread(self.proxy_master.stop)()
            except RuntimeError:
                self.proxy_master.stop()

        self.update_status()
        if self.dashboard_window:
            self.dashboard_window.set_proxy_features_enabled(enabled)
        self.refresh_settings_tab()

    def notify_proxy_mode_changed(self) -> None:
        """Let the dashboard's Proxy tab know hosts/env mode was switched in Settings."""
        if self.dashboard_window:
            self.dashboard_window.refresh_env_proxy_gate()

    def _set_theme(self, theme: str) -> None:
        """Set the application theme."""
        # Update checkmarks
        for name, action in self.theme_actions.items():
            action.setChecked(name == theme)

        # Apply theme
        ThemeManager.apply_theme(theme)

        # Save to config
        self.config_manager.theme = theme
        self.refresh_settings_tab()

    def _toggle_export_naming(self, option: str) -> None:
        """Toggle an export naming option."""
        new_state = self.config_manager.toggle_export_naming(option)
        self.export_naming_actions[option].setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_always_on_top(self) -> None:
        """Toggle always on top setting."""
        new_state = not self.config_manager.always_on_top
        self.config_manager.always_on_top = new_state
        self.always_on_top_action.setChecked(new_state)

        # Apply to all open windows (only if they're visible)

        for window in self.open_windows:
            if window.isVisible():
                flags = window.windowFlags()
                if new_state:
                    flags |= Qt.WindowType.WindowStaysOnTopHint
                else:
                    flags &= ~Qt.WindowType.WindowStaysOnTopHint
                window.setWindowFlags(flags)
                window.show()
        self.refresh_settings_tab()

    def _toggle_open_dashboard_on_launch(self) -> None:
        """Toggle open dashboard on launch setting."""
        new_state = not self.config_manager.open_dashboard_on_launch
        self.config_manager.open_dashboard_on_launch = new_state
        self.open_dashboard_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_auto_delete_cache(self) -> None:
        """Toggle auto delete cache on Roblox exit setting."""
        new_state = not self.config_manager.auto_delete_cache_on_exit
        self.config_manager.auto_delete_cache_on_exit = new_state
        self.auto_delete_cache_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_run_on_boot(self) -> None:
        """Toggle run-on-boot for the current platform."""
        autostart = importlib.import_module('.utils.autostart', __package__)
        checked = self.run_on_boot_action.isChecked()
        ok = autostart.sync_autostart(
            checked,
            CONFIG_DIR,
            proxy_mode=self.config_manager.proxy_mode,
        )
        if ok:
            self.config_manager.run_on_boot = checked
            self.refresh_settings_tab()
        else:
            if sys.platform == 'win32':
                if _show_run_on_boot_failure(
                    None,
                    self.config_manager.proxy_mode,
                    enabled=checked,
                ):
                    self.config_manager.run_on_boot = checked
                    self.refresh_settings_tab()
                    return
            # Revert UI state and show error dialog with detail
            self.run_on_boot_action.setChecked(not checked)

            top = QApplication.topLevelWidgets()
            parent = next((w for w in top if w.isVisible()), None)
            on_top = any(
                w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                for w in top
            )
            warn = QMessageBox(parent)
            warn.setWindowTitle(tr('ui.tray.run_on_boot_failed'))
            warn.setIcon(QMessageBox.Icon.Warning)
            message = tr('tray.autostart.registration_failed')
            if sys.platform == 'win32':
                message += '\n\n' + autostart.windows_autostart_privilege_hint(
                    self.config_manager.proxy_mode
                )
            warn.setText(message)
            if on_top:
                warn.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            warn.exec()

    def _toggle_desktop_integration(self) -> None:
        """Toggle desktop/start-menu integration for the current platform."""
        desktop_integration = importlib.import_module('.utils.desktop_integration', __package__)
        checked = self.desktop_integration_action.isChecked()
        ok = desktop_integration.sync_desktop_integration(checked)
        if ok:
            self.config_manager.desktop_integration = checked
            if sys.platform.startswith('linux') and self.config_manager.run_on_boot:
                autostart = importlib.import_module('.utils.autostart', __package__)
                if not autostart.sync_autostart(
                    enabled=True,
                    config_dir=CONFIG_DIR,
                    proxy_mode=self.config_manager.proxy_mode,
                ):
                    top = QApplication.topLevelWidgets()
                    parent = next((w for w in top if w.isVisible()), None)
                    warn = QMessageBox(parent)
                    warn.setWindowTitle(tr('ui.tray.run_on_boot_failed'))
                    warn.setIcon(QMessageBox.Icon.Warning)
                    warn.setText(tr('ui.tray.failed_to_refresh_autostart_after_changing_desktop'))
                    warn.exec()
            self.refresh_settings_tab()
        else:
            self.desktop_integration_action.setChecked(not checked)

            top = QApplication.topLevelWidgets()
            parent = next((w for w in top if w.isVisible()), None)
            on_top = any(
                w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
                for w in top
            )
            warn = QMessageBox(parent)
            warn.setWindowTitle(tr('ui.tray.desktop_integration_failed'))
            warn.setIcon(QMessageBox.Icon.Warning)
            warn.setText(tr('ui.tray.failed_to_create_desktop_start_menu_integration'))
            if on_top:
                warn.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            warn.exec()

    def _toggle_clear_cache_on_launch(self) -> None:
        """Toggle clear cache on launch setting."""
        new_state = not self.config_manager.clear_cache_on_launch
        self.config_manager.clear_cache_on_launch = new_state
        self.clear_cache_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_close_scraped_games(self) -> None:
        """Toggle close Roblox on open setting."""
        new_state = not self.config_manager.close_scraped_games_on_open
        self.config_manager.close_scraped_games_on_open = new_state
        self.close_scraped_games_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_close_viewer_on_replace(self) -> None:
        """Toggle close viewer on replace setting."""
        new_state = not self.config_manager.close_viewer_on_replace
        self.config_manager.close_viewer_on_replace = new_state
        self.close_viewer_on_replace_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_close_scraped_games_menu_on_open(self) -> None:
        """Toggle close Scraped Games menu on JSON open setting."""
        new_state = not self.config_manager.close_scraped_games_menu_on_open
        self.config_manager.close_scraped_games_menu_on_open = new_state
        self.close_scraped_games_menu_on_open_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_show_replacer_notifications(self) -> None:
        """Toggle replacer notification popups."""
        new_state = not self.config_manager.show_replacer_notifications
        self.config_manager.show_replacer_notifications = new_state
        self.show_replacer_notifications_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_close_to_tray(self) -> None:
        """Toggle close to tray setting."""
        new_state = not self.config_manager.close_to_tray
        self.config_manager.close_to_tray = new_state
        self.close_to_tray_action.setChecked(new_state)
        self.refresh_settings_tab()

    def _toggle_show_names(self) -> None:
        """Toggle Show Names setting."""
        new_state = not self.config_manager.show_names
        self.config_manager.show_names = new_state
        self.show_names_action.setChecked(new_state)
        if self.dashboard_window:
            self.dashboard_window.apply_cache_viewer_display_setting(
                'show_names',
                enabled=new_state,
            )
        self.refresh_settings_tab()

    def _toggle_show_creator_id(self) -> None:
        """Toggle Show User ID setting."""
        new_state = not self.config_manager.show_creator_id
        self.config_manager.show_creator_id = new_state
        self.show_creator_id_action.setChecked(new_state)
        if self.dashboard_window:
            self.dashboard_window.apply_cache_viewer_display_setting(
                'show_creator_id',
                enabled=new_state,
            )
        self.refresh_settings_tab()

    def _apply_always_on_top_to_window(self, window: QWidget) -> None:
        """Apply always on top setting to a window."""
        if self.config_manager.always_on_top:
            flags = window.windowFlags()
            flags |= Qt.WindowType.WindowStaysOnTopHint
            window.setWindowFlags(flags)

    def _show_about(self) -> None:
        """Show About window."""
        window = AboutWindow()

        def remove_about(_obj: object | None = None) -> None:
            self._remove_window(window)

        window.destroyed.connect(remove_about)
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _show_logs(self) -> None:
        """Show Logs window — only one instance allowed."""
        for w in self.open_windows:
            if isinstance(w, LogsWindow):
                w.showNormal()
                w.show()
                w.raise_()
                w.activateWindow()
                return
        window = LogsWindow()

        def remove_logs(_obj: object | None = None) -> None:
            self._remove_window(window)

        window.destroyed.connect(remove_logs)
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()
        window.raise_()
        window.activateWindow()

    def show_replacer_config(self) -> None:
        """Show Replacer Config window (Dashboard)."""
        self._set_dashboard_foreground_mode(enabled=True)
        if self.dashboard_window:
            self.dashboard_window.show()
            self.dashboard_window.raise_()
            self.dashboard_window.activateWindow()
            return

        window = ReplacerConfigWindow(
            self.config_manager,  # pyright: ignore[reportArgumentType]
            self.proxy_master,
            self.mod_manager,
            self.roblox_monitor,
            system_tray=self,  # pyright: ignore[reportArgumentType]
            hotkey_controller=self.custom_fflag_hotkeys,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._on_dashboard_destroyed)
        self.dashboard_window = window
        self.open_windows.append(window)
        # Note: ReplacerConfigWindow applies always_on_top in its __init__
        window.show()

    def _on_dashboard_destroyed(self) -> None:
        """Handle dashboard destruction."""
        self._set_dashboard_foreground_mode(enabled=False)
        if self.dashboard_window in self.open_windows:
            self.open_windows.remove(self.dashboard_window)
        self.dashboard_window = None
        if not self._exiting and not self.config_manager.close_to_tray:
            self.exit_app()

    def _toggle_dashboard(self) -> None:
        """Toggle dashboard visibility."""
        if self.dashboard_window and self.dashboard_window.isVisible():
            self.dashboard_window.hide()
            self._set_dashboard_foreground_mode(enabled=False)
        else:
            self.show_replacer_config()

    def _set_dashboard_foreground_mode(self, enabled: bool) -> None:
        """Keep the macOS dashboard visible when Fleasion loses focus."""
        if sys.platform != 'darwin':
            return
        platform_macos = importlib.import_module('.utils.platform_macos', __package__)
        if not platform_macos.set_application_foreground_mode(enabled):
            log_buffer.log('App', 'macOS dashboard activation-policy update was rejected')
        elif enabled and (icon_path := get_icon_path()):
            platform_macos.set_application_icon(icon_path)

    def notify_dashboard_closed(self) -> None:
        """Show the tray notice that the app is still running."""
        if self._dashboard_close_notice_shown:
            return

        self._dashboard_close_notice_shown = True
        title = APP_NAME
        message = tr('tray.dashboard_closed_notice')
        icon_path = get_icon_path()

        if sys.platform.startswith('linux') and _is_xfce_desktop():
            if self._show_xfce_notification(title, message, icon_path):
                return

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

    def _show_xfce_notification(self, title: str, message: str, icon_path: Path | None) -> bool:
        """Show an app-owned notification so XFCE cannot apply unreadable colors."""
        try:
            self._create_xfce_notification(title, message, icon_path)
        except RuntimeError as exc:
            self._xfce_notification = None
            log_buffer.log('Tray', f'Failed to show XFCE notification: {exc}')
            return False
        else:
            return True

    def _create_xfce_notification(self, title: str, message: str, icon_path: Path | None) -> None:
        if self._xfce_notification is not None:
            self._xfce_notification.close()

        icon = QIcon(str(icon_path)) if icon_path is not None else QIcon()
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        notification = _XfceTrayNotification(title, message, icon, dark, 10000)
        notification.closed.connect(self._on_xfce_notification_closed)
        self._xfce_notification = notification
        notification.show_near_tray(self.tray.geometry())

    def _on_xfce_notification_closed(self, notification: object) -> None:
        if self._xfce_notification is notification:
            self._xfce_notification = None

    def _show_windows_notification(self, title: str, message: str, icon_path: Path | None) -> bool:
        """Show a silent Windows toast with the app icon and app identity."""
        app_id = self._ensure_notification_app_id()
        if not app_id:
            return False

        sent = False
        with contextlib.suppress(Exception):
            self._send_windows_notification(title, message, icon_path, app_id)
            sent = True
        return sent

    @staticmethod
    def _send_windows_notification(
        title: str,
        message: str,
        icon_path: Path | None,
        app_id: str,
    ) -> None:
        notify = importlib.import_module('win11toast').notify
        notify(
            title=title,
            body=message,
            icon=str(icon_path) if icon_path is not None else None,
            audio={'silent': 'true'},
            duration='short',
            app_id=app_id,
            xml=_TOAST_TEMPLATE,
        )

    def _ensure_notification_app_id(self) -> str | None:
        """Register and cache the AUMID used for Fleasion notifications."""
        if self._notification_app_id:
            return self._notification_app_id

        if os.name != 'nt':
            return None
        app_id = _NOTIFICATION_APP_ID
        icon_path = get_icon_path()

        try:
            if not _register_notification_app_id(app_id, icon_path):
                return None
        except OSError:
            return None

        self._notification_app_id = app_id
        return app_id

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation (e.g., click)."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:  # Trigger is usually left-click
            # On macOS, clicking the menu-bar icon is how the user opens its
            # menu. Treating that click as a dashboard toggle makes a visible
            # dashboard disappear before the user can select a menu command.
            if sys.platform == 'darwin':
                return
            self._toggle_dashboard()

    def _show_delete_cache(self) -> None:
        """Show Delete Cache window."""
        window = DeleteCacheWindow()

        def remove_delete_cache(_obj: object | None = None) -> None:
            self._remove_window(window)

        window.destroyed.connect(remove_delete_cache)
        self.open_windows.append(window)
        self._apply_always_on_top_to_window(window)
        window.show()

    def _remove_window(self, window: QWidget) -> None:
        """Remove window from tracking list."""
        if window in self.open_windows:
            self.open_windows.remove(window)

    def _open_discord_server(self) -> None:
        """Open the Discord server invite in the default browser."""
        discord_url = (
            APP_DISCORD
            if APP_DISCORD.startswith(('http://', 'https://'))
            else f'https://{APP_DISCORD}'
        )
        QDesktopServices.openUrl(QUrl(discord_url))

    def _copy_discord(self) -> None:
        """Copy Discord invite to clipboard."""

        QApplication.clipboard().setText(tr('ui.tray.https_value', value0=APP_DISCORD))

        top = QApplication.topLevelWidgets()
        parent = next((w for w in top if w.isVisible()), None)
        on_top = any(
            w.isVisible() and bool(w.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
            for w in top
        )
        msg_box = QMessageBox(parent)
        if on_top:
            msg_box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        msg_box.setWindowTitle(tr('ui.tray.discord_invite_copied'))
        msg_box.setText(tr('ui.tray.discord_invite_copied_2'))
        msg_box.setInformativeText(tr('ui.tray.https_value', value0=APP_DISCORD))
        msg_box.setIcon(QMessageBox.Icon.Information)
        if icon_path := get_icon_path():
            msg_box.setWindowIcon(QIcon(str(icon_path)))
        msg_box.exec()

    def _open_kofi(self) -> None:
        """Open Ko-fi page in browser."""
        QDesktopServices.openUrl(QUrl(f'https://{APP_KOFI}'))

    def restart_fleasion(self) -> bool | None:
        """Verified relaunch used when a setting change needs a new process.

        The current proxy remains alive until the final child owns the
        single-instance state and has established the configured proxy. This
        keeps import, elevation, and Hosts-mode startup failures transactional.
        """
        lifecycle = self.roblox_monitor.env_lifecycle if self.roblox_monitor is not None else None
        preserve_player = bool(
            self.config_manager.proxy_mode == 'env'
            and lifecycle is not None
            and lifecycle.owns_player
            and self.roblox_monitor is not None
            and self.roblox_monitor.is_player_running()
        )
        requires_admin = bool(
            sys.platform == 'win32'
            and self.config_manager.proxy_mode != 'env'
            and not _app_is_admin()
        )
        try:
            restarted = restart_fleasion_normally(
                preserve_env_proxy_player=preserve_player,
                verify_startup=True,
                require_admin=requires_admin,
            )
        except RestartHandoffUncertain as exc:
            log_buffer.log('Restart', f'Replacement termination is uncertain: {exc}')
            return None
        if not restarted:
            log_buffer.log('Restart', 'Could not verify replacement Fleasion startup')
            return False
        self.exit_app(
            preserve_roblox=preserve_player,
            force_close_roblox=not preserve_player,
        )
        return True

    def exit_app(
        self,
        *,
        preserve_roblox: bool = False,
        force_close_roblox: bool = False,
    ) -> None:
        """Exit the application."""
        if getattr(self, '_exiting', False):
            return
        self._exiting = True
        self.cleanup_tray_icon()

        monitor = getattr(self, 'roblox_monitor', None)
        lifecycle = monitor.env_lifecycle if monitor is not None else None
        try:
            self._cleanup_exit_lifecycle(
                lifecycle,
                preserve_roblox=preserve_roblox,
                force_close_roblox=force_close_roblox,
            )
        except (OSError, RuntimeError) as exc:
            log_buffer.log('Launcher', f'Env Proxy Player exit cleanup failed: {exc}')

        # Player must be closed (or explicitly preserved) before its loopback
        # proxy disappears.
        try:
            self.proxy_master.stop()
        except (OSError, RuntimeError) as exc:
            log_buffer.log('Launcher', f'Proxy shutdown failed: {exc}')

        # Quit Qt app
        self.app.quit()

    def _cleanup_exit_lifecycle(
        self,
        lifecycle: EnvProxyLifecycleController | None,
        *,
        preserve_roblox: bool,
        force_close_roblox: bool,
    ) -> None:
        if (hotkeys := getattr(self, 'custom_fflag_hotkeys', None)) is not None:
            hotkeys.stop()
        if lifecycle is None:
            return
        if preserve_roblox:
            lifecycle.preserve_owned_player_for_restart()
        elif force_close_roblox or getattr(
            getattr(self, 'config_manager', None),
            'close_env_proxy_roblox_on_exit',
            True,
        ):
            lifecycle.close_owned_player_for_exit()
        else:
            lifecycle.cancel()

    def cleanup_tray_icon(self) -> None:
        """Remove the tray icon before the Qt event loop exits."""
        if self._tray_cleaned_up:
            return

        self._tray_cleaned_up = True
        if notification := getattr(self, '_xfce_notification', None):
            notification.close()
            self._xfce_notification = None
        try:
            self.tray.hide()
            self.tray.setContextMenu(None)  # pyright: ignore[reportArgumentType]
            self.tray.deleteLater()
            QApplication.processEvents()
        except RuntimeError as exc:
            log_buffer.log('Tray', f'Tray cleanup failed: {exc}')

    def update_status(self) -> None:
        """Update the status (called periodically or on proxy state change)."""
        self._update_tooltip()
