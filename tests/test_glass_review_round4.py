"""Regression tests for Glass settings layout, refresh, and Qt widget lifetimes."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)
from shiboken6 import isValid

from fleasion.config import manager
from fleasion.gui.glass import chrome, surface
from fleasion.gui.settings_tab import SettingsTab

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


@pytest.mark.parametrize('width', [680, 720, 740, 780])
def test_appearance_glass_controls_do_not_force_horizontal_scrolling(
    app: QApplication,
    config: manager.ConfigManager,
    monkeypatch: pytest.MonkeyPatch,
    width: int,
) -> None:
    from fleasion.gui.glass import blur

    config.theme = 'Glass'
    config.glass_backdrop = 'desktop'
    monkeypatch.setattr(blur, '_status', 'on')
    monkeypatch.setattr(blur, '_can_toggle', True)
    tab = SettingsTab(config)
    try:
        tab.resize(width, 700)
        tab.show()
        app.processEvents()
        scroll = tab.findChild(QScrollArea)
        assert scroll is not None
        assert tab._blur_button.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert scroll.horizontalScrollBar().maximum() == 0
        assert tab._backdrop_combo.isVisible()  # pyright: ignore[reportPrivateUsage]
        assert tab._accent_selector.isVisible()  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.close()
        tab.deleteLater()
        app.processEvents()


def test_refresh_from_config_synchronizes_all_glass_values_without_signals(
    app: QApplication, config: manager.ConfigManager
) -> None:
    config.theme = 'Glass'
    tab = SettingsTab(config)
    try:
        changes: list[str] = []

        def on_backdrop(_index: int) -> None:
            changes.append('backdrop')

        def on_accent(_text: str) -> None:
            changes.append('accent')

        def on_opacity(_value: int) -> None:
            changes.append('opacity')

        tab._backdrop_combo.currentIndexChanged.connect(on_backdrop)  # pyright: ignore[reportPrivateUsage]
        tab._accent_selector.accent_changed.connect(on_accent)  # pyright: ignore[reportPrivateUsage]
        tab._opacity_slider.valueChanged.connect(on_opacity)  # pyright: ignore[reportPrivateUsage]
        config.glass_accent = 'Violet'
        config.glass_backdrop = 'wallpaper'
        config.glass_opacity = 0.8
        tab.refresh_from_config()
        assert tab._accent_selector.accent() == 'Violet'  # pyright: ignore[reportPrivateUsage]
        assert tab._backdrop_combo.currentData() == 'wallpaper'  # pyright: ignore[reportPrivateUsage]
        assert tab._opacity_slider.value() == 80  # pyright: ignore[reportPrivateUsage]
        assert changes == []
        assert config.glass_accent == 'Violet'
        assert config.glass_backdrop == 'wallpaper'
        assert config.glass_opacity == pytest.approx(0.8)
    finally:
        tab.deleteLater()
        app.processEvents()


def test_deferred_repolish_ignores_destroyed_widget(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors: list[object] = []

    def record_unraisable(details: object) -> None:
        errors.append(details)

    monkeypatch.setattr(sys, 'unraisablehook', record_unraisable)
    widget = QPushButton()
    surface._repolish_later(widget)  # pyright: ignore[reportPrivateUsage]
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(widget)
    app.processEvents()
    assert not errors


@pytest.mark.parametrize('margins', [(17, 19, 23, 29), (0, 0, 0, 0)])
def test_glass_chrome_restores_registered_window_layout_margins(
    app: QApplication, margins: tuple[int, int, int, int]
) -> None:
    window = QDialog()
    layout = QVBoxLayout(window)
    layout.setContentsMargins(*margins)
    chrome.register(window)
    try:
        for _ in range(2):
            chrome.apply_chrome(window, enabled=True)
            assert layout.contentsMargins().left() == 4
            chrome.apply_chrome(window, enabled=False)
            restored = layout.contentsMargins()
            assert (restored.left(), restored.top(), restored.right(), restored.bottom()) == margins
    finally:
        chrome.apply_chrome(window, enabled=False)
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
