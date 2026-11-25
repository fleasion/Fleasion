"""Text tones resolve against the current theme and follow theme changes."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from fleasion.gui.theme import ThemeManager
from fleasion.widgets.text import style_text, tone_color

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    try:
        yield application
    finally:
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')


def _label_in_window() -> tuple[QWidget, QLabel]:
    window = QWidget()
    label = QLabel('status', window)
    QVBoxLayout(window).addWidget(label)
    return window, label


def test_status_tone_follows_light_and_dark_themes(app: QApplication) -> None:
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    window, label = _label_in_window()
    style_text(label, tone='error')
    dark_error = label.palette().windowText().color()
    assert dark_error == tone_color('error', window.palette())

    ThemeManager.apply_appearance('Fusion', 'Light', 'Ice')
    app.processEvents()
    light_error = label.palette().windowText().color()
    assert light_error == tone_color('error', window.palette())
    assert light_error != dark_error

    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    app.processEvents()
    assert label.palette().windowText().color() == dark_error
    window.deleteLater()


def test_muted_tone_uses_the_placeholder_role(app: QApplication) -> None:
    ThemeManager.apply_appearance('Fusion', 'Light', 'Ice')
    window, label = _label_in_window()
    style_text(label, tone='error')
    style_text(label, tone='muted')

    assert label.foregroundRole() == QPalette.ColorRole.PlaceholderText
    # The earlier status color is gone again
    assert label.palette().windowText().color() == window.palette().windowText().color()
    window.deleteLater()


def test_clearing_a_tone_restores_the_text_color(app: QApplication) -> None:
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    window, label = _label_in_window()
    style_text(label, tone='warning')
    style_text(label, tone=None)

    assert label.foregroundRole() == QPalette.ColorRole.WindowText
    assert label.palette().windowText().color() == window.palette().windowText().color()

    # The cleared label keeps following the theme instead of the color it had then
    before = window.palette().windowText().color()
    ThemeManager.apply_appearance('Fusion', 'Light', 'Ice')
    app.processEvents()
    assert window.palette().windowText().color() != before
    assert label.palette().windowText().color() == window.palette().windowText().color()
    window.deleteLater()


def test_font_changes_pin_only_the_attributes_given(app: QApplication) -> None:
    window, label = _label_in_window()
    family = QFont(window.font())
    family.setFamily('Monospace')
    window.setFont(family)

    style_text(label, point_size=8, bold=True)

    assert label.font().pointSizeF() == 8
    assert label.font().bold()
    assert label.font().family() == window.font().family()
    window.deleteLater()
