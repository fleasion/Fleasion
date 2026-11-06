"""Decrypt and rewrite genuine TLS 1.2 AES-GCM sessions with NSS master secrets."""

from __future__ import annotations

import asyncio
import socket
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.tls12_records import TLS12RecordCipher, TLS12_SUITES, derive_tls12_keys
from fleasion.proxy.upstream import UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


@pytest.mark.parametrize('cipher_name', ['ECDHE-RSA-AES128-GCM-SHA256', 'ECDHE-RSA-AES256-GCM-SHA384'])
@pytest.mark.parametrize('explicit', [True, False])
def test_tls12_real_handshake_and_modified_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cipher_name: str, explicit: bool
) -> None:
    host = 'tls12-splice.example.test'
    ca, ca_key = generate_ca(tmp_path)
    cert, key = generate_host_cert(host, ca, ca_key, tmp_path)
    keylog = tmp_path / 'tls12.keys'
    monkeypatch.setenv('FLEASION_SPLICE_KEYLOG', str(keylog))

    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    server_context.minimum_version = server_context.maximum_version = ssl.TLSVersion.TLSv1_2
    server_context.set_ciphers(cipher_name)
    server_context.keylog_filename = str(keylog)
    client_context = ssl.create_default_context(cafile=str(ca))
    client_context.minimum_version = client_context.maximum_version = ssl.TLSVersion.TLSv1_2
    client_context.set_ciphers(cipher_name)

    seen_requests: list[bytes] = []
    decrypted_hosts: list[str] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b'\r\n\r\n')
            size = int(next(x.split(b':', 1)[1] for x in headers.lower().split(b'\r\n') if x.startswith(b'content-length:')))
            body = await reader.readexactly(size)
            seen_requests.append(body)
            response = b'upstream-original'
            writer.write(b'HTTP/1.1 200 OK\r\n' + f'Content-Length: {len(response)}\r\n'.encode() + b'Connection: close\r\n\r\n' + response)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def client(port: int) -> bytes:
        with socket.create_connection(('127.0.0.1', port), timeout=6) as wire:
            if explicit:
                wire.sendall(f'CONNECT {host}:443 HTTP/1.1\r\n\r\n'.encode())
                response = bytearray()
                while not response.endswith(b'\r\n\r\n'):
                    response.extend(wire.recv(1))
                assert response.startswith(b'HTTP/1.1 200 ')
            with client_context.wrap_socket(wire, server_hostname=host) as tls:
                assert tls.version() == 'TLSv1.2'
                body = b'client-original'
                tls.sendall(b'POST /api HTTP/1.1\r\n' + f'Host: {host}\r\nContent-Length: {len(body)}\r\n\r\n'.encode() + body)
                result = bytearray()
                while part := tls.recv(65536):
                    result.extend(part)
                return bytes(result)

    async def run() -> None:
        server = await asyncio.start_server(upstream, '127.0.0.1', 0, ssl=server_context)
        port = server.sockets[0].getsockname()[1]
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={}, port=0,
            explicit_proxy=explicit, tls_splice=True, intercept_hosts={host},
            upstream_endpoints={host: [UpstreamEndpoint(host, port, '127.0.0.1')]},
            auto_replace_rules=[
                {'direction': 'request', 'match': 'client-original', 'replacement': 'client-modified'},
                {'direction': 'response', 'match': 'upstream-original', 'replacement': 'client-replaced'},
            ],
        )

        def capture_decryption(name: str) -> bool:
            decrypted_hosts.append(name)
            return True

        monkeypatch.setattr(proxy, 'mark_splice_decrypted', capture_decryption)
        try:
            await proxy.start()
            result = await asyncio.wait_for(asyncio.to_thread(client, proxy.port), timeout=8)
            assert result.endswith(b'\r\n\r\nclient-replaced')
            assert seen_requests == [b'client-modified']
            assert decrypted_hosts == [host]
        finally:
            await proxy.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_tls12_record_round_trip_and_nonce_separation() -> None:
    for cipher in TLS12_SUITES:
        master = bytes(range(48))
        client_random = bytes(range(32))
        server_random = bytes(range(32, 64))
        client, server, client_iv, server_iv = derive_tls12_keys(master, cipher, client_random, server_random)
        assert client != server
        assert client_iv != server_iv
        sender = TLS12RecordCipher(client, client_iv, cipher, sequence=2)
        receiver = TLS12RecordCipher(client, client_iv, cipher, sequence=2)
        record = sender.encrypt(b'tls12 payload')
        assert receiver.decrypt(record) == (23, b'tls12 payload')
        assert sender.encrypt(b'tls12 payload')[5:13] != record[5:13]
        assert sender.sequence == receiver.sequence + 1


