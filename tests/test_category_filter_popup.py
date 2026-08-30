import os
import subprocess
import sys

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from typing import cast

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtWidgets import QApplication

from fleasion.cache.asset_type_filter import CategoryFilterPopup


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
