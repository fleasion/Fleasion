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


def test_apply_forced_dark_sets_explicit_dark_palette(monkeypatch):
    color_scheme_names = []
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda app, color_scheme_name: color_scheme_names.append(color_scheme_name)),
    )

    class App:
        style_name = None
        palette = None

        def setStyle(self, style_name):
            self.style_name = style_name

        def setPalette(self, palette):
            self.palette = palette

    app = App()

    ThemeManager._apply_forced_theme(app, 'Dark')

    assert app.style_name == 'Fusion'
    assert color_scheme_names == ['Dark']
    assert_palette_colors(
        app.palette,
        {
            QPalette.ColorRole.Window: '#323232',
            QPalette.ColorRole.WindowText: '#f0f0f0',
            QPalette.ColorRole.Base: '#242424',
            QPalette.ColorRole.AlternateBase: '#2b2b2b',
            QPalette.ColorRole.ToolTipBase: '#ffffdc',
            QPalette.ColorRole.ToolTipText: '#000000',
            QPalette.ColorRole.Text: '#f0f0f0',
            QPalette.ColorRole.Button: '#323232',
            QPalette.ColorRole.ButtonText: '#f0f0f0',
            QPalette.ColorRole.BrightText: '#4b4b4b',
            QPalette.ColorRole.Link: '#308cc6',
            QPalette.ColorRole.Highlight: '#308cc6',
            QPalette.ColorRole.HighlightedText: '#f0f0f0',
            QPalette.ColorRole.PlaceholderText: '#f0f0f0',
            QPalette.ColorRole.Light: '#4b4b4b',
            QPalette.ColorRole.Midlight: '#2a2a2a',
            QPalette.ColorRole.Dark: '#212121',
            QPalette.ColorRole.Mid: '#262626',
            QPalette.ColorRole.Shadow: '#191919',
        },
    )
    assert_disabled_palette_colors(
        app.palette,
        {
            QPalette.ColorRole.Text: '#828282',
            QPalette.ColorRole.ButtonText: '#828282',
            QPalette.ColorRole.WindowText: '#828282',
            QPalette.ColorRole.HighlightedText: '#f0f0f0',
        },
    )


def test_apply_forced_light_sets_explicit_light_palette(monkeypatch):
    color_scheme_names = []
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda app, color_scheme_name: color_scheme_names.append(color_scheme_name)),
    )

    class App:
        style_name = None
        palette = None

        def setStyle(self, style_name):
            self.style_name = style_name

        def setPalette(self, palette):
            self.palette = palette

    app = App()

    ThemeManager._apply_forced_theme(app, 'Light')

    assert app.style_name == 'Fusion'
    assert color_scheme_names == ['Light']
    assert_palette_colors(
        app.palette,
        {
            QPalette.ColorRole.Window: '#efefef',
            QPalette.ColorRole.WindowText: '#000000',
            QPalette.ColorRole.Base: '#ffffff',
            QPalette.ColorRole.AlternateBase: '#f7f7f7',
            QPalette.ColorRole.ToolTipBase: '#ffffdc',
            QPalette.ColorRole.ToolTipText: '#000000',
            QPalette.ColorRole.Text: '#000000',
            QPalette.ColorRole.Button: '#efefef',
            QPalette.ColorRole.ButtonText: '#000000',
            QPalette.ColorRole.BrightText: '#ffffff',
            QPalette.ColorRole.Link: '#0000ff',
            QPalette.ColorRole.Highlight: '#308cc6',
            QPalette.ColorRole.HighlightedText: '#ffffff',
            QPalette.ColorRole.PlaceholderText: '#000000',
            QPalette.ColorRole.Light: '#ffffff',
            QPalette.ColorRole.Midlight: '#cacaca',
            QPalette.ColorRole.Dark: '#9f9f9f',
            QPalette.ColorRole.Mid: '#b8b8b8',
            QPalette.ColorRole.Shadow: '#767676',
        },
    )
    assert_disabled_palette_colors(
        app.palette,
        {
            QPalette.ColorRole.Text: '#bebebe',
            QPalette.ColorRole.ButtonText: '#bebebe',
            QPalette.ColorRole.WindowText: '#bebebe',
            QPalette.ColorRole.HighlightedText: '#ffffff',
        },
    )


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


def assert_palette_colors(palette, expected):
    for role, color_name in expected.items():
        assert palette.color(QPalette.ColorGroup.Active, role) == QColor(color_name)


def assert_disabled_palette_colors(palette, expected):
    for role, color_name in expected.items():
        assert palette.color(QPalette.ColorGroup.Disabled, role) == QColor(color_name)
