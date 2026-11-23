"""The application style orders dialog buttons and stays reachable under a style sheet."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QStyle

from fleasion.gui.theme import ThemeManager
from fleasion.widgets.style import ButtonOrderStyle, LayoutStyle

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    original_sheet = application.styleSheet()
    try:
        yield application
    finally:
        application.setStyleSheet(original_sheet)
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')


@pytest.mark.parametrize('style', [LayoutStyle(), ButtonOrderStyle('Windows')])
def test_dialog_buttons_keep_the_windows_order(app: QApplication, style: ButtonOrderStyle) -> None:
    del app
    layout = style.styleHint(QStyle.StyleHint.SH_DialogButtonLayout)
    assert layout == QDialogButtonBox.ButtonLayout.WinLayout.value


def test_menu_rows_reach_the_style_beneath_an_application_style_sheet(app: QApplication) -> None:
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    app.setStyleSheet('QLabel { color: red; }')

    # Qt wraps the style for the sheet; drawing through the wrapper would skip
    # the Glass menu rows, so they draw with the style it wraps
    assert QApplication.style().metaObject().className() == 'QStyleSheetStyle'
    assert isinstance(ButtonOrderStyle.application_style(), LayoutStyle)
