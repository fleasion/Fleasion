from __future__ import annotations

import asyncio
import contextlib
import socket
import ssl
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, InformationalResponseReceived, RequestReceived, StreamEnded

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert

if TYPE_CHECKING:
    from pathlib import Path

    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


@pytest.mark.parametrize('goaway', [True, False])
@pytest.mark.parametrize('explicit', [True, False])
@pytest.mark.parametrize('stream_ids', [(1, 3), (1, 3, 5, 7)])
@pytest.mark.parametrize('informational', [False, True])
def test_tls_h2_multiplexed_replacements_and_flow_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool, goaway: bool,
    stream_ids: tuple[int, ...], informational: bool,
) -> None:
    host = 'h2-splice.test'
    ca, key = generate_ca(tmp_path)
    cert, server_key = generate_host_cert(host, ca, key, tmp_path)
    keylog = tmp_path / 'test.keys'
    monkeypatch.setenv('FLEASION_SPLICE_KEYLOG', str(keylog))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, server_key)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(['h2'])
    context.keylog_filename = str(keylog)
    incoming: list[bytes] = []
    replacement = 'x' * 150000
    request_body = b'original' * 12000

    async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = H2Connection(H2Configuration(client_side=False))
        connection.initiate_connection()
        writer.write(connection.data_to_send())
        bodies: dict[int, bytearray] = {}
        try:
            while data := await reader.read(65536):
                for event in connection.receive_data(data):
                    if isinstance(event, RequestReceived):
                        bodies[event.stream_id] = bytearray()
                    elif isinstance(event, DataReceived):
                        bodies[event.stream_id].extend(event.data)
                        connection.acknowledge_received_data(
                            event.flow_controlled_length, event.stream_id
                        )
                    elif isinstance(event, StreamEnded):
                        incoming.append(bytes(bodies.pop(event.stream_id)))
                        if informational:
                            connection.send_headers(
                                event.stream_id,
                                [(b':status', b'103'), (b'link', b'</static.css>; rel=preload')],
                            )
                        connection.send_headers(
                            event.stream_id,
                            [(b':status', b'200')]
                            + ([(b'content-length', b'8')] if event.stream_id == 1 else []),
                        )
                        connection.send_data(event.stream_id, b'original', end_stream=True)
                if goaway and len(incoming) == len(stream_ids):
                    connection.close_connection()
                writer.write(connection.data_to_send())
                await writer.drain()
                if goaway and len(incoming) == len(stream_ids):
                    break
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    def client(port: int) -> tuple[dict[int, bytes], dict[int, int]]:
        ssl_context = ssl.create_default_context(cafile=str(ca))
        ssl_context.set_alpn_protocols(['h2'])
        with socket.create_connection(('127.0.0.1', port), timeout=10) as raw:
            if explicit:
                raw.sendall(f'CONNECT {host}:443 HTTP/1.1\r\n\r\n'.encode())
                reply = bytearray()
                while not reply.endswith(b'\r\n\r\n'):
                    reply.extend(raw.recv(1))
                assert reply.startswith(b'HTTP/1.1 200')
            with ssl_context.wrap_socket(raw, server_hostname=host) as tls:
                assert tls.selected_alpn_protocol() == 'h2'
                connection = H2Connection()
                connection.initiate_connection()
                for stream in stream_ids:
                    connection.send_headers(
                        stream,
                        [
                            (b':method', b'POST'),
                            (b':scheme', b'https'),
                            (b':authority', host.encode()),
                            (b':path', b'/asset'),
                            (b'content-length', str(len(request_body)).encode()),
                        ],
                    )
                offsets = dict.fromkeys(stream_ids, 0)
                responses = {stream: bytearray() for stream in stream_ids}
                informational_counts = dict.fromkeys(stream_ids, 0)
                done: set[int] = set()
                while len(done) < len(stream_ids):
                    for stream in stream_ids:
                        remaining = len(request_body) - offsets[stream]
                        while remaining and connection.local_flow_control_window(stream) > 0:
                            size = min(
                                remaining,
                                connection.local_flow_control_window(stream),
                                connection.max_outbound_frame_size,
                            )
                            start = offsets[stream]
                            offsets[stream] += size
                            remaining -= size
                            connection.send_data(
                                stream, request_body[start : start + size], end_stream=not remaining
                            )
                    tls.sendall(connection.data_to_send())
                    data = tls.recv(65536)
                    assert data
                    for event in connection.receive_data(data):
                        if isinstance(event, DataReceived):
                            responses[event.stream_id].extend(event.data)
                            connection.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, InformationalResponseReceived):
                            informational_counts[event.stream_id] += 1
                        elif isinstance(event, StreamEnded):
                            done.add(event.stream_id)
                return ({stream: bytes(body) for stream, body in responses.items()},
                        informational_counts)

    async def run() -> None:
        upstream_srv = await asyncio.start_server(server, '127.0.0.1', 0, ssl=context)
        port = upstream_srv.sockets[0].getsockname()[1]
        from types import SimpleNamespace

        texture = cast(
            'TextureStripper',
            SimpleNamespace(
                config_manager=SimpleNamespace(
                    get_all_replacements=lambda: (
                        dict[str, str](),
                        set[str](),
                        dict[str, str](),
                        dict[str, str](),
                    )
                )
            ),
        )
        scraper = cast('CacheScraper', SimpleNamespace(enabled=False))
        proxy = FleasionProxy(
            texture_stripper=texture,
            cache_scraper=scraper,
            host_certs={},
            port=0,
            explicit_proxy=explicit,
            tls_splice=True,
            intercept_hosts={host},
            intercept_all_hosts=True,
            upstream_endpoints={host: [UpstreamEndpoint(host, port, '127.0.0.1')]},
            auto_replace_rules=[
                {'direction': 'request', 'match': 'original', 'replacement': 'rewritten'},
                {'direction': 'response', 'match': 'original', 'replacement': replacement},
            ],
        )
        try:
            await proxy.start()
            responses, hints = await asyncio.wait_for(asyncio.to_thread(client, proxy.port), timeout=15)
            assert responses == dict.fromkeys(stream_ids, replacement.encode())
            assert hints == dict.fromkeys(stream_ids, int(informational))
            assert incoming == [b'rewritten' * 12000] * len(stream_ids)
        finally:
            await proxy.stop()
            upstream_srv.close()
            await upstream_srv.wait_closed()

    asyncio.run(run())
