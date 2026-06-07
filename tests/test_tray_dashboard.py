from PyQt6.QtWidgets import QSystemTrayIcon

from Fleasion import tray as tray_module
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


def test_windows_tray_activation_still_toggles_dashboard(monkeypatch):
    system_tray = SystemTray.__new__(SystemTray)
    toggle_calls = []
    system_tray._toggle_dashboard = lambda: toggle_calls.append(True)
    monkeypatch.setattr(tray_module.sys, 'platform', 'win32')

    system_tray._on_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert toggle_calls == [True]
