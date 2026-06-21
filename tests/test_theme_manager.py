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


def test_set_color_scheme_ignores_old_qt_style_hints_without_setter(monkeypatch):
    color_scheme = object()
    monkeypatch.setattr(
        ThemeManager,
        '_qt_color_scheme',
        staticmethod(lambda color_scheme_name: color_scheme),
    )

    class OldStyleHints:
        pass

    class App:
        def styleHints(self):
            return OldStyleHints()

    ThemeManager._set_color_scheme(App(), 'Dark')


def test_set_color_scheme_uses_new_qt_style_hints_when_available(monkeypatch):
    color_scheme = object()
    monkeypatch.setattr(
        ThemeManager,
        '_qt_color_scheme',
        staticmethod(lambda color_scheme_name: color_scheme),
    )

    class StyleHints:
        color_scheme = None

        def setColorScheme(self, color_scheme):
            self.color_scheme = color_scheme

    class App:
        def __init__(self):
            self.style_hints = StyleHints()

        def styleHints(self):
            return self.style_hints

    app = App()

    ThemeManager._set_color_scheme(app, 'Light')

    assert app.style_hints.color_scheme is color_scheme


def test_set_color_scheme_ignores_qt_without_color_scheme_enum(monkeypatch):
    monkeypatch.setattr(
        ThemeManager,
        '_qt_color_scheme',
        staticmethod(lambda color_scheme_name: None),
    )

    class App:
        def styleHints(self):
            raise AssertionError('style hints should not be queried')

    ThemeManager._set_color_scheme(App(), 'Dark')
