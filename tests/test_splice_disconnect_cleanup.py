"""An abandoned TLS splice must close promptly after transport EOF."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.splice import SpliceConnection
from fleasion.proxy.splice_keys import KeyProvider

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


class _MemoryWire:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    def is_closing(self) -> bool:
        return self.closed

    async def wait_closed(self) -> None:
        pass


class _IdleProxy:
    def mark_splice_decrypted(self, _host: str) -> bool:
        return False

    async def serve_spliced_session(self, *_args: object) -> None:
        await asyncio.Event().wait()


@pytest.mark.parametrize('closing_peer', ['client', 'upstream'])
def test_splice_transport_eof_does_not_wait_for_application_timeout(
    closing_peer: str,
) -> None:
    async def run() -> None:
        client_reader = asyncio.StreamReader()
        server_reader = asyncio.StreamReader()
        client_wire, server_wire = _MemoryWire(), _MemoryWire()
        splice = SpliceConnection(
            client_reader,
            cast('asyncio.StreamWriter', client_wire),
            server_reader,
            cast('asyncio.StreamWriter', server_wire),
            keys=KeyProvider(),
        )
        (client_reader if closing_peer == 'client' else server_reader).feed_eof()
        await asyncio.wait_for(splice.run(cast('FleasionProxy', _IdleProxy()), 'test.local'), 0.3)
        assert client_wire.closed
        assert server_wire.closed

    asyncio.run(run())


@pytest.mark.parametrize('closing_peer', ['client', 'upstream'])
@pytest.mark.parametrize('complete_http', [False, True])
def test_active_splice_ends_after_bounded_drain_or_completed_http(
    closing_peer: str, complete_http: bool,
) -> None:
    class _Proxy:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.finished = asyncio.Event()
            self.cancelled = asyncio.Event()

        def mark_splice_decrypted(self, _host: str) -> bool:
            return False

        async def serve_spliced_session(self, *_args: object) -> None:
            self.started.set()
            try:
                if complete_http:
                    await asyncio.sleep(0.02)
                    self.finished.set()
                else:
                    await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    async def run() -> None:
        proxy = _Proxy()
        client_reader = asyncio.StreamReader()
        server_reader = asyncio.StreamReader()
        client_wire, server_wire = _MemoryWire(), _MemoryWire()
        splice = SpliceConnection(
            client_reader,
            cast('asyncio.StreamWriter', client_wire),
            server_reader,
            cast('asyncio.StreamWriter', server_wire),
            keys=KeyProvider(),
        )
        splice._application_ready.set()  # pyright: ignore[reportPrivateUsage]
        task = asyncio.create_task(splice.run(cast('FleasionProxy', proxy), 'test.local'))
        await asyncio.wait_for(proxy.started.wait(), 0.2)
        (client_reader if closing_peer == 'client' else server_reader).feed_eof()
        await asyncio.wait_for(task, 1.5)
        assert proxy.cancelled.is_set()
        assert proxy.finished.is_set() == complete_http
        assert client_wire.closed and server_wire.closed

    asyncio.run(run())
