from __future__ import annotations

import os
import gc
from pathlib import Path
from threading import get_ident
from concurrent.futures import Future
from threading import Event
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage, QPalette
from PySide6.QtWidgets import QApplication, QDialog, QTreeWidget, QVBoxLayout, QWidget
from shiboken6 import isValid

from fleasion.config import manager
from fleasion.gui import glass
from fleasion.gui.command_palette import CommandPalette
from fleasion.gui.conflicts import Claim, Conflict, ConflictBadge, ConflictsDialog, find_conflicts
from fleasion.gui.glass import chrome
from fleasion.gui.glass import surface
from fleasion.gui.glass import wallpaper
from fleasion.gui.settings_tab import SettingsTab
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fleasion.utils.json_types import JsonObject


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[QApplication]:
    instance = QApplication.instance()
    application = instance if isinstance(instance, QApplication) else QApplication([])
    previous_theme = str(getattr(ThemeManager, '_current_theme'))
    for name in ('_current_theme', '_effective_theme', '_system_style_name'):
        monkeypatch.setattr(ThemeManager, name, getattr(ThemeManager, name))
    ThemeManager.apply_theme('Light')
    yield application
    ThemeManager.apply_theme(previous_theme)
    application.processEvents()


def _enable(config: manager.ConfigManager, name: str, rules: list[JsonObject]) -> None:
    assert config.create_config(name)
    config.set_replacement_rules(name, list(rules))
    config.set_config_enabled(name, True)


def test_conflict_retains_higher_priority_rule(config: manager.ConfigManager) -> None:
    _enable(
        config,
        'a',
        [
            {'mode': 'local', 'replace_ids': [1001], 'local_path': 'a.obj', 'name': 'local'},
            {'mode': 'id', 'replace_ids': [1001], 'with_id': 1002},
        ],
    )
    _enable(
        config,
        'b',
        [
            {'mode': 'cdn', 'replace_ids': [1001], 'cdn_url': 'https://example.invalid/b'},
        ],
    )
    assert 1001 in config.get_all_replacements()[3]
    (conflict,) = find_conflicts(config)
    assert conflict.winner.config == 'a'
    assert conflict.winner.rule == 'local'
    assert conflict.winner.mode == 'local'


@pytest.mark.parametrize('first,second', [('01001', 1001), ('Mesh', 'mesh')])
def test_asset_aliases_are_normalized(
    config: manager.ConfigManager,
    first: str,
    second: str | int,
) -> None:
    _enable(config, 'a', [{'replace_ids': [first], 'with_id': 2001}])
    _enable(config, 'b', [{'replace_ids': [second], 'with_id': 2002}])
    assert len(config.get_all_replacements()[0]) == 1
    (conflict,) = find_conflicts(config)
    assert conflict.winner.config == 'b'


def test_invalid_id_list_does_not_crash_badge(
    app: QApplication,
    config: manager.ConfigManager,
) -> None:
    _enable(config, 'a', [{'replace_ids': 1001, 'with_id': 2001}])
    assert config.get_all_replacements() == ({}, set(), {}, {})
    badge = ConflictBadge(config)
    assert badge.conflicts == []
    badge.deleteLater()
    app.processEvents()


@pytest.mark.parametrize(
    'rule',
    [
        {'mode': 'unknown', 'replace_ids': [1001], 'with_id': 2002},
        {'replace_ids': [1001], 'with_id': 1},
        {'replace_ids': [1001], 'with_id': 'invalid'},
    ],
)
def test_ignored_rules_do_not_create_conflicts(
    config: manager.ConfigManager,
    rule: JsonObject,
) -> None:
    _enable(config, 'a', [rule])
    _enable(config, 'b', [{'replace_ids': [1001], 'with_id': 2001}])
    assert find_conflicts(config) == []


@pytest.mark.parametrize('existing', [False, True])
def test_dialog_surface_survives_theme_round_trip(app: QApplication, existing: bool) -> None:
    dialog = QDialog() if existing else None
    if dialog is not None:
        dialog.resize(100, 100)
        dialog.show()
        app.processEvents()
    ThemeManager.apply_theme('Glass')
    if dialog is None:
        dialog = QDialog()
        dialog.resize(100, 100)
        dialog.show()
    try:
        app.processEvents()
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert dialog.grab().toImage().pixelColor(50, 50).alpha() > 0
        # Reapplying an accent must preserve the original attributes
        glass.theme.enable(app)
        ThemeManager.apply_theme('Light')
        app.processEvents()
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        assert dialog.grab().toImage().pixelColor(50, 50).alpha() == 255
    finally:
        dialog.close()
        dialog.deleteLater()


