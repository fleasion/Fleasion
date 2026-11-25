"""Measure an HTTP/2 CDN bridge against a local synthetic upstream."""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
import time
from typing import TYPE_CHECKING, cast

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded

from fleasion.proxy.h2_transport import H2FeatureBridge
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy

log = logging.getLogger(__name__)
_HOST = 'fts.rbxcdn.com'
_PAYLOAD = b'x' * 1024


class _Stripper:
    @staticmethod
    def has_cdn_route(_host: str, _path: str) -> bool:
        return False

    @staticmethod
    def has_pending() -> bool:
        return False


class _Proxy:
    texture_stripper = _Stripper()

    def __init__(self, *, buffered: bool, payload: bytes) -> None:
        self._buffered = buffered
        self._response = (
            b'HTTP/1.1 200 OK\r\nContent-Length: '
            + str(len(payload)).encode('ascii')
            + b'\r\n\r\n'
            + payload
        )

    def may_stream_untouched_cdn(self) -> bool:
        return not self._buffered

    async def serve_decrypted_http_session(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _host: str,
        _upstream: UpstreamConnectResult,
    ) -> None:
        writer.write(self._response)
        await writer.drain()


async def _origin(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = H2Connection(H2Configuration(client_side=False))
    peer.initiate_connection()
    writer.write(peer.data_to_send())
    await writer.drain()
    try:
        while data := await reader.read(65536):
            for event in peer.receive_data(data):
                if isinstance(event, RequestReceived):
                    peer.send_headers(
                        event.stream_id, [(b':status', b'200'), (b'content-length', b'1024')]
                    )
                    peer.send_data(event.stream_id, _PAYLOAD, end_stream=True)
            writer.write(peer.data_to_send())
            await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def _server_port(server: asyncio.Server) -> int:
    sockets = server.sockets
    if not sockets:
        msg = 'Loopback server has no listening socket'
        raise RuntimeError(msg)
    address = cast('tuple[str, int]', sockets[0].getsockname())
    return address[1]


async def _request(
    client: H2Connection,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    index: int,
    expected: bytes,
) -> None:
    stream = client.get_next_available_stream_id()
    client.send_headers(
        stream,
        [
            (b':method', b'GET'),
            (b':scheme', b'https'),
            (b':authority', _HOST.encode('ascii')),
            (b':path', f'/asset/{index}'.encode('ascii')),
        ],
        end_stream=True,
    )
    writer.write(client.data_to_send())
    await writer.drain()
    body = bytearray()
    status: bytes | None = None
    complete = False
    while not complete:
        packet = await asyncio.wait_for(reader.read(65536), timeout=5)
        if not packet:
            msg = 'Proxy closed before the response completed'
            raise ConnectionError(msg)
        for event in client.receive_data(packet):
            if isinstance(event, ResponseReceived):
                status = dict(event.headers).get(b':status')
            elif isinstance(event, DataReceived):
                body.extend(event.data)
                client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, StreamEnded):
                complete = True
        writer.write(client.data_to_send())
        await writer.drain()
    if status != b'200' or body != expected:  # pyright: ignore[reportUnnecessaryComparison]
        msg = 'Loopback response differed from the upstream payload'
        raise ValueError(msg)


async def _request_group(
    client: H2Connection,
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    start: int,
    count: int,
    expected: bytes,
) -> None:
    bodies: dict[int, bytearray] = {}
    statuses: dict[int, bytes] = {}
    for index in range(start, start + count):
        stream = client.get_next_available_stream_id()
        bodies[stream] = bytearray()
        client.send_headers(
            stream,
            [
                (b':method', b'GET'),
                (b':scheme', b'https'),
                (b':authority', _HOST.encode('ascii')),
                (b':path', f'/asset/{index}'.encode('ascii')),
            ],
            end_stream=True,
        )
    writer.write(client.data_to_send())
    await writer.drain()
    completed = 0
    while completed < count:
        packet = await asyncio.wait_for(reader.read(65536), timeout=5)
        if not packet:
            msg = 'Proxy closed before the responses completed'
            raise ConnectionError(msg)
        for event in client.receive_data(packet):
            if isinstance(event, ResponseReceived):
                statuses[event.stream_id] = dict(event.headers)[b':status']
            elif isinstance(event, DataReceived):
                bodies[event.stream_id].extend(event.data)
                client.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
            elif isinstance(event, StreamEnded):
                if (
                    statuses.get(event.stream_id) != b'200'
                    or bytes(bodies[event.stream_id]) != expected
                ):
                    msg = 'Loopback response differed from the upstream payload'
                    raise ValueError(msg)
                completed += 1
        writer.write(client.data_to_send())
        await writer.drain()


async def _run(requests: int, *, buffered: bool, parallel: int) -> None:
    payload = b'x' * (1024 * 1024) if buffered else _PAYLOAD
    origin = await asyncio.start_server(_origin, '127.0.0.1', 0)
    origin_port = _server_port(origin)
    tasks: set[asyncio.Task[None]] = set()

    async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        current = asyncio.current_task()
        if current is None:
            msg = 'Loopback bridge is not running in a task'
            raise RuntimeError(msg)
        tasks.add(current)
        upstream_reader, upstream_writer = await asyncio.open_connection('127.0.0.1', origin_port)
        try:
            upstream = UpstreamConnectResult(
                reader=upstream_reader, writer=upstream_writer, method='local', endpoint='local'
            )
            await H2FeatureBridge(
                cast('FleasionProxy', _Proxy(buffered=buffered, payload=payload)),
                _HOST,
                reader,
                writer,
                upstream,
            ).run()
        finally:
            writer.close()
            upstream_writer.close()
            await writer.wait_closed()
            await upstream_writer.wait_closed()
            tasks.discard(current)

    proxy = await asyncio.start_server(bridge, '127.0.0.1', 0)
    proxy_port = _server_port(proxy)
    reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
    client = H2Connection()
    client.initiate_connection()
    writer.write(client.data_to_send())
    await writer.drain()
    samples: list[tuple[float, float]] = []
    try:
        for _ in range(5):
            start_wall, start_cpu = time.perf_counter(), time.process_time()
            for index in range(0, requests, parallel):
                count = min(parallel, requests - index)
                if count == 1:
                    await _request(client, reader, writer, index, payload)
                else:
                    await _request_group(
                        client, reader, writer, start=index, count=count, expected=payload
                    )
            samples.append((time.perf_counter() - start_wall, time.process_time() - start_cpu))
        wall = statistics.median(sample[0] for sample in samples) * 1e6 / requests
        cpu = statistics.median(sample[1] for sample in samples) * 1e6 / requests
        log.info(
            'HTTP/2 %s loopback: %.2f us wall, %.2f us CPU per verified response',
            'buffered' if buffered else 'direct',
            wall,
            cpu,
        )
    finally:
        writer.close()
        await writer.wait_closed()
        proxy.close()
        origin.close()
        await proxy.wait_closed()
        await origin.wait_closed()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--requests', type=int, default=500)
    parser.add_argument('--buffered', action='store_true', help='Use 1 MiB feature responses')
    parser.add_argument('--parallel', type=int, default=1, help='Streams per batch (maximum 32)')
    args = parser.parse_args()
    if args.requests <= 0:
        parser.error('--requests must be positive')
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    if not 1 <= args.parallel <= 32:
        parser.error('--parallel must be between 1 and 32')
    asyncio.run(_run(args.requests, buffered=args.buffered, parallel=args.parallel))


if __name__ == '__main__':
    main()
