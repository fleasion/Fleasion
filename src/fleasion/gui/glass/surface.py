"""Painting the glass window surface, including the optional wallpaper backdrop."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final, override
from weakref import WeakKeyDictionary

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton, QWidget
from shiboken6 import isValid

from .desktop_blur import blur
from .state import UNBLURRED_OPACITY, state
from .style import RADIUS
from .wallpaper import wallpapers

if TYPE_CHECKING:
    from PySide6.QtGui import QScreen

_COMPACT_BUTTON_MAX: Final = 40
_ARROW_BUTTON_MAX: Final = 24
_FALLBACK_BACKDROP: Final = (20, 22, 34)


def effective_opacity() -> float:
    """Tint strength: the user's choice when something blurs behind us, else denser."""
    settings = state.settings
    if settings.backdrop == 'wallpaper' or blur.cached_status() == 'on':
        return settings.opacity
    return max(settings.opacity, UNBLURRED_OPACITY)


def _screen(window: QWidget) -> QScreen | None:
    # Qt returns no screen for a window that is not shown yet
    return window.screen()


def _origin_in_screen(window: QWidget, width: int, height: int) -> QPoint:
    """Where the window sits on its screen; centred when the platform hides it."""
    screen = _screen(window)
    if QApplication.platformName().startswith('wayland') or screen is None:
        # Wayland clients cannot learn their global position
        return QPoint((width - window.width()) // 2, (height - window.height()) // 2)
    return window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()


def _paint_wallpaper(painter: QPainter, window: QWidget, path: QPainterPath) -> None:
    screen = _screen(window)
    if screen is None:
        return
    geometry = screen.geometry()
    # Cover the screen, or the window if it is larger than the screen
    width = max(geometry.width(), window.width())
    height = max(geometry.height(), window.height())
    image = wallpapers.image(width, height, screen.name())
    if image.isNull():
        painter.fillPath(path, QColor(*_FALLBACK_BACKDROP))
        return
    origin = _origin_in_screen(window, width, height)
    source = QRectF(origin.x(), origin.y(), window.width(), window.height())
    painter.save()
    painter.setClipPath(path)
    painter.drawImage(QRectF(window.rect()), image, source)
    painter.restore()


# ---------------------------------------------------------------- surface


def paint_glass(
    painter: QPainter, rect: QRectF, radius: float, window: QWidget | None = None
) -> None:
    """Backdrop, tint, top-down sheen and a specular rim."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if window is not None and _opaque_fallback(window):
        painter.fillPath(path, QColor(*_FALLBACK_BACKDROP))
    if window is not None and state.settings.backdrop == 'wallpaper':
        _paint_wallpaper(painter, window, path)
    painter.fillPath(path, QColor(16, 17, 24, round(255 * effective_opacity())))
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
    sheen.setColorAt(0.25, QColor(255, 255, 255, 10))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 4))
    painter.fillPath(path, sheen)
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0.0, QColor(255, 255, 255, 120))
    rim.setColorAt(0.12, QColor(255, 255, 255, 40))
    rim.setColorAt(1.0, QColor(255, 255, 255, 22))
    painter.setPen(QPen(rim, 1.0))
    painter.drawPath(path)


def _repolish_later(widget: QWidget) -> None:
    def repolish() -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    QTimer.singleShot(0, repolish)


def _is_window(obj: QObject | None) -> bool:
    return isinstance(obj, (QMainWindow, QDialog)) and obj.isWindow()


def _opaque_fallback(window: QWidget) -> bool:
    # Windows and macOS require frameless windows for per-pixel translucency
    return sys.platform in {'win32', 'darwin'} and not (
        window.windowFlags() & Qt.WindowType.FramelessWindowHint
    )


class GlassWindows(QObject):
    """Gives top-level windows an alpha surface and paints the glass on them."""

    blur_status_changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._app = app
        self._attributes: WeakKeyDictionary[QWidget, tuple[bool, bool]] = WeakKeyDictionary()
        self._wallpaper_timer = QTimer(self)
        self._wallpaper_timer.setInterval(100)
        self._wallpaper_timer.timeout.connect(self.refresh_wallpaper)
        self._wallpaper_timer.start()
        self.refresh_wallpaper()

    def refresh_wallpaper(self) -> None:
        blur_changed = blur.poll_refresh()
        if blur_changed:
            self.blur_status_changed.emit()
        changed = blur_changed
        if state.settings.backdrop == 'wallpaper' and wallpapers.refresh_source():
            changed = True
        if changed:
            for window in self._app.topLevelWidgets():
                window.update()

    def restore(self) -> None:
        self._wallpaper_timer.stop()
        blur.stop_polling()
        wallpapers.close()
        for window, (translucent, no_background) in list(self._attributes.items()):
            if not isValid(window):
                continue
            window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=translucent)
            window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, on=no_background)
            window.update()
        self._attributes.clear()

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget):
            return False
        kind = event.type()
        if kind == QEvent.Type.Polish:
            self.polish(watched)
        elif kind == QEvent.Type.Paint and self._paints(watched):
            painter = QPainter(watched)
            framed = bool(watched.property('glassChrome')) and not watched.isMaximized()
            rect = QRectF(watched.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            paint_glass(painter, rect, RADIUS if framed else 0, watched)
            painter.end()
        elif (
            kind == QEvent.Type.Move
            and state.settings.backdrop == 'wallpaper'
            and _is_window(watched)
        ):
            watched.update()  # The wallpaper slice follows the window
        return False

    @staticmethod
    def _paints(widget: QWidget) -> bool:
        return (
            _is_window(widget)
            and (
                widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                or _opaque_fallback(widget)
            )
            and not widget.property('glassOwnSurface')
        )

    def polish(self, widget: QWidget) -> None:
        if _is_window(widget):
            if widget not in self._attributes:
                self._attributes[widget] = (
                    widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
                    widget.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground),
                )
            translucent = not _opaque_fallback(widget)
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=translucent)
            if not translucent:
                widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, on=False)
        elif not isinstance(widget, QPushButton):
            return
        elif (
            widget.isFlat()
            and widget.maximumWidth() <= _ARROW_BUTTON_MAX
            and widget.property('glassArrow') is None
        ):
            widget.setProperty('glassArrow', True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
            _repolish_later(widget)
        elif (
            0 < widget.maximumWidth() <= _COMPACT_BUTTON_MAX and widget.property('compact') is None
        ):
            widget.setProperty('compact', True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
            _repolish_later(widget)
