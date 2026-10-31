"""Multiplexed HTTP/2 bridge tests using controlled localhost peers."""

from __future__ import annotations

import asyncio
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from h2.config import H2Configuration
from h2.connection import H2Connection
from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded, StreamReset

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


def _proxy(path: Path) -> FleasionProxy:
    ca, key = generate_ca(path)
    certs = generate_multi_host_cert("h2-test", {"apis.roblox.com"}, ca, key, path)
    return FleasionProxy(
        texture_stripper=cast(
            "TextureStripper",
            SimpleNamespace(
                config_manager=SimpleNamespace(get_all_replacements=_empty_replacements)
            ),
        ),
        cache_scraper=cast("CacheScraper", SimpleNamespace(enabled=False)),
        host_certs={},
        default_cert=certs,
        intercept_hosts={"apis.roblox.com"},
        intercept_all_hosts=True,
        auto_replace_rules=[
            {"direction": "request", "match": "original", "replacement": "modified"},
            {"direction": "response", "match": "upstream", "replacement": "client"},
        ],
    )


@pytest.mark.parametrize("parallel", [1, 2, 4])
@pytest.mark.parametrize("authenticated_tls", [False, True])
def test_h2_bridge_reuses_feature_engine_for_multiplexed_streams(
    tmp_path: Path, parallel: int, authenticated_tls: bool
) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        server_ctx: ssl.SSLContext | None = None
        client_ctx: ssl.SSLContext | None = None
        if authenticated_tls:
            network_dir = tmp_path / "network"
            network_dir.mkdir()
            ca, ca_key = generate_ca(network_dir)
            cert, key = generate_host_cert("apis.roblox.com", ca, ca_key, network_dir)
            server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            server_ctx.load_cert_chain(cert, key)
            server_ctx.set_alpn_protocols(["h2"])
            client_ctx = ssl.create_default_context(cafile=str(ca))
            client_ctx.set_alpn_protocols(["h2"])
        received: list[bytes] = []
        completed = asyncio.Event()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            requests: dict[int, bytearray] = {}
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, RequestReceived):
                            requests[event.stream_id] = bytearray()
                        elif isinstance(event, DataReceived):
                            requests[event.stream_id].extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            received.append(bytes(requests.pop(event.stream_id)))
                            payload = b"upstream-original"
                            h2.send_headers(
                                event.stream_id,
                                [(b":status", b"200"), (b"content-length", b"17")],
                            )
                            h2.send_data(event.stream_id, payload, end_stream=True)
                            if len(received) == parallel:
                                completed.set()
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0, ssl=server_ctx)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1",
                upstream_port,
                ssl=client_ctx,
                server_hostname="apis.roblox.com" if authenticated_tls else None,
            )
            if authenticated_tls:
                negotiated = upstream_writer.get_extra_info("ssl_object")
                assert negotiated is not None and negotiated.selected_alpn_protocol() == "h2"
            upstream_connection = UpstreamConnectResult(
                reader=upstream_reader,
                writer=upstream_writer,
                method="local_h2_test",
                endpoint="127.0.0.1",
            )
            try:
                await proxy.serve_decrypted_h2_session(
                    reader, writer, "apis.roblox.com", upstream_connection
                )
            finally:
                writer.close()
                upstream_writer.close()
                await writer.wait_closed()
                await upstream_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0, ssl=server_ctx)
        bridge_port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(bridge_port, int)
        try:
            reader, writer = await asyncio.open_connection(
                "127.0.0.1",
                bridge_port,
                ssl=client_ctx,
                server_hostname="apis.roblox.com" if authenticated_tls else None,
            )
            if authenticated_tls:
                negotiated = writer.get_extra_info("ssl_object")
                assert negotiated is not None and negotiated.selected_alpn_protocol() == "h2"
            conn = H2Connection()
            conn.initiate_connection()
            streams: dict[int, bytearray] = {}
            ended: set[int] = set()
            for _ in range(parallel):
                stream = conn.get_next_available_stream_id()
                streams[stream] = bytearray()
                conn.send_headers(
                    stream,
                    [
                        (b":method", b"POST"),
                        (b":scheme", b"https"),
                        (b":authority", b"apis.roblox.com"),
                        (b":path", b"/test"),
                        (b"content-length", b"8"),
                    ],
                )
                conn.send_data(stream, b"original", end_stream=True)
            writer.write(conn.data_to_send())
            await writer.drain()

            async def collect() -> None:
                while len(ended) < parallel:
                    data = await reader.read(65536)
                    assert data, "HTTP/2 bridge closed before all responses"
                    for event in conn.receive_data(data):
                        if isinstance(event, DataReceived):
                            streams[event.stream_id].extend(event.data)
                            conn.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            ended.add(event.stream_id)
                        elif isinstance(event, ResponseReceived):
                            assert dict(event.headers)[b":status"] == b"200"
                    writer.write(conn.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=9)
            assert all(bytes(body) == b"client-original" for body in streams.values())
            await asyncio.wait_for(completed.wait(), timeout=3)
            assert received == [b"modified"] * parallel
            assert len(proxy.get_request_log()) == parallel
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            upstream_server.close()
            await bridge_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


