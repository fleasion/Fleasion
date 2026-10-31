"""Drive Fleasion's existing HTTP features over synthetic encrypted TCP records."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.tls_records import CipherSuite, TLS13RecordSession
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
