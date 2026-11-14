"""Every color theme and accent in one place: saved names, labels, and colors.

To add a theme, add its name to ``ColorMode`` and an entry to ``THEMES``. To add an
accent, add an entry to ``ACCENT_CHOICES``. An entry's position is its position in the
settings, the tray menu, and the command palette.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Final, Literal

from fleasion.localization import tr as translate

# Python cannot derive a Literal from data, so a test checks this matches THEMES
type ColorMode = Literal[
    'System',
    'Dark',
    'Light',
    'Charcoal',
    'Midnight',
    'Sweet',
    'Nord',
    'Catppuccin',
    'Dracula',
    'Forest',
    'OLED',
    'Paper',
    'Sand',
    'Latte',
    'HighContrast',
]
type Roles = tuple[tuple[str, str], ...]


def _label(name: str, label_key: str | None) -> str:
    return name if label_key is None else translate(label_key)


# Themes


@dataclass(frozen=True, slots=True, kw_only=True)
class Palette:
    """Base colors of a theme; the remaining roles are blended from them.

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
    border_mix: float = 0.13
    # Exact Qt palette roles, by role name, for themes copied from a desktop scheme
    roles: Roles = ()
    disabled_roles: Roles = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Theme:
    """One selectable theme."""

    name: ColorMode
    # Without a palette the theme follows the desktop
    palette: Palette | None = None
    # Translation identifier; proper names like Nord are shown as written
    label_key: str | None = None

    @property
    def label(self) -> str:
        return _label(self.name, self.label_key)


