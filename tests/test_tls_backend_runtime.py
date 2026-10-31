"""The optional backend entry point fails closed on unavailable/slow providers."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from fleasion.proxy.tls_backends import (
    AuthenticatedSession,
    BackendId,
    BackendProbe,
    BackendStatus,
    BackendUnavailableError,
    create_backend,
)
from fleasion.proxy.tls_backends.runtime import (
    acquire_verified_session,
    run_managed_backend_session,
)
from fleasion.proxy.upstream import UpstreamConnectResult


class _TrackingWriter:
    def __init__(self) -> None:
        self.close_calls = 0
        self.wait_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    async def wait_closed(self) -> None:
        self.wait_calls += 1


class _FakeProvider:
    backend_id = BackendId.WINDOWS

    def __init__(self, *, delay: float = 0, authenticated: bool = True) -> None:
        self.delay = delay
        self.authenticated = authenticated
        self.open_calls = 0
        self.client_writer = _TrackingWriter()
        self.upstream_writer = _TrackingWriter()

    def probe(self) -> BackendProbe:
        return BackendProbe(self.backend_id, BackendStatus.READY, "Controlled fixture")

    async def open_session(self, hostname: str) -> AuthenticatedSession:
        self.open_calls += 1
        await asyncio.sleep(self.delay)
        return AuthenticatedSession(
            hostname=hostname,
            alpn="h2",
            client_reader=asyncio.StreamReader(),
            client_writer=cast('asyncio.StreamWriter', self.client_writer),
            upstream=UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', self.upstream_writer),
                method="synthetic_local_test",
                endpoint="localhost",
            ),
            server_authenticated=self.authenticated,
        )


def test_unavailable_provider_is_not_started() -> None:
    async def run() -> None:
        backend = create_backend(BackendId.WINDOWS)
        with pytest.raises(BackendUnavailableError, match="not_implemented"):
            await acquire_verified_session(backend, "apis.roblox.com")

    asyncio.run(run())


def test_ready_fixture_can_open_verified_synthetic_session() -> None:
    async def run() -> None:
        backend = _FakeProvider()
        result = await acquire_verified_session(backend, "apis.roblox.com")
        assert result.server_authenticated
        assert backend.open_calls == 1

    asyncio.run(run())


def test_backend_open_times_out_without_fallback() -> None:
    async def run() -> None:
        backend = _FakeProvider(delay=0.5)
        with pytest.raises(BackendUnavailableError, match="timed out"):
            await acquire_verified_session(backend, "apis.roblox.com", timeout_seconds=0.01)
        assert backend.open_calls == 1

    asyncio.run(run())


def test_backend_cannot_return_unauthenticated_session() -> None:
    async def run() -> None:
        backend = _FakeProvider(authenticated=False)
        with pytest.raises(BackendUnavailableError, match="authentication"):
            await acquire_verified_session(backend, "apis.roblox.com")
        assert backend.client_writer.close_calls == 1
        assert backend.client_writer.wait_calls == 1
        assert backend.upstream_writer.close_calls == 1
        assert backend.upstream_writer.wait_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize("timeout", [0, -1, 61])
def test_bad_acquisition_timeout_rejected(timeout: float) -> None:
    async def run() -> None:
        with pytest.raises(ValueError, match="timeout"):
            await acquire_verified_session(
                _FakeProvider(), "apis.roblox.com", timeout_seconds=timeout
            )

    asyncio.run(run())


def test_verified_session_transfers_writer_ownership_to_caller() -> None:
    async def run() -> None:
        backend = _FakeProvider()
        await acquire_verified_session(backend, 'apis.roblox.com')
        assert backend.client_writer.close_calls == 0
        assert backend.upstream_writer.close_calls == 0

    asyncio.run(run())


class _RecordingProxy:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str]] = []

    async def serve_decrypted_h2_session(
        self,
        _reader: asyncio.StreamReader,
        _writer: asyncio.StreamWriter,
        hostname: str,
        _upstream: UpstreamConnectResult,
        *,
        initial_client_data: bytes = b'',
    ) -> None:
        assert initial_client_data == b''
        self.calls.append(('h2', hostname))
        if self.fail:
            msg = 'Synthetic peer disconnected'
            raise ConnectionError(msg)


@pytest.mark.parametrize('fails', [False, True])
def test_managed_dispatch_always_closes_both_stream_pairs(fails: bool) -> None:
    from fleasion.proxy.server import FleasionProxy

    async def run() -> None:
        backend = _FakeProvider()
        proxy = _RecordingProxy(fail=fails)
        if fails:
            with pytest.raises(ConnectionError, match='disconnected'):
                await run_managed_backend_session(
                    cast('FleasionProxy', proxy), backend, 'apis.roblox.com'
                )
        else:
            await run_managed_backend_session(
                cast('FleasionProxy', proxy), backend, 'apis.roblox.com'
            )
        assert backend.open_calls == 1
        assert proxy.calls == [('h2', 'apis.roblox.com')]
        assert backend.client_writer.close_calls == 1
        assert backend.client_writer.wait_calls == 1
        assert backend.upstream_writer.close_calls == 1
        assert backend.upstream_writer.wait_calls == 1

    asyncio.run(run())


class _StalledCloseWriter(_TrackingWriter):
    async def wait_closed(self) -> None:
        self.wait_calls += 1
        await asyncio.Event().wait()


def test_rejected_session_close_does_not_wait_forever() -> None:
    async def run() -> None:
        backend = _FakeProvider(authenticated=False)
        backend.upstream_writer = _StalledCloseWriter()
        with pytest.raises(BackendUnavailableError, match='authentication'):
            await asyncio.wait_for(
                acquire_verified_session(backend, 'apis.roblox.com'), timeout=0.8
            )
        assert backend.client_writer.close_calls == 1
        assert backend.upstream_writer.close_calls == 1
        assert backend.upstream_writer.wait_calls == 1

    asyncio.run(run())


def test_cancelled_managed_session_closes_both_writers() -> None:
    from fleasion.proxy.server import FleasionProxy

    class _WaitingDispatch:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def serve_decrypted_h2_session(
            self,
            _reader: asyncio.StreamReader,
            _writer: asyncio.StreamWriter,
            _host: str,
            _upstream: UpstreamConnectResult,
            *,
            initial_client_data: bytes = b'',
        ) -> None:
            assert not initial_client_data
            self.started.set()
            await asyncio.Event().wait()

    async def run() -> None:
        backend = _FakeProvider()
        proxy = _WaitingDispatch()
        task = asyncio.create_task(
            run_managed_backend_session(
                cast('FleasionProxy', proxy), backend, 'apis.roblox.com'
            )
        )
        await asyncio.wait_for(proxy.started.wait(), timeout=0.6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.6)
        assert backend.client_writer.close_calls == 1
        assert backend.upstream_writer.close_calls == 1

    asyncio.run(run())
