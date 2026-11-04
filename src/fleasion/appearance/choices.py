"""Shared appearance choices, independent of the widget style."""

from __future__ import annotations

import re
from typing import Final, Literal

type AppearanceStyle = Literal['Fusion', 'Glass']
type ColorMode = Literal['Dark', 'Light', 'System']

STYLES: Final[tuple[AppearanceStyle, ...]] = ('Fusion', 'Glass')
COLOR_MODES: Final[tuple[ColorMode, ...]] = ('Dark', 'Light', 'System')
DEFAULT_ACCENT: Final = 'Ice'
DEFAULT_CUSTOM_ACCENT: Final = '#a17dff'
ACCENTS: Final[dict[str, str]] = {
    'Ice': '#8ab4ff',
    'Signal': '#ff5a68',
    'Mint': '#4fe0a6',
    'Amber': '#ffbe45',
    'Violet': '#b49cff',
    'Rose': '#f59ac2',
    'Coral': '#ff977a',
    'Teal': '#54d2d2',
    'Mono': '#f2f4f8',
}


def appearance_style(value: object) -> AppearanceStyle:
    return 'Glass' if value == 'Glass' else 'Fusion'


def color_mode(value: object) -> ColorMode:
    if value == 'Dark':
        return 'Dark'
    if value == 'Light':
        return 'Light'
    return 'System'


def accent_name(value: object) -> str:
    if isinstance(value, str):
        if value == 'System' or value in ACCENTS:
            return value
        if re.fullmatch(r'#[0-9a-fA-F]{6}', value):
            return value.lower()
    return DEFAULT_ACCENT
