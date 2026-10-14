"""Named, keyboard-accessible accent swatches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QButtonGroup, QGridLayout, QPushButton, QSizePolicy, QWidget

from fleasion.appearance import ACCENTS, accent_name
from fleasion.appearance.colors import on_accent
from fleasion.localization import tr

if TYPE_CHECKING:
    from PySide6.QtWidgets import QAbstractButton


def choice_label(name: str) -> str:
    return {
        'Glass': tr('settings.appearance.theme.glass'),
        'Dark': tr('settings.appearance.theme.dark'),
        'Light': tr('settings.appearance.theme.light'),
        'System': tr('settings.appearance.theme.system'),
    }.get(name, name)


def swatch_icon(name: str, *, selected: bool = False) -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor('#808080'))
    painter.setBrush(QColor(ACCENTS[accent_name(name)]))
    painter.drawEllipse(3, 3, 26, 26)
    if selected:
        pen = painter.pen()
        pen.setColor(on_accent(QColor(ACCENTS[accent_name(name)])))
        pen.setWidth(3)
        painter.setPen(pen)
        painter.drawLine(9, 16, 14, 21)
        painter.drawLine(14, 21, 24, 10)
    painter.end()
    return QIcon(pixmap)


class AccentSelector(QWidget):
    accent_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._selected_name: str | None = None
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, name in enumerate(ACCENTS):
            button = QPushButton(name)
            button.setObjectName(f'Accent{name}')
            button.setAccessibleName(tr('settings.appearance.accent_named', name=name))
            button.setCheckable(True)
            button.setIcon(swatch_icon(name))
            button.setIconSize(QSize(16, 16))
            self._group.addButton(button)
            self._buttons[name] = button
            layout.addWidget(button, index // 3, index % 3)
        self._group.buttonClicked.connect(self._selected)

    def _selected(self, button: QAbstractButton) -> None:
        self.set_accent(button.text())
        self.accent_changed.emit(button.text())

    def set_accent(self, name: str) -> None:
        selected = accent_name(name)
        if self._selected_name == selected:
            return
        if self._selected_name is not None:
            self._buttons[self._selected_name].setIcon(swatch_icon(self._selected_name))
        self._buttons[selected].setChecked(True)
        self._buttons[selected].setIcon(swatch_icon(selected, selected=True))
        self._selected_name = selected

    def accent(self) -> str:
        button = self._group.checkedButton()
        return button.text() if button is not None else accent_name(None)
