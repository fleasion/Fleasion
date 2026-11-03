"""Keep authenticated HTTP/1 connections synchronized after bodyless responses."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def _empty_replacements() -> tuple[
    dict[str, str], set[str], dict[str, str], dict[str, str]
]:
    return {}, set(), {}, {}


@pytest.mark.parametrize('method,status', [(b'HEAD', 200), (b'GET', 304), (b'GET', 204)])
def test_spliced_bodyless_response_does_not_consume_next_response(
    method: bytes, status: int
) -> None:
    async def run() -> None:
        seen: list[bytes] = []
        responses: list[bytes] = []
        host = 'apis.roblox.com'
        proxy = FleasionProxy(
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
            intercept_hosts={host},
            tls_splice=True,
        )

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                first = await reader.readuntil(b'\r\n\r\n')
                seen.append(first.split(b' ', 1)[0])
                writer.write(
                    f'HTTP/1.1 {status} Test\r\nContent-Length: 37\r\n'
                    'Connection: keep-alive\r\n\r\n'.encode('ascii')
                )
                await writer.drain()
                second = await reader.readuntil(b'\r\n\r\n')
                seen.append(second.split(b' ', 1)[0])
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
                await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]

        sessions: set[asyncio.Task[None]] = set()

        async def downstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            if task is not None:
                sessions.add(task)
            source, sink = await asyncio.open_connection('127.0.0.1', upstream_port)
            try:
                await proxy.serve_decrypted_http_session(
                    reader,
                    writer,
                    host,
                    UpstreamConnectResult(
                        reader=source,
                        writer=sink,
                        method='authenticated_test',
                        endpoint=host,
                    ),
                )
            finally:
                writer.close()
                sink.close()
                await writer.wait_closed()
                await sink.wait_closed()
                if task is not None:
                    sessions.discard(task)

        proxy_server = await asyncio.start_server(downstream, '127.0.0.1', 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
            try:
                writer.write(
                    method + b' / HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                    b'Connection: keep-alive\r\n\r\n'
                )
                await writer.drain()
                responses.append(await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1))
                writer.write(
                    b'GET /second HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                    b'Connection: close\r\n\r\n'
                )
                await writer.drain()
                responses.append(await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1))
                assert await asyncio.wait_for(reader.readexactly(2), 1) == b'OK'
                assert responses[0].startswith(f'HTTP/1.1 {status}'.encode())
                assert responses[1].startswith(b'HTTP/1.1 200')
                assert seen == [method, b'GET']
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            proxy_server.close()
            upstream_server.close()
            for task in sessions:
                task.cancel()
            await asyncio.gather(*sessions, return_exceptions=True)
            await proxy_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())
