"""Settings tab - mirrors all settings available in the system tray menu."""

from __future__ import annotations

import importlib
import sys
from functools import partial
from pathlib import Path
from threading import Thread
from typing import TYPE_CHECKING, Literal, Protocol, cast, override

from PySide6.QtCore import QEvent, QObject, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fleasion.app.dialogs.startup import show_run_on_boot_failure
from fleasion.app.roblox_launch import arm_windows_gdk_env_proxy_when_ready
from fleasion.gui.theme import ThemeManager
from fleasion.localization import available_languages, get_language, tr
from fleasion.utils import (
    CONFIG_DIR,
    get_roblox_player_exe_path,
    log_buffer,
    run_in_thread,
)
from fleasion.utils.autostart import sync_autostart, windows_autostart_privilege_hint
from fleasion.utils.desktop_integration import sync_desktop_integration
from fleasion.utils.macos_proxy_helper import helper_is_ready, install_helper
from fleasion.utils.roblox_auth import (
    notify_auth_source_changed,
    store_manual_roblosecurity,
    validate_roblosecurity_for_import,
)

from .modifications_tab import CollapsibleSection, DropdownComboBox, NoWheelSpinBox

if TYPE_CHECKING:
    from collections.abc import Callable, Generator

    from PySide6.QtGui import QAction

    from fleasion.app.roblox_monitor import RobloxExitMonitor
    from fleasion.config.manager import ConfigManager
    from fleasion.modifications.manager import ModificationManager
    from fleasion.proxy.master import ProxyMaster


if sys.platform.startswith('linux'):
    from fleasion.utils import platform_linux
else:
    platform_linux = None


class _DashboardWindowLike(Protocol):
    def apply_cache_viewer_display_setting(
        self,
        setting: Literal['show_names', 'show_creator_id'],
        *,
        enabled: bool,
    ) -> None: ...

    def set_cache_scraper_enabled(self, *, enabled: bool) -> None: ...


class _SystemTrayLike(Protocol):
    proxy_master: ProxyMaster
    mod_manager: ModificationManager | None
    roblox_monitor: RobloxExitMonitor | None
    dashboard_window: _DashboardWindowLike | None
    open_windows: list[QWidget]
    theme_actions: dict[str, QAction]
    export_naming_actions: dict[str, QAction]
    open_dashboard_action: QAction
    run_on_boot_action: QAction
    desktop_integration_action: QAction
    always_on_top_action: QAction
    close_to_tray_action: QAction
    auto_delete_cache_action: QAction
    clear_cache_action: QAction
    close_scraped_games_action: QAction
    close_viewer_on_replace_action: QAction
    close_scraped_games_menu_on_open_action: QAction
    show_replacer_notifications_action: QAction
    show_names_action: QAction
    show_creator_id_action: QAction
    cache_scraper_action: QAction

    set_proxy_features_enabled: Callable[[bool], None]

    def restart_fleasion(self) -> bool | None: ...

    def notify_proxy_mode_changed(self) -> None: ...


def _set_signals_blocked(obj: QObject, *, blocked: bool) -> None:
    obj.blockSignals(blocked)


def _capture_failure[T](action: Callable[[], T]) -> tuple[T | None, Exception | None]:
    try:
        return action(), None
    except Exception as exc:  # ruff: ignore[blind-except]
        return None, exc


def _refresh_autostart_proxy_mode(proxy_mode: str) -> tuple[bool, Exception | None]:
    enable_autostart = True
    result, error = _capture_failure(
        lambda: sync_autostart(enable_autostart, CONFIG_DIR, proxy_mode=proxy_mode)
    )
    return (False if result is None else result), error


def _macos_auth_sources() -> tuple[tuple[str, str], ...]:
    return (
        (tr('settings.auth_source.choose_on_launch'), ''),
        (tr('settings.auth_source.manual_token'), 'manual'),
        (tr('settings.auth_source.chrome'), 'Chrome'),
        (tr('settings.auth_source.safari'), 'Safari'),
        (tr('settings.auth_source.firefox'), 'Firefox'),
        (tr('settings.auth_source.brave'), 'Brave'),
        (tr('settings.auth_source.edge'), 'Edge'),
        (tr('settings.auth_source.chromium'), 'Chromium'),
        (tr('settings.auth_source.opera'), 'Opera'),
        (tr('settings.auth_source.vivaldi'), 'Vivaldi'),
    )


def _ensure_macos_hosts_helper(parent: QWidget) -> bool:
    """Ensure Hosts mode has its privileged helper before a mode transition."""
    if helper_is_ready():
        return True

    prompt = QMessageBox(parent)
    prompt.setWindowTitle(tr('app.install_proxy_helper'))
    prompt.setIcon(QMessageBox.Icon.Information)
    prompt.setText(tr('app.install_the_fleasion_macos_proxy_helper'))
    prompt.setInformativeText(tr('app.macos_requires_a_small_root_service_to'))
    install_button = prompt.addButton(tr('app.install_helper'), QMessageBox.ButtonRole.AcceptRole)
    cancel_button = prompt.addButton(tr('app.not_now'), QMessageBox.ButtonRole.RejectRole)
    prompt.setDefaultButton(install_button)
    prompt.exec()
    if prompt.clickedButton() == cancel_button:
        log_buffer.log(
            'ProxyHelper',
            'macOS Hosts mode switch cancelled before restart because helper installation was postponed',
        )
        return False

    log_buffer.log(
        'ProxyHelper',
        'Installing macOS proxy helper before switching the running app to Hosts File mode',
    )
    ok, detail = install_helper()
    if ok:
        log_buffer.log(
            'ProxyHelper',
            'macOS proxy helper is ready; continuing Hosts File mode switch in-process',
        )
        return True

    log_buffer.log('ProxyHelper', f'macOS proxy helper install failed before mode switch: {detail}')
    QMessageBox.warning(
        parent,
        tr('app.fleasion_proxy_helper_installation_failed'),
        tr('app.fleasion_could_not_install_or_start_the', value0=detail),
    )
    return False


