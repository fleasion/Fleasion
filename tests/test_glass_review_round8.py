"""Regression tests for stylesheet ownership and wallpaper render recovery."""

from __future__ import annotations

import os
from concurrent.futures import Future
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from fleasion.gui.glass import controller, wallpaper
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator


_STARTUP_STYLESHEET = 'QDialogButtonBox, QMessageBox { button-layout: 0; }'


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    original_theme = ThemeManager._current_theme  # pyright: ignore[reportPrivateUsage]
    original_effective = ThemeManager._effective_theme  # pyright: ignore[reportPrivateUsage]
    original_stylesheet = application.styleSheet()
    yield application
    ThemeManager.apply_theme(original_theme)
    ThemeManager._effective_theme = original_effective  # pyright: ignore[reportPrivateUsage]
    application.setStyleSheet(original_stylesheet)
    application.processEvents()


def test_non_glass_themes_preserve_startup_stylesheet_without_ever_enabling_glass(
    app: QApplication,
) -> None:
    ThemeManager.apply_theme('Light')
    assert not controller.theme.active
    app.setStyleSheet(_STARTUP_STYLESHEET)
    for theme in ('System', 'Dark', 'Light', 'System'):
        ThemeManager.apply_theme(theme)
        assert app.styleSheet() == _STARTUP_STYLESHEET
        assert not controller.theme.active


def test_glass_restores_original_stylesheet_after_reenable_and_disable(
    app: QApplication,
) -> None:
    ThemeManager.apply_theme('Light')
    app.setStyleSheet(_STARTUP_STYLESHEET)
    ThemeManager.apply_theme('Glass')
    assert controller.theme.active
    assert app.styleSheet() == _STARTUP_STYLESHEET
    controller.theme.enable(app)
    ThemeManager.apply_theme('Dark')
    assert not controller.theme.active
    assert app.styleSheet() == _STARTUP_STYLESHEET
    # A redundant inactive disable must leave subsequent stylesheet changes alone
    new_stylesheet = _STARTUP_STYLESHEET + '\nQPushButton { padding: 4px; }'
    app.setStyleSheet(new_stylesheet)
    controller.theme.disable(app)
    assert app.styleSheet() == new_stylesheet
    ThemeManager.apply_theme('Glass')
    ThemeManager.apply_theme('System')
    assert app.styleSheet() == new_stylesheet


@pytest.mark.parametrize('complete_in', ['paint', 'poll'])
def test_wallpaper_retries_after_transient_render_failure_without_source_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete_in: str
) -> None:
    path = tmp_path / 'wallpaper.png'
    Image.new('RGB', (16, 16), '#123456').save(path)
    source_stamp = (path.stat().st_mtime_ns, path.stat().st_size)
    clock = [100.0]
    monkeypatch.setattr(wallpaper, 'monotonic', lambda: clock[0])
    original_renderer = wallpaper.render_wallpaper
    attempts: list[Path] = []

    def fail_once(source: Path, width: int, height: int) -> QImage:
        attempts.append(source)
        if len(attempts) == 1:
            raise OSError('transient wallpaper read failure')
        return original_renderer(source, width, height)

    monkeypatch.setattr(wallpaper, 'render_wallpaper', fail_once)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(path, source_stamp)  # pyright: ignore[reportPrivateUsage]
    cache._next_refresh = float('inf')  # pyright: ignore[reportPrivateUsage]
    try:
        assert cache.image(80, 60).isNull()
        entries = cache._entries  # pyright: ignore[reportPrivateUsage]
        assert len(entries) == 1
        failed = next(iter(entries.values()))
        assert isinstance(failed, Future)
        with pytest.raises(OSError, match='transient'):
            failed.result(timeout=3)
        if complete_in == 'paint':
            assert cache.image(80, 60).isNull()
        else:
            assert cache.refresh_source()
        assert len(attempts) == 1
        for seconds in (100.0, 103.0, 104.99):
            clock[0] = seconds
            assert cache.image(80, 60).isNull()
            assert cache.refresh_source() is (complete_in == 'paint' and seconds == 100.0)
            assert len(attempts) == 1
        clock[0] = 105.0
        assert cache.refresh_source() is False
        replacement = next(iter(entries.values()))
        assert isinstance(replacement, Future)
        assert replacement is not failed
        recovered = replacement.result(timeout=3)
        assert not recovered.isNull()
        assert cache.refresh_source()
        image = cache.image(80, 60)
        assert not image.isNull()
        assert image.size().width() == 80
        assert attempts == [path, path]
        assert cache._source.stamp == source_stamp  # pyright: ignore[reportPrivateUsage]
        assert cache.image(80, 60).cacheKey() == image.cacheKey()
        assert len(attempts) == 2
    finally:
        cache.close()


def test_null_wallpaper_render_is_retryable_and_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'broken-wallpaper.png'
    clock = [10.0]
    monkeypatch.setattr(wallpaper, 'monotonic', lambda: clock[0])
    render_calls: list[int] = []

    def broken(_path: Path, _width: int, _height: int) -> QImage:
        render_calls.append(1)
        return QImage()

    monkeypatch.setattr(wallpaper, 'render_wallpaper', broken)
    cache = wallpaper.WallpaperCache()
    cache._source = wallpaper.WallpaperSource(path)  # pyright: ignore[reportPrivateUsage]
    cache._next_refresh = float('inf')  # pyright: ignore[reportPrivateUsage]
    try:
        for attempt in range(3):
            assert cache.image(70, 70).isNull()
            current = next(iter(cache._entries.values()))  # pyright: ignore[reportPrivateUsage]
            assert isinstance(current, Future)
            assert current.result(timeout=3).isNull()
            assert cache.refresh_source()
            for _ in range(10):
                assert cache.image(70, 70).isNull()
                assert cache.refresh_source() is False
            assert len(render_calls) == attempt + 1
            clock[0] += wallpaper._RETRY_SECONDS  # pyright: ignore[reportPrivateUsage]
    finally:
        cache.close()
