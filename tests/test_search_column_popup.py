"""Search-column popup surface and checkbox behavior across themes."""

from __future__ import annotations

import os
from typing import cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidgetAction

from fleasion.cache.cache_viewer import ColumnFilterPopup
from fleasion.gui.theme import ThemeManager


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def test_search_column_popup_paints_opaque_glass_surface() -> None:
    """No table text should show through the popup or its embedded container."""
    app = _qapp()
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    popup = ColumnFilterPopup()
    try:
        popup.popup(QPoint(50, 50))
        QTest.qWait(60)
        assert popup.isVisible()
        opaque_surface = popup.palette().toolTipBase().color()
        assert opaque_surface.alpha() == 255
        image = popup.grab().toImage()
        for x, y in ((12, 50), (popup.width() - 7, 80)):
            pixel = image.pixelColor(x, y)
            assert pixel.alpha() == 255
            assert pixel.name() == opaque_surface.name()

        action = next(item for item in popup.actions() if isinstance(item, QWidgetAction))
        container = action.defaultWidget()
        container_image = container.grab().toImage()
        for x, y in ((2, 2), (container.width() - 8, 55)):
            pixel = container_image.pixelColor(x, y)
            assert pixel.alpha() == 255
            assert pixel.name() == opaque_surface.name()

        changes: list[set[str]] = []

        def record_changes(cols: set[str]) -> None:
            changes.append(cols)

        popup.cols_changed.connect(record_changes)
        popup._select_none()  # pyright: ignore[reportPrivateUsage]
        assert not popup.active_cols
        assert changes == [set()]
        assert popup.isVisible()
    finally:
        popup.close()
        popup.deleteLater()
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
        app.processEvents()


def test_search_column_popup_keeps_fusion_surface() -> None:
    app = _qapp()
    ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
    popup = ColumnFilterPopup()
    try:
        popup.popup(QPoint(50, 50))
        QTest.qWait(40)
        expected = popup.palette().window().color()
        pixel = popup.grab().toImage().pixelColor(12, 50)
        assert pixel.name() == expected.name()
        assert pixel.alpha() == 255
    finally:
        popup.close()
        popup.deleteLater()
        app.processEvents()
