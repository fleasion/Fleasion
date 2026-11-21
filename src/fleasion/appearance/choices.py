"""Widget style and Glass backdrop choices."""

from __future__ import annotations

from typing import Final, Literal

type AppearanceStyle = Literal['Fusion', 'Glass']

STYLES: Final[tuple[AppearanceStyle, ...]] = ('Fusion', 'Glass')


type Backdrop = Literal['flat', 'ambient', 'desktop', 'wallpaper']

BACKDROPS: Final[tuple[Backdrop, ...]] = ('flat', 'ambient', 'desktop', 'wallpaper')
DEFAULT_BACKDROP: Final[Backdrop] = 'flat'

# Tint over the backdrop; Flat stays opaque regardless
DEFAULT_OPACITY: Final = 0.85
MIN_OPACITY: Final = 0.2
MAX_OPACITY: Final = 1.0


def appearance_style(value: object) -> AppearanceStyle:
    return 'Glass' if value == 'Glass' else 'Fusion'


def backdrop(value: object) -> Backdrop:
    return value if value in BACKDROPS else DEFAULT_BACKDROP


def clamp_opacity(value: float) -> float:
    return min(MAX_OPACITY, max(MIN_OPACITY, value))
