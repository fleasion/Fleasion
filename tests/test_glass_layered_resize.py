"""Layered Glass windows on Windows never repaint ahead of a pending resize."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, override

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEvent, QEventLoop, QObject, QSize, Qt
from PySide6.QtWidgets import QApplication, QWidget

from fleasion.gui.glass import layered

if TYPE_CHECKING:
    from collections.abc import Iterator


class _UpdateRequests(QObject):
    """Counts the update requests a window receives."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.UpdateRequest:
            self.count += 1
        return False


@pytest.fixture
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


@pytest.fixture
def window(app: QApplication) -> Iterator[QWidget]:
    widget = QWidget()
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.resize(400, 300)
    widget.show()
    app.processEvents()
    yield widget
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.fixture
def owner() -> QObject:
    return QObject()


def _guard(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, platform: str = 'windows'
) -> layered.LayeredResizeGuard:
    monkeypatch.setattr(layered, 'QApplication', SimpleNamespace(platformName=lambda: platform))
    return layered.LayeredResizeGuard(owner)


def _resize_natively(window: QWidget, size: QSize) -> None:
    # The native window takes the size at once; Qt queues the widget's resize
    window.windowHandle().resize(size)
    assert window.windowHandle().geometry().size() == size
    assert window.size() != size


def test_settled_window_paints_at_once(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, window: QWidget
) -> None:
    guard = _guard(monkeypatch, owner)

    assert not guard.defer(window)


def test_update_waits_for_pending_resize(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, app: QApplication, window: QWidget
) -> None:
    guard = _guard(monkeypatch, owner)
    requests = _UpdateRequests()
    window.installEventFilter(requests)
    _resize_natively(window, QSize(300, 300))

    # The request is consumed without being posted again straight away
    assert guard.defer(window)
    QCoreApplication.sendPostedEvents(window, QEvent.Type.UpdateRequest.value)
    assert requests.count == 0

    # Once Qt has resized the widget, a retry asks for the update again
    app.processEvents()
    assert window.size() == QSize(300, 300)
    deadline = QDeadlineTimer(1000)
    while requests.count == 0 and not deadline.hasExpired():
        app.processEvents(QEventLoop.ProcessEventsFlag.WaitForMoreEvents, 50)
    assert requests.count >= 1
    assert not guard.defer(window)


def test_opaque_and_other_platform_windows_are_left_alone(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, window: QWidget
) -> None:
    _resize_natively(window, QSize(320, 300))
    assert not _guard(monkeypatch, owner, 'xcb').defer(window)

    window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, on=False)
    assert not _guard(monkeypatch, owner).defer(window)


def test_resize_that_never_arrives_stops_deferring(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, window: QWidget
) -> None:
    guard = _guard(monkeypatch, owner)
    _resize_natively(window, QSize(280, 300))

    deferred = 0
    while guard.defer(window):
        deferred += 1
        assert deferred <= 64

    assert deferred > 1

    # The count starts over once a request has gone through
    assert guard.defer(window)


def test_each_resize_starts_the_retry_count_over(
    monkeypatch: pytest.MonkeyPatch, owner: QObject, window: QWidget
) -> None:
    guard = _guard(monkeypatch, owner)
    _resize_natively(window, QSize(260, 300))

    # A long drag holds updates back across many resizes without ever forcing one through
    for _ in range(64):
        assert guard.defer(window)
        guard.resized(window)
