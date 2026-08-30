from __future__ import annotations

from typing import TYPE_CHECKING, Never, Protocol, cast

import conftest as pytest_config

if TYPE_CHECKING:
    from collections.abc import Callable

_EXPECTED_JOIN_TIMEOUT = 0.25


class _Receiver(Protocol):
    def settimeout(self, timeout: float | None) -> None: ...

    def recv(self, size: int) -> bytes: ...

    def close(self) -> None: ...


class _Sender(Protocol):
    def sendall(self, data: bytes) -> None: ...

    def close(self) -> None: ...


class _ThreadWorker(Protocol):
    def start(self) -> None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...


class _ThreadFactory(Protocol):
    def __call__(
        self,
        *,
        target: Callable[[], None],
        name: str,
        daemon: bool,
    ) -> _ThreadWorker: ...


class _WakeupProbe(Protocol):
    def __call__(
        self,
        *,
        socketpair: Callable[[], tuple[_Receiver, _Sender]],
        thread_factory: _ThreadFactory,
    ) -> str | None: ...


class _WorkingReceiver:
    def settimeout(self, timeout: float | None) -> None:
        _ = self, timeout

    def recv(self, size: int) -> bytes:
        _ = self, size
        return b'\0'

    def close(self) -> None:
        _ = self


class _DeniedReceiver:
    def settimeout(self, timeout: float | None) -> Never:
        _ = self, timeout
        raise AssertionError

    def recv(self, size: int) -> Never:
        _ = self, size
        raise AssertionError

    def close(self) -> None:
        _ = self


class _WorkingSender:
    def sendall(self, data: bytes) -> None:
        _ = self, data

    def close(self) -> None:
        _ = self


class _DeniedSender:
    def sendall(self, data: bytes) -> Never:
        _ = self, data
        raise PermissionError(1, 'Operation not permitted')

    def close(self) -> None:
        _ = self


class _ImmediateThread:
    def __init__(
        self,
        *,
        target: Callable[[], None],
        name: str,
        daemon: bool,
    ) -> None:
        _ = name, daemon
        self._target = target

    def start(self) -> None:
        self._target()

    @staticmethod
    def join(timeout: float | None = None) -> None:
        assert timeout == _EXPECTED_JOIN_TIMEOUT

    @staticmethod
    def is_alive() -> bool:
        return False


def _run_wakeup_probe(
    *,
    socketpair: Callable[[], tuple[_Receiver, _Sender]],
) -> str | None:
    helper: object = vars(pytest_config)['_cross_thread_socket_wakeup_failure']
    wakeup_probe = cast('_WakeupProbe', helper)
    return wakeup_probe(socketpair=socketpair, thread_factory=_ImmediateThread)


def test_cross_thread_socket_wakeup_probe_accepts_working_environment() -> None:
    receiver = _WorkingReceiver()
    sender = _WorkingSender()

    assert _run_wakeup_probe(socketpair=lambda: (receiver, sender)) is None


def test_cross_thread_socket_wakeup_probe_reports_sandbox_denial() -> None:
    receiver = _DeniedReceiver()
    sender = _DeniedSender()

    failure = _run_wakeup_probe(socketpair=lambda: (receiver, sender))

    assert failure == 'PermissionError: [Errno 1] Operation not permitted'