THEMES: Final[tuple[Theme, ...]] = (
    Theme(name='System', label_key='settings.appearance.theme.system'),
    # Dark and Light keep Fleasion's original palettes exactly
    Theme(
        name='Dark',
        palette=Palette(
            window='#323232',
            base='#242424',
            text='#f0f0f0',
            accent='#308cc6',
            raised='#323232',
            section='#272727',
            section_border='#3a3a3a',
        ),
        label_key='settings.appearance.theme.dark',
    ),
    Theme(
        name='Light',
        palette=Palette(
            window='#efefef',
            base='#ffffff',
            text='#000000',
            accent='#308cc6',
            raised='#efefef',
            section='#f0f0f0',
            section_border='#d0d0d0',
        ),
        label_key='settings.appearance.theme.light',
    ),
    Theme(
        name='Charcoal',
        palette=Palette(window='#1f2024', base='#18191c', text='#e8e8eb', accent='#a3bea4'),
        label_key='settings.appearance.theme.charcoal',
    ),
    Theme(
        name='Midnight',
        palette=Palette(window='#161c2a', base='#0f141f', text='#e0e7f4', accent='#88a6f3'),
        label_key='settings.appearance.theme.midnight',
    ),
    # Every role matches the KDE Sweet color scheme, not only the base colors
    Theme(
        name='Sweet',
        palette=Palette(
            window='#181b28',
            base='#161925',
            text='#d3dae3',
            accent='#c50ed2',
            raised='#1e2233',
            section='#1f2333',
            section_border='#282d44',
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
    ),
    Theme(
        name='Nord',
        palette=Palette(window='#2e3440', base='#242a35', text='#eceff4', accent='#88c0d0'),
    ),
    Theme(
        name='Catppuccin',
        palette=Palette(window='#1e1e2e', base='#181825', text='#cdd6f4', accent='#cba6f7'),
    ),
    Theme(
        name='Dracula',
        palette=Palette(window='#282a36', base='#1e1f2a', text='#f8f8f2', accent='#bd93f9'),
    ),
    Theme(
        name='Forest',
        palette=Palette(window='#1b2622', base='#141d1b', text='#e0ebde', accent='#a3be8c'),
        label_key='settings.appearance.theme.forest',
    ),
    Theme(
        name='OLED',
        palette=Palette(window='#0c0c0f', base='#000000', text='#ececf0', accent='#b9aeeb'),
    ),
    Theme(
        name='Paper',
        palette=Palette(window='#f2f3f5', base='#fcfcfd', text='#242830', accent='#5867ad'),
        label_key='settings.appearance.theme.paper',
    ),
    Theme(
        name='Sand',
        palette=Palette(window='#ebe7df', base='#f8f5ee', text='#36312d', accent='#94634b'),
        label_key='settings.appearance.theme.sand',
    ),
    Theme(
        name='Latte',
        palette=Palette(window='#e6e9ef', base='#eff1f5', text='#4c4f69', accent='#8839ef'),
    ),
    Theme(
        name='HighContrast',
        palette=Palette(
            window='#0a0a0a', base='#000000', text='#ffffff', accent='#ffdd52', border_mix=0.65
        ),
        label_key='settings.appearance.theme.high_contrast',
    ),
)

THEMES_BY_NAME: Final[dict[ColorMode, Theme]] = {theme.name: theme for theme in THEMES}
COLOR_MODES: Final[tuple[ColorMode, ...]] = tuple(theme.name for theme in THEMES)
# Windows looks best with Fleasion's own dark palette; other desktops keep their theme
DEFAULT_COLOR_MODE: Final[ColorMode] = 'Dark' if sys.platform == 'win32' else 'System'


def color_mode(value: object) -> ColorMode:
    """Validate a saved theme name, falling back to System."""
    for mode in COLOR_MODES:
        if mode == value:
            return mode
    return 'System'


def theme_label(name: str) -> str | None:
    """Return a theme's display name, or None when ``name`` is not a theme."""
    theme = next((theme for theme in THEMES if theme.name == name), None)
    return theme.label if theme is not None else None


# Accents


@dataclass(frozen=True, slots=True, kw_only=True)
class Accent:
    """One accent swatch."""

    name: str
    # Without a color the accent is resolved at runtime: from the theme, desktop, or picker
    color: str | None = None
    # Translation identifier; color names like Violet are shown as written
    label_key: str | None = None

    @property
    def label(self) -> str:
        return _label(self.name, self.label_key)


THEME_ACCENT: Final = 'Theme'
SYSTEM_ACCENT: Final = 'System'
CUSTOM_ACCENT: Final = 'Custom'

# Theme and System lead the swatch row, then the colors by hue, then the picker
ACCENT_CHOICES: Final[tuple[Accent, ...]] = (
    Accent(name=THEME_ACCENT, label_key='settings.appearance.accent.theme'),
    Accent(name=SYSTEM_ACCENT, label_key='settings.appearance.theme.system'),
    Accent(name='Violet', color='#b49cff'),
    Accent(name='Iris', color='#9097ff'),
    Accent(name='Ice', color='#8ab4ff'),
    Accent(name='Blue', color='#6ca4f5'),
    Accent(name='Sky', color='#69c3e9'),
    Accent(name='Teal', color='#54d2d2'),
    Accent(name='Mint', color='#4fe0a6'),
    Accent(name='Green', color='#97c871'),
    Accent(name='Amber', color='#ffbe45'),
    Accent(name='Peach', color='#efac85'),
    Accent(name='Coral', color='#ff977a'),
    Accent(name='Signal', color='#ff5a68'),
    Accent(name='Sweet', color='#c50ed2'),
    Accent(name='Rose', color='#f59ac2'),
    Accent(name='Mono', color='#f2f4f8'),
    Accent(name=CUSTOM_ACCENT, label_key='settings.appearance.custom'),
)

ACCENT_KEYS: Final[tuple[str, ...]] = tuple(accent.name for accent in ACCENT_CHOICES)

# Preset colors by name, without the runtime-resolved choices
ACCENTS: Final[dict[str, str]] = {
    accent.name: accent.color for accent in ACCENT_CHOICES if accent.color is not None
}
DEFAULT_ACCENT: Final = THEME_ACCENT

# Used when neither the theme nor the platform provides an accent
FALLBACK_ACCENT: Final = 'Ice'
DEFAULT_CUSTOM_ACCENT: Final = '#a17dff'


def accent_name(value: object) -> str:
    """Validate a saved accent: a choice name or a custom ``#rrggbb`` color."""
    if isinstance(value, str):
        if value in {THEME_ACCENT, SYSTEM_ACCENT} or value in ACCENTS:
            return value
        if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            return value.lower()
    return DEFAULT_ACCENT


def accent_label(name: str) -> str | None:
    """Return an accent's display name, or None when ``name`` is not an accent."""
    accent = next((accent for accent in ACCENT_CHOICES if accent.name == name), None)
    return accent.label if accent is not None else None
