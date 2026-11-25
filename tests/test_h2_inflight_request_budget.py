"""The HTTP/2 memory budget covers completed uploads awaiting feature processing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import StreamReset

from fleasion.proxy import h2_transport
from fleasion.proxy.h2_transport import H2FeatureBridge
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def take(self) -> bytes:
        data = bytes(self.data)
        self.data.clear()
        return data


class _WaitingProxy:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def serve_decrypted_http_session(
        self, _reader: asyncio.StreamReader, _writer: asyncio.StreamWriter,
        _host: str, _upstream: UpstreamConnectResult,
    ) -> None:
        self.started.set()
        await asyncio.Event().wait()


def test_completed_but_unprocessed_upload_keeps_aggregate_buffer_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        proxy = _WaitingProxy()
        writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com', asyncio.StreamReader(), cast('asyncio.StreamWriter', writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback', endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        headers = [
            (b':method', b'POST'), (b':scheme', b'https'),
            (b':authority', b'apis.roblox.com'), (b':path', b'/upload'),
        ]
        peer.send_headers(1, headers)
        peer.send_data(1, b'A' * 70, end_stream=True)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(proxy.started.wait(), 0.25)
        assert bridge.buffered_request_bytes == 70
        peer.send_headers(3, headers, end_stream=False)
        peer.send_data(3, b'B' * 30, end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        resets = [event.stream_id for event in peer.receive_data(writer.take())
                  if isinstance(event, StreamReset)]
        try:
            assert resets == [3]
            assert not bridge.client.terminated
        finally:
            for task in bridge.tasks:
                task.cancel()
            await asyncio.gather(*bridge.tasks, return_exceptions=True)
            assert bridge.buffered_request_bytes == 0

    asyncio.run(run())


def test_upstream_response_buffer_remains_reserved_until_feature_processing_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        upstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', object()), 'apis.roblox.com',
            asyncio.StreamReader(), cast('asyncio.StreamWriter', _MemoryWriter()),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', upstream_writer),
                method='controlled_loopback', endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection(H2Configuration(client_side=False))
        await bridge.server.start()
        peer.initiate_connection()
        peer.receive_data(upstream_writer.take())
        await bridge.server.receive(peer.data_to_send())
        peer.receive_data(upstream_writer.take())
        headers = [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'apis.roblox.com'), (b':path', b'/downstream'),
        ]
        request = h2_transport._Request(headers)  # pyright: ignore[reportPrivateUsage]

        def register(stream: int) -> None:
            request.upstream_id = stream
            bridge.upstream_requests[stream] = request

        await bridge.server.open_request(headers, b'', register)
        peer.receive_data(upstream_writer.take())
        peer.send_headers(1, [(b':status', b'200')])
        peer.send_data(1, b'R' * 70, end_stream=True)
        await bridge._receive_upstream(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert request.upstream_ended
        assert b'R' * 70 in await request.upstream_reader.read()
        assert bridge.buffered_response_bytes == 70
        with pytest.raises(ValueError, match='HTTP/2 connection body buffer limit exceeded'):
            bridge._reserve_body_bytes(30)  # pyright: ignore[reportPrivateUsage]
        bridge._release_response_body(request)  # pyright: ignore[reportPrivateUsage]
        assert bridge.buffered_response_bytes == 0

    asyncio.run(run())
