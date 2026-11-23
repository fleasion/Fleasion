"""Glass group boxes: a rounded pane with its muted title above it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyleOptionGroupBox

from .style import RADIUS, glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOptionComplex, QWidget


_SC = QStyle.SubControl

# The title sits this far down, in a band 1.4 lines tall above the pane
_TITLE_TOP: Final = 3
_TITLE_BAND: Final = 1.4


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _frame_top(option: QStyleOptionGroupBox) -> int:
    if not option.text:
        return 0
    return round(option.fontMetrics.height() * _TITLE_BAND)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def group_box_rect(
    option: QStyleOptionComplex, sub_control: QStyle.SubControl, widget: QWidget | None
) -> QRect | None:
    del widget
    if not isinstance(option, QStyleOptionGroupBox):
        return None

    rect = QRect(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    top = _frame_top(option)
    frame = rect.adjusted(0, top, 0, 0)
    match sub_control:
        case _SC.SC_GroupBoxFrame:
            return frame
        case _SC.SC_GroupBoxContents:
            return frame.adjusted(1, 1, -1, -1)
        case _SC.SC_GroupBoxLabel:
            # Titles start at the pane's edge, as Fusion places them
            metrics = option.fontMetrics  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            label = QRect(
                rect.left(),
                rect.top() + _TITLE_TOP,
                metrics.horizontalAdvance(option.text) + 1,
                metrics.height(),
            )
            return QStyle.visualRect(option.direction, rect, label)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        case _:
            return None


def paint_group_box(painter: QPainter, option: QStyleOptionComplex, widget: QWidget | None) -> bool:
    if not isinstance(option, QStyleOptionGroupBox):
        return False
    frame = group_box_rect(option, _SC.SC_GroupBoxFrame, widget)
    label = group_box_rect(option, _SC.SC_GroupBoxLabel, widget)
    if frame is None or label is None:
        return False

    colors = glass_colors()
    pane = QRectF(frame).adjusted(0.5, 0.5, -0.5, -0.5)
    rim = QLinearGradient(pane.topLeft(), pane.bottomLeft())
    rim.setColorAt(0, _tint(colors.ink, 0.13))
    rim.setColorAt(min(1.0, RADIUS / max(pane.height(), 1)), _tint(colors.ink, 0.08))
    rim.setColorAt(1, _tint(colors.ink, 0.08))

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(rim, 1.0))
    painter.setBrush(_tint(colors.ink, 0.06))
    painter.drawRoundedRect(pane, RADIUS, RADIUS)

    if option.text:
        text = option.palette.color(QPalette.ColorRole.WindowText)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        painter.setPen(_tint(text, 0.62))
        painter.drawText(
            label, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, option.text
        )
    painter.restore()
    return True
