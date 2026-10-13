"""Regression tests for PR 86's disabled tabs, scoped conflicts and screen wallpapers."""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import Future
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QWidget

from fleasion.config import manager
from fleasion.gui import replacer_config
from fleasion.gui.conflicts import find_conflicts
from fleasion.gui.glass import surface, wallpaper
from fleasion.localization import tr
from fleasion.utils.json_types import JsonValue


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.fixture
def app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _enable(config: manager.ConfigManager, name: str, rules: list[dict[str, object]]) -> None:
    assert config.create_config(name)
    config.set_replacement_rules(name, cast('list[JsonValue]', rules))
    config.set_config_enabled(name, True)


def test_palette_excludes_disabled_tabs_and_rechecks_stale_navigation(
    app: QApplication, config: manager.ConfigManager
) -> None:
    window = replacer_config.ReplacerConfigWindow(config)
    try:
        tabs = window.tab_widget
        assert tabs.count() >= 2
        tabs.setTabEnabled(0, True)
        tabs.setTabEnabled(1, True)
        tabs.setCurrentIndex(0)
        nav_kind = tr('ui.gui.command_palette.kind_go_to')
        title = tabs.tabText(1)
        commands = window._palette_commands()  # pyright: ignore[reportPrivateUsage]
        command = next(c for c in commands if c.kind == nav_kind and c.title == title)

        # The palette was opened while this tab was ready, but preload later locked it
        tabs.setTabEnabled(1, False)
        command.run()
        assert tabs.currentIndex() == 0
        assert all(
            c.title != title
            for c in window._palette_commands()  # pyright: ignore[reportPrivateUsage]
            if c.kind == nav_kind
        )
        tabs.setTabEnabled(1, True)
        command.run()
        assert tabs.currentIndex() == 1
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def test_asset_type_and_exact_id_are_reported_as_potential_overlap(
    config: manager.ConfigManager
) -> None:
    _enable(config, 'type', [{'replace_ids': ['Mesh'], 'with_id': 2010}])
    _enable(config, 'asset', [{'replace_ids': [1001], 'with_id': 2020}])
    (conflict,) = find_conflicts(config)
    assert conflict.potential
    assert '1001' in conflict.asset and 'Mesh' in conflict.asset
    assert {claim.config for claim in conflict.claims} == {'type', 'asset'}
    assert conflict.asset != '1001'


def test_potential_overlap_requires_different_configs(config: manager.ConfigManager) -> None:
    _enable(
        config, 'one',
        [
            {'replace_ids': ['Mesh'], 'with_id': 2010},
            {'replace_ids': [1001], 'with_id': 2020},
        ],
    )
    assert find_conflicts(config) == []


def test_texpack_slot_scope_overlap(config: manager.ConfigManager) -> None:
    _enable(config, 'wildcard', [{'replace_ids': ['TexturePack:1'], 'with_id': 2010}])
    _enable(config, 'exact', [{'replace_ids': ['1001:1'], 'with_id': 2020}])
    (conflict,) = find_conflicts(config)
    assert conflict.potential
    assert 'TexturePack:1' in conflict.asset
    assert {claim.config for claim in conflict.claims} == {'wildcard', 'exact'}


def test_xfce_per_screen_sources_use_current_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = [tmp_path / f'image {index}.png' for index in range(3)]
    for source in sources:
        Image.new('RGB', (10, 10)).save(source)
    def executable(name: str) -> str:
        return name

    monkeypatch.setattr(wallpaper.shutil, 'which', executable)

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == 'xprop':
            return subprocess.CompletedProcess(args, 0, '_NET_CURRENT_DESKTOP = 2')
        return subprocess.CompletedProcess(
            args, 0,
            f'/backdrop/screen0/monitorDP-0/workspace0/last-image {sources[0]}\n'
            f'/backdrop/screen0/monitorDP-0/workspace2/last-image {sources[1]}\n'
            f'/backdrop/screen0/monitorHDMI-1/workspace2/last-image {sources[2]}\n',
        )

    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    discovered = wallpaper._xfce_wallpapers()  # pyright: ignore[reportPrivateUsage]
    assert discovered == {'DP-0': sources[1], 'HDMI-1': sources[2]}


def _wait_screen(cache: wallpaper.WallpaperCache, name: str) -> QImage:
    assert cache.image(50, 50, name).isNull()
    source, stamp = cache._source.for_screen(name)  # pyright: ignore[reportPrivateUsage]
    assert source is not None
    entry = cache._entries[(source, stamp, 50, 50)]  # pyright: ignore[reportPrivateUsage]
    assert isinstance(entry, Future)
    entry.result(timeout=3)
    return cache.image(50, 50, name)


