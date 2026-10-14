"""Own Glass rules at widget roots without rebuilding the application stylesheet."""

from __future__ import annotations

from typing import TYPE_CHECKING
from weakref import WeakKeyDictionary

from shiboken6 import isValid

if TYPE_CHECKING:
    from PySide6.QtWidgets import QApplication, QWidget


class WindowStyles:
    def __init__(self) -> None:
        self._stylesheet = ''
        self._originals: WeakKeyDictionary[QWidget, str] = WeakKeyDictionary()

    def apply(self, app: QApplication, stylesheet: str) -> None:
        self._stylesheet = stylesheet
        for widget in app.topLevelWidgets():
            self.polish(widget)

    def polish(self, widget: QWidget) -> None:
        if not isValid(widget):
            return
        if widget.parentWidget() is not None:
            # Parented dialogs and menus inherit their root's rules, too
            self._restore(widget)
            return
        if not self._stylesheet:
            return
        if widget not in self._originals:
            self._originals[widget] = widget.styleSheet()
        original = self._originals[widget]
        combined = f'{self._stylesheet}\n{original}' if original else self._stylesheet
        if widget.styleSheet() != combined:
            widget.setStyleSheet(combined)

    def _restore(self, widget: QWidget) -> None:
        if widget in self._originals:
            widget.setStyleSheet(self._originals.pop(widget))

    def restore(self) -> None:
        self._stylesheet = ''
        for widget in list(self._originals):
            if isValid(widget):
                self._restore(widget)
        self._originals.clear()
