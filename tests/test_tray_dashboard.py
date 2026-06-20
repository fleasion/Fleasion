from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QSystemTrayIcon

from Fleasion import tray as tray_module
from Fleasion import app as app_module
from Fleasion.utils import platform_macos
from Fleasion.tray import SystemTray


class _DashboardStub:
    def __init__(self, visible: bool):
        self.visible = visible
        self.hide_calls = 0

    def isVisible(self):
        return self.visible

    def hide(self):
        self.visible = False
        self.hide_calls += 1


class _TrayIconStub:
    def __init__(self):
        self.hide_calls = 0
        self.context_menus = []
        self.delete_later_calls = 0

    def hide(self):
        self.hide_calls += 1

    def setContextMenu(self, menu):
        self.context_menus.append(menu)

    def deleteLater(self):
        self.delete_later_calls += 1


def test_dashboard_toggle_hides_visible_window():
    system_tray = SystemTray.__new__(SystemTray)
    dashboard = _DashboardStub(visible=True)
    foreground_modes = []
    system_tray.dashboard_window = dashboard
    system_tray._show_replacer_config = lambda: None
    system_tray._set_dashboard_foreground_mode = foreground_modes.append

    system_tray._toggle_dashboard()

    assert dashboard.hide_calls == 1
    assert foreground_modes == [False]


def test_dashboard_toggle_shows_hidden_window():
    system_tray = SystemTray.__new__(SystemTray)
    dashboard = _DashboardStub(visible=False)
    show_calls = []
    system_tray.dashboard_window = dashboard
    system_tray._show_replacer_config = lambda: show_calls.append(True)

    system_tray._toggle_dashboard()

    assert show_calls == [True]


def test_show_dashboard_enables_foreground_mode_before_showing_existing_window():
    system_tray = SystemTray.__new__(SystemTray)
    calls = []
    dashboard = _DashboardStub(visible=False)
    dashboard.show = lambda: calls.append('show')
    dashboard.raise_ = lambda: calls.append('raise')
    dashboard.activateWindow = lambda: calls.append('activate')
    system_tray.dashboard_window = dashboard
    system_tray._set_dashboard_foreground_mode = lambda enabled: calls.append(('foreground', enabled))

    system_tray._show_replacer_config()

    assert calls == [('foreground', True), 'show', 'raise', 'activate']


def test_macos_menu_bar_activation_does_not_hide_dashboard(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    toggle_calls = []
    system_tray._toggle_dashboard = lambda: toggle_calls.append(True)
    monkeypatch.setattr(tray_module.sys, 'platform', 'darwin')

    system_tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert toggle_calls == []


def test_macos_dashboard_foreground_mode_reapplies_dock_icon(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    calls = []
    icon_path = Path('/tmp/fleasion-test-icon.ico')

    monkeypatch.setattr(tray_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(tray_module, 'get_icon_path', lambda: icon_path)
    monkeypatch.setattr(
        platform_macos,
        'set_application_foreground_mode',
        lambda enabled: calls.append(('foreground', enabled)) or True,
    )
    monkeypatch.setattr(
        platform_macos,
        'set_application_icon',
        lambda path: calls.append(('icon', path)) or True,
    )

    system_tray._set_dashboard_foreground_mode(True)

    assert calls == [('foreground', True), ('icon', icon_path)]


def test_windows_tray_activation_still_toggles_dashboard(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    toggle_calls = []
    system_tray._toggle_dashboard = lambda: toggle_calls.append(True)
    monkeypatch.setattr(tray_module.sys, 'platform', 'win32')

    system_tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert toggle_calls == [True]


def test_cleanup_tray_icon_hides_and_deletes_once(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    tray_icon = _TrayIconStub()
    process_events_calls = []
    system_tray.tray = tray_icon
    system_tray._tray_cleaned_up = False
    monkeypatch.setattr(tray_module.QApplication, 'processEvents', lambda: process_events_calls.append(True))

    system_tray.cleanup_tray_icon()
    system_tray.cleanup_tray_icon()

    assert tray_icon.hide_calls == 1
    assert tray_icon.context_menus == [None]
    assert tray_icon.delete_later_calls == 1
    assert process_events_calls == [True]


def test_exit_app_cleans_tray_before_quitting(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    calls = []

    class _AppStub:
        def quit(self):
            calls.append('quit')

    class _ProxyMasterStub:
        def stop(self):
            calls.append('stop')

    system_tray.app = _AppStub()
    system_tray.proxy_master = _ProxyMasterStub()
    system_tray.cleanup_tray_icon = lambda: calls.append('cleanup')
    monkeypatch.setattr(tray_module, 'run_in_thread', lambda func: func)

    system_tray._exit_app()

    assert system_tray._exiting is True
    assert calls[0] == 'cleanup'
    assert calls[-1] == 'quit'


def test_linux_helper_start_failure_disables_proxy_features():
    config = SimpleNamespace(proxy_features_enabled=True)
    calls = []

    def set_proxy_features_enabled(enabled):
        calls.append(enabled)
        config.proxy_features_enabled = enabled

    tray = SimpleNamespace(set_proxy_features_enabled=set_proxy_features_enabled)

    app_module._disable_proxy_features_after_start_failure(
        config,
        tray,
        'Linux Polkit approval was denied or the proxy helper could not start',
    )

    assert calls == [False]
    assert config.proxy_features_enabled is False
