import asyncio
import ssl
import threading
from collections.abc import Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Never, Protocol, cast, override

import pytest

import fleasion.proxy.master as proxy_master
from fleasion.proxy.server import FleasionProxy
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

type _ExceptionContext = dict[str, object]
type _ExceptionHandler = Callable[[asyncio.AbstractEventLoop, _ExceptionContext], object]
type _ProxyCoroutine = Coroutine[object, object, None]


class _RawTlsProbe(Protocol):
    def __call__(
        self,
        host: str,
        ca_cert_path: Path,
        cert_path: Path,
        key_path: Path,
        *,
        tls_max_version: ssl.TLSVersion = proxy_master.PROXY_TLS_MAX_VERSION,
    ) -> tuple[bool, str]: ...


class _InMemoryTlsProbe(Protocol):
    def __call__(
        self,
        host: str,
        ca_cert_path: Path,
        cert_path: Path,
        key_path: Path,
        tls_max_version: ssl.TLSVersion = proxy_master.PROXY_TLS_MAX_VERSION,
    ) -> tuple[bool, str]: ...


class FakeProactorEventLoop(asyncio.AbstractEventLoop):
    def __init__(self, previous_handler: _ExceptionHandler | None = None) -> None:
        self.previous_handler = previous_handler
        self.exception_handler: _ExceptionHandler | None = None

    @override
    def get_exception_handler(self) -> _ExceptionHandler | None:
        return self.previous_handler

    @override
    def set_exception_handler(self, handler: _ExceptionHandler | None) -> None:
        self.exception_handler = handler

    @override
    def default_exception_handler(self, context: _ExceptionContext) -> Never:
        message = f'unexpected default handler call: {context}'
        raise AssertionError(message)


class WinAcceptError(OSError):
    winerror = 10014


class _StoppingProxy(FleasionProxy):
    def __init__(self, stopped: list[bool]) -> None:
        self._stopped = stopped

    @override
    async def stop(self) -> None:
        self._stopped.append(True)


class _TlsRecordingProxy(FleasionProxy):
    def __init__(self, tls_changes: list[ssl.TLSVersion]) -> None:
        self._tls_changes = tls_changes

    @override
    def set_local_tls_max_version(self, version: ssl.TLSVersion) -> None:
        self._tls_changes.append(version)

    @override
    def loopback_ips_for_hosts(self) -> tuple[str, ...]:
        return ('127.0.0.1', '::1')


