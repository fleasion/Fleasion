"""Loopback tests for bounded-memory CDN passthrough latency and framing."""

from __future__ import annotations

import asyncio
import gzip
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.addons.cache_scraper import CacheScraper

_HOST = 'fts.rbxcdn.com'
_HEADERS_END = b'\r\n\r\n'
_Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def _empty_replacements() -> tuple[dict[int, int], set[int], dict[int, str], dict[int, str]]:
    return {}, set(), {}, {}


@asynccontextmanager
async def _cdn_session(
    upstream_handler: _Handler,
    *,
    cache_enabled: bool = False,
    host: str = _HOST,
    request: bytes | None = None,
    replacements: tuple[dict[int, int], set[int], dict[int, str], dict[int, str]] | None = None,
    observed: list[TextureStripper] | None = None,
) -> AsyncGenerator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    TextureStripper.reset_routes()
    def replacement_maps() -> tuple[dict[int, int], set[int], dict[int, str], dict[int, str]]:
        return replacements if replacements is not None else _empty_replacements()

    stripper = TextureStripper(
        cast('ConfigManager', SimpleNamespace(get_all_replacements=replacement_maps))
    )
    if observed is not None:
        observed.append(stripper)
    cached: list[bytes] = []

    def record_cached(_url: str, _path: str, body: bytes, _content_type: str) -> None:
        cached.append(body)

    cache = cast(
        'CacheScraper',
        SimpleNamespace(
            enabled=cache_enabled,
            process_cdn_response=record_cached,
        ),
    )
    proxy = FleasionProxy(
        texture_stripper=stripper,
        cache_scraper=cache,
        host_certs={},
        tls_splice=True,
        intercept_hosts={host},
    )
    upstream_server = await asyncio.start_server(upstream_handler, '127.0.0.1', 0)
    upstream_port = upstream_server.sockets[0].getsockname()[1]
    sessions: set[asyncio.Task[None]] = set()

    async def downstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            sessions.add(task)
        try:
            up_reader, up_writer = await asyncio.open_connection('127.0.0.1', upstream_port)
            await proxy.serve_decrypted_http_session(
                reader,
                writer,
                host,
                UpstreamConnectResult(
                    reader=up_reader,
                    writer=up_writer,
                    endpoint='loopback',
                    method='authenticated_test',
                ),
            )
        finally:
            writer.close()
            await writer.wait_closed()
            if task is not None:
                sessions.discard(task)

    proxy_server = await asyncio.start_server(downstream, '127.0.0.1', 0)
    proxy_port = proxy_server.sockets[0].getsockname()[1]
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
        writer.write(
            request
            if request is not None
            else f'GET /asset HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode()
        )
        await writer.drain()
        try:
            yield reader, writer
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
        TextureStripper.reset_routes()


def test_unchanged_cdn_body_arrives_before_upstream_completion() -> None:
    async def run() -> None:
        release_tail = asyncio.Event()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(_HEADERS_END)
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\nFIRST-'
            )
            await writer.drain()
            await release_tail.wait()
            writer.write(b'SECOND')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with _cdn_session(upstream) as (reader, _writer):
            headers = await asyncio.wait_for(reader.readuntil(_HEADERS_END), timeout=2)
            assert b'Content-Length: 12' in headers
            assert await asyncio.wait_for(reader.readexactly(6), timeout=2) == b'FIRST-'
            release_tail.set()
            assert await asyncio.wait_for(reader.readexactly(6), timeout=2) == b'SECOND'

    asyncio.run(run())


