"""Validate colors shared by Settings, Miscellaneous and Modifications panels."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

from fleasion.gui.modifications_tab import CollapsibleSection
from fleasion.gui.rando_stuff_tab import CollapsibleSection as MiscSection
from fleasion.gui.settings_tab import CollapsibleSection as SettingsSection
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication([])
    original_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    original_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    original_stylesheet = application.styleSheet()
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(original_theme)
        ThemeManager._effective_theme = original_effective  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(original_stylesheet)
        application.processEvents()


def test_settings_misc_modifications_use_the_same_section_renderer() -> None:
    assert SettingsSection is CollapsibleSection
    assert MiscSection is CollapsibleSection


def test_glass_sections_match_scraper_surface_and_restore_dark(app: QApplication) -> None:
    ThemeManager.apply_theme('Dark')
    dialog = QDialog()
    dialog.resize(500, 280)
    layout = QVBoxLayout(dialog)
    section = CollapsibleSection('Test section', expanded=True)
    layout.addWidget(section)
    try:
        dialog.show()
        QTest.qWait(30)
        dark_colors = ThemeManager.panel_colors()
        dark_pixel = section.grab().toImage().pixelColor(30, 31)
        assert dark_colors.section_background.red() == dark_colors.section_background.blue()
        assert dark_pixel.alpha() == 255

        ThemeManager.apply_theme('Glass')
        QTest.qWait(40)
        glass_colors = ThemeManager.panel_colors()
        assert glass_colors.section_background == QColor(8, 9, 14, 77)
        assert glass_colors.section_background.alpha() < 255
        assert glass_colors.section_border == QColor(255, 255, 255, 20)
        assert glass_colors.container_background_css == 'background-color: transparent;'
        glass_pixel = section.grab().toImage().pixelColor(30, 31)
        assert glass_pixel.blue() > glass_pixel.red()
        assert glass_pixel.red() < dark_pixel.red()
        assert glass_pixel.alpha() < 255

        ThemeManager.apply_theme('Dark')
        QTest.qWait(35)
        after = section.grab().toImage().pixelColor(30, 31)
        assert after.alpha() == 255
        assert after.red() == after.blue()
        assert ThemeManager.panel_colors().container_background_css.startswith('background-color: rgb(')
    finally:
        ThemeManager.apply_theme('Dark')
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_glass_palette_restores_normal_window_color(app: QApplication) -> None:
    ThemeManager.apply_theme('Glass')
    assert app.palette().color(QPalette.ColorRole.Window).alpha() == 0
    ThemeManager.apply_theme('Dark')
    assert app.palette().color(QPalette.ColorRole.Window).alpha() == 255
    ThemeManager.apply_theme('Glass')
    assert ThemeManager.panel_colors().section_background == QColor(8, 9, 14, 77)
    ThemeManager.apply_theme('System')
    assert app.palette().color(QPalette.ColorRole.Window).alpha() == 255
