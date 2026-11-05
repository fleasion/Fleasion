"""Named, keyboard-accessible accent swatches."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QColorDialog,
    QGridLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from fleasion.appearance import ACCENTS, accent_name
from fleasion.appearance.colors import on_accent
from fleasion.localization import tr

if TYPE_CHECKING:
    from PySide6.QtWidgets import QAbstractButton


ACCENT_KEYS = ('System', *ACCENTS, 'Custom')


def choice_label(name: str) -> str:
    return {
        'Glass': tr('settings.appearance.theme.glass'),
        'Dark': tr('settings.appearance.theme.dark'),
        'Light': tr('settings.appearance.theme.light'),
        'System': tr('settings.appearance.theme.system'),
        'Custom': tr('settings.appearance.custom'),
    }.get(name, name)


def swatch_icon(name: str, *, selected: bool = False, custom_color: str = '#a17dff') -> QIcon:
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor('#808080'))
    if name == 'System':
        from fleasion.gui.theme import ThemeManager

        color = ThemeManager.system_accent_color()
    elif name == 'Custom':
        color = QColor(custom_color)
    else:
        resolved = accent_name(name)
        color = QColor(resolved if resolved.startswith('#') else ACCENTS[resolved])
    painter.setBrush(color)
    painter.drawEllipse(3, 3, 26, 26)
    if selected:
        pen = painter.pen()
        pen.setColor(on_accent(color))
        pen.setWidth(3)
        painter.setPen(pen)
        painter.drawLine(9, 16, 14, 21)
        painter.drawLine(14, 21, 24, 10)
    painter.end()
    return QIcon(pixmap)


class AccentSelector(QWidget):
    accent_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None, *, custom_color: str = '#a17dff') -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        self._selected_name: str | None = None
        self._accent = 'Ice'
        self._custom_color = (
            accent_name(custom_color) if accent_name(custom_color).startswith('#') else '#a17dff'
        )
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        for index, name in enumerate(ACCENT_KEYS):
            button = QPushButton(choice_label(name))
            button.setObjectName(f'Accent{name}')
            button.setProperty('accentName', name)
            button.setAccessibleName(tr('settings.appearance.accent_named', name=name))
            button.setCheckable(True)
            button.setIcon(swatch_icon(name, custom_color=self._custom_color))
            button.setIconSize(QSize(16, 16))
            self._group.addButton(button)
            self._buttons[name] = button
            layout.addWidget(button, index // 3, index % 3)
        self._group.buttonClicked.connect(self._selected)

    def _selected(self, button: QAbstractButton) -> None:
        name = str(button.property('accentName'))
        if name == 'Custom':
            picked = QColorDialog.getColor(
                QColor(self._custom_color), self, tr('settings.appearance.accent')
            )
            if not picked.isValid():
                if self._selected_name is not None:
                    self._buttons[self._selected_name].setChecked(True)
                return
            color = picked.name()
            self.set_accent(color)
            self.accent_changed.emit(color)
            return
        self.set_accent(name)
        self.accent_changed.emit(name)

    def set_custom_color(self, color: str) -> None:
        resolved = accent_name(color)
        if not resolved.startswith('#'):
            return
        self._custom_color = resolved
        self._buttons['Custom'].setIcon(
            swatch_icon('Custom', selected=self._selected_name == 'Custom', custom_color=resolved)
        )

    def set_accent(self, name: str) -> None:
        resolved = accent_name(name)
        if resolved.startswith('#'):
            self.set_custom_color(resolved)
        selected = 'Custom' if resolved.startswith('#') else resolved
        if self._selected_name == selected and self._accent == resolved:
            return
        if self._selected_name is not None and self._selected_name != selected:
            self._buttons[self._selected_name].setIcon(
                swatch_icon(self._selected_name, custom_color=self._custom_color)
            )
        self._buttons[selected].setChecked(True)
        self._buttons[selected].setIcon(
            swatch_icon(selected, selected=True, custom_color=self._custom_color)
        )
        self._selected_name = selected
        self._accent = resolved

    def accent(self) -> str:
        return self._accent