def test_h2_bridge_large_multiplexed_responses_obey_flow_control(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        payload = b"upstream-original-" * 10000
        expected = b"client-original-" * 10000
        total_streams = 2

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            pending: dict[int, int] = {}
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, DataReceived):
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            h2.send_headers(event.stream_id, [(b":status", b"200")])
                            pending[event.stream_id] = 0
                    for stream, offset in list(pending.items()):
                        while offset < len(payload):
                            limit = min(
                                len(payload) - offset,
                                h2.local_flow_control_window(stream),
                                h2.max_outbound_frame_size,
                            )
                            if not limit:
                                break
                            end = offset + limit
                            h2.send_data(
                                stream, payload[offset:end], end_stream=end == len(payload)
                            )
                            offset = end
                        if offset == len(payload):
                            pending.pop(stream)
                        else:
                            pending[stream] = offset
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            upstream_reader, upstream_writer = await asyncio.open_connection(
                "127.0.0.1", upstream_port
            )
            try:
                await proxy.serve_decrypted_h2_session(
                    reader,
                    writer,
                    "apis.roblox.com",
                    UpstreamConnectResult(
                        reader=upstream_reader,
                        writer=upstream_writer,
                        method="controlled_local_test",
                        endpoint="127.0.0.1",
                    ),
                )
            finally:
                writer.close()
                upstream_writer.close()
                await writer.wait_closed()
                await upstream_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0)
        bridge_port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(bridge_port, int)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", bridge_port)
            h2 = H2Connection()
            h2.initiate_connection()
            response: dict[int, bytearray] = {}
            completed: set[int] = set()
            for _ in range(total_streams):
                stream = h2.get_next_available_stream_id()
                response[stream] = bytearray()
                h2.send_headers(
                    stream,
                    [
                        (b":method", b"GET"),
                        (b":scheme", b"https"),
                        (b":authority", b"apis.roblox.com"),
                        (b":path", b"/big"),
                    ],
                    end_stream=True,
                )
            writer.write(h2.data_to_send())
            await writer.drain()

            async def collect() -> None:
                while len(completed) < total_streams:
                    data = await reader.read(65536)
                    assert data, "HTTP/2 bridge closed during large body"
                    for event in h2.receive_data(data):
                        if isinstance(event, DataReceived):
                            response[event.stream_id].extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            completed.add(event.stream_id)
                    writer.write(h2.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=14)
            assert all(bytes(body) == expected for body in response.values())
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            upstream_server.close()
            await bridge_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


