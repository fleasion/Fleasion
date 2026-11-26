"""A half-built Subplace Joiner tab survives resize events delivered while it is hidden."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QDeadlineTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from fleasion.gui.subplace_joiner_tab import SubplaceJoinerTab

if TYPE_CHECKING:
    from collections.abc import Iterator
    from types import TracebackType


@pytest.fixture
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def page(app: QApplication) -> Iterator[QWidget]:
    widget = QWidget()
    QVBoxLayout(widget).addWidget(QLabel('Visible sibling'))
    widget.resize(900, 600)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


def test_render_during_deferred_build_does_not_lay_out_missing_results(
    monkeypatch: pytest.MonkeyPatch, app: QApplication, page: QWidget
) -> None:
    errors: list[BaseException] = []

    def record(_kind: type[BaseException], error: BaseException, _tb: TracebackType | None) -> None:
        errors.append(error)

    monkeypatch.setattr(sys, 'excepthook', record)

    # Stop the build before the results grid exists
    tab = SubplaceJoinerTab(parent=page, defer_setup=True)
    steps = tab.build_ui()
    next(steps)
    assert tab.isHidden()

    # Rendering any visible widget, as Glass's rounded item views do on every
    # repaint, delivers the hidden tab's pending resize event
    label = page.findChild(QLabel)
    assert label is not None
    label.render(QPixmap(label.size()))

    # Give the resize debounce time to fire
    deadline = QDeadlineTimer(200)
    while not deadline.hasExpired():
        app.processEvents()

    assert errors == []
