"""Application appearance notifications and native palette updates."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, QObject, QTimer, Signal

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication


class AppearanceEvents(QObject):
    changed = Signal()

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._refresh_system)
        app.paletteChanged.connect(self._palette_changed)
        app.styleHints().colorSchemeChanged.connect(self._schedule_refresh)
        app.installEventFilter(self)

    @staticmethod
    def _refresh_system() -> None:
        from .theme import ThemeManager

        ThemeManager.refresh_system()

    def _palette_changed(self, palette: QPalette) -> None:
        from .theme import ThemeManager

        if ThemeManager.remember_native_palette(palette):
            self._timer.start(0)

    def _schedule_refresh(self) -> None:
        from .theme import ThemeManager

        if ThemeManager.follows_system():
            self._timer.start(0)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ThemeChange:
            self._schedule_refresh()
        return super().eventFilter(watched, event)
