"""HTTP/2-to-HTTP/1 feature-engine bridge for authenticated plaintext streams.

This module operates only on caller-provided, already decrypted connections.
It neither establishes TLS sessions nor obtains traffic secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import (
    ConnectionTerminated,
    DataReceived,
    InformationalResponseReceived,
    RemoteSettingsChanged,
    RequestReceived,
    ResponseReceived,
    StreamEnded,
    StreamReset,
    TrailersReceived,
    WindowUpdated,
)
from h2.exceptions import H2Error
from h2.settings import SettingCodes

from .upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .server import FleasionProxy


type H2Headers = list[tuple[bytes, bytes]]

log = logging.getLogger(__name__)
_MAX_BUFFERED_BODY = 64 * 1024 * 1024
_HOP_HEADERS = frozenset(
    {b'connection', b'keep-alive', b'proxy-connection', b'transfer-encoding', b'upgrade'}
)


def _as_http1(headers: H2Headers, body: bytes, *, request: bool) -> bytes:
    pseudo = dict(headers)
    authority = b''
    if request:
        method, path = pseudo.get(b':method'), pseudo.get(b':path')
        authority = pseudo.get(b':authority')
        if not method or not path or not authority:
            msg = 'HTTP/2 request missing required pseudo-header'
            raise ValueError(msg)
        first = method + b' ' + path + b' HTTP/1.1'
    else:
        status = pseudo.get(b':status')
        if not status or not status.isdigit():
            msg = 'HTTP/2 response missing valid status'
            raise ValueError(msg)
        first = b'HTTP/1.1 ' + status + b' Upstream'

    lines = [first]
    for name, value in headers:
        if name.startswith(b':') or name in _HOP_HEADERS or name in {b'content-length', b'host'}:
            continue
        lines.append(name + b': ' + value)
    if request:
        lines.append(b'host: ' + authority)
    if request:
        # Complete HTTP/2 requests have a known plaintext body length
        lines.append(b'content-length: ' + str(len(body)).encode('ascii'))
    # Upstream HTTP/2 responses are delimited by END_STREAM, not Content-Length
    lines.append(b'connection: close')
    return b'\r\n'.join(lines) + b'\r\n\r\n' + body


def _as_http2(message: bytes, *, request: bool, host: str) -> tuple[H2Headers, bytes]:
    raw, sep, body = message.partition(b'\r\n\r\n')
    if not sep:
        msg = 'Feature engine produced incomplete HTTP/1 message'
        raise ValueError(msg)
    first, *lines = raw.split(b'\r\n')
    parts = first.split(b' ', 2)
    if request:
        if len(parts) < 3 or not parts[2].startswith(b'HTTP/1.'):
            msg = 'Feature engine produced an invalid HTTP/1 request'
            raise ValueError(msg)
        headers: H2Headers = [
            (b':method', parts[0]),
            (b':scheme', b'https'),
            (b':authority', host.encode('ascii')),
            (b':path', parts[1]),
        ]
    else:
        if len(parts) < 2 or not parts[1].isdigit():
            msg = 'Feature engine produced an invalid HTTP/1 response'
            raise ValueError(msg)
        headers = [(b':status', parts[1])]
    for line in lines:
        name, colon, value = line.partition(b':')
        if not colon:
            msg = 'Feature engine produced a malformed HTTP header'
            raise ValueError(msg)
        name = name.strip().lower()
        value = value.strip()
        if name in _HOP_HEADERS or name in {b'host', b'content-length'}:
            continue
        if not name or name.startswith(b':'):
            msg = 'Feature engine produced a malformed header name'
            raise ValueError(msg)
        headers.append((name, value))
    headers.append((b'content-length', str(len(body)).encode('ascii')))
    return headers, body


class _MessageWriter(asyncio.StreamWriter):
    """Convert complete HTTP/1 writes from the feature engine to a callback."""

    def __init__(self, callback: Callable[[bytes], Awaitable[None]]) -> None:
        self._callback = callback
        self._buffer = bytearray()
        self._closing = False
        self.sent = False

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._closing:
            msg = 'Cannot write to a closed HTTP/2 stream adapter'
            raise ConnectionError(msg)
        self._buffer.extend(data)
        if len(self._buffer) > _MAX_BUFFERED_BODY:
            msg = 'HTTP feature message exceeds HTTP/2 bridge limit'
            raise ValueError(msg)

    async def drain(self) -> None:
        if self._buffer:
            message = bytes(self._buffer)
            self._buffer.clear()
            await self._callback(message)
            self.sent = True

    def close(self) -> None:
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        self._closing = True

    def __del__(self, _warnings: object = None) -> None:
        # No asyncio transport exists for this StreamWriter adapter
        pass


@dataclass
class _Request:
    headers: H2Headers
    body: bytearray = field(default_factory=bytearray)
    upstream_reader: asyncio.StreamReader = field(default_factory=asyncio.StreamReader)
    task: asyncio.Task[None] | None = None
    upstream_id: int | None = None
    response_bytes: int = 0
    upstream_ended: bool = False
    response_headers: H2Headers | None = None
    response_body: bytearray = field(default_factory=bytearray)
    response_rejected: bool = False


class _Peer:
    def __init__(self, writer: asyncio.StreamWriter, *, client_side: bool) -> None:
        self.writer = writer
        self.h2 = H2Connection(H2Configuration(client_side=client_side))
        self.lock = asyncio.Lock()
        self.window = asyncio.Event()
        self.window.set()
        self.terminated = False

    async def start(self, *, disable_push: bool = False) -> None:
        async with self.lock:
            self.h2.initiate_connection()
            if disable_push:
                self.h2.update_settings({SettingCodes.ENABLE_PUSH: 0})
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()

    async def receive(self, data: bytes) -> list[object]:
        async with self.lock:
            events: list[object] = list(self.h2.receive_data(data))
            if any(
                isinstance(
                    event, (StreamReset, ConnectionTerminated, WindowUpdated, RemoteSettingsChanged)
                )
                for event in events
            ):
                # Wake blocked sends even when the peer resets a stream without updating its window
                self.window.set()
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()
        return events

    async def acknowledge(self, stream: int, size: int) -> None:
        async with self.lock:
            self.h2.acknowledge_received_data(size, stream)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()

    async def send(self, stream: int, headers: H2Headers, body: bytes) -> None:
        async with self.lock:
            if self.terminated:
                msg = 'HTTP/2 peer has terminated'
                raise ConnectionError(msg)
            self.h2.send_headers(stream, headers, end_stream=not body)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()
        await self._send_body(stream, body)

    async def open_request(
        self,
        headers: H2Headers,
        body: bytes,
        on_open: Callable[[int], None],
    ) -> None:
        # Reserve and open the new stream atomically to prevent duplicate IDs
        async with self.lock:
            if self.terminated:
                msg = 'HTTP/2 peer has terminated'
                raise ConnectionError(msg)
            stream = self.h2.get_next_available_stream_id()
            self.h2.send_headers(stream, headers, end_stream=not body)
            on_open(stream)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()
        await self._send_body(stream, body)

    async def _send_body(self, stream: int, body: bytes) -> None:
        offset = 0
        while offset < len(body):
            async with self.lock:
                if self.terminated:
                    msg = 'HTTP/2 peer has terminated'
                    raise ConnectionError(msg)
                peer_stream = self.h2.streams.get(stream)
                if peer_stream is None or peer_stream.closed:
                    msg = 'HTTP/2 stream closed during outbound flow-control wait'
                    raise ConnectionError(msg)
                count = min(
                    len(body) - offset,
                    self.h2.local_flow_control_window(stream),
                    self.h2.max_outbound_frame_size,
                )
                if count:
                    end = offset + count
                    self.h2.send_data(stream, body[offset:end], end_stream=end == len(body))
                    offset = end
                    self.writer.write(self.h2.data_to_send())
                else:
                    self.window.clear()
            if count:
                await self.writer.drain()
            else:
                await self.window.wait()

    async def reset(self, stream: int) -> None:
        async with self.lock:
            with contextlib.suppress(H2Error):
                self.h2.reset_stream(stream, error_code=8)
                self.writer.write(self.h2.data_to_send())
            self.window.set()
        await self.writer.drain()

    def terminate(self) -> None:
        self.terminated = True
        self.window.set()


class H2FeatureBridge:
    """Map complete authenticated HTTP/2 streams to Fleasion HTTP features."""

    def __init__(
        self,
        proxy: FleasionProxy,
        host: str,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream: UpstreamConnectResult,
    ) -> None:
        if upstream.reader is None or upstream.writer is None:
            msg = 'HTTP/2 transport requires an authenticated upstream stream pair'
            raise ValueError(msg)
        self.proxy = proxy
        self.host = host
        self.client_reader = client_reader
        self.upstream_reader = upstream.reader
        self.client = _Peer(client_writer, client_side=False)
        self.server = _Peer(upstream.writer, client_side=True)
        self.requests: dict[int, _Request] = {}
        self.upstream_requests: dict[int, _Request] = {}
        self.tasks: set[asyncio.Task[None]] = set()
        self.buffered_request_bytes = 0
        self.buffered_response_bytes = 0

    def _reserve_body_bytes(self, size: int) -> None:
        # Enforce a total bridge buffer budget, not 64 MiB per HTTP/2 stream
        if self.buffered_request_bytes + self.buffered_response_bytes + size > _MAX_BUFFERED_BODY:
            msg = 'HTTP/2 connection body buffer limit exceeded'
            raise ValueError(msg)

    def _release_response_body(self, request: _Request) -> None:
        self.buffered_response_bytes -= len(request.response_body)
        request.response_body.clear()

    async def _run_request(self, stream: int, request: _Request) -> None:
        async def send_request(message: bytes) -> None:
            headers, body = _as_http2(message, request=True, host=self.host)

            def register(upstream_id: int) -> None:
                request.upstream_id = upstream_id
                self.upstream_requests[upstream_id] = request

            await self.server.open_request(headers, body, register)

        async def send_response(message: bytes) -> None:
            headers, body = _as_http2(message, request=False, host=self.host)
            await self.client.send(stream, headers, body)

        upstream_writer = _MessageWriter(send_request)
        response_writer = _MessageWriter(send_response)
        request_reader = asyncio.StreamReader()
        body = bytes(request.body)
        self.buffered_request_bytes -= len(request.body)
        request.body.clear()
        request_reader.feed_data(_as_http1(request.headers, body, request=True))
        request_reader.feed_eof()
        upstream = UpstreamConnectResult(
            reader=request.upstream_reader,
            writer=upstream_writer,
            method='authenticated_h2',
            endpoint=self.host,
        )
        try:
            await self.proxy.serve_decrypted_http_session(
                request_reader, response_writer, self.host, upstream
            )
        except OSError, ValueError, H2Error:
            # Malformed or cancelled streams are reset, not forwarded elsewhere
            pass
        finally:
            if not response_writer.sent:
                with contextlib.suppress(OSError, H2Error):
                    await self.client.reset(stream)
            if request.upstream_id is not None:
                with contextlib.suppress(OSError, H2Error):
                    await self.server.reset(request.upstream_id)
                self.upstream_requests.pop(request.upstream_id, None)
            self._release_response_body(request)
            self.requests.pop(stream, None)

    async def _receive_client(self, data: bytes) -> None:
        for event in await self.client.receive(data):
            if isinstance(event, RequestReceived):
                if event.stream_id in self.requests:
                    msg = 'Duplicate HTTP/2 request stream'
                    raise ValueError(msg)
                headers: H2Headers = list(event.headers)
                pseudo = dict(headers)
                authority = pseudo.get(b':authority', b'').decode('ascii', errors='replace')
                host = authority.lower().removesuffix(':443').rstrip('.')
                valid_request = (
                    host == self.host
                    and pseudo.get(b':scheme') == b'https'
                    and pseudo.get(b':method') != b'CONNECT'
                    and bool(pseudo.get(b':path'))
                )
                if not valid_request:
                    await self.client.reset(event.stream_id)
                else:
                    self.requests[event.stream_id] = _Request(headers)
            elif isinstance(event, DataReceived):
                request = self.requests.get(event.stream_id)
                if request is None:
                    # Discarded streams still consume the connection's receive window
                    await self.client.acknowledge(event.stream_id, event.flow_controlled_length)
                    continue
                self._reserve_body_bytes(len(event.data))
                self.buffered_request_bytes += len(event.data)
                request.body.extend(event.data)
                await self.client.acknowledge(event.stream_id, event.flow_controlled_length)
            elif isinstance(event, StreamEnded):
                request = self.requests.get(event.stream_id)
                if request is not None:
                    task = asyncio.create_task(self._run_request(event.stream_id, request))
                    request.task = task
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
            elif isinstance(event, TrailersReceived):
                # The HTTP/1 feature adapter cannot preserve request trailers
                request = self.requests.pop(event.stream_id, None)
                if request is not None:
                    if request.task is not None:
                        request.task.cancel()
                    else:
                        self.buffered_request_bytes -= len(request.body)
                await self.client.reset(event.stream_id)
            elif isinstance(event, StreamReset):
                request = self.requests.pop(event.stream_id, None)
                if request is not None:
                    if request.task is not None:
                        request.task.cancel()
                    else:
                        self.buffered_request_bytes -= len(request.body)
            elif isinstance(event, (WindowUpdated, RemoteSettingsChanged)):
                self.client.window.set()
            elif isinstance(event, ConnectionTerminated):
                self.client.terminate()

    async def _receive_upstream(self, data: bytes) -> None:
        for event in await self.server.receive(data):
            if isinstance(event, InformationalResponseReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None:
                    request.response_rejected = True
                    request.upstream_reader.set_exception(
                        ConnectionError('HTTP/2 informational responses are not supported')
                    )
            elif isinstance(event, ResponseReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None:
                    request.response_headers = list(event.headers)
            elif isinstance(event, DataReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    self._reserve_body_bytes(len(event.data))
                    self.buffered_response_bytes += len(event.data)
                    request.response_bytes += len(event.data)
                    request.response_body.extend(event.data)
                await self.server.acknowledge(event.stream_id, event.flow_controlled_length)
            elif isinstance(event, TrailersReceived):
                # Do not forward an incomplete response with silently dropped trailers
                request = self.upstream_requests.get(event.stream_id)
                if request is not None:
                    request.response_rejected = True
                    request.upstream_reader.set_exception(
                        ConnectionError('HTTP/2 response trailers are not supported')
                    )
            elif isinstance(event, StreamEnded):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    if request.response_headers is None:
                        request.upstream_reader.set_exception(
                            ConnectionError('HTTP/2 response missing final headers')
                        )
                    else:
                        request.upstream_reader.feed_data(
                            _as_http1(
                                request.response_headers,
                                bytes(request.response_body),
                                request=False,
                            )
                        )
                        request.upstream_ended = True
                        request.upstream_reader.feed_eof()
                    self._release_response_body(request)
            elif isinstance(event, StreamReset):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None:
                    self._release_response_body(request)
                    request.upstream_reader.set_exception(ConnectionError('HTTP/2 stream reset'))
            elif isinstance(event, (WindowUpdated, RemoteSettingsChanged)):
                self.server.window.set()
            elif isinstance(event, ConnectionTerminated):
                self.server.terminate()

    async def _pump(self, reader: asyncio.StreamReader, *, client: bool) -> None:
        while not (self.client if client else self.server).terminated:
            data = await reader.read(65536)
            if not data:
                break
            if client:
                await self._receive_client(data)
            else:
                await self._receive_upstream(data)
        if not client:
            for request in self.upstream_requests.values():
                if not request.upstream_ended:
                    request.upstream_reader.set_exception(
                        ConnectionError('HTTP/2 upstream ended before END_STREAM')
                    )

    async def run(self, *, initial_client_data: bytes = b'') -> None:
        """Run the bridge until either HTTP/2 connection closes."""
        await self.client.start()
        await self.server.start(disable_push=True)
        if initial_client_data:
            await self._receive_client(initial_client_data)
        pumps = [
            asyncio.create_task(self._pump(self.client_reader, client=True)),
            asyncio.create_task(self._pump(self.upstream_reader, client=False)),
        ]
        try:
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if pumps[1] in done and self.tasks:
                await asyncio.gather(*self.tasks)
        finally:
            self.client.terminate()
            self.server.terminate()
            for task in [*pumps, *self.tasks]:
                task.cancel()
            await asyncio.gather(*pumps, *self.tasks, return_exceptions=True)
            for request in self.upstream_requests.values():
                if not request.upstream_ended:
                    request.upstream_reader.set_exception(
                        ConnectionError('HTTP/2 bridge shut down before END_STREAM')
                    )
            self.requests.clear()
            self.upstream_requests.clear()
