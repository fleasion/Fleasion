"""Authenticated synthetic record lifecycle against local TCP peer sockets.

These scenarios use fixed test secrets and never negotiate or attach live TLS.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import pytest

from fleasion.proxy.tls_records import CipherSuite, TLS13RecordSession, TLSRecordError

_CLIENT_SECRET_256 = b'C' * 32
_SERVER_SECRET_256 = b'S' * 32
_CLIENT_SECRET_384 = b'C' * 48
_SERVER_SECRET_384 = b'S' * 48


def _session(suite: CipherSuite, *, client: bool) -> TLS13RecordSession:
    client_secret = (
        _CLIENT_SECRET_384 if suite is CipherSuite.AES_256_GCM_SHA384 else _CLIENT_SECRET_256
    )
    server_secret = (
        _SERVER_SECRET_384 if suite is CipherSuite.AES_256_GCM_SHA384 else _SERVER_SECRET_256
    )
    return TLS13RecordSession(
        inbound_secret=server_secret if client else client_secret,
        outbound_secret=client_secret if client else server_secret,
        suite=suite,
    )


async def _serve_one(
    handler: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
    interaction: Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]],
) -> None:
    server = await asyncio.start_server(handler, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    assert isinstance(port, int)
    try:
        reader, writer = await asyncio.open_connection('127.0.0.1', port)
        try:
            await asyncio.wait_for(interaction(reader, writer), timeout=3)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_tcp_lifecycle_with_key_update_and_bidirectional_close_notify(suite: CipherSuite) -> None:
    async def run() -> None:
        server_results: list[tuple[bytes, int, int, bool, bool]] = []
        finished = asyncio.Event()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            session = _session(suite, client=False)
            received = bytearray()
            try:
                while not session.inbound_closed:
                    chunk = await asyncio.wait_for(reader.read(7), timeout=1)
                    assert chunk, 'Missing client close_notify'
                    for record in session.receive(chunk):
                        if record.content_type == 23:
                            received.extend(record.content)
                    if session.key_update_response_pending:
                        writer.write(session.send_key_update())
                        await writer.drain()
                    if bytes(received) == b'firstsecond' and not session.outbound_closed:
                        writer.write(session.send_application(b'server-reply'))
                        writer.write(session.send_close_notify())
                        await writer.drain()
                session.finish()
                server_results.append(
                    (
                        bytes(received),
                        session.inbound.generation,
                        session.outbound.generation,
                        session.inbound_closed,
                        session.outbound_closed,
                    )
                )
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            session = _session(suite, client=True)
            encrypted = (
                session.send_application(b'first')
                + session.send_key_update(request_peer_update=True)
                + session.send_application(b'second')
            )
            for start in range(0, len(encrypted), 3):
                writer.write(encrypted[start : start + 3])
                await writer.drain()
            received: list[bytes] = []
            while not session.inbound_closed:
                packet = await asyncio.wait_for(reader.read(11), timeout=1)
                assert packet, 'Server closed without close_notify'
                for record in session.receive(packet):
                    if record.content_type == 23:
                        received.append(record.content)
            assert received == [b'server-reply']
            assert session.inbound.generation == session.outbound.generation == 1
            writer.write(session.send_close_notify())
            await writer.drain()
            session.finish()

        await _serve_one(handler, client)
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert server_results == [(b'firstsecond', 1, 1, True, True)]

    asyncio.run(run())


@pytest.mark.parametrize('fault', ['bad_tag', 'truncated', 'invalid_record_type'])
def test_tcp_fatal_input_closes_session_without_response(fault: str) -> None:
    async def run() -> None:
        failures: list[str] = []
        finished = asyncio.Event()
        suite = CipherSuite.AES_128_GCM_SHA256

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            session = _session(suite, client=False)
            try:
                while packet := await asyncio.wait_for(reader.read(9), timeout=1):
                    session.receive(packet)
                session.finish()
            except TLSRecordError:
                failures.append('rejected')
                with pytest.raises(TLSRecordError, match='failed'):
                    session.send_application(b'must-not-send')
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            session = _session(suite, client=True)
            data = bytearray(session.send_application(b'test'))
            if fault == 'bad_tag':
                data[-1] ^= 0x01
            elif fault == 'invalid_record_type':
                data[0] = 0x16
            else:
                data = data[:-2]
            writer.write(data)
            await writer.drain()
            writer.write_eof()
            assert await asyncio.wait_for(reader.read(), timeout=1) == b''

        await _serve_one(handler, client)
        await asyncio.wait_for(finished.wait(), timeout=1)
        assert failures == ['rejected']

    asyncio.run(run())


def test_pending_connection_can_be_cancelled_and_sockets_released() -> None:
    async def run() -> None:
        entered = asyncio.Event()
        stopped = asyncio.Event()
        active: set[asyncio.Task[None]] = set()

        async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            async def consume() -> None:
                try:
                    entered.set()
                    await reader.read()
                finally:
                    writer.close()
                    await writer.wait_closed()
                    stopped.set()

            task = asyncio.create_task(consume())
            active.add(task)
            task.add_done_callback(active.discard)

        async def client(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert len(active) == 1
            task = next(iter(active))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=1)
            await asyncio.wait_for(stopped.wait(), timeout=1)
            assert not active
            assert writer.can_write_eof()

        await _serve_one(handler, client)

    asyncio.run(run())
