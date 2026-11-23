"""Glass sliders: a recessed track filled with the accent and a round handle."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QSlider, QStyle, QStyleOptionSlider

from .style import glass_colors

if TYPE_CHECKING:
    from PySide6.QtWidgets import QStyleOptionComplex, QWidget


_SC = QStyle.SubControl

HANDLE: Final = 20
_TRACK: Final = 6

# QSlider adds 5px per side with tick marks; they sit 2px off the handle
TICK_SPACE: Final = 5
_TICK_LENGTH: Final = 3


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def _horizontal(option: QStyleOptionComplex) -> QStyleOptionSlider | None:
    # Every slider in Fleasion is horizontal; vertical ones keep the Fusion look
    if isinstance(option, QStyleOptionSlider) and option.orientation == Qt.Orientation.Horizontal:
        return option
    return None


def _band(option: QStyleOptionSlider) -> QRect:
    """The strip holding the track and handle, above or below any tick marks."""
    rect = QRect(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    ticks = option.tickPosition
    top = rect.top()
    if ticks in {QSlider.TickPosition.TicksAbove, QSlider.TickPosition.TicksBothSides}:
        top += TICK_SPACE
    return QRect(
        rect.left(), top + max(0, (rect.height() - _band_height(option)) // 2), rect.width(), HANDLE
    )


def _band_height(option: QStyleOptionSlider) -> int:
    ticks = option.tickPosition
    extra = 0
    if ticks in {QSlider.TickPosition.TicksAbove, QSlider.TickPosition.TicksBothSides}:
        extra += TICK_SPACE
    if ticks in {QSlider.TickPosition.TicksBelow, QSlider.TickPosition.TicksBothSides}:
        extra += TICK_SPACE
    return HANDLE + extra


def slider_rect(
    option: QStyleOptionComplex, sub_control: QStyle.SubControl, widget: QWidget | None
) -> QRect | None:
    del widget
    slider = _horizontal(option)
    if slider is None:
        return None

    band = _band(slider)
    match sub_control:
        case _SC.SC_SliderGroove:
            return band
        case _SC.SC_SliderHandle:
            offset = QStyle.sliderPositionFromValue(
                slider.minimum,
                slider.maximum,
                slider.sliderPosition,
                band.width() - HANDLE,
                slider.upsideDown,
            )
            return QRect(band.left() + offset, band.top(), HANDLE, HANDLE)
        case _SC.SC_SliderTickmarks:
            return QRect(slider.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        case _:
            return None


def _paint_ticks(painter: QPainter, slider: QStyleOptionSlider, band: QRect, color: QColor) -> None:
    ticks = slider.tickPosition
    if ticks == QSlider.TickPosition.NoTicks:
        return
    interval = slider.tickInterval or slider.pageStep
    if interval <= 0:
        return

    painter.setPen(QPen(color, 1.0))
    value = slider.minimum
    while value <= slider.maximum:
        x = (
            band.left()
            + HANDLE / 2
            + QStyle.sliderPositionFromValue(
                slider.minimum, slider.maximum, value, band.width() - HANDLE, slider.upsideDown
            )
        )
        if ticks in {QSlider.TickPosition.TicksBelow, QSlider.TickPosition.TicksBothSides}:
            painter.drawLine(
                QPointF(x, band.bottom() + 2), QPointF(x, band.bottom() + 2 + _TICK_LENGTH)
            )
        if ticks in {QSlider.TickPosition.TicksAbove, QSlider.TickPosition.TicksBothSides}:
            painter.drawLine(QPointF(x, band.top() - 2), QPointF(x, band.top() - 2 - _TICK_LENGTH))
        value += interval


def paint_slider(painter: QPainter, option: QStyleOptionComplex, widget: QWidget | None) -> bool:
    slider = _horizontal(option)
    if slider is None:
        return False

    colors = glass_colors()
    band = _band(slider)
    handle = slider_rect(slider, _SC.SC_SliderHandle, widget)
    if handle is None:
        return False
    accent = slider.palette.color(QPalette.ColorRole.Accent)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Track, filled with the accent up to the handle
    track = QRectF(band.left(), band.center().y() - _TRACK / 2 + 0.5, band.width(), _TRACK)
    painter.setPen(QPen(_tint(colors.ink, 0.06), 1.0))
    painter.setBrush(_tint(colors.well, 0.28))
    painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), _TRACK / 2, _TRACK / 2)
    filled = QRectF(track)
    filled.setRight(handle.center().x())
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(_tint(accent, 0.9))
    painter.drawRoundedRect(filled, _TRACK / 2, _TRACK / 2)

    _paint_ticks(painter, slider, band, _tint(colors.ink, 0.25))

    # A pearl handle with a dark rim
    knob = QRectF(handle).adjusted(0.5, 0.5, -0.5, -0.5)
    shade = QLinearGradient(knob.topLeft(), knob.bottomLeft())
    shade.setColorAt(0, QColor('#ffffff'))
    shade.setColorAt(1, QColor('#dde2ec'))
    painter.setBrush(shade)
    painter.setPen(QPen(_tint(colors.ink, 0.8), 1.0))
    painter.drawEllipse(knob)

    painter.restore()
    return True
