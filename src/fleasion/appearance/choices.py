"""Shared appearance choices, independent of the widget style."""

from __future__ import annotations

from typing import Final, Literal

type AppearanceStyle = Literal['Fusion', 'Glass']
type ColorMode = Literal['Dark', 'Light', 'System']

STYLES: Final[tuple[AppearanceStyle, ...]] = ('Fusion', 'Glass')
COLOR_MODES: Final[tuple[ColorMode, ...]] = ('Dark', 'Light', 'System')
DEFAULT_ACCENT: Final = 'Ice'
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
    return value if isinstance(value, str) and value in ACCENTS else DEFAULT_ACCENT
