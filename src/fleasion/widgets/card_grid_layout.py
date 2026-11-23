"""A layout that places equal cards in a grid whose column count follows its width."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QWidgetItem

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLayoutItem, QWidget


class CardGridLayout(QLayout):
    """
    Place fixed-height cards in equal columns chosen from the available width.

    The column count is derived inside the layout pass itself and reported
    through height-for-width, so a parent sizes the grid for its real width on
    the first pass instead of settling over several deferred relayouts.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        card_height: int,
        min_width: int,
        target_width: int,
        max_columns: int,
        spacing: int = 6,
    ) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._card_height = card_height
        self._min_width = min_width
        self._target_width = target_width
        self._max_columns = max_columns
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
        return Qt.Orientation.Horizontal

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
        columns = self._visible_count(cap=self._max_columns)
        width = columns * self._target_width + (columns - 1) * self._spacing
        return QSize(width, self.heightForWidth(width))

    @override
    def minimumSize(self) -> QSize:
        margins = self.contentsMargins()
        return QSize(
            self._min_width + margins.left() + margins.right(),
            self._card_height + margins.top() + margins.bottom(),
        )

    def columns_for(self, width: int) -> int:
        """Return how many columns fit the given content width."""
        fit = (width + self._spacing) // self._target_width
        return max(1, min(self._visible_count(cap=self._max_columns), fit))

    def _visible_count(self, *, cap: int) -> int:
        return max(1, min(cap, sum(not item.isEmpty() for item in self._items)))

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        visible = [item for item in self._items if not item.isEmpty()]
        columns = self.columns_for(area.width())
        rows = math.ceil(len(visible) / columns)

        # Spread the rounding remainder so every gap stays exactly the spacing
        if apply:
            cell = (area.width() - (columns - 1) * self._spacing) / columns
            for index, item in enumerate(visible):
                row, column = divmod(index, columns)
                left = round(column * (cell + self._spacing))
                right = round(column * (cell + self._spacing) + cell)
                top = row * (self._card_height + self._spacing)
                item.setGeometry(
                    QRect(area.x() + left, area.y() + top, right - left, self._card_height)
                )

        content = rows * self._card_height + max(0, rows - 1) * self._spacing
        return content + margins.top() + margins.bottom()
