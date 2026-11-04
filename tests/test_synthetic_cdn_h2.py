"""Opaque asset CDN routes work over decrypted HTTP/2 streams."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

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

_HOST = 'fts.rbxcdn.com'


def test_h2_repeat_get_head_and_revoked_synthetic_route(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('FLEASION_ASSET_SYNTHETIC_CDN', '1')

    async def run() -> None:
        TextureStripper.reset_routes()
        content = tmp_path / 'replacement.audio'
        content.write_bytes(b'H2-REPLACEMENT-TEST')

        class _LocalConfig:
            @staticmethod
            def get_all_replacements() -> ReplacementMaps:
                return {}, set(), {}, {111: str(content)}

        stripper = TextureStripper(cast('ConfigManager', _LocalConfig()))
        request = b'[{"requestId":"A","assetId":111,"assetType":"Audio"}]'
        original = json.dumps(
            [{'requestId': 'A', 'location': f'https://{_HOST}/original-hash?__token__=hidden'}]
        ).encode()
        stripper.process_batch_request(request, {}, _LocalConfig.get_all_replacements(), 'h2')
        rewritten = stripper.process_batch_response(request, original, {}, 'h2')
        assert rewritten is not None
        items = cast('list[dict[str, object]]', json.loads(rewritten))
        synthetic = urlsplit(cast('str', items[0]['location'])).path
        ca, key = generate_ca(tmp_path)
        cert = generate_multi_host_cert('synthetic-h2', {_HOST}, ca, key, tmp_path)
        proxy = FleasionProxy(
            texture_stripper=stripper,
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={},
            default_cert=cert,
            intercept_hosts={_HOST},
            intercept_all_hosts=True,
        )
        upstream_requests: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            peer = H2Connection(H2Configuration(client_side=False))
            peer.initiate_connection()
            writer.write(peer.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in peer.receive_data(data):
                        if isinstance(event, RequestReceived):
                            upstream_requests.append(dict(event.headers).get(b':path', b''))
                    writer.write(peer.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]

        async def bridge(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            up_reader, up_writer = await asyncio.open_connection('127.0.0.1', upstream_port)
            connection = UpstreamConnectResult(
                reader=up_reader,
                writer=up_writer,
                endpoint='127.0.0.1',
                method='controlled-local-upstream',
            )
            try:
                await proxy.serve_decrypted_h2_session(reader, writer, _HOST, connection)
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        server = await asyncio.start_server(bridge, '127.0.0.1', 0)
        port = server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            h2 = H2Connection()
            h2.initiate_connection()

            async def collect_streams(
                streams: dict[int, tuple[str, bytes]],
            ) -> dict[int, tuple[int, bytes]]:
                collected: dict[int, bytearray] = {sid: bytearray() for sid in streams}
                codes: dict[int, int] = {}
                ended: set[int] = set()
                for stream_id, (method, path) in streams.items():
                    h2.send_headers(
                        stream_id,
                        [
                            (b':method', method.encode()),
                            (b':scheme', b'https'),
                            (b':authority', _HOST.encode()),
                            (b':path', path),
                        ],
                        end_stream=True,
                    )
                writer.write(h2.data_to_send())
                await writer.drain()
                while ended != set(streams):
                    data = await asyncio.wait_for(reader.read(65536), timeout=5)
                    assert data, 'HTTP/2 bridge closed before all requests completed'
                    for event in h2.receive_data(data):
                        if isinstance(event, ResponseReceived):
                            codes[event.stream_id] = int(dict(event.headers)[b':status'])
                        elif isinstance(event, DataReceived):
                            collected[event.stream_id].extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            ended.add(event.stream_id)
                    writer.write(h2.data_to_send())
                    await writer.drain()
                return {sid: (codes[sid], bytes(collected[sid])) for sid in streams}

            try:
                streams = {
                    h2.get_next_available_stream_id(): ('GET', synthetic.encode()),
                }
                first = await collect_streams(streams)
                assert list(first.values()) == [(200, b'H2-REPLACEMENT-TEST')]
                streams2 = {h2.get_next_available_stream_id(): ('GET', synthetic.encode())}
                second = await collect_streams(streams2)
                assert list(second.values()) == [(200, b'H2-REPLACEMENT-TEST')]
                streams3 = {h2.get_next_available_stream_id(): ('HEAD', synthetic.encode())}
                head = await collect_streams(streams3)
                assert list(head.values()) == [(200, b'')]
                assert upstream_requests == []
                TextureStripper.reset_routes()
                streams4 = {h2.get_next_available_stream_id(): ('GET', synthetic.encode())}
                stale = await collect_streams(streams4)
                assert list(stale.values()) == [(410, b'')]
                assert upstream_requests == []
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            upstream_server.close()
            await server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()
            TextureStripper.reset_routes()

    asyncio.run(run())
