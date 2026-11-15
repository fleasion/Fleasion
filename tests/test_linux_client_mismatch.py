"""Linux client mismatch warnings are scoped, nonblocking and actionable."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from collections.abc import Callable

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QMessageBox

from fleasion.app import linux_client_mismatch
from fleasion.config.manager import ConfigManager
from fleasion.proxy.master import ProxyMaster
from fleasion.app.tray import SystemTray
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    MOCKTAIL_CLIENT,
    ROSEMARY_CLIENT,
    SOBER_CLIENT,
    LinuxClientInstallation,
)


@pytest.fixture
def app() -> QApplication:
    existing = QApplication.instance()
    return cast('QApplication', existing or QApplication([]))


def _installation(client: object, tmp_path: Path) -> LinuxClientInstallation:
    from fleasion.utils.linux_clients import LinuxClientDescriptor

    descriptor = cast('LinuxClientDescriptor', client)
    return LinuxClientInstallation(
        client=descriptor,
        paths=descriptor.paths(home=tmp_path, environ={}),
        executable=None,
    )


def test_mismatch_detects_only_one_other_running_player(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = tuple(
        _installation(descriptor, tmp_path)
        for descriptor in (SOBER_CLIENT, MOCKTAIL_CLIENT, ROSEMARY_CLIENT)
    )
    running: set[str] = {'rosemary'}
    monkeypatch.setattr(platform_linux, 'linux_client_installations', lambda: installed)

    def is_running(installation: LinuxClientInstallation) -> bool:
        return installation.key in running

    monkeypatch.setattr(platform_linux, 'is_roblox_running', is_running)
    detect = platform_linux.detect_running_linux_client_mismatch
    assert detect('sober') == ('sober', 'rosemary')
    assert detect('mocktail') == ('mocktail', 'rosemary')
    assert detect('auto') is None
    assert detect('rosemary') is None
    assert detect('unknown') is None

    running.add('sober')
    assert detect('sober') is None  # Multiple clients may legitimately run concurrently
    running.remove('sober')
    running.add('mocktail')
    assert detect('sober') is None  # Don't pick an arbitrary other client
    running.clear()
    assert detect('sober') is None


def test_mismatch_warns_if_configured_client_is_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = (_installation(ROSEMARY_CLIENT, tmp_path),)
    monkeypatch.setattr(platform_linux, 'linux_client_installations', lambda: installed)
    def running(_installation: LinuxClientInstallation) -> bool:
        return True

    monkeypatch.setattr(platform_linux, 'is_roblox_running', running)
    assert platform_linux.detect_running_linux_client_mismatch('sober') == (
        'sober', 'rosemary'
    )


def test_watcher_warns_once_and_ignores_auto_or_disabled_proxy(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(linux_client='sober', proxy_features_enabled=True)
    proxy = SimpleNamespace(is_running=True)
    parent = QObject()
    watcher = linux_client_mismatch.LinuxClientMismatchWatcher(
        cast('ConfigManager', config), cast('ProxyMaster', proxy), parent
    )
    watcher._timer.stop()  # pyright: ignore[reportPrivateUsage]
    emitted: list[tuple[str, str]] = []
    def on_mismatch(expected: str, actual: str) -> None:
        emitted.append((expected, actual))

    watcher.mismatch_detected.connect(on_mismatch)
    scanned: list[str] = []

    def detect(selected: str) -> tuple[str, str] | None:
        scanned.append(selected)
        return (selected, 'rosemary') if selected == 'sober' else None

    # Execute the background callable synchronously to test signal/event behavior.
    def inline_thread[**P, R](callback: Callable[P, R]) -> Callable[P, R]:
        return callback

    monkeypatch.setattr(linux_client_mismatch, 'run_in_thread', inline_thread)
    monkeypatch.setattr(linux_client_mismatch, 'detect_running_linux_client_mismatch', detect)
    watcher.check()
    watcher.check()
    assert emitted == [('sober', 'rosemary')]
    assert scanned == ['sober', 'sober']

    config.linux_client = 'auto'
    watcher.check()
    proxy.is_running = False
    config.linux_client = 'sober'
    watcher.check()
    proxy.is_running = True
    config.proxy_features_enabled = False
    watcher.check()
    assert scanned == ['sober', 'sober']


def test_watcher_discards_stale_result_after_selection_change(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = SimpleNamespace(linux_client='sober', proxy_features_enabled=True)
    proxy = SimpleNamespace(is_running=True)
    parent = QObject()
    watcher = linux_client_mismatch.LinuxClientMismatchWatcher(
        cast('ConfigManager', config), cast('ProxyMaster', proxy), parent
    )
    watcher._timer.stop()  # pyright: ignore[reportPrivateUsage]
    seen: list[tuple[str, str]] = []
    def on_mismatch(expected: str, actual: str) -> None:
        seen.append((expected, actual))

    watcher.mismatch_detected.connect(on_mismatch)
    delayed: list[object] = []
    def hold_thread(callback: Callable[[], None]) -> Callable[[], None]:
        delayed.append(callback)
        return lambda: None

    monkeypatch.setattr(linux_client_mismatch, 'run_in_thread', hold_thread)
    watcher.check()
    assert len(delayed) == 1
    config.linux_client = 'auto'
    # Simulate a worker completing after the user changed their selection.
    watcher._scan_complete.emit('sober', 'sober', 'rosemary')  # pyright: ignore[reportPrivateUsage]
    assert not seen
    assert not watcher._scanning  # pyright: ignore[reportPrivateUsage]


def test_mismatch_popup_opens_settings_without_changing_client(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    tray = SystemTray.__new__(SystemTray)
    setattr(tray, '_exiting', False)
    setattr(tray, '_linux_client_mismatch_dialog', None)
    setattr(tray, 'dashboard_window', None)
    config = SimpleNamespace(linux_client='sober')
    setattr(tray, 'config_manager', config)
    opened: list[str] = []
    setattr(tray, '_open_linux_client_settings', lambda: opened.append('settings'))
    monkeypatch.setattr('fleasion.app.tray.get_icon_path', lambda: None)
    tray._show_linux_client_mismatch('sober', 'rosemary')  # pyright: ignore[reportPrivateUsage]
    popup = cast('QMessageBox', getattr(tray, '_linux_client_mismatch_dialog'))
    assert popup.isVisible()
    assert 'Sober' in popup.text()
    assert 'Rosemary' in popup.text()
    assert 'Auto' in popup.informativeText()
    assert config.linux_client == 'sober'

    # An already-open popup should not spawn another instance.
    tray._show_linux_client_mismatch('sober', 'rosemary')  # pyright: ignore[reportPrivateUsage]
    assert getattr(tray, '_linux_client_mismatch_dialog') is popup
    settings_button = next(
        button for button in popup.buttons()
        if popup.buttonRole(button) == QMessageBox.ButtonRole.ActionRole
    )
    settings_button.click()
    assert opened == ['settings']
    assert getattr(tray, '_linux_client_mismatch_dialog') is None
    assert config.linux_client == 'sober'
    app.processEvents()


def test_settings_tab_navigation_survives_deferred_dashboard_setup(
    app: QApplication,
) -> None:
    from fleasion.gui.replacer_config import ReplacerConfigWindow

    dashboard = ReplacerConfigWindow.__new__(ReplacerConfigWindow)
    setattr(dashboard, '_settings_tab', None)
    setattr(dashboard, '_pending_open_settings_tab', False)
    dashboard.open_settings_tab()
    assert getattr(dashboard, '_pending_open_settings_tab') is True

    chosen: list[int] = []

    class Tabs:
        def isTabEnabled(self, index: int) -> bool:
            return index == 6

        def setCurrentIndex(self, index: int) -> None:
            chosen.append(index)

    setattr(dashboard, '_tab_indices', {'settings': 6})
    setattr(dashboard, 'tab_widget', Tabs())
    setattr(dashboard, '_settings_tab', object())
    dashboard.open_settings_tab()
    assert chosen == [6]
    assert getattr(dashboard, '_pending_open_settings_tab') is False


def test_mismatch_settings_action_navigates_existing_dashboard() -> None:
    tray = SystemTray.__new__(SystemTray)
    events: list[str] = []
    setattr(tray, 'show_replacer_config', lambda: events.append('show_dashboard'))
    setattr(tray, 'dashboard_window', SimpleNamespace(
        open_settings_tab=lambda: events.append('show_settings')
    ))
    tray._open_linux_client_settings()  # pyright: ignore[reportPrivateUsage]
    assert events == ['show_dashboard', 'show_settings']
