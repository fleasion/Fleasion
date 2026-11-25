"""Own Glass rules at widget roots without rebuilding the application stylesheet."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
)
from shiboken6 import isValid

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget


# Qt creates combo popups and tooltips itself, so their Python wrappers can be
# collected while the widget lives on. The original stylesheet stays on the widget
_ORIGINAL = 'glassOriginalStyleSheet'

_CONTROL_TYPES = (
    QAbstractButton,
    QAbstractSlider,
    QAbstractSpinBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QProgressBar,
)


class WindowStyles:
    """Prepends the Glass stylesheet to each top-level window's own."""

    def __init__(self) -> None:
        self._stylesheet = ''

    def apply(self, app: QApplication, stylesheet: str) -> None:
        self._stylesheet = stylesheet
        for widget in app.topLevelWidgets():
            self.polish(widget)

    def polish(self, widget: QWidget) -> None:
        if not isValid(widget):
            return

        # Parented dialogs and menus inherit their root's rules, too. Native combo
        # and tooltip panels need their own QSS to suppress square fills
        combo_popup = widget.metaObject().className() == 'QComboBoxPrivateContainer'
        tooltip = widget.windowType() == Qt.WindowType.ToolTip
        if widget.parentWidget() is not None and not (combo_popup or tooltip):
            self._restore(widget)
            return

        if not self._stylesheet:
            return

        # Controls are often polished before they are added to a layout, while
        # still parentless. Parsing the Glass rules for each of them and
        # stripping them again on reparenting costs about 2 ms per control.
        # A control is practically never a window, so style it only if it is
        # shown as one. Container widgets keep early styling so windows have
        # their rules while laying out
        if not (combo_popup or tooltip or widget.isVisible()) and isinstance(
            widget, _CONTROL_TYPES
        ):
            return

        # Remember the widget's own stylesheet once, then keep it after ours
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
