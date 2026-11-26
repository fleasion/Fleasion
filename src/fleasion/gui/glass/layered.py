"""Keep Windows' layered glass windows whole while the user resizes them.

On Windows a frameless translucent window is a layered window. Qt shows each
repaint with UpdateLayeredWindowIndirect, passing the native window's current
size and only the rectangle it repainted. Whenever that size differs from the
last one, Windows starts the window over on a new surface and fills in just
that rectangle, so everything else turns transparent until the next full
repaint.

While an edge is dragged, Qt updates the native geometry at once but delivers
the widget's resize several milliseconds later. A partial repaint pending in
between, such as an item view finishing its delayed layout, would reach the
screen at the new size before the widget is resized and repainted in full:
the rest of the window flashes out on every such step.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final
from weakref import WeakKeyDictionary

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

if TYPE_CHECKING:
    from PySide6.QtCore import QObject

# How long a held back update waits before asking again, about one frame. The
# resize normally repaints the window first, which makes the retry a no-op
_RETRY_MS: Final = 16

# Retries since the window's last resize before an update goes through anyway,
# in case the resize never comes
_MAX_DEFERRALS: Final = 16


class LayeredResizeGuard:
    """Holds back a window's repaint until Qt has caught up with its native size."""

    def __init__(self, parent: QObject) -> None:
        self._layered = QApplication.platformName() == 'windows'

        # Windows with a held back update, and how often it was held since their last resize
        self._waiting: WeakKeyDictionary[QWidget, int] = WeakKeyDictionary()

        self._retry = QTimer(parent)
        self._retry.setSingleShot(True)
        self._retry.setInterval(_RETRY_MS)
        self._retry.timeout.connect(self._request_updates)

    def _resize_pending(self, window: QWidget) -> bool:
        if not (
            self._layered
            and window.isWindow()
            and window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            and window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        ):
            return False

        # A created top level always has its native window
        return window.windowHandle().geometry().size() != window.size()

    def defer(self, window: QWidget) -> bool:
        """Hold back an update request that would paint ahead of the window's resize.

        Returns whether the request was consumed. The resize repaints the whole
        window and lets Qt send update requests again; a retry shortly after
        covers a resize that never comes.
        """
        deferrals = self._waiting.get(window, 0)
        if deferrals >= _MAX_DEFERRALS or not self._resize_pending(window):
            self._waiting.pop(window, None)
            return False

        # Reposting at once would only spin the event loop until the resize arrives
        self._waiting[window] = deferrals + 1
        if not self._retry.isActive():
            self._retry.start()
        return True

    def resized(self, window: QWidget) -> None:
        """Start a window's retry count over once Qt has delivered a resize."""
        if window in self._waiting:
            self._waiting[window] = 0

    def _request_updates(self) -> None:
        # Qt sends a window no further update requests until it handles this one
        for window in list(self._waiting):
            if isValid(window):
                QCoreApplication.postEvent(
                    window,
                    QEvent(QEvent.Type.UpdateRequest),
                    Qt.EventPriority.LowEventPriority.value,
                )
