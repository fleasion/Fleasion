"""Push buttons drawn from cached pixmaps still follow their state and the theme."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QPushButton, QStyle, QStyleOptionButton

from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator

_State = QStyle.StateFlag


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    try:
        yield application
    finally:
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')


def _render(
    button: QPushButton,
    state: QStyle.StateFlag,
    direction: Qt.LayoutDirection = Qt.LayoutDirection.LayoutDirectionAuto,
) -> QImage:
    option = QStyleOptionButton()
    button.initStyleOption(option)
    option.state = state
    image = QImage(button.size(), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setFont(button.font())
    painter.setLayoutDirection(direction)
    button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
    painter.end()
    return image


def test_cached_buttons_follow_state_and_theme(app: QApplication) -> None:
    del app
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    button = QPushButton('Browse')
    button.resize(90, 28)
    resting_state = _State.State_Enabled | _State.State_Raised

    resting = _render(button, resting_state)
    hovered = _render(button, resting_state | _State.State_MouseOver)
    assert hovered != resting
    assert _render(button, resting_state) == resting

    ThemeManager.apply_appearance('Glass', 'Light', 'Ice')
    assert _render(button, resting_state) != resting


def _ink_columns(image: QImage, blank: QImage) -> list[int]:
    """Columns where image differs from the same button drawn without a label."""
    return [
        x
        for x in range(image.width())
        if any(image.pixel(x, y) != blank.pixel(x, y) for y in range(image.height()))
    ]


def test_cached_button_keeps_icon_and_text_together(app: QApplication) -> None:
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    icon = app.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton)
    button = QPushButton(icon, 'Cancel')
    button.resize(200, 28)
    blank = QPushButton()
    blank.resize(button.size())
    state = _State.State_Enabled | _State.State_Raised

    # A widget's painter runs left to right, unlike a bare image painter
    columns = _ink_columns(
        _render(button, state, Qt.LayoutDirection.LeftToRight),
        _render(blank, state, Qt.LayoutDirection.LeftToRight),
    )

    # The icon and text sit as one group in the middle, not split to either edge
    left, right = columns[0], columns[-1]
    assert right - left < 100
    assert abs((left + right) / 2 - button.width() / 2) <= 4
