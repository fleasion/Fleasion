"""Glass menus: a rounded translucent panel with pill-shaped item highlights."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette, QPen
from PySide6.QtWidgets import QMenu, QStyle, QStyleOptionMenuItem

from .style import glass_colors

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtWidgets import QStyleOption, QWidget


_State = QStyle.StateFlag
_PM = QStyle.PixelMetric

PANEL_RADIUS: Final = 12.0
_ITEM_RADIUS: Final = 7.0

# The panel's border, and the padding between it and the items
MENU_METRICS: Final = {
    _PM.PM_MenuPanelWidth: 1,
    _PM.PM_MenuHMargin: 5,
    _PM.PM_MenuVMargin: 5,
}

# Tooltips are rounded less than a button, to suit their small size
TOOLTIP_RADIUS: Final = 6.0

# Items are a little roomier than Fusion's; separators are a hairline with air around it
_ITEM_GROWTH: Final = QSize(4, 4)
_SEPARATOR_HEIGHT: Final = 9
_SEPARATOR_INSET: Final = 8


def _tint(color: QColor, alpha: float) -> QColor:
    tinted = QColor(color)
    tinted.setAlphaF(alpha)
    return tinted


def paint_menu_panel(painter: QPainter, option: QStyleOption, widget: QWidget | None) -> bool:
    """The menu background, nearly opaque so the items stay legible over any backdrop."""
    if not isinstance(widget, QMenu):
        return False

    colors = glass_colors()
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0, _tint(colors.ink, 0.18))
    rim.setColorAt(min(1.0, PANEL_RADIUS / max(rect.height(), 1)), _tint(colors.ink, 0.14))
    rim.setColorAt(1, _tint(colors.ink, 0.14))

    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(rim, 1.0))
    painter.setBrush(_tint(colors.popup, 0.97))
    painter.drawRoundedRect(rect, PANEL_RADIUS, PANEL_RADIUS)
    painter.restore()
    return True


def paint_menu_item(
    painter: QPainter, option: QStyleOption, base: Callable[[QStyleOption], None]
) -> bool:
    """Draw a separator, or a pill under the selected item before Fusion draws its contents."""
    if not isinstance(option, QStyleOptionMenuItem):
        return False

    rect = QRectF(option.rect)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    if option.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
        y = rect.center().y()
        painter.save()
        painter.setPen(QPen(_tint(glass_colors().ink, 0.10), 1.0))
        painter.drawLine(
            QPointF(rect.left() + _SEPARATOR_INSET, y), QPointF(rect.right() - _SEPARATOR_INSET, y)
        )
        painter.restore()
        return True

    state = option.state  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    palette = QPalette(option.palette)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    if state & _State.State_Selected and state & _State.State_Enabled:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        # Glass's own accent: a popup list can carry a platform palette of its own
        painter.setBrush(_tint(glass_colors().accent, 0.30))
        painter.drawRoundedRect(rect, _ITEM_RADIUS, _ITEM_RADIUS)
        painter.restore()

    # Fusion then draws the label, check and arrow over the pill without its own highlight
    changed = QStyleOptionMenuItem(option)
    palette.setColor(QPalette.ColorRole.Highlight, QColor(Qt.GlobalColor.transparent))
    palette.setColor(QPalette.ColorRole.HighlightedText, palette.color(QPalette.ColorRole.Text))
    changed.palette = palette  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    base(changed)
    return True


def menu_item_size(option: QStyleOption, result: QSize) -> QSize | None:
    if not isinstance(option, QStyleOptionMenuItem):
        return None
    if option.menuItemType == QStyleOptionMenuItem.MenuItemType.Separator:
        return QSize(result.width(), _SEPARATOR_HEIGHT)
    return result + _ITEM_GROWTH


def paint_tooltip(painter: QPainter, option: QStyleOption) -> bool:
    """A tooltip: the popup surface in a small rounded panel."""
    colors = glass_colors()
    rect = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)  # pyright: ignore[reportAttributeAccessIssue] - Qt option field
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(_tint(colors.ink, 0.14), 1.0))
    painter.setBrush(_tint(colors.popup, 0.96))
    painter.drawRoundedRect(rect, TOOLTIP_RADIUS, TOOLTIP_RADIUS)
    painter.restore()
    return True
