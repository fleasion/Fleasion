"""Serialized GNOME blur toggles and accurate Glass/GUI status regressions."""

from __future__ import annotations

import os
from pathlib import Path
from threading import Event, current_thread, main_thread
from time import perf_counter
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from fleasion.config import manager
from fleasion.gui import glass
from fleasion.gui.conflicts import ConflictBadge
from fleasion.gui.glass import chrome, controller, desktop_blur, state
from fleasion.localization import tr

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


class FakeDesktop:
    def __init__(self) -> None:
        self.values = {'blur': 'true', 'enable-all': 'false', 'whitelist': "['Firefox']"}
        self.read_started = Event()
        self.read_release = Event()
        self.stall = False
        self.writes: list[str] = []

    def run(self, *args: str) -> str | None:
        if args[0] == 'gnome-extensions':
            if self.stall and current_thread() is not main_thread():
                self.read_started.set()
                assert self.read_release.wait(timeout=4)
            return 'State: ACTIVE'
        command = args[1]
        key = args[2].rsplit('/', 1)[-1]
        if command == 'read':
            return self.values.get(key, '')
        if command == 'write':
            self.values[key] = args[3]
            self.writes.append(args[3])
        elif command == 'reset':
            self.values.pop(key, None)
        return ''


def prepare_blur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, desktop: FakeDesktop
) -> desktop_blur.DesktopBlur:
    monkeypatch.setattr(desktop_blur, '_run', desktop.run)
    monkeypatch.setattr(desktop_blur.sys, 'platform', 'linux')
    monkeypatch.setattr(desktop_blur, '_BACKUP_FILE', tmp_path / 'backup.json')
    monkeypatch.setattr(desktop_blur, 'CONFIG_DIR', tmp_path)
    blur = desktop_blur.DesktopBlur()
    assert blur.status(refresh=True) == 'off'
    assert blur.can_toggle()
    return blur


def test_glass_toggle_does_not_block_gui_and_disables_control(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = FakeDesktop()
    blur = prepare_blur(tmp_path, monkeypatch, desktop)
    monkeypatch.setattr(controller, 'blur', blur)
    monkeypatch.setattr(glass, 'blur', blur)
    monkeypatch.setattr(state.state.settings, 'backdrop', 'desktop')
    button = QPushButton()
    button.setObjectName(controller.BLUR_BUTTON)
    button.show()
    try:
        controller.GlassTheme._sync_blur_controls()  # pyright: ignore[reportPrivateUsage]
        assert button.isEnabled()
        desktop.stall = True
        started = perf_counter()
        controller.GlassTheme.toggle_desktop_blur()
        assert perf_counter() - started < 0.2
        assert desktop.read_started.wait(timeout=3)
        assert blur.toggle_pending
        assert not button.isEnabled()
        assert blur.toggle() is False
        assert not desktop.writes
        assert blur.poll_refresh() is False
        desktop.read_release.set()
        pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        assert pending.result(timeout=4) == ('on', True)
        assert blur.poll_refresh()
        controller.GlassTheme._sync_blur_controls()  # pyright: ignore[reportPrivateUsage]
        assert not blur.toggle_pending
        assert button.isEnabled()
        assert button.text() == tr('settings.appearance.glass_blur_on')
        assert desktop.values['whitelist'] == "['Firefox', 'fleasion']"
        assert len(desktop.writes) == 1
    finally:
        desktop.read_release.set()
        blur.stop_polling()
        button.close()
        button.deleteLater()
        app.processEvents()


def test_stale_probe_does_not_override_new_toggle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = FakeDesktop()
    blur = prepare_blur(tmp_path, monkeypatch, desktop)
    try:
        desktop.stall = True
        assert not blur.poll_refresh()
        assert desktop.read_started.wait(timeout=3)
        assert blur.toggle()
        assert not blur.poll_refresh()
        desktop.read_release.set()
        pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        assert pending.result(timeout=4)[0] == 'on'
        assert blur.poll_refresh()
        assert blur.cached_status() == 'on'
        assert blur.can_toggle()
        assert not blur.toggle_pending
        assert not blur.poll_refresh()
        assert blur.cached_status() == 'on'
    finally:
        desktop.read_release.set()
        blur.stop_polling()


def test_disabling_theme_mid_toggle_does_not_start_competing_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    desktop = FakeDesktop()
    blur = prepare_blur(tmp_path, monkeypatch, desktop)
    try:
        desktop.stall = True
        assert blur.toggle()
        assert desktop.read_started.wait(timeout=3)
        blur.stop_polling()
        assert blur.toggle_pending
        assert not blur.toggle()
        desktop.read_release.set()
        pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        assert pending.result(timeout=4)[0] == 'on'
        assert blur.poll_refresh()
        assert blur.cached_status() == 'on'
        assert not blur.toggle_pending
        assert blur.toggle()
        second = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
        assert second is not None
        assert second.result(timeout=4)[0] == 'off'
        assert blur.poll_refresh()
        assert blur.cached_status() == 'off'
        assert desktop.values['whitelist'] == "['Firefox']"
    finally:
        desktop.read_release.set()
        blur.stop_polling()


def test_failed_toggle_clears_pending_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    blur = prepare_blur(tmp_path, monkeypatch, FakeDesktop())

    def fail() -> tuple[desktop_blur.BlurStatus, bool]:
        raise OSError('simulated compositor failure')

    monkeypatch.setattr(blur, '_toggle_work', fail)
    try:
        assert blur.toggle()
        pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        with pytest.raises(OSError, match='simulated compositor failure'):
            pending.result(timeout=3)
        assert blur.poll_refresh()
        assert not blur.toggle_pending
        assert blur.can_toggle()
    finally:
        blur.stop_polling()


def test_glass_titlebar_maximizes_only_on_left_doubleclick(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = QWidget()
    titlebar = chrome.GlassTitleBar(window)
    actions: list[str] = []

    def record_maximize(action: chrome.WindowButton) -> None:
        actions.append(action)

    monkeypatch.setattr(titlebar, '_press', record_maximize)
    try:
        for button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            QTest.mouseDClick(titlebar, button)
        assert actions == ['max']
    finally:
        window.deleteLater()
        app.processEvents()


@pytest.mark.parametrize(
    ('rules', 'expected_key', 'counts'),
    [
        ([('a', 1001), ('b', 1001)], 'badge_count', (1, 0)),
        ([('a', 'Mesh'), ('b', 1001)], 'badge_potential', (0, 1)),
        ([('a', 1001), ('b', 1001), ('c', 'Mesh')], 'badge_mixed', (1, 1)),
    ],
)
def test_conflict_footer_reports_exact_and_potential_separately(
    app: QApplication,
    config: manager.ConfigManager,
    rules: list[tuple[str, str | int]],
    expected_key: str,
    counts: tuple[int, int],
) -> None:
    for name, asset in rules:
        assert config.create_config(name)
        config.set_replacement_rules(name, [{'replace_ids': [asset], 'with_id': 2010}])
        config.set_config_enabled(name, True)
    badge = ConflictBadge(config)
    try:
        assert sum(not c.potential for c in badge.conflicts) == counts[0]
        assert sum(c.potential for c in badge.conflicts) == counts[1]
        assert badge.text() == tr(f'ui.gui.conflicts.{expected_key}', value0=counts[0] or counts[1], value1=counts[1])
    finally:
        badge.deleteLater()
        app.processEvents()
