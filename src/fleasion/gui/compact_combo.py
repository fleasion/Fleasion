"""A borderless selector that blends into a table cell."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter

from fleasion.widgets.no_wheel import DropdownComboBox

if TYPE_CHECKING:
    from PySide6.QtGui import QPaintEvent
    from PySide6.QtWidgets import QWidget


class CompactCellComboBox(DropdownComboBox):
    """A borderless selector that blends into a table cell.

    Parameters
    ----------
    parent
        The widget that owns the selector, usually the table viewport.
    text_inset
        Space between the cell's left edge and the value.
    arrow_inset
        Distance from the cell's right edge to the centre of the arrow.
    """

    def __init__(
        self, parent: QWidget | None = None, *, text_inset: int = 4, arrow_inset: int = 10
    ) -> None:
        super().__init__(parent)
        self._text_inset = text_inset
        self._arrow_inset = arrow_inset

    @override
    def paintEvent(self, e: QPaintEvent) -> None:
        del e

        # The editor overlaps a table item that still paints its own text. Keep
        # the entire editor opaque, even when its popup owns focus
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if self.hasFocus() or self.underMouse():
            painter.fillRect(self.rect(), self.palette().alternateBase())

        painter.setPen(self.palette().text().color())
        painter.drawText(
            self.rect().adjusted(self._text_inset, 0, -18, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.currentText(),
        )

        # A small chevron in place of the full dropdown button
        arrow_x = self.width() - self._arrow_inset
        arrow_y = self.height() // 2
        painter.drawLine(arrow_x - 3, arrow_y - 1, arrow_x, arrow_y + 2)
        painter.drawLine(arrow_x, arrow_y + 2, arrow_x + 3, arrow_y - 1)