class _ProxyMasterHarness(proxy_master.ProxyMaster):
    def detects_windows_proactor_accept_fault(
        self,
        loop: asyncio.AbstractEventLoop,
        context: _ExceptionContext,
    ) -> bool:
        return self._is_windows_proactor_accept_fault(loop, context)

    def install_proxy_loop_diagnostics(
        self,
        loop: asyncio.AbstractEventLoop,
        *,
        env_proxy_mode: bool,
    ) -> None:
        self._install_proxy_loop_diagnostics(loop, env_proxy_mode=env_proxy_mode)

    def set_windows_selector_fallback_attempted(self, *, value: bool) -> None:
        self._windows_selector_fallback_attempted = value

    @property
    def windows_selector_fallback_attempted(self) -> bool:
        return self._windows_selector_fallback_attempted

    def set_windows_proactor_accept_fault(self, *, value: bool) -> None:
        self._windows_proactor_accept_fault = value

    @property
    def windows_proactor_accept_fault(self) -> bool:
        return self._windows_proactor_accept_fault

    def set_hosts_proxy_ready(self, event: threading.Event) -> None:
        self._hosts_proxy_ready = event

    @property
    def hosts_proxy_ready(self) -> threading.Event:
        return self._hosts_proxy_ready

    def set_worker_thread(self, thread: threading.Thread | None) -> None:
        self._thread = thread

    def override_run_proxy(self, run_proxy: Callable[[], _ProxyCoroutine]) -> None:
        vars(self)['_run_proxy'] = run_proxy

    def run_proxy_worker(self) -> None:
        self._run_proxy_worker()

    def set_running(self, *, value: bool) -> None:
        self._running = value

    @property
    def running(self) -> bool:
        return self._running

    def set_linux_env_proxy_override_client_key(self, value: str | None) -> None:
        self._linux_env_proxy_override_client_key = value

    @property
    def linux_env_proxy_override_client_key(self) -> str | None:
        return self._linux_env_proxy_override_client_key

    def set_sober_env_proxy_override_active(self, *, value: bool) -> None:
        self._sober_env_proxy_override_active = value

    @property
    def sober_env_proxy_override_active(self) -> bool:
        return self._sober_env_proxy_override_active

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def set_proxy(self, proxy: FleasionProxy | None) -> None:
        self._proxy = proxy

    @property
    def proxy(self) -> FleasionProxy | None:
        return self._proxy

    def set_active_proxy_port(self, port: int | None) -> None:
        self._active_proxy_port = port

    @property
    def active_proxy_port(self) -> int | None:
        return self._active_proxy_port

    def set_env_proxy_ready(self, event: threading.Event) -> None:
        self._env_proxy_ready = event

    @property
    def env_proxy_ready(self) -> threading.Event:
        return self._env_proxy_ready

    async def raise_selector_retry_for_proactor_tls_failure(
        self,
        *,
        raw_tls_probe_ok: bool = False,
    ) -> None:
        await self._raise_selector_retry_for_proactor_tls_failure(raw_tls_probe_ok=raw_tls_probe_ok)

    def set_env_proxy_loopback_host(self, host: str) -> None:
        self._env_proxy_loopback_host = host

    @property
    def env_proxy_loopback_host(self) -> str:
        return self._env_proxy_loopback_host

    @property
    def active_local_tls_max_version(self) -> ssl.TLSVersion:
        return self._active_local_tls_max_version

    async def run_startup_tls_self_test(
        self,
        hosts: set[str],
        ca_cert_path: Path,
        port: int,
        *,
        explicit_proxy: bool,
    ) -> bool:
        return await self._run_startup_tls_self_test(
            hosts,
            ca_cert_path,
            port,
            explicit_proxy=explicit_proxy,
        )


def _new_proxy_master() -> _ProxyMasterHarness:
    return _ProxyMasterHarness.__new__(_ProxyMasterHarness)


def _retry_signal_type() -> type[RuntimeError]:
    candidate: object = vars(proxy_master)['_RetryProxyWithWindowsSelector']
    if not isinstance(candidate, type) or not issubclass(candidate, RuntimeError):
        message = 'proxy retry signal must remain a RuntimeError subclass'
        raise TypeError(message)
    return candidate


def _raw_tls_probe() -> _RawTlsProbe:
    candidate: object = vars(proxy_master)['_run_raw_tls_loopback_probe_sync']
    if not callable(candidate):
        message = 'raw TLS probe must remain callable'
        raise TypeError(message)
    return cast('_RawTlsProbe', candidate)


def _in_memory_tls_probe() -> _InMemoryTlsProbe:
    candidate: object = vars(proxy_master)['_run_in_memory_tls_probe_sync']
    if not callable(candidate):
        message = 'in-memory TLS probe must remain callable'
        raise TypeError(message)
    return cast('_InMemoryTlsProbe', candidate)


def _discard_log(*_args: object) -> None:
    pass


def test_proactor_accept_fault_detection_is_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    loop = FakeProactorEventLoop()
    error = WinAcceptError(14, 'bad address')
    context: _ExceptionContext = {
        'message': 'Accept failed on a socket',
        'exception': error,
    }
    proxy = _new_proxy_master()

    assert proxy.detects_windows_proactor_accept_fault(loop, context)
    assert not proxy.detects_windows_proactor_accept_fault(
        loop,
        {**context, 'message': 'Task exception was never retrieved'},
    )
    assert not proxy.detects_windows_proactor_accept_fault(
        loop,
        {**context, 'exception': OSError(10048, 'address already in use')},
    )


