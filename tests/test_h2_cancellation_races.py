"""HTTP/2 cancellation ordering must preserve per-connection byte accounting."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from h2.connection import H2Connection
from h2.events import StreamReset

from fleasion.proxy.h2_transport import H2FeatureBridge
from fleasion.proxy.upstream import UpstreamConnectResult

if TYPE_CHECKING:
    from fleasion.proxy.server import FleasionProxy


class _MemoryWriter:
    def __init__(self) -> None:
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def take(self) -> bytes:
        result = bytes(self.data)
        self.data.clear()
        return result


def test_reset_immediately_after_request_end_releases_pending_body() -> None:
    async def run() -> None:
        writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', object()),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='controlled_loopback',
                endpoint='127.0.0.1',
            ),
        )
        peer = H2Connection()
        await bridge.client.start()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        peer.send_headers(
            1,
            [
                (b':method', b'POST'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/cancel'),
            ],
        )
        peer.send_data(1, b'body-held-until-task-scheduled', end_stream=True)
        peer.reset_stream(1, error_code=8)
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert not bridge.requests
        assert not bridge.tasks
        assert bridge.buffered_request_bytes == 0
        assert bridge.buffered_response_bytes == 0
        assert not bridge.client.terminated
        assert not [e for e in peer.receive_data(writer.take()) if isinstance(e, StreamReset)]

    asyncio.run(run())