def test_tls12_http2_feature_bridge_with_real_aes_gcm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TLS 1.2 record handling also feeds the existing multiplexed HTTP/2 engine."""
    from h2.config import H2Configuration
    from h2.connection import H2Connection
    from h2.events import DataReceived, RequestReceived, StreamEnded

    host = 'tls12-h2.example.test'
    ca, ca_key = generate_ca(tmp_path)
    cert, key = generate_host_cert(host, ca, ca_key, tmp_path)
    keylog = tmp_path / 'tls12-h2.keys'
    monkeypatch.setenv('FLEASION_SPLICE_KEYLOG', str(keylog))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers('ECDHE-RSA-AES128-GCM-SHA256')
    context.set_alpn_protocols(['h2'])
    context.keylog_filename = str(keylog)
    upstream_bodies: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        h2 = H2Connection(H2Configuration(client_side=False))
        h2.initiate_connection()
        writer.write(h2.data_to_send())
        requests: dict[int, bytearray] = {}
        try:
            while raw := await reader.read(65536):
                for event in h2.receive_data(raw):
                    if isinstance(event, RequestReceived):
                        requests[event.stream_id] = bytearray()
                    elif isinstance(event, DataReceived):
                        requests[event.stream_id].extend(event.data)
                        h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                    elif isinstance(event, StreamEnded):
                        upstream_bodies.append(bytes(requests.pop(event.stream_id)))
                        h2.send_headers(event.stream_id, [(b':status', b'200')])
                        h2.send_data(event.stream_id, b'upstream-original', end_stream=True)
                writer.write(h2.data_to_send())
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def client(port: int) -> bytes:
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers('ECDHE-RSA-AES128-GCM-SHA256')
        context.set_alpn_protocols(['h2'])
        with socket.create_connection(('127.0.0.1', port), timeout=6) as raw:
            raw.sendall(f'CONNECT {host}:443 HTTP/1.1\r\n\r\n'.encode())
            response = bytearray()
            while not response.endswith(b'\r\n\r\n'):
                response.extend(raw.recv(1))
            assert response.startswith(b'HTTP/1.1 200 ')
            with context.wrap_socket(raw, server_hostname=host) as tls:
                assert tls.version() == 'TLSv1.2'
                assert tls.selected_alpn_protocol() == 'h2'
                h2 = H2Connection()
                h2.initiate_connection()
                h2.send_headers(1, [
                    (b':method', b'POST'),
                    (b':scheme', b'https'),
                    (b':authority', host.encode()),
                    (b':path', b'/h2'),
                    (b'content-length', b'15'),
                ])
                h2.send_data(1, b'client-original', end_stream=True)
                tls.sendall(h2.data_to_send())
                content = bytearray()
                while True:
                    data = tls.recv(65536)
                    assert data, 'TLS 1.2 HTTP/2 session closed before completing the response'
                    complete = False
                    for event in h2.receive_data(data):
                        if isinstance(event, DataReceived):
                            content.extend(event.data)
                            h2.acknowledge_received_data(event.flow_controlled_length, event.stream_id)
                        elif isinstance(event, StreamEnded):
                            complete = True
                    if complete:
                        return bytes(content)
                    tls.sendall(h2.data_to_send())

    async def run() -> None:
        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0, ssl=context)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={}, port=0, explicit_proxy=True, tls_splice=True,
            intercept_hosts={host},
            upstream_endpoints={host: [UpstreamEndpoint(host, upstream_port, '127.0.0.1')]},
            auto_replace_rules=[
                {'direction': 'request', 'match': 'client-original', 'replacement': 'client-modified'},
                {'direction': 'response', 'match': 'upstream-original', 'replacement': 'client-replaced'},
            ],
        )
        try:
            await proxy.start()
            result = await asyncio.wait_for(asyncio.to_thread(client, proxy.port), timeout=8)
            assert result == b'client-replaced'
            assert upstream_bodies == [b'client-modified']
        finally:
            await proxy.stop()
            upstream_server.close()
            await upstream_server.wait_closed()

    asyncio.run(run())
