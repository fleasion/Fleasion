from __future__ import annotations

import asyncio
import socket
import ssl
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from cryptography.exceptions import InvalidTag

from fleasion.proxy.addons.texture_stripper import TextureStripper
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.splice_keys import KeyProvider
from fleasion.proxy.tls_records import MAX_PLAINTEXT, TrafficKeys, expand_label
from fleasion.proxy.upstream import UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert

if TYPE_CHECKING:
    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.addons.cache_scraper import CacheScraper


@pytest.mark.parametrize('cipher', [0x1301, 0x1302, 0x1303])
def test_padding_binary_zeros_and_key_update(cipher: int) -> None:
    secret = bytes(range(48 if cipher == 0x1302 else 32))
    writer, reader = TrafficKeys(secret, cipher), TrafficKeys(secret, cipher)
    content = b'\x14\x00\x00\x20' + bytes(32)
    assert reader.decrypt(writer.encrypt(content, 22, padding=15)) == (22, content)
    old_secret = writer.secret
    writer.update()
    reader.update()
    hash_name = 'sha384' if cipher == 0x1302 else 'sha256'
    assert writer.secret == expand_label(old_secret, b'traffic upd', len(secret), hash_name)
    assert len(writer.secret) == len(secret)
    assert writer.sequence == reader.sequence == 0
    assert reader.decrypt(writer.encrypt(bytes(MAX_PLAINTEXT))) == (23, bytes(MAX_PLAINTEXT))


def test_bad_tag_does_not_advance_sequence() -> None:
    writer, reader = TrafficKeys(bytes(32), 0x1301), TrafficKeys(bytes(32), 0x1301)
    record = writer.encrypt(b'body')
    with pytest.raises(InvalidTag):
        reader.decrypt(record[:-1] + bytes([record[-1] ^ 1]))
    assert reader.decrypt(record) == (23, b'body')


@pytest.mark.parametrize('explicit', [True, False])
def test_shutdown_closes_idle_client_before_handshake(explicit: bool) -> None:
    async def run() -> None:
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace()),
            host_certs={},
            port=0,
            explicit_proxy=explicit,
            tls_splice=True,
        )
        await proxy.start()
        reader, writer = await asyncio.open_connection('127.0.0.1', proxy.port)
        await asyncio.sleep(0)
        await asyncio.wait_for(proxy.stop(), timeout=1)
        assert await asyncio.wait_for(reader.read(), timeout=1) == b''
        writer.close()
        await writer.wait_closed()

    asyncio.run(run())


def test_key_provider_retries_missing_or_partial_keylog(tmp_path: Path) -> None:
    async def run() -> None:
        keylog = tmp_path / 'keys.log'
        provider = KeyProvider(keylog)
        client_random = 'ab' * 32

        async def publish() -> None:
            await asyncio.sleep(0.15)
            keylog.write_text(
                '# Test keys only\nmalformed line\n'
                f'CLIENT_TRAFFIC_SECRET_0 {client_random} not-hex\n'
                f'CLIENT_TRAFFIC_SECRET_0 {client_random} {"cd" * 32}\n',
                encoding='ascii',
            )

        task = asyncio.create_task(publish())
        assert (
            await provider.get(client_random, 'CLIENT_TRAFFIC_SECRET_0', wait_seconds=2)
            == bytes.fromhex('cd' * 32)
        )
        await task
        provider.clear()

    asyncio.run(run())


