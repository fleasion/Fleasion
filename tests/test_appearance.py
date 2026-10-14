"""Independent appearance preferences, migration, and palette integration."""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QMenu, QPushButton

from fleasion.appearance import ACCENTS, COLOR_MODES, STYLES
from fleasion.appearance.colors import contrast
from fleasion.app.tray import SystemTray
from fleasion.config import manager
from fleasion.gui import glass
from fleasion.gui.glass import style as glass_style
from fleasion.gui.glass.state import state
from fleasion.gui.settings_tab import SettingsTab
from fleasion.gui.theme import ThemeManager
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.gui.appearance_controls import choice_label
from fleasion.localization import tr

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fleasion.appearance import AppearanceStyle, ColorMode


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication([])
    ThemeManager.apply_theme('Light')
    original = QPalette(application.palette())
    monkeypatch.setattr(glass_style, 'ICON_DIR', tmp_path / 'icons')
    yield application
    ThemeManager.apply_theme('Light')
    application.setPalette(original)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


@pytest.mark.parametrize('legacy', ['Glass', 'Dark', 'Light', 'System'])
def test_migrate_combined_theme_preserves_existing_choices(
    config: manager.ConfigManager, legacy: str
) -> None:
    loaded = config._settings_from_loaded(  # pyright: ignore[reportPrivateUsage]
        {
            'theme': legacy,
            'glass_accent': 'Violet',
            'glass_opacity': 0.72,
            'glass_backdrop': 'wallpaper',
        }
    )
    config.settings = loaded
    assert config.appearance_style == ('Glass' if legacy == 'Glass' else 'Fusion')
    assert config.color_mode == ('Dark' if legacy == 'Glass' else legacy)
    assert config.accent_color == 'Violet'
    assert config.glass_opacity == 0.72
    assert config.glass_backdrop == 'wallpaper'
    assert 'theme' not in loaded
    assert 'glass_accent' not in loaded


def test_new_preferences_win_over_legacy_values(config: manager.ConfigManager) -> None:
    config.settings = config._settings_from_loaded(  # pyright: ignore[reportPrivateUsage]
        {
            'theme': 'Glass',
            'glass_accent': 'Amber',
            'appearance_style': 'Fusion',
            'color_mode': 'Light',
            'accent_color': 'Teal',
        }
    )
    assert (config.appearance_style, config.color_mode, config.accent_color) == (
        'Fusion',
        'Light',
        'Teal',
    )


def test_defaults_validation_and_persistence(config: manager.ConfigManager) -> None:
    assert (config.appearance_style, config.color_mode, config.accent_color) == (
        'Fusion',
        'System',
        'Ice',
    )
    config.appearance_style = 'Glass'
    config.color_mode = 'Light'
    config.accent_color = 'Coral'
    config = manager.ConfigManager()
    assert (config.appearance_style, config.color_mode, config.accent_color) == (
        'Glass',
        'Light',
        'Coral',
    )
    config.settings.update(appearance_style=['bad'], color_mode=42, accent_color={'bad': True})
    assert (config.appearance_style, config.color_mode, config.accent_color) == (
        'Fusion',
        'System',
        'Ice',
    )


@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('mode', COLOR_MODES)
@pytest.mark.parametrize('accent', ACCENTS)
def test_all_combinations_have_readable_palettes(
    app: QApplication, style: AppearanceStyle, mode: ColorMode, accent: str
) -> None:
    ThemeManager.apply_appearance(style, mode, accent)
    palette = app.palette()
    assert glass.theme.active == (style == 'Glass')
    assert contrast(palette.text().color(), palette.base().color()) >= 4.5
    assert contrast(palette.highlightedText().color(), palette.highlight().color()) >= 4.5
    assert contrast(palette.link().color(), palette.base().color()) >= 4.5
    if mode != 'System':
        assert (palette.window().color().lightness() < 128) == (mode == 'Dark')


