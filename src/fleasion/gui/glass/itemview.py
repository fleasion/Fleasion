"""Smooth clipping for item views whose children paint rectangular backgrounds."""

from __future__ import annotations

from math import ceil
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QGraphicsEffect

from .style import RADIUS, glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


class RoundedItemViewEffect(QGraphicsEffect):
    def __init__(self, parent: QWidget, *, radius: int = RADIUS, popup: bool = False) -> None:
        super().__init__(parent)
        self._radius = radius
        self._popup = popup
        self._rim_key: tuple[QSize, float, int, int] | None = None
        self._rim_corners: list[tuple[QPointF, QImage]] = []

    @override
    def draw(self, painter: QPainter) -> None:
        offset = QPoint()
        pixmap = self.sourcePixmap(
            Qt.CoordinateSystem.LogicalCoordinates, offset, self.PixmapPadMode.NoPad
        )
        if pixmap.isNull():
            return
        rect = QRectF(pixmap.rect())
        rect.setSize(rect.size() / pixmap.devicePixelRatio())
        frame = QPainterPath()
        frame.addRoundedRect(rect, self._radius, self._radius)
        outside = QPainterPath()
        outside.addRect(rect)
        # Remove the outside with fractional coverage instead of a binary widget mask
        clip = QPainter(pixmap)
        clip.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
        clip.fillPath(outside.subtracted(frame), Qt.GlobalColor.black)
        clip.end()
        painter.drawPixmap(offset, pixmap)
        view = self.parent()
        if not isinstance(view, QAbstractItemView) or (width := view.frameWidth()) <= 0:
            return

        # Paint the rim once, after clipping the rectangular child backgrounds
        inset = width / 2
        rim_rect = rect.adjusted(inset, inset, -inset, -inset)
        rim = QLinearGradient(rim_rect.topLeft(), rim_rect.bottomLeft())
        color = QColor(glass_colors().ink)
        color.setAlphaF(0.14 if self._popup else 0.18)
        rim.setColorAt(0, color)
        color.setAlphaF(0.14 if self._popup else 0.08)
        rim.setColorAt(1, color)
        outline = QPainterPath()
        radius = max(0.0, self._radius - inset)
        outline.addRoundedRect(rim_rect, radius, radius)
        painter.save()
        painter.translate(offset)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(rim, width)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        ratio = pixmap.devicePixelRatio()
        extent = min(
            ceil((self._radius + width) * ratio),
            pixmap.width() // 2,
            pixmap.height() // 2,
        )
        if extent < 1:
            painter.restore()
            return
        corner_size = extent / ratio
        # Keep straight edges and text at native resolution
        painter.drawLine(QPointF(corner_size, inset), QPointF(rect.width() - corner_size, inset))
        painter.drawLine(
            QPointF(corner_size, rect.height() - inset),
            QPointF(rect.width() - corner_size, rect.height() - inset),
        )
        painter.drawLine(QPointF(inset, corner_size), QPointF(inset, rect.height() - corner_size))
        painter.drawLine(
            QPointF(rect.width() - inset, corner_size),
            QPointF(rect.width() - inset, rect.height() - corner_size),
        )
        key = (pixmap.size(), ratio, width, glass_colors().ink.rgba())
        if key != self._rim_key:
            self._rim_corners = []
            for x in (0, pixmap.width() - extent):
                for y in (0, pixmap.height() - extent):
                    corner = QImage(
                        extent * 4, extent * 4, QImage.Format.Format_ARGB32_Premultiplied
                    )
                    corner.fill(Qt.GlobalColor.transparent)
                    sampling = QPainter(corner)
                    sampling.setRenderHint(QPainter.RenderHint.Antialiasing)
                    sampling.scale(ratio * 4, ratio * 4)
                    sampling.translate(-x / ratio, -y / ratio)
                    sampling.setPen(pen)
                    sampling.setBrush(Qt.BrushStyle.NoBrush)
                    sampling.drawPath(outline)
                    sampling.end()
                    corner = corner.scaled(
                        extent,
                        extent,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                    corner.setDevicePixelRatio(ratio)
                    self._rim_corners.append((QPointF(x / ratio, y / ratio), corner))
            self._rim_key = key
        for position, corner in self._rim_corners:
            painter.drawImage(position, corner)
        painter.restore()
