"""Glass scroll bars: a slim rounded handle on a transparent track."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractItemView, QAbstractScrollArea, QStyle, QStyleOptionSlider

from .style import SCROLLBAR_CORNER_INSET, glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOptionComplex, QWidget


_SC = QStyle.SubControl
_State = QStyle.StateFlag

EXTENT: Final = 10
SLIDER_MIN: Final = 30
_RADIUS: Final = 3.0

# Space kept between the handle and the track's ends and sides
_END_MARGIN: Final = 4
_SIDE_MARGIN: Final = 2

# Set by the item-view chrome on a vertical bar that starts below a column header
BELOW_HEADER: Final = 'glassBelowHeader'


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _end_margins(widget: QWidget | None, *, vertical: bool) -> tuple[int, int]:
    """Margins at the start and end of the track.

    Inside rounded item views the ends stay clear of the corners, except
    that a bar already starting below a column header keeps the usual margin.
    """
    area = widget
    while area is not None and not isinstance(area, QAbstractScrollArea):
        area = area.parentWidget()
    if not isinstance(area, QAbstractItemView):
        return _END_MARGIN, _END_MARGIN

    start = SCROLLBAR_CORNER_INSET
    if vertical and widget is not None and widget.property(BELOW_HEADER):
        start = _END_MARGIN
    return start, SCROLLBAR_CORNER_INSET


def scroll_bar_rect(
    option: QStyleOptionComplex, sub_control: QStyle.SubControl, widget: QWidget | None
) -> QRect | None:
    if not isinstance(option, QStyleOptionSlider):
        return None

    rect = QRect(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    vertical = option.orientation == Qt.Orientation.Vertical
    start, end = _end_margins(widget, vertical=vertical)
    if vertical:
        groove = rect.adjusted(_SIDE_MARGIN, start, -_SIDE_MARGIN, -end)
        length = groove.height()
    else:
        groove = rect.adjusted(start, _SIDE_MARGIN, -end, -_SIDE_MARGIN)
        length = groove.width()

    # The handle covers the visible share of the range, but never less than the minimum
    span = option.maximum - option.minimum
    handle = length if span <= 0 else round(length * option.pageStep / (span + option.pageStep))
    handle = min(length, max(SLIDER_MIN, handle))
    offset = QStyle.sliderPositionFromValue(
        option.minimum, option.maximum, option.sliderPosition, length - handle, option.upsideDown
    )

    def along(begin: int, size: int) -> QRect:
        if vertical:
            return QRect(groove.left(), groove.top() + begin, groove.width(), size)
        return QRect(groove.left() + begin, groove.top(), size, groove.height())

    match sub_control:
        case _SC.SC_ScrollBarGroove:
            part = groove
        case _SC.SC_ScrollBarSlider:
            part = along(offset, handle)
        case _SC.SC_ScrollBarSubPage:
            part = along(0, offset)
        case _SC.SC_ScrollBarAddPage:
            part = along(offset + handle, length - offset - handle)
        case (
            _SC.SC_ScrollBarAddLine
            | _SC.SC_ScrollBarSubLine
            | _SC.SC_ScrollBarFirst
            | _SC.SC_ScrollBarLast
        ):
            # No step buttons; the whole track belongs to the handle
            return QRect()
        case _:
            return None
    return QStyle.visualRect(option.direction, rect, part)


def paint_scroll_bar(
    painter: QPainter, option: QStyleOptionComplex, widget: QWidget | None
) -> bool:
    if not isinstance(option, QStyleOptionSlider):
        return False
    handle = scroll_bar_rect(option, _SC.SC_ScrollBarSlider, widget)
    if handle is None or handle.isEmpty():
        return True

    # The handle brightens under the pointer and while dragged
    state = option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    active = bool(option.activeSubControls & _SC.SC_ScrollBarSlider) and bool(
        state & (_State.State_MouseOver | _State.State_Sunken)
    )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_tint(glass_colors().ink, 0.36 if active else 0.20))
    painter.drawRoundedRect(QRectF(handle), _RADIUS, _RADIUS)
    painter.restore()
    return True
