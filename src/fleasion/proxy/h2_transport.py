"""HTTP/2-to-HTTP/1 feature-engine bridge for authenticated plaintext streams.

This module operates only on caller-provided, already decrypted connections.
It neither establishes TLS sessions nor obtains traffic secrets.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import re
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

from fleasion.utils.logging import log_buffer

from .upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .server import FleasionProxy


type H2Headers = list[tuple[bytes, bytes]]

log = logging.getLogger(__name__)
_MAX_BUFFERED_BODY = 64 * 1024 * 1024
_MAX_ACTIVE_STREAMS = 64
_UPSTREAM_DRAIN_GRACE_SECONDS = 1.0
_H2_SEND_IDLE_TIMEOUT = 30.0
_H2_UPLOAD_IDLE_TIMEOUT = 30.0
_HOP_HEADERS = frozenset(
    {b'connection', b'keep-alive', b'proxy-connection', b'transfer-encoding', b'upgrade'}
)


def _valid_h2_request_target(method: bytes | None, path: bytes | None) -> bool:
    if method is None or re.fullmatch(rb"[!#$%&'*+\-.^_`|~0-9A-Za-z]+", method) is None:
        return False
    if path == b'*':
        return method == b'OPTIONS'
    return bool(
        path
        and path.startswith(b'/')
        and b'#' not in path
        and all(0x21 <= char <= 0x7E for char in path)
    )


def _as_http1(headers: H2Headers, body: bytes | bytearray, *, request: bool) -> bytes:
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


def _as_http2(
    message: bytes,
    *,
    request: bool,
    host: str,
    request_method: bytes | None = None,
) -> tuple[H2Headers, bytes]:
    raw, sep, body = message.partition(b'\r\n\r\n')
    if not sep:
        msg = 'Feature engine produced incomplete HTTP/1 message'
        raise ValueError(msg)
    first, *lines = raw.split(b'\r\n')
    parts = first.split(b' ', 2)
    body_forbidden = False
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
        status = int(parts[1])
        body_forbidden = request_method == b'HEAD' or status in {204, 304} or 100 <= status < 200
        if body_forbidden and body:
            msg = 'Feature engine produced a body for a bodyless HTTP response'
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
    if not body_forbidden:
        headers.append((b'content-length', str(len(body)).encode('ascii')))
    return headers, body


class _MessageWriter(asyncio.StreamWriter):
    """Convert complete HTTP/1 writes from the feature engine to a callback."""

    def __init__(
        self,
        callback: Callable[[bytes], Awaitable[None]],
        *,
        reserve: Callable[[int], None],
        release: Callable[[int], None],
    ) -> None:
        self._callback = callback
        self._reserve = reserve
        self._release = release
        self._buffer: bytes | bytearray = b''
        self._closing = False
        self.sent = False

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self._closing:
            msg = 'Cannot write to a closed HTTP/2 stream adapter'
            raise ConnectionError(msg)
        amount = data.nbytes if isinstance(data, memoryview) else len(data)
        if len(self._buffer) + amount > _MAX_BUFFERED_BODY:
            msg = 'HTTP feature message exceeds HTTP/2 bridge limit'
            raise ValueError(msg)
        self._reserve(amount)
        if not self._buffer and isinstance(data, bytes):
            self._buffer = data
            return
        buffer = bytearray(self._buffer) if isinstance(self._buffer, bytes) else self._buffer
        previous_size = len(buffer)
        try:
            buffer.extend(data.tobytes() if isinstance(data, memoryview) else data)
        except BaseException:
            del buffer[previous_size:]
            self._release(amount)
            raise
        self._buffer = buffer

    async def drain(self) -> None:
        if self._buffer:
            amount = len(self._buffer)
            message = self._buffer if isinstance(self._buffer, bytes) else bytes(self._buffer)
            self._buffer = b''
            try:
                await self._callback(message)
                self.sent = True
            finally:
                self._release(amount)

    def close(self) -> None:
        if not self._closing:
            self._closing = True
            amount = len(self._buffer)
            self._buffer = b''
            self._release(amount)

    def is_closing(self) -> bool:
        return self._closing

    async def wait_closed(self) -> None:
        self.close()

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
    response_held_bytes: int = 0
    response_rejected: bool = False
    request_trailers: H2Headers | None = None
    request_trailer_expected_headers: H2Headers | None = None
    request_trailer_expected_body_hash: bytes | None = None
    response_trailers: H2Headers | None = None
    trailer_expected_headers: H2Headers | None = None
    trailer_expected_body_hash: bytes | None = None
    upload_timer: asyncio.TimerHandle | None = None
    upload_generation: int = 0


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

    async def send(
        self,
        stream: int,
        headers: H2Headers,
        body: bytes,
        *,
        trailers: H2Headers | None = None,
    ) -> None:
        async with self.lock:
            if self.terminated:
                msg = 'HTTP/2 peer has terminated'
                raise ConnectionError(msg)
            self.h2.send_headers(stream, headers, end_stream=not body and trailers is None)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()
        await self._send_body(stream, body, end_stream=trailers is None)
        if trailers is not None:
            async with self.lock:
                if self.terminated:
                    msg = 'HTTP/2 peer has terminated'
                    raise ConnectionError(msg)
                self.h2.send_headers(stream, trailers, end_stream=True)
                self.writer.write(self.h2.data_to_send())
            await self.writer.drain()

    async def send_informational(self, stream: int, headers: H2Headers) -> None:
        async with self.lock:
            if self.terminated:
                msg = 'HTTP/2 peer has terminated'
                raise ConnectionError(msg)
            self.h2.send_headers(stream, headers, end_stream=False)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()

    async def open_request(
        self,
        headers: H2Headers,
        body: bytes,
        on_open: Callable[[int], None],
        *,
        trailers: H2Headers | None = None,
    ) -> None:
        # Reserve and open the new stream atomically to prevent duplicate IDs
        async with self.lock:
            if self.terminated:
                msg = 'HTTP/2 peer has terminated'
                raise ConnectionError(msg)
            stream = self.h2.get_next_available_stream_id()
            self.h2.send_headers(stream, headers, end_stream=not body and trailers is None)
            on_open(stream)
            self.writer.write(self.h2.data_to_send())
        await self.writer.drain()
        await self._send_body(stream, body, end_stream=trailers is None)
        if trailers is not None:
            async with self.lock:
                if self.terminated:
                    msg = 'HTTP/2 peer has terminated'
                    raise ConnectionError(msg)
                self.h2.send_headers(stream, trailers, end_stream=True)
                self.writer.write(self.h2.data_to_send())
            await self.writer.drain()

    async def _send_body(self, stream: int, body: bytes, *, end_stream: bool = True) -> None:
        offset = 0
        deadline = asyncio.get_running_loop().time() + _H2_SEND_IDLE_TIMEOUT
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
                    self.h2.send_data(
                        stream, body[offset:end], end_stream=end_stream and end == len(body)
                    )
                    offset = end
                    self.writer.write(self.h2.data_to_send())
                else:
                    self.window.clear()
            if count:
                deadline = asyncio.get_running_loop().time() + _H2_SEND_IDLE_TIMEOUT
                await self.writer.drain()
            else:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    msg = 'HTTP/2 flow-control window stalled'
                    raise TimeoutError(msg)
                try:
                    await asyncio.wait_for(self.window.wait(), timeout=remaining)
                except TimeoutError as exc:
                    msg = 'HTTP/2 flow-control window stalled'
                    raise TimeoutError(msg) from exc

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
        self.buffered_output_bytes = 0

    def _reserve_body_bytes(self, size: int) -> None:
        # Include pending feature-generated messages in the connection-wide budget
        if (
            self.buffered_request_bytes
            + self.buffered_response_bytes
            + self.buffered_output_bytes
            + size
            > _MAX_BUFFERED_BODY
        ):
            msg = 'HTTP/2 connection body buffer limit exceeded'
            raise ValueError(msg)

    def _reserve_output_bytes(self, size: int) -> None:
        self._reserve_body_bytes(size)
        self.buffered_output_bytes += size

    def _release_output_bytes(self, size: int) -> None:
        self.buffered_output_bytes -= size

    def _release_response_body(self, request: _Request) -> None:
        self.buffered_response_bytes -= len(request.response_body) + request.response_held_bytes
        request.response_body.clear()
        request.response_held_bytes = 0

    @staticmethod
    def _cancel_upload_timeout(request: _Request) -> None:
        request.upload_generation += 1
        if request.upload_timer is not None:
            request.upload_timer.cancel()
            request.upload_timer = None

    def _arm_upload_timeout(self, stream: int, request: _Request) -> None:
        self._cancel_upload_timeout(request)
        generation = request.upload_generation
        request.upload_timer = asyncio.get_running_loop().call_later(
            _H2_UPLOAD_IDLE_TIMEOUT,
            lambda: asyncio.create_task(self._expire_idle_upload(stream, request, generation)),
        )

    async def _expire_idle_upload(
        self, stream: int, request: _Request, generation: int
    ) -> None:
        if (
            self.requests.get(stream) is not request
            or request.upload_generation != generation
            or request.task is not None
        ):
            return
        self._cancel_upload_timeout(request)
        self.requests.pop(stream, None)
        self.buffered_request_bytes -= len(request.body)
        request.body.clear()
        with contextlib.suppress(OSError, H2Error):
            await self.client.reset(stream)

    async def _run_request(self, stream: int, request: _Request) -> None:
        final_response_sent = False

        async def send_request(message: bytes) -> None:
            headers, body = _as_http2(message, request=True, host=self.host)
            trailers = request.request_trailers
            if trailers is not None and (
                request.request_trailer_expected_headers != headers
                or request.request_trailer_expected_body_hash != hashlib.sha256(body).digest()
            ):
                msg = 'Cannot forward HTTP/2 request trailers after request modification'
                raise ValueError(msg)

            def register(upstream_id: int) -> None:
                request.upstream_id = upstream_id
                self.upstream_requests[upstream_id] = request

            await self.server.open_request(headers, body, register, trailers=trailers)

        async def send_response(message: bytes) -> None:
            nonlocal final_response_sent
            method = dict(request.headers).get(b':method')
            headers, body = _as_http2(message, request=False, host=self.host, request_method=method)
            status = int(dict(headers)[b':status'])
            if 100 <= status < 200:
                await self.client.send_informational(stream, headers)
            else:
                trailers = request.response_trailers
                if trailers is not None and (
                    request.trailer_expected_headers != headers
                    or request.trailer_expected_body_hash != hashlib.sha256(body).digest()
                ):
                    msg = 'Cannot forward HTTP/2 trailers after response modification'
                    raise ValueError(msg)
                await self.client.send(stream, headers, body, trailers=trailers)
                final_response_sent = True

        upstream_writer = _MessageWriter(
            send_request, reserve=self._reserve_output_bytes, release=self._release_output_bytes
        )
        response_writer = _MessageWriter(
            send_response, reserve=self._reserve_output_bytes, release=self._release_output_bytes
        )
        request_reader = asyncio.StreamReader()
        body = bytes(request.body)
        reserved_body_size = len(body)

        def prepare_feature_input() -> UpstreamConnectResult:
            if request.request_trailers is not None:
                original_message = _as_http1(request.headers, body, request=True)
                expected_headers, expected_body = _as_http2(
                    original_message, request=True, host=self.host
                )
                request.request_trailer_expected_headers = expected_headers
                request.request_trailer_expected_body_hash = hashlib.sha256(expected_body).digest()
            request.body.clear()
            request_reader.feed_data(_as_http1(request.headers, body, request=True))
            request_reader.feed_eof()
            return UpstreamConnectResult(
                reader=request.upstream_reader,
                writer=upstream_writer,
                method='authenticated_h2',
                endpoint=self.host,
            )

        # The feature engine keeps the body until it exits, so keep its budget reservation
        try:
            upstream = prepare_feature_input()
            await self.proxy.serve_decrypted_http_session(
                request_reader, response_writer, self.host, upstream
            )
        except (OSError, ValueError, H2Error) as exc:
            # A failing stream is reset, without leaking any request headers or body
            log_buffer.log(
                'TLS',
                f'HTTP/2 stream failed for {self.host}: '
                f'upstream_opened={request.upstream_id is not None} '
                f'upstream_ended={request.upstream_ended} '
                f'error={type(exc).__name__}',
            )
        finally:
            upstream_writer.close()
            response_writer.close()
            self.buffered_request_bytes -= reserved_body_size
            request.body.clear()
            if not final_response_sent:
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
                    and _valid_h2_request_target(
                        pseudo.get(b':method'), pseudo.get(b':path')
                    )
                )
                if not valid_request or len(self.requests) >= _MAX_ACTIVE_STREAMS:
                    # One overloaded stream cannot starve the connection or other streams
                    await self.client.reset(event.stream_id)
                else:
                    expects_continue = any(
                        name == b'expect' and value.strip().lower() == b'100-continue'
                        for name, value in headers
                    )
                    if expects_continue:
                        # This bridge buffers the request body before forwarding it
                        headers = [(name, value) for name, value in headers if name != b'expect']
                    request = _Request(headers)
                    self.requests[event.stream_id] = request
                    if event.stream_ended is None:
                        self._arm_upload_timeout(event.stream_id, request)
                    if expects_continue and event.stream_ended is None:
                        await self.client.send_informational(
                            event.stream_id, [(b':status', b'100')]
                        )
            elif isinstance(event, DataReceived):
                request = self.requests.get(event.stream_id)
                if request is None:
                    # Discarded streams still consume the connection's receive window
                    await self.client.acknowledge(event.stream_id, event.flow_controlled_length)
                    continue
                try:
                    self._reserve_body_bytes(len(event.data))
                except ValueError:
                    # Reject only the stream exceeding the aggregate buffer budget
                    self._cancel_upload_timeout(request)
                    self.buffered_request_bytes -= len(request.body)
                    request.body.clear()
                    self.requests.pop(event.stream_id, None)
                    if request.task is not None:
                        request.task.cancel()
                    await self.client.acknowledge(event.stream_id, event.flow_controlled_length)
                    await self.client.reset(event.stream_id)
                    continue
                self.buffered_request_bytes += len(event.data)
                request.body.extend(event.data)
                if event.data:
                    self._arm_upload_timeout(event.stream_id, request)
                await self.client.acknowledge(event.stream_id, event.flow_controlled_length)
            elif isinstance(event, StreamEnded):
                request = self.requests.get(event.stream_id)
                if request is not None:
                    self._cancel_upload_timeout(request)
                    task = asyncio.create_task(self._run_request(event.stream_id, request))
                    request.task = task
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
            elif isinstance(event, TrailersReceived):
                request = self.requests.get(event.stream_id)
                if request is not None:
                    request.request_trailers = list(event.headers)
            elif isinstance(event, StreamReset):
                request = self.requests.pop(event.stream_id, None)
                if request is not None:
                    self._cancel_upload_timeout(request)
                    self.buffered_request_bytes -= len(request.body)
                    request.body.clear()
                    if request.task is not None:
                        request.task.cancel()
            elif isinstance(event, (WindowUpdated, RemoteSettingsChanged)):
                self.client.window.set()
            elif isinstance(event, ConnectionTerminated):
                self.client.terminate()

    async def _receive_upstream(self, data: bytes) -> None:
        for event in await self.server.receive(data):
            if isinstance(event, InformationalResponseReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    request.upstream_reader.feed_data(
                        _as_http1(list(event.headers), b'', request=False)
                    )
            elif isinstance(event, ResponseReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None:
                    request.response_headers = list(event.headers)
            elif isinstance(event, DataReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    try:
                        self._reserve_body_bytes(len(event.data))
                    except ValueError:
                        # Discard oversized upstream data without harming other streams
                        request.response_rejected = True
                        self._release_response_body(request)
                        request.upstream_reader.set_exception(
                            ConnectionError('HTTP/2 response exceeds connection buffer budget')
                        )
                        await self.server.acknowledge(event.stream_id, event.flow_controlled_length)
                        await self.server.reset(event.stream_id)
                        continue
                    self.buffered_response_bytes += len(event.data)
                    request.response_bytes += len(event.data)
                    request.response_body.extend(event.data)
                await self.server.acknowledge(event.stream_id, event.flow_controlled_length)
            elif isinstance(event, TrailersReceived):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    request.response_trailers = list(event.headers)
            elif isinstance(event, StreamEnded):
                request = self.upstream_requests.get(event.stream_id)
                if request is not None and not request.response_rejected:
                    if request.response_headers is None:
                        request.upstream_reader.set_exception(
                            ConnectionError('HTTP/2 response missing final headers')
                        )
                    else:
                        message = _as_http1(
                            request.response_headers,
                            request.response_body,
                            request=False,
                        )
                        if request.response_trailers is not None:
                            expected_headers, expected_body = _as_http2(
                                message,
                                request=False,
                                host=self.host,
                                request_method=dict(request.headers).get(b':method'),
                            )
                            request.trailer_expected_headers = expected_headers
                            request.trailer_expected_body_hash = hashlib.sha256(
                                expected_body
                            ).digest()
                        request.upstream_reader.feed_data(message)
                        request.upstream_ended = True
                        request.upstream_reader.feed_eof()
                        # The feature engine still owns these bytes until its task exits
                        request.response_held_bytes += len(request.response_body)
                        request.response_body.clear()
                    if request.response_headers is None:
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
            unfinished = sum(
                not request.upstream_ended for request in self.upstream_requests.values()
            )
            if unfinished:
                log_buffer.log(
                    'TLS',
                    f'HTTP/2 upstream ended for {self.host}: '
                    f'unfinished_streams={unfinished} '
                    f'buffered_request_bytes={self.buffered_request_bytes} '
                    f'buffered_response_bytes={self.buffered_response_bytes}',
                )
            for request in self.upstream_requests.values():
                if not request.upstream_ended:
                    request.upstream_reader.set_exception(
                        ConnectionError('HTTP/2 upstream ended before END_STREAM')
                    )

    async def run(self, *, initial_client_data: bytes = b'') -> None:
        """Run the bridge until either HTTP/2 connection closes."""
        pumps: list[asyncio.Task[None]] = []
        try:
            await self.client.start()
            await self.server.start(disable_push=True)
            if initial_client_data:
                await self._receive_client(initial_client_data)
            pumps = [
                asyncio.create_task(self._pump(self.client_reader, client=True)),
                asyncio.create_task(self._pump(self.upstream_reader, client=False)),
            ]
            done, _ = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            if pumps[1] in done and self.tasks:
                # Upstream EOF cannot justify an indefinite feature-task wait
                # Give completed streams time to flush, then cancel stalled tasks
                await asyncio.wait(self.tasks, timeout=_UPSTREAM_DRAIN_GRACE_SECONDS)
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
            # Release unfinished request/response buffers on either peer's EOF
            # Tasks have already been cancelled and joined above
            for request in self.requests.values():
                self._cancel_upload_timeout(request)
                request.body.clear()
                request.response_body.clear()
            for request in self.upstream_requests.values():
                request.response_body.clear()
            self.buffered_request_bytes = 0
            self.buffered_response_bytes = 0
            self.buffered_output_bytes = 0
            self.requests.clear()
            self.upstream_requests.clear()
