"""Resolve the theme table into Qt colors and palettes."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from PySide6.QtGui import QColor, QPalette

from .catalog import ACCENTS, FALLBACK_ACCENT, THEMES_BY_NAME

if TYPE_CHECKING:
    from .catalog import ColorMode, Palette, Roles

# Qt's built-in accent, which a platform leaves in place when it reports none
_QT_DEFAULT_ACCENT: Final = QColor('#308cc6')


def native_accent(palette: QPalette) -> QColor:
    """Return the desktop accent reported by a platform palette.

    KDE and GNOME report their accent as Highlight and leave Accent at Qt's default,
    while Windows reports its personalization color as Accent.
    """
    accent = palette.accent().color()
    if sys.platform == 'win32' and accent.isValid() and accent != _QT_DEFAULT_ACCENT:
        return QColor(accent)
    return QColor(palette.highlight().color())


def mix(first: QColor, second: QColor, amount: float) -> QColor:
    """Blend ``amount`` of the way from ``first`` to ``second``."""
    return QColor.fromRgbF(
        first.redF() + (second.redF() - first.redF()) * amount,
        first.greenF() + (second.greenF() - first.greenF()) * amount,
        first.blueF() + (second.blueF() - first.blueF()) * amount,
    )


def is_dark(color: QColor) -> bool:
    return color.red() + color.green() + color.blue() < 384


@dataclass(frozen=True, slots=True)
class ThemeColors:
    """Resolved colors of one theme, shared by both widget styles."""

    window: QColor
    base: QColor
    raised: QColor
    text: QColor
    muted: QColor
    border: QColor
    section: QColor
    section_border: QColor
    accent: QColor
    dark: bool
    roles: Roles = ()
    disabled_roles: Roles = ()


def resolve(palette: Palette) -> ThemeColors:
    """Blend the roles a theme's palette leaves out."""
    window, base, text = QColor(palette.window), QColor(palette.base), QColor(palette.text)
    section = QColor(palette.section) if palette.section else mix(window, base, 0.5)
    return ThemeColors(
        window=window,
        base=base,
        raised=QColor(palette.raised) if palette.raised else mix(window, text, 0.065),
        text=text,
        muted=mix(window, text, palette.muted_mix),
        border=mix(window, text, palette.border_mix),
        section=section,
        section_border=QColor(palette.section_border)
        if palette.section_border
        else mix(section, text, palette.border_mix * 0.8),
        accent=QColor(palette.accent),
        dark=is_dark(window),
        roles=palette.roles,
        disabled_roles=palette.disabled_roles,
    )


def theme_colors(mode: ColorMode, native: QPalette | None = None) -> ThemeColors:
    """Resolve a theme; System reads the platform palette when one is available."""
    palette = THEMES_BY_NAME[mode].palette
    if palette is not None:
        return resolve(palette)
    if native is None:
        return theme_colors('Dark')
    window, base = native.window().color(), native.base().color()
    text = native.windowText().color()
    section = native.alternateBase().color()
    accent = native_accent(native)
    return ThemeColors(
        window=window,
        base=base,
        raised=native.button().color(),
        text=text,
        muted=mix(window, text, 0.63),
        border=mix(window, text, 0.13),
        section=section,
        section_border=native.mid().color(),
        accent=accent if accent.isValid() else QColor(ACCENTS[FALLBACK_ACCENT]),
        dark=is_dark(window),
    )


def to_qpalette(colors: ThemeColors) -> QPalette:
    """Build the Qt palette for a theme; the accent roles are applied separately."""
    role, group = QPalette.ColorRole, QPalette.ColorGroup
    black, white = QColor('#000000'), QColor('#ffffff')
    window, text = colors.window, colors.text
    palette = QPalette()
    for color_role, color in (
        (role.Window, window),
        (role.WindowText, text),
        (role.Base, colors.base),
        (role.AlternateBase, mix(colors.base, text, 0.035)),
        (role.ToolTipBase, colors.raised),
        (role.ToolTipText, text),
        (role.Text, text),
        (role.Button, colors.raised),
        (role.ButtonText, text),
        (role.Light, mix(window, text, 0.12) if colors.dark else white),
        (role.Midlight, mix(window, black, 0.15)),
        (role.Mid, mix(window, black, 0.25)),
        (role.Dark, mix(window, black, 0.35)),
        (role.Shadow, mix(window, black, 0.5)),
        (role.Highlight, colors.accent),
        (role.Link, colors.accent),
    ):
        palette.setColor(color_role, color)
    palette.setColor(role.BrightText, palette.light().color())
    placeholder = QColor(text)
    placeholder.setAlpha(128)
    palette.setColor(role.PlaceholderText, placeholder)
    disabled = mix(window, text, 0.45)
    for color_role in (role.Text, role.ButtonText, role.WindowText):
        palette.setColor(group.Disabled, color_role, disabled)
    for name, color in colors.roles:
        palette.setColor(getattr(role, name), QColor(color))
    for name, color in colors.disabled_roles:
        palette.setColor(group.Disabled, getattr(role, name), QColor(color))
    return palette
