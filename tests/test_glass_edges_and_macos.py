from __future__ import annotations

import subprocess
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PIL import Image
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QHoverEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget

from fleasion.gui.glass import chrome, wallpaper


@pytest.fixture
def app() -> QApplication:
    instance = QApplication.instance()
    return instance if isinstance(instance, QApplication) else QApplication([])


class _ResizeHandle:
    def __init__(self, succeeds: bool = True) -> None:
        self.edges: list[Qt.Edge] = []
        self.succeeds = succeeds

    def startSystemResize(self, edges: Qt.Edge) -> bool:
        self.edges.append(edges)
        return self.succeeds


def _native_tool(name: str) -> str:
    return '/usr/bin/' + name


@pytest.mark.parametrize(
    'position,expected',
    [
        (QPoint(3, 75), Qt.Edge.LeftEdge),
        (QPoint(196, 75), Qt.Edge.RightEdge),
        (QPoint(100, 3), Qt.Edge.TopEdge),
        (QPoint(100, 146), Qt.Edge.BottomEdge),
        (QPoint(3, 3), Qt.Edge.LeftEdge | Qt.Edge.TopEdge),
        (QPoint(196, 146), Qt.Edge.RightEdge | Qt.Edge.BottomEdge),
    ],
)
def test_child_widget_edge_press_starts_system_resize(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    position: QPoint,
    expected: Qt.Edge,
) -> None:
    window = QDialog()
    window.resize(200, 150)
    panel = QWidget(window)
    panel.setGeometry(window.rect())
    child = QPushButton(panel)
    child.setGeometry(2, 2, 196, 146)
    handle = _ResizeHandle()

    def window_handle(_window: QWidget) -> _ResizeHandle:
        return handle

    monkeypatch.setattr(chrome, '_window_handle', window_handle)
    resizer = chrome.EdgeResizer(window)
    try:
        local = child.mapFrom(window, position)
        event = QMouseEvent(
            QEvent.Type.MouseButtonPress,
            QPointF(local),
            QPointF(child.mapToGlobal(local)),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        QApplication.sendEvent(child, event)
        assert handle.edges == [expected]
        assert not child.isDown()
        resizer.detach()
        QApplication.sendEvent(child, event)
        assert handle.edges == [expected]
        assert child.isDown()
    finally:
        resizer.detach()
        window.deleteLater()
        app.processEvents()


def test_edge_hover_and_dynamic_child_attributes_are_restored(app: QApplication) -> None:
    window = QDialog()
    window.resize(200, 150)
    child = QWidget(window)
    child.setGeometry(2, 2, 196, 146)
    child.setCursor(Qt.CursorShape.PointingHandCursor)
    original_hover = child.testAttribute(Qt.WidgetAttribute.WA_Hover)
    resizer = chrome.EdgeResizer(window)
    dynamic = QWidget(child)
    dynamic.ensurePolished()
    try:
        assert dynamic.testAttribute(Qt.WidgetAttribute.WA_Hover)
        event = QHoverEvent(
            QEvent.Type.HoverMove,
            QPointF(1, 70),
            QPointF(child.mapToGlobal(QPoint(1, 70))),
            QPointF(50, 70),
        )
        QApplication.sendEvent(child, event)
        assert child.cursor().shape() == Qt.CursorShape.SizeHorCursor
        resizer.detach()
        assert child.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert child.testAttribute(Qt.WidgetAttribute.WA_Hover) == original_hover
        assert not dynamic.testAttribute(Qt.WidgetAttribute.WA_Hover)
    finally:
        resizer.detach()
        window.deleteLater()
        app.processEvents()


def test_resizer_does_not_consume_normal_or_failed_clicks(
    app: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window = QDialog()
    window.resize(200, 150)
    child = QPushButton(window)
    child.setGeometry(window.rect())
    handle = _ResizeHandle(succeeds=False)

    def window_handle(_window: QWidget) -> _ResizeHandle:
        return handle

    monkeypatch.setattr(chrome, '_window_handle', window_handle)
    resizer = chrome.EdgeResizer(window)
    try:
        for position in (QPoint(100, 75), QPoint(3, 75)):
            event = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(position),
                QPointF(child.mapToGlobal(position)),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            QApplication.sendEvent(child, event)
            assert child.isDown()
            child.setDown(False)
        assert handle.edges == [Qt.Edge.LeftEdge]
    finally:
        resizer.detach()
        window.deleteLater()
        app.processEvents()


def test_macos_wallpaper_uses_native_appkit_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / 'Wallpaper with spaces.png'
    Image.new('RGB', (10, 10)).save(source)
    commands: list[list[str]] = []

    def run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert capture_output and text and timeout == 2 and not check
        commands.append(args)
        return subprocess.CompletedProcess(args, 0, stdout=str(source) + '\n')

    monkeypatch.setattr(wallpaper, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(wallpaper.shutil, 'which', _native_tool)
    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    assert wallpaper.wallpaper_path() == source
    assert commands[0][:4] == ['/usr/bin/osascript', '-l', 'JavaScript', '-e']
    assert 'desktopImageURLForScreen' in commands[0][4]


@pytest.mark.parametrize('failure', ['empty', 'missing', 'error', 'timeout'])
def test_macos_wallpaper_unavailable_is_handled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    def run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        if failure == 'timeout':
            raise subprocess.TimeoutExpired(args, timeout)
        return subprocess.CompletedProcess(
            args,
            1 if failure == 'error' else 0,
            stdout='' if failure == 'empty' else str(tmp_path / 'missing.png'),
        )

    monkeypatch.setattr(wallpaper, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(wallpaper.shutil, 'which', _native_tool)
    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    assert wallpaper.wallpaper_path() is None


def test_macos_native_decoder_conversion_is_temporary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / 'wallpaper.heic'
    source.write_bytes(b'Native image format not understood by Pillow')
    targets: list[Path] = []

    def run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: int,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        assert args[:4] == ['/usr/bin/sips', '-s', 'format', 'png']
        assert args[4] == str(source)
        assert check and capture_output and text and timeout == 2
        target = Path(args[-1])
        targets.append(target)
        Image.new('RGB', (10, 10), '#4080c0').save(target)
        return subprocess.CompletedProcess(args, 0, stdout='')

    monkeypatch.setattr(wallpaper, 'sys', SimpleNamespace(platform='darwin'))
    monkeypatch.setattr(wallpaper.shutil, 'which', _native_tool)
    monkeypatch.setattr(wallpaper.subprocess, 'run', run)
    image = wallpaper.render_wallpaper(source, 100, 100)
    assert not image.isNull()
    assert len(targets) == 1
    assert not targets[0].exists()