def test_unchanged_chunked_cdn_stream_preserves_extensions_and_trailers() -> None:
    async def run() -> None:
        release_tail = asyncio.Event()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(_HEADERS_END)
            writer.write(
                b'HTTP/1.1 206 Partial Content\r\nTransfer-Encoding: chunked\r\n'
                b'Connection: close\r\n\r\n4;test=yes\r\nDATA\r\n'
            )
            await writer.drain()
            await release_tail.wait()
            writer.write(b'0\r\nX-Asset-Check: intact\r\n\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with _cdn_session(upstream) as (reader, _writer):
            headers = await asyncio.wait_for(reader.readuntil(_HEADERS_END), timeout=2)
            assert b'Transfer-Encoding: chunked' in headers
            assert await asyncio.wait_for(reader.readexactly(18), timeout=2) == b'4;test=yes\r\nDATA\r\n'
            release_tail.set()
            assert await asyncio.wait_for(reader.readexactly(28), timeout=2) == (
                b'0\r\nX-Asset-Check: intact\r\n\r\n'
            )

    asyncio.run(run())


def test_cache_scraping_keeps_buffered_response_path() -> None:
    async def run() -> None:
        release_tail = asyncio.Event()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(_HEADERS_END)
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Length: 12\r\nConnection: close\r\n\r\nFIRST-'
            )
            await writer.drain()
            await release_tail.wait()
            writer.write(b'SECOND')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with _cdn_session(upstream, cache_enabled=True) as (reader, _writer):
            headers_task = asyncio.create_task(reader.readuntil(_HEADERS_END))
            await asyncio.sleep(0.05)
            assert not headers_task.done()
            release_tail.set()
            headers = await asyncio.wait_for(headers_task, timeout=2)
            assert b'content-length: 12' in headers.lower()
            assert await asyncio.wait_for(reader.readexactly(12), timeout=2) == b'FIRST-SECOND'

    asyncio.run(run())


def test_unmodified_compressed_batch_is_forwarded_without_json_processing() -> None:
    async def run() -> None:
        release_tail = asyncio.Event()
        original_batch = gzip.compress(b'[{"assetId":123,"requestId":"one"}]')
        request = (
            b'POST /v2/assets/batch HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n'
            b'Content-Encoding: gzip\r\n'
            + f'Content-Length: {len(original_batch)}\r\n'.encode()
            + b'Connection: close\r\n\r\n'
            + original_batch
        )
        forwarded: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            headers = await reader.readuntil(_HEADERS_END)
            size = int(
                next(
                    line.partition(b':')[2]
                    for line in headers.split(b'\r\n')
                    if line.lower().startswith(b'content-length:')
                )
            )
            forwarded.append(headers + await reader.readexactly(size))
            writer.write(
                b'HTTP/1.1 200 OK\r\nContent-Length: 10\r\nConnection: close\r\n\r\nRESULT-'
            )
            await writer.drain()
            await release_tail.wait()
            writer.write(b'END')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with _cdn_session(
            upstream,
            host='assetdelivery.roblox.com',
            request=request,
        ) as (reader, _writer):
            assert b'Content-Length: 10' in await asyncio.wait_for(
                reader.readuntil(_HEADERS_END), timeout=2
            )
            assert await asyncio.wait_for(reader.readexactly(7), timeout=2) == b'RESULT-'
            release_tail.set()
            assert await asyncio.wait_for(reader.readexactly(3), timeout=2) == b'END'
        assert forwarded == [request]

    asyncio.run(run())


def test_failed_batch_discards_pending_asset_routes(tmp_path: Path) -> None:
    """A non-2xx response must release pending CDN waiters for this batch."""
    async def run() -> None:
        replacement = tmp_path / 'replacement.mesh'
        replacement.write_bytes(b'local')
        request_body = b'[{"assetId":123,"assetTypeId":4,"requestId":"one"}]'
        request = (
            b'POST /v2/assets/batch HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n'
            + f'Content-Length: {len(request_body)}\r\n'.encode()
            + b'Connection: close\r\n\r\n'
            + request_body
        )
        observed: list[TextureStripper] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            headers = await reader.readuntil(_HEADERS_END)
            content_length = int(
                next(
                    line.partition(b':')[2]
                    for line in headers.split(b'\r\n')
                    if line.lower().startswith(b'content-length:')
                )
            )
            await reader.readexactly(content_length)
            writer.write(
                b'HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        async with _cdn_session(
            upstream,
            host='assetdelivery.roblox.com',
            request=request,
            replacements=({}, set(), {}, {123: str(replacement)}),
            observed=observed,
        ) as (reader, _writer):
            headers = await asyncio.wait_for(reader.readuntil(_HEADERS_END), timeout=2)
            assert b'503 Service Unavailable' in headers
            assert len(observed) == 1
            assert not observed[0].has_pending()

    asyncio.run(run())
