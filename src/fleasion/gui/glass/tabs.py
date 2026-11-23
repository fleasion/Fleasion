"""Glass tabs: frameless pills, with the selected tab raised like a pane."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QStyle, QStyleOptionTab

from .style import glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOption, QWidget


_State = QStyle.StateFlag
_PM = QStyle.PixelMetric
_RADIUS: Final = 8.0

# Fusion shifts and overlaps its tabs like folder tabs; pills sit side by side
FLAT_TAB_METRICS: Final = frozenset(
    {
        _PM.PM_TabBarTabShiftVertical,
        _PM.PM_TabBarTabShiftHorizontal,
        _PM.PM_TabBarTabOverlap,
        _PM.PM_TabBarBaseOverlap,
    }
)


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _state(option: QStyleOption) -> _State:
    return option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def paint_tab(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    del widget
    if not isinstance(option, QStyleOptionTab):
        return False

    colors = glass_colors()
    state = _state(option)
    if not state & _State.State_Enabled:
        return True

    selected = bool(state & _State.State_Selected)
    focused = bool(state & _State.State_HasFocus)
    if selected:
        fill: QColor | None = _tint(colors.ink, 0.11)
    elif state & _State.State_MouseOver:
        fill = _tint(colors.ink, 0.06)
    else:
        fill = None

    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    if focused:
        painter.setPen(QPen(_tint(option.palette.color(QPalette.ColorRole.Accent), 0.95), 1.0))  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    elif selected:
        # The same brighter top edge as the panes
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0, _tint(colors.ink, 0.13))
        rim.setColorAt(min(1.0, _RADIUS / max(rect.height(), 1)), _tint(colors.ink, 0.08))
        rim.setColorAt(1, _tint(colors.ink, 0.08))
        painter.setPen(QPen(rim, 1.0))
    else:
        painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(rect, _RADIUS, _RADIUS)
    painter.restore()
    return True


def tab_label_option(option: QStyleOption) -> QStyleOption | None:
    """Unselected tabs read muted until the pointer is over them."""
    if not isinstance(option, QStyleOptionTab):
        return None
    state = _state(option)
    if not state & _State.State_Enabled or state & (_State.State_Selected | _State.State_MouseOver):
        return None

    changed = QStyleOptionTab(option)
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    palette.setColor(
        QPalette.ColorRole.WindowText, _tint(palette.color(QPalette.ColorRole.WindowText), 0.62)
    )
    changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    return changed
