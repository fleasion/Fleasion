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
