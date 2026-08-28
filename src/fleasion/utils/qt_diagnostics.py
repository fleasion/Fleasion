"""Low-volume Qt diagnostics for release builds."""

from __future__ import annotations

import os
import sys
import threading

from PyQt6.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, QtMsgType, qInstallMessageHandler

from .logging import log_buffer

_MAX_IDENTICAL_MESSAGES = 3
_VERBOSE_ENV = 'FLEASION_QT_VERBOSE_LOGGING'
_counts: dict[tuple[str, str, str], int] = {}
_counts_lock = threading.Lock()
_installed = False
_previous_handler = None


def _message_level(message_type: QtMsgType) -> str:
    return {
        QtMsgType.QtDebugMsg: 'Debug',
        QtMsgType.QtInfoMsg: 'Info',
        QtMsgType.QtWarningMsg: 'Warning',
        QtMsgType.QtCriticalMsg: 'Critical',
        QtMsgType.QtFatalMsg: 'Fatal',
    }.get(message_type, str(message_type))


def _should_log(message_type: QtMsgType) -> bool:
    if message_type in {
        QtMsgType.QtWarningMsg,
        QtMsgType.QtCriticalMsg,
        QtMsgType.QtFatalMsg,
    }:
        return True
    return os.environ.get(_VERBOSE_ENV) == '1'


def _forward_to_previous_handler(message_type: QtMsgType, context, message: str) -> None:
    """Preserve existing Qt console/debugger output when diagnostics are installed."""
    if _previous_handler is not None:
        try:
            _previous_handler(message_type, context, message)
        except Exception:
            pass
        return

    # qInstallMessageHandler() returns None for Qt's built-in handler. Frozen
    # windowed builds have no useful stderr, but source/development runs do.
    if not getattr(sys, 'frozen', False) and sys.stderr is not None:
        try:
            level = _message_level(message_type)
            category = getattr(context, 'category', None) or 'qt'
            print(f'Qt {level} [{category}] {message}', file=sys.stderr)
        except Exception:
            pass


def _qt_message_handler(message_type: QtMsgType, context, message: str) -> None:
    """Write useful Qt diagnostics while suppressing repetitive warning storms."""
    try:
        if not _should_log(message_type):
            return

        level = _message_level(message_type)
        category = getattr(context, 'category', None) or 'qt'
        text = str(message).strip()
        key = (level, category, text)

        with _counts_lock:
            count = _counts.get(key, 0) + 1
            _counts[key] = count

        if count <= _MAX_IDENTICAL_MESSAGES:
            log_buffer.log('Qt', f'{level} [{category}] {text}')
        elif count == _MAX_IDENTICAL_MESSAGES + 1:
            log_buffer.log(
                'Qt',
                f'{level} [{category}] repeated message suppressed after '
                f'{_MAX_IDENTICAL_MESSAGES} occurrences: {text}',
            )
    finally:
        _forward_to_previous_handler(message_type, context, message)


def install_qt_message_logging() -> None:
    """Install a process-wide Qt message handler once without risking startup."""
    global _installed, _previous_handler
    if _installed:
        return
    try:
        _previous_handler = qInstallMessageHandler(_qt_message_handler)
    except Exception as exc:
        log_buffer.log('Qt', f'Could not install Qt message logging: {exc}')
        return
    _installed = True
    mode = 'verbose' if os.environ.get(_VERBOSE_ENV) == '1' else 'warnings/errors'
    log_buffer.log(
        'Qt',
        f'Qt {QT_VERSION_STR}, PyQt {PYQT_VERSION_STR}; message logging={mode}',
    )
