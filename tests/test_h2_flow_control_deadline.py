"""HTTP/2 slow consumers must not pin a stream's sender forever."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest
from h2.connection import H2Connection
from h2.events import DataReceived, StreamEnded, StreamReset

from fleasion.proxy import h2_transport
from fleasion.proxy.h2_transport import H2FeatureBridge, _Peer  # pyright: ignore[reportPrivateUsage]
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


async def _make_peer() -> tuple[_Peer, H2Connection, _MemoryWriter]:  # pyright: ignore[reportPrivateUsage]
    writer = _MemoryWriter()
    proxy = _Peer(cast('asyncio.StreamWriter', writer), client_side=False)
    client = H2Connection()
    await proxy.start()
    client.initiate_connection()
    client.receive_data(writer.take())
    client.send_headers(1, [(b':method', b'GET'), (b':scheme', b'https'),
                            (b':authority', b'local.test'), (b':path', b'/download')],
                        end_stream=True)
    await proxy.receive(client.data_to_send())
    client.receive_data(writer.take())
    return proxy, client, writer


def test_stalled_http2_outbound_window_expires_without_window_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_SEND_IDLE_TIMEOUT', 0.05, raising=False)
        proxy, client, writer = await _make_peer()
        with pytest.raises(TimeoutError, match='HTTP/2 flow-control window stalled'):
            await asyncio.wait_for(proxy.send(1, [(b':status', b'200')], b'Z' * 70000), 0.5)
        events = client.receive_data(writer.take())
        assert sum(len(e.data) for e in events if isinstance(e, DataReceived)) == 65535
        assert not any(isinstance(e, StreamEnded) for e in events)

    asyncio.run(run())


def test_http2_window_updates_allow_large_response_with_slow_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_SEND_IDLE_TIMEOUT', 0.09, raising=False)
        proxy, client, writer = await _make_peer()
        sender = asyncio.create_task(proxy.send(1, [(b':status', b'200')], b'Z' * 140000))
        received = 0
        ended = False
        for _ in range(20):
            await asyncio.sleep(0.025)
            events = client.receive_data(writer.take())
            for event in events:
                if isinstance(event, DataReceived):
                    received += len(event.data)
                    client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                elif isinstance(event, StreamEnded):
                    ended = True
            await proxy.receive(client.data_to_send())
            if ended:
                break
        await asyncio.wait_for(sender, 0.5)
        assert received == 140000
        assert ended

    asyncio.run(run())


def test_unrelated_http2_window_updates_do_not_extend_blocked_stream_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_SEND_IDLE_TIMEOUT', 0.07)
        proxy, client, writer = await _make_peer()
        client.send_headers(3, [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'local.test'), (b':path', b'/other'),
        ], end_stream=True)
        await proxy.receive(client.data_to_send())
        client.receive_data(writer.take())
        sender = asyncio.create_task(proxy.send(1, [(b':status', b'200')], b'Z' * 70000))
        await asyncio.sleep(0.01)
        assert not sender.done()
        for _ in range(10):
            if sender.done():
                break
            client.increment_flow_control_window(1, stream_id=3)
            await proxy.receive(client.data_to_send())
            await asyncio.sleep(0.015)
        with pytest.raises(TimeoutError, match='HTTP/2 flow-control window stalled'):
            await asyncio.wait_for(sender, 0.2)
        assert not proxy.terminated

    asyncio.run(run())


def test_stalled_stream_does_not_block_neighboring_http2_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_SEND_IDLE_TIMEOUT', 0.09)
        proxy, client, writer = await _make_peer()
        client.send_headers(3, [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'local.test'), (b':path', b'/neighbor'),
        ], end_stream=True)
        await proxy.receive(client.data_to_send())
        client.receive_data(writer.take())

        stalled = asyncio.create_task(proxy.send(1, [(b':status', b'200')], b'Z' * 70000))
        await asyncio.sleep(0.01)
        assert not stalled.done()
        initial = client.receive_data(writer.take())
        assert sum(len(e.data) for e in initial if isinstance(e, DataReceived)) == 65535
        # Replenish connection capacity, but leave stream 1's window exhausted
        client.increment_flow_control_window(70000)
        await proxy.receive(client.data_to_send())
        neighbor = b'neighbor response'
        await asyncio.wait_for(proxy.send(3, [(b':status', b'200')], neighbor), 0.3)
        neighbor_events = client.receive_data(writer.take())
        assert b''.join(
            e.data for e in neighbor_events
            if isinstance(e, DataReceived) and e.stream_id == 3
        ) == neighbor
        assert any(isinstance(e, StreamEnded) and e.stream_id == 3 for e in neighbor_events)
        with pytest.raises(TimeoutError, match='HTTP/2 flow-control window stalled'):
            await asyncio.wait_for(stalled, 0.2)
        assert not proxy.terminated

    asyncio.run(run())


def test_http2_feature_stream_is_reset_when_downstream_window_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _LargeResponseProxy:
        async def serve_decrypted_http_session(
            self,
            _reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
            _host: str,
            _upstream: UpstreamConnectResult,
        ) -> None:
            body = b'Z' * 70000
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 70000\r\n\r\n' + body)
            await writer.drain()

    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_H2_SEND_IDLE_TIMEOUT', 0.07)
        client_writer, upstream_writer = _MemoryWriter(), _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', _LargeResponseProxy()),
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
        await bridge.client.start()
        peer = H2Connection()
        peer.initiate_connection()
        peer.receive_data(client_writer.take())
        peer.send_headers(1, [
            (b':method', b'GET'), (b':scheme', b'https'),
            (b':authority', b'apis.roblox.com'), (b':path', b'/big'),
        ], end_stream=False)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        request = bridge.requests[1]
        await asyncio.wait_for(
            bridge._run_request(1, request), 0.5  # pyright: ignore[reportPrivateUsage]
        )
        events = peer.receive_data(client_writer.take())
        assert sum(len(e.data) for e in events if isinstance(e, DataReceived)) == 65535
        assert [e.stream_id for e in events if isinstance(e, StreamReset)] == [1]
        assert not any(isinstance(e, StreamEnded) for e in events)
        assert bridge.buffered_request_bytes == 0
        assert bridge.buffered_response_bytes == 0
        assert not bridge.requests
        assert not bridge.upstream_requests

    asyncio.run(run())
