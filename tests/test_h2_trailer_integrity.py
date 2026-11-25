"""Feature rewrites may not forward stale HTTP/2 integrity trailers."""

from __future__ import annotations

import asyncio
import hashlib
from typing import TYPE_CHECKING, cast

import pytest
from h2.connection import H2Connection
from h2.events import ResponseReceived, StreamReset

from fleasion.proxy.h2_transport import H2FeatureBridge, _as_http2  # pyright: ignore[reportPrivateUsage]
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


class _MutatingProxy:
    def __init__(self, *, mutate_request: bool) -> None:
        self.mutate_request = mutate_request

    async def serve_decrypted_http_session(
        self,
        _reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        _host: str,
        upstream: UpstreamConnectResult,
    ) -> None:
        if self.mutate_request:
            assert upstream.writer is not None
            upstream.writer.write(
                b'POST /changed HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Content-Length: 8\r\n\r\noriginal'
            )
            await upstream.writer.drain()
        else:
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nmutated!')
            await writer.drain()


@pytest.mark.parametrize('mutate_request', [True, False])
def test_mutated_h2_message_resets_stream_instead_of_forwarding_stale_trailer(
    mutate_request: bool,
) -> None:
    async def run() -> None:
        client_writer = _MemoryWriter()
        upstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', _MutatingProxy(mutate_request=mutate_request)),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', client_writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', upstream_writer),
                method='test',
                endpoint='127.0.0.1',
            ),
        )
        await bridge.client.start()
        peer = H2Connection()
        peer.initiate_connection()
        peer.receive_data(client_writer.take())
        peer.send_headers(
            1,
            [
                (b':method', b'POST'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/original'),
            ],
        )
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        request = bridge.requests[1]
        if mutate_request:
            request.body.extend(b'original')
            bridge.buffered_request_bytes = len(request.body)
            request.request_trailers = [(b'x-integrity', b'original')]
        else:
            baseline = b'HTTP/1.1 200 OK\r\ncontent-length: 8\r\n\r\noriginal'
            expected_headers, expected_body = _as_http2(
                baseline, request=False, host='apis.roblox.com'
            )
            request.response_trailers = [(b'x-integrity', b'original')]
            request.trailer_expected_headers = expected_headers
            request.trailer_expected_body_hash = hashlib.sha256(expected_body).digest()
        await bridge._run_request(1, request)  # pyright: ignore[reportPrivateUsage]
        events = peer.receive_data(client_writer.take())
        assert [event.stream_id for event in events if isinstance(event, StreamReset)] == [1]
        assert not any(isinstance(event, ResponseReceived) for event in events)
        assert 1 not in bridge.requests
        assert not bridge.upstream_requests

    asyncio.run(run())