def test_h2_bridge_large_client_upload_respects_flow_control(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        body = b"original-" * 13000
        expected_body = b"modified-" * 13000
        upstream_received: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            request = bytearray()
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, DataReceived):
                            request.extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            upstream_received.append(bytes(request))
                            h2.send_headers(event.stream_id, [(b":status", b"200")])
                            h2.send_data(event.stream_id, b"upstream-original", end_stream=True)
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            try:
                await proxy.serve_decrypted_h2_session(
                    reader,
                    writer,
                    "apis.roblox.com",
                    UpstreamConnectResult(
                        reader=up_reader,
                        writer=up_writer,
                        method="controlled_local_test",
                        endpoint="127.0.0.1",
                    ),
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0)
        port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(port, int)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            h2 = H2Connection()
            h2.initiate_connection()
            stream = h2.get_next_available_stream_id()
            h2.send_headers(
                stream,
                [
                    (b":method", b"POST"),
                    (b":scheme", b"https"),
                    (b":authority", b"apis.roblox.com:443"),
                    (b":path", b"/upload"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            )
            position = 0
            response = bytearray()
            complete = False

            async def send_available() -> None:
                nonlocal position
                while position < len(body):
                    size = min(
                        len(body) - position,
                        h2.local_flow_control_window(stream),
                        h2.max_outbound_frame_size,
                    )
                    if not size:
                        break
                    end = position + size
                    h2.send_data(stream, body[position:end], end_stream=end == len(body))
                    position = end
                writer.write(h2.data_to_send())
                await writer.drain()

            async def exchange() -> None:
                nonlocal complete
                while not complete:
                    await send_available()
                    data = await reader.read(65536)
                    assert data, "HTTP/2 bridge unexpectedly closed"
                    for event in h2.receive_data(data):
                        if isinstance(event, DataReceived):
                            response.extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            complete = True
                await send_available()

            await asyncio.wait_for(exchange(), timeout=12)
            assert position == len(body)
            assert bytes(response) == b"client-original"
            assert upstream_received == [expected_body]
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            server.close()
            await bridge_server.wait_closed()
            await server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


@pytest.mark.parametrize("invalid_type", ["cross-host", "http-scheme", "connect", "reset"])
def test_h2_bridge_rejects_cross_authority_stream(tmp_path: Path, invalid_type: str) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        upstream_paths: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, RequestReceived):
                            upstream_paths.append(dict(event.headers)[b":path"])
                        elif isinstance(event, StreamEnded):
                            h2.send_headers(
                                event.stream_id, [(b":status", b"204")], end_stream=True
                            )
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            try:
                await proxy.serve_decrypted_h2_session(
                    reader,
                    writer,
                    "apis.roblox.com",
                    UpstreamConnectResult(
                        reader=up_reader,
                        writer=up_writer,
                        method="local_h2_test",
                        endpoint="127.0.0.1",
                    ),
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0)
        port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(port, int)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            h2 = H2Connection()
            h2.initiate_connection()
            invalid_headers = [
                (b":method", b"GET"),
                (b":scheme", b"http" if invalid_type == "http-scheme" else b"https"),
                (
                    b":authority",
                    b"evil.example" if invalid_type == "cross-host" else b"apis.roblox.com",
                ),
                (b":path", b"/blocked"),
            ]
            if invalid_type == "connect":
                invalid_headers = [
                    (b":method", b"CONNECT"),
                    (b":authority", b"apis.roblox.com"),
                ]
            first_stream = h2.get_next_available_stream_id()
            if invalid_type == "reset":
                h2.send_headers(
                    first_stream,
                    [
                        (b":method", b"POST"),
                        (b":scheme", b"https"),
                        (b":authority", b"apis.roblox.com"),
                        (b":path", b"/cancelled"),
                    ],
                )
                h2.send_data(first_stream, b"original")
                h2.reset_stream(first_stream)
            else:
                h2.send_headers(first_stream, invalid_headers, end_stream=True)
            h2.send_headers(
                h2.get_next_available_stream_id(),
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":authority", b"apis.roblox.com"),
                    (b":path", b"/accepted"),
                ],
                end_stream=True,
            )
            writer.write(h2.data_to_send())
            await writer.drain()
            resets: set[int] = set()
            completed: set[int] = set()

            async def collect() -> None:
                while not (3 in completed and (invalid_type == "reset" or 1 in resets)):
                    data = await reader.read(65536)
                    assert data, "HTTP/2 bridge closed without rejecting invalid authority"
                    for event in h2.receive_data(data):
                        if isinstance(event, StreamReset):
                            resets.add(event.stream_id)
                        elif isinstance(event, StreamEnded):
                            completed.add(event.stream_id)
                    writer.write(h2.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=6)
            assert upstream_paths == [b"/accepted"]
            assert len(proxy.get_request_log()) == 1
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            server.close()
            await bridge_server.wait_closed()
            await server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


