"""A dropdown menu that shows one widget and scrolls once it outgrows the screen."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast, override

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QFrame, QMenu, QScrollArea, QStyle, QWidgetAction

if TYPE_CHECKING:
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QMouseEvent, QScreen
    from PySide6.QtWidgets import QWidget


# Room kept between the menu and the screen edge
_SCREEN_MARGIN: Final = 12


class ScrollingMenu(QMenu):
    """A menu that shows one widget, sized to its content and to the screen.

    Parameters
    ----------
    parent
        The widget the menu opens from.
    horizontal_scroll
        Whether content wider than the screen scrolls sideways; otherwise it
        is narrowed to fit.
    """

    def __init__(self, parent: QWidget | None = None, *, horizontal_scroll: bool = False) -> None:
        super().__init__(parent)
        self._horizontal_scroll = horizontal_scroll
        self._minimum_width = 0
        self._natural_content_size = QSize(0, 0)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if horizontal_scroll
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self._content_action = QWidgetAction(self)
        self._content_action.setDefaultWidget(self.scroll_area)
        self.addAction(self._content_action)

    #
    # Content
    #

    def set_content(self, widget: QWidget, *, minimum_width: int = 0) -> None:
        """Show a widget in the menu, replacing the previous one."""
        if (old := self.scroll_area.takeWidget()) is not None:  # pyright: ignore[reportUnnecessaryComparison] - empty areas return None
            old.deleteLater()

        self._minimum_width = max(0, minimum_width)
        self.scroll_area.setWidget(widget)
        widget.adjustSize()
        self._natural_content_size = widget.sizeHint()
        self._set_content_size(self._natural_content_size.height())
        self._reset_geometry()

    #
    # Geometry
    #

    def _insets(self) -> tuple[int, int]:
        """Horizontal and vertical space the menu frame adds around its content."""
        style = self.style()
        panel = style.pixelMetric(QStyle.PixelMetric.PM_MenuPanelWidth, None, self)
        horizontal = style.pixelMetric(QStyle.PixelMetric.PM_MenuHMargin, None, self)
        vertical = style.pixelMetric(QStyle.PixelMetric.PM_MenuVMargin, None, self)
        return panel + horizontal, panel + vertical

    @override
    def sizeHint(self) -> QSize:
        """Size the menu around the constrained scroll area, including the frame."""
        if self._natural_content_size.isValid() and self.scroll_area.size().isValid():
            horizontal, vertical = self._insets()
            return QSize(
                self.scroll_area.width() + 2 * horizontal,
                self.scroll_area.height() + 2 * vertical,
            )
        return super().sizeHint()

    def _set_content_size(self, max_height: int, *, max_width: int | None = None) -> None:
        natural = self._natural_content_size
        if natural.height() <= 0:
            return

        # A vertical scroll bar takes its width beside the content
        height = min(natural.height(), max(1, max_height))
        scrollbar_width = self.scroll_area.verticalScrollBar().sizeHint().width()
        needs_scrollbar = natural.height() > height
        width = max(natural.width(), self._minimum_width)
        if needs_scrollbar:
            width += scrollbar_width
        if max_width is not None:
            width = min(width, max_width)

        # Without sideways scrolling the content is narrowed to the visible width
        if not self._horizontal_scroll and (widget := self.scroll_area.widget()) is not None:  # pyright: ignore[reportUnnecessaryComparison] - empty areas return None
            widget.setFixedWidth(max(1, width - (scrollbar_width if needs_scrollbar else 0)))

        self.scroll_area.setFixedSize(max(1, width), height)
        self.scroll_area.updateGeometry()
        self.updateGeometry()

    def _reset_geometry(self) -> None:
        """Drop the size QMenu caches for its widget action after the content changes."""
        self.adjustSize()
        self.resize(self.sizeHint())
        self.updateGeometry()
        self.update()

    def constrain_to_available_geometry(
        self, available_geometry: QRect | None, anchor_y: int | None = None
    ) -> None:
        """Fit the menu into the visible screen area; the content scrolls as needed."""
        if available_geometry is None:
            return

        # Open above or below the anchor, whichever side has more room
        if anchor_y is None:
            available_height = available_geometry.height()
        else:
            space_below = available_geometry.bottom() - anchor_y
            space_above = anchor_y - available_geometry.top()
            available_height = max(space_below, space_above)

        horizontal, vertical = self._insets()
        max_height = max(1, available_height - _SCREEN_MARGIN - 2 * vertical)
        max_width = max(1, available_geometry.width() - _SCREEN_MARGIN - 2 * horizontal)
        self._set_content_size(max_height, max_width=max_width)
        self._reset_geometry()

    def constrain_to_widget(self, widget: QWidget | None) -> None:
        """Fit the menu into the screen of the widget it opens below."""
        if widget is None:
            return

        anchor = widget.mapToGlobal(widget.rect().bottomLeft())
        # Widgets that were never shown have no screen yet
        screen = cast('QScreen | None', widget.screen()) or QApplication.screenAt(anchor)
        if screen is not None:
            self.constrain_to_available_geometry(screen.availableGeometry(), anchor.y())

    #
    # Events
    #

    @override
    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        # Releasing over the embedded widget must not close the menu
        if event is None:
            return
        action = self.actionAt(event.pos())
        if isinstance(action, QWidgetAction) and action.defaultWidget() == self.scroll_area:
            return
        super().mouseReleaseEvent(event)
