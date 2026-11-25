"""Incomplete HTTP/1 framing must not be accepted as a complete message."""

from __future__ import annotations

import asyncio

import pytest

from fleasion.proxy import server as proxy_server


@pytest.mark.parametrize(
    ('headers', 'wire'),
    [
        ({b'content-length': b'5'}, b'AB'),
        ({b'content-length': b'garbage'}, b''),
        ({b'content-length': b'-1'}, b''),
        ({b'content-length': b''}, b''),
        ({b'transfer-encoding': b'chunked'}, b'5\r\nAB'),
        ({b'transfer-encoding': b'chunked'}, b'Z\r\n'),
        ({b'transfer-encoding': b'chunked'}, b'2\r\nABXX0\r\n\r\n'),
        ({b'transfer-encoding': b'chunked'}, b'2\r\nAB\r\n0\r\nTrailer: unfinished'),
        ({b'transfer-encoding': b'chunked'}, b'2\r\nAB\r\n0\r\n'),
        ({b'transfer-encoding': b'chunked', b'content-length': b'2'}, b'0\r\n\r\n'),
        ({b'transfer-encoding': b'xchunked'}, b''),
    ],
)
def test_malformed_body_is_rejected(headers: dict[bytes, bytes], wire: bytes) -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(wire)
        reader.feed_eof()
        with pytest.raises(ValueError, match='HTTP|body|chunk|length|encoding'):
            await asyncio.wait_for(
                proxy_server._read_body_wire(reader, headers), 1  # pyright: ignore[reportPrivateUsage]
            )

    asyncio.run(run())


@pytest.mark.parametrize(
    'data',
    [
        b'GET / HTTP/1.1\r\nHost: example.test\r\nContent-Length: 2\r\nContent-Length: 5\r\n\r\n',
        b'GET / HTTP/1.1\r\nHost: example.test\r\nTransfer-Encoding: chunked\r\nContent-Length: 0\r\n\r\n',
        b'GET / HTTP/1.1\r\nHost: example.test\r\nContent-Length: 5\r\n',
        b'GET / HTTP/1.1\r\nHost: example.test\r\nBad Header Line\r\n\r\n',
    ],
)
def test_malformed_or_incomplete_header_block_is_rejected(data: bytes) -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        with pytest.raises(ValueError, match='HTTP|header|length|encoding'):
            await asyncio.wait_for(
                proxy_server._read_headers_raw(reader), 1  # pyright: ignore[reportPrivateUsage]
            )

    asyncio.run(run())


@pytest.mark.parametrize('partial', [b'', b'GET / HTTP/1.1\r\n'])
def test_stalled_header_preserves_empty_and_partial_timeout_behavior(
    monkeypatch: pytest.MonkeyPatch, partial: bytes,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(proxy_server, '_HTTP_HEADER_IDLE_TIMEOUT', 0.05)
        reader = asyncio.StreamReader()
        reader.feed_data(partial)
        if partial:
            with pytest.raises(ValueError, match='Incomplete HTTP header'):
                await proxy_server._read_headers_raw(reader)  # pyright: ignore[reportPrivateUsage]
        else:
            assert await proxy_server._read_headers_raw(reader) is None  # pyright: ignore[reportPrivateUsage]

    asyncio.run(run())


def test_progressing_header_can_exceed_one_idle_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(proxy_server, '_HTTP_HEADER_IDLE_TIMEOUT', 0.1)
        reader = asyncio.StreamReader()
        lines = [b'GET / HTTP/1.1\r\n', b'Host: example.test\r\n', b'X: y\r\n', b'\r\n']

        async def supply() -> None:
            for line in lines:
                await asyncio.sleep(0.04)
                reader.feed_data(line)

        sender = asyncio.create_task(supply())
        parsed = await asyncio.wait_for(
            proxy_server._read_headers_raw(reader), 1  # pyright: ignore[reportPrivateUsage]
        )
        await sender
        assert parsed is not None
        assert parsed.raw_header_block == b''.join(lines)

    asyncio.run(run())


def test_header_read_propagates_external_cancellation() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        task = asyncio.create_task(
            proxy_server._read_headers_raw(reader)  # pyright: ignore[reportPrivateUsage]
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_valid_chunked_message_retains_trailers() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        wire = b'2;foo=bar\r\nAB\r\n0\r\nx-proof: valid\r\n\r\n'
        reader.feed_data(wire)
        reader.feed_eof()
        parsed = await proxy_server._read_body_wire(  # pyright: ignore[reportPrivateUsage]
            reader, {b'transfer-encoding': b'chunked'}
        )
        assert parsed.payload == b'AB'
        assert parsed.wire == wire
        assert parsed.has_trailers

    asyncio.run(run())


@pytest.mark.parametrize(
    ('headers', 'partial_wire'),
    [
        ({b'content-length': b'8'}, b'PART'),
        ({b'transfer-encoding': b'chunked'}, b'3'),
        ({b'transfer-encoding': b'chunked'}, b'3\r\nA'),
        ({b'transfer-encoding': b'chunked'}, b'1\r\nA'),
        ({b'transfer-encoding': b'chunked'}, b'0\r\nx-proof: '),
    ],
)
def test_stalled_http_body_expires_without_peer_eof(
    monkeypatch: pytest.MonkeyPatch, headers: dict[bytes, bytes], partial_wire: bytes
) -> None:
    async def run() -> None:
        monkeypatch.setattr(proxy_server, '_HTTP_BODY_IDLE_TIMEOUT', 0.05, raising=False)
        reader = asyncio.StreamReader()
        reader.feed_data(partial_wire)
        with pytest.raises(TimeoutError, match='HTTP body read stalled'):
            await asyncio.wait_for(
                proxy_server._read_body_wire(reader, headers), 0.5  # pyright: ignore[reportPrivateUsage]
            )

    asyncio.run(run())


def test_progressing_body_may_exceed_one_idle_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(proxy_server, '_HTTP_BODY_IDLE_TIMEOUT', 0.07, raising=False)
        reader = asyncio.StreamReader()

        async def supply() -> None:
            for ch in b'ABCDEFGH':
                await asyncio.sleep(0.02)
                reader.feed_data(bytes([ch]))

        sender = asyncio.create_task(supply())
        body = await asyncio.wait_for(
            proxy_server._read_body_wire(  # pyright: ignore[reportPrivateUsage]
                reader, {b'content-length': b'8'}
            ), 1
        )
        await sender
        assert body.payload == b'ABCDEFGH'

    asyncio.run(run())


def test_body_read_propagates_external_cancellation() -> None:
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(b'part')
        task = asyncio.create_task(
            proxy_server._read_http_body_bytes(  # pyright: ignore[reportPrivateUsage]
                reader, 8, context='test body'
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
