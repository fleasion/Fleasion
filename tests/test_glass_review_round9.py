"""Regression coverage for composed Glass QSS and owned dynamic button properties."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton
from shiboken6 import isValid

from fleasion.gui.glass import controller, surface
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


_BASE_QSS = 'QDialogButtonBox, QMessageBox { button-layout: 0; }'


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    previous_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    previous_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    previous_stylesheet = application.styleSheet()
    ThemeManager.apply_theme('Light')
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(previous_theme)
        ThemeManager._effective_theme = previous_effective  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(previous_stylesheet)
        application.processEvents()


def test_glass_keeps_startup_button_layout_policy_while_active(app: QApplication) -> None:
    app.setStyleSheet(_BASE_QSS)
    ThemeManager.apply_theme('Glass')
    assert controller.theme.active
    composed = app.styleSheet()
    assert composed.startswith(_BASE_QSS + '\n')
    assert 'QPushButton[glassArrow=' in composed
    assert composed.count(_BASE_QSS) == 1
    controller.theme.enable(app)
    assert app.styleSheet().count(_BASE_QSS) == 1
    ThemeManager.apply_theme('System')
    assert app.styleSheet() == _BASE_QSS
    ThemeManager.apply_theme('Glass')
    assert app.styleSheet().count(_BASE_QSS) == 1
    ThemeManager.apply_theme('Dark')
    assert app.styleSheet() == _BASE_QSS


def test_glass_preserves_preexisting_app_qss_after_repeated_enable(app: QApplication) -> None:
    original = _BASE_QSS + '\nQLabel#PersistentLabel { color: red; }'
    app.setStyleSheet(original)
    for _ in range(3):
        ThemeManager.apply_theme('Glass')
        assert app.styleSheet().startswith(original + '\n')
        controller.theme.enable(app)
        assert app.styleSheet().count(original) == 1
        ThemeManager.apply_theme('Light')
        assert app.styleSheet() == original


def test_glass_releases_properties_and_reclassifies_resized_button(app: QApplication) -> None:
    button = QPushButton('›')
    button.setFlat(True)
    button.setMaximumWidth(20)
    try:
        ThemeManager.apply_theme('Glass')
        assert button.property('glassArrow') is True
        assert button.property('compact') is None
        ThemeManager.apply_theme('Light')
        assert button.property('glassArrow') is None
        assert button.property('compact') is None
        button.setFlat(False)
        button.setMaximumWidth(300)
        ThemeManager.apply_theme('Glass')
        assert button.property('glassArrow') is None
        assert button.property('compact') is None
        ThemeManager.apply_theme('Light')
    finally:
        ThemeManager.apply_theme('Light')
        button.deleteLater()
        app.processEvents()


def test_glass_restores_compact_and_does_not_modify_existing_properties(app: QApplication) -> None:
    compact = QPushButton('…')
    compact.setMaximumWidth(36)
    custom = QPushButton('custom')
    custom.setFlat(True)
    custom.setMaximumWidth(20)
    custom.setProperty('glassArrow', False)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
    custom.setProperty('compact', 'user-managed')
    try:
        ThemeManager.apply_theme('Glass')
        assert compact.property('compact') is True
        assert custom.property('glassArrow') is False
        assert custom.property('compact') == 'user-managed'
        ThemeManager.apply_theme('Light')
        assert compact.property('compact') is None
        assert custom.property('glassArrow') is False
        assert custom.property('compact') == 'user-managed'
    finally:
        ThemeManager.apply_theme('Light')
        compact.deleteLater()
        custom.deleteLater()
        app.processEvents()


def test_late_created_or_destroyed_buttons_restore_safely(app: QApplication) -> None:
    windows = surface.GlassWindows(app)
    late = QPushButton('late')
    late.setFlat(True)
    late.setMaximumWidth(18)
    dead = QPushButton('dead')
    dead.setMaximumWidth(28)
    try:
        windows.polish(late)
        windows.polish(dead)
        assert late.property('glassArrow') is True
        assert dead.property('compact') is True
        dead.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dead)
        windows.restore()
        assert late.property('glassArrow') is None
    finally:
        windows.restore()
        late.deleteLater()
        app.processEvents()