@pytest.mark.parametrize('explicit', [True, False])
@pytest.mark.parametrize('framing', ['length', 'chunked', 'eof', 'local'])
def test_real_tls_reuses_existing_http_rules_and_logging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, framing: str, explicit: bool
) -> None:
    host = 'fts.rbxcdn.com' if framing == 'local' else 'splice.test'
    ca, key = generate_ca(tmp_path)
    cert, leaf_key = generate_host_cert(host, ca, key, tmp_path)
    keylog = tmp_path / 'server.keys'
    monkeypatch.setenv('FLEASION_SPLICE_KEYLOG', str(keylog))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, leaf_key)
    server_context.minimum_version = server_context.maximum_version = ssl.TLSVersion.TLSv1_3
    server_context.set_alpn_protocols(['http/1.1'])
    server_context.keylog_filename = str(keylog)
    replacement = 'x' * 40000
    received: list[bytes] = []
    cached: list[bytes] = []

    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if framing == 'local':
                assert await reader.read() == b''
                return
            for number in range(1 if framing in {'eof', 'local'} else 2):
                headers = await reader.readuntil(b'\r\n\r\n')
                length = next(
                    (
                        int(line.split(b':')[1])
                        for line in headers.split(b'\r\n')
                        if line.lower().startswith(b'content-length:')
                    ),
                    0,
                )
                received.append(await reader.readexactly(length))
                body = b'original' if number == 0 else b'second'
                connection = b'close' if number or framing == 'eof' else b'keep-alive'
                head = b'HTTP/1.1 200 OK\r\nConnection: ' + connection + b'\r\n'
                if framing == 'chunked':
                    response = head + b'Transfer-Encoding: chunked\r\n\r\n'
                    response += f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n'
                elif framing == 'eof':
                    response = head + b'\r\n' + body
                else:
                    response = head + f'Content-Length: {len(body)}\r\n\r\n'.encode() + body
                # Fragment headers and payload across independent TLS records
                for offset in range(0, len(response), 7):
                    writer.write(response[offset : offset + 7])
                    await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def client(port: int) -> list[bytes]:
        context = ssl.create_default_context(cafile=str(ca))
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(['http/1.1'])
        with socket.create_connection(('127.0.0.1', port), timeout=5) as raw:
            if explicit:
                raw.sendall(f'CONNECT {host}:443 HTTP/1.1\r\n\r\n'.encode())
                connect_response = bytearray()
                while not connect_response.endswith(b'\r\n\r\n'):
                    connect_response.extend(raw.recv(1))
                assert connect_response.startswith(b'HTTP/1.1 200 ')
            with context.wrap_socket(raw, server_hostname=host) as tls:
                # The peer certificate is the real upstream certificate
                assert tls.getpeercert(binary_form=True) == ssl.PEM_cert_to_DER_cert(
                    cert.read_text()
                )
                with tls.makefile('rb') as source:
                    results: list[bytes] = []
                    for number in range(1 if framing in {'eof', 'local'} else 2):
                        body = b'original' if number == 0 else b''
                        tls.sendall(
                            f'POST /asset/{number} HTTP/1.1\r\nHost: {host}\r\n'.encode()
                            + f'Content-Length: {len(body)}\r\n\r\n'.encode()
                            + body
                        )
                        assert source.readline().startswith(b'HTTP/1.1 200 ')
                        headers: dict[bytes, bytes] = {}
                        while (line := source.readline()) != b'\r\n':
                            assert line
                            name, value = line.split(b':', 1)
                            headers[name.lower()] = value.strip()
                        results.append(source.read(int(headers[b'content-length'])))
                    return results

    async def run() -> None:
        server = await asyncio.start_server(upstream, '127.0.0.1', 0, ssl=server_context)
        upstream_port = server.sockets[0].getsockname()[1]
        texture = TextureStripper(
            cast(
                'ConfigManager',
                SimpleNamespace(
                    get_all_replacements=lambda: (
                        dict[str, str](),
                        set[str](),
                        dict[str, str](),
                        dict[str, str](),
                    )
                ),
            )
        )
        cache_ready = asyncio.Event()

        def capture(_url: str, _path: str, body: bytes, _mime: str) -> None:
            cached.append(body)
            cache_ready.set()

        if framing == 'local':
            local = tmp_path / 'replacement.bin'
            local.write_bytes(replacement.encode())
            monkeypatch.setattr(
                TextureStripper, '_local_redirects', {f'https://{host}/asset/0': str(local)}
            )
        proxy = FleasionProxy(
            texture_stripper=texture,
            cache_scraper=cast(
                'CacheScraper',
                SimpleNamespace(enabled=framing == 'local', process_cdn_response=capture),
            ),
            host_certs={},
            port=0,
            explicit_proxy=explicit,
            tls_splice=True,
            intercept_hosts={host},
            intercept_all_hosts=True,
            upstream_endpoints={host: [UpstreamEndpoint(host, upstream_port, '127.0.0.1')]},
            auto_replace_rules=[
                {'direction': 'request', 'match': 'original', 'replacement': 'request-rewritten'},
                {'direction': 'response', 'match': 'original', 'replacement': replacement},
            ],
        )
        try:
            await proxy.start()
            result = await asyncio.wait_for(asyncio.to_thread(client, proxy.port), timeout=10)
            assert result[0] == replacement.encode()
            if framing == 'local':
                await asyncio.wait_for(cache_ready.wait(), timeout=2)
                assert cached == [replacement.encode()]
                assert received == []
            else:
                assert received[0] == b'request-rewritten'
            if framing not in {'eof', 'local'}:
                assert result[1] == b'second'
                assert len(proxy.get_request_log()) == 2
        finally:
            await proxy.stop()
            server.close()
            await server.wait_closed()

    asyncio.run(run())


def test_splice_closes_both_tls_directions_with_notify() -> None:
    from fleasion.proxy.splice import SpliceConnection

    class MemoryWire:
        def __init__(self) -> None:
            self.data = bytearray()
            self.closed = False

        def write(self, payload: bytes) -> None:
            self.data.extend(payload)

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

        def is_closing(self) -> bool:
            return self.closed

        async def wait_closed(self) -> None:
            pass

    class Session:
        def mark_splice_decrypted(self, _host: str) -> bool:
            return False

        async def serve_spliced_session(
            self,
            reader: asyncio.StreamReader,
            _writer: asyncio.StreamWriter,
            _host: str,
            _upstream: object,
        ) -> None:
            assert await reader.read() == b''

    async def run() -> None:
        client_reader, server_reader = asyncio.StreamReader(), asyncio.StreamReader()
        client_wire, server_wire = MemoryWire(), MemoryWire()
        connection = SpliceConnection(
            client_reader,
            cast('asyncio.StreamWriter', client_wire),
            server_reader,
            cast('asyncio.StreamWriter', server_wire),
            keys=KeyProvider(),
        )
        secret = b'c' * 32
        for direction in (connection.client, connection.server):
            direction.input_keys = TrafficKeys(secret, 0x1301)
            direction.writer.keys = TrafficKeys(secret, 0x1301)
            direction.handshake_keys = TrafficKeys(secret, 0x1301)
        connection.client_finished_relayed = True
        connection._application_ready.set()  # pyright: ignore[reportPrivateUsage]
        client_reader.feed_data(TrafficKeys(secret, 0x1301).encrypt(b'\x01\x00', 21))
        await asyncio.wait_for(run_connection(connection), timeout=1.0)
        for wire in (client_wire, server_wire):
            assert wire.closed
            assert TrafficKeys(secret, 0x1301).decrypt(bytes(wire.data)) == (21, b'\x01\x00')

    async def run_connection(connection: SpliceConnection) -> None:
        await connection.run(cast('FleasionProxy', Session()), 'splice.test')

    asyncio.run(run())
