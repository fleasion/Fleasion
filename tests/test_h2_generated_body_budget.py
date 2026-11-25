"""Keep HTTP feature-generated output within the HTTP/2 bridge's shared buffer budget."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Literal, cast

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


class _BlockedFeatureProxy:
    def __init__(self, direction: Literal['request', 'response'] = 'response') -> None:
        self.direction = direction
        self.first_started = asyncio.Event()
        self.release = asyncio.Event()
        self.started = 0

    async def serve_decrypted_http_session(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _host: str,
        upstream: UpstreamConnectResult,
    ) -> None:
        self.started += 1
        if self.direction == 'request':
            assert upstream.writer is not None
            upstream.writer.write(b'POST / HTTP/1.1\r\nContent-Length: 100\r\n\r\n' + b'X' * 100)
        else:
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n' + b'X' * 100)
        self.first_started.set()
        await self.release.wait()


@pytest.mark.parametrize('direction', ['request', 'response'])
def test_feature_output_from_two_streams_respects_shared_budget(
    monkeypatch: pytest.MonkeyPatch,
    direction: Literal['request', 'response'],
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 250)
        proxy = _BlockedFeatureProxy(direction=direction)
        downstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', downstream_writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback',
                endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(downstream_writer.take())
        headers = [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'apis.roblox.com'), (b':path', b'/synthetic'),
        ]
        peer.send_headers(1, headers, end_stream=True)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(proxy.first_started.wait(), 0.2)
        peer.send_headers(3, headers, end_stream=True)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        try:
            await asyncio.sleep(0.05)
            resets = [
                event.stream_id for event in peer.receive_data(downstream_writer.take())
                if isinstance(event, StreamReset)
            ]
            assert resets == [3]
            assert bridge.buffered_output_bytes <= 250
            assert bridge.buffered_output_bytes > 0
        finally:
            proxy.release.set()
            for task in list(bridge.tasks):
                task.cancel()
            await asyncio.gather(*bridge.tasks, return_exceptions=True)
            assert bridge.buffered_output_bytes == 0
            assert not bridge.requests

    asyncio.run(run())


def test_feature_message_reservation_survives_drain_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        started = asyncio.Event()
        release = asyncio.Event()
        held = 0

        def reserve(size: int) -> None:
            nonlocal held
            if held + size > 90:
                raise ValueError('HTTP/2 connection body buffer limit exceeded')
            held += size

        def unreserve(size: int) -> None:
            nonlocal held
            held -= size

        async def callback(_message: bytes) -> None:
            started.set()
            await release.wait()

        writer = h2_transport._MessageWriter(  # pyright: ignore[reportPrivateUsage]
            callback, reserve=reserve, release=unreserve
        )
        writer.write(b'A' * 70)
        assert held == 70
        drain_task = asyncio.create_task(writer.drain())
        await asyncio.wait_for(started.wait(), 0.2)
        writer.close()
        assert held == 70
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        assert held == 0
        writer.close()
        assert held == 0

    asyncio.run(run())


def test_feature_message_rejection_and_close_restore_exact_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', 90)
        held = 0

        def reserve(size: int) -> None:
            nonlocal held
            if held + size > 90:
                raise ValueError('HTTP/2 connection body buffer limit exceeded')
            held += size

        def unreserve(size: int) -> None:
            nonlocal held
            held -= size

        async def callback(_message: bytes) -> None:
            pass

        writer = h2_transport._MessageWriter(  # pyright: ignore[reportPrivateUsage]
            callback, reserve=reserve, release=unreserve
        )
        writer.write(memoryview(b'A' * 50))
        with pytest.raises(ValueError, match='HTTP feature message exceeds'):
            writer.write(b'B' * 60)
        # Non-byte memoryviews reserve their byte size rather than element count
        writer.write(memoryview(bytes([1, 0, 0, 0, 255, 255, 255, 255])).cast('I'))
        assert held == 58
        with pytest.raises(ValueError, match='connection body buffer limit exceeded'):
            reserve(50)
        assert held == 58
        await writer.drain()
        assert held == 0
        writer.write(b'A' * 50)
        await writer.wait_closed()
        assert held == 0
        await writer.wait_closed()
        assert held == 0

    asyncio.run(run())


def test_burst_of_sixty_four_generated_responses_recovers_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        message = b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n' + b'X' * 100
        monkeypatch.setattr(h2_transport, '_MAX_BUFFERED_BODY', len(message) * 8)
        proxy = _BlockedFeatureProxy()
        downstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', downstream_writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback',
                endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(downstream_writer.take())
        headers = [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'apis.roblox.com'), (b':path', b'/burst'),
        ]
        for _round in range(5):
            for _ in range(64):
                peer.send_headers(peer.get_next_available_stream_id(), headers, end_stream=True)
            await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
            await asyncio.sleep(0.04)
            assert bridge.buffered_output_bytes == len(message) * 8
            resets = [
                e.stream_id for e in peer.receive_data(downstream_writer.take())
                if isinstance(e, StreamReset)
            ]
            assert len(resets) == 56
            assert len(bridge.requests) == 8
            for task in list(bridge.tasks):
                task.cancel()
            await asyncio.gather(*bridge.tasks, return_exceptions=True)
            late = [
                e.stream_id for e in peer.receive_data(downstream_writer.take())
                if isinstance(e, StreamReset)
            ]
            assert len(late) == 8
            assert bridge.buffered_output_bytes == 0
            assert not bridge.requests
            assert bridge.buffered_request_bytes == 0
            assert bridge.buffered_response_bytes == 0
        peer.send_headers(peer.get_next_available_stream_id(), headers, end_stream=True)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0.01)
        assert bridge.buffered_output_bytes == len(message)
        for task in list(bridge.tasks):
            task.cancel()
        await asyncio.gather(*bridge.tasks, return_exceptions=True)
        assert bridge.buffered_output_bytes == 0

    asyncio.run(run())
