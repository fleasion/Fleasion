from types import SimpleNamespace

import conftest as pytest_config


class _ImmediateThread:
    def __init__(self, *, target, **_kwargs):
        self._target = target

    def start(self):
        self._target()

    def join(self, timeout):
        assert timeout == 0.25

    def is_alive(self):
        return False


def test_cross_thread_socket_wakeup_probe_accepts_working_environment():
    receiver = SimpleNamespace(
        settimeout=lambda timeout: None,
        recv=lambda size: b'\0',
        close=lambda: None,
    )
    sender = SimpleNamespace(sendall=lambda data: None, close=lambda: None)

    assert (
        pytest_config._cross_thread_socket_wakeup_failure(
            socketpair=lambda: (receiver, sender),
            thread_factory=_ImmediateThread,
        )
        is None
    )


def test_cross_thread_socket_wakeup_probe_reports_sandbox_denial():
    receiver = SimpleNamespace(close=lambda: None)

    def deny_send(_data):
        raise PermissionError(1, 'Operation not permitted')

    sender = SimpleNamespace(sendall=deny_send, close=lambda: None)

    failure = pytest_config._cross_thread_socket_wakeup_failure(
        socketpair=lambda: (receiver, sender),
        thread_factory=_ImmediateThread,
    )

    assert failure == 'PermissionError: [Errno 1] Operation not permitted'
