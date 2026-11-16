"""Menu rows shared by dropdown menus and combo box popups."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from PySide6.QtCore import QMargins, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionMenuItem,
)

from .spacing import CONTROL_HEIGHT

if TYPE_CHECKING:
    from PySide6.QtCore import QModelIndex, QPersistentModelIndex
    from PySide6.QtWidgets import QStyleOptionViewItem, QWidget

# Qt's own combo delegates, which the menu rows replace
_NATIVE_DELEGATES: Final = frozenset({'QComboBoxDelegate', 'QComboMenuDelegate'})
# Matches the padding of the Replacer's Editing menu, which Glass menus widen
_PADDING: Final = 2
_GLASS_PADDING: Final = 5
# Matches the rounding of Glass menu items
_GLASS_RADIUS: Final = 7
# Combo rows have no status icons, so their text starts near the edge of the highlight
_TEXT_PADDING: Final = 8
_ICON_GAP: Final = 6


def plain_style() -> QStyle:
    """Return the application style beneath any stylesheet.

    Stylesheet item rules for list views would otherwise restyle combo rows,
    so every menu row draws with the same plain style
    """
    style = QApplication.style()
    if style.metaObject().className() == 'QStyleSheetStyle':
        # findChild ignores FindDirectChildrenOnly through PySide, and the base
        # style's own Fusion child must not be picked over it
        base = next((child for child in style.children() if isinstance(child, QStyle)), None)
        if base is not None:
            return base
    return style


def _paint_rounded_highlight(painter: QPainter, option: QStyleOptionMenuItem) -> None:
    """Paint the selection with rounded corners and hide the style's square one."""
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    highlight = palette.highlight().color()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # The same fill and darker outline the style uses, inside a half-pixel inset
    painter.setPen(QPen(highlight.darker(125), 1.0))
    painter.setBrush(highlight)
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    painter.drawRoundedRect(rect, _GLASS_RADIUS, _GLASS_RADIUS)
    painter.restore()
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Qt.GlobalColor.transparent))
    option.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field


def draw_menu_item(
    painter: QPainter, option: QStyleOptionMenuItem, widget: QWidget | None, *, rounded: bool
) -> None:
    """Draw one menu row; Glass rounds the selection like its menu items."""
    if rounded and option.state & QStyle.StateFlag.State_Selected:  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        _paint_rounded_highlight(painter, option)
    plain_style().drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, widget)


