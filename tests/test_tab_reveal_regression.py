"""Native-compatible regression tests for dashboard tab reveal animations."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, Qt
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


def test_tab_reveal_animates_without_making_the_pages_transparent(app: QApplication) -> None:
    tabs = _tabs()
    try:
        app.processEvents()
        tabs.setCurrentIndex(1)
        app.processEvents()
        page = tabs.widget(1)
        assert page is not None
        covers = page.findChildren(command_palette._TabReveal)  # pyright: ignore[reportPrivateUsage]
        assert len(covers) == 1
        cover = covers[0]
        assert cover.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        assert cover.geometry() == page.rect()
        assert cover._alpha > 0.99  # pyright: ignore[reportPrivateUsage]
        for index in range(tabs.count()):
            visited = tabs.widget(index)
            assert visited is not None
            assert not isinstance(visited.graphicsEffect(), QGraphicsOpacityEffect)
        QTest.qWait(80)
        assert 0.0 < cover._alpha < 1.0  # pyright: ignore[reportPrivateUsage]
        page.resize(page.width() - 12, page.height() - 10)
        assert cover.geometry() == page.rect()
        QTest.qWait(170)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not tabs.findChildren(command_palette._TabReveal)  # pyright: ignore[reportPrivateUsage]
        assert tabs.currentIndex() == 1
        tabs.setCurrentIndex(0)
        QTest.qWait(230)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not tabs.findChildren(command_palette._TabReveal)  # pyright: ignore[reportPrivateUsage]
    finally:
        tabs.close()
        tabs.deleteLater()
        app.processEvents()


def test_tab_switching_many_times_restores_full_page_pixels(app: QApplication) -> None:
    tabs = _tabs()
    try:
        for index in [1, 0, 1, 0, 1, 0, 1, 0, 1]:
            tabs.setCurrentIndex(index)
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert len(tabs.findChildren(command_palette._TabReveal)) <= 1  # pyright: ignore[reportPrivateUsage]
        QTest.qWait(240)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not tabs.findChildren(command_palette._TabReveal)  # pyright: ignore[reportPrivateUsage]
        page = tabs.currentWidget()
        assert page is not None
        sample = page.grab().toImage().pixelColor(page.width() // 2, page.height() // 2)
        assert sample == QColor('#0000ff')
        tabs.setCurrentIndex(0)
        QTest.qWait(240)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        page = tabs.currentWidget()
        assert page is not None
        sample = page.grab().toImage().pixelColor(page.width() // 2, page.height() // 2)
        assert sample == QColor('#ff0000')
        assert not tabs.findChildren(QGraphicsOpacityEffect)
        assert not tabs.findChildren(command_palette._TabReveal)  # pyright: ignore[reportPrivateUsage]
    finally:
        tabs.close()
        tabs.deleteLater()
        app.processEvents()
