"""Shared appearance choices, independent of the widget style."""

from __future__ import annotations

import re
import sys
from typing import Final, Literal

type AppearanceStyle = Literal['Fusion', 'Glass']
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

STYLES: Final[tuple[AppearanceStyle, ...]] = ('Fusion', 'Glass')
COLOR_MODES: Final[tuple[ColorMode, ...]] = (
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
)
# Windows looks best with Fleasion's own dark palette; other desktops keep their theme
DEFAULT_COLOR_MODE: Final[ColorMode] = 'Dark' if sys.platform == 'win32' else 'System'

THEME_ACCENT: Final = 'Theme'
SYSTEM_ACCENT: Final = 'System'
DEFAULT_ACCENT: Final = THEME_ACCENT
# Used when neither the theme nor the platform provides an accent
FALLBACK_ACCENT: Final = 'Ice'
DEFAULT_CUSTOM_ACCENT: Final = '#a17dff'
# Ordered by hue so the swatch row reads as a spectrum
ACCENTS: Final[dict[str, str]] = {
    'Violet': '#b49cff',
    'Iris': '#9097ff',
    'Ice': '#8ab4ff',
    'Blue': '#6ca4f5',
    'Sky': '#69c3e9',
    'Teal': '#54d2d2',
    'Mint': '#4fe0a6',
    'Green': '#97c871',
    'Amber': '#ffbe45',
    'Peach': '#efac85',
    'Coral': '#ff977a',
    'Signal': '#ff5a68',
    'Sweet': '#c50ed2',
    'Rose': '#f59ac2',
    'Mono': '#f2f4f8',
}


def appearance_style(value: object) -> AppearanceStyle:
    return 'Glass' if value == 'Glass' else 'Fusion'


def color_mode(value: object) -> ColorMode:
    for mode in COLOR_MODES:
        if mode == value:
            return mode
    return 'System'


def accent_name(value: object) -> str:
    if isinstance(value, str):
        if value in {THEME_ACCENT, SYSTEM_ACCENT} or value in ACCENTS:
            return value
        if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            return value.lower()
    return DEFAULT_ACCENT