def test_proxy_loop_diagnostics_capture_proactor_accept_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    logs: list[tuple[str, str]] = []
    delegated: list[tuple[asyncio.AbstractEventLoop, _ExceptionContext]] = []

    def record_log(category: str, message: str) -> None:
        logs.append((category, message))

    def previous_handler(
        active_loop: asyncio.AbstractEventLoop,
        context: _ExceptionContext,
    ) -> None:
        delegated.append((active_loop, context))

    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=record_log),
    )
    loop = FakeProactorEventLoop(previous_handler=previous_handler)
    proxy = _new_proxy_master()
    proxy.set_windows_selector_fallback_attempted(value=False)

    proxy.install_proxy_loop_diagnostics(loop, env_proxy_mode=True)
    error = WinAcceptError(14, 'bad address')
    context: _ExceptionContext = {
        'message': 'Accept failed on a socket',
        'exception': error,
        'socket': SimpleNamespace(getsockname=lambda: ('127.0.0.1', 58443)),
    }
    handler = loop.exception_handler
    assert handler is not None
    handler(loop, context)

    assert proxy.windows_proactor_accept_fault
    assert delegated == [(loop, context)]
    assert any('winerror=10014' in message for _, message in logs)


def test_hosts_proxy_readiness_requires_final_hosts_event() -> None:
    proxy = _new_proxy_master()
    proxy.set_hosts_proxy_ready(threading.Event())
    proxy.set_worker_thread(threading.Thread())

    assert not proxy.wait_for_hosts_proxy_ready(timeout=0.1)

    proxy.hosts_proxy_ready.set()
    assert proxy.wait_for_hosts_proxy_ready(timeout=0.1)


def test_proxy_worker_retries_once_with_selector_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    logs: list[tuple[str, str]] = []

    def fake_run(coro: Coroutine[object, object, object], **kwargs: object) -> None:
        coro.close()
        calls.append(kwargs)
        if len(calls) == 1:
            raise _retry_signal_type()

    async def fake_run_proxy() -> None:
        return None

    def record_log(category: str, message: str) -> None:
        logs.append((category, message))

    monkeypatch.setattr(proxy_master.asyncio, 'run', fake_run)
    monkeypatch.setattr(
        proxy_master,
        'log_buffer',
        SimpleNamespace(log=record_log),
    )
    proxy = _new_proxy_master()
    proxy.override_run_proxy(fake_run_proxy)
    proxy.set_running(value=True)

    proxy.run_proxy_worker()

    assert calls == [{}, {'loop_factory': asyncio.SelectorEventLoop}]
    assert proxy.windows_selector_fallback_attempted
    assert any('SelectorEventLoop' in message for _, message in logs)


def test_proxy_worker_cleans_exact_owned_linux_override_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleared: list[str] = []

    def fail_run(coro: Coroutine[object, object, object], **_kwargs: object) -> Never:
        coro.close()
        message = 'worker failed'
        raise RuntimeError(message)

    async def fake_run_proxy() -> None:
        return None

    def clear_override(*, client_key: str | None = None) -> bool:
        assert client_key is not None
        cleared.append(client_key)
        return True

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master.asyncio, 'run', fail_run)
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_discard_log))
    monkeypatch.setattr(
        'fleasion.utils.platform_linux.clear_linux_client_env_proxy_override',
        clear_override,
    )
    proxy = _new_proxy_master()
    proxy.override_run_proxy(fake_run_proxy)
    proxy.set_running(value=True)
    proxy.set_linux_env_proxy_override_client_key('sober')
    proxy.set_sober_env_proxy_override_active(value=True)

    proxy.run_proxy_worker()

    assert cleared == ['sober']
    assert proxy.linux_env_proxy_override_client_key is None
    assert proxy.sober_env_proxy_override_active is False


