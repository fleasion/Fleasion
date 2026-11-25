"""A layout that places items left to right and wraps them onto new rows."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QWidgetItem

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLayoutItem, QWidget


class FlowLayout(QLayout):
    """Wrap fixed-size items, like swatches, to the available width."""

    def __init__(self, parent: QWidget | None = None, *, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self.setContentsMargins(0, 0, 0, 0)

    @override
    def addItem(self, arg__1: QLayoutItem, /) -> None:
        self._items.append(arg__1)

    def add(self, widget: QWidget) -> None:
        self.addChildWidget(widget)
        self.addItem(QWidgetItem(widget))

    @override
    def count(self) -> int:
        return len(self._items)

    @override
    def itemAt(self, index: int, /) -> QLayoutItem | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._items[index] if 0 <= index < len(self._items) else None

    @override
    def takeAt(self, index: int, /) -> QLayoutItem | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    @override
    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    @override
    def hasHeightForWidth(self) -> bool:
        return True

    @override
    def heightForWidth(self, arg__1: int, /) -> int:
        return self._arrange(QRect(0, 0, arg__1, 0), apply=False)

    @override
    def setGeometry(self, arg__1: QRect, /) -> None:
        super().setGeometry(arg__1)
        self._arrange(arg__1, apply=True)

    @override
    def sizeHint(self) -> QSize:
        return self.minimumSize()

    @override
    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, row_height = area.x(), area.y(), 0
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x, y = area.x(), y + row_height + self._spacing
                row_height = 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
            row_height = max(row_height, hint.height())
        return y + row_height - rect.y() + margins.bottom()
