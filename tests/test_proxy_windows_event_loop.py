import asyncio
import threading
from types import SimpleNamespace

import pytest

import fleasion.proxy.master as proxy_master


class FakeProactorEventLoop:
    def __init__(self, previous_handler=None):
        self.previous_handler = previous_handler
        self.exception_handler = None

    def get_exception_handler(self):
        return self.previous_handler

    def set_exception_handler(self, handler):
        self.exception_handler = handler

    def default_exception_handler(self, context):
        raise AssertionError(f'unexpected default handler call: {context}')


class WinAcceptError(OSError):
    winerror = 10014


def test_proactor_accept_fault_detection_is_exact(monkeypatch):
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    loop = FakeProactorEventLoop()
    error = WinAcceptError(14, 'bad address')
    context = {
        'message': 'Accept failed on a socket',
        'exception': error,
    }

    assert proxy_master.ProxyMaster._is_windows_proactor_accept_fault(loop, context)
    assert not proxy_master.ProxyMaster._is_windows_proactor_accept_fault(
        loop,
        {**context, 'message': 'Task exception was never retrieved'},
    )
    assert not proxy_master.ProxyMaster._is_windows_proactor_accept_fault(
        loop,
        {**context, 'exception': OSError(10048, 'address already in use')},
    )


def test_proxy_loop_diagnostics_capture_proactor_accept_fault(monkeypatch):
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    logs = []
    delegated = []
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=lambda category, message: logs.append((category, message))),
    )
    loop = FakeProactorEventLoop(
        previous_handler=lambda active_loop, context: delegated.append((active_loop, context))
    )
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy._windows_selector_fallback_attempted = False

    proxy._install_proxy_loop_diagnostics(loop, env_proxy_mode=True)
    error = WinAcceptError(14, 'bad address')
    context = {
        'message': 'Accept failed on a socket',
        'exception': error,
        'socket': SimpleNamespace(getsockname=lambda: ('127.0.0.1', 58443)),
    }
    loop.exception_handler(loop, context)

    assert proxy._windows_proactor_accept_fault
    assert delegated == [(loop, context)]
    assert any('winerror=10014' in message for _, message in logs)


def test_proxy_worker_retries_once_with_selector_loop(monkeypatch):
    calls = []
    logs = []

    def fake_run(coro, **kwargs):
        coro.close()
        calls.append(kwargs)
        if len(calls) == 1:
            raise proxy_master._RetryProxyWithWindowsSelector

    async def fake_run_proxy():
        return None

    monkeypatch.setattr(proxy_master.asyncio, 'run', fake_run)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=lambda category, message: logs.append((category, message))),
    )
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy._run_proxy = fake_run_proxy
    proxy._running = True

    proxy._run_proxy_worker()

    assert calls == [{}, {'loop_factory': asyncio.SelectorEventLoop}]
    assert proxy._windows_selector_fallback_attempted
    assert any('SelectorEventLoop' in message for _, message in logs)


def test_proactor_fault_cleanup_raises_retry_signal(monkeypatch):
    stopped = []
    loopbacks = []

    class FakeProxy:
        async def stop(self):
            stopped.append(True)

    monkeypatch.setattr(
        proxy_master,
        '_set_active_hosts_loopbacks',
        lambda value: loopbacks.append(value),
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=lambda *_args: None))
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    proxy._windows_proactor_accept_fault = True
    proxy._proxy = FakeProxy()
    proxy._active_proxy_port = 58443
    proxy._env_proxy_ready = threading.Event()
    proxy._env_proxy_ready.set()
    proxy._running = True

    with pytest.raises(proxy_master._RetryProxyWithWindowsSelector):
        asyncio.run(proxy._raise_selector_retry_for_proactor_fault())

    assert stopped == [True]
    assert proxy._proxy is None
    assert proxy._active_proxy_port is None
    assert not proxy._env_proxy_ready.is_set()
    assert not proxy._running
    assert loopbacks == [None]
