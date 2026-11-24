"""Give existing opaque native windows a real alpha channel when entering Glass."""

from __future__ import annotations

from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

# Platforms that fix a window's alpha channel when its native window is created.
# Qt creates a window before polishing it, so Glass often sets translucency too
# late and has to recreate the native window: X11 keeps the opaque visual, and
# Windows fills the transparent corners in solid
RECREATING_PLATFORMS: Final = frozenset({'xcb', 'windows'})

# Window flags rounded popups need on Windows, and the property recording which
# of them Glass added so leaving Glass can take them back
_WINDOWS_POPUP_FLAGS: Final = (
    Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
)
_ADDED_POPUP_FLAGS: Final = 'glassAddedPopupFlags'


def has_alpha(window: QWidget) -> bool:
    """Whether the window's native surface already has an alpha channel."""
    return (
        window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        and window.windowHandle().format().alphaBufferSize() > 0
    )


def enable_transparency(window: QWidget) -> None:
    # A created native window keeps its opaque surface until it is recreated
    recreate = (
        QApplication.platformName() in RECREATING_PLATFORMS
        and window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        and not has_alpha(window)
    )
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    if recreate:
        _recreate_native_window(window)


def enable_popup_transparency(popup: QWidget) -> None:
    """Give a menu, combo popup or tooltip an alpha channel for its rounded corners."""
    if QApplication.platformName() == 'windows':
        # Windows only gives a window per-pixel alpha when it is explicitly
        # frameless, which popups are by type but not by flag, and draws its drop
        # shadow around the popup's square bounds. Set before recreating below
        missing = _WINDOWS_POPUP_FLAGS & ~popup.windowFlags()
        if missing:
            popup.setProperty(_ADDED_POPUP_FLAGS, missing.value)
            popup.setWindowFlags(popup.windowFlags() | missing)
    enable_transparency(popup)


def restore_popup_flags(popup: QWidget) -> None:
    """Take back the window flags enable_popup_transparency added."""
    added = popup.property(_ADDED_POPUP_FLAGS)
    if not isinstance(added, int):
        return

    # None removes the dynamic property again
    popup.setProperty(_ADDED_POPUP_FLAGS, None)
    popup.setWindowFlags(popup.windowFlags() & ~Qt.WindowType(added))


def restore_transparency(window: QWidget, *, translucent: bool, no_system_background: bool) -> None:
    """Restore the original attributes and native surface after leaving Glass."""
    recreate = (
        QApplication.platformName() in RECREATING_PLATFORMS
        and window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        and has_alpha(window)
        and not translucent
    )
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=translucent)
    window.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, on=no_system_background)

    if recreate:
        _recreate_native_window(window)


def _recreate_native_window(window: QWidget) -> None:
    """Recreate a native window while keeping its state, geometry and focus."""
    visible = window.isVisible()
    active = window.isActiveWindow()
    geometry = window.geometry()
    window_state = window.windowState()
    screen = window.screen()
    focus = QApplication.focusWidget()
    restore_focus = focus is not None and window.isAncestorOf(focus)

    window.hide()
    window.destroy()
    window.setScreen(screen)
    window.create()

    window.setGeometry(geometry)
    window.setWindowState(window_state)
    if visible:
        window.show()
        if active:
            window.activateWindow()
        if restore_focus and focus is not None:
            focus.setFocus(Qt.FocusReason.OtherFocusReason)
