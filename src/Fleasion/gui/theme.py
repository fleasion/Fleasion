"""Theme management for PyQt6."""

from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtWidgets import QApplication, QWidget
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
    def apply_to_widget(widget: QWidget):
        """Apply current theme to a specific widget immediately to prevent white flicker."""
        app = QApplication.instance()
        if not app:
            return

        # Apply the application's current palette to the widget
        widget.setPalette(app.palette())
        widget.setAutoFillBackground(True)

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
        """Apply dark theme."""
        app.setStyle('Fusion')
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Base, QColor(35, 35, 35))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(25, 25, 25))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Text, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.Button, QColor(53, 53, 53))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(255, 255, 255))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Link, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(42, 130, 218))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(0, 0, 0))
        app.setPalette(palette)
