"""Hosts-file game-join relay regression tests."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import GAMEJOIN_HOST, FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


async def _round_trip_gamejoin(
    *, interceptors: bool, header_only: bool = False, chunked: bool = False
) -> tuple[bytes, bytes]:
    """Exercise the game-join HTTP engine using authenticated stream fixtures."""
    body = b'4\r\nbody\r\n0\r\nX-Trailer: protected\r\n\r\n' if chunked else b'body'
    framing = b'Transfer-Encoding: chunked\r\n' if chunked else b'Content-Length: 4\r\n'
    request = (
        b'POST /v1/join HTTP/1.1\r\n'
        b'Host: gamejoin.roblox.com\r\n'
        b'X-Join-Token: first\r\n'
        b'X-Join-Token: second\r\n'
        + framing
        + b'Connection: close\r\n'
        b'\r\n'
        + body
    )
    response = (
        b'HTTP/1.1 200 OK\r\n'
        b'X-Join-Token: first\r\n'
        b'X-Join-Token: second\r\n'
        b'Content-Length: 2\r\n'
        b'Connection: close\r\n'
        b'\r\nok'
    )
    observed: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        header = await reader.readuntil(b'\r\n\r\n')
        upstream_body = await reader.readexactly(len(body))
        observed.append(header + upstream_body)
        writer.write(response)
        await writer.drain()
        writer.close()

    proxy = FleasionProxy(
        texture_stripper=cast('TextureStripper', SimpleNamespace()),
        cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
        host_certs={},
        tls_splice=True,
        intercept_hosts={GAMEJOIN_HOST},
        wire_preserving_passthrough=False,
    )
    if interceptors:
        class NoOpInterceptor:
            def request(self, flow: object) -> None:
                if header_only:
                    from fleasion.proxy.server import ProxyFlow

                    assert isinstance(flow, ProxyFlow)
                    flow.request.headers['X-Modified'] = 'yes'

            def response(self, flow: object) -> None:
                del flow

        proxy.set_module_interceptors([NoOpInterceptor()])

    upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
    port = upstream_server.sockets[0].getsockname()[1]

    async def relay(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        up_reader, up_writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            await proxy.serve_decrypted_http_session(
                reader, writer, GAMEJOIN_HOST,
                UpstreamConnectResult(up_reader, up_writer, 'authenticated_test', 'loopback'),
            )
        finally:
            up_writer.close()
            writer.close()

    relay_server = await asyncio.start_server(relay, '127.0.0.1', 0)
    relay_port = relay_server.sockets[0].getsockname()[1]
    try:
        client_reader, client_writer = await asyncio.open_connection('127.0.0.1', relay_port)
        client_writer.write(request)
        await client_writer.drain()
        delivered = await asyncio.wait_for(client_reader.read(), timeout=3)
        client_writer.close()
        await client_writer.wait_closed()
        assert observed
        return observed[0], delivered
    finally:
        relay_server.close()
        upstream_server.close()
        await relay_server.wait_closed()
        await upstream_server.wait_closed()
        await proxy.stop()


def test_gamejoin_untouched_wire_preserved_without_interceptors() -> None:
    request, response = asyncio.run(_round_trip_gamejoin(interceptors=False))
    assert request.count(b'X-Join-Token:') == 2
    assert response.count(b'X-Join-Token:') == 2


def test_gamejoin_untouched_wire_preserved_with_noop_interceptor() -> None:
    request, response = asyncio.run(_round_trip_gamejoin(interceptors=True))
    assert request.count(b'X-Join-Token:') == 2
    assert response.count(b'X-Join-Token:') == 2


def test_gamejoin_header_only_feature_edit_reaches_upstream() -> None:
    request, _response = asyncio.run(
        _round_trip_gamejoin(interceptors=True, header_only=True)
    )
    assert b'x-modified: yes' in request.lower()


def test_gamejoin_chunked_request_with_trailers_passes_when_unmodified() -> None:
    request, response = asyncio.run(
        _round_trip_gamejoin(interceptors=False, chunked=True)
    )
    assert b'X-Trailer: protected' in request
    assert b'Transfer-Encoding: chunked' in request
    assert response.count(b'X-Join-Token:') == 2


@pytest.mark.parametrize('tls_version', ['TLSv1_2', 'TLSv1_3'])
def test_hosts_direct_tls_splice_gamejoin_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tls_version: str
) -> None:
    """Pass a game-join HTTP request through the real direct TLS splice."""
    import ssl

    from fleasion.proxy.upstream import UpstreamEndpoint
    from fleasion.utils.certs import generate_ca, generate_host_cert

    keylog_file = tmp_path / 'client-keys.log'
    monkeypatch.setenv('FLEASION_SPLICE_KEYLOG', str(keylog_file))
    ca_cert, ca_key = generate_ca(tmp_path)
    host_cert, host_key = generate_host_cert(GAMEJOIN_HOST, ca_cert, ca_key, tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(host_cert), str(host_key))
    version = getattr(ssl.TLSVersion, tls_version)
    server_context.minimum_version = version
    server_context.maximum_version = version
    server_context.set_alpn_protocols(['http/1.1'])
    client_context = ssl.create_default_context(cafile=str(ca_cert))
    client_context.minimum_version = version
    client_context.maximum_version = version
    client_context.set_alpn_protocols(['http/1.1'])
    client_context.keylog_filename = str(keylog_file)
    seen: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers = await reader.readuntil(b'\r\n\r\n')
            body = await reader.readexactly(4)
            seen.append(headers + body)
            writer.write(
                b'HTTP/1.1 200 OK\r\n'
                b'X-Join-Token: first\r\n'
                b'X-Join-Token: second\r\n'
                b'Content-Length: 2\r\nConnection: close\r\n\r\nok'
            )
            await writer.drain()
        finally:
            writer.close()

    async def run() -> None:
        remote = await asyncio.start_server(
            upstream, '127.0.0.1', 0, ssl=server_context
        )
        remote_port = remote.sockets[0].getsockname()[1]
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={},
            port=0,
            tls_splice=True,
            intercept_hosts={GAMEJOIN_HOST},
            wire_preserving_passthrough=False,
            upstream_endpoints={
                GAMEJOIN_HOST: [UpstreamEndpoint(GAMEJOIN_HOST, remote_port, '127.0.0.1')]
            },
        )
        try:
            await proxy.start()
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    '127.0.0.1', proxy.port,
                    ssl=client_context, server_hostname=GAMEJOIN_HOST,
                ),
                timeout=5,
            )
            try:
                writer.write(
                    b'POST /v1/join HTTP/1.1\r\n'
                    b'Host: gamejoin.roblox.com\r\n'
                    b'X-Join-Token: first\r\n'
                    b'X-Join-Token: second\r\n'
                    b'Content-Length: 4\r\nConnection: close\r\n\r\nbody'
                )
                await writer.drain()
                headers = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
                payload = await asyncio.wait_for(reader.readexactly(2), timeout=10)
                assert headers.startswith(b'HTTP/1.1 200 OK')
                assert headers.count(b'X-Join-Token:') == 2
                assert payload == b'ok'
                assert len(seen) == 1
                assert seen[0].count(b'X-Join-Token:') == 2
                assert GAMEJOIN_HOST in cast('set[str]', getattr(proxy, '_splice_decrypted_hosts'))
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await proxy.stop()
            remote.close()
            await remote.wait_closed()

    asyncio.run(run())
