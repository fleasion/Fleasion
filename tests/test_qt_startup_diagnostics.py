from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtCore import QMessageLogContext, QtMsgType

from fleasion.utils import qt_diagnostics


def _qt_message_handler(message_type: QtMsgType, context: QMessageLogContext, message: str) -> None:
    callback = cast(
        'Callable[[QtMsgType, QMessageLogContext, str], None]',
        qt_diagnostics.__dict__['_qt_message_handler'],
    )
    callback(message_type, context, message)


def _noop_previous(_message_type: QtMsgType, _context: QMessageLogContext, _message: str) -> None:
    return None


def _record_log(entries: list[tuple[str, str]]) -> Callable[[str, str], None]:
    def record(category: str, message: str) -> None:
        entries.append((category, message))

    return record


def _context(category: str) -> QMessageLogContext:
    return cast('QMessageLogContext', SimpleNamespace(category=category))


def test_cache_viewer_import_does_not_load_opengl_viewers() -> None:
    code = """
import sys
import fleasion.cache.cache_viewer
assert 'fleasion.cache.obj_viewer' not in sys.modules
assert 'fleasion.cache.animation_viewer' not in sys.modules
assert 'OpenGL.GL' not in sys.modules
"""
    subprocess.run([sys.executable, '-c', code], check=True, env=os.environ.copy())


def test_gl_format_import_does_not_load_pyopengl() -> None:
    code = """
import sys
import fleasion.cache.gl_format
assert 'OpenGL.GL' not in sys.modules
"""
    subprocess.run([sys.executable, '-c', code], check=True, env=os.environ.copy())


def test_windows_legacy_gl_format_requests_4x_msaa(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.cache import gl_format

    monkeypatch.setattr(gl_format.sys, 'platform', 'win32')
    assert gl_format.legacy_gl_format().samples() == 4


def test_startup_opengl_config_does_not_create_global_share_context() -> None:
    code = """
import sys
from PySide6.QtGui import QOpenGLContext
from PySide6.QtWidgets import QApplication
from fleasion.app import _configure_opengl_for_legacy_viewers
_configure_opengl_for_legacy_viewers()
assert 'OpenGL.GL' not in sys.modules
app = QApplication([])
assert QOpenGLContext.globalShareContext() is None
"""
    env = os.environ.copy()
    env['QT_QPA_PLATFORM'] = 'offscreen'
    subprocess.run([sys.executable, '-c', code], check=True, env=env)


def test_lazy_3d_previews_use_raster_widgets_with_offscreen_gl() -> None:
    from PySide6.QtWidgets import QWidget

    from fleasion.cache.animation_viewer import AnimationGLWidget
    from fleasion.cache.obj_viewer import ObjViewerWidget
    from fleasion.cache.offscreen_gl_widget import OffscreenOpenGLWidget

    assert issubclass(ObjViewerWidget, OffscreenOpenGLWidget)
    assert issubclass(AnimationGLWidget, OffscreenOpenGLWidget)
    assert issubclass(OffscreenOpenGLWidget, QWidget)


def test_qt_warning_logging_suppresses_repeated_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    entries: list[tuple[str, str]] = []
    monkeypatch.setattr(qt_diagnostics, '_counts', {})
    monkeypatch.setattr(qt_diagnostics, '_previous_handler', _noop_previous)
    monkeypatch.delenv('FLEASION_QT_VERBOSE_LOGGING', raising=False)
    monkeypatch.setattr(
        qt_diagnostics.log_buffer,
        'log',
        _record_log(entries),
    )
    context = _context('qt.qpa.gl')

    for _ in range(6):
        _qt_message_handler(
            QtMsgType.QtWarningMsg,
            context,
            'Failed to create OpenGL context',
        )

    assert len(entries) == 4
    assert all(category == 'Qt' for category, _message in entries)
    assert 'repeated message suppressed after 3 occurrences' in entries[-1][1]


def test_qt_debug_logging_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    entries: list[tuple[str, str]] = []
    monkeypatch.setattr(qt_diagnostics, '_counts', {})
    monkeypatch.setattr(qt_diagnostics, '_previous_handler', _noop_previous)
    monkeypatch.delenv('FLEASION_QT_VERBOSE_LOGGING', raising=False)
    monkeypatch.setattr(
        qt_diagnostics.log_buffer,
        'log',
        _record_log(entries),
    )
    context = _context('qt.qpa.gl')

    _qt_message_handler(QtMsgType.QtDebugMsg, context, 'debug detail')
    assert entries == []

    monkeypatch.setenv('FLEASION_QT_VERBOSE_LOGGING', '1')
    _qt_message_handler(QtMsgType.QtDebugMsg, context, 'debug detail')
    assert entries == [('Qt', 'Debug [qt.qpa.gl] debug detail')]


def test_qt_message_handler_preserves_previous_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded: list[tuple[QtMsgType, str]] = []
    monkeypatch.setattr(qt_diagnostics, '_counts', {})
    monkeypatch.delenv('FLEASION_QT_VERBOSE_LOGGING', raising=False)

    def forward(message_type: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        forwarded.append((message_type, message))

    monkeypatch.setattr(qt_diagnostics, '_previous_handler', forward)
    context = _context('qt.qpa.gl')

    _qt_message_handler(QtMsgType.QtDebugMsg, context, 'native debug detail')

    assert forwarded == [(QtMsgType.QtDebugMsg, 'native debug detail')]
