"""Drive Fleasion's existing HTTP features over synthetic encrypted TCP records."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.tls_records import CipherSuite, TLS13RecordSession, TLSRecordError
from fleasion.proxy.types import AutoReplaceRule
from fleasion.proxy.upstream import UpstreamConnectResult
from fleasion.utils.certs import generate_ca, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper

_CLIENT_TEST_SECRET_256 = b'c' * 32
_SERVER_TEST_SECRET_256 = b's' * 32
_CLIENT_TEST_SECRET_384 = b'c' * 48
_SERVER_TEST_SECRET_384 = b's' * 48


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


class _RecordWriter(asyncio.StreamWriter):
    """Test-only plaintext writer backed by encrypted localhost records."""

    def __init__(self, session: TLS13RecordSession, writer: asyncio.StreamWriter) -> None:
        self.session = session
        self.writer = writer
        self.buffer = bytearray()
        self.closing = False

    def write(self, data: bytes | bytearray | memoryview) -> None:
        if self.closing:
            msg = 'Synthetic record writer closed'
            raise ConnectionError(msg)
        self.buffer.extend(data)

    async def drain(self) -> None:
        if self.buffer:
            self.writer.write(self.session.send_application(bytes(self.buffer)))
            self.buffer.clear()
            await self.writer.drain()

    def close(self) -> None:
        self.closing = True

    def is_closing(self) -> bool:
        return self.closing

    async def wait_closed(self) -> None:
        self.closing = True

    def __del__(self, _warnings: object = None) -> None:
        # The encrypted TCP writer owns the actual asyncio transport
        pass


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_synthetic_tls_records_drive_http_feature_engine(
    tmp_path: Path, suite: CipherSuite
) -> None:
    async def run() -> None:
        client_secret = (
            _CLIENT_TEST_SECRET_384
            if suite is CipherSuite.AES_256_GCM_SHA384
            else _CLIENT_TEST_SECRET_256
        )
        server_secret = (
            _SERVER_TEST_SECRET_384
            if suite is CipherSuite.AES_256_GCM_SHA384
            else _SERVER_TEST_SECRET_256
        )
        ca, key = generate_ca(tmp_path)
        cert = generate_multi_host_cert(
            'synthetic-record-test', {'apis.roblox.com'}, ca, key, tmp_path
        )
        rules: list[AutoReplaceRule] = [
            {'direction': 'request', 'match': 'original', 'replacement': 'modified'},
            {'direction': 'response', 'match': 'upstream', 'replacement': 'client'},
        ]
        proxy = FleasionProxy(
            texture_stripper=cast(
                'TextureStripper',
                SimpleNamespace(
                    config_manager=SimpleNamespace(get_all_replacements=_empty_replacements)
                ),
            ),
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={},
            default_cert=cert,
            intercept_hosts={'apis.roblox.com'},
            intercept_all_hosts=True,
            auto_replace_rules=rules,
        )
        upstream_requests: list[bytes] = []

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                header = await reader.readuntil(b'\r\n\r\n')
                content_length = next(
                    int(line.split(b':', 1)[1])
                    for line in header.lower().split(b'\r\n')
                    if line.startswith(b'content-length:')
                )
                upstream_requests.append(await reader.readexactly(content_length))
                body = b'upstream-original'
                writer.write(
                    b'HTTP/1.1 200 OK\r\n'
                    + f'Content-Length: {len(body)}\r\n'.encode()
                    + b'Connection: close\r\n\r\n'
                    + body
                )
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def controlled_gateway(
            encrypted_reader: asyncio.StreamReader, encrypted_writer: asyncio.StreamWriter
        ) -> None:
            session = TLS13RecordSession(
                inbound_secret=client_secret,
                outbound_secret=server_secret,
                suite=suite,
            )
            upstream_writer: asyncio.StreamWriter | None = None
            try:
                plaintext = asyncio.StreamReader()
                have_request = False
                while not have_request:
                    packet = await encrypted_reader.read(3)
                    if not packet:
                        session.finish()
                        msg = 'Synthetic sender closed before complete request'
                        raise ConnectionError(msg)
                    messages = session.receive(packet)
                    if messages:
                        assert len(messages) == 1
                        plaintext.feed_data(messages[0].content)
                        plaintext.feed_eof()
                        have_request = True
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    '127.0.0.1', upstream_port
                )
                await proxy.serve_decrypted_http_session(
                    plaintext,
                    _RecordWriter(session, encrypted_writer),
                    'apis.roblox.com',
                    UpstreamConnectResult(
                        reader=upstream_reader,
                        writer=upstream_writer,
                        method='synthetic_record_test',
                        endpoint='127.0.0.1',
                    ),
                )
            finally:
                if upstream_writer is not None:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                encrypted_writer.close()
                await encrypted_writer.wait_closed()

        gateway_server = await asyncio.start_server(controlled_gateway, '127.0.0.1', 0)
        gateway_port = gateway_server.sockets[0].getsockname()[1]
        assert isinstance(gateway_port, int)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', gateway_port)
            session = TLS13RecordSession(
                inbound_secret=server_secret,
                outbound_secret=client_secret,
                suite=suite,
            )
            body = b'original'
            request = (
                b'POST /synthetic HTTP/1.1\r\n'
                + b'Host: apis.roblox.com\r\n'
                + f'Content-Length: {len(body)}\r\n'.encode()
                + b'Connection: close\r\n\r\n'
                + body
            )
            wire = session.send_application(request)
            for segment in (wire[:3], wire[3:15], wire[15:]):
                writer.write(segment)
                await writer.drain()
            response_parts: list[bytes] = []
            while packet := await asyncio.wait_for(reader.read(4096), timeout=3):
                for record in session.receive(packet):
                    assert record.content_type == 23
                    response_parts.append(record.content)
            session.finish()
            assert b''.join(response_parts).endswith(b'client-original')
            assert upstream_requests == [b'modified']
            assert len(proxy.get_request_log()) == 1
            writer.close()
            await writer.wait_closed()
        finally:
            gateway_server.close()
            upstream_server.close()
            await gateway_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())


@pytest.mark.parametrize('suite', list(CipherSuite))
def test_synthetic_tls_records_drive_http2_feature_engine(
    tmp_path: Path, suite: CipherSuite
) -> None:
    from h2.config import H2Configuration
    from h2.connection import H2Connection
    from h2.events import DataReceived, RequestReceived, ResponseReceived, StreamEnded

    async def run() -> None:
        client_secret = (
            _CLIENT_TEST_SECRET_384
            if suite is CipherSuite.AES_256_GCM_SHA384
            else _CLIENT_TEST_SECRET_256
        )
        server_secret = (
            _SERVER_TEST_SECRET_384
            if suite is CipherSuite.AES_256_GCM_SHA384
            else _SERVER_TEST_SECRET_256
        )
        ca, key = generate_ca(tmp_path)
        cert = generate_multi_host_cert(
            'synthetic-http2-records', {'apis.roblox.com'}, ca, key, tmp_path
        )
        rules: list[AutoReplaceRule] = [
            {'direction': 'request', 'match': 'original', 'replacement': 'modified'},
            {'direction': 'response', 'match': 'upstream', 'replacement': 'client'},
        ]
        proxy = FleasionProxy(
            texture_stripper=cast(
                'TextureStripper',
                SimpleNamespace(
                    config_manager=SimpleNamespace(get_all_replacements=_empty_replacements)
                ),
            ),
            cache_scraper=cast('CacheScraper', SimpleNamespace(enabled=False)),
            host_certs={},
            default_cert=cert,
            intercept_hosts={'apis.roblox.com'},
            intercept_all_hosts=True,
            auto_replace_rules=rules,
        )
        received_upstream: list[bytes] = []
        handler_finished = asyncio.Event()

        async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            h2 = H2Connection(H2Configuration(client_side=False))
            h2.initiate_connection()
            writer.write(h2.data_to_send())
            await writer.drain()
            requests: dict[int, bytearray] = {}
            try:
                while packet := await reader.read(65536):
                    for event in h2.receive_data(packet):
                        if isinstance(event, RequestReceived):
                            assert dict(event.headers)[b':authority'] == b'apis.roblox.com'
                            requests[event.stream_id] = bytearray()
                        elif isinstance(event, DataReceived):
                            requests[event.stream_id].extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded):
                            received_upstream.append(bytes(requests.pop(event.stream_id)))
                            h2.send_headers(event.stream_id, [(b':status', b'200')])
                            h2.send_data(event.stream_id, b'upstream-original', end_stream=True)
                    writer.write(h2.data_to_send())
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        upstream_server = await asyncio.start_server(upstream, '127.0.0.1', 0)
        upstream_port = upstream_server.sockets[0].getsockname()[1]
        assert isinstance(upstream_port, int)

        async def controlled_gateway(
            encrypted_reader: asyncio.StreamReader, encrypted_writer: asyncio.StreamWriter
        ) -> None:
            session = TLS13RecordSession(
                inbound_secret=client_secret,
                outbound_secret=server_secret,
                suite=suite,
            )
            plaintext_reader = asyncio.StreamReader()
            upstream_writer: asyncio.StreamWriter | None = None

            async def receive_records() -> None:
                try:
                    while packet := await encrypted_reader.read(7):
                        for record in session.receive(packet):
                            if record.content_type == 23:
                                plaintext_reader.feed_data(record.content)
                            elif record.content_type == 21:
                                assert session.inbound_closed
                                plaintext_reader.feed_eof()
                                return
                    session.finish()
                except TLSRecordError as exc:
                    plaintext_reader.set_exception(exc)
                finally:
                    if not plaintext_reader.at_eof():
                        plaintext_reader.feed_eof()

            recv_task = asyncio.create_task(receive_records())
            try:
                upstream_reader, upstream_writer = await asyncio.open_connection(
                    '127.0.0.1', upstream_port
                )
                await proxy.serve_decrypted_h2_session(
                    plaintext_reader,
                    _RecordWriter(session, encrypted_writer),
                    'apis.roblox.com',
                    UpstreamConnectResult(
                        reader=upstream_reader,
                        writer=upstream_writer,
                        method='synthetic_http2_record_test',
                        endpoint='127.0.0.1',
                    ),
                )
            finally:
                recv_task.cancel()
                await asyncio.gather(recv_task, return_exceptions=True)
                if upstream_writer is not None:
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                encrypted_writer.close()
                await encrypted_writer.wait_closed()
                handler_finished.set()

        gateway_server = await asyncio.start_server(controlled_gateway, '127.0.0.1', 0)
        gateway_port = gateway_server.sockets[0].getsockname()[1]
        assert isinstance(gateway_port, int)
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', gateway_port)
            session = TLS13RecordSession(
                inbound_secret=server_secret, outbound_secret=client_secret, suite=suite
            )
            h2 = H2Connection()
            h2.initiate_connection()
            h2.send_headers(
                1,
                [
                    (b':method', b'POST'),
                    (b':scheme', b'https'),
                    (b':authority', b'apis.roblox.com'),
                    (b':path', b'/synthetic-h2'),
                ],
            )
            h2.send_data(1, b'original', end_stream=True)
            plain_request = h2.data_to_send()
            for fragment in (plain_request[:31], plain_request[31:]):
                wire = session.send_application(fragment)
                for offset in range(0, len(wire), 11):
                    writer.write(wire[offset : offset + 11])
                    await writer.drain()
            response = bytearray()
            response_status: bytes | None = None
            finished = False
            while not finished:
                packet = await asyncio.wait_for(reader.read(4096), timeout=4)
                assert packet, 'HTTP/2 response transport terminated unexpectedly'
                for record in session.receive(packet):
                    assert record.content_type == 23
                    for event in h2.receive_data(record.content):
                        if isinstance(event, ResponseReceived):
                            response_status = dict(event.headers)[b':status']
                        elif isinstance(event, DataReceived):
                            response.extend(event.data)
                            h2.acknowledge_received_data(
                                event.flow_controlled_length, event.stream_id
                            )
                        elif isinstance(event, StreamEnded) and event.stream_id == 1:
                            finished = True
                acknowledgments = h2.data_to_send()
                if acknowledgments:
                    writer.write(session.send_application(acknowledgments))
                    await writer.drain()
            assert response_status == b'200'
            assert bytes(response) == b'client-original'
            assert received_upstream == [b'modified']
            assert len(proxy.get_request_log()) == 1
            writer.write(session.send_close_notify())
            await writer.drain()
            await asyncio.wait_for(handler_finished.wait(), timeout=2)
            writer.close()
            await writer.wait_closed()
        finally:
            gateway_server.close()
            upstream_server.close()
            await gateway_server.wait_closed()
            await upstream_server.wait_closed()
            await proxy.stop()

    asyncio.run(run())
