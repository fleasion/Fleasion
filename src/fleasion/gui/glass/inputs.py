"""Glass line edits, spin boxes and combo boxes: recessed wells with chevron buttons."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QLineEdit,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionFrame,
    QStyleOptionSpinBox,
)

from .style import glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOption, QStyleOptionComplex, QWidget


_SC = QStyle.SubControl
_State = QStyle.StateFlag

_RADIUS: Final = 8.0

# Text starts this far in: the border plus the padding
_TEXT_INSET: Final = 9

# The drop-down area at the right of a combo box, and its chevron
_ARROW_WIDTH: Final = 24
_ARROW_MARGIN: Final = 3
_ARROW_SIZE: Final = 14

# Selectors with this property reserve less room for the arrow
_COMPACT_PROPERTY: Final = 'fflagBrowserCompact'
_COMPACT_ARROW_WIDTH: Final = 16
_COMPACT_ARROW_MARGIN: Final = 1
_COMPACT_ARROW_SIZE: Final = 12

# Gap kept between a combo box's text and its drop-down area
_EDIT_GAP: Final = 2

# Up and down buttons of a spin box, inset from its border
_SPIN_BUTTON_WIDTH: Final = 20
_SPIN_BUTTON_MARGIN: Final = 3
_SPIN_ARROW_SIZE: Final = 11
_SPIN_BUTTON_RADIUS: Final = 6.0


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _state(option: QStyleOption) -> _State:
    return option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def _rect(option: QStyleOption) -> QRect:
    return option.rect  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def _palette(option: QStyleOption) -> QPalette:
    return option.palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


#
# Painting
#


def _paint_well(painter: QPainter, option: QStyleOption) -> None:
    """The recessed field behind every input; the border lights up on hover and focus."""
    colors = glass_colors()
    state = _state(option)
    if not state & _State.State_Enabled:
        border = _tint(colors.ink, 0.06)
    elif state & _State.State_HasFocus:
        border = _tint(colors.accent, 0.85)
    elif state & _State.State_MouseOver:
        border = _tint(colors.ink, 0.14)
    else:
        border = _tint(colors.ink, 0.06)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(border, 1.0))
    painter.setBrush(_tint(colors.well, 0.28))
    painter.drawRoundedRect(QRectF(_rect(option)).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS)
    painter.restore()


def _paint_chevron(
    painter: QPainter, area: QRect, color: QColor, *, size: float, up: bool = False
) -> None:
    """A rounded chevron centred in an area, drawn on the 16px glyph grid."""
    scale = size / 16
    origin = QRectF(area).center() - QPointF(size / 2, size / 2)

    def point(x: float, y: float) -> QPointF:
        return origin + QPointF(x * scale, y * scale)

    path = QPainterPath()
    if up:
        path.moveTo(point(4, 10))
        path.lineTo(point(8, 6))
        path.lineTo(point(12, 10))
    else:
        path.moveTo(point(4, 6))
        path.lineTo(point(8, 10))
        path.lineTo(point(12, 6))

    pen = QPen(color, 1.8 * scale)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


def paint_line_edit(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    """Paint a framed line edit; the frameless edits inside other inputs stay clear."""
    if not isinstance(widget, QLineEdit) or not isinstance(option, QStyleOptionFrame):
        return False

    # The spin box or combo box around an inner edit already paints the well
    if isinstance(widget.parentWidget(), QAbstractSpinBox | QComboBox):
        return True
    if option.lineWidth <= 0:
        return False
    _paint_well(painter, option)
    return True


def paint_combo_box(painter: QPainter, option: QStyleOptionComplex, widget: QWidget | None) -> bool:
    if not isinstance(option, QStyleOptionComboBox):
        return False
    if option.frame:
        _paint_well(painter, option)

    if (arrow := combo_box_rect(option, _SC.SC_ComboBoxArrow, widget)) is not None:
        color = _palette(option).color(QPalette.ColorRole.Text)
        size = _COMPACT_ARROW_SIZE if _is_compact(widget) else _ARROW_SIZE
        _paint_chevron(painter, arrow, color, size=size)
    return True


def paint_spin_box(painter: QPainter, option: QStyleOptionComplex, widget: QWidget | None) -> bool:
    if not isinstance(option, QStyleOptionSpinBox):
        return False
    if option.frame:
        _paint_well(painter, option)
    if option.buttonSymbols == QAbstractSpinBox.ButtonSymbols.NoButtons:
        return True

    colors = glass_colors()
    state = _state(option)
    text = _palette(option).color(QPalette.ColorRole.Text)
    steps = option.stepEnabled
    for sub_control, step, up in (
        (_SC.SC_SpinBoxUp, QAbstractSpinBox.StepEnabledFlag.StepUpEnabled, True),
        (_SC.SC_SpinBoxDown, QAbstractSpinBox.StepEnabledFlag.StepDownEnabled, False),
    ):
        button = spin_box_rect(option, sub_control, widget)
        if button is None:
            continue
        enabled = bool(state & _State.State_Enabled and steps & step)

        # A faint pill under the pointer
        if enabled and state & _State.State_MouseOver and option.activeSubControls & sub_control:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_tint(colors.ink, 0.10))
            painter.drawRoundedRect(QRectF(button), _SPIN_BUTTON_RADIUS, _SPIN_BUTTON_RADIUS)
            painter.restore()

        _paint_chevron(
            painter, button, text if enabled else _tint(text, 0.35), size=_SPIN_ARROW_SIZE, up=up
        )
    return True


#
# Geometry
#


def _is_compact(widget: QWidget | None) -> bool:
    return widget is not None and widget.property(_COMPACT_PROPERTY) in {True, 'true'}


def combo_box_rect(
    option: QStyleOptionComplex, sub_control: QStyle.SubControl, widget: QWidget | None
) -> QRect | None:
    rect = _rect(option)
    compact = _is_compact(widget)
    width = _COMPACT_ARROW_WIDTH if compact else _ARROW_WIDTH
    margin = _COMPACT_ARROW_MARGIN if compact else _ARROW_MARGIN
    arrow = QRect(rect.right() - margin - width + 1, rect.top(), width, rect.height())

    match sub_control:
        case _SC.SC_ComboBoxArrow:
            return QStyle.visualRect(option.direction, rect, arrow)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        case _SC.SC_ComboBoxEditField:
            edit = QRect(
                rect.left() + _TEXT_INSET,
                rect.top() + 1,
                max(0, arrow.left() - _EDIT_GAP - rect.left() - _TEXT_INSET),
                rect.height() - 2,
            )
            return QStyle.visualRect(option.direction, rect, edit)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        case _SC.SC_ComboBoxFrame | _SC.SC_ComboBoxListBoxPopup:
            return QRect(rect)
        case _:
            return None


def spin_box_rect(
    option: QStyleOptionComplex, sub_control: QStyle.SubControl, widget: QWidget | None
) -> QRect | None:
    del widget
    rect = _rect(option)
    no_buttons = (
        isinstance(option, QStyleOptionSpinBox)
        and option.buttonSymbols == QAbstractSpinBox.ButtonSymbols.NoButtons
    )
    inner = rect.height() - 2 * _SPIN_BUTTON_MARGIN
    left = rect.right() - _SPIN_BUTTON_MARGIN - _SPIN_BUTTON_WIDTH + 1

    match sub_control:
        case _SC.SC_SpinBoxUp:
            button = QRect(left, rect.top() + _SPIN_BUTTON_MARGIN, _SPIN_BUTTON_WIDTH, inner // 2)
        case _SC.SC_SpinBoxDown:
            top = rect.top() + _SPIN_BUTTON_MARGIN + inner // 2
            button = QRect(left, top, _SPIN_BUTTON_WIDTH, inner - inner // 2)
        case _SC.SC_SpinBoxEditField:
            right = rect.right() - 1 if no_buttons else left - _EDIT_GAP
            button = QRect(
                rect.left() + _TEXT_INSET,
                rect.top() + 1,
                right - rect.left() - _TEXT_INSET + 1,
                rect.height() - 2,
            )
        case _SC.SC_SpinBoxFrame:
            return QRect(rect)
        case _:
            return None
    return QStyle.visualRect(option.direction, rect, button)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def line_edit_contents(option: QStyleOption, widget: QWidget | None, rect: QRect) -> QRect | None:
    """Pad a framed line edit's text like the other inputs."""
    if not isinstance(widget, QLineEdit) or not isinstance(option, QStyleOptionFrame):
        return None
    if option.lineWidth <= 0:
        return None

    padded = QRect(rect)
    outer = _rect(option)
    padded.setLeft(outer.left() + _TEXT_INSET)
    padded.setRight(outer.right() - _TEXT_INSET)
    return padded


def input_size(
    contents_type: QStyle.ContentsType, size: QSize, widget: QWidget | None, result: QSize
) -> QSize | None:
    """Widen inputs for their padding and their drop-down or spin buttons."""
    match contents_type:
        case QStyle.ContentsType.CT_ComboBox if isinstance(widget, QComboBox):
            # Compact selectors are sized by their owner; this is the natural size
            extra = _TEXT_INSET + _EDIT_GAP + _ARROW_WIDTH + _ARROW_MARGIN + 6
        case QStyle.ContentsType.CT_SpinBox:
            extra = _TEXT_INSET + _EDIT_GAP + _SPIN_BUTTON_WIDTH + _SPIN_BUTTON_MARGIN + 2
        case QStyle.ContentsType.CT_LineEdit if isinstance(widget, QLineEdit) and not isinstance(
            widget.parentWidget(), QAbstractSpinBox | QComboBox
        ):
            return QSize(result.width() + 2 * _TEXT_INSET, result.height())
        case _:
            return None
    return QSize(size.width() + extra, result.height())
