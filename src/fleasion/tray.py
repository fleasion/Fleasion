"""System tray implementation."""

from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .localization import tr

if not TYPE_CHECKING:
    try:
        import winreg
    except ImportError:
        winreg = None

from PySide6.QtCore import QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QCloseEvent, QColor, QDesktopServices, QIcon, QPalette, QScreen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from .gui import (
    AboutWindow,
    DeleteCacheWindow,
    LogsWindow,
    ReplacerConfigWindow,
    ThemeManager,
)
from .utils import (
    APP_DISCORD,
    APP_NAME,
    APP_VERSION,
    LOGS_DIR,
    get_icon_path,
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


class _SettingsTabLike(Protocol):
    def refresh_from_config(self) -> None: ...

    def set_cache_scraper_enabled(self, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]


class _CacheViewerTabLike(Protocol):
    def set_cache_scraper_enabled(self, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]

    def _on_show_names_toggled(self, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]

    def _on_show_creator_id_toggled(self, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]


if TYPE_CHECKING:

    def _settings_tab(window: ReplacerConfigWindow) -> _SettingsTabLike | None: ...

    def _cache_viewer_tab(window: ReplacerConfigWindow) -> _CacheViewerTabLike | None: ...

    def _env_lifecycle(monitor: RobloxExitMonitor | None) -> EnvProxyLifecycleController | None: ...

    def _cache_scraper_from_proxy(proxy_master: ProxyMaster) -> CacheScraper | None: ...

    def _win_is_admin() -> bool: ...

    def _win_set_app_id(app_id: str) -> None: ...

    def _set_context_menu_none(tray: QSystemTrayIcon) -> None: ...

    def _optional_screen(screen: QScreen) -> QScreen | None: ...

    def _cache_viewer_show_names(tab: _CacheViewerTabLike, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]

    def _cache_viewer_show_creator_id(tab: _CacheViewerTabLike, enabled: bool) -> None: ...  # ruff: ignore[boolean-type-hint-positional-argument]

    def _register_notification_app_id(app_id: str, icon_path: Path | None) -> bool: ...

    def _make_dashboard(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        config_manager: ConfigManager,
        proxy_master: ProxyMaster,
        mod_manager: ModificationManager | None,
        roblox_monitor: RobloxExitMonitor | None,
        system_tray: SystemTray,
        hotkey_controller: _HotkeyController | None,
    ) -> ReplacerConfigWindow: ...
else:

    def _settings_tab(window: ReplacerConfigWindow) -> _SettingsTabLike | None:
        return getattr(window, '_settings_tab', None)

    def _cache_viewer_tab(window: ReplacerConfigWindow) -> _CacheViewerTabLike | None:
        return getattr(window, '_cache_viewer_tab', None)

    def _env_lifecycle(monitor: RobloxExitMonitor | None) -> EnvProxyLifecycleController | None:
        return getattr(monitor, 'env_lifecycle', None)

    def _cache_scraper_from_proxy(proxy_master: ProxyMaster) -> CacheScraper | None:
        return getattr(proxy_master, 'cache_scraper', None)

    def _win_is_admin() -> bool:
        return bool(ctypes.windll.shell32.IsUserAnAdmin()) if hasattr(ctypes, 'windll') else False

    def _win_set_app_id(app_id: str) -> None:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)

    def _set_context_menu_none(tray: QSystemTrayIcon) -> None:
        tray.setContextMenu(None)

    def _optional_screen(screen: QScreen) -> QScreen | None:
        return screen

    def _cache_viewer_show_names(tab: _CacheViewerTabLike, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
        tab._on_show_names_toggled(enabled)  # ruff: ignore[private-member-access]

    def _cache_viewer_show_creator_id(tab: _CacheViewerTabLike, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
        tab._on_show_creator_id_toggled(enabled)  # ruff: ignore[private-member-access]

    def _register_notification_app_id(app_id: str, icon_path: Path | None) -> bool:
        if winreg is None:
            return False
        key = winreg.CreateKey(
            winreg.HKEY_CURRENT_USER, rf'SOFTWARE\Classes\AppUserModelId\{app_id}'
        )
        winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_EXPAND_SZ, APP_NAME)
        winreg.SetValueEx(key, 'IconBackgroundColor', 0, winreg.REG_SZ, '00000000')
        if icon_path is not None:
            winreg.SetValueEx(key, 'IconUri', 0, winreg.REG_SZ, str(icon_path))
        winreg.SetValueEx(key, 'ShowInSettings', 0, winreg.REG_DWORD, 1)
        try:  # ruff: ignore[suppressible-exception]
            key.Close()
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass
        _win_set_app_id(app_id)
        return True

    def _make_dashboard(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        config_manager: ConfigManager,
        proxy_master: ProxyMaster,
        mod_manager: ModificationManager | None,
        roblox_monitor: RobloxExitMonitor | None,
        system_tray: SystemTray,
        hotkey_controller: _HotkeyController | None,
    ) -> ReplacerConfigWindow:
        return ReplacerConfigWindow(
            config_manager,
            proxy_master,
            mod_manager,
            roblox_monitor,
            system_tray=system_tray,
            hotkey_controller=hotkey_controller,
        )


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

    def __init__(self, title: str, message: str, icon: QIcon, dark: bool, timeout: int) -> None:  # ruff: ignore[boolean-type-hint-positional-argument, too-many-statements]
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
            screen = _optional_screen(QApplication.primaryScreen())

        if screen is not None:
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

    def closeEvent(self, event: QCloseEvent) -> None:  # ruff: ignore[invalid-function-name]
        self.closed.emit(self)
        super().closeEvent(event)


def _is_admin() -> bool:
    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        return hasattr(os, 'geteuid') and os.geteuid() == 0
    try:
        return _win_is_admin()
    except Exception:  # ruff: ignore[blind-except]
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
            from .gui.windows_hotkeys import (  # ruff: ignore[import-outside-top-level]
                WindowsCustomFFlagHotkeyController,
            )

            hotkey_controller = WindowsCustomFFlagHotkeyController(
                config_manager, proxy_master, app
            )
        elif sys.platform.startswith('linux'):
            from .gui.linux_hotkeys import (  # ruff: ignore[import-outside-top-level]
                LinuxCustomFFlagHotkeyController,
            )

            hotkey_controller = LinuxCustomFFlagHotkeyController(config_manager, proxy_master, app)

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
        # Title (disabled)  # ruff: ignore[commented-out-code]
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
        self.exit_action.triggered.connect(self._exit_app)
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

            def toggle_config(_checked: bool = False, config_name: str = name) -> None:  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
                self._toggle_config(config_name)

            action.triggered.connect(toggle_config)
            self.configs_menu.addAction(action)

    def _toggle_config(self, name: str) -> None:
        """Toggle a config's enabled state."""
        self.config_manager.toggle_config_enabled(name)

    def _create_settings_menu(self) -> None:  # ruff: ignore[too-many-statements]
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

            def set_theme(_checked: bool = False, theme: str = theme_name) -> None:  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
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

            def toggle_export(_checked: bool = False, export_option: str = option) -> None:  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
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

    def _refresh_settings_tab(self) -> None:
        """Push current config state to the Settings tab if the dashboard is open."""
        if self.dashboard_window:
            settings_tab = _settings_tab(self.dashboard_window)
            if settings_tab is not None:
                settings_tab.refresh_from_config()

    def _cache_scraper(self) -> CacheScraper | None:
        return _cache_scraper_from_proxy(self.proxy_master)

    def _is_cache_scraper_enabled(self) -> bool:
        scraper = self._cache_scraper()
        return bool(getattr(scraper, 'enabled', False))

    def _set_cache_scraper_enabled(self, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
        scraper = self._cache_scraper()
        if scraper is not None:
            scraper.set_enabled(enabled)

        if hasattr(self, 'cache_scraper_action'):
            self.cache_scraper_action.blockSignals(True)  # ruff: ignore[boolean-positional-value-in-call]
            self.cache_scraper_action.setChecked(enabled)
            self.cache_scraper_action.blockSignals(False)  # ruff: ignore[boolean-positional-value-in-call]

        if self.dashboard_window:
            tab = _cache_viewer_tab(self.dashboard_window)
            if tab is not None and hasattr(tab, 'set_cache_scraper_enabled'):
                tab.set_cache_scraper_enabled(enabled)

            settings_tab = _settings_tab(self.dashboard_window)
            if settings_tab is not None and hasattr(settings_tab, 'set_cache_scraper_enabled'):
                settings_tab.set_cache_scraper_enabled(enabled)

    def _toggle_cache_scraper(self, checked: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
        self._set_cache_scraper_enabled(checked)

    def set_proxy_features_enabled(self, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument, complex-structure, too-many-branches]
        """Persist and apply the top-level proxy feature toggle."""
        self.config_manager.proxy_features_enabled = enabled

        if enabled:
            if self.config_manager.proxy_mode == 'env':
                # Env Proxy binds only a loopback high port. Any protected
                # macOS cacert.pem fallback is requested only if direct patching fails.
                self.proxy_master.start()
                lifecycle = _env_lifecycle(self.roblox_monitor)
                if (
                    lifecycle is not None
                    and self.roblox_monitor is not None
                    and self.roblox_monitor.is_player_running()
                ):
                    if sys.platform.startswith('linux'):
                        from .utils.platform_linux import (  # ruff: ignore[import-outside-top-level]
                            selected_linux_client_app_id,
                        )

                        exe_path = Path(selected_linux_client_app_id())
                    else:
                        from .utils import (  # ruff: ignore[import-outside-top-level]
                            get_roblox_player_exe_path,
                        )

                        exe_path = get_roblox_player_exe_path()
                    run_in_thread(lifecycle.handle_player_launch)(exe_path)
            elif sys.platform == 'darwin':
                from .utils.macos_proxy_helper import (  # ruff: ignore[import-outside-top-level]
                    helper_is_ready,
                    install_helper,
                )

                if helper_is_ready():
                    self.proxy_master.start()
                else:
                    ok, detail = install_helper()
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
                if TYPE_CHECKING:

                    def relaunch_as_admin() -> bool: ...
                else:
                    from .app import (  # ruff: ignore[import-outside-top-level]
                        _relaunch_as_admin as relaunch_as_admin,
                    )

                log_buffer.log('Proxy', 'Proxy features enabled: requesting administrator relaunch')
                if relaunch_as_admin():
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
            except Exception:  # ruff: ignore[blind-except]
                self.proxy_master.stop()

        self.update_status()
        if self.dashboard_window and hasattr(self.dashboard_window, 'set_proxy_features_enabled'):
            self.dashboard_window.set_proxy_features_enabled(enabled)
        self._refresh_settings_tab()

    def notify_proxy_mode_changed(self) -> None:
        """Let the dashboard's Proxy tab know hosts/env mode was switched in Settings."""
        if self.dashboard_window and hasattr(self.dashboard_window, 'refresh_env_proxy_gate'):
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
        self._refresh_settings_tab()

    def _toggle_export_naming(self, option: str) -> None:
        """Toggle an export naming option."""
        new_state = self.config_manager.toggle_export_naming(option)
        self.export_naming_actions[option].setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_always_on_top(self) -> None:
        """Toggle always on top setting."""
        new_state = not self.config_manager.always_on_top
        self.config_manager.always_on_top = new_state
        self.always_on_top_action.setChecked(new_state)

        # Apply to all open windows (only if they're visible)
        from PySide6.QtCore import Qt  # ruff: ignore[import-outside-top-level]

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

    def _toggle_open_dashboard_on_launch(self) -> None:
        """Toggle open dashboard on launch setting."""
        new_state = not self.config_manager.open_dashboard_on_launch
        self.config_manager.open_dashboard_on_launch = new_state
        self.open_dashboard_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_auto_delete_cache(self) -> None:
        """Toggle auto delete cache on Roblox exit setting."""
        new_state = not self.config_manager.auto_delete_cache_on_exit
        self.config_manager.auto_delete_cache_on_exit = new_state
        self.auto_delete_cache_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_run_on_boot(self) -> None:
        """Toggle run-on-boot for the current platform."""
        from .utils import CONFIG_DIR  # ruff: ignore[import-outside-top-level]
        from .utils.autostart import (  # ruff: ignore[import-outside-top-level]
            sync_autostart,
            windows_autostart_privilege_hint,
        )

        checked = self.run_on_boot_action.isChecked()
        ok = sync_autostart(
            checked,
            CONFIG_DIR,
            proxy_mode=self.config_manager.proxy_mode,
        )
        if ok:
            self.config_manager.run_on_boot = checked
            self._refresh_settings_tab()
        else:
            if sys.platform == 'win32':
                # Imported on demand to avoid an app <-> tray import cycle during startup.
                if TYPE_CHECKING:

                    def show_run_on_boot_failure(
                        parent: QWidget | None,
                        proxy_mode: str | None = None,
                        *,
                        enabled: bool = True,
                    ) -> bool: ...
                else:
                    from .app import (  # ruff: ignore[import-outside-top-level]
                        _show_run_on_boot_failure as show_run_on_boot_failure,
                    )

                if show_run_on_boot_failure(None, self.config_manager.proxy_mode, enabled=checked):
                    self.config_manager.run_on_boot = checked
                    self._refresh_settings_tab()
                    return
            # Revert UI state and show error dialog with detail
            self.run_on_boot_action.setChecked(not checked)
            from PySide6.QtCore import Qt  # ruff: ignore[import-outside-top-level]
            from PySide6.QtWidgets import (  # ruff: ignore[import-outside-top-level]
                QApplication,
                QMessageBox,
            )

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
                message += '\n\n' + windows_autostart_privilege_hint(self.config_manager.proxy_mode)
            warn.setText(message)
            if on_top:
                warn.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            warn.exec()

    def _toggle_desktop_integration(self) -> None:
        """Toggle desktop/start-menu integration for the current platform."""
        from .utils.desktop_integration import (  # ruff: ignore[import-outside-top-level]
            sync_desktop_integration,
        )

        checked = self.desktop_integration_action.isChecked()
        ok = sync_desktop_integration(checked)
        if ok:
            self.config_manager.desktop_integration = checked
            if sys.platform.startswith('linux') and self.config_manager.run_on_boot:
                from .utils import CONFIG_DIR  # ruff: ignore[import-outside-top-level]
                from .utils.autostart import (  # ruff: ignore[import-outside-top-level]
                    sync_autostart,
                )

                if not sync_autostart(
                    True,  # ruff: ignore[boolean-positional-value-in-call]
                    CONFIG_DIR,
                    proxy_mode=self.config_manager.proxy_mode,
                ):
                    from PySide6.QtWidgets import (  # ruff: ignore[import-outside-top-level]
                        QApplication,
                        QMessageBox,
                    )

                    top = QApplication.topLevelWidgets()
                    parent = next((w for w in top if w.isVisible()), None)
                    warn = QMessageBox(parent)
                    warn.setWindowTitle(tr('ui.tray.run_on_boot_failed'))
                    warn.setIcon(QMessageBox.Icon.Warning)
                    warn.setText(tr('ui.tray.failed_to_refresh_autostart_after_changing_desktop'))
                    warn.exec()
            self._refresh_settings_tab()
        else:
            self.desktop_integration_action.setChecked(not checked)
            from PySide6.QtWidgets import (  # ruff: ignore[import-outside-top-level]
                QApplication,
                QMessageBox,
            )

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
        self._refresh_settings_tab()

    def _toggle_close_scraped_games(self) -> None:
        """Toggle close Roblox on open setting."""
        new_state = not self.config_manager.close_scraped_games_on_open
        self.config_manager.close_scraped_games_on_open = new_state
        self.close_scraped_games_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_close_viewer_on_replace(self) -> None:
        """Toggle close viewer on replace setting."""
        new_state = not self.config_manager.close_viewer_on_replace
        self.config_manager.close_viewer_on_replace = new_state
        self.close_viewer_on_replace_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_close_scraped_games_menu_on_open(self) -> None:
        """Toggle close Scraped Games menu on JSON open setting."""
        new_state = not self.config_manager.close_scraped_games_menu_on_open
        self.config_manager.close_scraped_games_menu_on_open = new_state
        self.close_scraped_games_menu_on_open_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_show_replacer_notifications(self) -> None:
        """Toggle replacer notification popups."""
        new_state = not self.config_manager.show_replacer_notifications
        self.config_manager.show_replacer_notifications = new_state
        self.show_replacer_notifications_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_close_to_tray(self) -> None:
        """Toggle close to tray setting."""
        new_state = not self.config_manager.close_to_tray
        self.config_manager.close_to_tray = new_state
        self.close_to_tray_action.setChecked(new_state)
        self._refresh_settings_tab()

    def _toggle_show_names(self) -> None:
        """Toggle Show Names setting."""
        new_state = not self.config_manager.show_names
        self.config_manager.show_names = new_state
        self.show_names_action.setChecked(new_state)
        if self.dashboard_window:
            tab = _cache_viewer_tab(self.dashboard_window)
            if tab is not None:
                _cache_viewer_show_names(tab, new_state)
        self._refresh_settings_tab()

    def _toggle_show_creator_id(self) -> None:
        """Toggle Show User ID setting."""
        new_state = not self.config_manager.show_creator_id
        self.config_manager.show_creator_id = new_state
        self.show_creator_id_action.setChecked(new_state)
        if self.dashboard_window:
            tab = _cache_viewer_tab(self.dashboard_window)
            if tab is not None:
                _cache_viewer_show_creator_id(tab, new_state)
        self._refresh_settings_tab()

    def _apply_always_on_top_to_window(self, window: QWidget) -> None:
        """Apply always on top setting to a window."""
        if self.config_manager.always_on_top:
            from PySide6.QtCore import Qt  # ruff: ignore[import-outside-top-level]

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

    def _show_replacer_config(self) -> None:
        """Show Replacer Config window (Dashboard)."""
        self._set_dashboard_foreground_mode(True)  # ruff: ignore[boolean-positional-value-in-call]
        if self.dashboard_window:
            self.dashboard_window.show()
            self.dashboard_window.raise_()
            self.dashboard_window.activateWindow()
            return

        from PySide6.QtCore import Qt  # ruff: ignore[import-outside-top-level]

        window = _make_dashboard(
            self.config_manager,
            self.proxy_master,
            self.mod_manager,
            self.roblox_monitor,
            self,
            self.custom_fflag_hotkeys,
        )
        window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        window.destroyed.connect(self._on_dashboard_destroyed)
        self.dashboard_window = window
        self.open_windows.append(window)
        # Note: ReplacerConfigWindow applies always_on_top in its __init__
        window.show()

    def _on_dashboard_destroyed(self) -> None:
        """Handle dashboard destruction."""
        self._set_dashboard_foreground_mode(False)  # ruff: ignore[boolean-positional-value-in-call]
        if self.dashboard_window in self.open_windows:
            self.open_windows.remove(self.dashboard_window)
        self.dashboard_window = None
        if not self._exiting and not self.config_manager.close_to_tray:
            self._exit_app()

    def _toggle_dashboard(self) -> None:
        """Toggle dashboard visibility."""
        if self.dashboard_window and self.dashboard_window.isVisible():
            self.dashboard_window.hide()
            self._set_dashboard_foreground_mode(False)  # ruff: ignore[boolean-positional-value-in-call]
        else:
            self._show_replacer_config()

    def _set_dashboard_foreground_mode(self, enabled: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument, no-self-use]
        """Keep the macOS dashboard visible when Fleasion loses focus."""
        if sys.platform != 'darwin':
            return
        from .utils.platform_macos import (  # ruff: ignore[import-outside-top-level]
            set_application_foreground_mode,
            set_application_icon,
        )

        if not set_application_foreground_mode(enabled):
            log_buffer.log('App', 'macOS dashboard activation-policy update was rejected')
        elif enabled and (icon_path := get_icon_path()):
            set_application_icon(icon_path)

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
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if self._xfce_notification is not None:
                self._xfce_notification.close()

            icon = QIcon(str(icon_path)) if icon_path is not None else QIcon()
            dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128  # ruff: ignore[magic-value-comparison]
            notification = _XfceTrayNotification(title, message, icon, dark, 10000)
            notification.closed.connect(self._on_xfce_notification_closed)
            self._xfce_notification = notification
            notification.show_near_tray(self.tray.geometry())
            return True  # ruff: ignore[try-consider-else]
        except Exception as exc:  # ruff: ignore[blind-except]
            self._xfce_notification = None
            log_buffer.log('Tray', f'Failed to show XFCE notification: {exc}')
            return False

    def _on_xfce_notification_closed(self, notification: object) -> None:
        if self._xfce_notification is notification:
            self._xfce_notification = None

    def _show_windows_notification(self, title: str, message: str, icon_path: Path | None) -> bool:
        """Show a silent Windows toast with the app icon and app identity."""
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            app_id = self._ensure_notification_app_id()
            if not app_id:
                return False

            if TYPE_CHECKING:

                def notify(**kwargs: object) -> object: ...
            else:
                from win11toast import notify  # ruff: ignore[import-outside-top-level]

            notify(
                title=title,
                body=message,
                icon=str(icon_path) if icon_path is not None else None,
                audio={'silent': 'true'},
                duration='short',
                app_id=app_id,
                xml=_TOAST_TEMPLATE,
            )
            return True  # ruff: ignore[try-consider-else]
        except Exception:  # ruff: ignore[blind-except]
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
            if not _register_notification_app_id(app_id, icon_path):
                return None
        except Exception:  # ruff: ignore[blind-except]
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

    def _open_discord_server(self) -> None:  # ruff: ignore[no-self-use]
        """Open the Discord server invite in the default browser."""
        discord_url = (
            APP_DISCORD
            if APP_DISCORD.startswith(('http://', 'https://'))
            else f'https://{APP_DISCORD}'
        )
        QDesktopServices.openUrl(QUrl(discord_url))

    def _copy_discord(self) -> None:  # ruff: ignore[no-self-use]
        """Copy Discord invite to clipboard."""
        from PySide6.QtCore import Qt  # ruff: ignore[import-outside-top-level]
        from PySide6.QtWidgets import (  # ruff: ignore[import-outside-top-level]
            QApplication,
            QMessageBox,
        )

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
            from PySide6.QtGui import QIcon  # ruff: ignore[import-outside-top-level]

            msg_box.setWindowIcon(QIcon(str(icon_path)))
        msg_box.exec()

    def _open_kofi(self) -> None:  # ruff: ignore[no-self-use]
        """Open Ko-fi page in browser."""
        import webbrowser  # ruff: ignore[import-outside-top-level]

        webbrowser.open(f'https://{APP_KOFI}')

    def restart_fleasion(self) -> bool | None:
        """Verified relaunch used when a setting change needs a new process.

        The current proxy remains alive until the final child owns the
        single-instance state and has established the configured proxy. This
        keeps import, elevation, and Hosts-mode startup failures transactional.
        """
        from .app import (  # ruff: ignore[import-outside-top-level]
            RestartHandoffUncertain,
            restart_fleasion_normally,
        )

        if TYPE_CHECKING:

            def app_is_admin() -> bool: ...
        else:
            from .app import _is_admin as app_is_admin  # ruff: ignore[import-outside-top-level]

        lifecycle = _env_lifecycle(self.roblox_monitor)
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
            and not app_is_admin()
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
        self._exit_app(
            preserve_roblox=preserve_player,
            force_close_roblox=not preserve_player,
        )
        return True

    def _exit_app(
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

        lifecycle = _env_lifecycle(getattr(self, 'roblox_monitor', None))
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            if self.custom_fflag_hotkeys is not None:
                self.custom_fflag_hotkeys.stop()
            if lifecycle is not None:
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
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('Launcher', f'Env Proxy Player exit cleanup failed: {exc}')

        # Player must be closed (or explicitly preserved) before its loopback
        # proxy disappears.
        try:  # ruff: ignore[suppressible-exception]
            self.proxy_master.stop()
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass

        # Quit Qt app
        self.app.quit()

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
            _set_context_menu_none(self.tray)
            self.tray.deleteLater()
            QApplication.processEvents()
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass

    def update_status(self) -> None:
        """Update the status (called periodically or on proxy state change)."""
        self._update_tooltip()
