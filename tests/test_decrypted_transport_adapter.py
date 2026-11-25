"""Exercise the shared HTTP feature engine with controlled plaintext streams."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.tls_backends import (
    AuthenticatedSession,
    BackendId,
    dispatch_authenticated_session,
)
from fleasion.proxy.tls_backends.health import HealthPhase, http1_roundtrip_health
from fleasion.proxy.types import AutoReplaceRule
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


def _proxy(
    cert_dir: Path,
    *,
    intercept_all_hosts: bool = False,
    auto_replace_rules: list[AutoReplaceRule] | None = None,
) -> FleasionProxy:
    ca, key = generate_ca(cert_dir)
    default_cert = generate_multi_host_cert(
        'controlled-local-test', {'apis.roblox.com'}, ca, key, cert_dir
    )
    return FleasionProxy(
        texture_stripper=cast(
            'TextureStripper',
            SimpleNamespace(
                config_manager=SimpleNamespace(
                    get_all_replacements=_empty_replacements
                )
            ),
        ),
        cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
        host_certs={},
        default_cert=default_cert,
        intercept_hosts={'apis.roblox.com'},
        intercept_all_hosts=intercept_all_hosts,
        auto_replace_rules=auto_replace_rules,
    )


@pytest.mark.parametrize('framing', ['length', 'eof'])
def test_decrypted_http_session_reuses_existing_replacement_engine(
    tmp_path: Path, framing: str
) -> None:
    async def run() -> None:
        upstream_requests: list[bytes] = []

        async def local_upstream(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                body_size = int(
                    next(
                        line.split(b':', 1)[1].strip()
                        for line in headers.lower().split(b'\r\n')
                        if line.startswith(b'content-length:')
                    )
                )
                upstream_requests.append(await reader.readexactly(body_size))
                payload = b'upstream-original'
                framing_header = (
                    f'Content-Length: {len(payload)}\r\n'.encode()
                    if framing == 'length'
                    else b''
                )
                writer.write(
                    b'HTTP/1.1 200 OK\r\n'
                    + framing_header
                    + b'Connection: close\r\n\r\n'
                    + payload
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(local_upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)
        proxy = _proxy(
            tmp_path,
            intercept_all_hosts=True,
            auto_replace_rules=[
                {'direction': 'request', 'match': 'original', 'replacement': 'modified'},
                {'direction': 'response', 'match': 'upstream', 'replacement': 'client'},
            ],
        )

        async def serve_decrypted(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                '127.0.0.1', upstream_port
            )
            try:
                await dispatch_authenticated_session(
                    proxy,
                    AuthenticatedSession(
                        hostname='apis.roblox.com',
                        alpn='http/1.1',
                        client_reader=reader,
                        client_writer=writer,
                        upstream=UpstreamConnectResult(
                            reader=upstream_reader,
                            writer=upstream_writer,
                            method='controlled_local_test',
                            endpoint='127.0.0.1',
                        ),
                        server_authenticated=True,
                    ),
                    expected_hostname='apis.roblox.com',
                )
            finally:
                writer.close()
                await writer.wait_closed()

        adapter_server = await asyncio.start_server(serve_decrypted, '127.0.0.1', 0)
        adapter_port = adapter_server.sockets[0].getsockname()[1]
        assert isinstance(adapter_port, int)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', adapter_port)
            writer.write(
                b'POST /asset HTTP/1.1\r\n'
                b'Host: apis.roblox.com\r\n'
                b'Content-Length: 8\r\n'
                b'Connection: close\r\n\r\n'
                b'original'
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=5.0)
            writer.close()
            await writer.wait_closed()
            assert response.startswith(b'HTTP/1.1 200 OK\r\n')
            assert response.endswith(b'client-original')
            # The positive health proof comes from this real localhost proxy exchange
            health = http1_roundtrip_health(
                BackendId.WINDOWS,
                AuthenticatedSession(
                    hostname='apis.roblox.com',
                    alpn='http/1.1',
                    client_reader=reader,
                    client_writer=writer,
                    upstream=UpstreamConnectResult(
                        reader=asyncio.StreamReader(),
                        writer=writer,
                        method='synthetic_loopback',
                        endpoint='127.0.0.1',
                    ),
                    server_authenticated=True,
                ),
                hostname='apis.roblox.com',
                expected_challenge=b'client-original',
                raw_response=response,
            )
            assert health.phase is HealthPhase.END_TO_END_VERIFIED
            assert upstream_requests == [b'modified']
            requests = proxy.get_request_log()
            assert len(requests) == 1
            assert requests[0]['host'] == 'apis.roblox.com'
        finally:
            adapter_server.close()
            upstream_server.close()
            await adapter_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


@pytest.mark.parametrize('invalid', ['unauthorized', 'no-upstream', 'http2', 'host-mismatch'])
def test_decrypted_http_adapter_rejects_unsupported_inputs(
    invalid: str, tmp_path: Path
) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        reader = asyncio.StreamReader()
        if invalid == 'host-mismatch':
            reader.feed_data(
                b'GET / HTTP/1.1\r\nHost: other.example\r\n\r\n'
            )
        else:
            reader.feed_data(b'PRI * HTTP/2.0\r\n\r\n')
        reader.feed_eof()
        upstream = UpstreamConnectResult(
            reader=asyncio.StreamReader(),
            writer=cast('asyncio.StreamWriter', object()),
            method='controlled_local_test',
            endpoint='127.0.0.1',
        )
        if invalid == 'no-upstream':
            upstream.reader = None
        host = 'other.example' if invalid == 'unauthorized' else 'apis.roblox.com'
        try:
            with pytest.raises(ValueError):
                await proxy.serve_decrypted_http_session(
                    reader, cast('asyncio.StreamWriter', object()), host, upstream
                )
        finally:
            await proxy.stop()

    asyncio.run(run())


def test_decrypted_http_adapter_rejects_host_switch_on_keepalive(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        received: list[bytes] = []
        errors: list[str] = []

        async def upstream_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                received.append(await reader.readuntil(b'\r\n\r\n'))
                writer.write(
                    b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n'
                    b'Connection: keep-alive\r\n\r\n'
                )
                await writer.drain()
                await reader.read()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream_handler, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def adapter_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                '127.0.0.1', upstream_port
            )
            try:
                await proxy.serve_decrypted_http_session(
                    reader,
                    writer,
                    'apis.roblox.com',
                    UpstreamConnectResult(
                        reader=upstream_reader,
                        writer=upstream_writer,
                        method='controlled_local_test',
                        endpoint='127.0.0.1',
                    ),
                )
            except ValueError as exc:
                errors.append(str(exc))
            finally:
                writer.close()
                await writer.wait_closed()

        adapter_server = await asyncio.start_server(adapter_handler, '127.0.0.1', 0)
        adapter_port = adapter_server.sockets[0].getsockname()[1]
        assert isinstance(adapter_port, int)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', adapter_port)
            writer.write(
                b'GET /first HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Connection: keep-alive\r\n\r\n'
                b'GET /second HTTP/1.1\r\nHost: other.example\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), timeout=5.0)
            writer.close()
            await writer.wait_closed()
            assert response.startswith(b'HTTP/1.1 200 OK\r\n')
            assert len(received) == 1
            assert errors == ['Request Host does not match authenticated session host']
        finally:
            adapter_server.close()
            upstream_server.close()
            await adapter_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())
