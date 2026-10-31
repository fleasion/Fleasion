"""HTTP/2 GOAWAY and abrupt-close recovery against controlled localhost peers."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import (
    DataReceived,
    RequestReceived,
    ResponseReceived,
    StreamEnded,
    StreamReset,
)

from fleasion.proxy.h2_transport import H2FeatureBridge
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.tls_backends import (
    AuthenticatedSession,
    dispatch_authenticated_session,
)
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


def _proxy(path: Path) -> FleasionProxy:
    ca, key = generate_ca(path)
    cert = generate_multi_host_cert('goaway-test', {'apis.roblox.com'}, ca, key, path)
    return FleasionProxy(
        texture_stripper=cast(
            'TextureStripper',
            SimpleNamespace(
                config_manager=SimpleNamespace(get_all_replacements=_empty_replacements)
            ),
        ),
        cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
        host_certs={},
        default_cert=cert,
        intercept_hosts={'apis.roblox.com'},
        intercept_all_hosts=True,
    )


@pytest.mark.parametrize('failure', ['goaway', 'eof'])
def test_upstream_goaway_or_eof_never_completes_an_incomplete_response(
    tmp_path: Path, failure: str
) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        upstream_seen: set[int] = set()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            try:
                while packet := await reader.read(65536):
                    for event in h2.receive_data(packet):
                        if isinstance(event, RequestReceived):
                            upstream_seen.add(event.stream_id)
                        elif isinstance(event, StreamEnded) and len(upstream_seen) == 2:
                            h2.send_headers(1, [(b':status', b'200')])
                            h2.send_data(1, b'complete-upstream', end_stream=True)
                            h2.send_headers(3, [(b':status', b'200')])
                            h2.send_data(3, b'truncated-response', end_stream=False)
                            if failure == 'goaway':
                                h2.close_connection(error_code=0, last_stream_id=1)
                            writer.write(h2.data_to_send())
                            await writer.drain()
                            return
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        server_port = server.sockets[0].getsockname()[1]
        assert isinstance(server_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            up_reader, up_writer = await asyncio.open_connection('127.0.0.1', server_port)
            try:
                await dispatch_authenticated_session(
                    proxy,
                    AuthenticatedSession(
                        hostname='apis.roblox.com',
                        alpn='h2',
                        client_reader=reader,
                        client_writer=writer,
                        upstream=UpstreamConnectResult(
                            reader=up_reader,
                            writer=up_writer,
                            method='controlled_loopback_test',
                            endpoint='127.0.0.1',
                        ),
                        server_authenticated=True,
                    ),
                    expected_hostname='apis.roblox.com',
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, '127.0.0.1', 0)
        bridge_port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(bridge_port, int)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', bridge_port)
            client = H2Connection()
            client.initiate_connection()
            for stream_id in (1, 3):
                client.send_headers(
                    stream_id,
                    [
                        (b':method', b'GET'),
                        (b':scheme', b'https'),
                        (b':authority', b'apis.roblox.com'),
                        (b':path', b'/valid' if stream_id == 1 else b'/incomplete'),
                    ],
                    end_stream=True,
                )
            writer.write(client.data_to_send())
            await writer.drain()
            bodies: dict[int, bytearray] = {1: bytearray(), 3: bytearray()}
            finished: set[int] = set()
            resets: set[int] = set()

            async def collect() -> None:
                while not (1 in finished and 3 in resets):
                    packet = await reader.read(65536)
                    if not packet:
                        break
                    for event in client.receive_data(packet):
                        if isinstance(event, ResponseReceived):
                            assert dict(event.headers)[b':status'] == b'200'
                        elif isinstance(event, DataReceived):
                            bodies[event.stream_id].extend(event.data)
                            client.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            finished.add(event.stream_id)
                        elif isinstance(event, StreamReset):
                            resets.add(event.stream_id)
                    writer.write(client.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=5)
            assert upstream_seen == {1, 3}
            assert finished == {1}
            assert resets == {3}
            assert bytes(bodies[1]) == b'complete-upstream'
            assert not bodies[3]
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            server.close()
            await bridge_server.wait_closed()
            await server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass


def test_client_disconnect_releases_partially_uploaded_request_bytes(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        client_reader = asyncio.StreamReader()
        client_reader.feed_eof()
        bridge = H2FeatureBridge(
            proxy,
            'apis.roblox.com',
            client_reader,
            cast('asyncio.StreamWriter', _MemoryWriter()),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback_test',
                endpoint='127.0.0.1',
            ),
        )
        client = H2Connection()
        client.initiate_connection()
        client.send_headers(
            1,
            [
                (b':method', b'POST'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/unfinished-upload'),
            ],
        )
        client.send_data(1, b'partial-upload', end_stream=False)
        try:
            await asyncio.wait_for(
                bridge.run(initial_client_data=client.data_to_send()), timeout=0.8
            )
            assert not bridge.requests
            assert not bridge.upstream_requests
            assert bridge.buffered_request_bytes == 0
            assert bridge.buffered_response_bytes == 0
            assert not bridge.tasks
        finally:
            await proxy.stop()

    asyncio.run(run())


def test_client_goaway_cancels_inflight_request_without_waiting_for_upstream() -> None:
    class _StalledProxy:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def serve_decrypted_http_session(
            self,
            _reader: asyncio.StreamReader,
            _writer: asyncio.StreamWriter,
            _host: str,
            _upstream: UpstreamConnectResult,
        ) -> None:
            self.started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cancelled.set()

    async def run() -> None:
        proxy = _StalledProxy()
        client_reader = asyncio.StreamReader()
        upstream_reader = asyncio.StreamReader()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            client_reader,
            cast('asyncio.StreamWriter', _MemoryWriter()),
            UpstreamConnectResult(
                reader=upstream_reader,
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback_test',
                endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection()
        peer.initiate_connection()
        peer.send_headers(
            1,
            [
                (b':method', b'GET'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/client-goaway'),
            ],
            end_stream=True,
        )
        connection = asyncio.create_task(bridge.run(initial_client_data=peer.data_to_send()))
        await asyncio.wait_for(proxy.started.wait(), timeout=0.5)
        peer.close_connection(last_stream_id=1, error_code=0)
        client_reader.feed_data(peer.data_to_send())
        await asyncio.wait_for(connection, timeout=0.7)
        assert proxy.cancelled.is_set()
        assert not bridge.requests
        assert not bridge.upstream_requests
        assert not bridge.tasks
        assert bridge.client.terminated

    asyncio.run(run())


def test_invalid_initial_http2_preface_still_terminates_bridge() -> None:
    from h2.exceptions import ProtocolError

    async def run() -> None:
        bridge = H2FeatureBridge(
            cast('FleasionProxy', object()),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', _MemoryWriter()),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback_test',
                endpoint='127.0.0.1',
            ),
        )
        with pytest.raises(ProtocolError):
            await bridge.run(initial_client_data=b'NOT AN HTTP2 PREFACE')
        assert bridge.client.terminated
        assert bridge.server.terminated
        assert not bridge.tasks
        assert not bridge.requests
        assert bridge.buffered_request_bytes == 0

    asyncio.run(run())
