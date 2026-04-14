"""Theme management for PyQt6."""

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


class ThemeManager:
    """Manages application theme."""

    @staticmethod
    def apply_theme(theme: str):
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        app.setStyle('Fusion')

        if theme == 'Dark':
            app.styleHints().setColorScheme(Qt.ColorScheme.Dark)
        elif theme == 'Light':
            app.styleHints().setColorScheme(Qt.ColorScheme.Light)
        else:  # System
            app.styleHints().setColorScheme(Qt.ColorScheme.Unknown)

        app.setPalette(app.style().standardPalette())

        for widget in app.allWidgets():
            style = widget.style()
            if style:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()
