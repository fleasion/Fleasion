"""Rounded glass panes, rendered once per look and reused as pixmaps."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap, QPixmapCache

if TYPE_CHECKING:
    from PySide6.QtCore import QRect


def _rgba(color: QColor | None) -> int:
    return 0 if color is None else color.rgba()


def _draw(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    *,
    fill: QColor | None,
    side: QColor | None,
    top: QColor | None,
) -> None:
    """Draw the pane with the painter as it is; the slow, exact path."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    if side is None:
        painter.setPen(Qt.PenStyle.NoPen)
    elif top is None or top == side:
        painter.setPen(QPen(side, 1.0))
    else:
        # A brighter top edge that blends into the sides below the corners
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0, top)
        rim.setColorAt(min(1.0, radius / max(rect.height(), 1)), side)
        rim.setColorAt(1, side)
        painter.setPen(QPen(rim, 1.0))
    painter.drawRoundedRect(rect, radius, radius)


def _blittable(painter: QPainter) -> bool:
    """Whether a pixmap drawn at whole device pixels matches a direct paint."""
    transform = painter.worldTransform()
    return transform.isIdentity() or not (
        transform.isScaling()
        or transform.isRotating()
        or transform.dx() != int(transform.dx())
        or transform.dy() != int(transform.dy())
    )


def paint_pane(
    painter: QPainter,
    rect: QRect,
    radius: float,
    *,
    fill: QColor | None,
    side: QColor | None = None,
    top: QColor | None = None,
) -> None:
    """Paint a rounded pane with a hairline rim inside `rect`.

    Parameters
    ----------
    painter
        The painter to draw with; its state is restored afterwards.
    rect
        The widget rect the pane fills; the rim runs half a pixel inside it.
    radius
        The corner radius.
    fill
        The pane colour, or None for a bare rim.
    side
        The rim colour, or None for no rim.
    top
        A brighter colour the rim starts from at the top edge, or None for an even rim.
    """
    if rect.isEmpty() or (fill is None and side is None):
        return

    painter.save()
    if not _blittable(painter):
        bounds = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
        _draw(painter, bounds, radius, fill=fill, side=side, top=top)
        painter.restore()
        return

    scale = painter.device().devicePixelRatioF()
    key = (
        f'glass-pane:{rect.width()}x{rect.height()}@{scale}:{radius}:'
        f'{_rgba(fill)}:{_rgba(side)}:{_rgba(top)}'
    )
    pixmap = QPixmap()
    if not QPixmapCache.find(key, pixmap):
        pixmap = QPixmap(round(rect.width() * scale), round(rect.height() * scale))
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        local = QPainter(pixmap)
        bounds = QRectF(0, 0, rect.width(), rect.height()).adjusted(0.5, 0.5, -0.5, -0.5)
        _draw(local, bounds, radius, fill=fill, side=side, top=top)
        local.end()
        QPixmapCache.insert(key, pixmap)
    painter.drawPixmap(QPointF(rect.topLeft()), pixmap)
    painter.restore()
