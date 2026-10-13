"""Liquid-glass theme.

Windows get a real alpha channel and paint a light, translucent sheen, so the
desktop genuinely shows through. What the glass blurs is configurable:

- ``desktop``: the window under Fleasion, blurred by the compositor (on GNOME
  via the Blur my Shell extension, see :mod:`.desktop_blur`)
- ``wallpaper``: the desktop wallpaper, blurred and painted by Fleasion itself

The dashboard also swaps the platform title bar for a glass one.
"""

from .chrome import register
from .controller import (
    ACCENT_COMBO,
    BACKDROP_COMBO,
    BLUR_BUTTON,
    GlassTheme,
    backdrop_label,
    theme,
)
from .desktop_blur import blur
from .state import (
    ACCENTS,
    BACKDROPS,
    DEFAULT_ACCENT,
    DEFAULT_BACKDROP,
    DEFAULT_OPACITY,
    MAX_OPACITY,
    MIN_OPACITY,
    Backdrop,
)

__all__ = [
    'ACCENTS',
    'ACCENT_COMBO',
    'BACKDROPS',
    'BACKDROP_COMBO',
    'BLUR_BUTTON',
    'DEFAULT_ACCENT',
    'DEFAULT_BACKDROP',
    'DEFAULT_OPACITY',
    'MAX_OPACITY',
    'MIN_OPACITY',
    'Backdrop',
    'GlassTheme',
    'backdrop_label',
    'blur',
    'register',
    'theme',
]
