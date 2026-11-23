"""Menu rows shared by dropdown menus and combo box popups."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, override

from PySide6.QtCore import QMargins, QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPainter, QPalette
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
    from PySide6.QtWidgets import QMenu, QStyleOptionViewItem, QWidget

# Qt's own combo delegates, which the menu rows replace
_NATIVE_DELEGATES: Final = frozenset({'QComboBoxDelegate', 'QComboMenuDelegate'})
# Matches the padding of the Replacer's Editing menu; a style with wider menu margins wins
_PADDING: Final = 2
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


def draw_menu_item(painter: QPainter, option: QStyleOptionMenuItem, widget: QWidget | None) -> None:
    """Draw one menu row exactly as the style draws the rows of a QMenu."""
    plain_style().drawControl(QStyle.ControlElement.CE_MenuItem, option, painter, widget)


class MenuItemDelegate(QStyledItemDelegate):
    """Paint popup rows with the style's menu items, like every other dropdown menu."""

    @staticmethod
    def _insets(
        index: QModelIndex | QPersistentModelIndex, widget: QWidget | None
    ) -> tuple[int, int, int]:
        """Return the side, top, and bottom padding; only the outer rows pad vertically.

        The rows carry the popup padding because a scroll area paints its own
        padding apart from the viewport, leaving a visible band in Glass
        """
        margin = plain_style().pixelMetric(QStyle.PixelMetric.PM_MenuVMargin, None, widget)
        padding = max(_PADDING, margin)
        top = padding if index.row() == 0 else 0
        bottom = padding if index.row() == index.model().rowCount(index.parent()) - 1 else 0
        return padding, top, bottom

    @staticmethod
    def _menu_option(
        option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QStyleOptionMenuItem:
        menu_option = QStyleOptionMenuItem()
        menu_option.rect = option.rect  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.menuRect = option.rect
        palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
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
        # Imported here: the style module installs these rows on combo boxes
        from .style import LayoutStyle  # ruff: ignore[import-outside-top-level]

        role = LayoutStyle.selected_text_role() if selected else QPalette.ColorRole.Text
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

        # Menus sit on the window color rather than the list's input color; Glass
        # makes Window transparent, so its popup surface shows through
        painter.fillRect(option.rect, option.palette.window())  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option = self._menu_option(option, index)
        side, top, bottom = self._insets(index, widget)
        menu_option.rect = option.rect.adjusted(side, top, -side, -bottom)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option.menuRect = menu_option.rect
        draw_menu_item(painter, menu_option, widget)
        if menu_option.menuItemType == QStyleOptionMenuItem.MenuItemType.Normal:
            self._draw_label(painter, option, menu_option, index)

    @override
    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        widget = option.widget  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
        menu_option = self._menu_option(option, index)
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
        side, top, bottom = self._insets(index, widget)
        return size.grownBy(QMargins(side, top, side, bottom))


def use_menu_items(combo: QComboBox) -> None:
    """Replace Qt's list rows, keeping any delegate the application installed."""
    view = combo.view()
    if view.itemDelegate().metaObject().className() in _NATIVE_DELEGATES:
        # QComboBox.setItemDelegate deletes the old delegate behind its Python
        # wrapper; the view keeps it parented to the combo box instead
        view.setItemDelegate(MenuItemDelegate(combo))


def sync_widget_menu_surface(menu: QMenu, container: QWidget, *, checkbox_padding: str) -> None:
    """Paint a menu that embeds widgets, and their container, on one opaque surface.

    Glass makes the Window role transparent, so the menu falls back to the
    tooltip surface and the table underneath never shows through.
    """
    surface = menu.palette().window().color()
    if surface.alpha() == 0:
        surface = menu.palette().toolTipBase().color()
    color = surface.name()

    stylesheet = (
        f'QMenu {{ background-color: {color}; border: 1px solid palette(mid);'
        ' border-radius: 4px; color: palette(window-text); }'
        f' QWidget#{container.objectName()} {{ background-color: {color}; }}'
        f' QCheckBox {{ padding: {checkbox_padding}; font-size: 12px; }}'
        ' QCheckBox::indicator { width: 14px; height: 14px; }'
    )
    if menu.styleSheet() != stylesheet:
        menu.setStyleSheet(stylesheet)
