"""Avoid redundant immutable-message copies while retaining writer ownership semantics."""

from __future__ import annotations

import asyncio
import tracemalloc
from typing import TYPE_CHECKING

from fleasion.proxy import h2_transport

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def _writer(
    callback: Callable[[bytes], Awaitable[None]],
) -> tuple[h2_transport._MessageWriter, list[int]]:  # pyright: ignore[reportPrivateUsage]
    held = [0]

    def reserve(amount: int) -> None:
        held[0] += amount

    def release(amount: int) -> None:
        held[0] -= amount

    return h2_transport._MessageWriter(  # pyright: ignore[reportPrivateUsage]
        callback, reserve=reserve, release=release
    ), held


def test_single_immutable_message_handoff_has_no_body_copy() -> None:
    async def run() -> None:
        payload = b'P' * (4 * 1024 * 1024)
        seen: list[bytes] = []

        async def callback(message: bytes) -> None:
            seen.append(message)

        writer, held = _writer(callback)
        tracemalloc.start()
        try:
            writer.write(payload)
            assert held == [len(payload)]
            await writer.drain()
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert seen == [payload]
        assert seen[0] is payload
        assert peak < 1024 * 1024
        assert held == [0]

    asyncio.run(run())


def test_mutable_messages_are_snapshotted_and_multiple_writes_preserve_order() -> None:
    async def run() -> None:
        seen: list[bytes] = []

        async def callback(message: bytes) -> None:
            seen.append(message)

        writer, held = _writer(callback)
        mutable = bytearray(b'alpha')
        writer.write(mutable)
        mutable[:] = b'XXXXX'
        writer.write(b'-beta')
        assert held == [10]
        await writer.drain()
        assert seen == [b'alpha-beta']
        assert held == [0]
        immutable = b'gamma'
        writer.write(immutable)
        await writer.drain()
        assert seen[-1] is immutable
        writer.close()
        assert held == [0]

    asyncio.run(run())


def test_nonbyte_memoryview_preserves_its_byte_sequence() -> None:
    async def run() -> None:
        seen: list[bytes] = []

        async def callback(message: bytes) -> None:
            seen.append(message)

        writer, held = _writer(callback)
        data = bytes(range(16))
        view = memoryview(data).cast('I')
        writer.write(view)
        assert held == [16]
        await writer.drain()
        assert seen == [data]
        assert held == [0]

    asyncio.run(run())


def test_memoryviews_are_snapshotted_without_unnecessary_contiguous_copy() -> None:
    async def run() -> None:
        seen: list[bytes] = []

        async def callback(message: bytes) -> None:
            seen.append(message)

        writer, held = _writer(callback)
        source = bytearray(b'P' * (4 * 1024 * 1024))
        tracemalloc.start()
        try:
            writer.write(memoryview(source))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < len(source) * 3 // 2
        source[0] = ord('X')
        await writer.drain()
        assert seen[-1] == b'P' * len(source)
        assert held == [0]

        writer.write(memoryview(b'abcdef')[::2])
        await writer.drain()
        assert seen[-1] == b'ace'
        assert held == [0]

    asyncio.run(run())


def test_upstream_response_envelope_avoids_intermediate_body_copy() -> None:
    body = bytearray(b'P' * (4 * 1024 * 1024))
    headers = [(b':status', b'200'), (b'content-type', b'application/octet-stream')]
    tracemalloc.start()
    try:
        message = h2_transport._as_http1(  # pyright: ignore[reportPrivateUsage]
            headers, body, request=False
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert message.startswith(b'HTTP/1.1 200 Upstream\r\n')
    assert message.endswith(body)
    assert peak < len(body) * 3 // 2


def test_large_http1_message_conversion_keeps_body_without_a_full_copy() -> None:
    payload = b'P' * (4 * 1024 * 1024)
    message = b'HTTP/1.1 200 OK\r\ncontent-type: application/octet-stream\r\n\r\n' + payload
    tracemalloc.start()
    try:
        headers, body = h2_transport._as_http2(  # pyright: ignore[reportPrivateUsage]
            message, request=False, host='example.test'
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert (b':status', b'200') in headers
    assert body == payload
    assert peak < 1024 * 1024