def test_h2_bridge_rejects_truncated_upstream_response(tmp_path: Path) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, StreamEnded):
                            h2.send_headers(event.stream_id, [(b":status", b"200")])
                            h2.send_data(event.stream_id, b"partial-data", end_stream=False)
                            writer.write(h2.data_to_send())
                            await writer.drain()
                            # Disappear before the HTTP/2 END_STREAM marker
                            return
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            try:
                await proxy.serve_decrypted_h2_session(
                    reader,
                    writer,
                    "apis.roblox.com",
                    UpstreamConnectResult(
                        reader=up_reader,
                        writer=up_writer,
                        method="controlled_local_test",
                        endpoint="127.0.0.1",
                    ),
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0)
        port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(port, int)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            h2 = H2Connection()
            h2.initiate_connection()
            h2.send_headers(
                1,
                [
                    (b":method", b"GET"),
                    (b":scheme", b"https"),
                    (b":authority", b"apis.roblox.com"),
                    (b":path", b"/truncated"),
                ],
                end_stream=True,
            )
            writer.write(h2.data_to_send())
            await writer.drain()
            result: list[str] = []

            async def check_failure() -> None:
                while not result:
                    data = await reader.read(65536)
                    assert data, "Bridge closed instead of signaling the failed stream"
                    for event in h2.receive_data(data):
                        if isinstance(event, StreamReset):
                            result.append("reset")
                        elif isinstance(event, StreamEnded):
                            result.append("completed")
                    writer.write(h2.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(check_failure(), timeout=6)
            assert result == ["reset"]
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            server.close()
            await bridge_server.wait_closed()
            await server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


@pytest.mark.parametrize("trailer_direction", ["request", "response", "informational"])
def test_h2_bridge_rejects_unsupported_trailers(tmp_path: Path, trailer_direction: str) -> None:
    async def run() -> None:
        proxy = _proxy(tmp_path)
        upstream_paths: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            try:
                while data := await reader.read(65536):
                    for event in h2.receive_data(data):
                        if isinstance(event, RequestReceived):
                            upstream_paths.append(dict(event.headers)[b":path"])
                        elif isinstance(event, StreamEnded):
                            if trailer_direction == "informational":
                                h2.send_headers(
                                    event.stream_id,
                                    [(b":status", b"103"), (b"link", b"</early>; rel=preload")],
                                )
                            h2.send_headers(event.stream_id, [(b":status", b"200")])
                            h2.send_data(event.stream_id, b"upstream-original")
                            if trailer_direction == "response":
                                h2.send_headers(
                                    event.stream_id, [(b"x-integrity", b"example")], end_stream=True
                                )
                            else:
                                h2.end_stream(event.stream_id)
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, "127.0.0.1", 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def bridge_handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", upstream_port)
            try:
                await proxy.serve_decrypted_h2_session(
                    reader,
                    writer,
                    "apis.roblox.com",
                    UpstreamConnectResult(
                        reader=up_reader,
                        writer=up_writer,
                        method="controlled_local_test",
                        endpoint="127.0.0.1",
                    ),
                )
            finally:
                writer.close()
                up_writer.close()
                await writer.wait_closed()
                await up_writer.wait_closed()

        bridge_server = await asyncio.start_server(bridge_handler, "127.0.0.1", 0)
        bridge_port = bridge_server.sockets[0].getsockname()[1]
        assert isinstance(bridge_port, int)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", bridge_port)
            h2 = H2Connection()
            h2.initiate_connection()
            h2.send_headers(
                1,
                [
                    (b":method", b"POST"),
                    (b":scheme", b"https"),
                    (b":authority", b"apis.roblox.com"),
                    (b":path", b"/with-trailers"),
                ],
                end_stream=trailer_direction != "request",
            )
            if trailer_direction == "request":
                h2.send_data(1, b"original")
                h2.send_headers(1, [(b"x-integrity", b"example")], end_stream=True)
            writer.write(h2.data_to_send())
            await writer.drain()
            events: list[str] = []

            async def collect() -> None:
                while not events:
                    data = await reader.read(65536)
                    assert data, "HTTP/2 bridge closed before resetting unsupported trailers"
                    for event in h2.receive_data(data):
                        if isinstance(event, StreamReset):
                            events.append("reset")
                        elif isinstance(event, StreamEnded):
                            events.append("completed")
                    writer.write(h2.data_to_send())
                    await writer.drain()

            await asyncio.wait_for(collect(), timeout=6)
            assert events == ["reset"]
            assert upstream_paths == ([] if trailer_direction == "request" else [b"/with-trailers"])
            writer.close()
            await writer.wait_closed()
        finally:
            bridge_server.close()
            upstream_server.close()
            await bridge_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())
