"""An oversized HTTP/2 stream must not terminate its neighboring streams."""

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


def _bridge() -> tuple[H2FeatureBridge, _MemoryWriter, _MemoryWriter]:
    client_writer = _MemoryWriter()
    upstream_writer = _MemoryWriter()
    bridge = H2FeatureBridge(
        cast('FleasionProxy', object()),
        'apis.roblox.com',
        asyncio.StreamReader(),
        cast('asyncio.StreamWriter', client_writer),
        UpstreamConnectResult(
            reader=asyncio.StreamReader(),
            writer=cast('asyncio.StreamWriter', upstream_writer),
            method='synthetic_loopback',
            endpoint='127.0.0.1',
        ),
    )
    return bridge, client_writer, upstream_writer


_HEADERS = [
    (b':method', b'POST'),
    (b':scheme', b'https'),
    (b':authority', b'apis.roblox.com'),
    (b':path', b'/buffer-test'),
]


def test_oversized_request_resets_only_offending_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        bridge, writer, _ = _bridge()
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        peer.send_headers(1, _HEADERS)
        peer.send_data(1, b'x' * 100, end_stream=True)
        peer.send_headers(3, _HEADERS)
        peer.send_data(3, b'valid', end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        events = peer.receive_data(writer.take())
        resets = [e.stream_id for e in events if isinstance(e, StreamReset)]
        assert resets == [1]
        assert set(bridge.requests) == {3}
        assert bytes(bridge.requests[3].body) == b'valid'
        assert bridge.buffered_request_bytes == len(b'valid')
        assert not bridge.client.terminated

    asyncio.run(run())


def test_oversized_upstream_response_resets_only_offending_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        bridge, _, writer = _bridge()
        peer = H2Connection(H2Configuration(client_side=False))
        await bridge.server.start()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        await bridge.server.receive(peer.data_to_send())
        peer.receive_data(writer.take())

        requests: list[h2_transport._Request] = []  # pyright: ignore[reportPrivateUsage]
        for stream in (1, 3):
            req = h2_transport._Request(_HEADERS)  # pyright: ignore[reportPrivateUsage]
            requests.append(req)

            def register(
                stream_id: int,
                request: h2_transport._Request = req,  # pyright: ignore[reportPrivateUsage]
                expected_stream: int = stream,
            ) -> None:
                assert stream_id == expected_stream
                request.upstream_id = stream_id
                bridge.upstream_requests[stream_id] = request

            await bridge.server.open_request(_HEADERS, b'', register)
        peer.receive_data(writer.take())
        peer.send_headers(1, [(b':status', b'200')])
        peer.send_data(1, b'X' * 100, end_stream=False)
        peer.send_headers(3, [(b':status', b'200')])
        peer.send_data(3, b'valid', end_stream=True)
        await bridge._receive_upstream(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        events = peer.receive_data(writer.take())
        assert [e.stream_id for e in events if isinstance(e, StreamReset)] == [1]
        assert isinstance(requests[0].upstream_reader.exception(), ConnectionError)
        assert requests[0].response_rejected
        assert not requests[0].response_body
        assert requests[1].upstream_ended
        forwarded = await requests[1].upstream_reader.read()
        assert forwarded.endswith(b'valid')
        assert bridge.buffered_response_bytes == 0
        assert not bridge.server.terminated

    asyncio.run(run())


def test_connection_budget_exhaustion_keeps_previously_buffered_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        bridge, writer, _ = _bridge()
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        peer.send_headers(1, _HEADERS)
        peer.send_data(1, b'a' * 70)
        peer.send_headers(3, _HEADERS)
        peer.send_data(3, b'b' * 30)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        events = peer.receive_data(writer.take())
        assert [e.stream_id for e in events if isinstance(e, StreamReset)] == [3]
        assert set(bridge.requests) == {1}
        assert bytes(bridge.requests[1].body) == b'a' * 70
        assert bridge.buffered_request_bytes == 70
        assert bridge.buffered_response_bytes == 0
        assert not bridge.client.terminated

    asyncio.run(run())
