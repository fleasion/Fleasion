"""Repository-wide pytest environment guards."""

import socket
import threading
from collections.abc import Callable
from typing import Protocol

import pytest

_THREADED_ASYNCIO_MARKER = 'threaded_asyncio'


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
    errors: list[BaseException] = []

    def wake() -> None:
        try:
            sender.sendall(b'\0')
        except BaseException as exc:  # ruff: ignore[blind-except]
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
    if len(guarded_items) > 3:  # ruff: ignore[magic-value-comparison]
        selected += f', and {len(guarded_items) - 3} more'
    pytest.exit(
        'Cross-thread event-loop wakeups are blocked in this environment '
        f'({failure}). The selected test(s) would hang: {selected}. '
        'Rerun pytest outside the restricted sandbox.',
        returncode=2,
    )
