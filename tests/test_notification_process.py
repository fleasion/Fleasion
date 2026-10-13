from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QIcon, QPixmap
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from fleasion.app import tray as tray_module
from fleasion.app.notification_process import NotificationProcess
from fleasion.app.tray import SystemTray


def _app() -> QApplication:
    return cast('QApplication', QApplication.instance() or QApplication([]))


@pytest.mark.parametrize('code', ['pass', 'raise SystemExit(1)'])
def test_notification_command_success_and_failure(code: str) -> None:
    app = _app()
    parent = QObject(app)
    process = NotificationProcess(parent)
    finished = QSignalSpy(process.finished)
    failed = QSignalSpy(process.failed)
    process.start(sys.executable, ['-c', code])
    assert finished.wait(2000)
    assert finished.count() == 1
    assert failed.count() == int(code != 'pass')
    parent.deleteLater()


def test_stalled_notification_times_out_without_blocking_gui_events() -> None:
    app = _app()
    parent = QObject(app)
    process = NotificationProcess(parent, timeout_ms=100)
    finished = QSignalSpy(process.finished)
    failed = QSignalSpy(process.failed)
    events: list[str] = []
    process.start(sys.executable, ['-c', 'import time; time.sleep(10)'])
    assert finished.count() == 0
    QTimer.singleShot(0, lambda: events.append('gui event'))
    assert finished.wait(2000)
    assert events == ['gui event']
    assert failed.count() == 1
    parent.deleteLater()


def test_cancelled_notification_does_not_fall_back_during_shutdown() -> None:
    app = _app()
    parent = QObject(app)
    process = NotificationProcess(parent)
    finished = QSignalSpy(process.finished)
    failed = QSignalSpy(process.failed)
    process.start(sys.executable, ['-c', 'import time; time.sleep(10)'])
    process.cancel()
    assert finished.count() or finished.wait(2000)
    assert failed.count() == 0
    parent.deleteLater()


def test_failed_notify_send_falls_back_to_tray_message(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = _app()
    tray = SystemTray.__new__(SystemTray)
    icon = QSystemTrayIcon(app)
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor('blue'))
    icon.setIcon(QIcon(pixmap))
    tray.__dict__['tray'] = icon
    calls: list[tuple[str, str]] = []

    def fallback(title: str, message: str, _icon_path: Path | None) -> None:
        calls.append((title, message))

    setattr(tray, '_show_qt_notification', fallback)
    def missing_command(_name: str) -> str:
        return str(tmp_path / 'missing-command')

    monkeypatch.setattr(tray_module.shutil, 'which', missing_command)
    assert getattr(tray, '_show_freedesktop_notification')('Title', 'Message', None)
    assert calls == []
    process = icon.findChild(NotificationProcess)
    assert process is not None
    finished = QSignalSpy(process.finished)
    assert finished.wait(2000)
    assert calls == [('Title', 'Message')]
    icon.deleteLater()
