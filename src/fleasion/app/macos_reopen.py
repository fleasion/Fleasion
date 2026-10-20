"""Show the dashboard when Fleasion is reopened on macOS with no windows open."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from PySide6.QtCore import Qt

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication

REOPEN_ARM_DELAY_MS = 2000
"""Wait out the activation that comes with launching before handling reopens."""


class _Tray(Protocol):
    @property
    def exiting(self) -> bool: ...

    def show_replacer_config(self) -> None: ...


def has_visible_windows(app: QApplication) -> bool:
    """Whether a real window is showing; menus and tooltips do not count."""
    return any(
        widget.isVisible() and widget.windowType() in {Qt.WindowType.Window, Qt.WindowType.Dialog}
        for widget in app.topLevelWidgets()
    )


def install_macos_reopen_handler(app: QApplication, tray: _Tray) -> None:
    """Show the dashboard when Fleasion is reopened with no windows open.

    Clicking the Dock icon or opening Fleasion.app again while it runs only in
    the menu bar sends the running instance a reopen event, which Qt reports
    as a forced ApplicationActive state change. Without this, nothing appears.
    """

    def on_state_changed(state: Qt.ApplicationState) -> None:
        if state != Qt.ApplicationState.ApplicationActive or tray.exiting:
            return
        if has_visible_windows(app):
            return
        tray.show_replacer_config()

    app.applicationStateChanged.connect(on_state_changed)