class EnvProxyWarningDialog(QMessageBox):
    """Explain the Player-only relaunch behavior when Env Proxy is selected."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.settings_tab.roblox_env_proxy'))
        self.setIcon(QMessageBox.Icon.Information)
        self.setText(tr('ui.gui.settings_tab.fleasion_will_relaunch_roblox_player_with_a'))
        self.setStandardButtons(QMessageBox.StandardButton.Ok)


class SettingsTab(QWidget):
    """Settings tab exposing all options found in the system tray Settings menu."""

    def __init__(
        self,
        config_manager: object,
        system_tray: object | None = None,
        parent: QWidget | None = None,
        *,
        defer_setup: bool = False,
    ) -> None:
        super().__init__(parent)
        self._config = cast('ConfigManager', config_manager)
        self._tray = cast('_SystemTrayLike | None', system_tray)
        self._manual_proxy_credentials_timer = QTimer(self)
        self._manual_proxy_credentials_timer.setSingleShot(True)
        self._manual_proxy_credentials_timer.setInterval(10_000)
        self._manual_proxy_credentials_timer.timeout.connect(
            self._revert_manual_proxy_without_credentials
        )
        self._linux_status_thread: Thread | None = None
        self._linux_status_pending = False
        self._linux_status_text = ''
        self._linux_status_timer = QTimer(self)
        self._linux_status_timer.setInterval(25)
        self._linux_status_timer.timeout.connect(self._receive_linux_client_status)
        if not defer_setup:
            for _ in self.build_ui():
                pass

    def build_ui(self) -> Generator[None]:
        yield from self._build_ui()
        self._sync_manual_proxy_credentials_timer()

    # UI construction

    def _build_ui(self) -> Generator[None]:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        container.setObjectName('_FleasionSettingsContainer')
        self._settings_container = container
        self._container_layout = QVBoxLayout(container)
        self._container_layout.setSpacing(10)
        self._container_layout.setContentsMargins(10, 10, 10, 10)
        self._update_container_bg()
        scroll.setWidget(container)
        outer.addWidget(scroll)

        self._container_layout.addWidget(self._build_language_section())
        yield
        self._container_layout.addWidget(self._build_appearance_section())
        yield
        if sys.platform.startswith('linux'):
            self._container_layout.addWidget(self._build_linux_client_section())
            yield
        self._container_layout.addWidget(self._build_proxy_section())
        yield
        self._container_layout.addWidget(self._build_convenience_section())
        yield
        if sys.platform == 'darwin':
            self._container_layout.addWidget(self._build_macos_auth_section())
            yield
        self._container_layout.addWidget(self._build_scraper_section())
        yield
        self._container_layout.addWidget(self._build_scraped_games_section())
        yield
        self._container_layout.addWidget(self._build_export_section())
        yield
        self._container_layout.addStretch()

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._footer_widget = footer_widget
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        self._status_label = QLabel('')
        self._status_label.setStyleSheet('color: #888;')
        footer_layout.addWidget(self._status_label)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton(tr('ui.gui.settings_tab.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)

        outer.addWidget(footer_widget)

    @override
    def changeEvent(self, a0: QEvent) -> None:
        super().changeEvent(a0)
        if a0.type() == QEvent.Type.PaletteChange:
            self._update_container_bg()

    # Language

    def _build_language_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.language.section'), expanded=True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr('settings.language.label')))
        self._language_combo = DropdownComboBox()
        for code, display_name in available_languages():
            self._language_combo.addItem(display_name, code)
        index = self._language_combo.findData(self._config.language)
        self._language_combo.setCurrentIndex(max(0, index))
        self._language_combo.activated.connect(self._on_language_changed)
        row.addWidget(self._language_combo)
        row.addStretch()
        row_widget = QWidget()
        row_widget.setLayout(row)
        section.add_widget(row_widget)

        note = QLabel(tr('settings.language.fallback_note'))
        note.setWordWrap(True)
        section.add_widget(note)
        return section

    # Appearance

    def _build_appearance_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.appearance.section'), expanded=True)

        self._theme_buttons: dict[str, QCheckBox] = {}
        btn_group = QButtonGroup(self)
        btn_group.setExclusive(True)
        theme_row = QHBoxLayout()
        theme_row.setContentsMargins(0, 0, 0, 0)
        current_theme = self._config.theme
        for name, label in (
            ('System', tr('settings.appearance.theme.system')),
            ('Light', tr('settings.appearance.theme.light')),
            ('Dark', tr('settings.appearance.theme.dark')),
        ):
            chk = QCheckBox(label)
            chk.setChecked(name == current_theme)
            chk.toggled.connect(partial(self._on_theme_toggled, theme=name))
            btn_group.addButton(chk)
            theme_row.addWidget(chk)
            self._theme_buttons[name] = chk
        theme_row.addStretch()

        row_widget = QWidget()
        row_widget.setLayout(theme_row)
        section.add_widget(row_widget)

        return section

    def _build_linux_client_section(self) -> CollapsibleSection:
        linux_clients = importlib.import_module('fleasion.utils.linux_clients')
        linux_client_descriptors = linux_clients.LINUX_CLIENTS

        section = CollapsibleSection(tr('settings.linux_client.section'), expanded=True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr('ui.gui.settings_tab.client')))
        self._linux_client_combo = DropdownComboBox()
        self._linux_client_combo.addItem(tr('ui.gui.settings_tab.auto_desktop_handler'), 'auto')
        for client in linux_client_descriptors:
            self._linux_client_combo.addItem(client.display_name, client.key)
        index = self._linux_client_combo.findData(getattr(self._config, 'linux_client', 'auto'))
        self._linux_client_combo.setCurrentIndex(max(0, index))
        self._linux_client_combo.activated.connect(self._on_linux_client_changed)
        if len(linux_client_descriptors) <= 1:
            self._linux_client_combo.setEnabled(False)
            self._linux_client_combo.setToolTip(
                tr(
                    'ui.gui.settings_tab.value_is_currently_the_only_supported_linux',
                    value0=linux_client_descriptors[0].display_name,
                )
            )
        row.addWidget(self._linux_client_combo)
        row.addStretch()
        row_widget = QWidget()
        row_widget.setLayout(row)
        section.add_widget(row_widget)

        self._linux_client_status = QLabel()
        self._linux_client_status.setWordWrap(True)
        self._refresh_linux_client_status()
        section.add_widget(self._linux_client_status)
        return section

    def _refresh_linux_client_status(self) -> None:
        if not sys.platform.startswith('linux'):
            return
        if self._linux_status_thread is not None:
            self._linux_status_pending = True
            return
        self._linux_status_thread = Thread(
            target=self._probe_linux_client_status, name='fleasion-linux-client-probe', daemon=True
        )
        self._linux_status_thread.start()
        self._linux_status_timer.start()

    def _probe_linux_client_status(self) -> None:
        if platform_linux is None:
            return
        try:
            installed = ', '.join(
                item.display_name for item in platform_linux.linux_client_installations()
            )
            selected = platform_linux.selected_linux_client_display_name()
            self._linux_status_text = tr(
                'ui.gui.settings_tab.active_value_installed_value_fleasion_routes_linux',
                value0=selected,
                value1=installed or tr('settings.linux_client.none_detected'),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            log_buffer.log('Settings', f'Linux client detection failed: {exc}')
            self._linux_status_text = tr(
                'ui.gui.settings_tab.unable_to_detect_linux_roblox_clients'
            )

    def _receive_linux_client_status(self) -> None:
        thread = self._linux_status_thread
        if thread is None or thread.is_alive():
            return
        thread.join()
        self._linux_status_thread = None
        self._linux_status_timer.stop()
        if self._linux_status_pending:
            self._linux_status_pending = False
            self._refresh_linux_client_status()
            return
        self._linux_client_status.setText(self._linux_status_text)

    def _build_macos_auth_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.roblox_login.section'), expanded=True)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel(tr('ui.gui.settings_tab.roblox_is_signed_in_through')))
        self._macos_auth_source_combo = QComboBox()
        for label, value in _macos_auth_sources():
            self._macos_auth_source_combo.addItem(label, value)
        idx = self._macos_auth_source_combo.findData(self._config.macos_auth_source)
        self._macos_auth_source_combo.setCurrentIndex(max(0, idx))
        self._macos_auth_source_combo.activated.connect(self._on_macos_auth_source_changed)
        row.addWidget(self._macos_auth_source_combo)
        self._manual_token_btn = QPushButton(tr('ui.gui.settings_tab.import_token'))
        self._manual_token_btn.clicked.connect(self._on_import_manual_token)
        row.addWidget(self._manual_token_btn)
        row.addStretch()

        row_widget = QWidget()
        row_widget.setLayout(row)
        section.add_widget(row_widget)
        return section

    # Proxy

    def _build_proxy_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.proxy.section'), expanded=True)

        self._proxy_features_chk = QCheckBox(tr('ui.gui.settings_tab.enable_proxy_features'))
        self._proxy_features_chk.setChecked(self._config.proxy_features_enabled)
        self._proxy_features_chk.toggled.connect(self._on_proxy_features_toggled)
        section.add_widget(self._proxy_features_chk)

        # Warning shown under the proxy features checkbox
        self._proxy_warning_label = QLabel(
            tr('ui.gui.settings_tab.do_not_touch_unless_you_know_what')
        )
        self._proxy_warning_label.setStyleSheet('color: #e07a00; font-weight: bold;')
        section.add_widget(self._proxy_warning_label)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.addWidget(QLabel(tr('ui.gui.settings_tab.upstream_transport')))
        self._upstream_mode_combo = DropdownComboBox()
        self._upstream_mode_combo.addItem(tr('ui.gui.settings_tab.auto'), 'auto')
        self._upstream_mode_combo.addItem(tr('ui.gui.settings_tab.direct_ip'), 'direct_ip')
        self._upstream_mode_combo.addItem(tr('ui.gui.settings_tab.system_proxy'), 'system_proxy')
        self._upstream_mode_combo.addItem(
            tr('ui.gui.settings_tab.manual_http_connect'), 'http_connect'
        )
        self._upstream_mode_combo.addItem(tr('ui.gui.settings_tab.manual_socks5'), 'socks5')
        current_mode = self._config.upstream_transport_mode
        idx = self._upstream_mode_combo.findData(current_mode)
        self._upstream_mode_combo.setCurrentIndex(max(0, idx))
        self._upstream_mode_combo.activated.connect(self._on_upstream_mode_changed)
        mode_row.addWidget(self._upstream_mode_combo)
        mode_row.addStretch()
        mode_widget = QWidget()
        mode_widget.setLayout(mode_row)
        section.add_widget(mode_widget)

        proxy_mode_row = QHBoxLayout()
        proxy_mode_row.setContentsMargins(0, 0, 0, 0)
        proxy_mode_row.addWidget(QLabel(tr('ui.gui.settings_tab.proxy_mode')))
        self._proxy_mode_combo = DropdownComboBox()
        self._proxy_mode_combo.addItem(tr('ui.gui.settings_tab.hosts_file'), 'hosts')
        self._proxy_mode_combo.addItem(tr('ui.gui.settings_tab.roblox_env_proxy'), 'env')
        current_proxy_mode = self._config.proxy_mode
        proxy_mode_idx = self._proxy_mode_combo.findData(current_proxy_mode)
        self._proxy_mode_combo.setCurrentIndex(max(0, proxy_mode_idx))
        self._proxy_mode_combo.activated.connect(self._on_proxy_mode_changed)
        proxy_mode_row.addWidget(self._proxy_mode_combo)
        proxy_mode_row.addStretch()
        proxy_mode_widget = QWidget()
        proxy_mode_widget.setLayout(proxy_mode_row)
        section.add_widget(proxy_mode_widget)

        self._wire_preserving_chk = QCheckBox(
            tr('ui.gui.settings_tab.enable_wire_preserving_passthrough_advanced_compatibility_mode')
        )
        self._wire_preserving_chk.setChecked(self._config.wire_preserving_passthrough)
        self._wire_preserving_chk.toggled.connect(self._on_wire_preserving_toggled)
        section.add_widget(self._wire_preserving_chk)

        def _proxy_row(label: str, host_value: str, port_value: int) -> tuple[QLineEdit, QSpinBox]:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(label))
            host = QLineEdit()
            host.setText(host_value)
            host.setPlaceholderText(tr('ui.gui.settings_tab.host'))
            port = QSpinBox()
            port.setRange(0, 65535)
            port.setValue(port_value)
            port.setPrefix(tr('ui.gui.settings_tab.port'))
            row.addWidget(host, 1)
            row.addWidget(port)
            widget = QWidget()
            widget.setLayout(row)
            section.add_widget(widget)
            return host, port

        self._http_proxy_host, self._http_proxy_port = _proxy_row(
            'HTTP CONNECT',
            self._config.upstream_http_connect_host,
            self._config.upstream_http_connect_port,
        )
        self._http_proxy_host.editingFinished.connect(self._on_http_proxy_changed)
        self._http_proxy_port.valueChanged.connect(self._on_http_proxy_changed)

        self._socks5_host, self._socks5_port = _proxy_row(
            'SOCKS5',
            self._config.upstream_socks5_host,
            self._config.upstream_socks5_port,
        )
        self._socks5_host.editingFinished.connect(self._on_socks5_proxy_changed)
        self._socks5_port.valueChanged.connect(self._on_socks5_proxy_changed)

        auth_row = QHBoxLayout()
        auth_row.setContentsMargins(0, 0, 0, 0)
        auth_row.addWidget(QLabel(tr('ui.gui.settings_tab.proxy_auth')))
        self._http_proxy_user = QLineEdit()
        self._http_proxy_user.setText(self._config.upstream_http_connect_username)
        self._http_proxy_user.setPlaceholderText(tr('ui.gui.settings_tab.http_user'))
        self._http_proxy_pass = QLineEdit()
        self._http_proxy_pass.setText(self._config.upstream_http_connect_password)
        self._http_proxy_pass.setPlaceholderText(tr('ui.gui.settings_tab.http_password'))
        self._http_proxy_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._socks5_user = QLineEdit()
        self._socks5_user.setText(self._config.upstream_socks5_username)
        self._socks5_user.setPlaceholderText(tr('ui.gui.settings_tab.socks_user'))
        self._socks5_pass = QLineEdit()
        self._socks5_pass.setText(self._config.upstream_socks5_password)
        self._socks5_pass.setPlaceholderText(tr('ui.gui.settings_tab.socks_password'))
        self._socks5_pass.setEchoMode(QLineEdit.EchoMode.Password)
        for widget in (
            self._http_proxy_user,
            self._http_proxy_pass,
            self._socks5_user,
            self._socks5_pass,
        ):
            auth_row.addWidget(widget)
        auth_widget = QWidget()
        auth_widget.setLayout(auth_row)
        section.add_widget(auth_widget)
        self._http_proxy_user.editingFinished.connect(self._on_http_proxy_auth_changed)
        self._http_proxy_pass.editingFinished.connect(self._on_http_proxy_auth_changed)
        self._socks5_user.editingFinished.connect(self._on_socks5_proxy_auth_changed)
        self._socks5_pass.editingFinished.connect(self._on_socks5_proxy_auth_changed)

        limits_row = QHBoxLayout()
        limits_row.setContentsMargins(0, 0, 0, 0)
        limits_row.addWidget(QLabel(tr('ui.gui.settings_tab.vpn_connection_limits')))
        self._asset_limit_spin = NoWheelSpinBox()
        self._asset_limit_spin.setRange(1, 128)
        self._asset_limit_spin.setValue(self._config.vpn_compat_max_assetdelivery_connections)
        self._asset_limit_spin.setPrefix(tr('ui.gui.settings_tab.asset'))
        self._cdn_limit_spin = NoWheelSpinBox()
        self._cdn_limit_spin.setRange(1, 256)
        self._cdn_limit_spin.setValue(self._config.vpn_compat_max_cdn_connections)
        self._cdn_limit_spin.setPrefix(tr('ui.gui.settings_tab.cdn'))
        self._asset_limit_spin.valueChanged.connect(self._on_connection_limits_changed)
        self._cdn_limit_spin.valueChanged.connect(self._on_connection_limits_changed)
        limits_row.addWidget(self._asset_limit_spin)
        limits_row.addWidget(self._cdn_limit_spin)
        limits_row.addStretch()
        limits_widget = QWidget()
        limits_widget.setLayout(limits_row)
        section.add_widget(limits_widget)

        return section

    # Startup

    def _build_convenience_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.convenience.section'), expanded=True)

        self._open_dashboard_chk = QCheckBox(tr('ui.gui.settings_tab.open_dashboard_on_start'))
        self._open_dashboard_chk.setChecked(self._config.open_dashboard_on_launch)
        self._open_dashboard_chk.toggled.connect(self._on_open_dashboard_toggled)
        section.add_widget(self._open_dashboard_chk)

        self._auto_clear_cache_chk = QCheckBox(tr('ui.gui.settings_tab.auto_clear_cache_on_exit'))
        self._auto_clear_cache_chk.setChecked(self._config.auto_delete_cache_on_exit)
        self._auto_clear_cache_chk.toggled.connect(self._on_auto_clear_cache_toggled)
        section.add_widget(self._auto_clear_cache_chk)

        self._clear_cache_launch_chk = QCheckBox(tr('ui.gui.settings_tab.clear_cache_on_launch'))
        self._clear_cache_launch_chk.setChecked(self._config.clear_cache_on_launch)
        self._clear_cache_launch_chk.toggled.connect(self._on_clear_cache_launch_toggled)
        section.add_widget(self._clear_cache_launch_chk)

        self._lock_roblox_files_chk = QCheckBox(
            tr('ui.gui.settings_tab.lock_roblox_files_to_read_only')
        )
        self._lock_roblox_files_chk.setChecked(self._config.lock_roblox_files_read_only)
        self._lock_roblox_files_chk.setToolTip(
            tr('ui.gui.settings_tab.stops_roblox_from_overwriting_active_fleasion_modification')
        )
        self._lock_roblox_files_chk.toggled.connect(self._on_lock_roblox_files_toggled)
        section.add_widget(self._lock_roblox_files_chk)

        self._close_env_roblox_chk = QCheckBox(
            tr('ui.gui.settings_tab.close_env_proxied_roblox_player_on_exit')
        )
        self._close_env_roblox_chk.setChecked(self._config.close_env_proxy_roblox_on_exit)
        self._close_env_roblox_chk.setToolTip(
            tr('ui.gui.settings_tab.roblox_player_and_supported_linux_clients_depend')
        )
        self._close_env_roblox_chk.toggled.connect(self._on_close_env_roblox_toggled)
        section.add_widget(self._close_env_roblox_chk)

        self._run_on_boot_chk = QCheckBox(tr('ui.gui.settings_tab.run_on_boot'))
        self._run_on_boot_chk.setChecked(self._config.run_on_boot)
        self._run_on_boot_chk.toggled.connect(self._on_run_on_boot_toggled)
        section.add_widget(self._run_on_boot_chk)

        self._desktop_integration_chk = QCheckBox(
            tr('ui.gui.settings_tab.create_desktop_start_menu_integration_on_boot')
        )
        self._desktop_integration_chk.setChecked(self._config.desktop_integration)
        self._desktop_integration_chk.toggled.connect(self._on_desktop_integration_toggled)
        section.add_widget(self._desktop_integration_chk)

        self._close_scraped_games_chk = QCheckBox(tr('ui.gui.settings_tab.close_roblox_on_open'))
        self._close_scraped_games_chk.setChecked(self._config.close_scraped_games_on_open)
        self._close_scraped_games_chk.toggled.connect(self._on_close_scraped_games_toggled)
        section.add_widget(self._close_scraped_games_chk)

        self._close_to_tray_chk = QCheckBox(tr('ui.gui.settings_tab.close_to_tray'))
        self._close_to_tray_chk.setChecked(self._config.close_to_tray)
        self._close_to_tray_chk.toggled.connect(self._on_close_to_tray_toggled)
        section.add_widget(self._close_to_tray_chk)

        self._always_on_top_chk = QCheckBox(tr('ui.gui.settings_tab.always_on_top'))
        self._always_on_top_chk.setChecked(self._config.always_on_top)
        self._always_on_top_chk.toggled.connect(self._on_always_on_top_toggled)
        section.add_widget(self._always_on_top_chk)

        return section

    def _build_scraper_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.scraper.section'), expanded=True)

        self._cache_scraper_chk = QCheckBox(tr('ui.gui.settings_tab.enable_cache_scraper'))
        self._cache_scraper_chk.setChecked(self._is_cache_scraper_enabled())
        self._cache_scraper_chk.toggled.connect(self._on_cache_scraper_toggled)
        section.add_widget(self._cache_scraper_chk)

        self._show_names_chk = QCheckBox(tr('ui.gui.settings_tab.show_names'))
        self._show_names_chk.setChecked(self._config.show_names)
        self._show_names_chk.toggled.connect(self._on_show_names_toggled)
        section.add_widget(self._show_names_chk)

        self._show_creator_id_chk = QCheckBox(tr('ui.gui.settings_tab.show_user_id'))
        self._show_creator_id_chk.setChecked(self._config.show_creator_id)
        self._show_creator_id_chk.toggled.connect(self._on_show_creator_id_toggled)
        section.add_widget(self._show_creator_id_chk)

        return section

    # Export naming

    def _build_export_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.export_naming.section'), expanded=True)

        self._export_chks: dict[str, QCheckBox] = {}
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for option, label in (
            ('name', tr('settings.export_naming.name')),
            ('id', tr('settings.export_naming.id')),
            ('hash', tr('settings.export_naming.hash')),
        ):
            chk = QCheckBox(label)
            chk.setChecked(self._config.is_export_naming_enabled(option))
            chk.toggled.connect(partial(self._on_export_naming_toggled, option=option))
            row.addWidget(chk)
            self._export_chks[option] = chk
        row.addStretch()
        row_widget = QWidget()
        row_widget.setLayout(row)
        section.add_widget(row_widget)

        return section

    def _build_scraped_games_section(self) -> CollapsibleSection:
        section = CollapsibleSection(tr('settings.scraped_games.section'), expanded=True)

        self._show_replacer_notifications_chk = QCheckBox(
            tr('ui.gui.settings_tab.show_replacer_notifications')
        )
        self._show_replacer_notifications_chk.setChecked(self._config.show_replacer_notifications)
        self._show_replacer_notifications_chk.toggled.connect(
            self._on_show_replacer_notifications_toggled
        )
        section.add_widget(self._show_replacer_notifications_chk)

        self._close_viewer_on_replace_chk = QCheckBox(
            tr('ui.gui.settings_tab.close_viewer_on_replace')
        )
        self._close_viewer_on_replace_chk.setChecked(self._config.close_viewer_on_replace)
        self._close_viewer_on_replace_chk.toggled.connect(self._on_close_viewer_on_replace_toggled)
        section.add_widget(self._close_viewer_on_replace_chk)

        self._close_scraped_games_menu_on_open_chk = QCheckBox(
            tr('ui.gui.settings_tab.close_scraped_games_menu_on_open')
        )
        self._close_scraped_games_menu_on_open_chk.setChecked(
            self._config.close_scraped_games_menu_on_open
        )
        self._close_scraped_games_menu_on_open_chk.toggled.connect(
            self._on_close_scraped_games_menu_on_open_toggled
        )
        section.add_widget(self._close_scraped_games_menu_on_open_chk)

        return section

    def _update_container_bg(self) -> None:
        """Keep the Settings tab background aligned with the tab theme."""
        colors = ThemeManager.panel_colors(self.palette())
        self._settings_container.setStyleSheet(
            f'QWidget#_FleasionSettingsContainer {{ {colors.container_background_css} }}'
        )

    # Public

    def refresh_from_config(self) -> None:
        """Re-read all settings from config and update widgets (no signals emitted)."""
        for name, rb in self._theme_buttons.items():
            _set_signals_blocked(rb, blocked=True)
            rb.setChecked(name == self._config.theme)
            _set_signals_blocked(rb, blocked=False)

        for chk, value in [
            (self._open_dashboard_chk, self._config.open_dashboard_on_launch),
            (self._proxy_features_chk, self._config.proxy_features_enabled),
            (self._wire_preserving_chk, self._config.wire_preserving_passthrough),
            (self._auto_clear_cache_chk, self._config.auto_delete_cache_on_exit),
            (self._clear_cache_launch_chk, self._config.clear_cache_on_launch),
            (self._lock_roblox_files_chk, self._config.lock_roblox_files_read_only),
            (self._close_env_roblox_chk, self._config.close_env_proxy_roblox_on_exit),
            (self._run_on_boot_chk, self._config.run_on_boot),
            (self._desktop_integration_chk, self._config.desktop_integration),
            (self._close_scraped_games_chk, self._config.close_scraped_games_on_open),
            (self._close_to_tray_chk, self._config.close_to_tray),
            (self._always_on_top_chk, self._config.always_on_top),
            (
                self._show_replacer_notifications_chk,
                self._config.show_replacer_notifications,
            ),
            (self._close_viewer_on_replace_chk, self._config.close_viewer_on_replace),
            (
                self._close_scraped_games_menu_on_open_chk,
                self._config.close_scraped_games_menu_on_open,
            ),
            (self._show_names_chk, self._config.show_names),
            (self._show_creator_id_chk, self._config.show_creator_id),
        ]:
            _set_signals_blocked(chk, blocked=True)
            chk.setChecked(value)
            _set_signals_blocked(chk, blocked=False)

        idx = self._upstream_mode_combo.findData(self._config.upstream_transport_mode)
        _set_signals_blocked(self._upstream_mode_combo, blocked=True)
        self._upstream_mode_combo.setCurrentIndex(max(0, idx))
        _set_signals_blocked(self._upstream_mode_combo, blocked=False)

        idx = self._proxy_mode_combo.findData(self._config.proxy_mode)
        _set_signals_blocked(self._proxy_mode_combo, blocked=True)
        self._proxy_mode_combo.setCurrentIndex(max(0, idx))
        _set_signals_blocked(self._proxy_mode_combo, blocked=False)

        if sys.platform.startswith('linux'):
            idx = self._linux_client_combo.findData(getattr(self._config, 'linux_client', 'auto'))
            _set_signals_blocked(self._linux_client_combo, blocked=True)
            self._linux_client_combo.setCurrentIndex(max(0, idx))
            _set_signals_blocked(self._linux_client_combo, blocked=False)
            self._refresh_linux_client_status()

        for widget, value in [
            (self._http_proxy_host, self._config.upstream_http_connect_host),
            (self._http_proxy_user, self._config.upstream_http_connect_username),
            (self._http_proxy_pass, self._config.upstream_http_connect_password),
            (self._socks5_host, self._config.upstream_socks5_host),
            (self._socks5_user, self._config.upstream_socks5_username),
            (self._socks5_pass, self._config.upstream_socks5_password),
        ]:
            _set_signals_blocked(widget, blocked=True)
            widget.setText(value)
            _set_signals_blocked(widget, blocked=False)

        for widget, value in [
            (self._http_proxy_port, self._config.upstream_http_connect_port),
            (self._socks5_port, self._config.upstream_socks5_port),
            (
                self._asset_limit_spin,
                self._config.vpn_compat_max_assetdelivery_connections,
            ),
            (self._cdn_limit_spin, self._config.vpn_compat_max_cdn_connections),
        ]:
            _set_signals_blocked(widget, blocked=True)
            widget.setValue(value)
            _set_signals_blocked(widget, blocked=False)

        self.set_cache_scraper_enabled(self._is_cache_scraper_enabled())

        for option, chk in self._export_chks.items():
            _set_signals_blocked(chk, blocked=True)
            chk.setChecked(self._config.is_export_naming_enabled(option))
            _set_signals_blocked(chk, blocked=False)
        if sys.platform == 'darwin':
            idx = self._macos_auth_source_combo.findData(self._config.macos_auth_source)
            _set_signals_blocked(self._macos_auth_source_combo, blocked=True)
            self._macos_auth_source_combo.setCurrentIndex(max(0, idx))
            _set_signals_blocked(self._macos_auth_source_combo, blocked=False)

        idx = self._language_combo.findData(self._config.language)
        _set_signals_blocked(self._language_combo, blocked=True)
        self._language_combo.setCurrentIndex(max(0, idx))
        _set_signals_blocked(self._language_combo, blocked=False)

    # Handlers

    def _clear_roblox_cache(self) -> None:
        delete_cache = importlib.import_module('.delete_cache', __package__)
        window = delete_cache.DeleteCacheWindow()
        window.show()

    def _on_language_changed(self, *_args: object) -> None:
        language = str(self._language_combo.currentData() or 'en')
        self._config.language = language
        if self._config.language != get_language():
            QMessageBox.information(
                self,
                tr('settings.language.restart_required_title'),
                tr('settings.language.restart_required_body'),
            )

    def _on_theme_toggled(self, checked: bool, theme: str) -> None:
        if not checked:
            return
        ThemeManager.apply_theme(theme)
        self._config.theme = theme
        if self._tray and hasattr(self._tray, 'theme_actions'):
            for name, action in self._tray.theme_actions.items():
                action.setChecked(name == theme)

    def _on_open_dashboard_toggled(self, checked: bool) -> None:
        self._config.open_dashboard_on_launch = checked
        if self._tray and hasattr(self._tray, 'open_dashboard_action'):
            self._tray.open_dashboard_action.setChecked(checked)

    def _on_proxy_features_toggled(self, checked: bool) -> None:
        if not checked:
            result = QMessageBox.warning(
                self,
                tr('ui.gui.settings_tab.disable_proxy_features'),
                tr('ui.gui.settings_tab.turning_off_proxy_features_will_stop_the'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            )
            if result != QMessageBox.StandardButton.Yes:
                _set_signals_blocked(self._proxy_features_chk, blocked=True)
                self._proxy_features_chk.setChecked(True)
                _set_signals_blocked(self._proxy_features_chk, blocked=False)
                return

        if self._tray and hasattr(self._tray, 'set_proxy_features_enabled'):
            self._tray.set_proxy_features_enabled(checked)
        else:
            self._config.proxy_features_enabled = checked

    def _on_upstream_mode_changed(self, *_args: object) -> None:
        self._config.upstream_transport_mode = cast('str', self._upstream_mode_combo.currentData())
        self._sync_manual_proxy_credentials_timer()

    def _on_linux_client_changed(self, *_args: object) -> None:
        if not sys.platform.startswith('linux'):
            return
        new_client = str(self._linux_client_combo.currentData() or 'auto')
        previous_client = getattr(self._config, 'linux_client', 'auto')
        if new_client == previous_client:
            return

        proxy_master = getattr(self._tray, 'proxy_master', None) if self._tray else None
        proxy_was_running = bool(proxy_master is not None and proxy_master.is_running)
        # Stop first so the old client's app-scoped Flatpak override is disarmed.
        if proxy_master is not None and proxy_was_running:
            proxy_master.stop()

        mod_manager = getattr(self._tray, 'mod_manager', None) if self._tray else None
        if mod_manager is not None:
            # Restore the old client's files before changing the selection.
            mod_manager.restore_all()

        self._config.linux_client = new_client
        platform_linux = importlib.import_module('fleasion.utils.platform_linux')
        platform_linux.set_linux_client_preference(new_client)

        if mod_manager is not None:
            mod_manager.refresh_roblox_dirs(reapply_if_changed=True)
        if proxy_master is not None and proxy_was_running:
            proxy_master.start()
        self._refresh_linux_client_status()

    def _on_proxy_mode_changed(self, *_args: object) -> None:
        previous_mode = self._config.proxy_mode
        new_mode = cast('str', self._proxy_mode_combo.currentData())

        if (
            new_mode == 'hosts'
            and previous_mode != 'hosts'
            and sys.platform == 'darwin'
            and self._config.proxy_features_enabled
            and not _ensure_macos_hosts_helper(self)
        ):
            # Do not persist Hosts mode or enter the verified restart handoff
            # until its privileged prerequisite is actually available
            # Prompting from the replacement process can deadlock the handoff while
            # the original process waits synchronously for replacement readiness
            previous_index = self._proxy_mode_combo.findData(previous_mode)
            if previous_index >= 0:
                _set_signals_blocked(self._proxy_mode_combo, blocked=True)
                self._proxy_mode_combo.setCurrentIndex(previous_index)
                _set_signals_blocked(self._proxy_mode_combo, blocked=False)
            return

        self._config.proxy_mode = new_mode
        if self._config.run_on_boot:
            boot_ok, sync_error = _refresh_autostart_proxy_mode(new_mode)
            if sync_error is not None:
                log_buffer.log('Autostart', f'Run on Boot mode refresh failed: {sync_error}')
            if not boot_ok:
                QMessageBox.warning(
                    self,
                    tr('ui.gui.settings_tab.run_on_boot_update_failed'),
                    tr(
                        'ui.gui.settings_tab.proxy_mode_changed_but_the_run_on',
                        value0=windows_autostart_privilege_hint(new_mode),
                    ),
                )
        if new_mode == 'env' and previous_mode != 'env':
            EnvProxyWarningDialog(self).exec()
            # Env mode needs nothing this process doesn't already have, so
            # swap the running proxy over live instead of restarting the app.
            proxy_master = getattr(self._tray, 'proxy_master', None) if self._tray else None
            if proxy_master is not None and hasattr(proxy_master, 'restart_for_mode_switch'):
                proxy_master.restart_for_mode_switch()
                if sys.platform == 'win32':
                    # The proxy may have fallen back from 58443 to a dynamic
                    # port. Arm Store/GDK only after the restarted proxy has
                    # published its final loopback URL.
                    run_in_thread(arm_windows_gdk_env_proxy_when_ready)(proxy_master)
            monitor = getattr(self._tray, 'roblox_monitor', None) if self._tray else None
            lifecycle = getattr(monitor, 'env_lifecycle', None)
            if (
                lifecycle is not None
                and monitor is not None
                and self._config.proxy_features_enabled
                and monitor.is_player_running()
            ):
                if sys.platform.startswith('linux'):
                    platform_linux = importlib.import_module('fleasion.utils.platform_linux')
                    exe_path = Path(platform_linux.selected_linux_client_app_id())
                else:
                    exe_path = get_roblox_player_exe_path()
                run_in_thread(lifecycle.handle_player_launch)(exe_path)
        if new_mode == 'hosts' and previous_mode != 'hosts':
            proxy_master = getattr(self._tray, 'proxy_master', None) if self._tray else None
            can_live_switch = bool(
                proxy_master is not None
                and hasattr(proxy_master, 'can_live_switch_to_hosts')
                and proxy_master.can_live_switch_to_hosts()
            )
            if not self._config.proxy_features_enabled:
                # There is no active route to swap. Persisting Hosts File mode
                # is sufficient; enabling proxy features later owns any helper
                # install/elevation that mode requires.
                pass
            elif (
                proxy_master is not None
                and can_live_switch
                and hasattr(proxy_master, 'restart_for_mode_switch')
            ):
                # Prefer an in-process transition when this process already has
                # a safe privileged path. stop() disarms Env Proxy state before
                # start() applies hosts routing, so no stale route is left behind.
                proxy_master.restart_for_mode_switch()
            else:
                # Windows normal-user mode (and macOS without its helper) needs
                # a new startup path. Do not tear down the working Env Proxy
                # until the final replacement owns single-instance state and
                # has established the configured Hosts File proxy.
                restart_result = False
                if self._tray and hasattr(self._tray, 'restart_fleasion'):
                    restart_result = self._tray.restart_fleasion()
                if restart_result is True:
                    return
                if restart_result is None:
                    # Fail closed when the final replacement cannot be proven
                    # dead. It may still own Hosts/single-instance resources, so
                    # automatically rewriting config/autostart back to Env would
                    # create a second contradictory owner. Keep the user's Hosts
                    # selection persisted and require a clean manual restart.
                    QMessageBox.critical(
                        self,
                        tr('ui.gui.settings_tab.proxy_mode_change_incomplete'),
                        tr('ui.gui.settings_tab.fleasion_could_not_confirm_that_the_replacement'),
                    )
                    return

                # The replacement never became viable and was confirmed gone.
                # Restore every persisted
                # surface so the still-running process and launch integration
                # continue to describe the Env Proxy that is actually active.
                self._config.proxy_mode = previous_mode
                previous_index = self._proxy_mode_combo.findData(previous_mode)
                if previous_index >= 0:
                    _set_signals_blocked(self._proxy_mode_combo, blocked=True)
                    self._proxy_mode_combo.setCurrentIndex(previous_index)
                    _set_signals_blocked(self._proxy_mode_combo, blocked=False)
                if self._config.run_on_boot:
                    _, sync_error = _refresh_autostart_proxy_mode(previous_mode)
                    if sync_error is not None:
                        log_buffer.log(
                            'Autostart',
                            f'Run on Boot rollback after failed mode switch failed: {sync_error}',
                        )
                QMessageBox.warning(
                    self,
                    tr('ui.gui.settings_tab.proxy_mode_change_failed'),
                    tr('ui.gui.settings_tab.fleasion_could_not_verify_that_the_hosts'),
                )
                return
        if self._tray and hasattr(self._tray, 'notify_proxy_mode_changed'):
            self._tray.notify_proxy_mode_changed()

    def _on_wire_preserving_toggled(self, checked: bool) -> None:
        self._config.wire_preserving_passthrough = checked

    def _on_http_proxy_changed(self, *_args: object) -> None:
        self._config.upstream_http_connect_host = self._http_proxy_host.text()
        self._config.upstream_http_connect_port = self._http_proxy_port.value()

    def _on_http_proxy_auth_changed(self) -> None:
        self._config.upstream_http_connect_username = self._http_proxy_user.text()
        self._config.upstream_http_connect_password = self._http_proxy_pass.text()
        self._sync_manual_proxy_credentials_timer()

    def _on_socks5_proxy_changed(self, *_args: object) -> None:
        self._config.upstream_socks5_host = self._socks5_host.text()
        self._config.upstream_socks5_port = self._socks5_port.value()

    def _on_socks5_proxy_auth_changed(self) -> None:
        self._config.upstream_socks5_username = self._socks5_user.text()
        self._config.upstream_socks5_password = self._socks5_pass.text()
        self._sync_manual_proxy_credentials_timer()

    def _selected_manual_proxy_has_credentials(self) -> bool:
        mode = self._upstream_mode_combo.currentData()
        if mode == 'http_connect':
            return bool(self._http_proxy_user.text().strip() or self._http_proxy_pass.text())
        if mode == 'socks5':
            return bool(self._socks5_user.text().strip() or self._socks5_pass.text())
        return True

    def _sync_manual_proxy_credentials_timer(self) -> None:
        if self._selected_manual_proxy_has_credentials():
            self._manual_proxy_credentials_timer.stop()
        else:
            self._manual_proxy_credentials_timer.start()

    def _revert_manual_proxy_without_credentials(self) -> None:
        if self._selected_manual_proxy_has_credentials():
            return
        auto_index = self._upstream_mode_combo.findData('auto')
        _set_signals_blocked(self._upstream_mode_combo, blocked=True)
        self._upstream_mode_combo.setCurrentIndex(auto_index)
        _set_signals_blocked(self._upstream_mode_combo, blocked=False)
        self._config.upstream_transport_mode = 'auto'
        proxy_master = getattr(self._tray, 'proxy_master', None)
        if proxy_master is not None and proxy_master.is_running:

            def _restart_proxy() -> None:
                proxy_master.stop()
                proxy_master.start()

            run_in_thread(_restart_proxy)()

    def _on_connection_limits_changed(self, *_args: object) -> None:
        self._config.vpn_compat_max_assetdelivery_connections = self._asset_limit_spin.value()
        self._config.vpn_compat_max_cdn_connections = self._cdn_limit_spin.value()

    def _on_run_on_boot_toggled(self, checked: bool) -> None:
        ok = sync_autostart(
            checked,
            CONFIG_DIR,
            proxy_mode=self._config.proxy_mode,
        )
        if ok:
            self._config.run_on_boot = checked
            if self._tray and hasattr(self._tray, 'run_on_boot_action'):
                self._tray.run_on_boot_action.setChecked(checked)
        else:
            if sys.platform == 'win32':
                if show_run_on_boot_failure(self, self._config.proxy_mode, enabled=checked):
                    self._config.run_on_boot = checked
                    if self._tray and hasattr(self._tray, 'run_on_boot_action'):
                        self._tray.run_on_boot_action.setChecked(checked)
                    return
            _set_signals_blocked(self._run_on_boot_chk, blocked=True)
            self._run_on_boot_chk.setChecked(not checked)
            _set_signals_blocked(self._run_on_boot_chk, blocked=False)
            QMessageBox.warning(
                self,
                tr('ui.gui.settings_tab.run_on_boot_failed'),
                tr('ui.gui.settings_tab.failed_to_register_the_autostart_task_check'),
            )

    def _on_lock_roblox_files_toggled(self, checked: bool) -> None:
        self._config.lock_roblox_files_read_only = checked
        mod_manager = getattr(self._tray, 'mod_manager', None)
        if mod_manager is not None and hasattr(mod_manager, 'set_read_only_lock_enabled'):
            mod_manager.set_read_only_lock_enabled(checked)

    def _on_close_env_roblox_toggled(self, checked: bool) -> None:
        self._config.close_env_proxy_roblox_on_exit = checked

    def _on_desktop_integration_toggled(self, checked: bool) -> None:
        ok = sync_desktop_integration(checked)
        if ok:
            self._config.desktop_integration = checked
            if self._tray and hasattr(self._tray, 'desktop_integration_action'):
                self._tray.desktop_integration_action.setChecked(checked)
            if sys.platform.startswith('linux') and self._config.run_on_boot:
                enable_autostart = True
                if not sync_autostart(
                    enable_autostart, CONFIG_DIR, proxy_mode=self._config.proxy_mode
                ):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.settings_tab.run_on_boot_failed'),
                        tr('ui.gui.settings_tab.failed_to_refresh_the_autostart_task_after'),
                    )
        else:
            _set_signals_blocked(self._desktop_integration_chk, blocked=True)
            self._desktop_integration_chk.setChecked(not checked)
            _set_signals_blocked(self._desktop_integration_chk, blocked=False)
            QMessageBox.warning(
                self,
                tr('ui.gui.settings_tab.desktop_integration_failed'),
                tr('ui.gui.settings_tab.failed_to_create_desktop_start_menu_integration'),
            )

    def _on_always_on_top_toggled(self, checked: bool) -> None:
        self._config.always_on_top = checked
        if self._tray and hasattr(self._tray, 'always_on_top_action'):
            self._tray.always_on_top_action.setChecked(checked)
        if self._tray and hasattr(self._tray, 'open_windows'):
            for window in self._tray.open_windows:
                if window.isVisible():
                    flags = window.windowFlags()
                    if checked:
                        flags |= Qt.WindowType.WindowStaysOnTopHint
                    else:
                        flags &= ~Qt.WindowType.WindowStaysOnTopHint
                    window.setWindowFlags(flags)
                    window.show()

    def _on_close_to_tray_toggled(self, checked: bool) -> None:
        self._config.close_to_tray = checked
        if self._tray and hasattr(self._tray, 'close_to_tray_action'):
            self._tray.close_to_tray_action.setChecked(checked)

    def _on_auto_clear_cache_toggled(self, checked: bool) -> None:
        self._config.auto_delete_cache_on_exit = checked
        if self._tray and hasattr(self._tray, 'auto_delete_cache_action'):
            self._tray.auto_delete_cache_action.setChecked(checked)

    def _on_clear_cache_launch_toggled(self, checked: bool) -> None:
        self._config.clear_cache_on_launch = checked
        if self._tray and hasattr(self._tray, 'clear_cache_action'):
            self._tray.clear_cache_action.setChecked(checked)

    def _on_macos_auth_source_changed(self, *_args: object) -> None:
        if sys.platform != 'darwin':
            return
        self._config.macos_auth_source = cast('str', self._macos_auth_source_combo.currentData())
        notify_auth_source_changed()

    def _on_import_manual_token(self) -> None:
        if sys.platform != 'darwin':
            return
        rando_stuff_tab = importlib.import_module('.rando_stuff_tab', __package__)
        dlg = rando_stuff_tab.AddAccountDialog(
            self, title=tr('settings.roblox_login.import_token_title')
        )
        dlg.set_ok_label(tr('settings.roblox_login.import'))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        if not dlg.result_cookie:
            return
        valid, detail = validate_roblosecurity_for_import(dlg.result_cookie)
        if not valid:
            QMessageBox.warning(
                self,
                tr('ui.gui.settings_tab.invalid_roblox_token'),
                tr(
                    'ui.gui.settings_tab.fleasion_could_not_confirm_this_roblox_token',
                    value0=detail,
                ),
            )
            return
        if not store_manual_roblosecurity(dlg.result_cookie):
            QMessageBox.warning(
                self,
                tr('ui.gui.settings_tab.token_import_failed'),
                tr('ui.gui.settings_tab.fleasion_could_not_store_the_roblox_token'),
            )
            return
        self._config.macos_auth_source = 'manual'
        notify_auth_source_changed()
        idx = self._macos_auth_source_combo.findData('manual')
        _set_signals_blocked(self._macos_auth_source_combo, blocked=True)
        self._macos_auth_source_combo.setCurrentIndex(max(0, idx))
        _set_signals_blocked(self._macos_auth_source_combo, blocked=False)
        username = dlg.result_username or tr('settings.roblox_login.roblox_account_fallback')
        QMessageBox.information(
            self,
            tr('ui.gui.settings_tab.token_imported'),
            tr('ui.gui.settings_tab.value_was_stored_encrypted', value0=username),
        )

    def _on_close_scraped_games_toggled(self, checked: bool) -> None:
        self._config.close_scraped_games_on_open = checked
        if self._tray and hasattr(self._tray, 'close_scraped_games_action'):
            self._tray.close_scraped_games_action.setChecked(checked)

    def _on_close_viewer_on_replace_toggled(self, checked: bool) -> None:
        self._config.close_viewer_on_replace = checked
        if self._tray and hasattr(self._tray, 'close_viewer_on_replace_action'):
            self._tray.close_viewer_on_replace_action.setChecked(checked)

    def _on_close_scraped_games_menu_on_open_toggled(self, checked: bool) -> None:
        self._config.close_scraped_games_menu_on_open = checked
        if self._tray and hasattr(self._tray, 'close_scraped_games_menu_on_open_action'):
            self._tray.close_scraped_games_menu_on_open_action.setChecked(checked)

    def _on_show_replacer_notifications_toggled(self, checked: bool) -> None:
        self._config.show_replacer_notifications = checked
        if self._tray and hasattr(self._tray, 'show_replacer_notifications_action'):
            self._tray.show_replacer_notifications_action.setChecked(checked)

    def _on_show_names_toggled(self, checked: bool) -> None:
        self._config.show_names = checked
        if self._tray and hasattr(self._tray, 'show_names_action'):
            self._tray.show_names_action.setChecked(checked)
        self._apply_to_cache_viewer('show_names', checked)

    def _on_show_creator_id_toggled(self, checked: bool) -> None:
        self._config.show_creator_id = checked
        if self._tray and hasattr(self._tray, 'show_creator_id_action'):
            self._tray.show_creator_id_action.setChecked(checked)
        self._apply_to_cache_viewer('show_creator_id', checked)

    def _apply_to_cache_viewer(
        self,
        setting: Literal['show_names', 'show_creator_id'],
        value: bool,
    ) -> None:
        if self._tray and self._tray.dashboard_window:
            self._tray.dashboard_window.apply_cache_viewer_display_setting(
                setting,
                enabled=value,
            )

    def _is_cache_scraper_enabled(self) -> bool:
        return bool(self._tray and self._tray.proxy_master.cache_scraper.enabled)

    def set_cache_scraper_enabled(self, enabled: bool) -> None:
        _set_signals_blocked(self._cache_scraper_chk, blocked=True)
        self._cache_scraper_chk.setChecked(enabled)
        _set_signals_blocked(self._cache_scraper_chk, blocked=False)

    def _on_cache_scraper_toggled(self, checked: bool) -> None:
        if not self._tray:
            return
        self._tray.proxy_master.cache_scraper.set_enabled(checked)
        if hasattr(self._tray, 'cache_scraper_action'):
            with QSignalBlocker(self._tray.cache_scraper_action):
                self._tray.cache_scraper_action.setChecked(checked)
        if self._tray.dashboard_window:
            self._tray.dashboard_window.set_cache_scraper_enabled(enabled=checked)

    def _on_export_naming_toggled(self, checked: bool, option: str) -> None:
        current = self._config.is_export_naming_enabled(option)
        if current != checked:
            new_state = self._config.toggle_export_naming(option)
            _set_signals_blocked(self._export_chks[option], blocked=True)
            self._export_chks[option].setChecked(new_state)
            _set_signals_blocked(self._export_chks[option], blocked=False)
        if self._tray and hasattr(self._tray, 'export_naming_actions'):
            self._tray.export_naming_actions[option].setChecked(
                self._config.is_export_naming_enabled(option)
            )