def test_proactor_accept_fault_cleanup_raises_retry_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    stopped: list[bool] = []
    loopbacks: list[tuple[str, ...] | list[str] | set[str] | None] = []

    def record_loopbacks(value: tuple[str, ...] | list[str] | set[str] | None) -> None:
        loopbacks.append(value)

    monkeypatch.setattr(
        proxy_master,
        '_set_active_hosts_loopbacks',
        record_loopbacks,
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_discard_log))
    proxy = _new_proxy_master()
    proxy.set_windows_proactor_accept_fault(value=True)
    proxy.set_windows_selector_fallback_attempted(value=False)
    proxy.set_loop(FakeProactorEventLoop())
    proxy.set_proxy(_StoppingProxy(stopped))
    proxy.set_active_proxy_port(58443)
    ready = threading.Event()
    ready.set()
    proxy.set_env_proxy_ready(ready)
    proxy.set_running(value=True)

    with pytest.raises(_retry_signal_type()):
        asyncio.run(proxy.raise_selector_retry_for_proactor_tls_failure())

    assert stopped == [True]
    assert proxy.proxy is None
    assert proxy.active_proxy_port is None
    assert not proxy.env_proxy_ready.is_set()
    assert not proxy.running
    assert loopbacks == [None]


def test_proactor_tls_timeout_does_not_retry_with_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    stopped: list[bool] = []

    proxy = _new_proxy_master()
    proxy.set_loop(FakeProactorEventLoop())
    proxy.set_windows_selector_fallback_attempted(value=False)
    proxy.set_windows_proactor_accept_fault(value=False)
    proxy.set_proxy(_StoppingProxy(stopped))

    asyncio.run(proxy.raise_selector_retry_for_proactor_tls_failure())

    assert stopped == []


def test_proactor_accept_fault_does_not_retry_twice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    proxy = _new_proxy_master()
    proxy.set_loop(FakeProactorEventLoop())
    proxy.set_windows_selector_fallback_attempted(value=True)
    proxy.set_windows_proactor_accept_fault(value=True)

    asyncio.run(proxy.raise_selector_retry_for_proactor_tls_failure())


def test_raw_tls_loopback_probe_bypasses_asyncio_transports(tmp_path: Path) -> None:
    ca_cert, ca_key = generate_ca(tmp_path)
    cert, key = generate_multi_host_cert(
        'default',
        {'assetdelivery.roblox.com'},
        ca_cert,
        ca_key,
        tmp_path,
    )

    ok, detail = _raw_tls_probe()(
        'assetdelivery.roblox.com',
        ca_cert,
        cert,
        key,
    )

    assert ok, detail
    assert 'protocol=TLSv1.2' in detail


@pytest.mark.skipif(not proxy_master.ssl.HAS_TLSv1_3, reason='TLS 1.3 is unavailable')
def test_raw_tls_loopback_probe_can_negotiate_tls13(tmp_path: Path) -> None:
    ca_cert, ca_key = generate_ca(tmp_path)
    cert, key = generate_multi_host_cert(
        'default',
        {'assetdelivery.roblox.com'},
        ca_cert,
        ca_key,
        tmp_path,
    )

    ok, detail = _raw_tls_probe()(
        'assetdelivery.roblox.com',
        ca_cert,
        cert,
        key,
        tls_max_version=proxy_master.ssl.TLSVersion.MAXIMUM_SUPPORTED,
    )

    assert ok, detail
    assert 'protocol=TLSv1.3' in detail


def test_proactor_tls_failure_retries_when_blocking_tls_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    stopped: list[bool] = []
    loopbacks: list[tuple[str, ...] | list[str] | set[str] | None] = []

    def record_loopbacks(value: tuple[str, ...] | list[str] | set[str] | None) -> None:
        loopbacks.append(value)

    monkeypatch.setattr(
        proxy_master,
        '_set_active_hosts_loopbacks',
        record_loopbacks,
    )
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_discard_log))
    proxy = _new_proxy_master()
    proxy.set_windows_proactor_accept_fault(value=False)
    proxy.set_windows_selector_fallback_attempted(value=False)
    proxy.set_loop(FakeProactorEventLoop())
    proxy.set_proxy(_StoppingProxy(stopped))
    proxy.set_active_proxy_port(58443)
    ready = threading.Event()
    ready.set()
    proxy.set_env_proxy_ready(ready)
    proxy.set_running(value=True)

    with pytest.raises(_retry_signal_type()):
        asyncio.run(proxy.raise_selector_retry_for_proactor_tls_failure(raw_tls_probe_ok=True))

    assert stopped == [True]
    assert proxy.proxy is None
    assert proxy.active_proxy_port is None
    assert not proxy.env_proxy_ready.is_set()
    assert not proxy.running
    assert loopbacks == [None]


