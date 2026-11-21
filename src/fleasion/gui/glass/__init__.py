"""Liquid-glass theme.

Windows get a real alpha channel and paint a tinted surface with a faint sheen.
What sits behind the tint is configurable:

- ``flat``: nothing; the surface is opaque and ignores the tint setting
- ``ambient``: soft accent and complementary glows painted by Fleasion
- ``desktop``: the window under Fleasion, blurred by the compositor where it
  can (on GNOME via the Blur my Shell extension, see :mod:`.desktop_blur`)
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
