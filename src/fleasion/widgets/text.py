"""Text emphasis shared by every screen: muted and status tones, sizes and weights.

Widgets call :func:`style_text` instead of writing colors and font sizes into
their own style sheets. Tones resolve against the current palette, so the same
call reads well on light, dark and Glass surfaces and follows theme changes.
"""

from __future__ import annotations

from functools import cache
from typing import Final, Literal, override

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QWidget

type Tone = Literal['muted', 'info', 'success', 'warning', 'error']

# Status colors for dark and for light surfaces, each readable on its own surface
_TONES: Final[dict[Tone, tuple[str, str]]] = {
    'info': ('#6cb4ee', '#1f6fb2'),
    'success': ('#67c587', '#1e7b3c'),
    'warning': ('#ffcc66', '#8a5a00'),
    'error': ('#ef8f8f', '#b42318'),
}

_TONE_PROPERTY: Final = 'textTone'


def tone_color(tone: Tone, palette: QPalette) -> QColor:
    """Return the color of a tone on the surface the palette describes."""
    if tone == 'muted':
        return palette.placeholderText().color()

    # Glass makes Window transparent, so judge the surface by its text instead
    dark_surface = palette.text().color().lightnessF() > 0.5
    dark, light = _TONES[tone]
    return QColor(dark if dark_surface else light)


class _ToneKeeper(QObject):
    """Re-resolves a widget's tone whenever its inherited palette changes."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.PaletteChange:
            tone = watched.property(_TONE_PROPERTY)
            if isinstance(tone, str) and tone in _TONES:
                _paint_tone(watched, tone)  # pyright: ignore[reportArgumentType] - checked above
        return False


@cache
def _keeper() -> _ToneKeeper:
    # One shared filter serves every toned widget
    return _ToneKeeper()


def _paint_tone(widget: QObject, tone: Tone) -> None:
    if not isinstance(widget, QWidget):
        return

    # Our own color stays in the widget palette, so resolve against the parent's
    parent = widget.parentWidget()
    color = tone_color(tone, parent.palette() if parent is not None else widget.palette())

    # Setting the palette sends another PaletteChange; stop once it matches
    palette = widget.palette()
    if palette.color(QPalette.ColorRole.WindowText) == color:
        return
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
        palette.setColor(role, color)
    widget.setPalette(palette)


def set_tone(widget: QWidget, tone: Tone | None) -> None:
    """Color a widget's text with a tone, or return it to the normal text color."""
    previous = widget.property(_TONE_PROPERTY)
    widget.setProperty(_TONE_PROPERTY, tone)

    # Muted text is a palette role of its own and follows the theme by itself
    widget.setForegroundRole(
        QPalette.ColorRole.PlaceholderText if tone == 'muted' else QPalette.ColorRole.WindowText
    )

    # Drop a status color set earlier
    if isinstance(previous, str) and previous in _TONES and tone != previous:
        palette = widget.palette()
        parent = widget.parentWidget()
        inherited = parent.palette() if parent is not None else QPalette()
        for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text):
            palette.setColor(role, inherited.color(role))
        widget.setPalette(palette)

    if tone is None or tone == 'muted':
        return

    widget.removeEventFilter(_keeper())
    widget.installEventFilter(_keeper())
    _paint_tone(widget, tone)


def style_text(
    widget: QWidget,
    *,
    tone: Tone | None = None,
    point_size: float | None = None,
    pixel_size: int | None = None,
    bold: bool | None = None,
    italic: bool | None = None,
) -> None:
    """Set a widget's text tone and font in one call.

    Parameters
    ----------
    widget
        The label or control whose text is styled.
    tone
        Muted or status color; None keeps the normal text color.
    point_size, pixel_size
        Font size; at most one of them. None keeps the inherited size.
    bold, italic
        Font weight and slant; None keeps the inherited value.
    """
    set_tone(widget, tone)

    if point_size is None and pixel_size is None and bold is None and italic is None:
        return

    # A blank font pins only the attributes set here; the rest still follow the parent
    font = QFont()
    if point_size is not None:
        font.setPointSizeF(point_size)
    if pixel_size is not None:
        font.setPixelSize(pixel_size)
    if bold is not None:
        font.setBold(bold)
    if italic is not None:
        font.setItalic(italic)
    widget.setFont(font)
