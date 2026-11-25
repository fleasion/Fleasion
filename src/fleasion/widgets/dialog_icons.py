"""Line icons for dialog buttons on desktops without an icon theme.

KDE and GNOME supply dialog button icons from their icon theme. Windows and
macOS have none, so Qt would fall back to its old bitmap icons; these line
icons stand in for them and take the color of the button's label.
"""

from __future__ import annotations

import math
from typing import Final, Literal, override

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QStyle

type Glyph = Literal['check', 'cancel', 'close', 'save']

_SP = QStyle.StandardPixmap

# The dialog buttons each glyph stands in for
_GLYPHS: Final[dict[QStyle.StandardPixmap, Glyph]] = {
    _SP.SP_DialogOkButton: 'check',
    _SP.SP_DialogYesButton: 'check',
    _SP.SP_DialogApplyButton: 'check',
    _SP.SP_DialogCancelButton: 'cancel',
    _SP.SP_DialogNoButton: 'cancel',
    _SP.SP_DialogAbortButton: 'cancel',
    _SP.SP_DialogCloseButton: 'close',
    _SP.SP_DialogDiscardButton: 'close',
    _SP.SP_DialogSaveButton: 'save',
}

# Glyphs are drawn on a 16px grid with a stroke that scales with the icon
_GRID: Final = 16.0
_STROKE: Final = 1.5

# The cancel circle, and how far its slash reaches from the center on each axis
_CANCEL_RADIUS: Final = 5.5
_CANCEL_SLASH: Final = _CANCEL_RADIUS * math.sqrt(0.5)

# Disabled buttons fade their glyph like their label
_DISABLED_ALPHA: Final = 0.45

# Every engine names its icons with this prefix, so a label can find them
_NAME_PREFIX: Final = 'fleasion-dialog-'
_BY_NAME: Final[dict[str, Glyph]] = {_NAME_PREFIX + glyph: glyph for glyph in set(_GLYPHS.values())}


def _theme_has_dialog_icons() -> bool:
    # An icon theme that has the OK icon covers the rest of the set too
    return QIcon.hasThemeIcon('dialog-ok')


def dialog_icon(pixmap: QStyle.StandardPixmap) -> QIcon | None:
    """The line icon for a dialog button, or None where the icon theme has one."""
    glyph = _GLYPHS.get(pixmap)
    if glyph is None or _theme_has_dialog_icons():
        return None
    return QIcon(_GlyphEngine(glyph, None))


def tinted(icon: QIcon, color: QColor) -> QIcon | None:
    """A copy of one of these icons drawn in color, or None for any other icon."""
    name = icon.name()
    glyph = _BY_NAME.get(name)
    if glyph is None:
        return None
    return QIcon(_GlyphEngine(glyph, color))


#
# Drawing
#


def _path(glyph: Glyph) -> QPainterPath:
    """The glyph's strokes on the 16px grid."""
    path = QPainterPath()

    def line(*points: tuple[float, float]) -> None:
        path.moveTo(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(QPointF(*point))

    match glyph:
        case 'check':
            line((3.0, 8.5), (6.5, 12.0), (13.0, 4.5))
        case 'cancel':
            # A circle struck through, like the theme icon on KDE
            path.addEllipse(QPointF(8.0, 8.0), _CANCEL_RADIUS, _CANCEL_RADIUS)
            near, far = 8.0 - _CANCEL_SLASH, 8.0 + _CANCEL_SLASH
            line((near, near), (far, far))
        case 'close':
            line((4.0, 4.0), (12.0, 12.0))
            line((12.0, 4.0), (4.0, 12.0))
        case 'save':
            # An arrow down into a tray
            line((8.0, 2.5), (8.0, 10.0))
            line((4.5, 6.5), (8.0, 10.0), (11.5, 6.5))
            line((2.5, 10.5), (2.5, 13.5), (13.5, 13.5), (13.5, 10.5))
    return path


class _GlyphEngine(QIconEngine):
    """Draws one glyph in a fixed color, or the palette's button text color."""

    def __init__(self, glyph: Glyph, color: QColor | None) -> None:
        super().__init__()
        self._glyph: Glyph = glyph
        self._color = color

    @override
    def clone(self) -> QIconEngine:
        return _GlyphEngine(self._glyph, self._color)

    @override
    def iconName(self) -> str:
        return _NAME_PREFIX + self._glyph

    @override
    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        del state

        # Color
        color = QColor(self._color or QApplication.palette().buttonText().color())
        if mode == QIcon.Mode.Disabled:
            color.setAlphaF(color.alphaF() * _DISABLED_ALPHA)

        # Strokes, scaled from the grid into the square centered in rect
        side = min(rect.width(), rect.height())
        square = QRectF(0, 0, side, side)
        square.moveCenter(QRectF(rect).center())
        scale = side / _GRID

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(square.topLeft())
        painter.scale(scale, scale)
        pen = QPen(color, _STROKE)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(_path(self._glyph))
        painter.restore()

    @override
    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        return self.scaledPixmap(size, mode, state, 1.0)

    @override
    def scaledPixmap(
        self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float
    ) -> QPixmap:
        # Drawn at device pixels so the strokes stay sharp on scaled displays
        pixmap = QPixmap(round(size.width() * scale), round(size.height() * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)

        # The painter works in logical pixels once the ratio is set
        painter = QPainter(pixmap)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap
