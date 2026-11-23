"""Glass primitives drawn by the application style instead of the style sheet."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QToolButton,
)

from .inputs import (
    combo_box_rect,
    input_size,
    line_edit_contents,
    paint_combo_box,
    paint_line_edit,
    paint_spin_box,
    spin_box_rect,
)
from .itemview import paint_item_focus
from .scrollbars import EXTENT, SLIDER_MIN, paint_scroll_bar, scroll_bar_rect
from .style import glass_colors

if TYPE_CHECKING:
    from PySide6.QtCore import QRect, QSize
    from PySide6.QtWidgets import QStyleOption, QStyleOptionComplex, QWidget

    from .style import GlassColors


_PE = QStyle.PrimitiveElement
_CE = QStyle.ControlElement
_CC = QStyle.ComplexControl
_State = QStyle.StateFlag

# Marks are drawn on a 16px grid centred in the indicator, as the old glyphs were
_MARK_GRID: Final = 16
_CHECK_RADIUS: Final = 5.0
_BUTTON_RADIUS: Final = 8.0


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _state(option: QStyleOption) -> _State:
    return option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def _accent(option: QStyleOption) -> tuple[QColor, QColor]:
    """The accent and the text drawn on it, from the palette the theme applied."""
    palette: QPalette = option.palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    return palette.color(QPalette.ColorRole.Accent), palette.color(
        QPalette.ColorRole.HighlightedText
    )


#
# Check boxes and radio buttons
#


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
    accent, on_accent = _accent(option)
    state = _state(option)
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    radius = rect.height() / 2 if radio else _CHECK_RADIUS

    on = bool(state & _State.State_On)
    partial = bool(state & _State.State_NoChange)
    hovered = bool(state & _State.State_MouseOver)

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # A filled accent well when set, otherwise a faint recess
    if on or partial:
        painter.setBrush(accent)
        border = _tint(colors.ink, 0.30)
    else:
        painter.setBrush(_tint(colors.well, 0.28))
        border = _tint(accent, 0.8) if hovered else _tint(colors.ink, 0.06)
    painter.setPen(QPen(border, 1.0))
    painter.drawRoundedRect(rect, radius, radius)

    if on or partial:
        kind = 'dot' if radio else 'dash' if partial else 'check'
        mark = _mark(kind, QRectF(option.rect))  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        if kind == 'dot':
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(on_accent)
        else:
            pen = QPen(on_accent, 2.2)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(mark)

    painter.restore()


#
# Buttons
#


def _button_features(option: QStyleOption, widget: QWidget | None) -> tuple[bool, bool]:
    """Whether a push button is flat and whether it is the dialog's default button."""
    if isinstance(option, QStyleOptionButton):
        features = option.features
        return (
            bool(features & QStyleOptionButton.ButtonFeature.Flat),
            bool(features & QStyleOptionButton.ButtonFeature.DefaultButton),
        )
    if isinstance(widget, QPushButton):
        return widget.isFlat(), widget.isDefault()
    return False, False


def _button_colors(
    colors: GlassColors, accent: QColor, state: _State, *, flat: bool, default: bool
) -> tuple[QColor | None, QColor | None, QColor | None]:
    """Fill, side border and top border of a button; None leaves a part unpainted."""
    pressed = bool(state & (_State.State_Sunken | _State.State_On))
    hovered = bool(state & _State.State_MouseOver)

    # The first matching state wins, as the later rule did in the old style sheet
    if not state & _State.State_Enabled:
        border = _tint(colors.ink, 0.06)
        return _tint(colors.ink, 0.03), border, border
    if default:
        border = _tint(colors.ink, 0.30)
        return accent, border, border
    if pressed:
        border = _tint(accent, 0.55)
        return _tint(accent, 0.30), border, border
    if flat:
        # Flat buttons only show a faint fill under the pointer
        return (_tint(colors.ink, 0.08) if hovered else None), None, None

    fill = _tint(colors.ink, 0.11 if hovered else 0.06)
    if state & _State.State_HasFocus:
        border = _tint(accent, 0.95)
        return fill, border, border
    return fill, _tint(colors.ink, 0.08), _tint(colors.ink, 0.20 if hovered else 0.13)


