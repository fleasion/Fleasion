"""Theme management for PyQt6."""

from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


class ThemeManager:
    """Manages application theme."""

    @staticmethod
    def apply_theme(theme: str):
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not app:
            return

        if theme == 'Light':
            ThemeManager._apply_light_theme(app)
        elif theme == 'Dark':
            ThemeManager._apply_dark_theme(app)
        else:  # System
            # Reset to system default
            app.setStyle('Fusion')
            app.setPalette(app.style().standardPalette())

    @staticmethod
    def _apply_light_theme(app: QApplication):
        """Apply light theme."""
        app.setStyle('Fusion')
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Base, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(245, 245, 245))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Button, QColor(240, 240, 240))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(0, 0, 255))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        app.setPalette(palette)

    @staticmethod
    def _apply_dark_theme(app: QApplication):
        """Apply dark theme matching Windows system dark mode colors."""
        app.setStyle('Fusion')
        palette = QPalette()

        # Match Windows system dark theme colors
        palette.setColor(QPalette.ColorRole.Window, QColor(32, 32, 32))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Base, QColor(45, 45, 45))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(50, 50, 50))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(160, 160, 160))
        palette.setColor(QPalette.ColorRole.Button, QColor(51, 51, 51))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(99, 177, 255))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(0, 120, 215))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Light, QColor(75, 75, 75))
        palette.setColor(QPalette.ColorRole.Midlight, QColor(63, 63, 63))
        palette.setColor(QPalette.ColorRole.Mid, QColor(42, 42, 42))
        palette.setColor(QPalette.ColorRole.Dark, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.Shadow, QColor(0, 0, 0))

        # Disabled state colors for input widgets
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(128, 128, 128))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor(128, 128, 128))
        palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(128, 128, 128))

        app.setPalette(palette)
