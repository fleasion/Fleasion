import os
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from fleasion.app.dialogs import proxy as dialogs_proxy_module
from fleasion.app import qt_runtime as qt_runtime_module
from fleasion.app import tray as tray_module
from fleasion.app.tray import SystemTray
from fleasion.utils import platform_macos


def _toggle_dashboard(tray: SystemTray) -> None:
    callback = cast('Callable[[SystemTray], None]', SystemTray.__dict__['_toggle_dashboard'])
    callback(tray)


def _show_replacer_config(tray: SystemTray) -> None:
    tray.show_replacer_config()


def _set_dashboard_foreground_mode(tray: SystemTray, enabled: bool) -> None:
    callback = cast(
        'Callable[[SystemTray, bool], None]',
        SystemTray.__dict__['_set_dashboard_foreground_mode'],
    )
    callback(tray, enabled)


def _show_logs(tray: SystemTray) -> None:
    callback = cast('Callable[[SystemTray], None]', SystemTray.__dict__['_show_logs'])
    callback(tray)


def _on_tray_activated(tray: SystemTray, reason: QSystemTrayIcon.ActivationReason) -> None:
    callback = cast(
        'Callable[[SystemTray, QSystemTrayIcon.ActivationReason], None]',
        SystemTray.__dict__['_on_tray_activated'],
    )
    callback(tray, reason)


def _exit_app(tray: SystemTray) -> None:
    tray.exit_app()


def _ensure_exit_action_enabled(tray: SystemTray) -> None:
    callback = cast(
        'Callable[[SystemTray], None]',
        SystemTray.__dict__['_ensure_exit_action_enabled'],
    )
    callback(tray)


def _check_linux_gui_dependencies() -> bool:
    callback = cast('Callable[[], bool]', qt_runtime_module.__dict__['check_linux_gui_dependencies'])
    return callback()


def _is_xfce_desktop() -> bool:
    callback = cast('Callable[[], bool]', tray_module.__dict__['_is_xfce_desktop'])
    return callback()


def _xfce_notification(title: str, message: str, icon: QIcon, dark: bool, timeout: int) -> QWidget:
    factory = cast(
        'Callable[[str, str, QIcon, bool, int], QWidget]',
        tray_module.__dict__['_XfceTrayNotification'],
    )
    return factory(title, message, icon, dark, timeout)


def _disable_proxy_features_after_start_failure(
    config: object, tray: object | None, reason: str
) -> None:
    callback = cast(
        'Callable[[object, object | None, str], None]',
        dialogs_proxy_module.__dict__['disable_proxy_features_after_start_failure'],
    )
    callback(config, tray, reason)


def _noop() -> None:
    return None


def _noop_window(_window: QWidget) -> None:
    return None


def _foreground_recorder(values: list[bool]) -> Callable[[bool], None]:
    def record(enabled: bool) -> None:
        values.append(enabled)

    return record


def _toggle_recorder(values: list[bool]) -> Callable[[], None]:
    def record() -> None:
        values.append(True)

    return record


def _inline_call[**P, R](func: Callable[P, R]) -> Callable[P, R]:
    return func


class _DashboardStub:
    def __init__(self, visible: bool, events: list[object] | None = None) -> None:
        self.visible = visible
        self.hide_calls = 0
        self.events = events

    def isVisible(self) -> bool:
        return self.visible

    def hide(self) -> None:
        self.visible = False
        self.hide_calls += 1

    def show(self) -> None:
        if self.events is not None:
            self.events.append('show')

    def raise_(self) -> None:
        if self.events is not None:
            self.events.append('raise')

    def activateWindow(self) -> None:
        if self.events is not None:
            self.events.append('activate')


class _TrayIconStub:
    def __init__(self) -> None:
        self.hide_calls = 0
        self.context_menus: list[QMenu | None] = []
        self.delete_later_calls = 0

    def hide(self) -> None:
        self.hide_calls += 1

    def setContextMenu(self, menu: QMenu | None) -> None:
        self.context_menus.append(menu)

    def deleteLater(self) -> None:
        self.delete_later_calls += 1


def test_linux_gui_dependency_check_reports_install_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils import platform_linux

    critical_calls: list[tuple[object, ...]] = []
    log_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(qt_runtime_module.sys, 'platform', 'linux')

    def missing_packages() -> list[str]:
        return ['qt6-base']

    def critical(*args: object) -> None:
        critical_calls.append(args)

    def log(category: str, message: str) -> None:
        log_calls.append((category, message))

    monkeypatch.setattr(platform_linux, 'missing_linux_gui_packages', missing_packages)
    monkeypatch.setattr(qt_runtime_module.QMessageBox, 'critical', critical)
    monkeypatch.setattr(qt_runtime_module.log_buffer, 'log', log)

    assert _check_linux_gui_dependencies() is False
    critical_message = cast('str', critical_calls[0][2])
    assert 'sudo pacman -S --needed qt6-base' in critical_message
    assert 'Required package:\n  • qt6-base' in critical_message
    assert log_calls == [
        (
            'Linux GUI',
            (
                'A required Arch Linux GUI package is missing.\n'
                '  Package: qt6-base\n'
                '  Impact: Fleasion cannot reliably publish its system tray icon.\n'
                '  Install: sudo pacman -S --needed qt6-base'
            ),
        )
    ]


