"""Painting the glass window surface, including the optional wallpaper backdrop."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final, override
from weakref import WeakKeyDictionary, WeakSet

from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHeaderView,
    QMainWindow,
    QMenu,
    QPushButton,
    QWidget,
)
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
        if not isValid(widget):
            return
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

    def __init__(
        self,
        app: QApplication,
        original_combo_masks: dict[QWidget, QRegion] | None = None,
        original_itemview_masks: dict[QWidget, QRegion] | None = None,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._active = True
        self._original_combo_masks: WeakKeyDictionary[QWidget, QRegion] = WeakKeyDictionary(
            original_combo_masks or {}
        )
        self._original_itemview_masks: WeakKeyDictionary[QWidget, QRegion] = WeakKeyDictionary(
            original_itemview_masks or {}
        )
        self._clipped_itemviews: WeakSet[QWidget] = WeakSet()
        self._attributes: WeakKeyDictionary[QWidget, tuple[bool, bool]] = WeakKeyDictionary()
        self._button_properties: WeakKeyDictionary[QPushButton, set[str]] = WeakKeyDictionary()
        self._original_masks: WeakKeyDictionary[QWidget, QRegion] = WeakKeyDictionary()
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
        self._active = False
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
        for widget, original_mask in list(self._original_masks.items()):
            if isValid(widget):
                if original_mask.isEmpty():
                    widget.clearMask()
                else:
                    widget.setMask(original_mask)
        self._original_masks.clear()
        for widget in list(self._clipped_itemviews):
            if not isValid(widget):
                continue
            original = self._original_itemview_masks.get(widget)
            if original is None or original.isEmpty():
                widget.clearMask()
            else:
                widget.setMask(original)
        self._clipped_itemviews.clear()
        self._original_itemview_masks.clear()
        # Qt sets the combo popup's native mask from QSS independently of our
        # event filter. QSS removal does not always undo that native mask, so
        # return popups to the exact shape they had before Glass was enabled.
        if QApplication.platformName() == 'xcb':
            for widget in self._app.allWidgets():
                if not isValid(widget) or not self.is_combo_popup(widget):
                    continue
                original = self._original_combo_masks.get(widget)
                if original is None or original.isEmpty():
                    widget.clearMask()
                else:
                    widget.setMask(original)
        self._original_combo_masks.clear()
        for button, names in list(self._button_properties.items()):
            if not isValid(button):
                continue
            for name in names:
                # None removes the dynamic property, restoring its initial absence
                button.setProperty(name, None)
        self._button_properties.clear()

    def remember_window(self, window: QWidget) -> None:
        # Capture before Glass chrome can change the window attributes. A
        # dashboard created while Glass is already active is not yet polished
        # when its custom title bar is attached.
        if window not in self._attributes:
            self._attributes[window] = (
                window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
                window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground),
            )

    @staticmethod
    def is_combo_popup(widget: QWidget) -> bool:
        return widget.isWindow() and widget.metaObject().className() == 'QComboBoxPrivateContainer'

    @staticmethod
    def _mask_radius(widget: QWidget) -> int:
        if widget.property('glassOwnSurface') or widget.isMaximized():
            return 0
        if _is_window(widget) and widget.property('glassChrome'):
            return RADIUS
        if widget.isWindow() and widget.windowFlags() & Qt.WindowType.Popup:
            if isinstance(widget, QMenu):
                return 12
            if GlassWindows.is_combo_popup(widget):
                return 10
        return 0

    def update_window_shape(self, widget: QWidget) -> None:
        # On X11, switching a shown window to translucent commonly retains its
        # original 24-bit visual. QSS border-radius alone does not clip it.
        if QApplication.platformName() != 'xcb' or widget.depth() >= 32:
            return
        if not widget.isWindow() or (radius := self._mask_radius(widget)) <= 0:
            if widget in self._original_masks:
                original = self._original_masks.pop(widget)
                if original.isEmpty():
                    widget.clearMask()
                else:
                    widget.setMask(original)
            return
        if widget.width() < 2 or widget.height() < 2:
            return
        if widget not in self._original_masks:
            self._original_masks[widget] = QRegion(widget.mask())
        rect = QRectF(0, 0, widget.width(), widget.height())
        path = QPainterPath()
        path.addRoundedRect(rect, min(radius, widget.width() / 2), min(radius, widget.height() / 2))
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))

    @staticmethod
    def is_itemview_chrome(widget: QWidget) -> bool:
        if not isValid(widget):
            return False
        try:
            view = widget.parentWidget()
            return (
                isinstance(view, QAbstractItemView)
                and not isinstance(view, QHeaderView)
                and (
                    isinstance(widget, QHeaderView)
                    or widget.metaObject().className() == 'QTableCornerButton'
                    or widget.objectName() == 'qt_scrollarea_viewport'
                )
            )
        except RuntimeError:
            # Qt may dispatch widget events during native popup destruction
            return False

    def update_itemview_shape(self, widget: QWidget) -> None:
        """Clip Qt viewports and headers against their parent's rounded Glass frame."""
        if QApplication.platformName() != 'xcb' or not self.is_itemview_chrome(widget):
            return
        try:
            view = widget.parentWidget()
        except RuntimeError:
            return
        if not isinstance(view, QAbstractItemView) or not isValid(view):
            return
        if min(widget.width(), widget.height(), view.width(), view.height()) < 2:
            return

        # Qt child viewports and header cells paint rectangular backgrounds
        # independently of the parent's rounded border
        frame = QPainterPath()
        frame.addRoundedRect(QRectF(view.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        view_region = QRegion(frame.toFillPolygon().toPolygon())
        child_region = view_region.intersected(QRegion(widget.geometry())).translated(-widget.pos())
        self._clipped_itemviews.add(widget)
        # Do not intersect a stale Qt stylesheet mask: on resize it still has
        # the old header width and would clip newly exposed header cells
        if child_region != widget.mask():
            widget.setMask(child_region)

    def _refresh_itemview_children(self, view: QAbstractItemView) -> None:
        if not self._active or not isValid(view):
            return
        for child in view.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly):
            self.update_itemview_shape(child)

    def _schedule_itemview_refresh(self, widget: QWidget) -> None:
        if QApplication.platformName() != 'xcb':
            return
        if not isinstance(widget, QAbstractItemView) or isinstance(widget, QHeaderView):
            return
        # Qt may reposition headers *after* notifying the parent of its resize
        QTimer.singleShot(0, widget, lambda: self._refresh_itemview_children(widget))

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget) or not isValid(watched):
            return False
        kind = event.type()
        if kind == QEvent.Type.Polish:
            self.polish(watched)
            self.update_itemview_shape(watched)
        elif kind in {QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowStateChange}:
            self.update_window_shape(watched)
            self.update_itemview_shape(watched)
            if kind in {QEvent.Type.Show, QEvent.Type.Resize}:
                self._schedule_itemview_refresh(watched)
        elif kind in {QEvent.Type.Move, QEvent.Type.StyleChange}:
            self.update_itemview_shape(watched)
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

    def _mark_button(self, button: QPushButton, name: str) -> None:
        # Properties supplied by the application belong to the application.
        # Only record and undo the dynamic properties Glass itself introduces.
        if button.property(name) is not None:
            return
        button.setProperty(name, True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
        self._button_properties.setdefault(button, set()).add(name)
        _repolish_later(button)

    def polish(self, widget: QWidget) -> None:
        if _is_window(widget):
            self.remember_window(widget)
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
            self._mark_button(widget, 'glassArrow')
        elif (
            0 < widget.maximumWidth() <= _COMPACT_BUTTON_MAX and widget.property('compact') is None
        ):
            self._mark_button(widget, 'compact')
