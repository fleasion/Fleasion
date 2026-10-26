"""Theme management for PySide6."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

if sys.platform == 'win32':
    import winreg

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QWidget

from fleasion.appearance import accent_name
from fleasion.appearance.colors import apply_accent, luminance
from fleasion.widgets.style import LayoutStyle

from .appearance_events import AppearanceEvents

if TYPE_CHECKING:
    from typing import Literal

    from fleasion.appearance import AppearanceStyle, ColorMode
    from fleasion.config.manager import ConfigManager


@dataclass(frozen=True)
class PanelThemeColors:
    """Resolved colors for custom-painted tab panels."""

    section_background: QColor
    section_border: QColor
    container_background: QColor | None = None

    @property
    def container_background_css(self) -> str:
        if self.container_background is None:
            return 'background-color: palette(window);'
        if self.container_background.alpha() == 0:
            return 'background-color: transparent;'
        color = self.container_background
        return f'background-color: rgb({color.red()}, {color.green()}, {color.blue()});'


def _windows_apps_use_light_theme() -> bool | None:
    if sys.platform != 'win32':
        return None
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
            0,
            winreg.KEY_QUERY_VALUE,
        ) as key:
            use_light, _value_type = winreg.QueryValueEx(key, 'AppsUseLightTheme')
        return bool(int(use_light))
    except OSError, TypeError, ValueError:
        return None


class ThemeManager:
    """Manages application theme."""

    _system_style_name: ClassVar[str | None] = None
    _current_theme: ClassVar[str] = 'System'
    # Visual theme after resolving the System option.  Windows System maps to
    # one of Fleasion's explicit Light/Dark palettes; Linux and macOS keep
    # their Qt-provided system theme behavior.
    _effective_theme: ClassVar[str] = 'System'
    _system_palette: ClassVar[QPalette | None] = None
    _style: ClassVar[AppearanceStyle] = 'Fusion'
    _color_mode: ClassVar[ColorMode] = 'System'
    _accent: ClassVar[str] = 'Ice'
    _applying: ClassVar[bool] = False
    _events: ClassVar[AppearanceEvents | None] = None

    @staticmethod
    def _widget_style_name(app: QApplication) -> str:
        style = app.style()
        if name := style.objectName():
            return name
        # The application stylesheet wraps the native style in an unnamed proxy
        return next(
            (child.objectName() for child in style.findChildren(QStyle) if child.objectName()), ''
        )

    @staticmethod
    def ensure_widget_style(app: QApplication, name: str) -> None:
        if ThemeManager._widget_style_name(app).casefold() == name.casefold():
            current = app.style()
            if name.casefold() != 'fusion' or isinstance(current, LayoutStyle):
                return
            if current.findChild(LayoutStyle) is not None:
                return
        if name.casefold() == 'fusion':
            app.setStyle(LayoutStyle())
        else:
            app.setStyle(name)

    @staticmethod
    def events() -> AppearanceEvents:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            msg = 'Appearance events require a QApplication'
            raise TypeError(msg)
        if ThemeManager._events is None:
            ThemeManager._events = AppearanceEvents(app)
        return ThemeManager._events

    @staticmethod
    def apply_from_config(config: ConfigManager) -> None:
        from . import glass

        glass.theme.configure(config)
        ThemeManager.apply_appearance(
            config.appearance_style, config.color_mode, config.accent_color
        )

    @staticmethod
    def set_preference(
        config: ConfigManager, preference: Literal['style', 'color_mode', 'accent'], value: str
    ) -> None:
        from fleasion.appearance import appearance_style, color_mode

        if preference == 'style':
            style = appearance_style(value)
            if config.appearance_style == style:
                return
            config.appearance_style = style
        elif preference == 'color_mode':
            mode = color_mode(value)
            if config.color_mode == mode:
                return
            config.color_mode = mode
        else:
            if config.accent_color == accent_name(value):
                return
            config.accent_color = value
        ThemeManager.apply_from_config(config)

    @staticmethod
    def apply_theme(theme: str) -> None:
        """Apply a legacy combined theme choice."""
        from fleasion.appearance import color_mode

        ThemeManager.apply_appearance(
            'Glass' if theme == 'Glass' else 'Fusion',
            'Dark' if theme == 'Glass' else color_mode(theme),
            ThemeManager._accent,
        )

    @staticmethod
    def apply_appearance(style: AppearanceStyle, color_mode: ColorMode, accent: str) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication) or ThemeManager._applying:
            return
        ThemeManager._remember_system_theme(app)
        if ThemeManager._system_palette is None:
            ThemeManager._system_palette = QPalette(app.palette())
        events = ThemeManager.events()
        ThemeManager._style = style
        ThemeManager._color_mode = color_mode
        ThemeManager._accent = accent_name(accent)
        ThemeManager._current_theme = 'Glass' if style == 'Glass' else color_mode
        ThemeManager._effective_theme = color_mode
        from . import glass
        from .glass.state import state

        ThemeManager._applying = True
        try:
            if style == 'Fusion':
                glass.theme.disable(app)
            base, widget_style = ThemeManager._resolve_palette(app, style, color_mode)
            ThemeManager.ensure_widget_style(app, widget_style)
            if style == 'Glass':
                state.base_palette = (
                    base if color_mode == 'System' and sys.platform != 'win32' else None
                )
                state.dark = luminance(base.window().color()) < 0.3
                glass.theme.enable(app, accent=ThemeManager._accent, base_palette=base)
            else:
                app.setPalette(apply_accent(base, ThemeManager._accent))
            ThemeManager._refresh_widgets(app)
        finally:
            ThemeManager._applying = False
        events.changed.emit()

    @staticmethod
    def _resolve_palette(
        app: QApplication, style: AppearanceStyle, color_mode: ColorMode
    ) -> tuple[QPalette, str]:
        """Resolve colors without installing an intermediate application palette."""
        ThemeManager._set_color_scheme(app, 'Unknown' if color_mode == 'System' else color_mode)
        if color_mode == 'Dark':
            return ThemeManager._dark_palette(), 'Fusion'
        if color_mode == 'Light':
            return ThemeManager._light_palette(), 'Fusion'
        if sys.platform == 'win32':
            effective = ThemeManager._windows_system_theme(app)
            ThemeManager._effective_theme = effective
            return (
                ThemeManager._dark_palette()
                if effective == 'Dark'
                else ThemeManager._light_palette(),
                'Fusion',
            )
        widget_style = (
            ThemeManager._system_style_name or 'Fusion'
            if style == 'Fusion' and sys.platform.startswith('linux')
            else 'Fusion'
        )
        return QPalette(ThemeManager._system_palette), widget_style

    @staticmethod
    def follows_system() -> bool:
        return ThemeManager._color_mode == 'System' and not ThemeManager._applying

    @staticmethod
    def set_accent(name: str) -> None:
        ThemeManager.apply_appearance(ThemeManager._style, ThemeManager._color_mode, name)

    @staticmethod
    def remember_native_palette(palette: QPalette) -> bool:
        # Qt can deliver Glass palette changes after the synchronous apply guard ends
        if ThemeManager._applying or palette.window().color().alpha() == 0:
            return False
        ThemeManager._system_palette = QPalette(palette)
        return ThemeManager.follows_system()

    @staticmethod
    def refresh_system() -> None:
        if ThemeManager.follows_system():
            ThemeManager.apply_appearance(ThemeManager._style, 'System', ThemeManager._accent)

    @staticmethod
    def _remember_system_theme(app: QApplication) -> None:
        if ThemeManager._system_style_name is not None:
            return

        ThemeManager._system_style_name = ThemeManager._widget_style_name(app) or None

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
            app.setPalette(ThemeManager._system_palette or QPalette())
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
    def _qt_color_scheme(color_scheme_name: str) -> object | None:
        color_scheme_type = getattr(Qt, 'ColorScheme', None)
        return getattr(color_scheme_type, color_scheme_name, None)

    @staticmethod
    def _refresh_widgets(app: QApplication) -> None:
        for window in app.topLevelWidgets():
            if window.parentWidget() is not None:
                continue
            if ThemeManager._style == 'Fusion':
                # QSS caches inherited palettes even for rules that only set
                # padding. Refresh those caches once, from parent to child
                for widget in (window, *window.findChildren(QWidget)):
                    if widget.testAttribute(Qt.WidgetAttribute.WA_StyleSheet):
                        widget.style().polish(widget)
            window.update()

    @staticmethod
    def panel_colors(palette: QPalette | None = None) -> PanelThemeColors:
        """Return colors for custom panels without losing forced-theme styling.

        Forced Light/Dark keep the Fleasion palette.  Windows System resolves
        to one of those same palettes, while Linux/macOS System continue using
        the active Qt palette.
        """
        app = QApplication.instance()
        if palette is None:
            palette = app.palette() if isinstance(app, QApplication) else QPalette()

        panel_theme = ThemeManager._current_theme
        if panel_theme == 'System':
            panel_theme = ThemeManager._effective_theme

        if panel_theme == 'System':
            return PanelThemeColors(
                section_background=palette.alternateBase().color(),
                section_border=palette.mid().color(),
            )

        if panel_theme == 'Glass':
            from .glass.style import glass_colors

            colors = glass_colors()
            # Match the QAbstractItemView / Scraper Glass surface rather than
            # adding an unrelated opaque navy panel above the backdrop
            return PanelThemeColors(
                section_background=QColor(
                    colors.well.red(), colors.well.green(), colors.well.blue(), 77
                ),
                section_border=QColor(colors.ink.red(), colors.ink.green(), colors.ink.blue(), 20),
                container_background=QColor(0, 0, 0, 0),
            )

        is_dark = panel_theme == 'Dark'
        return PanelThemeColors(
            section_background=QColor('#272727') if is_dark else QColor('#f0f0f0'),
            section_border=QColor('#3a3a3a') if is_dark else QColor('#d0d0d0'),
        )

    @staticmethod
    def apply_panel_background(widget: QWidget) -> None:
        """Paint the panel without a local stylesheet that freezes inherited colors."""
        color = ThemeManager.panel_colors().container_background
        palette = QPalette()
        role = QPalette.ColorRole.Window
        if color is not None:
            palette.setColor(role, color)
        widget.setPalette(palette)
        widget.setBackgroundRole(role)
        widget.setAutoFillBackground(color is None or color.alpha() != 0)