def test_linux_gui_dependency_check_accepts_complete_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.utils import platform_linux

    monkeypatch.setattr(qt_runtime_module.sys, 'platform', 'linux')
    monkeypatch.setattr(platform_linux, 'missing_linux_gui_packages', list[str])

    assert _check_linux_gui_dependencies() is True


def test_dashboard_toggle_hides_visible_window() -> None:
    system_tray = SystemTray.__new__(SystemTray)
    dashboard = _DashboardStub(visible=True)
    foreground_modes: list[bool] = []
    system_tray.__dict__['dashboard_window'] = dashboard
    system_tray.__dict__['show_replacer_config'] = _noop
    system_tray.__dict__['_set_dashboard_foreground_mode'] = _foreground_recorder(foreground_modes)

    _toggle_dashboard(system_tray)

    assert dashboard.hide_calls == 1
    assert foreground_modes == [False]


def test_xfce_desktop_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('XDG_CURRENT_DESKTOP', raising=False)
    monkeypatch.delenv('XDG_SESSION_DESKTOP', raising=False)
    monkeypatch.setenv('DESKTOP_SESSION', 'xfce')

    assert _is_xfce_desktop() is True

    monkeypatch.setenv('DESKTOP_SESSION', 'gnome')
    assert _is_xfce_desktop() is False


def test_xfce_notification_uses_an_opaque_surface() -> None:
    app = QApplication.instance() or QApplication([])
    notification = _xfce_notification(
        'Fleasion',
        'Fleasion is still running in the system tray.',
        QIcon(),
        True,
        1000,
    )

    assert not notification.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert notification.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
    assert notification.autoFillBackground()
    assert notification.palette().color(QPalette.ColorRole.Window).alpha() == 255

    notification.close()
    app.processEvents()


def test_dashboard_close_uses_styled_notification_on_xfce(monkeypatch: pytest.MonkeyPatch) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    calls: list[tuple[str, tuple[object, ...]]] = []

    class _TrayStub:
        def showMessage(self, *args: object) -> None:
            calls.append(('native', args))

    system_tray.__dict__['_dashboard_close_notice_shown'] = False
    system_tray.__dict__['tray'] = _TrayStub()

    def show_xfce(title: str, message: str, icon_path: Path | None) -> bool:
        calls.append(('xfce', (title, message, icon_path)))
        return True

    system_tray.__dict__['_show_xfce_notification'] = show_xfce
    monkeypatch.setattr(tray_module.sys, 'platform', 'linux')
    monkeypatch.setattr(tray_module, '_is_xfce_desktop', lambda: True)
    monkeypatch.setattr(tray_module, 'get_icon_path', lambda: None)

    system_tray.notify_dashboard_closed()

    assert [call[0] for call in calls] == ['xfce']


def test_dashboard_toggle_shows_hidden_window() -> None:
    system_tray = SystemTray.__new__(SystemTray)
    dashboard = _DashboardStub(visible=False)
    show_calls: list[bool] = []
    system_tray.__dict__['dashboard_window'] = dashboard
    system_tray.__dict__['show_replacer_config'] = _toggle_recorder(show_calls)

    _toggle_dashboard(system_tray)

    assert show_calls == [True]


def test_show_logs_raises_and_activates_new_window(monkeypatch: pytest.MonkeyPatch) -> None:
    app = QApplication.instance() or QApplication([])
    calls: list[str] = []

    class _RecordingLogsWindow(tray_module.LogsWindow):
        def show(self) -> None:
            calls.append('show')
            super().show()

        def raise_(self) -> None:
            calls.append('raise')
            super().raise_()

        def activateWindow(self) -> None:
            calls.append('activate')
            super().activateWindow()

    system_tray = SystemTray.__new__(SystemTray)
    system_tray.open_windows = []
    system_tray.__dict__['_apply_always_on_top_to_window'] = _noop_window
    monkeypatch.setattr(tray_module, 'LogsWindow', _RecordingLogsWindow)

    _show_logs(system_tray)
    app.processEvents()

    assert calls[:3] == ['show', 'raise', 'activate']
    assert len(system_tray.open_windows) == 1
    assert system_tray.open_windows[0].isVisible()

    system_tray.open_windows[0].close()
    app.processEvents()
    assert system_tray.open_windows == []


