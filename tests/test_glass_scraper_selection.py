"""Scraper alternating rows stay stable when Glass gains its first selection."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fleasion.cache import cache_manager as cache_module
from fleasion.cache.cache_viewer import CacheViewerTab
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication([])
    old_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    old_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    old_sheet = application.styleSheet()
    try:
        yield application
    finally:
        ThemeManager.apply_theme('Light')
        ThemeManager.apply_theme(old_theme)
        ThemeManager._effective_theme = old_effective  # pyright: ignore[reportPrivateUsage]
        application.setStyleSheet(old_sheet)
        application.processEvents()


def _row_colors(table: QTableWidget) -> list[QColor]:
    image = table.viewport().grab().toImage()
    # Sample the unused right edge rather than the row's white text pixels
    x = table.viewport().width() - 12
    return [
        image.pixelColor(x, table.rowViewportPosition(row) + table.rowHeight(row) // 2)
        for row in range(table.rowCount())
    ]


def test_glass_scraper_selected_row_is_subtle_and_unselected_rows_keep_colors(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, 'CONFIG_DIR', tmp_path)
    ThemeManager.apply_theme('System')
    container = QWidget()
    container.resize(830, 470)
    layout = QVBoxLayout(container)
    scraper = CacheViewerTab(cache_module.CacheManager(), defer_setup=True)
    monkeypatch.setattr(scraper, '_on_selection_changed', lambda: None)
    scraper._create_table(layout)  # pyright: ignore[reportPrivateUsage]
    # Exercise the PaletteChange handler used by a fully initialized Scraper.
    scraper._ui_ready = True  # pyright: ignore[reportPrivateUsage]
    table = scraper.table
    table.setRowCount(6)
    for row in range(6):
        for column in range(1, 5):
            table.setItem(row, column, QTableWidgetItem(f'{row}-{column}'))
    try:
        container.show()
        ThemeManager.apply_theme('Glass')
        QTest.qWait(100)
        table.clearSelection()
        table.setCurrentItem(None)  # pyright: ignore[reportArgumentType] - Qt supports None
        table.clearFocus()
        QTest.qWait(20)
        before = _row_colors(table)
        assert table.objectName() == 'ScraperAssetsTable'
        glass_alternate = table.palette().color(QPalette.ColorRole.AlternateBase)
        assert glass_alternate == QColor(255, 255, 255, 9)
        assert before[1].alpha() < 255
        header = table.horizontalHeader()
        header_image = header.grab().toImage()
        assert header.frameWidth() == 0
        assert header_image.width() > 40
        assert header_image.pixelColor(0, 3).alpha() == header_image.pixelColor(20, 3).alpha()
        assert before[0] != before[1]
        assert before[0] == before[2]
        assert before[1] == before[3]

        table.setCurrentCell(0, 1)
        QTest.qWait(50)
        first_selected = _row_colors(table)
        assert first_selected[1:] == before[1:]
        assert first_selected[0] != before[0]
        # Previously the first click produced a saturated blue (R=114, alpha=177)
        # over the almost-black rows, rather than a restrained selection wash
        assert first_selected[0].red() < 105
        assert first_selected[0].alpha() < 165

        table.setCurrentCell(1, 1)
        QTest.qWait(50)
        second_selected = _row_colors(table)
        assert second_selected[0] == before[0]
        assert second_selected[2:] == before[2:]
        assert second_selected[1] != before[1]

        ThemeManager.apply_theme('System')
        QTest.qWait(50)
        table.clearSelection()
        after_system = _row_colors(table)
        assert all(color.alpha() == 255 for color in after_system), (
            [color.getRgb() for color in after_system],
            table.palette().color(table.backgroundRole()).getRgb(),
            table.viewport().palette().color(table.viewport().backgroundRole()).getRgb(),
            table.viewport().testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground),
        )

        ThemeManager.apply_theme('Dark')
        QTest.qWait(30)
        assert table.palette().color(QPalette.ColorRole.AlternateBase) == QColor(53, 53, 53)
        assert all(color.alpha() == 255 for color in _row_colors(table))
    finally:
        ThemeManager.apply_theme('Light')
        # This fixture builds only the table, not the remaining timers/loaders.
        scraper._ui_ready = False  # pyright: ignore[reportPrivateUsage]
        scraper.shutdown()
        container.close()
        scraper.deleteLater()
        container.deleteLater()
        app.processEvents()
