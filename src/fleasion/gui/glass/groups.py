"""Glass group boxes and frames: rounded panes and recessed wells."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QScrollArea, QStyle, QStyleOptionGroupBox

from .style import RADIUS, glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOption, QStyleOptionComplex, QWidget


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


#
# Frames
#


# A raised panel is a pane, like a group box; a framed scroll area is a recessed well
_PANE_RADIUS: Final = 12.0
_WELL_RADIUS: Final = 10.0


def frame_kind(widget: QWidget | None) -> str | None:
    """Whether Glass draws a frame as a pane or a well, or leaves it to Fusion."""
    if isinstance(widget, QScrollArea) and widget.frameShape() != QFrame.Shape.NoFrame:
        return 'well'
    if (
        isinstance(widget, QFrame)
        and not isinstance(widget, QAbstractScrollArea)
        and widget.frameShape() == QFrame.Shape.StyledPanel
        and widget.frameShadow() == QFrame.Shadow.Raised
    ):
        return 'pane'
    return None


def paint_frame(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    kind = frame_kind(widget)
    if kind is None:
        return False

    colors = glass_colors()
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    radius = _PANE_RADIUS if kind == 'pane' else _WELL_RADIUS
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if kind == 'pane':
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0, _tint(colors.ink, 0.14))
        rim.setColorAt(min(1.0, radius / max(rect.height(), 1)), _tint(colors.ink, 0.09))
        rim.setColorAt(1, _tint(colors.ink, 0.09))
        painter.setPen(QPen(rim, 1.0))
        painter.setBrush(_tint(colors.ink, 0.06))
    else:
        painter.setPen(QPen(_tint(colors.ink, 0.09), 1.0))
        painter.setBrush(_tint(colors.well, 0.26))
    painter.drawRoundedRect(rect, radius, radius)
    painter.restore()
    return True


#
# Splitters
#


# A short rounded grip in the middle of the handle; the rest stays clear
_GRIP_LENGTH: Final = 32.0
_GRIP_THICKNESS: Final = 3.0


def paint_splitter(painter: QPainter, option: QStyleOption) -> bool:
    """A grip that brightens under the pointer and takes the accent while dragged."""
    state = option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    colors = glass_colors()
    if state & QStyle.StateFlag.State_Sunken:
        color = _tint(colors.accent, 0.85)
    elif state & QStyle.StateFlag.State_MouseOver:
        color = _tint(colors.ink, 0.34)
    else:
        color = _tint(colors.ink, 0.16)

    # A horizontal splitter lays its panes side by side, so its grip stands upright
    rect = QRectF(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    upright = bool(state & QStyle.StateFlag.State_Horizontal)
    thickness = min(_GRIP_THICKNESS, rect.width() if upright else rect.height())
    length = min(_GRIP_LENGTH, rect.height() if upright else rect.width())
    grip = QRectF(0, 0, thickness, length) if upright else QRectF(0, 0, length, thickness)
    grip.moveCenter(rect.center())

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(grip, thickness / 2, thickness / 2)
    painter.restore()
    return True
