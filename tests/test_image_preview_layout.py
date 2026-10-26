"""Image previews fit their viewport when resized."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from fleasion.widgets.image_preview import ImagePreviewLabel
from fleasion.widgets.spacing import PANEL_MARGINS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    existing = QApplication.instance()
    application = existing if isinstance(existing, QApplication) else QApplication([])
    try:
        yield application
    finally:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        application.processEvents()


@pytest.mark.parametrize('image_size', [(1600, 200), (200, 1600)])
def test_image_fits_without_scrollbars_after_resize(
    app: QApplication, image_size: tuple[int, int]
) -> None:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(*PANEL_MARGINS)
    label = ImagePreviewLabel()
    layout.addWidget(label)
    scroll.setWidget(content)
    pixmap = QPixmap(*image_size)
    pixmap.fill(Qt.GlobalColor.red)
    label.set_image(pixmap)
    try:
        scroll.show()
        for size in ((640, 480), (100, 90), (800, 600)):
            scroll.resize(*size)
            app.processEvents()
            assert scroll.horizontalScrollBar().maximum() == 0
            assert scroll.verticalScrollBar().maximum() == 0
    finally:
        scroll.close()
        scroll.deleteLater()


def test_texture_pack_scrolls_only_when_its_images_exceed_the_viewport(app: QApplication) -> None:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    layout.setContentsMargins(*PANEL_MARGINS)
    pixmap = QPixmap(1024, 1024)
    pixmap.fill(Qt.GlobalColor.blue)
    for _ in range(3):
        label = ImagePreviewLabel(fit_height=False)
        layout.addWidget(label)
        label.set_image(pixmap)
    layout.addStretch()
    scroll.setWidget(content)
    try:
        scroll.show()
        for width, height, needs_vertical_scroll in ((640, 400, True), (240, 900, False)):
            scroll.resize(width, height)
            app.processEvents()
            assert scroll.horizontalScrollBar().maximum() == 0
            assert (scroll.verticalScrollBar().maximum() > 0) == needs_vertical_scroll
    finally:
        scroll.close()
        scroll.deleteLater()
