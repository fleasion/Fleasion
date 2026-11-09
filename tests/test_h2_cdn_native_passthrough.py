"""Unmodified CDN responses must stream across the authenticated HTTP/2 bridge."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded, TrailersReceived

from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.addons.cache_scraper import CacheScraper

_HOST = 'contentdelivery.roblox.com'
_UpstreamHandler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


@asynccontextmanager
async def _h2_session(
    handler: _UpstreamHandler,
    *,
    replacement: Path | None = None,
    route: bool = False,
    cache_enabled: bool = False,
    cache_store: list[bytes] | None = None,
) -> AsyncGenerator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    TextureStripper.reset_routes()

    def mappings() -> tuple[dict[int, int], set[int], dict[int, str], dict[int, str]]:
        return {}, set(), {}, {45678: str(replacement)} if replacement else {}

    stripper = TextureStripper(
        cast('ConfigManager', SimpleNamespace(get_all_replacements=mappings))
    )
    if replacement is not None and route:
        routes = cast('dict[str, str]', getattr(TextureStripper, '_local_redirects'))
        routes[f'https://{_HOST}/asset'] = str(replacement)

    def scrape(_url: str, _path: str, data: bytes, _content_type: str) -> None:
        if cache_store is not None:
            cache_store.append(data)

    scraper = cast(
        'CacheScraper', SimpleNamespace(enabled=cache_enabled, process_cdn_response=scrape)
    )
    proxy = FleasionProxy(
        texture_stripper=stripper,
        cache_scraper=scraper,
        host_certs={},
        intercept_hosts={_HOST},
        tls_splice=True,
    )
    server = await asyncio.start_server(handler, '127.0.0.1', 0)
    server_port = server.sockets[0].getsockname()[1]
    tasks: set[asyncio.Task[None]] = set()

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        if current is not None:
            tasks.add(current)
        up_reader, up_writer = await asyncio.open_connection('127.0.0.1', server_port)
        try:
            await proxy.serve_decrypted_h2_session(
                reader, writer, _HOST,
                UpstreamConnectResult(
                    reader=up_reader, writer=up_writer, method='test', endpoint='loopback'
                ),
            )
        finally:
            writer.close()
            up_writer.close()
            await writer.wait_closed()
            await up_writer.wait_closed()
            if current is not None:
                tasks.discard(current)

    proxy_server = await asyncio.start_server(bridge, '127.0.0.1', 0)
    proxy_port = proxy_server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
        try:
            yield reader, writer
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        proxy_server.close()
        server.close()
        await proxy_server.wait_closed()
        await server.wait_closed()
        await proxy.stop()
        TextureStripper.reset_routes()


async def _request_stream(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    path: bytes = b'/asset',
    method: bytes = b'GET',
) -> tuple[H2Connection, int]:
    client = H2Connection()
    client.initiate_connection()
    stream = client.get_next_available_stream_id()
    client.send_headers(
        stream,
        [(b':method', method), (b':scheme', b'https'),
         (b':authority', _HOST.encode()), (b':path', path)],
        end_stream=True,
    )
    writer.write(client.data_to_send())
    await writer.drain()
    return client, stream


async def _get_events(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, client: H2Connection
) -> list[object]:
    packet = await asyncio.wait_for(reader.read(65536), timeout=2)
    assert packet, 'Proxy closed before a complete HTTP/2 response'
    events: list[object] = list(client.receive_data(packet))
    for event in events:
        if isinstance(event, DataReceived):
            client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
    writer.write(client.data_to_send())
    await writer.drain()
    return events


def test_untouched_h2_cdn_delivers_data_before_upstream_end() -> None:
    async def run() -> None:
        finish = asyncio.Event()
        request_paths: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, RequestReceived):
                            request_paths.append(dict(event.headers)[b':path'])
                        if isinstance(event, StreamEnded):
                            conn.send_headers(event.stream_id, [(b':status', b'200')])
                            conn.send_data(event.stream_id, b'FIRST-')
                            writer.write(conn.data_to_send())
                            await writer.drain()
                            await finish.wait()
                            conn.send_data(event.stream_id, b'SECOND')
                            conn.send_headers(
                                event.stream_id, [(b'x-integrity', b'ok')], end_stream=True
                            )
                    writer.write(conn.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(upstream) as (reader, writer):
            client, stream = await _request_stream(reader, writer)
            first = bytearray()
            first_events: list[object] = []
            while not first:
                events = await _get_events(reader, writer, client)
                first_events.extend(events)
                first.extend(b''.join(e.data for e in events if isinstance(e, DataReceived)))
            assert first == b'FIRST-'
            assert any(isinstance(e, ResponseReceived) for e in first_events)
            assert not any(isinstance(e, StreamEnded) for e in first_events)
            finish.set()
            rest = bytearray()
            trailers: list[tuple[bytes, bytes]] = []
            ended = False
            while not ended:
                events = await _get_events(reader, writer, client)
                for event in events:
                    if isinstance(event, DataReceived):
                        rest.extend(event.data)
                    elif isinstance(event, TrailersReceived):
                        trailers = list(event.headers)
                    elif isinstance(event, StreamEnded):
                        assert event.stream_id == stream
                        ended = True
            assert rest == b'SECOND'
            assert trailers == [(b'x-integrity', b'ok')]
            assert request_paths == [b'/asset']

    asyncio.run(run())


@pytest.mark.parametrize('matched', [False, True])
def test_h2_cdn_with_other_active_replacements_still_streams_unmatched(
    tmp_path: Path, matched: bool
) -> None:
    async def run() -> None:
        replacement = tmp_path / 'replacement.mesh'
        replacement.write_bytes(b'REPLACEMENT')
        request_paths: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, RequestReceived):
                            request_paths.append(dict(event.headers)[b':path'])
                        if isinstance(event, StreamEnded):
                            conn.send_headers(event.stream_id, [(b':status', b'200')])
                            conn.send_data(event.stream_id, b'ORIGINAL', end_stream=True)
                    writer.write(conn.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(
            upstream, replacement=replacement, route=matched
        ) as (reader, writer):
            client, _stream = await _request_stream(reader, writer)
            body = bytearray()
            completed = False
            while not completed:
                for event in await _get_events(reader, writer, client):
                    if isinstance(event, DataReceived):
                        body.extend(event.data)
                    elif isinstance(event, StreamEnded):
                        completed = True
            assert body == (b'REPLACEMENT' if matched else b'ORIGINAL')
            assert request_paths == ([] if matched else [b'/asset'])

    asyncio.run(run())


def test_concurrent_large_cdn_streams_respect_http2_flow_control() -> None:
    """Large concurrent streams must progress without holding complete bodies."""
    async def run() -> None:
        from h2.events import WindowUpdated

        payload_size = 1024 * 1024
        received_requests: set[int] = set()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            remaining: dict[int, int] = {}
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, StreamEnded):
                            received_requests.add(event.stream_id)
                            conn.send_headers(event.stream_id, [(b':status', b'200')])
                            remaining[event.stream_id] = payload_size
                        elif isinstance(event, WindowUpdated):
                            pass
                    for stream, amount in tuple(remaining.items()):
                        # The next read receives WINDOW_UPDATE when flow-control
                        # capacity is exhausted, allowing both streams to progress
                        while amount > 0:
                            can_send = min(
                                amount,
                                conn.max_outbound_frame_size,
                                conn.local_flow_control_window(stream),
                            )
                            if not can_send:
                                break
                            amount -= can_send
                            conn.send_data(
                                stream,
                                b'Z' * can_send,
                                end_stream=(amount == 0),
                            )
                        if amount:
                            remaining[stream] = amount
                        else:
                            del remaining[stream]
                    writer.write(conn.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(upstream) as (reader, writer):
            client = H2Connection()
            client.initiate_connection()
            for stream in (1, 3):
                client.send_headers(
                    stream,
                    [
                        (b':method', b'GET'),
                        (b':scheme', b'https'),
                        (b':authority', _HOST.encode()),
                        (b':path', f'/texture/{stream}'.encode()),
                    ],
                    end_stream=True,
                )
            writer.write(client.data_to_send())
            await writer.drain()
            sizes = {1: 0, 3: 0}
            completed: set[int] = set()
            while len(completed) < 2:
                for event in await _get_events(reader, writer, client):
                    if isinstance(event, DataReceived):
                        assert event.data == b'Z' * len(event.data)
                        sizes[event.stream_id] += len(event.data)
                    elif isinstance(event, StreamEnded):
                        completed.add(event.stream_id)
            assert sizes == {1: payload_size, 3: payload_size}
            assert len(received_requests) == 2

    asyncio.run(asyncio.wait_for(run(), timeout=12))


def test_h2_peer_advertises_large_receive_windows() -> None:
    """Each incoming stream and the aggregate connection need sufficient credit."""
    from fleasion.proxy import h2_transport
    from fleasion.proxy.h2_transport import _Peer  # pyright: ignore[reportPrivateUsage]

    class _Wire:
        def __init__(self) -> None:
            self.data = bytearray()

        def write(self, payload: bytes) -> None:
            self.data.extend(payload)

        async def drain(self) -> None:
            return

    async def run() -> None:
        wire = _Wire()
        peer = _Peer(cast('asyncio.StreamWriter', wire), client_side=False)
        await peer.start()
        client = H2Connection()
        client.initiate_connection()
        client.receive_data(bytes(wire.data))
        assert client.outbound_flow_control_window == h2_transport._H2_CONNECTION_RECV_WINDOW  # pyright: ignore[reportPrivateUsage]
        assert client.remote_settings.initial_window_size == h2_transport._H2_STREAM_RECV_WINDOW  # pyright: ignore[reportPrivateUsage]

    asyncio.run(run())


def test_blocked_cdn_stream_does_not_block_unrelated_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One slow downstream writer must not hold the shared upstream receive loop."""
    from fleasion.proxy.h2_transport import _Peer  # pyright: ignore[reportPrivateUsage]

    async def run() -> None:
        blocked = asyncio.Event()
        unblock = asyncio.Event()
        original_send = _Peer.send_response_data
        original_ack = _Peer.acknowledge
        upstream_acknowledged: set[int] = set()

        async def observe_ack(self: _Peer, stream: int, size: int) -> None:
            if self.h2.config.client_side:
                upstream_acknowledged.add(stream)
            await original_ack(self, stream, size)

        monkeypatch.setattr(_Peer, 'acknowledge', observe_ack)

        async def slow_send(self: _Peer, stream: int, data: bytes) -> None:
            if stream == 1:
                blocked.set()
                await unblock.wait()
            await original_send(self, stream, data)

        monkeypatch.setattr(_Peer, 'send_response_data', slow_send)

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            received: set[int] = set()
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, StreamEnded):
                            received.add(event.stream_id)
                    if received == {1, 3}:
                        for stream, body in ((1, b'SLOW'), (3, b'FAST')):
                            conn.send_headers(stream, [(b':status', b'200')])
                            conn.send_data(stream, body, end_stream=True)
                        writer.write(conn.data_to_send())
                        await writer.drain()
                        received.clear()
                    else:
                        writer.write(conn.data_to_send())
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(upstream) as (reader, writer):
            client = H2Connection()
            client.initiate_connection()
            for stream in (1, 3):
                client.send_headers(
                    stream,
                    [(b':method', b'GET'), (b':scheme', b'https'),
                     (b':authority', _HOST.encode()),
                     (b':path', f'/asset/{stream}'.encode())],
                    end_stream=True,
                )
            writer.write(client.data_to_send())
            await writer.drain()
            await asyncio.wait_for(blocked.wait(), timeout=2)

            bodies = {1: bytearray(), 3: bytearray()}
            ended: set[int] = set()
            while 3 not in ended:
                for event in await _get_events(reader, writer, client):
                    if isinstance(event, DataReceived):
                        bodies[event.stream_id].extend(event.data)
                    elif isinstance(event, StreamEnded):
                        ended.add(event.stream_id)
            assert bodies[3] == b'FAST'
            assert bodies[1] == b''
            assert 1 not in ended
            # A paused downstream must not grant more receive window credit
            # to the upstream until its bytes have been delivered
            assert 3 in upstream_acknowledged
            assert 1 not in upstream_acknowledged
            unblock.set()
            while 1 not in ended:
                for event in await _get_events(reader, writer, client):
                    if isinstance(event, DataReceived):
                        bodies[event.stream_id].extend(event.data)
                    elif isinstance(event, StreamEnded):
                        ended.add(event.stream_id)
            assert bodies[1] == b'SLOW'
            assert 1 in upstream_acknowledged

    asyncio.run(asyncio.wait_for(run(), timeout=5))


