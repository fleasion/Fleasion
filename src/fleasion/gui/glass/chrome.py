"""Glass title bar and edge resizing for frameless dashboard windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, override
from weakref import WeakKeyDictionary, ref

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PySide6.QtGui import QCursor, QHoverEvent, QIcon, QMouseEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)
from shiboken6 import isValid

from .style import icon_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from PySide6.QtGui import QWindow

type WindowButton = Literal['min', 'max', 'close']

_TITLE_BAR_HEIGHT: Final = 46
_ICON_SIZE: Final = QSize(20, 20)
_BUTTON_ICON_SIZE: Final = QSize(14, 14)
_RESIZE_MARGIN: Final = 6
_CHROME_MARGINS: Final = (4, 0, 4, 4)


def _window_handle(window: QWidget) -> QWindow | None:
    # Qt has no native window until the widget is first shown
    return window.windowHandle()


class GlassTitleBar(QWidget):
    """Draggable glass header with pill window controls."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        self.setObjectName('GlassTitleBar')
        self.setFixedHeight(_TITLE_BAR_HEIGHT)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 10, 2)
        row.setSpacing(8)

        icon = QLabel()
        app_icon = window.windowIcon()
        if app_icon.isNull():
            app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            icon.setPixmap(app_icon.pixmap(_ICON_SIZE))
        title = QLabel(window.windowTitle())
        title.setObjectName('GlassTitle')
        window.windowTitleChanged.connect(title.setText)
        row.addWidget(icon)
        row.addWidget(title)
        row.addStretch()

        buttons: tuple[WindowButton, ...] = ('min', 'max', 'close')
        for role in buttons:
            button = QPushButton()
            button.setIcon(QIcon(icon_path(f'win-{role}')))
            button.setIconSize(_BUTTON_ICON_SIZE)
            button.setObjectName('GlassWinBtn')
            button.setProperty('role', role)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(self._presser(role))
            row.addWidget(button)

    def _presser(self, role: WindowButton) -> Callable[[], None]:
        return lambda: self._press(role)

    def _press(self, role: WindowButton) -> None:
        window = self._window
        if role == 'min':
            window.showMinimized()
        elif role == 'close':
            window.close()
        elif window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        handle = _window_handle(self._window)
        if event.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
        super().mousePressEvent(event)

    @override
    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        self._press('max')


_SIDE_CURSORS: Final = {
    Qt.Edge.LeftEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.RightEdge: Qt.CursorShape.SizeHorCursor,
    Qt.Edge.TopEdge: Qt.CursorShape.SizeVerCursor,
    Qt.Edge.BottomEdge: Qt.CursorShape.SizeVerCursor,
}
_FALLING_DIAGONALS: Final = (
    Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
    Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
)


