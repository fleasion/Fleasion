"""Glass item views: translucent wells, tinted selections and quiet headers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QMargins, QObject, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QStyle,
    QStyleOptionHeader,
    QStyleOptionViewItem,
    QTableView,
    QTreeView,
)
from shiboken6 import isValid

from .style import glass_colors

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtWidgets import QStyleOption, QWidget


_SE = QStyle.SubElement
_State = QStyle.StateFlag

# Items are padded inside their cells, as the panes are
_ITEM_PADDING: Final = QSize(4, 2)

# Column headers form a faint strip; row numbers sit on the view itself
_HEADER_PADDING: Final = 4
_HEADER_FILL: Final = 0.045
_HEADER_RULE: Final = 0.10


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def grid_color() -> QColor:
    """Fusion rules table grids with the view background darkened by 20%."""
    return _tint(glass_colors().well.darker(120), 0.30)


def header_strip_color() -> QColor:
    return _tint(glass_colors().ink, _HEADER_FILL)


def header_rule_color() -> QColor:
    return _tint(glass_colors().ink, _HEADER_RULE)


#
# Surfaces
#


def well_color() -> QColor:
    """The translucent well behind every item view, painted by the rounded effect."""
    return _tint(glass_colors().well, 0.30)


def stripe_color() -> QColor:
    return _tint(glass_colors().ink, 0.025)


def selection_color(view: QWidget | None, palette: QPalette) -> QColor:
    # A table's band runs across every column, so it is a little stronger to
    # read as one row
    accent = palette.color(QPalette.ColorRole.Accent)
    return _tint(accent, 0.28 if isinstance(view, QTableView) else 0.24)


class ViewSurface(QObject):
    """Stops one viewport filling its background while Glass paints the well behind it.

    Editors and cell widgets inherit the view's palette, so the view keeps its
    opaque Base color for them and only the viewport itself stops filling.
    """

    def __init__(self, viewport: QWidget) -> None:
        super().__init__(viewport)
        self._viewport = viewport
        self._filled = viewport.autoFillBackground()
        viewport.setAutoFillBackground(False)

    @staticmethod
    def attach(view: QWidget) -> None:
        """Clear the viewports of an item view and of its headers once."""
        if not isinstance(view, QAbstractItemView) or isinstance(view, QHeaderView):
            return
        viewports = [view.viewport()]
        if isinstance(view, QTreeView):
            viewports.append(view.header().viewport())
        elif isinstance(view, QTableView):
            viewports += [view.horizontalHeader().viewport(), view.verticalHeader().viewport()]
        for viewport in viewports:
            if not viewport.findChild(
                ViewSurface, options=Qt.FindChildOption.FindDirectChildrenOnly
            ):
                ViewSurface(viewport)

    @staticmethod
    def detach_all(widgets: list[QWidget]) -> None:
        for widget in widgets:
            if not isValid(widget):
                continue
            for surface in widget.findChildren(
                ViewSurface, options=Qt.FindChildOption.FindDirectChildrenOnly
            ):
                surface.detach()

    def detach(self) -> None:
        if isValid(self._viewport):
            self._viewport.setAutoFillBackground(self._filled)
            self._viewport.update()
        self.deleteLater()


#
# Items
#


def paint_item_panel(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    """An item's own background, its selection band, or a faint hover tint."""
    if not isinstance(option, QStyleOptionViewItem):
        return False

    state = option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    rect = QRect(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    if option.backgroundBrush.style() != Qt.BrushStyle.NoBrush:
        painter.fillRect(rect, option.backgroundBrush)
    if state & _State.State_Selected:
        painter.fillRect(rect, selection_color(widget, option.palette))  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    elif state & _State.State_MouseOver and state & _State.State_Enabled:
        painter.fillRect(rect, _tint(glass_colors().ink, 0.05))
    return True


def paint_item_row(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    """Alternate rows carry a faint stripe; a selected row's band reaches its tree gutter."""
    if not isinstance(option, QStyleOptionViewItem):
        return False
    rect = QRect(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    if option.features & QStyleOptionViewItem.ViewItemFeature.Alternate:
        painter.fillRect(rect, stripe_color())
    # Items paint their own band; only a tree's branch gutter needs the row to
    if isinstance(widget, QTreeView) and option.state & _State.State_Selected:  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        painter.fillRect(rect, selection_color(widget, option.palette))  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    return True


def item_option(option: QStyleOption) -> QStyleOption | None:
    """Selected items keep the normal text color on the tinted band."""
    if not isinstance(option, QStyleOptionViewItem):
        return None
    changed = QStyleOptionViewItem(option)
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    palette.setColor(QPalette.ColorRole.HighlightedText, palette.color(QPalette.ColorRole.Text))
    changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    return changed


def item_rect(element: QStyle.SubElement, rect: QRect, option: QStyleOption) -> QRect | None:
    """Move an item's parts inside its padding."""
    if not isinstance(option, QStyleOptionViewItem):
        return None
    pad = _ITEM_PADDING.width()
    match element:
        case _SE.SE_ItemViewItemText:
            return rect.adjusted(pad, 0, -pad, 0)
        case _SE.SE_ItemViewItemDecoration | _SE.SE_ItemViewItemCheckIndicator:
            rtl = option.direction == Qt.LayoutDirection.RightToLeft  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            return rect.translated(-pad if rtl else pad, 0)
        case _:
            return None


def item_size(result: QSize) -> QSize:
    pad = _ITEM_PADDING
    return result.grownBy(QMargins(pad.width(), pad.height(), pad.width(), pad.height()))


#
# Headers
#


def _vertical(option: QStyleOption) -> bool:
    return isinstance(option, QStyleOptionHeader) and option.orientation == Qt.Orientation.Vertical


def paint_header_section(painter: QPainter, option: QStyleOption) -> bool:
    if not isinstance(option, QStyleOptionHeader):
        return False

    rect = QRectF(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    painter.save()
    if _vertical(option):
        # Row numbers: plain cells ruled like the grid, banded when their row is selected
        if option.state & _State.State_On:  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            accent = option.palette.color(QPalette.ColorRole.Accent)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            painter.fillRect(rect, _tint(accent, 0.11))
        painter.setPen(QPen(grid_color(), 1.0))
        painter.drawLine(
            QPointF(rect.right() - 0.5, rect.top()), QPointF(rect.right() - 0.5, rect.bottom())
        )
        painter.drawLine(
            QPointF(rect.left(), rect.bottom() - 0.5),
            QPointF(rect.right(), rect.bottom() - 0.5),
        )
    else:
        painter.fillRect(rect, header_strip_color())
        painter.setPen(QPen(header_rule_color(), 1.0))
        painter.drawLine(
            QPointF(rect.left(), rect.bottom() - 0.5),
            QPointF(rect.right(), rect.bottom() - 0.5),
        )
    painter.restore()
    return True


def paint_header_label(
    painter: QPainter, option: QStyleOption, fusion: Callable[[QStyleOption], None]
) -> bool:
    """Muted column titles; row numbers in input text without Qt's bold highlight."""
    if not isinstance(option, QStyleOptionHeader):
        return False

    changed = QStyleOptionHeader(option)

    # Centre the label clear of the section's rules
    vertical = _vertical(option)
    changed.rect = QRect(option.rect).adjusted(0, 0, -1 if vertical else 0, -1)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    text = palette.color(QPalette.ColorRole.Text)
    palette.setColor(
        QPalette.ColorRole.ButtonText, text if _vertical(option) else _tint(text, 0.62)
    )
    changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field

    painter.save()
    if _vertical(option):
        font = QFont(painter.font())
        font.setBold(False)
        painter.setFont(font)
    fusion(changed)
    painter.restore()
    return True


def header_size(option: QStyleOption, result: QSize) -> QSize | None:
    """Pad header sections and leave room for their rule."""
    if not isinstance(option, QStyleOptionHeader):
        return None
    extra = 2 * _HEADER_PADDING + 1
    if _vertical(option):
        return QSize(result.width() + extra, result.height())
    return QSize(result.width(), result.height() + extra)
