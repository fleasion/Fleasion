import os
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from typing import cast

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from fleasion.cache.asset_type_filter import CategoryFilterPopup
from fleasion.gui.theme import ThemeManager


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast('QApplication', app) if app is not None else QApplication([])


def _natural_content_size(popup: CategoryFilterPopup) -> QSize:
    return cast('QSize', getattr(popup, '_natural_content_size'))


def test_asset_type_filter_import_does_not_load_opengl_viewers() -> None:
    script = """
import sys
import fleasion.cache.asset_type_filter

loaded = set(sys.modules)
for module_name in ('fleasion.cache.cache_viewer', 'fleasion.cache.obj_viewer'):
    assert module_name not in loaded, module_name
assert not any(name == 'OpenGL' or name.startswith('OpenGL.') for name in loaded)
"""

    subprocess.run([sys.executable, '-c', script], check=True)


def test_category_filter_popup_recalculates_outer_and_inner_geometry_on_reopen() -> None:
    app = _qapp()
    popup = CategoryFilterPopup(active_filters={24, 41})
    available = QRect(0, 0, 900, 650)
    try:
        for anchor_y in (620, 20, 590, 45, 620, 20):
            popup.constrain_to_available_geometry(available, anchor_y)
            hint = popup.sizeHint()
            assert popup.size() == hint
            assert hint.width() <= available.width()
            assert hint.height() <= available.height()
            assert popup.checkboxes[24].isChecked()
            assert popup.checkboxes[41].isChecked()

        small = QRect(0, 0, 440, 330)
        popup.constrain_to_available_geometry(small, 315)
        assert popup.size() == popup.sizeHint()
        assert popup.width() <= small.width()
        assert popup.height() <= small.height()
        assert popup.scroll_area.horizontalScrollBarPolicy() == (
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
    finally:
        popup.close()
        popup.deleteLater()
        app.processEvents()


def test_category_filter_popup_paints_background_in_glass() -> None:
    app = _qapp()
    ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
    popup = CategoryFilterPopup()
    try:
        popup.popup(QPoint(50, 50))
        QTest.qWait(60)
        assert popup.isVisible()
        image = popup.grab().toImage()
        # The gutter between the menu edge and its category cards must paint
        assert image.pixelColor(12, 50).alpha() == 255
        expected = popup.palette().toolTipBase().color().name()
        assert image.pixelColor(12, 50).name() == expected
    finally:
        popup.close()
        popup.deleteLater()
        ThemeManager.apply_appearance('Fusion', 'Dark', 'Ice')
        app.processEvents()


def test_category_filter_popup_constrains_height_and_scrolls() -> None:
    app = _qapp()
    popup = CategoryFilterPopup(active_filters={24, 41})
    natural_height = _natural_content_size(popup).height()

    popup.constrain_to_available_geometry(QRect(0, 0, 900, 360), anchor_y=30)

    assert natural_height > popup.scroll_area.height()
    assert popup.scroll_area.height() <= 360
    assert popup.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert popup.checkboxes[24].isChecked()
    assert popup.checkboxes[41].isChecked()
    assert app is not None
