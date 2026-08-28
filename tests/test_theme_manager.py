import sys
import types

from PyQt6.QtGui import QColor, QPalette

from fleasion.gui.theme import ThemeManager


class _FakeStyle:
    def __init__(self, name='mock-style', palette=None):
        self._name = name
        self._palette = palette or QPalette()

    def objectName(self):
        return self._name

    def standardPalette(self):
        return self._palette


class _FakeApp:
    def __init__(self, style=None, palette=None):
        self._style = style or _FakeStyle()
        self._palette = palette or QPalette()
        self.style_name = None
        self.applied_palette = None

    def style(self):
        return self._style

    def setStyle(self, style_name):
        self.style_name = style_name
        self._style = _FakeStyle(style_name, self._style.standardPalette())

    def setPalette(self, palette):
        self.applied_palette = palette
        self._palette = palette

    def palette(self):
        return self._palette



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
            QPalette.ColorRole.PlaceholderText: '#80f0f0f0',
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
            QPalette.ColorRole.PlaceholderText: '#80000000',
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


def assert_palette_colors(palette, expected):
    for role, color_name in expected.items():
        assert palette.color(QPalette.ColorGroup.Active, role) == QColor(color_name)


def assert_disabled_palette_colors(palette, expected):
    for role, color_name in expected.items():
        assert palette.color(QPalette.ColorGroup.Disabled, role) == QColor(color_name)


def test_windows_system_theme_routes_dark_to_fleasion_dark_palette(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(ThemeManager, '_current_theme', 'System')
    monkeypatch.setattr(ThemeManager, '_effective_theme', 'System')
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda _app, _name: None),
    )
    monkeypatch.setattr(
        ThemeManager,
        '_windows_system_theme',
        staticmethod(lambda _app: 'Dark'),
    )
    app = _FakeApp()

    ThemeManager._apply_system_theme(app)

    assert ThemeManager._current_theme == 'System'
    assert ThemeManager._effective_theme == 'Dark'
    assert app.style_name == 'Fusion'
    assert app.applied_palette.color(QPalette.ColorRole.Window) == QColor('#323232')
    assert app.applied_palette.color(QPalette.ColorRole.Base) == QColor('#242424')
    colors = ThemeManager.panel_colors(app.applied_palette)
    assert colors.section_background == QColor('#272727')
    assert colors.container_background_css == 'background-color: rgb(64, 64, 64);'


def test_windows_system_theme_routes_light_to_fleasion_light_palette(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(ThemeManager, '_current_theme', 'System')
    monkeypatch.setattr(ThemeManager, '_effective_theme', 'System')
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda _app, _name: None),
    )
    monkeypatch.setattr(
        ThemeManager,
        '_windows_system_theme',
        staticmethod(lambda _app: 'Light'),
    )
    app = _FakeApp()

    ThemeManager._apply_system_theme(app)

    assert ThemeManager._current_theme == 'System'
    assert ThemeManager._effective_theme == 'Light'
    assert app.applied_palette.color(QPalette.ColorRole.Window) == QColor('#efefef')
    assert app.applied_palette.color(QPalette.ColorRole.Base) == QColor('#ffffff')
    colors = ThemeManager.panel_colors(app.applied_palette)
    assert colors.section_background == QColor('#f0f0f0')


def test_windows_system_theme_reads_apps_use_light_theme_registry(monkeypatch):
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    values = {'AppsUseLightTheme': 0}
    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_QUERY_VALUE=1,
        OpenKey=lambda *_args, **_kwargs: _Key(),
        QueryValueEx=lambda _key, name: (values[name], 4),
    )
    monkeypatch.setitem(sys.modules, 'winreg', fake_winreg)

    assert ThemeManager._windows_system_theme(_FakeApp()) == 'Dark'
    values['AppsUseLightTheme'] = 1
    assert ThemeManager._windows_system_theme(_FakeApp()) == 'Light'


def test_macos_system_theme_keeps_qt_system_palette(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(ThemeManager, '_current_theme', 'System')
    monkeypatch.setattr(ThemeManager, '_effective_theme', 'Dark')
    color_scheme_names = []
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda _app, name: color_scheme_names.append(name)),
    )
    system_palette = QPalette()
    system_palette.setColor(QPalette.ColorRole.Window, QColor('#123456'))
    app = _FakeApp(style=_FakeStyle('macos', system_palette))

    ThemeManager._apply_system_theme(app)

    assert color_scheme_names == ['Unknown']
    assert ThemeManager._effective_theme == 'System'
    assert app.style_name == 'Fusion'
    assert app.applied_palette.color(QPalette.ColorRole.Window) == QColor('#123456')


def test_linux_system_theme_still_restores_global_qt_style(monkeypatch):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(ThemeManager, '_system_style_name', 'breeze')
    monkeypatch.setattr(ThemeManager, '_effective_theme', 'Dark')
    monkeypatch.setattr(
        ThemeManager,
        '_set_color_scheme',
        staticmethod(lambda _app, _name: None),
    )
    app = _FakeApp(style=_FakeStyle('Fusion'))

    ThemeManager._apply_system_theme(app)

    assert ThemeManager._effective_theme == 'System'
    assert app.style_name == 'breeze'
    assert isinstance(app.applied_palette, QPalette)