def test_switching_style_preserves_mode_accent_and_glass_settings(
    app: QApplication, config: manager.ConfigManager
) -> None:
    config.color_mode = 'Light'
    config.accent_color = 'Amber'
    config.glass_opacity = 0.68
    config.glass_backdrop = 'wallpaper'
    for style in ('Glass', 'Fusion', 'Glass'):
        ThemeManager.set_preference(config, 'style', style)
        assert config.color_mode == 'Light'
        assert config.accent_color == 'Amber'
        assert config.glass_opacity == 0.68
        assert config.glass_backdrop == 'wallpaper'
        assert state.settings.accent == 'Amber'
        assert app.palette().window().color().lightness() > 128


def test_linux_system_keeps_native_style_and_glass_uses_native_colors(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fleasion.gui.theme as theme_module

    monkeypatch.setattr(theme_module.sys, 'platform', 'linux')
    native = QPalette()
    native.setColor(QPalette.ColorRole.Window, QColor('#d9e8d6'))
    native.setColor(QPalette.ColorRole.WindowText, QColor('#192d1b'))
    native.setColor(QPalette.ColorRole.Base, QColor('#f4fff0'))
    native.setColor(QPalette.ColorRole.Text, QColor('#0f2414'))
    monkeypatch.setattr(ThemeManager, '_system_palette', native)
    monkeypatch.setattr(ThemeManager, '_system_style_name', 'Windows')
    ThemeManager.apply_appearance('Fusion', 'System', 'Rose')
    assert app.style().objectName() == 'windows'
    assert app.palette().window().color().rgb() == native.window().color().rgb()
    ThemeManager.apply_appearance('Glass', 'System', 'Rose')
    assert glass.theme.active
    assert app.palette().window().color().rgb() == native.window().color().rgb()
    assert app.palette().text().color() == native.text().color()
    assert glass_style.glass_colors().well == native.base().color()
    ThemeManager.apply_appearance('Fusion', 'System', 'Rose')
    assert app.style().objectName() == 'windows'
    assert app.palette().window().color() == native.window().color()


@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('light', [False, True])
def test_windows_system_resolves_os_preference_without_changing_style(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, style: AppearanceStyle, light: bool
) -> None:
    import fleasion.gui.theme as theme_module

    monkeypatch.setattr(theme_module.sys, 'platform', 'win32')
    monkeypatch.setattr(theme_module, '_windows_apps_use_light_theme', lambda: light)
    ThemeManager.apply_appearance(style, 'System', 'Mint')
    assert glass.theme.active == (style == 'Glass')
    assert (app.palette().window().color().lightness() > 128) == light


def test_external_native_palette_update_refreshes_system_glass(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fleasion.gui.theme as theme_module

    monkeypatch.setattr(theme_module.sys, 'platform', 'linux')
    ThemeManager.apply_appearance('Glass', 'System', 'Violet')
    native = QPalette(app.palette())
    native.setColor(QPalette.ColorRole.Window, QColor('#253029'))
    native.setColor(QPalette.ColorRole.WindowText, QColor('#ecfff0'))
    native.setColor(QPalette.ColorRole.Base, QColor('#101c14'))
    app.setPalette(native)
    app.processEvents()
    assert app.palette().window().color().rgb() == QColor('#253029').rgb()
    assert glass_style.glass_colors().surface == QColor('#253029')
    assert state.settings.accent == 'Violet'
    assert glass.theme.active


def test_swatches_apply_live_and_settings_refresh_without_writes(
    app: QApplication, config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    ThemeManager.apply_from_config(config)
    tab = SettingsTab(config)
    try:
        button = tab.findChild(QPushButton, 'AccentTeal')
        assert button is not None
        button.click()
        assert config.accent_color == 'Teal'
        assert tab._accent_selector.accent() == 'Teal'  # pyright: ignore[reportPrivateUsage]
        ThemeManager.set_preference(config, 'style', 'Glass')
        ThemeManager.set_preference(config, 'color_mode', 'Light')
        assert tab._style_buttons['Glass'].isChecked()  # pyright: ignore[reportPrivateUsage]
        assert tab._color_buttons['Light'].isChecked()  # pyright: ignore[reportPrivateUsage]
        assert not tab._backdrop_combo.isHidden()  # pyright: ignore[reportPrivateUsage]
        saved = json.loads((manager.CONFIG_FILE).read_text())
        writes: list[bool] = []
        monkeypatch.setattr(config, '_save_settings', lambda: writes.append(True))
        tab.refresh_from_config()
        assert writes == []
        assert json.loads(manager.CONFIG_FILE.read_text()) == saved
    finally:
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_tray_actions_and_settings_stay_in_sync(
    app: QApplication, config: manager.ConfigManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    tray = SystemTray.__new__(SystemTray)
    tray.config_manager = config
    tray.menu = QMenu()
    monkeypatch.setattr(tray, '_is_cache_scraper_enabled', lambda: False)
    tray._create_settings_menu()  # pyright: ignore[reportPrivateUsage]
    events = ThemeManager.events()
    events.changed.connect(tray._sync_appearance_actions)  # pyright: ignore[reportPrivateUsage]
    tab = SettingsTab(config)
    try:
        tray.style_actions['Glass'].trigger()
        tray.color_mode_actions['Light'].trigger()
        tray.accent_actions['Coral'].trigger()
        assert (config.appearance_style, config.color_mode, config.accent_color) == (
            'Glass',
            'Light',
            'Coral',
        )
        assert tab._style_buttons['Glass'].isChecked()  # pyright: ignore[reportPrivateUsage]
        assert tab._color_buttons['Light'].isChecked()  # pyright: ignore[reportPrivateUsage]
        assert tab._accent_selector.accent() == 'Coral'  # pyright: ignore[reportPrivateUsage]
        ThemeManager.set_preference(config, 'style', 'Fusion')
        ThemeManager.set_preference(config, 'accent', 'Teal')
        assert tray.style_actions['Fusion'].isChecked()
        assert not tray.style_actions['Glass'].isChecked()
        assert tray.accent_actions['Teal'].isChecked()
        assert tray.color_mode_actions['Light'].isChecked()
    finally:
        events.changed.disconnect(tray._sync_appearance_actions)  # pyright: ignore[reportPrivateUsage]
        tab.deleteLater()
        tray.menu.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_command_palette_changes_each_preference_independently(
    app: QApplication, config: manager.ConfigManager
) -> None:
    window = ReplacerConfigWindow(config, system_tray=None)
    try:
        commands = window._palette_commands()  # pyright: ignore[reportPrivateUsage]
        for kind, title in (
            (tr('settings.appearance.style'), choice_label('Glass')),
            (tr('settings.appearance.color_mode'), choice_label('Light')),
            (tr('settings.appearance.accent'), 'Rose'),
        ):
            next(
                command for command in commands if command.kind == kind and command.title == title
            ).run()
        assert (config.appearance_style, config.color_mode, config.accent_color) == (
            'Glass',
            'Light',
            'Rose',
        )
        assert glass.theme.active
        assert app.palette().window().color().lightness() > 128
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_backdrop_updates_opacity_label_from_settings_and_external_commands(
    app: QApplication, config: manager.ConfigManager
) -> None:
    ThemeManager.apply_from_config(config)
    tab = SettingsTab(config)
    try:
        assert tab._opacity_label.text() == tr('settings.appearance.opacity')  # pyright: ignore[reportPrivateUsage]
        glass.theme.set_backdrop('wallpaper')
        assert tab._opacity_label.text() == tr('settings.appearance.tint_strength')  # pyright: ignore[reportPrivateUsage]
        glass.theme.set_backdrop('desktop')
        assert tab._opacity_label.text() == tr('settings.appearance.opacity')  # pyright: ignore[reportPrivateUsage]
        assert tab._opacity_slider.toolTip() == tr('settings.appearance.window_opacity_tooltip')  # pyright: ignore[reportPrivateUsage]
        tab._backdrop_combo.setCurrentIndex(tab._backdrop_combo.findData('wallpaper'))  # pyright: ignore[reportPrivateUsage]
        assert config.glass_backdrop == 'wallpaper'
        assert tab._opacity_label.text() == tr('settings.appearance.tint_strength')  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
