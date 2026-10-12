"""Glass title bar and edge resizing for frameless dashboard windows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, override
from weakref import WeakKeyDictionary

from PyQt6.QtCore import QEvent, QObject, QPoint, QSize, Qt
from PyQt6.QtGui import QIcon, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
)

from .style import icon_path

if TYPE_CHECKING:
    from collections.abc import Callable

type WindowButton = Literal['min', 'max', 'close']

_TITLE_BAR_HEIGHT: Final = 46
_ICON_SIZE: Final = QSize(20, 20)
_BUTTON_ICON_SIZE: Final = QSize(14, 14)
_RESIZE_MARGIN: Final = 6
_CHROME_MARGINS: Final = (4, 0, 4, 4)


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
    def mousePressEvent(self, a0: QMouseEvent | None) -> None:
        handle = self._window.windowHandle()
        if a0 is not None and a0.button() == Qt.MouseButton.LeftButton and handle is not None:
            handle.startSystemMove()
        super().mousePressEvent(a0)

    @override
    def mouseDoubleClickEvent(self, a0: QMouseEvent | None) -> None:
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
        window.setMouseTracking(True)

    def _edges(self, pos: QPoint) -> Qt.Edge:
        window = self._window
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

    def _update_cursor(self, edges: Qt.Edge) -> None:
        if edges in _SIDE_CURSORS:
            self._window.setCursor(_SIDE_CURSORS[edges])
        elif edges:
            diagonal = edges in _FALLING_DIAGONALS
            cursor = Qt.CursorShape.SizeFDiagCursor if diagonal else Qt.CursorShape.SizeBDiagCursor
            self._window.setCursor(cursor)
        else:
            self._window.unsetCursor()

    @override
    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        if a0 is not self._window or self._window.isMaximized() or not isinstance(a1, QMouseEvent):
            return False
        edges = self._edges(a1.position().toPoint())
        if a1.type() == QEvent.Type.MouseMove:
            self._update_cursor(edges)
        elif (
            a1.type() == QEvent.Type.MouseButtonPress
            and a1.button() == Qt.MouseButton.LeftButton
            and edges
            and (handle := self._window.windowHandle()) is not None
        ):
            handle.startSystemResize(edges)
            return True
        return False


@dataclass(frozen=True)
class _Chrome:
    title_bar: GlassTitleBar
    resizer: EdgeResizer


_installed: WeakKeyDictionary[QWidget, _Chrome] = WeakKeyDictionary()
_capable: WeakKeyDictionary[QWidget, bool] = WeakKeyDictionary()


def register(window: QWidget) -> None:
    """Mark a window whose platform title bar Glass may replace."""
    _capable[window] = True


def registered_windows() -> list[QWidget]:
    return list(_capable)


def _layout(window: QWidget) -> QBoxLayout | None:
    layout = window.layout()
    return layout if isinstance(layout, QBoxLayout) else None


def apply_chrome(window: QWidget, *, enabled: bool) -> None:
    """Swap the platform title bar for the glass one, or restore it."""
    layout = _layout(window)
    chrome = _installed.get(window)
    if enabled and chrome is None and layout is not None:
        chrome = _Chrome(GlassTitleBar(window), EdgeResizer(window))
        layout.insertWidget(0, chrome.title_bar)
        window.installEventFilter(chrome.resizer)
        layout.setContentsMargins(*_CHROME_MARGINS)
        _installed[window] = chrome
    elif not enabled and chrome is not None:
        if layout is not None:
            layout.removeWidget(chrome.title_bar)
            layout.setContentsMargins(0, 0, 0, 0)
        chrome.title_bar.deleteLater()
        window.removeEventFilter(chrome.resizer)
        del _installed[window]
    window.setProperty('glassChrome', enabled)
    frameless = bool(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
    if frameless != enabled:
        visible = window.isVisible()
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=enabled)
        window.setWindowFlag(Qt.WindowType.FramelessWindowHint, on=enabled)
        if visible:
            window.show()
