import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication

from Fleasion.cache.cache_viewer import CategoryFilterPopup


def _qapp():
    return QApplication.instance() or QApplication([])


def test_category_filter_popup_constrains_height_and_scrolls():
    app = _qapp()
    popup = CategoryFilterPopup(active_filters={24, 41})
    natural_height = popup._natural_content_size.height()

    popup.constrain_to_available_geometry(QRect(0, 0, 900, 360), anchor_y=30)

    assert natural_height > popup.scroll_area.height()
    assert popup.scroll_area.height() <= 360
    assert popup.scroll_area.verticalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAsNeeded
    assert popup.checkboxes[24].isChecked()
    assert popup.checkboxes[41].isChecked()
    assert app is not None
