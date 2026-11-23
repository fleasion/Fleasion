"""Glass primitives drawn by the application style instead of the style sheet."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QStyle

from .itemview import paint_item_focus
from .style import glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOption, QWidget


# Marks are drawn on a 16px grid centred in the indicator, as the old glyphs were
_MARK_GRID: Final = 16
_CHECK_RADIUS: Final = 5.0

_PE = QStyle.PrimitiveElement
_PM = QStyle.PixelMetric
_INDICATOR_METRICS: Final = frozenset(
    {
        _PM.PM_IndicatorWidth,
        _PM.PM_IndicatorHeight,
        _PM.PM_ExclusiveIndicatorWidth,
        _PM.PM_ExclusiveIndicatorHeight,
    }
)


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _mark(kind: str, rect: QRectF) -> QPainterPath:
    """The check, dash or dot inside an indicator, on the 16px grid at its centre."""
    origin = rect.center() - QPointF(_MARK_GRID / 2, _MARK_GRID / 2)

    def point(x: float, y: float) -> QPointF:
        return origin + QPointF(x, y)

    path = QPainterPath()
    if kind == 'check':
        path.moveTo(point(3.5, 8.5))
        path.lineTo(point(6.5, 11.5))
        path.lineTo(point(12.5, 4.5))
    elif kind == 'dash':
        path.moveTo(point(4.5, 8))
        path.lineTo(point(11.5, 8))
    else:
        path.addEllipse(point(8, 8), 3.2, 3.2)
    return path


def _paint_indicator(painter: QPainter, option: QStyleOption, *, radio: bool) -> None:
    """Paint a check box or radio button like a recessed glass well."""
    colors = glass_colors()
    state = option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    radius = rect.height() / 2 if radio else _CHECK_RADIUS

    on = bool(state & QStyle.StateFlag.State_On)
    partial = bool(state & QStyle.StateFlag.State_NoChange)
    hovered = bool(state & QStyle.StateFlag.State_MouseOver)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # A filled accent well when set, otherwise a faint recess
    if on or partial:
        painter.setBrush(colors.accent)
        border = _tint(colors.ink, 0.30)
    else:
        painter.setBrush(_tint(colors.well, 0.28))
        border = _tint(colors.accent, 0.8) if hovered else _tint(colors.ink, 0.06)
    painter.setPen(QPen(border, 1.0))
    painter.drawRoundedRect(rect, radius, radius)

    if on or partial:
        kind = 'dot' if radio else 'dash' if partial else 'check'
        mark = _mark(kind, QRectF(option.rect))  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        if kind == 'dot':
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colors.accent_text)
        else:
            pen = QPen(colors.accent_text, 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(mark)

    painter.restore()


class GlassPresentation:
    """The parts of Glass that the application style draws."""

    @staticmethod
    def draw_primitive(
        element: QStyle.PrimitiveElement,
        option: QStyleOption,
        painter: QPainter,
        widget: QWidget | None,
    ) -> bool:
        match element:
            case _PE.PE_FrameFocusRect if isinstance(widget, QAbstractItemView) and not isinstance(
                widget, QHeaderView
            ):
                paint_item_focus(painter, option, widget)
            case _PE.PE_IndicatorCheckBox | _PE.PE_IndicatorItemViewItemCheck:
                _paint_indicator(painter, option, radio=False)
            case _PE.PE_IndicatorRadioButton:
                _paint_indicator(painter, option, radio=True)
            case _:
                return False
        return True

    @staticmethod
    def pixel_metric(
        metric: QStyle.PixelMetric, option: QStyleOption | None, widget: QWidget | None
    ) -> int | None:
        del metric, option, widget
        return None
