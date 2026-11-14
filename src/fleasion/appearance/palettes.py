"""Built-in color themes, described by a few base colors and resolved into roles."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from PySide6.QtGui import QColor, QPalette

from .choices import ACCENTS, FALLBACK_ACCENT

if TYPE_CHECKING:
    from .choices import ColorMode

type Roles = tuple[tuple[str, str], ...]

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


@dataclass(frozen=True, slots=True)
class Palette:
    """Base colors of a built-in theme; the remaining roles are blended from them.

    ``window`` is the main background and ``base`` the background of inputs and lists.
    Dark themes recess ``base`` below ``window``, light themes raise it above.
    """

    window: str
    base: str
    text: str
    accent: str
    raised: str | None = None
    section: str | None = None
    section_border: str | None = None
    muted_mix: float = 0.63
    border_mix: float = 0.13
    # Exact Qt palette roles, by role name, for themes copied from a desktop scheme
    roles: Roles = ()
    disabled_roles: Roles = ()

    def resolve(self) -> ThemeColors:
        window, base, text = QColor(self.window), QColor(self.base), QColor(self.text)
        section = QColor(self.section) if self.section else mix(window, base, 0.5)
        return ThemeColors(
            window=window,
            base=base,
            raised=QColor(self.raised) if self.raised else mix(window, text, 0.065),
            text=text,
            muted=mix(window, text, self.muted_mix),
            border=mix(window, text, self.border_mix),
            section=section,
            section_border=QColor(self.section_border)
            if self.section_border
            else mix(section, text, self.border_mix * 0.8),
            accent=QColor(self.accent),
            dark=is_dark(window),
            roles=self.roles,
            disabled_roles=self.disabled_roles,
        )


# Dark and Light keep Fleasion's original palettes exactly
PALETTES: Final[dict[ColorMode, Palette]] = {
    'Dark': Palette(
        '#323232',
        '#242424',
        '#f0f0f0',
        '#308cc6',
        raised='#323232',
        section='#272727',
        section_border='#3a3a3a',
    ),
    'Light': Palette(
        '#efefef',
        '#ffffff',
        '#000000',
        '#308cc6',
        raised='#efefef',
        section='#f0f0f0',
        section_border='#d0d0d0',
    ),
    'Charcoal': Palette('#1f2024', '#18191c', '#e8e8eb', '#a3bea4'),
    'Midnight': Palette('#161c2a', '#0f141f', '#e0e7f4', '#88a6f3'),
    # Every role matches the KDE Sweet color scheme, not only the base colors
    'Sweet': Palette(
        '#181b28',
        '#161925',
        '#d3dae3',
        ACCENTS['Sweet'],
        raised='#1e2233',
        section='#1f2333',
        section_border='#282d44',
        muted_mix=0.65,
        border_mix=0.16,
        roles=(
            ('AlternateBase', '#1f2333'),
            ('ToolTipBase', '#353945'),
            ('ToolTipText', '#d3dae3'),
            ('ButtonText', '#c3c7d1'),
            ('BrightText', '#4b4b4b'),
            ('Light', '#0f1119'),
            ('Midlight', '#171a26'),
            ('Dark', '#3c4466'),
            ('Mid', '#282d44'),
            ('Shadow', '#191919'),
            ('PlaceholderText', '#666a73'),
        ),
        disabled_roles=(
            ('WindowText', '#3c4466'),
            ('Text', '#3c4466'),
            ('ButtonText', '#3c4466'),
            ('Base', '#1e2233'),
            ('Window', '#1e2233'),
            ('Shadow', '#252525'),
            ('BrightText', '#ffffff'),
            ('Highlight', '#282d44'),
            ('HighlightedText', '#171a26'),
            ('Link', '#5294e2'),
        ),
    ),
    'Nord': Palette('#2e3440', '#242a35', '#eceff4', '#88c0d0'),
    'Catppuccin': Palette('#1e1e2e', '#181825', '#cdd6f4', '#cba6f7'),
    'Dracula': Palette('#282a36', '#1e1f2a', '#f8f8f2', '#bd93f9'),
    'Forest': Palette('#1b2622', '#141d1b', '#e0ebde', '#a3be8c'),
    'OLED': Palette('#0c0c0f', '#000000', '#ececf0', '#b9aeeb'),
    'Paper': Palette('#f2f3f5', '#fcfcfd', '#242830', '#5867ad'),
    'Sand': Palette('#ebe7df', '#f8f5ee', '#36312d', '#94634b'),
    'Latte': Palette('#e6e9ef', '#eff1f5', '#4c4f69', '#8839ef', muted_mix=0.75),
    'HighContrast': Palette('#0a0a0a', '#000000', '#ffffff', '#ffdd52', border_mix=0.65),
}


def theme_colors(mode: ColorMode, native: QPalette | None = None) -> ThemeColors:
    """Resolve a theme; System reads the platform palette when one is available."""
    palette = PALETTES.get(mode)
    if palette is not None:
        return palette.resolve()
    if native is None:
        return PALETTES['Dark'].resolve()
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
