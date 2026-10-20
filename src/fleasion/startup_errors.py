"""Explain Windows application control blocks that stop Fleasion from starting.

Smart App Control and other Windows Defender Application Control policies
check every native module as it loads. Fleasion's bundled modules are not
code-signed, so a policy can block one (``PyQt6\\sip``, ``PIL\\_imaging``, ...)
before Fleasion has a window, which otherwise surfaces as a bare traceback.

This runs before settings, and with them the chosen language, are loaded, and
must not import anything that could itself be blocked; it uses only the
standard library and shows its message in English.
"""

from __future__ import annotations

import ctypes
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

# ERROR_SYSTEM_INTEGRITY_POLICY_VIOLATION: "An Application Control policy has
# blocked this file."
_APP_CONTROL_WINERROR = 4551
_APP_CONTROL_TEXT = 'Application Control policy has blocked'
STATUS_URL = 'https://github.com/fleasion/Fleasion/issues/82'

_TITLE = 'Windows blocked Fleasion'
_MESSAGE = (
    'Windows Smart App Control (or another application control policy) blocked a '
    'file Fleasion needs to start:\n\n{blocked}\n\n'
    "Fleasion's bundled files are not code-signed yet, so Windows has no reputation "
    'for them, and Fleasion cannot work around the block itself.\n\n'
    'If you trust this download, you can turn Smart App Control off in Windows '
    'Security > App & browser control > Smart App Control settings. On some Windows '
    'versions it cannot be turned back on without reinstalling Windows.\n\n'
    'Progress on a signed build: {url}'
)


def _exception_chain(exc: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def blocked_by_application_control(exc: BaseException) -> bool:
    """Whether ``exc`` comes from Windows refusing to load a native module."""
    return any(
        getattr(error, 'winerror', None) == _APP_CONTROL_WINERROR or _APP_CONTROL_TEXT in str(error)
        for error in _exception_chain(exc)
    )


def blocked_file(exc: BaseException) -> str:
    """Best description of what was blocked, for the message."""
    for error in _exception_chain(exc):
        for attribute in ('filename', 'path', 'name'):
            value = getattr(error, attribute, None)
            if value:
                return str(value)
    return str(exc)


def application_control_message(exc: BaseException) -> str:
    """The explanation shown to the user."""
    return _MESSAGE.format(blocked=blocked_file(exc), url=STATUS_URL)


def report_blocked_startup(exc: BaseException) -> bool:
    """Show why Windows stopped Fleasion; return False if ``exc`` is unrelated."""
    if sys.platform != 'win32' or not blocked_by_application_control(exc):
        return False
    message = application_control_message(exc)
    mb_iconerror = 0x10
    try:
        ctypes.windll.user32.MessageBoxW(None, message, _TITLE, mb_iconerror)
    except AttributeError, OSError:
        sys.stderr.write(message + '\n')
    return True
