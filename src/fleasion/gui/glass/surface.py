"""Painting the glass window surface and its selected backdrop."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final, override
from weakref import WeakKeyDictionary

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
    QRegion,
    QTransform,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHeaderView,
    QMainWindow,
    QMenu,
    QWidget,
)
from shiboken6 import isValid

from .desktop_blur import blur
from .itemview import ItemViewChrome, RoundedItemViewEffect
from .layered import LayeredResizeGuard
from .menus import TOOLTIP_RADIUS
from .state import state
from .style import RADIUS, glass_colors
from .transparency import (
    RECREATING_PLATFORMS,
    enable_popup_transparency,
    enable_transparency,
    has_alpha,
    restore_popup_flags,
    restore_transparency,
)
from .views import ViewSurface
from .wallpaper import wallpapers

if TYPE_CHECKING:
    from PySide6.QtGui import QScreen

    from .stylesheets import WindowStyles


# Painted under the tint when there is no alpha channel or wallpaper to show
_FALLBACK_BACKDROP: Final = (20, 22, 34)

# Ambient glows: accent from the top left, a complementary hue from the bottom right
_AMBIENT_ACCENT_ALPHA: Final = 230
_AMBIENT_COMPLEMENT_ALPHA: Final = 170
_AMBIENT_HUE_SHIFT: Final = 140

# Qt creates combo popups and tooltips itself, so their Python wrappers can be
# collected while the widget lives on. The original attributes are therefore
# stored on the widget, as bit flags in a dynamic property
_ORIGINAL_TRANSPARENCY: Final = 'glassOriginalTransparency'
_TRANSLUCENT: Final = 1
_NO_SYSTEM_BACKGROUND: Final = 2


#
# Backdrops
#


def _fallback_color() -> QColor:
    return (
        QColor(*_FALLBACK_BACKDROP)
        if state.dark and state.base_palette is None
        else glass_colors().surface
    )


def effective_opacity() -> float:
    """Use the chosen opacity regardless of compositor blur support; Flat stays opaque."""
    return 1.0 if state.settings.backdrop == 'flat' else state.settings.opacity


def _glow(centre: QPointF, reach: float, color: QColor, alpha: int) -> QBrush:
    """A radial gradient fading from `color` at `alpha` to fully transparent."""
    inner = QColor(color)
    inner.setAlpha(alpha)

    outer = QColor(color)
    outer.setAlpha(0)

    gradient = QRadialGradient(centre, reach)
    gradient.setColorAt(0.0, inner)
    gradient.setColorAt(1.0, outer)
    return QBrush(gradient)


def _paint_ambient(painter: QPainter, rect: QRectF, path: QPainterPath) -> None:
    colors = glass_colors()
    painter.fillPath(path, colors.surface)

    reach = max(rect.width(), rect.height())
    hue = (max(colors.accent.hsvHue(), 0) + _AMBIENT_HUE_SHIFT) % 360
    complement = QColor.fromHsv(hue, 120, 200)

    top_left = QPointF(rect.left() + rect.width() * 0.15, rect.top())
    painter.fillPath(path, _glow(top_left, reach * 0.9, colors.accent, _AMBIENT_ACCENT_ALPHA))
    painter.fillPath(
        path, _glow(rect.bottomRight(), reach * 0.8, complement, _AMBIENT_COMPLEMENT_ALPHA)
    )


def _screen(window: QWidget) -> QScreen | None:
    # Qt returns no screen for a window that is not shown yet
    return window.screen()


def _origin_in_screen(window: QWidget, width: int, height: int) -> QPoint:
    """Where the window sits on its screen; centred when the platform hides it."""
    screen = _screen(window)

    # Wayland clients cannot learn their global position
    if QApplication.platformName().startswith('wayland') or screen is None:
        return QPoint((width - window.width()) // 2, (height - window.height()) // 2)

    return window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()


def _paint_wallpaper(painter: QPainter, window: QWidget, path: QPainterPath) -> None:
    screen = _screen(window)
    if screen is None:
        return

    # Cover the screen, or the window if it is larger than the screen
    geometry = screen.geometry()
    width = max(geometry.width(), window.width())
    height = max(geometry.height(), window.height())

    image = wallpapers.image(width, height, screen.name())
    if image.isNull():
        painter.fillPath(path, _fallback_color())
        return

    # Shift the screen-sized image so the window shows its own slice of it
    origin = _origin_in_screen(window, width, height)
    wallpaper = QBrush(image)
    wallpaper.setTransform(QTransform.fromTranslate(-origin.x(), -origin.y()))
    painter.fillPath(path, wallpaper)


#
# Surface
#


def paint_glass(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    window: QWidget | None = None,
    *,
    rim: bool = False,
) -> None:
    """Replace the previous surface, then paint the backdrop, tint, sheen and optional rim."""
    # Clear the backing store first; translucent paints otherwise keep old child pixels
    painter.save()
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    painter.fillRect(rect.toAlignedRect(), Qt.GlobalColor.transparent)
    painter.restore()

    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    # Backdrop
    backdrop = state.settings.backdrop
    if window is not None and _opaque_fallback(window):
        painter.fillPath(path, _fallback_color())
    if window is not None and backdrop == 'wallpaper':
        _paint_wallpaper(painter, window, path)
    elif backdrop == 'ambient':
        _paint_ambient(painter, rect, path)

    # Tint
    surface = QColor(glass_colors().surface)
    surface.setAlpha(round(255 * effective_opacity()))
    painter.fillPath(path, surface)

    # Sheen: a faint, even falloff; a front-loaded sheen reads as a bright band
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sheen.setColorAt(0.0, QColor(255, 255, 255, 12))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.fillPath(path, sheen)

    if not rim:
        return

    # Rim: a bright top edge fading down the sides
    edge = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    edge.setColorAt(0.0, QColor(255, 255, 255, 60))
    edge.setColorAt(0.3, QColor(255, 255, 255, 22))
    edge.setColorAt(1.0, QColor(255, 255, 255, 14))
    painter.setPen(QPen(edge, 1.0))
    painter.drawPath(path)


#
# Helpers
#


def _is_window(obj: QObject | None) -> bool:
    return isinstance(obj, (QMainWindow, QDialog)) and obj.isWindow()


def _opaque_fallback(window: QWidget) -> bool:
    # Windows and macOS require frameless windows for per-pixel translucency
    return sys.platform in {'win32', 'darwin'} and not (
        window.windowFlags() & Qt.WindowType.FramelessWindowHint
    )


#
# Windows
#


# Events the Glass window filter reacts to; it filters every event in the application
_SURFACE_EVENTS: Final = frozenset(
    {
        QEvent.Type.Polish,
        QEvent.Type.ParentChange,
        QEvent.Type.Show,
        QEvent.Type.Resize,
        QEvent.Type.WindowStateChange,
        QEvent.Type.Move,
        QEvent.Type.StyleChange,
        QEvent.Type.Paint,
        QEvent.Type.UpdateRequest,
    }
)


class GlassWindows(QObject):
    """Gives top-level windows an alpha surface and paints the glass on them."""

    blur_status_changed = Signal()

    def __init__(
        self,
        app: QApplication,
        original_popup_masks: dict[QWidget, QRegion] | None = None,
        stylesheets: WindowStyles | None = None,
        original_popup_attributes: dict[QWidget, tuple[bool, bool]] | None = None,
    ) -> None:
        super().__init__(app)
        self._app = app
        self._stylesheets = stylesheets

        # State to undo when Glass is turned off
        self._original_popup_masks: WeakKeyDictionary[QWidget, QRegion] = WeakKeyDictionary(
            original_popup_masks or {}
        )
        self._original_popup_attributes: WeakKeyDictionary[QWidget, tuple[bool, bool]] = (
            WeakKeyDictionary(original_popup_attributes or {})
        )
        self._original_masks: WeakKeyDictionary[QWidget, QRegion] = WeakKeyDictionary()

        # Layered windows on Windows must not repaint ahead of a pending resize
        self._layered = LayeredResizeGuard(self)

        # Poll for wallpaper and compositor blur changes made outside Fleasion
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

        # Window transparency. Parents go before their popup children, since
        # recreating a native X11 parent may also invalidate its children's handles

        def parent_depth(window: QWidget) -> int:
            depth = 0
            parent = window.parentWidget()
            while parent is not None:
                depth += 1
                parent = parent.parentWidget()
            return depth

        remembered = [
            widget
            for widget in self._app.allWidgets()
            if isValid(widget) and isinstance(widget.property(_ORIGINAL_TRANSPARENCY), int)
        ]
        for window in sorted(remembered, key=parent_depth):
            if not isValid(window):
                continue

            original = window.property(_ORIGINAL_TRANSPARENCY)
            flags = original if isinstance(original, int) else 0

            # None removes the dynamic property again
            window.setProperty(_ORIGINAL_TRANSPARENCY, None)
            restore_popup_flags(window)
            restore_transparency(
                window,
                translucent=bool(flags & _TRANSLUCENT),
                no_system_background=bool(flags & _NO_SYSTEM_BACKGROUND),
            )
            window.update()

        # Window masks
        for widget, original_mask in list(self._original_masks.items()):
            if isValid(widget):
                if original_mask.isEmpty():
                    widget.clearMask()
                else:
                    widget.setMask(original_mask)
        self._original_masks.clear()

        # Rounded item views
        for view in self._app.allWidgets():
            if isValid(view) and isinstance(view.graphicsEffect(), RoundedItemViewEffect):
                view.setGraphicsEffect(None)  # pyright: ignore[reportArgumentType]
        ItemViewChrome.detach_all(self._app.allWidgets())
        ViewSurface.detach_all(self._app.allWidgets())

        # Popup masks. QSS removal does not always undo the masks Qt adds to native popups
        for widget in self._app.allWidgets():
            if not isValid(widget) or not self.is_rounded_popup(widget):
                continue

            original = self._original_popup_masks.get(widget)
            if original is None or original.isEmpty():
                widget.clearMask()
            else:
                widget.setMask(original)
        self._original_popup_masks.clear()
        self._original_popup_attributes.clear()

    def remember_window(self, window: QWidget) -> None:
        # Root windows created during Glass predate their custom title bar, but
        # popup widgets may already have Glass QSS attributes when polished
        if isinstance(window.property(_ORIGINAL_TRANSPARENCY), int):
            return

        original = self._original_popup_attributes.get(window)
        if original is not None:
            translucent, no_background = original
        elif QApplication.platformName() == 'xcb' and self.is_rounded_popup(window):
            # Popups created during Glass should return to opaque X11 visuals
            translucent, no_background = False, False
        else:
            translucent = window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            no_background = window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

        flags = (_TRANSLUCENT if translucent else 0) | (
            _NO_SYSTEM_BACKGROUND if no_background else 0
        )
        window.setProperty(_ORIGINAL_TRANSPARENCY, flags)

    #
    # Shape
    #

    @staticmethod
    def is_combo_popup(widget: QWidget) -> bool:
        return widget.isWindow() and widget.metaObject().className() == 'QComboBoxPrivateContainer'

    @staticmethod
    def is_rounded_popup(widget: QWidget) -> bool:
        return widget.isWindow() and (
            isinstance(widget, QMenu)
            or GlassWindows.is_combo_popup(widget)
            or widget.windowType() == Qt.WindowType.ToolTip
        )

    @staticmethod
    def _mask_radius(widget: QWidget) -> int:
        if widget.property('glassOwnSurface') or widget.isMaximized():
            return 0

        if _is_window(widget) and widget.property('glassChrome'):
            return RADIUS

        # Menus and combo popups
        if (
            widget.isWindow()
            and widget.windowFlags() & Qt.WindowType.Popup
            and (isinstance(widget, QMenu) or GlassWindows.is_combo_popup(widget))
        ):
            return 12

        if widget.isWindow() and widget.windowType() == Qt.WindowType.ToolTip:
            return round(TOOLTIP_RADIUS)

        return 0

    def update_window_shape(self, widget: QWidget) -> None:
        # On X11 and Windows, a window made translucent after it was created can
        # keep an opaque surface. QSS border-radius alone does not clip it
        if not widget.isWindow():
            return

        # Windows with an alpha channel round themselves; drop any mask we added
        if has_alpha(widget):
            if widget in self._original_masks:
                original = self._original_masks.pop(widget)
                if original.isEmpty():
                    widget.clearMask()
                else:
                    widget.setMask(original)
            if self.is_rounded_popup(widget):
                widget.clearMask()
            return

        if QApplication.platformName() not in RECREATING_PLATFORMS:
            return

        # Nothing to round; restore the original mask
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

        # Clip the opaque window to its rounded outline
        if widget not in self._original_masks:
            self._original_masks[widget] = QRegion(widget.mask())
        rect = QRectF(0, 0, widget.width(), widget.height())
        path = QPainterPath()
        path.addRoundedRect(rect, min(radius, widget.width() / 2), min(radius, widget.height() / 2))
        widget.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def polish_itemview(self, widget: QWidget) -> None:
        if not isinstance(widget, QAbstractItemView) or isinstance(widget, QHeaderView):
            return

        if widget.graphicsEffect() is None:
            popup = self.is_combo_popup(widget.window())
            radius = 12 if popup else RADIUS
            widget.setGraphicsEffect(RoundedItemViewEffect(widget, radius=radius, popup=popup))
        ItemViewChrome.attach(widget)
        ViewSurface.attach(widget)

    #
    # Events
    #

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Rejects most events by type before the costlier validity checks
        kind = event.type()
        if kind not in _SURFACE_EVENTS or not isinstance(watched, QWidget) or not isValid(watched):
            return False
        if kind == QEvent.Type.Polish:
            self.polish(watched)
            self.polish_itemview(watched)

        elif kind == QEvent.Type.ParentChange and self._stylesheets is not None:
            self._stylesheets.polish(watched)

        elif kind in {QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowStateChange}:
            # Qt has caught up with the native size of a window holding back an update
            if kind == QEvent.Type.Resize:
                self._layered.resized(watched)

            if (
                kind == QEvent.Type.Show
                and self._stylesheets is not None
                and (self.is_rounded_popup(watched) or watched.parentWidget() is None)
            ):
                self._stylesheets.polish(watched)
            self.update_window_shape(watched)
            self.polish_itemview(watched)

        elif kind in {QEvent.Type.Move, QEvent.Type.StyleChange}:
            self.polish_itemview(watched)

        elif kind == QEvent.Type.Paint and self._paints(watched):
            # Native decorations already outline the window, so only the
            # custom chrome draws its own rim
            painter = QPainter(watched)
            framed = bool(watched.property('glassChrome')) and not watched.isMaximized()
            rect = QRectF(watched.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            paint_glass(painter, rect, RADIUS if framed else 0, watched, rim=framed)
            painter.end()

        elif kind == QEvent.Type.UpdateRequest:
            return self._layered.defer(watched)

        elif (
            kind == QEvent.Type.Move
            and state.settings.backdrop == 'wallpaper'
            and _is_window(watched)
        ):
            # The wallpaper slice follows the window
            watched.update()

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

    #
    # Polish
    #

    def polish(self, widget: QWidget) -> None:
        if self._stylesheets is not None:
            self._stylesheets.polish(widget)

        # Top-level windows get an alpha channel where the platform allows one
        if _is_window(widget):
            self.remember_window(widget)
            translucent = not _opaque_fallback(widget)
            if translucent:
                enable_transparency(widget)
            else:
                widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=False)
                widget.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, on=False)

        elif self.is_rounded_popup(widget):
            self.remember_window(widget)
            enable_popup_transparency(widget)
