"""Glass leaves the application's own style sheet in place."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication

from fleasion.gui.glass import controller
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


_BASE_QSS = 'QDialogButtonBox, QMessageBox { button-layout: 0; }'


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    previous_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    previous_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    previous_stylesheet = application.styleSheet()
    ThemeManager.apply_theme('Light')
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(previous_theme)
        ThemeManager._effective_theme = previous_effective  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(previous_stylesheet)
        application.processEvents()


def test_glass_preserves_preexisting_app_qss_after_repeated_enable(app: QApplication) -> None:
    original = _BASE_QSS + '\nQLabel#PersistentLabel { color: red; }'
    app.setStyleSheet(original)
    for _ in range(3):
        ThemeManager.apply_theme('Glass')
        assert app.styleSheet() == original
        controller.theme.enable(app)
        assert app.styleSheet().count(original) == 1
        ThemeManager.apply_theme('Light')
        assert app.styleSheet() == original
