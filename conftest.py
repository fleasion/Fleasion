"""Repository-wide pytest environment guards."""

import os
import socket
import sys
import threading
from collections.abc import Callable, Iterator
from tempfile import TemporaryDirectory
from typing import Protocol

import pytest

_THREADED_ASYNCIO_MARKER = 'threaded_asyncio'


class _PytestConfigIsolation:
    config_home: TemporaryDirectory[str] | None = None
    environment_key: str | None = None
    original_value: str | None = None



def pytest_sessionstart(session: pytest.Session) -> None:
    """Keep all Fleasion config paths in an isolated tree during pytest."""
    del session
    if 'fleasion.utils.paths' in sys.modules:
        pytest.exit('Fleasion paths were initialized before pytest could isolate the user config')
    key = (
        'LOCALAPPDATA' if sys.platform == 'win32'
        else 'FLEASION_USER_HOME' if sys.platform == 'darwin'
        else 'XDG_CONFIG_HOME'
    )
    _PytestConfigIsolation.environment_key = key
    _PytestConfigIsolation.original_value = os.environ.get(key)
    _PytestConfigIsolation.config_home = TemporaryDirectory(prefix='fleasion-pytest-config-')
    os.environ[key] = _PytestConfigIsolation.config_home.name


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Restore the external environment after pytest finishes."""
    del session, exitstatus
    key = _PytestConfigIsolation.environment_key
    if key is not None:
        if _PytestConfigIsolation.original_value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = _PytestConfigIsolation.original_value
    if _PytestConfigIsolation.config_home is not None:
        _PytestConfigIsolation.config_home.cleanup()
        _PytestConfigIsolation.config_home = None



class _ThreadFactory(Protocol):
    def __call__(
        self,
        *,
        target: Callable[[], None],
        name: str,
        daemon: bool,
    ) -> threading.Thread: ...


type _SocketPairFactory = Callable[[], tuple[socket.socket, socket.socket]]


def _cross_thread_socket_wakeup_failure(
    *,
    socketpair: _SocketPairFactory = socket.socketpair,
    thread_factory: _ThreadFactory = threading.Thread,
) -> str | None:
    """Return why a socket wakeup failed, or ``None`` when it works."""
    receiver, sender = socketpair()
    errors: list[OSError] = []

    def wake() -> None:
        try:
            sender.sendall(b'\0')
        except OSError as exc:
            errors.append(exc)

    worker = thread_factory(target=wake, name='pytest-sandbox-wakeup-probe', daemon=True)
    try:
        worker.start()
        worker.join(timeout=0.25)
        if worker.is_alive():
            return 'the cross-thread socket send did not finish within 0.25 seconds'
        if errors:
            error = errors[0]
            return f'{type(error).__name__}: {error}'

        receiver.settimeout(0.1)
        try:
            if receiver.recv(1) != b'\0':
                return 'the socketpair received an unexpected wakeup payload'
        except OSError as exc:
            return f'{type(exc).__name__}: {exc}'
        return None
    finally:
        receiver.close()
        sender.close()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Fail fast when selected async/thread tests would deadlock in a sandbox."""
    guarded_items = [item for item in items if item.get_closest_marker(_THREADED_ASYNCIO_MARKER)]
    if not guarded_items:
        return

    failure = _cross_thread_socket_wakeup_failure()
    if failure is None:
        return

    selected = ', '.join(item.nodeid for item in guarded_items[:3])
    if len(guarded_items) > 3:
        selected += f', and {len(guarded_items) - 3} more'
    pytest.exit(
        'Cross-thread event-loop wakeups are blocked in this environment '
        f'({failure}). The selected test(s) would hang: {selected}. '
        'Rerun pytest outside the restricted sandbox.',
        returncode=2,
    )


@pytest.fixture(autouse=True)  # ruff: ignore[pytest-fixture-autouse]
def _finish_qt_deletions() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Dispatch Qt deferred destruction after each test and its fixtures finish."""
    yield
    # Most tests never import Qt; do not initialize it just for teardown
    if 'PySide6.QtWidgets' not in sys.modules:
        return
    from PySide6.QtCore import QCoreApplication, QEvent  # ruff: ignore[import-outside-top-level]
    from PySide6.QtWidgets import QApplication  # ruff: ignore[import-outside-top-level]

    if isinstance(QApplication.instance(), QApplication):
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
