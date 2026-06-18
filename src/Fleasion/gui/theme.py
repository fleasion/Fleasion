"""Theme management for PyQt6."""

import sys
from typing import ClassVar

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


class ThemeManager:
    """Manages application theme."""

    _system_style_name: ClassVar[str | None] = None

    @staticmethod
    def apply_theme(theme: str) -> None:
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        ThemeManager._remember_system_theme(app)

        if theme == 'System':
            ThemeManager._apply_system_theme(app)
        else:
            ThemeManager._apply_forced_theme(app, theme)

        ThemeManager._refresh_widgets(app)

    @staticmethod
    def _remember_system_theme(app: QApplication) -> None:
        if ThemeManager._system_style_name is not None:
            return

        style = app.style()
        ThemeManager._system_style_name = style.objectName() if style else None

    @staticmethod
    def _apply_system_theme(app: QApplication) -> None:
        app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)

        if sys.platform.startswith('linux'):
            current_style = app.style()
            current_style_name = current_style.objectName() if current_style else None
            if (
                ThemeManager._system_style_name
                and current_style_name != ThemeManager._system_style_name
            ):
                app.setStyle(ThemeManager._system_style_name)
            app.setPalette(QPalette())
            return

        app.setStyle('Fusion')
        app.setPalette(app.style().standardPalette())

    @staticmethod
    def _apply_forced_theme(app: QApplication, theme: str) -> None:
        app.setStyle('Fusion')
        if theme == 'Dark':
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        elif theme == 'Light':
            app.styleHints().setColorScheme(Qt.ColorScheme.Light)

        app.setPalette(app.style().standardPalette())

    @staticmethod
    def _refresh_widgets(app: QApplication) -> None:
        for widget in app.allWidgets():
            style = widget.style()
            if style:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()
