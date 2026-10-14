"""Transparent Glass repaints replace stale pixels and keep surfaces neutral."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget

from fleasion.appearance import ACCENTS
from fleasion.gui.glass import surface
from fleasion.gui.glass.state import GlassSettings, state
from fleasion.gui.theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fleasion.gui.glass.desktop_blur import BlurStatus
    from fleasion.gui.glass.state import Backdrop


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    monkeypatch.setattr(state, 'settings', GlassSettings(opacity=0.2, backdrop='desktop'))
    monkeypatch.setattr(state, 'base_palette', None)
    monkeypatch.setattr(state, 'dark', True)
    monkeypatch.setattr(surface.blur, 'cached_status', lambda: 'on')
    yield application


def _image() -> QImage:
    result = QImage(180, 120, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(Qt.GlobalColor.transparent)
    return result


def _paint(image: QImage, clip: QRect | None = None) -> None:
    painter = QPainter(image)
    if clip is not None:
        painter.setClipRect(clip)
    surface.paint_glass(painter, QRectF(0.5, 0.5, 179, 119), 14)
    painter.end()


def test_repaint_erases_previous_ui_pixels(app: QApplication) -> None:
    fresh = _image()
    _paint(fresh)
    previous = _image()
    painter = QPainter(previous)
    painter.fillRect(20, 20, 120, 60, QColor('#ff1a37'))
    painter.setPen(Qt.GlobalColor.white)
    painter.drawText(25, 45, 'Previous layout')
    painter.end()
    _paint(previous)
    assert previous == fresh
    for _ in range(4):
        _paint(previous)
    assert previous == fresh


def test_partial_repaint_replaces_only_the_dirty_region(app: QApplication) -> None:
    fresh = _image()
    _paint(fresh)
    dirty = QRect(25, 20, 40, 50)
    previous = _image()
    previous.fill(QColor('#ff1a37'))
    _paint(previous, dirty)
    assert previous.pixelColor(dirty.center()) == fresh.pixelColor(dirty.center())
    assert previous.pixelColor(5, 5) == QColor('#ff1a37')


@pytest.mark.parametrize('dark', [False, True])
def test_accent_does_not_change_background_pixels(app: QApplication, dark: bool) -> None:
    state.dark = dark
    reference = _image()
    state.settings.accent = 'Ice'
    _paint(reference)
    for accent in ACCENTS:
        state.settings.accent = accent
        image = _image()
        _paint(image)
        assert image == reference, accent


@pytest.mark.parametrize('status', ['on', 'off', 'unavailable'])
@pytest.mark.parametrize('opacity', [0.2, 0.45, 0.8, 1.0])
def test_window_under_honors_opacity_without_requiring_blur(
    app: QApplication, monkeypatch: pytest.MonkeyPatch, status: BlurStatus, opacity: float
) -> None:
    monkeypatch.setattr(surface.blur, 'cached_status', lambda: status)
    state.settings.opacity = opacity
    assert surface.effective_opacity() == opacity
    image = _image()
    _paint(image)
    assert image.pixelColor(90, 100).alphaF() == pytest.approx(opacity, abs=0.04)


@pytest.mark.parametrize('backdrop', ['desktop', 'wallpaper'])
def test_reducing_opacity_replaces_the_previous_surface(
    app: QApplication, backdrop: Backdrop
) -> None:
    state.settings.backdrop = backdrop
    image = _image()
    state.settings.opacity = 0.9
    _paint(image)
    opaque = image.pixelColor(90, 100).alpha()
    state.settings.opacity = 0.2
    _paint(image)
    assert image.pixelColor(90, 100).alpha() < opaque
    fresh = _image()
    _paint(fresh)
    assert image == fresh


def test_live_backing_store_has_no_old_content_after_move_and_scroll(
    app: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous_style = ThemeManager._style  # pyright: ignore[reportPrivateUsage]
    previous_mode = ThemeManager._color_mode  # pyright: ignore[reportPrivateUsage]
    previous_accent = ThemeManager._accent  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(state, '_config', None)
    window = QDialog()
    window.resize(400, 260)
    layout = QVBoxLayout(window)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    content.setMinimumHeight(700)
    label = QLabel('Previous layout', content)
    label.setStyleSheet('background-color: red; color: white;')
    label.setGeometry(20, 70, 140, 30)
    scroll.setWidget(content)
    layout.addWidget(scroll)
    try:
        ThemeManager.apply_appearance('Glass', 'Dark', 'Ice')
        window.show()
        app.processEvents()
        previous_position = label.mapTo(window, QPoint(50, 15))
        label.move(180, 180)
        app.processEvents()
        for offset in (0, 30, 85, 0):
            scroll.verticalScrollBar().setValue(offset)
            app.processEvents()
            store = window.backingStore()
            assert store is not None
            device = store.paintDevice()
            assert isinstance(device, QImage)
            cached = device.copy()
            fresh = window.grab().toImage()
            assert cached.pixelColor(previous_position) == fresh.pixelColor(previous_position)
            for x in range(30, 350, 40):
                for y in range(40, 220, 30):
                    assert cached.pixelColor(x, y) == fresh.pixelColor(x, y), (offset, x, y)
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        ThemeManager.apply_appearance(previous_style, previous_mode, previous_accent)
