"""Unsupported TLS cipher negotiation must never break a client's connection."""

from __future__ import annotations

import asyncio
import socket
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.splice_keys import KeyProvider
from fleasion.proxy.upstream import UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


@pytest.mark.parametrize('explicit_proxy', [True, False])
@pytest.mark.parametrize(('cipher_name', 'secret_error'), [
    ('ECDHE-RSA-AES128-SHA256', ''),
    ('ECDHE-RSA-AES128-GCM-SHA256', 'missing'),
    ('ECDHE-RSA-AES128-GCM-SHA256', 'incorrect'),
])
def test_actual_tls12_is_relayed_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_proxy: bool,
    cipher_name: str, secret_error: str
) -> None:
    if secret_error:
        async def unavailable_secret(
            _provider: KeyProvider, _random: str, _label: str, *, wait_seconds: float = 10
        ) -> bytes:
            if secret_error == 'incorrect':
                return bytes(range(48))
            raise TimeoutError('TLS 1.2 master secret unavailable')

        monkeypatch.setattr(KeyProvider, 'get', unavailable_secret)
    host = 'tls12.fleasion-test.invalid'
    ca, key = generate_ca(tmp_path)
    cert, leaf_key = generate_host_cert(host, ca, key, tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, leaf_key)
    server_context.minimum_version = ssl.TLSVersion.TLSv1_2
    server_context.maximum_version = ssl.TLSVersion.TLSv1_2
    server_context.set_ciphers(cipher_name)
    client_context = ssl.create_default_context(cafile=str(ca))
    client_context.minimum_version = ssl.TLSVersion.TLSv1_2
    client_context.maximum_version = ssl.TLSVersion.TLSv1_2
    client_context.set_ciphers(cipher_name)
    captured: list[bytes] = []
    decrypted: list[str] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=4)
            captured.append(request)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\nTLS12-SERVED')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def client(port: int) -> bytes:
        with socket.create_connection(('127.0.0.1', port), timeout=5) as wire:
            if explicit_proxy:
                wire.sendall(f'CONNECT {host}:443 HTTP/1.1\r\n\r\n'.encode())
                response = bytearray()
                while not response.endswith(b'\r\n\r\n'):
                    response.extend(wire.recv(1))
                assert response.startswith(b'HTTP/1.1 200 ')
            with client_context.wrap_socket(wire, server_hostname=host) as tls:
                assert tls.version() == 'TLSv1.2'
                tls.sendall(f'GET /asset HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode())
                result = bytearray()
                while True:
                    part = tls.recv(65536)
                    if not part:
                        break
                    result.extend(part)
                return bytes(result)

    async def run() -> None:
        server = await asyncio.start_server(upstream, '127.0.0.1', 0, ssl=server_context)
        upstream_port = server.sockets[0].getsockname()[1]
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace()),
            host_certs={}, port=0,
            explicit_proxy=explicit_proxy, tls_splice=True,
            intercept_hosts={host},
            upstream_endpoints={host: [UpstreamEndpoint(host, upstream_port, '127.0.0.1')]},
        )
        def capture_decrypted(name: str) -> bool:
            decrypted.append(name)
            return True

        monkeypatch.setattr(proxy, 'mark_splice_decrypted', capture_decrypted)
        try:
            await proxy.start()
            result = await asyncio.wait_for(asyncio.to_thread(client, proxy.port), timeout=9)
            assert result.endswith(b'\r\n\r\nTLS12-SERVED')
            assert captured == [f'GET /asset HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode()]
            assert decrypted == []
        finally:
            await proxy.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(run())
