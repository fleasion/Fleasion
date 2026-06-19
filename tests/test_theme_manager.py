from PyQt6.QtGui import QColor, QPalette

from Fleasion.gui.theme import ThemeManager


def test_panel_colors_keep_forced_dark_palette_independent_colors(monkeypatch):
    monkeypatch.setattr(ThemeManager, '_current_theme', 'Dark')

    colors = ThemeManager.panel_colors(QPalette())

    assert colors.section_background == QColor('#272727')
    assert colors.section_border == QColor('#3a3a3a')
    assert colors.container_background_css == 'background-color: rgb(64, 64, 64);'


def test_panel_colors_use_qpalette_for_system_theme(monkeypatch):
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#123456'))
    palette.setColor(QPalette.ColorRole.Mid, QColor('#abcdef'))
    monkeypatch.setattr(ThemeManager, '_current_theme', 'System')

    colors = ThemeManager.panel_colors(palette)

    assert colors.section_background == QColor('#123456')
    assert colors.section_border == QColor('#abcdef')
    assert colors.container_background_css == 'background-color: palette(alternate-base);'
