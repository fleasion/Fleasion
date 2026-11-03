"""Authenticated HTTP forwarding semantics and response framing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast

import pytest

from fleasion.proxy.server import FleasionProxy, _read_headers_raw  # pyright: ignore[reportPrivateUsage]
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper

_HOST = 'apis.roblox.com'
_REQUEST = b'GET / HTTP/1.1\r\nHost: apis.roblox.com\r\nConnection: keep-alive\r\n\r\n'
_HANDLER = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]


def _empty_replacements() -> tuple[dict[str, str], set[str], dict[str, str], dict[str, str]]:
    return {}, set(), {}, {}


@asynccontextmanager
async def _proxy_session(
    handler: _HANDLER,
    *,
    authenticated_upstream: bool = True,
    host: str = _HOST,
    mutate: Literal['request', 'response'] | None = None,
    failures: list[str] | None = None,
) -> AsyncGenerator[tuple[asyncio.StreamReader, asyncio.StreamWriter]]:
    proxy = FleasionProxy(
        texture_stripper=cast(
            'TextureStripper',
            SimpleNamespace(config_manager=SimpleNamespace(get_all_replacements=_empty_replacements)),
        ),
        cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
        host_certs={},
        intercept_hosts={host},
        tls_splice=True,
        auto_replace_rules=(
            [{'direction': mutate, 'match': 'DATA', 'replacement': 'EDIT'}]
            if mutate is not None
            else []
        ),
    )
    upstream_server = await asyncio.start_server(handler, '127.0.0.1', 0)
    upstream_port = upstream_server.sockets[0].getsockname()[1]
    sessions: set[asyncio.Task[None]] = set()

    async def connect(_host: str) -> UpstreamConnectResult:
        source, sink = await asyncio.open_connection('127.0.0.1', upstream_port)
        return UpstreamConnectResult(
            reader=source, writer=sink, method='authenticated_test', endpoint=host
        )

    async def downstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if task is not None:
            sessions.add(task)
        try:
            if authenticated_upstream:
                await proxy.serve_decrypted_http_session(reader, writer, host, await connect(host))
            else:
                first = await _read_headers_raw(reader)
                assert first is not None
                await proxy._http_session(  # pyright: ignore[reportPrivateUsage]
                    first, reader, writer, host
                )
        except (TimeoutError, ValueError) as exc:
            if failures is None:
                raise
            failures.append(str(exc))
        finally:
            writer.close()
            await writer.wait_closed()
            if task is not None:
                sessions.discard(task)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(proxy, '_connect_upstream', connect)
        proxy_server = await asyncio.start_server(downstream, '127.0.0.1', 0)
        proxy_port = proxy_server.sockets[0].getsockname()[1]
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
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


@pytest.mark.parametrize('method,status', [(b'HEAD', 200), (b'GET', 304), (b'GET', 204)])
def test_spliced_bodyless_response_does_not_consume_next_response(
    method: bytes, status: int
) -> None:
    async def run() -> None:
        seen: list[bytes] = []

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
                writer.write(
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK'
                )
                await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream) as (reader, writer):
            writer.write(method + _REQUEST[3:])
            await writer.drain()
            first = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert first.startswith(f'HTTP/1.1 {status}'.encode())
            writer.write(
                b'GET /second HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            second = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert second.startswith(b'HTTP/1.1 200')
            assert await asyncio.wait_for(reader.readexactly(2), 1) == b'OK'
            assert seen == [method, b'GET']

    asyncio.run(run())


def test_spliced_informational_response_precedes_final_response() -> None:
    async def run() -> None:
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(
                    b'HTTP/1.1 103 Early Hints\r\nLink: </style.css>; rel=preload\r\n\r\n'
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nOK'
                )
                await writer.drain()
                await reader.readuntil(b'\r\n\r\n')
                writer.write(
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nHI'
                )
                await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream) as (reader, writer):
            writer.write(_REQUEST)
            await writer.drain()
            hint = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert hint.startswith(b'HTTP/1.1 103')
            final = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert final.startswith(b'HTTP/1.1 200')
            assert await asyncio.wait_for(reader.readexactly(2), 1) == b'OK'
            writer.write(
                b'GET /second HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Connection: close\r\n\r\n'
            )
            await writer.drain()
            second = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert second.startswith(b'HTTP/1.1 200')
            assert await asyncio.wait_for(reader.readexactly(2), 1) == b'HI'

    asyncio.run(run())


def test_spliced_expect_continue_upload_does_not_deadlock() -> None:
    async def run() -> None:
        received: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                received.append(await reader.readexactly(4))
                writer.write(
                    b'HTTP/1.1 100 Continue\r\n\r\n'
                    b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK'
                )
                await writer.drain()
            except asyncio.IncompleteReadError:
                pass
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream) as (reader, writer):
            writer.write(
                b'POST /upload HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Content-Length: 4\r\nExpect: 100-continue\r\nConnection: close\r\n\r\n'
            )
            await writer.drain()
            interim = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert interim.startswith(b'HTTP/1.1 100 Continue')
            writer.write(b'test')
            await writer.drain()
            final = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 1)
            assert final.startswith(b'HTTP/1.1 200 OK')
            assert await asyncio.wait_for(reader.readexactly(2), 1) == b'OK'
            assert received == [b'test']

    asyncio.run(run())


@pytest.mark.parametrize('authenticated_upstream', [False, True])
@pytest.mark.parametrize('connection_close', [False, True])
def test_close_delimited_http_response_preserves_body(
    authenticated_upstream: bool, connection_close: bool
) -> None:
    async def run() -> None:
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                connection_header = b'Connection: close\r\n' if connection_close else b''
                writer.write(b'HTTP/1.1 200 OK\r\n' + connection_header + b'\r\nUNFRAMED BODY')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, authenticated_upstream=authenticated_upstream) as (
            reader,
            writer,
        ):
            writer.write(_REQUEST)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 1)
            assert response.startswith(b'HTTP/1.1 200 OK')
            assert response.endswith(b'UNFRAMED BODY')

    asyncio.run(run())


@pytest.mark.parametrize('status', [204, 304])
def test_bodyless_cdn_response_does_not_invent_content_length(status: int) -> None:
    async def run() -> None:
        host = 'clientsettingscdn.roblox.com'

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(
                    f'HTTP/1.1 {status} Response\r\nConnection: close\r\n\r\n'.encode()
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, host=host) as (reader, writer):
            writer.write(f'GET / HTTP/1.1\r\nHost: {host}\r\n\r\n'.encode())
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 1)
            assert response.startswith(f'HTTP/1.1 {status}'.encode())
            assert b'content-length' not in response.lower()

    asyncio.run(run())


def test_spliced_http_upgrade_relays_opaque_application_bytes() -> None:
    async def run() -> None:
        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                request = await reader.readuntil(b'\r\n\r\n')
                assert b'upgrade: websocket' in request.lower()
                writer.write(
                    b'HTTP/1.1 101 Switching Protocols\r\n'
                    b'Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n'
                )
                await writer.drain()
                payload = await asyncio.wait_for(reader.readexactly(4), 2)
                assert payload == b'PING'
                writer.write(b'PONG')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream) as (reader, writer):
            writer.write(
                b'GET /connect HTTP/1.1\r\nHost: apis.roblox.com\r\n'
                b'Connection: Upgrade\r\nUpgrade: websocket\r\n\r\n'
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), 2)
            assert response.startswith(b'HTTP/1.1 101 Switching Protocols')
            writer.write(b'PING')
            await writer.drain()
            assert await asyncio.wait_for(reader.readexactly(4), 2) == b'PONG'

    asyncio.run(run())


@pytest.mark.parametrize('host', ['apis.roblox.com', 'clientsettingscdn.roblox.com'])
def test_chunked_http_response_preserves_trailers(host: str) -> None:
    async def run() -> None:
        response = (
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
            b'Trailer: x-verification\r\nConnection: close\r\n\r\n'
            b'4\r\nDATA\r\n0\r\nx-verification: valid\r\n\r\n'
        )

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, host=host) as (reader, writer):
            writer.write(f'GET /data HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode())
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == response

    asyncio.run(run())


@pytest.mark.parametrize('host', ['apis.roblox.com', 'clientsettingscdn.roblox.com'])
def test_chunked_http_request_preserves_trailers(host: str) -> None:
    async def run() -> None:
        payload = b'4\r\nDATA\r\n0\r\nx-verification: valid\r\n\r\n'
        received: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                headers = await reader.readuntil(b'\r\n\r\n')
                assert b'transfer-encoding: chunked' in headers.lower()
                received.append(await asyncio.wait_for(reader.readuntil(b'0\r\nx-verification: valid\r\n\r\n'), 1))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK')
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, host=host) as (reader, writer):
            writer.write(
                f'POST /data HTTP/1.1\r\nHost: {host}\r\n'.encode()
                + b'Transfer-Encoding: chunked\r\nTrailer: x-verification\r\n'
                + b'Connection: close\r\n\r\n' + payload
            )
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 2)
            assert response.startswith(b'HTTP/1.1 200 OK')
            assert response.endswith(b'OK')
            assert received == [payload]

    asyncio.run(run())


@pytest.mark.parametrize('mutate', ['request', 'response'])
def test_chunked_integrity_trailer_rewrite_fails_closed(
    mutate: Literal['request', 'response'],
) -> None:
    async def run() -> None:
        host = 'clientsettingscdn.roblox.com'
        failures: list[str] = []
        payload = b'4\r\nDATA\r\n0\r\nx-verification: valid\r\n\r\n'

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                if mutate == 'response':
                    writer.write(
                        b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n'
                        b'Trailer: x-verification\r\nConnection: close\r\n\r\n' + payload
                    )
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, host=host, mutate=mutate, failures=failures) as (
            reader,
            writer,
        ):
            request = (
                f'POST /data HTTP/1.1\r\nHost: {host}\r\n'.encode()
                + b'Transfer-Encoding: chunked\r\nTrailer: x-verification\r\n'
                + b'Connection: close\r\n\r\n' + payload
            ) if mutate == 'request' else (
                f'GET /data HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n'.encode()
            )
            writer.write(request)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b''
            assert failures == [f'Cannot rewrite an HTTP {mutate} carrying chunked trailers']

    asyncio.run(run())


@pytest.mark.parametrize(
    ('upstream_response', 'error'),
    [
        (
            b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\nConnection: close\r\n\r\nPART',
            'Incomplete HTTP content-length body',
        ),
        (
            b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nAB',
            'Incomplete HTTP chunk body',
        ),
        (
            b'HTTP/1.1 200 OK\r\nContent-Length: 4\r\nContent-Length: 8\r\n\r\nPART',
            'Duplicate HTTP framing header',
        ),
        (
            b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n',
            'Incomplete HTTP header block',
        ),
    ],
)
def test_incomplete_upstream_message_never_reaches_client(
    upstream_response: bytes, error: str
) -> None:
    async def run() -> None:
        failures: list[str] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(upstream_response)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, failures=failures) as (reader, writer):
            writer.write(_REQUEST)
            await writer.drain()
            assert await asyncio.wait_for(reader.read(), 2) == b''
            assert len(failures) == 1
            assert error in failures[0]

    asyncio.run(run())


@pytest.mark.parametrize(
    'upstream_response',
    [
        b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\nPARTIAL',
        b'HTTP/1.1 200 OK\r\nContent-Length: 8\r\n\r\nPARTIAL',
        b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n4\r\nPAR',
    ],
)
def test_stalled_upstream_body_closes_proxy_session(
    monkeypatch: pytest.MonkeyPatch, upstream_response: bytes,
) -> None:
    async def run() -> None:
        from fleasion.proxy import server as proxy_server

        monkeypatch.setattr(proxy_server, '_HTTP_BODY_IDLE_TIMEOUT', 0.05, raising=False)
        release = asyncio.Event()
        failures: list[str] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(upstream_response)
                await writer.drain()
                await release.wait()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream, failures=failures) as (reader, writer):
            try:
                writer.write(_REQUEST)
                await writer.drain()
                assert await asyncio.wait_for(reader.read(), 0.5) == b''
                assert failures == ['HTTP body read stalled']
            finally:
                release.set()

    asyncio.run(run())


def test_progressing_unframed_upstream_response_survives_idle_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        from fleasion.proxy import server as proxy_server

        monkeypatch.setattr(proxy_server, '_HTTP_BODY_IDLE_TIMEOUT', 0.09)

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await reader.readuntil(b'\r\n\r\n')
                writer.write(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n')
                await writer.drain()
                for fragment in (b'A', b'B', b'C', b'D', b'E', b'F'):
                    await asyncio.sleep(0.03)
                    writer.write(fragment)
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        async with _proxy_session(upstream) as (reader, writer):
            writer.write(_REQUEST)
            await writer.drain()
            response = await asyncio.wait_for(reader.read(), 1)
            assert response.endswith(b'ABCDEF')

    asyncio.run(run())
