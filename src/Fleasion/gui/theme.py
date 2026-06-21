"""Theme management for PyQt6."""

import sys
from dataclasses import dataclass
from typing import ClassVar

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt


@dataclass(frozen=True)
class PanelThemeColors:
    """Resolved colors for custom-painted tab panels."""

    section_background: QColor
    section_border: QColor
    container_background_css: str


class ThemeManager:
    """Manages application theme."""

    _system_style_name: ClassVar[str | None] = None
    _current_theme: ClassVar[str] = 'System'

    @staticmethod
    def apply_theme(theme: str) -> None:
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        ThemeManager._remember_system_theme(app)
        ThemeManager._current_theme = theme

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
        ThemeManager._set_color_scheme(app, 'Unknown')

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
            ThemeManager._set_color_scheme(app, 'Dark')
        elif theme == 'Light':
            ThemeManager._set_color_scheme(app, 'Light')

        app.setPalette(app.style().standardPalette())

    @staticmethod
    def _set_color_scheme(app: QApplication, color_scheme_name: str) -> None:
        color_scheme = ThemeManager._qt_color_scheme(color_scheme_name)
        if color_scheme is None:
            return

        style_hints = app.styleHints()
        set_color_scheme = getattr(style_hints, 'setColorScheme', None)
        if callable(set_color_scheme):
            set_color_scheme(color_scheme)

    @staticmethod
    def _qt_color_scheme(color_scheme_name: str):
        color_scheme_type = getattr(Qt, 'ColorScheme', None)
        return getattr(color_scheme_type, color_scheme_name, None)

    @staticmethod
    def _refresh_widgets(app: QApplication) -> None:
        for widget in app.allWidgets():
            style = widget.style()
            if style:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()

    @staticmethod
    def panel_colors(palette: QPalette | None = None) -> PanelThemeColors:
        """Return colors for custom panels without losing forced-theme styling.

        The forced Light/Dark themes keep the previous Fleasion look. System
        follows the active Qt palette so Linux desktop themes can supply their
        own colors.
        """
        app = QApplication.instance()
        if palette is None:
            if isinstance(app, QApplication):
                palette = app.palette()
            else:
                palette = QPalette()

        if ThemeManager._current_theme == 'System':
            return PanelThemeColors(
                section_background=palette.alternateBase().color(),
                section_border=palette.mid().color(),
                container_background_css='background-color: palette(alternate-base);',
            )

        is_dark = ThemeManager._current_theme == 'Dark'
        return PanelThemeColors(
            section_background=QColor('#272727') if is_dark else QColor('#f0f0f0'),
            section_border=QColor('#3a3a3a') if is_dark else QColor('#d0d0d0'),
            container_background_css=(
                'background-color: rgb(64, 64, 64);'
                if is_dark
                else 'background-color: palette(alternate-base);'
            ),
        )