def test_show_dashboard_enables_foreground_mode_before_showing_existing_window() -> None:
    system_tray = SystemTray.__new__(SystemTray)
    calls: list[object] = []
    dashboard = _DashboardStub(visible=False, events=calls)
    system_tray.__dict__['dashboard_window'] = dashboard

    def foreground(enabled: bool) -> None:
        calls.append(('foreground', enabled))

    system_tray.__dict__['_set_dashboard_foreground_mode'] = foreground

    _show_replacer_config(system_tray)

    assert calls == [('foreground', True), 'show', 'raise', 'activate']


def test_macos_menu_bar_activation_does_not_hide_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    toggle_calls: list[bool] = []
    system_tray.__dict__['_toggle_dashboard'] = _toggle_recorder(toggle_calls)
    monkeypatch.setattr(tray_module.sys, 'platform', 'darwin')

    _on_tray_activated(system_tray, QSystemTrayIcon.ActivationReason.Trigger)

    assert toggle_calls == []


def test_macos_dashboard_foreground_mode_reapplies_dock_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    calls: list[tuple[str, object]] = []
    icon_path = Path('/tmp/fleasion-test-icon.ico')

    monkeypatch.setattr(tray_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(tray_module, 'get_icon_path', lambda: icon_path)

    def set_foreground(enabled: bool) -> bool:
        calls.append(('foreground', enabled))
        return True

    def set_icon(path: Path) -> bool:
        calls.append(('icon', path))
        return True

    monkeypatch.setattr(platform_macos, 'set_application_foreground_mode', set_foreground)
    monkeypatch.setattr(platform_macos, 'set_application_icon', set_icon)

    _set_dashboard_foreground_mode(system_tray, True)

    assert calls == [('foreground', True), ('icon', icon_path)]


def test_windows_tray_activation_still_toggles_dashboard(monkeypatch: pytest.MonkeyPatch) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    toggle_calls: list[bool] = []
    system_tray.__dict__['_toggle_dashboard'] = _toggle_recorder(toggle_calls)
    monkeypatch.setattr(tray_module.sys, 'platform', 'win32')

    _on_tray_activated(system_tray, QSystemTrayIcon.ActivationReason.Trigger)

    assert toggle_calls == [True]


def test_cleanup_tray_icon_hides_and_deletes_once(monkeypatch: pytest.MonkeyPatch) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    tray_icon = _TrayIconStub()
    process_events_calls: list[bool] = []
    system_tray.__dict__['tray'] = tray_icon
    system_tray.__dict__['_tray_cleaned_up'] = False
    monkeypatch.setattr(
        tray_module.QApplication, 'processEvents', _toggle_recorder(process_events_calls)
    )

    system_tray.cleanup_tray_icon()
    system_tray.cleanup_tray_icon()

    assert tray_icon.hide_calls == 1
    assert tray_icon.context_menus == [None]
    assert tray_icon.delete_later_calls == 1
    assert process_events_calls == [True]


def test_exit_app_cleans_tray_before_quitting(monkeypatch: pytest.MonkeyPatch) -> None:
    system_tray = SystemTray.__new__(SystemTray)
    calls: list[str] = []

    class _AppStub:
        def quit(self) -> None:
            calls.append('quit')

    class _ProxyMasterStub:
        def stop(self) -> None:
            calls.append('stop')

    system_tray.__dict__['app'] = _AppStub()
    system_tray.__dict__['proxy_master'] = _ProxyMasterStub()
    system_tray.cleanup_tray_icon = lambda: calls.append('cleanup')
    monkeypatch.setattr(tray_module, 'run_in_thread', _inline_call)

    _exit_app(system_tray)

    assert system_tray.__dict__['_exiting'] is True
    assert calls[0] == 'cleanup'
    assert calls[-1] == 'quit'


def test_ensure_exit_action_keeps_exit_enabled() -> None:
    system_tray = SystemTray.__new__(SystemTray)
    calls: list[bool] = []

    class _ActionStub:
        def setEnabled(self, enabled: bool) -> None:
            calls.append(enabled)

    system_tray.__dict__['exit_action'] = _ActionStub()

    _ensure_exit_action_enabled(system_tray)

    assert calls == [True]


def test_linux_helper_start_failure_keeps_proxy_features_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(proxy_features_enabled=True)
    calls: list[str] = []
    warnings: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def update_status() -> None:
        calls.append('update_status')

    tray = SimpleNamespace(update_status=update_status)
    monkeypatch.setattr(dialogs_proxy_module.sys, 'platform', 'linux')

    def warning(*args: object, **kwargs: object) -> None:
        warnings.append((args, kwargs))

    monkeypatch.setattr(dialogs_proxy_module.QMessageBox, 'warning', warning)

    _disable_proxy_features_after_start_failure(
        config,
        tray,
        'Linux Polkit approval was denied or the proxy helper could not start',
    )

    assert calls == ['update_status']
    assert warnings
    assert config.proxy_features_enabled is True