def _paint_button(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> None:
    """Paint a push or tool button as a rounded glass pane."""
    flat, default = _button_features(option, widget)
    accent, _on_accent = _accent(option)
    fill, side, top = _button_colors(
        glass_colors(), accent, _state(option), flat=flat, default=default
    )
    if fill is None and side is None:
        return

    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    if side is None or top is None:
        painter.setPen(Qt.PenStyle.NoPen)
    else:
        # A brighter top edge that blends into the sides below the corners
        rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        rim.setColorAt(0, top)
        rim.setColorAt(min(1.0, _BUTTON_RADIUS / max(rect.height(), 1)), side)
        rim.setColorAt(1, side)
        painter.setPen(QPen(rim, 1.0))
    painter.drawRoundedRect(rect, _BUTTON_RADIUS, _BUTTON_RADIUS)
    painter.restore()


def _button_label_option(option: QStyleOption, widget: QWidget | None) -> QStyleOption | None:
    """Accent text on the default button and muted text on resting flat buttons."""
    if not isinstance(option, QStyleOptionButton):
        return None

    state = _state(option)
    flat, default = _button_features(option, widget)
    if not state & _State.State_Enabled:
        return None
    if default:
        _accent_color, text = _accent(option)
    elif flat and not state & (_State.State_MouseOver | _State.State_Sunken | _State.State_On):
        text = _tint(option.palette.color(QPalette.ColorRole.ButtonText), 0.62)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    else:
        return None

    changed = QStyleOptionButton(option)
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    palette.setColor(QPalette.ColorRole.ButtonText, text)
    changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    return changed


#
# Registration
#


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
            case _PE.PE_PanelButtonCommand:
                _paint_button(painter, option, widget)
            case _PE.PE_PanelButtonTool if isinstance(widget, QToolButton):
                _paint_button(painter, option, widget)
            case _PE.PE_PanelLineEdit:
                return paint_line_edit(painter, option, widget)
            case _:
                return False
        return True

    @staticmethod
    def control_option(
        element: QStyle.ControlElement, option: QStyleOption, widget: QWidget | None
    ) -> QStyleOption | None:
        match element:
            case _CE.CE_PushButtonLabel:
                return _button_label_option(option, widget)
            case _CE.CE_ComboBoxLabel if isinstance(option, QStyleOptionComboBox):
                # A selector's value reads as input text, like the line edits beside it
                changed = QStyleOptionComboBox(option)
                palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
                palette.setColor(
                    QPalette.ColorRole.ButtonText, palette.color(QPalette.ColorRole.Text)
                )
                changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
                return changed
            case _:
                return None

    @staticmethod
    def draw_complex_control(
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        painter: QPainter,
        widget: QWidget | None,
    ) -> bool:
        match control:
            case _CC.CC_ComboBox:
                return paint_combo_box(painter, option, widget)
            case _CC.CC_SpinBox:
                return paint_spin_box(painter, option, widget)
            case _CC.CC_ScrollBar:
                return paint_scroll_bar(painter, option, widget)
            case _:
                return False

    @staticmethod
    def sub_control_rect(
        control: QStyle.ComplexControl,
        option: QStyleOptionComplex,
        sub_control: QStyle.SubControl,
        widget: QWidget | None,
    ) -> QRect | None:
        match control:
            case _CC.CC_ComboBox:
                return combo_box_rect(option, sub_control, widget)
            case _CC.CC_SpinBox:
                return spin_box_rect(option, sub_control, widget)
            case _CC.CC_ScrollBar:
                return scroll_bar_rect(option, sub_control, widget)
            case _:
                return None

    @staticmethod
    def sub_element_rect(
        element: QStyle.SubElement, option: QStyleOption, widget: QWidget | None, rect: QRect
    ) -> QRect | None:
        if element == QStyle.SubElement.SE_LineEditContents:
            return line_edit_contents(option, widget, rect)
        return None

    @staticmethod
    def size_from_contents(
        contents_type: QStyle.ContentsType,
        option: QStyleOption,
        size: QSize,
        widget: QWidget | None,
        result: QSize,
    ) -> QSize | None:
        del option
        return input_size(contents_type, size, widget, result)

    @staticmethod
    def pixel_metric(
        metric: QStyle.PixelMetric, option: QStyleOption | None, widget: QWidget | None
    ) -> int | None:
        del option, widget
        match metric:
            case QStyle.PixelMetric.PM_ScrollBarExtent:
                return EXTENT
            case QStyle.PixelMetric.PM_ScrollBarSliderMin:
                return SLIDER_MIN
            case _:
                return None
