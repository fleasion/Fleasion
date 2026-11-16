"""Own Glass rules at widget roots without rebuilding the application stylesheet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

# Qt creates combo popups and tooltips itself, so their Python wrappers can be
# collected while the widget lives on. The original stylesheet stays on the widget
_ORIGINAL = 'glassOriginalStyleSheet'


class WindowStyles:
    def __init__(self) -> None:
        self._stylesheet = ''

    def apply(self, app: QApplication, stylesheet: str) -> None:
        self._stylesheet = stylesheet
        for widget in app.topLevelWidgets():
            self.polish(widget)

    def polish(self, widget: QWidget) -> None:
        if not isValid(widget):
            return
        combo_popup = widget.metaObject().className() == 'QComboBoxPrivateContainer'
        tooltip = widget.windowType() == Qt.WindowType.ToolTip
        if widget.parentWidget() is not None and not (combo_popup or tooltip):
            # Parented dialogs and menus inherit their root's rules, too
            self._restore(widget)
            return
        # Native combo and tooltip panels need their own QSS to suppress square fills
        if not self._stylesheet:
            return
        original = widget.property(_ORIGINAL)
        if not isinstance(original, str):
            original = widget.styleSheet()
            widget.setProperty(_ORIGINAL, original)
        combined = f'{self._stylesheet}\n{original}' if original else self._stylesheet
        if widget.styleSheet() != combined:
            widget.setStyleSheet(combined)

    @staticmethod
    def _restore(widget: QWidget) -> None:
        original = widget.property(_ORIGINAL)
        if isinstance(original, str):
            # None removes the dynamic property again
            widget.setProperty(_ORIGINAL, None)
            widget.setStyleSheet(original)

    def restore(self) -> None:
        self._stylesheet = ''
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        for widget in app.allWidgets():
            if isValid(widget):
                self._restore(widget)
