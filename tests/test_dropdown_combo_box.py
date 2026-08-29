import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QPoint
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleFactory, QStyleOptionComboBox

from fleasion.gui.modifications_tab import DropdownComboBox
from fleasion.gui.theme import ThemeManager


_APP = None


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _render_combo(palette, *, enabled):
    app = _qapp()
    combo = DropdownComboBox()
    combo.setStyle(QStyleFactory.create('Fusion'))
    combo.setPalette(palette)
    combo.addItem('Auto')
    combo.resize(180, 28)
    combo.setEnabled(enabled)
    combo.show()
    app.processEvents()

    image = QImage(combo.size(), QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    combo.render(painter, QPoint())
    painter.end()

    option = QStyleOptionComboBox()
    combo.initStyleOption(option)
    arrow_rect = combo.style().subControlRect(
        QStyle.ComplexControl.CC_ComboBox,
        option,
        QStyle.SubControl.SC_ComboBoxArrow,
        combo,
    )
    return combo, image, arrow_rect


def test_dropdown_arrow_background_matches_combo_surface():
    for palette in (ThemeManager._light_palette(), ThemeManager._dark_palette()):
        for enabled in (True, False):
            combo, image, arrow_rect = _render_combo(palette, enabled=enabled)
            body_x = arrow_rect.left() - 20
            arrow_background_x = arrow_rect.left() + 2

            for y in (0, 1, 2, 3, 5, 8, 19, 23, 25, 26, 27):
                assert image.pixelColor(arrow_background_x, y) == image.pixelColor(body_x, y)

            combo.close()
            combo.deleteLater()
            _qapp().processEvents()
