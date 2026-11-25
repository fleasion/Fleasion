"""Dialog buttons show line icons on desktops without an icon theme."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import QApplication, QStyle

from fleasion.gui.theme import ThemeManager
from fleasion.widgets.dialog_icons import dialog_icon, tinted

if TYPE_CHECKING:
    from collections.abc import Iterator

_SP = QStyle.StandardPixmap


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    theme = QIcon.themeName()

    # Stand in for Windows and macOS, which have no dialog icons in a theme
    QIcon.setThemeName('')
    try:
        yield application
    finally:
        QIcon.setThemeName(theme)
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')


def test_dialog_buttons_have_icons_on_every_desktop(app: QApplication) -> None:
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    hint = QStyle.StyleHint.SH_DialogButtonBox_ButtonsHaveIcons
    assert app.style().styleHint(hint) == 1


def test_style_draws_line_icons_without_a_theme(app: QApplication) -> None:
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    for pixmap in (_SP.SP_DialogOkButton, _SP.SP_DialogCancelButton, _SP.SP_DialogYesButton):
        icon = app.style().standardIcon(pixmap)
        assert icon.name().startswith('fleasion-dialog-')
        assert not icon.pixmap(QSize(16, 16)).toImage().isNull()

    # Other standard icons stay Qt's
    assert dialog_icon(_SP.SP_DirIcon) is None


def test_tinted_icon_takes_the_label_color(app: QApplication) -> None:
    del app
    icon = dialog_icon(_SP.SP_DialogOkButton)
    assert icon is not None

    image = tinted(icon, QColor('#ff0000'))
    assert image is not None
    pixels = image.pixmap(QSize(16, 16)).toImage()
    opaque = [
        pixels.pixelColor(x, y)
        for x in range(16)
        for y in range(16)
        if pixels.pixelColor(x, y).alpha() == 255
    ]
    assert opaque
    assert all(color.red() == 255 and color.green() == 0 for color in opaque)

    # Icons that are not line icons are left alone
    assert tinted(QIcon(), QColor('#ff0000')) is None