class MenuItemDelegate(QStyledItemDelegate):
    """Paint popup rows with the style's menu items, like every other dropdown menu."""

    @staticmethod
    def _glass(widget: QWidget | None) -> bool:
        # Glass gives the popup window its own stylesheet and surface
        return widget is not None and bool(widget.window().styleSheet())

    @staticmethod
    def _insets(index: QModelIndex | QPersistentModelIndex, *, glass: bool) -> tuple[int, int, int]:
        """Return the side, top, and bottom padding; only the outer rows pad vertically.

        The rows carry the popup padding because a scroll area paints its own
        padding apart from the viewport, leaving a visible band in Glass
        """
        padding = _GLASS_PADDING if glass else _PADDING
        top = padding if index.row() == 0 else 0
        bottom = padding if index.row() == index.model().rowCount(index.parent()) - 1 else 0
        return padding, top, bottom

    @staticmethod
    def _menu_option(
        option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex, *, glass: bool
    ) -> QStyleOptionMenuItem:
        menu_option = QStyleOptionMenuItem()
        menu_option.rect = option.rect  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.menuRect = option.rect
        # Glass tints list selections; menu rows keep the solid application colors
        palette = QPalette(QApplication.palette() if glass else option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.direction = option.direction  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.font = option.font
        menu_option.fontMetrics = option.fontMetrics  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        state = QStyle.StateFlag.State_None
        if option.state & QStyle.StateFlag.State_Enabled and (
            index.flags() & Qt.ItemFlag.ItemIsEnabled
        ):
            state |= QStyle.StateFlag.State_Enabled
        else:
            palette.setCurrentColorGroup(QPalette.ColorGroup.Disabled)
        if option.state & QStyle.StateFlag.State_Selected:
            state |= QStyle.StateFlag.State_Selected
        if option.state & QStyle.StateFlag.State_Active:
            state |= QStyle.StateFlag.State_Active
        menu_option.state = state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        separator = index.data(Qt.ItemDataRole.AccessibleDescriptionRole) == 'separator'
        menu_option.menuItemType = (
            QStyleOptionMenuItem.MenuItemType.Separator
            if separator
            else QStyleOptionMenuItem.MenuItemType.Normal
        )
        menu_option.checkType = QStyleOptionMenuItem.CheckType.NotCheckable
        menu_option.reservedShortcutWidth = 0
        return menu_option

    @staticmethod
    def _label(index: QModelIndex | QPersistentModelIndex) -> tuple[str, QIcon | None]:
        text = index.data(Qt.ItemDataRole.DisplayRole)
        icon = index.data(Qt.ItemDataRole.DecorationRole)
        return (
            str(text) if text is not None else '',
            icon if isinstance(icon, QIcon) and not icon.isNull() else None,
        )

    def _draw_label(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        menu_option: QStyleOptionMenuItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        """Draw the text without the check and icon column menu items reserve."""
        text, icon = self._label(index)
        direction = menu_option.direction  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        bounds = menu_option.rect.adjusted(_TEXT_PADDING, 0, -_TEXT_PADDING, 0)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        selected = bool(menu_option.state & QStyle.StateFlag.State_Selected)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        enabled = bool(menu_option.state & QStyle.StateFlag.State_Enabled)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        if icon is not None:
            size = option.decorationSize
            icon_rect = QRect(bounds.left(), bounds.top(), size.width(), bounds.height())
            mode = QIcon.Mode.Normal if enabled else QIcon.Mode.Disabled
            icon.paint(
                painter,
                QStyle.visualRect(direction, bounds, icon_rect),
                Qt.AlignmentFlag.AlignCenter,
                mode,
            )
            bounds.setLeft(bounds.left() + size.width() + _ICON_GAP)
        palette = menu_option.palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        role = QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text
        painter.save()
        painter.setFont(menu_option.font)
        painter.setPen(palette.color(role))
        metrics = menu_option.fontMetrics  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        painter.drawText(
            QStyle.visualRect(direction, menu_option.rect, bounds),  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            QStyle.visualAlignment(
                direction, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
            | Qt.TextFlag.TextSingleLine,
            metrics.elidedText(text, Qt.TextElideMode.ElideRight, bounds.width()),
        )
        painter.restore()

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        widget = option.widget  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        glass = self._glass(widget)
        if not glass:
            # Menus sit on the window color rather than the list's input color
            painter.fillRect(option.rect, option.palette.window())  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option = self._menu_option(option, index, glass=glass)
        side, top, bottom = self._insets(index, glass=glass)
        menu_option.rect = option.rect.adjusted(side, top, -side, -bottom)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.menuRect = menu_option.rect
        draw_menu_item(painter, menu_option, widget, rounded=glass)
        if menu_option.menuItemType == QStyleOptionMenuItem.MenuItemType.Normal:
            self._draw_label(painter, option, menu_option, index)

    @override
    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        widget = option.widget  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        glass = self._glass(widget)
        menu_option = self._menu_option(option, index, glass=glass)
        size = plain_style().sizeFromContents(
            QStyle.ContentsType.CT_MenuItem, menu_option, QSize(0, 0), widget
        )
        if menu_option.menuItemType == QStyleOptionMenuItem.MenuItemType.Normal:
            text, icon = self._label(index)
            width = menu_option.fontMetrics.horizontalAdvance(text) + 2 * _TEXT_PADDING  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
            if icon is not None:
                width += option.decorationSize.width() + _ICON_GAP
            # Match the height of the other dropdown menu rows
            size = QSize(width, max(size.height(), CONTROL_HEIGHT))
        side, top, bottom = self._insets(index, glass=glass)
        return size.grownBy(QMargins(side, top, side, bottom))


def use_menu_items(combo: QComboBox) -> None:
    """Replace Qt's list rows, keeping any delegate the application installed."""
    view = combo.view()
    if view.itemDelegate().metaObject().className() in _NATIVE_DELEGATES:
        # QComboBox.setItemDelegate deletes the old delegate behind its Python
        # wrapper; the view keeps it parented to the combo box instead
        view.setItemDelegate(MenuItemDelegate(combo))
