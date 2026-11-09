"""Native-compatible regression tests for dashboard tab-frame animations."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QTabWidget, QWidget

from fleasion.gui import command_palette

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    yield application
    application.processEvents()


def _tabs() -> QTabWidget:
    tabs = QTabWidget()
    for name, rgb in [('Red', '255,0,0'), ('Blue', '0,0,255')]:
        page = QWidget()
        page.setStyleSheet(f'background-color: rgb({rgb});')
        tabs.addTab(page, name)
    tabs.resize(500, 340)
    command_palette.fade_tabs(tabs)
    tabs.show()
    return tabs


def _finish_fade(tabs: QTabWidget) -> None:
    controller = tabs.findChild(command_palette._TabTransitionController)  # pyright: ignore[reportPrivateUsage]
    assert controller is not None
    deadline = time.monotonic() + 2.0
    while controller._active is not None and time.monotonic() < deadline:  # pyright: ignore[reportPrivateUsage]
        QTest.qWait(20)
    assert controller._active is None  # pyright: ignore[reportPrivateUsage]
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not tabs.findChildren(command_palette._TabFade)  # pyright: ignore[reportPrivateUsage]


def test_tab_frame_animates_without_making_pages_transparent(app: QApplication) -> None:
    tabs = _tabs()
    try:
        app.processEvents()
        tabs.setCurrentIndex(1)
        app.processEvents()
        page = tabs.widget(1)
        assert page is not None
        frames = tabs.findChildren(command_palette._TabFade)  # pyright: ignore[reportPrivateUsage]
        assert len(frames) == 1
        frame = frames[0]
        assert frame.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert frame.size() == page.size()
        assert not frame._frame.isNull()  # pyright: ignore[reportPrivateUsage]
        assert frame._animation.state() == QPropertyAnimation.State.Running  # pyright: ignore[reportPrivateUsage]
        for index in range(tabs.count()):
            visited = tabs.widget(index)
            assert visited is not None
            assert not isinstance(visited.graphicsEffect(), QGraphicsOpacityEffect)
        _finish_fade(tabs)
        assert tabs.currentIndex() == 1
        assert page.isVisible()
        tabs.setCurrentIndex(0)
        _finish_fade(tabs)
    finally:
        tabs.close()
        tabs.deleteLater()
        app.processEvents()


def test_rapid_tab_switches_restore_full_page_pixels(app: QApplication) -> None:
    tabs = _tabs()
    try:
        for index in [1, 0, 1, 0, 1, 0, 1, 0, 1]:
            tabs.setCurrentIndex(index)
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert len(tabs.findChildren(command_palette._TabFade)) <= 1  # pyright: ignore[reportPrivateUsage]
            for page_index in range(tabs.count()):
                page = tabs.widget(page_index)
                assert page is not None and page.graphicsEffect() is None
        _finish_fade(tabs)
        page = tabs.currentWidget()
        assert page is not None
        sample = page.grab().toImage().pixelColor(page.width() // 2, page.height() // 2)
        assert sample == QColor('#0000ff')
        tabs.setCurrentIndex(0)
        _finish_fade(tabs)
        page = tabs.currentWidget()
        assert page is not None
        sample = page.grab().toImage().pixelColor(page.width() // 2, page.height() // 2)
        assert sample == QColor('#ff0000')
        assert not tabs.findChildren(QGraphicsOpacityEffect)
    finally:
        tabs.close()
        tabs.deleteLater()
        app.processEvents()
