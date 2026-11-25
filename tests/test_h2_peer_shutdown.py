"""Ensure HTTP/2 flow-control waiters stop on stream termination."""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection

from fleasion.proxy.h2_transport import _Peer  # pyright: ignore[reportPrivateUsage]


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def take(self) -> bytes:
        data = bytes(self.data)
        self.data.clear()
        return data


@pytest.mark.parametrize('reset_origin', ['remote', 'local'])
def test_reset_terminates_sender_waiting_on_http2_window(reset_origin: str) -> None:
    async def run() -> None:
        writer = _MemoryWriter()
        client = _Peer(cast('asyncio.StreamWriter', writer), client_side=True)
        server = H2Connection(H2Configuration(client_side=False))
        await client.start()
        server.initiate_connection()
        server.receive_data(writer.take())
        await client.receive(server.data_to_send())
        server.receive_data(writer.take())

        stream_ids: list[int] = []
        headers = [
            (b':method', b'POST'),
            (b':scheme', b'https'),
            (b':authority', b'local.test'),
            (b':path', b'/upload'),
        ]
        sender = asyncio.create_task(
            client.open_request(headers, b'x' * 70000, stream_ids.append)
        )
        await asyncio.sleep(0.02)
        assert stream_ids == [1]
        assert not sender.done()
        server.receive_data(writer.take())
        if reset_origin == 'remote':
            server.reset_stream(1, error_code=8)
            await client.receive(server.data_to_send())
        else:
            await client.reset(1)
        with pytest.raises(ConnectionError, match='stream closed'):
            await asyncio.wait_for(sender, timeout=0.35)

    asyncio.run(run())
