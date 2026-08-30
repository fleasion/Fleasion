"""Theme management for PySide6."""

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class PanelThemeColors:
    """Resolved colors for custom-painted tab panels."""

    section_background: QColor
    section_border: QColor
    container_background_css: str


if TYPE_CHECKING:

    def _windows_apps_use_light_theme() -> bool | None: ...
else:

    def _windows_apps_use_light_theme() -> bool | None:
        try:
            winreg = __import__('winreg')
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
                0,
                winreg.KEY_QUERY_VALUE,
            ) as key:
                use_light, _value_type = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return bool(int(use_light))
        except ImportError, OSError, TypeError, ValueError:
            return None


class ThemeManager:
    """Manages application theme."""

    _system_style_name: ClassVar[str | None] = None
    _current_theme: ClassVar[str] = 'System'
    # Visual theme after resolving the System option.  Windows System maps to
    # one of Fleasion's explicit Light/Dark palettes; Linux and macOS keep
    # their Qt-provided system theme behavior.
    _effective_theme: ClassVar[str] = 'System'

    @staticmethod
    def apply_theme(theme: str) -> None:
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        ThemeManager._remember_system_theme(app)
        ThemeManager._current_theme = theme
        ThemeManager._effective_theme = theme

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
            # Linux deliberately keeps the desktop's real Qt style/palette.
            # Users commonly choose a global Qt theme (Breeze, Kvantum, etc.)
            # and System should continue to mean exactly that.
            ThemeManager._effective_theme = 'System'
            current_style = app.style()
            current_style_name = current_style.objectName() if current_style else None
            if (
                ThemeManager._system_style_name
                and current_style_name != ThemeManager._system_style_name
            ):
                app.setStyle(ThemeManager._system_style_name)
            app.setPalette(QPalette())
            return

        if sys.platform == 'win32':
            # Qt's Windows system dark palette is not the palette Fleasion uses
            # for its Dark option (and can be noticeably darker).  Keep the
            # saved/selected option as System, but resolve Windows' actual app
            # appearance and use the exact same Fleasion palette as Light/Dark.
            effective = ThemeManager._windows_system_theme(app)
            ThemeManager._effective_theme = effective
            app.setStyle('Fusion')
            app.setPalette(
                ThemeManager._dark_palette()
                if effective == 'Dark'
                else ThemeManager._light_palette()
            )
            return

        # macOS already reports its current appearance through Qt's platform
        # theme.  Unlike Windows, keep System dynamic/native instead of mapping
        # it to Fleasion's forced palettes.
        ThemeManager._effective_theme = 'System'
        app.setStyle('Fusion')
        app.setPalette(app.style().standardPalette())

    @staticmethod
    def _windows_system_theme(app: QApplication) -> str:
        """Return the Windows app appearance as ``Light`` or ``Dark``.

        Read the Windows personalization setting directly instead of inferring
        it from Qt's palette -- the palette is the part we intentionally do not
        trust for System mode on Windows.  Qt/palette checks are only fallbacks
        for unusual environments where the registry value is unavailable.
        """
        use_light = _windows_apps_use_light_theme()
        if use_light is not None:
            return 'Light' if use_light else 'Dark'

        style_hints = app.styleHints()
        color_scheme_getter = getattr(style_hints, 'colorScheme', None)
        if callable(color_scheme_getter):
            color_scheme = color_scheme_getter()
            if color_scheme == ThemeManager._qt_color_scheme('Dark'):
                return 'Dark'
            if color_scheme == ThemeManager._qt_color_scheme('Light'):
                return 'Light'

        return 'Dark' if app.palette().window().color().lightness() < 128 else 'Light'

    @staticmethod
    def _apply_forced_theme(app: QApplication, theme: str) -> None:
        app.setStyle('Fusion')
        if theme == 'Dark':
            ThemeManager._set_color_scheme(app, 'Dark')
            app.setPalette(ThemeManager._dark_palette())
        elif theme == 'Light':
            ThemeManager._set_color_scheme(app, 'Light')
            app.setPalette(ThemeManager._light_palette())
        else:
            app.setPalette(app.style().standardPalette())

    @staticmethod
    def _dark_palette() -> QPalette:
        palette = QPalette()
        text = QColor('#f0f0f0')
        palette.setColor(QPalette.ColorRole.Window, QColor('#323232'))
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, QColor('#242424'))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#2b2b2b'))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#ffffdc'))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor('#000000'))
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, QColor('#323232'))
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.BrightText, QColor('#4b4b4b'))
        palette.setColor(QPalette.ColorRole.Link, QColor('#308cc6'))
        palette.setColor(QPalette.ColorRole.Highlight, QColor('#308cc6'))
        palette.setColor(QPalette.ColorRole.HighlightedText, text)
        palette.setColor(QPalette.ColorRole.PlaceholderText, ThemeManager._placeholder_color(text))
        palette.setColor(QPalette.ColorRole.Light, QColor('#4b4b4b'))
        palette.setColor(QPalette.ColorRole.Midlight, QColor('#2a2a2a'))
        palette.setColor(QPalette.ColorRole.Dark, QColor('#212121'))
        palette.setColor(QPalette.ColorRole.Mid, QColor('#262626'))
        palette.setColor(QPalette.ColorRole.Shadow, QColor('#191919'))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor('#f0f0f0'),
        )
        return palette

    @staticmethod
    def _light_palette() -> QPalette:
        palette = QPalette()
        text = QColor('#000000')
        palette.setColor(QPalette.ColorRole.Window, QColor('#efefef'))
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#f7f7f7'))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#ffffdc'))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor('#000000'))
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, QColor('#efefef'))
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.BrightText, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Link, QColor('#0000ff'))
        palette.setColor(QPalette.ColorRole.Highlight, QColor('#308cc6'))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.PlaceholderText, ThemeManager._placeholder_color(text))
        palette.setColor(QPalette.ColorRole.Light, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Midlight, QColor('#cacaca'))
        palette.setColor(QPalette.ColorRole.Dark, QColor('#9f9f9f'))
        palette.setColor(QPalette.ColorRole.Mid, QColor('#b8b8b8'))
        palette.setColor(QPalette.ColorRole.Shadow, QColor('#767676'))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor('#ffffff'),
        )
        return palette

    @staticmethod
    def _placeholder_color(text_color: QColor) -> QColor:
        color = QColor(text_color)
        color.setAlpha(128)
        return color

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

        Forced Light/Dark keep the Fleasion palette.  Windows System resolves
        to one of those same palettes, while Linux/macOS System continue using
        the active Qt palette.
        """
        app = QApplication.instance()
        if palette is None:
            if isinstance(app, QApplication):
                palette = app.palette()
            else:
                palette = QPalette()

        panel_theme = ThemeManager._current_theme
        if panel_theme == 'System':
            panel_theme = ThemeManager._effective_theme

        if panel_theme == 'System':
            return PanelThemeColors(
                section_background=palette.alternateBase().color(),
                section_border=palette.mid().color(),
                container_background_css='background-color: palette(alternate-base);',
            )

        is_dark = panel_theme == 'Dark'
        return PanelThemeColors(
            section_background=QColor('#272727') if is_dark else QColor('#f0f0f0'),
            section_border=QColor('#3a3a3a') if is_dark else QColor('#d0d0d0'),
            container_background_css=(
                'background-color: rgb(64, 64, 64);'
                if is_dark
                else 'background-color: palette(alternate-base);'
            ),
        )
