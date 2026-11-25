"""A rounded panel that paints itself from the palette."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent


# Tints of the text color, so the card reads on light, dark and Glass surfaces alike
_FILL: Final = 0.04
_HOVER_FILL: Final = 0.08
_BORDER: Final = 0.18
_RADIUS: Final = 8.0


class Card(QWidget):
    """A rounded panel with a faint fill that brightens under the pointer.

    The colors are tints of the palette's text color, so a theme change
    repaints the card without restyling it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # Repaint on enter and leave for the hover fill; one pixel stays clear for the border
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setContentsMargins(1, 1, 1, 1)

    @override
    def paintEvent(self, _event: QPaintEvent, /) -> None:
        ink = self.palette().windowText().color()
        fill = QColor(ink)
        fill.setAlphaF(_HOVER_FILL if self.underMouse() else _FILL)
        border = QColor(ink)
        border.setAlphaF(_BORDER)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(fill)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), _RADIUS, _RADIUS
        )
        painter.end()