def test_in_memory_tls_probe_bypasses_socket_layer(tmp_path: Path) -> None:
    ca_cert, ca_key = generate_ca(tmp_path)
    cert, key = generate_multi_host_cert(
        'default',
        {'assetdelivery.roblox.com'},
        ca_cert,
        ca_key,
        tmp_path,
    )

    ok, detail = _in_memory_tls_probe()(
        'assetdelivery.roblox.com',
        ca_cert,
        cert,
        key,
    )

    assert ok, detail
    assert 'protocol=TLSv1.2' in detail


def test_windows_startup_tls_relaxes_cap_before_switching_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    calls: list[tuple[set[str], bool, str, ssl.TLSVersion]] = []
    tls_changes: list[ssl.TLSVersion] = []
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_discard_log))

    async def fake_self_test(
        hosts: set[str],
        _ca_cert_path: Path,
        _port: int,
        explicit_proxy: bool,
        loopback_host: str,
        tls_max_version: ssl.TLSVersion,
    ) -> tuple[bool, list[str]]:
        calls.append((set(hosts), explicit_proxy, loopback_host, tls_max_version))
        if loopback_host == '127.0.0.1' and tls_max_version is ssl.TLSVersion.MAXIMUM_SUPPORTED:
            return True, []
        return False, ['timed out']

    monkeypatch.setattr(proxy_master, '_tls_self_test_result', fake_self_test)
    proxy = _new_proxy_master()
    proxy.set_proxy(_TlsRecordingProxy(tls_changes))
    proxy.set_env_proxy_loopback_host('127.0.0.1')

    ok = asyncio.run(
        proxy.run_startup_tls_self_test(
            {'assetdelivery.roblox.com', 'gamejoin.roblox.com'},
            Path('ca.crt'),
            58443,
            explicit_proxy=True,
        )
    )

    assert ok
    assert proxy.env_proxy_loopback_host == '127.0.0.1'
    assert proxy.active_local_tls_max_version is ssl.TLSVersion.MAXIMUM_SUPPORTED
    assert tls_changes == [ssl.TLSVersion.MAXIMUM_SUPPORTED]
    assert [call[2:] for call in calls] == [
        ('127.0.0.1', proxy_master.PROXY_TLS_MAX_VERSION),
        ('127.0.0.1', ssl.TLSVersion.MAXIMUM_SUPPORTED),
        ('127.0.0.1', ssl.TLSVersion.MAXIMUM_SUPPORTED),
    ]


def test_windows_env_proxy_startup_tls_can_switch_to_ipv6_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    tls_changes: list[ssl.TLSVersion] = []
    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=_discard_log))

    async def fake_self_test(
        _hosts: set[str],
        _ca_cert_path: Path,
        _port: int,
        _explicit_proxy: bool,
        loopback_host: str,
        tls_max_version: ssl.TLSVersion,
    ) -> tuple[bool, list[str]]:
        if loopback_host == '::1' and tls_max_version is proxy_master.PROXY_TLS_MAX_VERSION:
            return True, []
        return False, ['timed out']

    monkeypatch.setattr(proxy_master, '_tls_self_test_result', fake_self_test)
    proxy = _new_proxy_master()
    proxy.set_proxy(_TlsRecordingProxy(tls_changes))
    proxy.set_env_proxy_loopback_host('127.0.0.1')
    proxy.set_active_proxy_port(58443)

    ok = asyncio.run(
        proxy.run_startup_tls_self_test(
            {'assetdelivery.roblox.com', 'gamejoin.roblox.com'},
            Path('ca.crt'),
            58443,
            explicit_proxy=True,
        )
    )

    assert ok
    assert proxy.env_proxy_loopback_host == '::1'
    assert proxy.active_local_tls_max_version is proxy_master.PROXY_TLS_MAX_VERSION
    assert proxy.roblox_env_proxy_url() == 'http://[::1]:58443'
    assert tls_changes == [
        ssl.TLSVersion.MAXIMUM_SUPPORTED,
        proxy_master.PROXY_TLS_MAX_VERSION,
    ]