def test_preexisting_translucent_surface_is_preserved(app: QApplication) -> None:
    dialog = QDialog()
    dialog.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    dialog.show()
    app.processEvents()
    try:
        ThemeManager.apply_theme('Glass')
        ThemeManager.apply_theme('Light')
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_wallpaper_discovery_runs_outside_paint_and_gui_thread(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image = tmp_path / 'source.png'
    Image.new('RGB', (10, 10)).save(image)
    calls: list[int] = []

    def discover() -> Path:
        calls.append(get_ident())
        return image

    monkeypatch.setattr(wallpaper, 'wallpaper_path', discover)
    cache = wallpaper.WallpaperCache()
    try:
        cache.image(100, 100)
        assert calls == []
        cache.refresh_source()
        pending = cache._pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        assert pending.result(timeout=2).path == image
        assert cache.refresh_source()
        assert len(calls) == 1
        assert calls[0] != get_ident()
        assert not _wait_image(cache, 100, 100).isNull()
        cache.image(100, 100)
        assert len(calls) == 1
    finally:
        cache.close()


def _wait_image(cache: wallpaper.WallpaperCache, width: int, height: int) -> QImage:
    cache.image(width, height)
    entry = cache._entries[(width, height)]  # pyright: ignore[reportPrivateUsage]
    if isinstance(entry, Future):
        entry.result(timeout=5)
    return cache.image(width, height)


def test_wallpaper_keeps_each_monitor_size_and_renders_in_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / 'source.png'
    Image.new('RGB', (100, 100), '#4080c0').save(path)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(path)  # pyright: ignore[reportPrivateUsage]
    render = wallpaper.render_wallpaper
    calls: list[tuple[int, int, int]] = []

    def recorded(source: Path, width: int, height: int) -> QImage:
        calls.append((width, height, get_ident()))
        return render(source, width, height)

    monkeypatch.setattr(wallpaper, 'render_wallpaper', recorded)
    try:
        first = _wait_image(cache, 1920, 1080)
        second = _wait_image(cache, 3840, 2160)
        for _ in range(10):
            assert cache.image(1920, 1080).cacheKey() == first.cacheKey()
            assert cache.image(3840, 2160).cacheKey() == second.cacheKey()
        assert [(width, height) for width, height, _ in calls] == [(1920, 1080), (3840, 2160)]
        assert all(thread != get_ident() for _, _, thread in calls)
        assert first.size().width() == 1920
        assert second.size().width() == 3840
        assert first.pixelColor(0, 0) == second.pixelColor(0, 0)
    finally:
        cache.close()


def test_wallpaper_does_not_wait_for_pending_render(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(tmp_path / 'source.png')  # pyright: ignore[reportPrivateUsage]
    started, release = Event(), Event()
    calls: list[int] = []

    def slow_render(_source: Path, width: int, height: int) -> QImage:
        calls.append(get_ident())
        started.set()
        assert release.wait(timeout=5)
        return QImage(width, height, QImage.Format.Format_RGB888)

    monkeypatch.setattr(wallpaper, 'render_wallpaper', slow_render)
    try:
        assert cache.image(100, 100).isNull()
        assert started.wait(timeout=2)
        for _ in range(10):
            assert cache.image(100, 100).isNull()
        assert len(calls) == 1
        assert calls[0] != get_ident()
        release.set()
        assert not _wait_image(cache, 100, 100).isNull()
    finally:
        release.set()
        cache.close()


def test_wallpaper_invalidates_sizes_when_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / 'first.png', tmp_path / 'second.png'
    Image.new('RGB', (10, 10), '#ff0000').save(first)
    Image.new('RGB', (10, 10), '#0000ff').save(second)
    monkeypatch.setattr(wallpaper, 'wallpaper_path', lambda: second)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(first)  # pyright: ignore[reportPrivateUsage]
    try:
        red = _wait_image(cache, 100, 100)
        cache.refresh_source()
        pending = cache._pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        assert pending.result(timeout=2).path == second
        assert cache.refresh_source()
        blue = _wait_image(cache, 100, 100)
        assert red.pixelColor(0, 0).red() == 255
        assert blue.pixelColor(0, 0).blue() == 255
    finally:
        cache.close()


@pytest.mark.parametrize('platform', ['win32', 'darwin'])
@pytest.mark.parametrize('frameless', [False, True])
def test_secondary_window_uses_opaque_fallback_when_frame_requires_it(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    frameless: bool,
) -> None:
    # Exercise the platform policy without changing the actual Qt backend
    monkeypatch.setattr(surface, 'sys', SimpleNamespace(platform=platform))
    ThemeManager.apply_theme('Glass')
    dialog = QDialog()
    dialog.setWindowFlag(Qt.WindowType.FramelessWindowHint, on=frameless)
    dialog.resize(100, 100)
    dialog.show()
    app.processEvents()
    try:
        assert dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) == frameless
        alpha = dialog.grab().toImage().pixelColor(50, 50).alpha()
        assert alpha == 255 if not frameless else alpha > 0
        assert bool(dialog.windowFlags() & Qt.WindowType.FramelessWindowHint) == frameless
        ThemeManager.apply_theme('Light')
        assert not dialog.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_tray_theme_refresh_updates_glass_controls(
    app: QApplication,
    config: manager.ConfigManager,
) -> None:
    config.theme = 'Light'
    tab = SettingsTab(config)
    try:
        for theme in ('Glass', 'Light', 'Glass'):
            config.theme = theme
            tab.refresh_from_config()
            assert tab._glass_controls.isHidden() == (theme != 'Glass')  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('theme', ['Light', 'Dark'])
def test_palette_has_readable_text(app: QApplication, theme: str) -> None:
    ThemeManager.apply_theme(theme)
    parent = QWidget()
    palette = CommandPalette(parent, lambda: [])
    try:
        palette.ensurePolished()
        palette.results.ensurePolished()
        assert palette.results.palette().color(QPalette.ColorRole.Text).lightness() > 128
    finally:
        parent.deleteLater()
        app.processEvents()


def test_destroyed_glass_dashboard_does_not_break_theme_changes(app: QApplication) -> None:
    window = QDialog()
    window.setLayout(QVBoxLayout())
    window.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    glass.theme.attach(window)
    ThemeManager.apply_theme('Glass')
    window.show()
    app.processEvents()
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()
    assert not isValid(window)
    assert window not in chrome.registered_windows()
    assert window not in chrome._installed  # pyright: ignore[reportPrivateUsage]
    # Both enumeration and a direct stale reference must be harmless
    chrome.apply_chrome(window, enabled=False)
    ThemeManager.apply_theme('Light')
    ThemeManager.apply_theme('Glass')
    ThemeManager.apply_theme('Light')


@pytest.mark.parametrize('initial', ['Glass', 'Light'])
def test_palette_style_tracks_theme_changes(app: QApplication, initial: str) -> None:
    ThemeManager.apply_theme(initial)
    parent = QWidget()
    palette = CommandPalette(parent, lambda: [])
    try:
        for theme in ('Glass', 'Light', 'Dark', 'Glass', 'Light'):
            ThemeManager.apply_theme(theme)
            palette.popup()
            app.processEvents()
            assert palette.results.palette().color(QPalette.ColorRole.Text).lightness() > 128
            assert bool(palette.styleSheet()) == (theme != 'Glass')
            palette.close()
    finally:
        parent.deleteLater()
        app.processEvents()


@pytest.mark.parametrize('theme', ['Light', 'Dark', 'Glass'])
def test_conflict_details_follow_theme_colors(app: QApplication, theme: str) -> None:
    ThemeManager.apply_theme(theme)
    conflict = Conflict('1001', [Claim('a', '', 'id', 0), Claim('b', '', 'id', 1)])
    dialog = ConflictsDialog(None, [conflict])
    try:
        tree = dialog.findChild(QTreeWidget)
        assert tree is not None
        item = tree.topLevelItem(0)
        assert item is not None
        assert item.foreground(1).style() == Qt.BrushStyle.NoBrush
        assert item.foreground(2).style() == Qt.BrushStyle.NoBrush
        assert item.font(1).bold()
    finally:
        dialog.deleteLater()
        app.processEvents()