def test_abusive_h2_direct_stream_queue_overflow_is_isolated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A burst of tiny DATA frames cannot grow an unbounded per-stream queue."""
    from h2.events import StreamReset

    from fleasion.proxy import h2_transport

    async def run() -> None:
        monkeypatch.setattr(h2_transport, '_MAX_PENDING_DIRECT_EVENTS', 4)

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            received: set[int] = set()
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, StreamEnded):
                            received.add(event.stream_id)
                    if received == {1, 3}:
                        conn.send_headers(1, [(b':status', b'200')])
                        for _ in range(24):
                            conn.send_data(1, b'x')
                        conn.send_headers(3, [(b':status', b'200')])
                        conn.send_data(3, b'FAST', end_stream=True)
                        writer.write(conn.data_to_send())
                        await writer.drain()
                        received.clear()
                    else:
                        writer.write(conn.data_to_send())
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(upstream) as (reader, writer):
            client = H2Connection()
            client.initiate_connection()
            for stream in (1, 3):
                client.send_headers(
                    stream,
                    [(b':method', b'GET'), (b':scheme', b'https'),
                     (b':authority', _HOST.encode()),
                     (b':path', f'/asset/{stream}'.encode())],
                    end_stream=True,
                )
            writer.write(client.data_to_send())
            await writer.drain()
            finished: set[int] = set()
            reset: set[int] = set()
            body = bytearray()
            while 3 not in finished or 1 not in reset:
                for event in await _get_events(reader, writer, client):
                    if isinstance(event, DataReceived) and event.stream_id == 3:
                        body.extend(event.data)
                    elif isinstance(event, StreamEnded):
                        finished.add(event.stream_id)
                    elif isinstance(event, StreamReset):
                        reset.add(event.stream_id)
            assert body == b'FAST'
            assert finished == {3}
            assert reset == {1}

    asyncio.run(asyncio.wait_for(run(), timeout=5))


def test_reset_upstream_stream_aborts_blocked_downstream_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream RST_STREAM must cancel a blocked downstream send promptly."""
    from h2.events import StreamReset

    from fleasion.proxy.h2_transport import _Peer  # pyright: ignore[reportPrivateUsage]

    async def run() -> None:
        blocked = asyncio.Event()
        release = asyncio.Event()
        original_send = _Peer.send_response_data

        async def slow_send(self: _Peer, stream: int, data: bytes) -> None:
            if stream == 1:
                blocked.set()
                await release.wait()
            await original_send(self, stream, data)

        monkeypatch.setattr(_Peer, 'send_response_data', slow_send)

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            conn = H2Connection(H2Configuration(client_side=False))
            conn.initiate_connection()
            writer.write(conn.data_to_send())
            await writer.drain()
            received: set[int] = set()
            try:
                while data := await reader.read(65536):
                    for event in conn.receive_data(data):
                        if isinstance(event, StreamEnded):
                            received.add(event.stream_id)
                    if received == {1, 3}:
                        conn.send_headers(1, [(b':status', b'200')])
                        conn.send_data(1, b'SLOW')
                        writer.write(conn.data_to_send())
                        await writer.drain()
                        await asyncio.wait_for(blocked.wait(), timeout=2)
                        conn.reset_stream(1, error_code=8)
                        conn.send_headers(3, [(b':status', b'200')])
                        conn.send_data(3, b'FAST', end_stream=True)
                        writer.write(conn.data_to_send())
                        await writer.drain()
                        received.clear()
                    else:
                        writer.write(conn.data_to_send())
                        await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _h2_session(upstream) as (reader, writer):
            client = H2Connection()
            client.initiate_connection()
            for stream in (1, 3):
                client.send_headers(
                    stream,
                    [(b':method', b'GET'), (b':scheme', b'https'),
                     (b':authority', _HOST.encode()),
                     (b':path', f'/asset/{stream}'.encode())],
                    end_stream=True,
                )
            writer.write(client.data_to_send())
            await writer.drain()
            ended: set[int] = set()
            resets: set[int] = set()
            body = bytearray()
            try:
                while 3 not in ended or 1 not in resets:
                    for event in await _get_events(reader, writer, client):
                        if isinstance(event, StreamEnded):
                            ended.add(event.stream_id)
                        elif isinstance(event, StreamReset):
                            resets.add(event.stream_id)
                        elif isinstance(event, DataReceived) and event.stream_id == 3:
                            body.extend(event.data)
                assert body == b'FAST'
                assert resets == {1}
                assert ended == {3}
            finally:
                release.set()

    asyncio.run(asyncio.wait_for(run(), timeout=5))
