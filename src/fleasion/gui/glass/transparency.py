"""Give existing opaque X11 windows a real alpha channel when entering Glass."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget


def has_alpha(window: QWidget) -> bool:
    return (
        window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        and window.windowHandle().format().alphaBufferSize() > 0
    )


def enable_transparency(window: QWidget) -> None:
    recreate = (
        QApplication.platformName() == 'xcb'
        and window.testAttribute(Qt.WidgetAttribute.WA_WState_Created)
        and not has_alpha(window)
    )
    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    if not recreate:
        return

    # Changing the attribute alone leaves an existing native visual opaque
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
