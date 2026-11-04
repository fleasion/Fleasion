"""Unbatched asset replacement through the authenticated HTTP/2 feature bridge."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded

from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.config.types import ReplacementMaps
    from fleasion.proxy.addons.cache_scraper import CacheScraper

_HOST = 'assetdelivery.roblox.com'


class _Config:
    @staticmethod
    def get_all_replacements() -> ReplacementMaps:
        return {12345: 98765}, set(), {}, {}


@pytest.mark.parametrize(
    ('path', 'rewritten'),
    [
        (b'/v1/asset/?client=studio&id=12345&token=opaque', b'/v1/asset/?client=studio&id=98765&token=opaque'),
        (b'/v2/asset/?client=studio&id=12345&token=opaque', b'/v2/asset/?client=studio&id=98765&token=opaque'),
        (b'/v2/assetId/12345?format=source', b'/v2/assetId/98765?format=source'),
    ],
)
def test_h2_asset_delivery_rewrites_unbatched_id_before_forwarding(
    tmp_path: Path, path: bytes, rewritten: bytes
) -> None:
    async def run() -> None:
        ca, key = generate_ca(tmp_path)
        default_cert = generate_multi_host_cert('h2-asset', {_HOST}, ca, key, tmp_path)
        proxy = FleasionProxy(
            texture_stripper=TextureStripper(cast('ConfigManager', _Config())),
            cache_scraper=cast('CacheScraper', type('Scraper', (), {'enabled': False})()),
            host_certs={},
            default_cert=default_cert,
            intercept_hosts={_HOST},
            intercept_all_hosts=True,
        )
        forwarded_paths: list[bytes] = []
        forwarded_headers: list[dict[bytes, bytes]] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, RequestReceived):
                            headers = dict(event.headers)
                            forwarded_headers.append(headers)
                            forwarded_paths.append(headers[b':path'])
                        elif isinstance(event, StreamEnded):
                            h2.send_headers(
                                event.stream_id,
                                [(b':status', b'200'), (b'content-length', b'2')],
                            )
                            h2.send_data(event.stream_id, b'OK', end_stream=True)
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]

        async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            up_reader, up_writer = await asyncio.open_connection('127.0.0.1', upstream_port)
            upstream_connection = UpstreamConnectResult(
                reader=up_reader,
                writer=up_writer,
                endpoint='127.0.0.1',
                method='authenticated_h2_test',
            )
            try:
                await proxy.serve_decrypted_h2_session(
                    reader, writer, _HOST, upstream_connection
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge, '127.0.0.1', 0)
        bridge_port = bridge_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', bridge_port)
            h2 = H2Connection()
            h2.initiate_connection()
            stream = h2.get_next_available_stream_id()
            h2.send_headers(
                stream,
                [
                    (b':method', b'GET'),
                    (b':scheme', b'https'),
                    (b':authority', _HOST.encode('ascii')),
                    (b':path', path),
                    (b'x-sentinel', b'header-kept'),
                ],
                end_stream=True,
            )
            writer.write(h2.data_to_send())
            await writer.drain()
            response = bytearray()
            complete = False

            async def collect() -> None:
                nonlocal complete
                while not complete:
                    data = await reader.read(65536)
                    assert data, 'Bridge disconnected before the response was complete'
                    for event in h2.receive_data(data):
                        if isinstance(event, ResponseReceived):
                            assert dict(event.headers)[b':status'] == b'200'
                        elif isinstance(event, DataReceived):
                            response.extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            complete = True
                    writer.write(h2.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=8)
            assert response == b'OK'
            assert forwarded_paths == [rewritten]
            assert len(forwarded_headers) == 1
            assert forwarded_headers[0][b'x-sentinel'] == b'header-kept'
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            upstream_server.close()
            await bridge_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())