def test_two_identically_sized_monitors_have_distinct_wallpaper_cache_entries(
    tmp_path: Path,
) -> None:
    red = tmp_path / 'red.png'
    blue = tmp_path / 'blue.png'
    Image.new('RGB', (20, 20), '#ff0000').save(red)
    Image.new('RGB', (20, 20), '#0000ff').save(blue)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(  # pyright: ignore[reportPrivateUsage]
        red,
        None,
        (('DP-0', red, (1, 1)), ('HDMI-1', blue, (2, 2))),
    )
    try:
        left = _wait_screen(cache, 'DP-0')
        right = _wait_screen(cache, 'HDMI-1')
        assert left.pixelColor(25, 25).red() == 255
        assert right.pixelColor(25, 25).blue() == 255
        assert left.cacheKey() != right.cacheKey()
        assert cache.image(50, 50, 'DP-0').cacheKey() == left.cacheKey()
        assert cache.image(50, 50, 'HDMI-1').cacheKey() == right.cacheKey()
    finally:
        cache.close()


def test_wallpaper_painter_uses_window_screen_and_changes_when_moved(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    red = tmp_path / 'red.png'
    blue = tmp_path / 'blue.png'
    Image.new('RGB', (20, 20), '#ff0000').save(red)
    Image.new('RGB', (20, 20), '#0000ff').save(blue)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(  # pyright: ignore[reportPrivateUsage]
        red, None, (('DP-0', red, (1, 1)), ('HDMI-1', blue, (2, 2)))
    )
    widget = QWidget()
    widget.resize(50, 50)
    target = ['DP-0']
    try:
        _wait_screen(cache, 'DP-0')
        _wait_screen(cache, 'HDMI-1')
        monkeypatch.setattr(surface, 'wallpapers', cache)
        def screen(_window: QWidget) -> SimpleNamespace:
            def name() -> str:
                return target[0]

            def geometry() -> QRect:
                return QRect(0, 0, 50, 50)

            return SimpleNamespace(name=name, geometry=geometry)

        def origin(_window: QWidget, _width: int, _height: int) -> QPoint:
            return QPoint(0, 0)

        monkeypatch.setattr(surface, '_screen', screen)
        monkeypatch.setattr(surface, '_origin_in_screen', origin)
        path = QPainterPath()
        path.addRect(0, 0, 50, 50)
        for monitor, blue_channel in [('DP-0', False), ('HDMI-1', True)]:
            target[0] = monitor
            canvas = QImage(50, 50, QImage.Format.Format_RGB888)
            painter = QPainter(canvas)
            surface._paint_wallpaper(painter, widget, path)  # pyright: ignore[reportPrivateUsage]
            painter.end()
            color = canvas.pixelColor(25, 25)
            assert (color.blue() == 255) == blue_channel
    finally:
        cache.close()
        widget.deleteLater()
        app.processEvents()


def test_deactivate_glass_closes_wallpaper_executor_and_clears_images(
    app: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'wallpaper.png'
    Image.new('RGB', (10, 10)).save(path)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(path)  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(surface, 'wallpapers', cache)
    windows = surface.GlassWindows(app)
    try:
        _wait_screen(cache, '')
        assert cache._executor is not None  # pyright: ignore[reportPrivateUsage]
        windows.restore()
        assert cache._executor is None  # pyright: ignore[reportPrivateUsage]
        assert not cache._entries  # pyright: ignore[reportPrivateUsage]
        assert cache._source.path is None  # pyright: ignore[reportPrivateUsage]
    finally:
        windows.restore()
        windows.deleteLater()
        app.processEvents()


def test_macos_per_screen_wallpapers_are_discovered_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from types import SimpleNamespace

    left = tmp_path / 'Built in.png'
    right = tmp_path / 'External.png'
    left.touch()
    right.touch()

    def executable(name: str) -> str:
        return f'/usr/bin/{name}'

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[:4] == ['/usr/bin/osascript', '-l', 'JavaScript', '-e']
        assert 'desktopImageURLForScreen' in args[4]
        return subprocess.CompletedProcess(
            args, 0, json.dumps({'Built-in Retina Display': str(left), 'External': str(right)})
        )

    monkeypatch.setattr(wallpaper, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(wallpaper.shutil, 'which', executable)
    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    assert wallpaper.screen_wallpaper_paths() == {
        'Built-in Retina Display': left,
        'External': right,
    }


def test_cache_recreates_discovery_worker_after_theme_reactivation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / 'wall.png'
    Image.new('RGB', (20, 20)).save(source)

    def discover() -> Path:
        return source

    monkeypatch.setattr(wallpaper, 'wallpaper_path', discover)
    cache = wallpaper.WallpaperCache()
    try:
        cache.refresh_source()
        first = cache._pending  # pyright: ignore[reportPrivateUsage]
        assert first is not None
        first.result(timeout=3)
        cache.refresh_source()
        assert cache._source.path == source  # pyright: ignore[reportPrivateUsage]
        cache.close()
        assert cache._source.path is None  # pyright: ignore[reportPrivateUsage]
        cache.refresh_source()
        next_discovery = cache._pending  # pyright: ignore[reportPrivateUsage]
        assert next_discovery is not None
        next_discovery.result(timeout=3)
        cache.refresh_source()
        assert cache._source.path == source  # pyright: ignore[reportPrivateUsage]
    finally:
        cache.close()


@pytest.mark.parametrize(
    ('first', 'second', 'expected'),
    [
        ('TexturePack:1', 1001, True),
        ('TexturePack:1', 'TexturePack', True),
        ('R6Animation', 1001, True),
        ('R15Animation', 'Animation', True),
        ('R6Animation', 'R15Animation', True),
        (1001, '1001:1', True),
        ('TexturePack', '1001:1', True),
        ('TexturePack:1', '1001:1', True),
        ('Mesh', 1001, True),
        ('TexturePack:1', 'Mesh', False),
        ('R6Animation', 'TexturePack', False),
        ('R6Animation', 'Mesh', False),
        ('TexturePack:1', 'TexturePack:2', False),
        ('1001:1', '1001:2', False),
        ('1001:1', '1002:1', False),
        (1001, 1002, False),
        ('Mesh', 'Audio', False),
    ],
)
def test_conflict_overlap_follows_possible_proxy_request(
    config: manager.ConfigManager, first: str | int, second: str | int, expected: bool
) -> None:
    _enable(config, 'a', [{'replace_ids': [first], 'with_id': 2001}])
    _enable(config, 'b', [{'replace_ids': [second], 'with_id': 2002}])
    overlaps = find_conflicts(config)
    assert bool(overlaps) is expected
    if expected:
        assert len(overlaps) == 1
        assert overlaps[0].potential
        assert {claim.config for claim in overlaps[0].claims} == {'a', 'b'}


def test_animated_proxy_type_ids_use_shared_matching_constraints() -> None:
    from fleasion.config.manager import ANIMATION_TYPE_KEYS
    from fleasion.proxy.addons.texture_stripper import TextureStripper

    assert TextureStripper._ANIM_TYPE_IDS == ANIMATION_TYPE_KEYS  # pyright: ignore[reportPrivateUsage]


def test_fade_tabs_does_not_accumulate_completed_animations(app: QApplication) -> None:
    from PySide6.QtCore import QAbstractAnimation, QEvent, QPropertyAnimation
    from PySide6.QtWidgets import QTabWidget

    from fleasion.gui.command_palette import fade_tabs

    tabs = QTabWidget()
    tabs.addTab(QWidget(), 'First')
    tabs.addTab(QWidget(), 'Second')
    fade_tabs(tabs)
    try:
        for index in range(100):
            tabs.setCurrentIndex((index + 1) % 2)
            active = tabs.findChildren(QPropertyAnimation)
            assert len(active) <= 2
            for animation in active:
                if animation.state() == QAbstractAnimation.State.Running:
                    animation.setCurrentTime(animation.duration())
            app.processEvents()
            app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not tabs.findChildren(QPropertyAnimation)
    finally:
        tabs.deleteLater()
        app.processEvents()


def test_many_unrelated_asset_ids_do_not_create_spurious_conflicts(
    config: manager.ConfigManager,
) -> None:
    _enable(config, 'even', [{'replace_ids': list(range(1000, 5000, 2)), 'with_id': 8001}])
    _enable(config, 'odd', [{'replace_ids': list(range(1001, 5000, 2)), 'with_id': 8002}])
    assert find_conflicts(config) == []


def test_rapid_tab_switches_eventually_release_animations(app: QApplication) -> None:
    from PySide6.QtCore import QEvent, QPropertyAnimation
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QTabWidget

    from fleasion.gui.command_palette import fade_tabs

    tabs = QTabWidget()
    tabs.addTab(QWidget(), 'First')
    tabs.addTab(QWidget(), 'Second')
    fade_tabs(tabs)
    try:
        for index in range(100):
            tabs.setCurrentIndex((index + 1) % 2)
        QTest.qWait(250)
        app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not tabs.findChildren(QPropertyAnimation)
    finally:
        tabs.deleteLater()
        app.processEvents()
