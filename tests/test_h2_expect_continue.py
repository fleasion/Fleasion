"""HTTP/2 uploads must receive 100 Continue before their bodies are sent."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from h2.connection import H2Connection
from h2.events import InformationalResponseReceived

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
        data = bytes(self.data)
        self.data.clear()
        return data


def test_h2_expect_continue_before_upload_body() -> None:
    async def run() -> None:
        writer = _MemoryWriter()
        upstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', object()),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', upstream_writer),
                method='test',
                endpoint='127.0.0.1',
            ),
        )
        await bridge.client.start()
        peer = H2Connection()
        peer.initiate_connection()
        peer.receive_data(writer.take())
        peer.send_headers(
            1,
            [
                (b':method', b'POST'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/upload'),
                (b'expect', b'100-continue'),
            ],
            end_stream=False,
        )
        await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
        events = peer.receive_data(writer.take())
        assert [
            dict(event.headers)[b':status']
            for event in events
            if isinstance(event, InformationalResponseReceived)
        ] == [b'100']
        assert 1 in bridge.requests
        assert bridge.requests[1].task is None

    asyncio.run(run())
