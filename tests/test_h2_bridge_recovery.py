"""Controlled shutdown tests for an upstream that disappears during HTTP/2 work."""

from __future__ import annotations

import asyncio
from typing import cast

from h2.connection import H2Connection

from fleasion.proxy.h2_transport import H2FeatureBridge
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult


class _MemoryWriter:
    def __init__(self) -> None:
        self.closed = False
        self.data = bytearray()

    def write(self, data: bytes) -> None:
        self.data.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


class _WaitingProxy:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def serve_decrypted_http_session(
        self,
        _reader: asyncio.StreamReader,
        _writer: asyncio.StreamWriter,
        _host: str,
        _upstream: UpstreamConnectResult,
    ) -> None:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cancelled.set()


def test_upstream_disconnect_cancels_a_stalled_request_within_deadline() -> None:
    async def run() -> None:
        proxy = _WaitingProxy()
        client_reader = asyncio.StreamReader()
        upstream_reader = asyncio.StreamReader()
        client_writer = _MemoryWriter()
        upstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            client_reader,
            cast('asyncio.StreamWriter', client_writer),
            UpstreamConnectResult(
                reader=upstream_reader,
                writer=cast('asyncio.StreamWriter', upstream_writer),
                method='test',
                endpoint='localhost',
            ),
        )
        peer = H2Connection()
        peer.initiate_connection()
        peer.send_headers(
            1,
            [
                (b':method', b'GET'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/stalled'),
            ],
            end_stream=True,
        )

        async def close_after_request_started() -> None:
            await proxy.started.wait()
            upstream_reader.feed_eof()

        closer = asyncio.create_task(close_after_request_started())
        await asyncio.wait_for(bridge.run(initial_client_data=peer.data_to_send()), timeout=2.0)
        await closer
        assert proxy.cancelled.is_set()
        assert not bridge.tasks
        assert not bridge.requests
        assert not bridge.upstream_requests

    asyncio.run(run())


def test_connection_rejects_excessive_http2_streams_without_terminating_peer() -> None:
    async def run() -> None:
        proxy = _WaitingProxy()
        client_writer = _MemoryWriter()
        upstream_writer = _MemoryWriter()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', client_writer),
            UpstreamConnectResult(
                reader=asyncio.StreamReader(),
                writer=cast('asyncio.StreamWriter', upstream_writer),
                method='test',
                endpoint='localhost',
            ),
        )
        peer = H2Connection()
        peer.initiate_connection()
        streams: list[int] = []
        for _ in range(65):
            stream = peer.get_next_available_stream_id()
            streams.append(stream)
            peer.send_headers(
                stream,
                [
                    (b':method', b'GET'),
                    (b':scheme', b'https'),
                    (b':authority', b'apis.roblox.com'),
                    (b':path', b'/hello'),
                ],
                end_stream=True,
            )
        await bridge.client.start()
        try:
            await bridge._receive_client(peer.data_to_send())  # pyright: ignore[reportPrivateUsage]
            assert len(bridge.requests) == 64
            assert len(bridge.tasks) == 64
            from h2.events import StreamReset

            events = peer.receive_data(bytes(client_writer.data))
            reset_streams = [event.stream_id for event in events if isinstance(event, StreamReset)]
            assert reset_streams == [streams[-1]]
            assert not bridge.client.terminated
        finally:
            for task in bridge.tasks:
                task.cancel()
            await asyncio.gather(*bridge.tasks, return_exceptions=True)

    asyncio.run(run())


def test_upstream_disconnect_allows_quickly_finishing_tasks_to_exit_normally() -> None:
    class _CompletingProxy:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.finish = asyncio.Event()
            self.completed = False
            self.cancelled = False

        async def serve_decrypted_http_session(
            self,
            _reader: asyncio.StreamReader,
            _writer: asyncio.StreamWriter,
            _host: str,
            _upstream: UpstreamConnectResult,
        ) -> None:
            self.started.set()
            try:
                await self.finish.wait()
                self.completed = True
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def run() -> None:
        proxy = _CompletingProxy()
        upstream_reader = asyncio.StreamReader()
        bridge = H2FeatureBridge(
            cast('FleasionProxy', proxy),
            'apis.roblox.com',
            asyncio.StreamReader(),
            cast('asyncio.StreamWriter', _MemoryWriter()),
            UpstreamConnectResult(
                reader=upstream_reader,
                writer=cast('asyncio.StreamWriter', _MemoryWriter()),
                method='test',
                endpoint='localhost',
            ),
        )
        peer = H2Connection()
        peer.initiate_connection()
        peer.send_headers(
            1,
            [
                (b':method', b'GET'),
                (b':scheme', b'https'),
                (b':authority', b'apis.roblox.com'),
                (b':path', b'/quick'),
            ],
            end_stream=True,
        )

        async def complete_after_shutdown() -> None:
            await proxy.started.wait()
            upstream_reader.feed_eof()
            await asyncio.sleep(0.02)
            proxy.finish.set()

        closer = asyncio.create_task(complete_after_shutdown())
        await asyncio.wait_for(bridge.run(initial_client_data=peer.data_to_send()), timeout=1.7)
        await closer
        assert proxy.completed
        assert not proxy.cancelled
        assert not bridge.tasks

    asyncio.run(run())