class EdgeResizer(QObject):
    """Frameless windows still need resizable edges."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        app = QApplication.instance()
        self._app = app if isinstance(app, QApplication) else None
        self._hover: WeakKeyDictionary[QWidget, bool] = WeakKeyDictionary()
        self._cursor_widget: QWidget | None = None
        self._previous_cursor: QCursor | None = None
        self._cursor_was_set = False
        for widget in [window, *window.findChildren(QWidget)]:
            self._track(widget)
        if self._app is not None:
            self._app.installEventFilter(self)

    def _track(self, widget: QWidget) -> None:
        if widget not in self._hover:
            self._hover[widget] = widget.testAttribute(Qt.WidgetAttribute.WA_Hover)
            widget.setAttribute(Qt.WidgetAttribute.WA_Hover)

    def _restore_cursor(self) -> None:
        widget = self._cursor_widget
        if widget is not None and isValid(widget):
            if self._cursor_was_set and self._previous_cursor is not None:
                widget.setCursor(self._previous_cursor)
            else:
                widget.unsetCursor()
        self._cursor_widget = None
        self._previous_cursor = None

    def detach(self) -> None:
        if self._app is not None:
            self._app.removeEventFilter(self)
        self._restore_cursor()
        for widget, hovered in list(self._hover.items()):
            if isValid(widget):
                widget.setAttribute(Qt.WidgetAttribute.WA_Hover, on=hovered)
        self._hover.clear()

    def _edges(self, pos: QPoint) -> Qt.Edge:
        window = self._window
        if not window.rect().contains(pos):
            return Qt.Edge(0)
        edges = Qt.Edge(0)
        if pos.x() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.LeftEdge
        elif pos.x() >= window.width() - _RESIZE_MARGIN:
            edges |= Qt.Edge.RightEdge
        if pos.y() <= _RESIZE_MARGIN:
            edges |= Qt.Edge.TopEdge
        elif pos.y() >= window.height() - _RESIZE_MARGIN:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _update_cursor(self, widget: QWidget, edges: Qt.Edge) -> None:
        if not edges or widget is not self._cursor_widget:
            self._restore_cursor()
        if not edges:
            return
        if self._cursor_widget is None:
            self._cursor_widget = widget
            self._previous_cursor = QCursor(widget.cursor())
            self._cursor_was_set = widget.testAttribute(Qt.WidgetAttribute.WA_SetCursor)
        if edges in _SIDE_CURSORS:
            widget.setCursor(_SIDE_CURSORS[edges])
        else:
            diagonal = edges in _FALLING_DIAGONALS
            cursor = Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor
            widget.setCursor(cursor)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if (
            not isValid(self._window)
            or not isinstance(watched, QWidget)
            or kind
            not in {
                QEvent.Type.Polish,
                QEvent.Type.Leave,
                QEvent.Type.Hide,
                QEvent.Type.WindowDeactivate,
                QEvent.Type.MouseMove,
                QEvent.Type.HoverMove,
                QEvent.Type.MouseButtonPress,
            }
        ):
            return False
        if watched.window() is not self._window:
            if kind in {QEvent.Type.MouseMove, QEvent.Type.HoverMove, QEvent.Type.MouseButtonPress}:
                self._restore_cursor()
            return False
        if kind == QEvent.Type.Polish:
            self._track(watched)
        if (
            self._window.isMaximized()
            or (kind == QEvent.Type.Leave and watched is self._cursor_widget)
            or (
                kind in {QEvent.Type.Hide, QEvent.Type.WindowDeactivate} and watched is self._window
            )
        ):
            self._restore_cursor()
            return False
        if not isinstance(event, QMouseEvent | QHoverEvent):
            return False
        position = watched.mapTo(self._window, event.position().toPoint())
        edges = self._edges(position)
        if kind in {QEvent.Type.MouseMove, QEvent.Type.HoverMove}:
            self._update_cursor(self._window.childAt(position) or self._window, edges)
        elif (
            isinstance(event, QMouseEvent)
            and kind == QEvent.Type.MouseButtonPress
            and event.button() == Qt.MouseButton.LeftButton
            and edges
            and (handle := _window_handle(self._window)) is not None
        ):
            return handle.startSystemResize(edges)
        return False


@dataclass(frozen=True)
class _Chrome:
    title_bar: GlassTitleBar
    resizer: EdgeResizer


_installed: WeakKeyDictionary[QWidget, _Chrome] = WeakKeyDictionary()
_capable: WeakKeyDictionary[QWidget, bool] = WeakKeyDictionary()


def register(window: QWidget) -> None:
    """Mark a window whose platform title bar Glass may replace."""
    if window in _capable or not isValid(window):
        return
    _capable[window] = True
    reference = ref(window)

    def forget() -> None:
        if (deleted := reference()) is not None:
            _installed.pop(deleted, None)
            _capable.pop(deleted, None)

    window.destroyed.connect(forget)


def registered_windows() -> list[QWidget]:
    return [window for window in _capable if isValid(window)]


def _layout(window: QWidget) -> QBoxLayout | None:
    layout = window.layout()
    return layout if isinstance(layout, QBoxLayout) else None


def apply_chrome(window: QWidget, *, enabled: bool) -> None:
    """Swap the platform title bar for the glass one, or restore it."""
    if not isValid(window):
        _installed.pop(window, None)
        _capable.pop(window, None)
        return
    layout = _layout(window)
    chrome = _installed.get(window)
    if enabled and chrome is None and layout is not None:
        chrome = _Chrome(GlassTitleBar(window), EdgeResizer(window))
        layout.insertWidget(0, chrome.title_bar)
        layout.setContentsMargins(*_CHROME_MARGINS)
        _installed[window] = chrome
    elif not enabled and chrome is not None:
        if layout is not None:
            layout.removeWidget(chrome.title_bar)
            layout.setContentsMargins(0, 0, 0, 0)
        chrome.title_bar.deleteLater()
        chrome.resizer.detach()
        chrome.resizer.deleteLater()
        del _installed[window]
    window.setProperty('glassChrome', enabled)
    frameless = bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    if frameless != enabled:
        visible = window.isVisible()
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=enabled)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, on=enabled)
        if visible:
            window.show()
