"""Abandoned HTTP/2 request uploads must release their stream reservations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
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


async def _bridge() -> tuple[H2FeatureBridge, H2Connection, _MemoryWriter]:
    writer = _MemoryWriter()
    bridge = H2FeatureBridge(
        cast('FleasionProxy', object()),
        'apis.roblox.com',
        asyncio.StreamReader(),
        cast('asyncio.StreamWriter', writer),
        UpstreamConnectResult(
            reader=asyncio.StreamReader(),
            writer=cast('asyncio.StreamWriter', _MemoryWriter()),
            method='synthetic',
            endpoint='127.0.0.1',
        ),
    )
    peer = H2Connection()
    await bridge.client.start()
    peer.initiate_connection()
    peer.receive_data(writer.take())
    return bridge, peer, writer


def _headers() -> list[tuple[bytes, bytes]]:
    return [
        (b':method', b'POST'), (b':scheme', b'https'),
        (b':authority', b'apis.roblox.com'), (b':path', b'/upload'),
    ]


def test_abandoned_http2_upload_expires_and_frees_buffer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_UPLOAD_IDLE_TIMEOUT', 0.05, raising=False)
        bridge, peer, writer = await _bridge()
        peer.send_headers(1, _headers(), end_stream=False)
        peer.send_data(1, b'PARTIAL', end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert bridge.buffered_request_bytes == 7
        await asyncio.sleep(0.09)
        assert not bridge.requests
        assert bridge.buffered_request_bytes == 0
        assert [event.stream_id for event in peer.receive_data(writer.take())
                if isinstance(event, StreamReset)] == [1]
        assert not bridge.client.terminated

    asyncio.run(run())


def test_active_http2_upload_progress_prevents_premature_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_UPLOAD_IDLE_TIMEOUT', 0.08, raising=False)
        bridge, peer, writer = await _bridge()
        peer.send_headers(1, _headers(), end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        for _ in range(3):
            await asyncio.sleep(0.035)
            peer.send_data(1, b'X', end_stream=False)
            await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert list(bridge.requests) == [1]
        assert bridge.buffered_request_bytes == 3
        assert not any(isinstance(e, StreamReset) for e in peer.receive_data(writer.take()))
        await asyncio.sleep(0.095)
        assert not bridge.requests
        assert bridge.buffered_request_bytes == 0

    asyncio.run(run())


def test_expiring_many_abandoned_uploads_restores_stream_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_UPLOAD_IDLE_TIMEOUT', 0.055)
        bridge, peer, writer = await _bridge()
        expired: list[int] = []
        for _ in range(64):
            stream = peer.get_next_available_stream_id()
            expired.append(stream)
            peer.send_headers(stream, _headers(), end_stream=False)
            peer.send_data(stream, b'Z', end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert len(bridge.requests) == 64
        assert bridge.buffered_request_bytes == 64
        await asyncio.sleep(0.12)
        events = peer.receive_data(writer.take())
        assert [event.stream_id for event in events
                if isinstance(event, StreamReset)] == expired
        assert not bridge.requests
        assert bridge.buffered_request_bytes == 0
        fresh = peer.get_next_available_stream_id()
        peer.send_headers(fresh, _headers(), end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert list(bridge.requests) == [fresh]
        bridge._cancel_upload_timeout(bridge.requests[fresh])  # pyright: ignore[reportPrivateUsage]

    asyncio.run(run())


def test_peer_reset_cancels_upload_timeout_without_later_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_UPLOAD_IDLE_TIMEOUT', 0.05)
        bridge, peer, writer = await _bridge()
        peer.send_headers(1, _headers(), end_stream=False)
        peer.send_data(1, b'X')
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert bridge.requests[1].upload_timer is not None
        peer.reset_stream(1, error_code=8)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        assert not bridge.requests
        assert bridge.buffered_request_bytes == 0
        await asyncio.sleep(0.09)
        assert not any(isinstance(event, StreamReset) for event in peer.receive_data(writer.take()))

    asyncio.run(run())
