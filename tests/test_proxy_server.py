import asyncio
import socket
import ssl
import tempfile
import unittest
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy import server as proxy_server
from fleasion.proxy.server import (
    ASSET_DELIVERY_HOST,
    PROFILE_API_HOST,
    FleasionProxy,
    RawBody,
    RawHeaders,
)
from fleasion.proxy.upstream import (
    AutoConnector,
    BaseUpstreamConnector,
    UpstreamConnectResult,
    UpstreamEndpoint,
)
from fleasion.utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def _texture_stub(**values: object) -> TextureStripper:
    return cast('TextureStripper', SimpleNamespace(**values))


def _cache_stub(**values: object) -> CacheScraper:
    return cast('CacheScraper', SimpleNamespace(**values))


def _build_modified_request(req_line: bytes, headers: dict[bytes, bytes], body: bytes) -> bytes:
    callback = cast(
        'Callable[[bytes, dict[bytes, bytes], bytes], bytes]',
        proxy_server.__dict__['_build_modified_request'],
    )
    return callback(req_line, headers, body)


def _is_empty_json_array(body: bytes) -> bool:
    callback = cast('Callable[[bytes], bool]', proxy_server.__dict__['_is_empty_json_array'])
    return callback(body)


async def _open_explicit_proxy_tunnel(
    host: str, port: int, *, timeout: float = 10.0
) -> UpstreamConnectResult:
    callback = cast(
        'Callable[..., Awaitable[UpstreamConnectResult]]',
        proxy_server.__dict__['_open_explicit_proxy_tunnel'],
    )
    return await callback(host, port, timeout=timeout)


async def _read_headers_raw(reader: asyncio.StreamReader) -> RawHeaders | None:
    callback = cast(
        'Callable[[asyncio.StreamReader], Awaitable[RawHeaders | None]]',
        proxy_server.__dict__['_read_headers_raw'],
    )
    return await callback(reader)


async def _read_body_wire(reader: asyncio.StreamReader, headers: dict[bytes, bytes]) -> RawBody:
    callback = cast(
        'Callable[[asyncio.StreamReader, dict[bytes, bytes]], Awaitable[RawBody]]',
        proxy_server.__dict__['_read_body_wire'],
    )
    return await callback(reader, headers)


def _serve_local_file(path: str) -> bytes:
    callback = cast('Callable[[str], bytes]', proxy_server.__dict__['_serve_local_file'])
    return callback(path)


def _preserve_wire(proxy: FleasionProxy, host: str) -> bool:
    callback = cast(
        'Callable[[FleasionProxy, str], bool]',
        FleasionProxy.__dict__['_preserve_unmodified_wire_for_host'],
    )
    return callback(proxy, host)


def _notify_upstream_failure(proxy: FleasionProxy, host: str, error: str) -> None:
    callback = cast(
        'Callable[[FleasionProxy, str, str], None]',
        FleasionProxy.__dict__['_notify_upstream_connect_failure_once'],
    )
    callback(proxy, host, error)


async def _connect_upstream(
    proxy: FleasionProxy, host: str, *, timeout: float = 10.0
) -> UpstreamConnectResult:
    callback = cast(
        'Callable[..., Awaitable[UpstreamConnectResult]]',
        FleasionProxy.__dict__['_connect_upstream'],
    )
    return await callback(proxy, host, timeout=timeout)


def _host_context(proxy: FleasionProxy, host: str) -> ssl.SSLContext | None:
    callback = cast(
        'Callable[[FleasionProxy, str], ssl.SSLContext | None]',
        FleasionProxy.__dict__['_get_or_generate_host_ctx'],
    )
    return callback(proxy, host)


def _should_intercept(proxy: FleasionProxy, host: str, port: int) -> bool:
    callback = cast(
        'Callable[[FleasionProxy, str, int], bool]',
        FleasionProxy.__dict__['_should_intercept_explicit_host'],
    )
    return callback(proxy, host, port)


def _server_port(server: object) -> int:
    sockets = cast('Sequence[socket.socket] | None', getattr(server, 'sockets'))
    assert sockets
    address = sockets[0].getsockname()
    assert isinstance(address, tuple)
    return cast(int, address[1])


def _empty_replacements() -> tuple[
    dict[object, object], set[object], dict[object, object], dict[object, object]
]:
    return {}, set(), {}, {}


def _identity_batch_request(body: bytes, _replacements: object) -> bytes:
    return body


def _record_failure(values: list[tuple[str, str]]) -> Callable[[str, str], None]:
    def record(host: str, error: str) -> None:
        values.append((host, error))

    return record


def _record_real_ips(
    values: list[dict[str, list[str]]],
) -> Callable[[dict[str, list[str]]], None]:
    def record(update: dict[str, list[str]]) -> None:
        values.append(update)

    return record


async def _read_message(data: bytes) -> tuple[RawHeaders, RawBody]:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    headers = await _read_headers_raw(reader)
    assert headers is not None
    body = await _read_body_wire(reader, headers.headers)
    return headers, body


def _response_body(response: bytes) -> bytes:
    return response.split(b'\r\n\r\n', 1)[1]


class _FakeUpstreamWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closed

    def close(self) -> None:
        self.closed = True


def test_upstream_self_test_serializes_and_fully_closes_probes() -> None:
    events: list[tuple[str, str]] = []

    class _ProbeWriter:
        def __init__(self, host: str) -> None:
            self.host = host

        def close(self) -> None:
            events.append(('close', self.host))

        async def wait_closed(self) -> None:
            events.append(('wait_closed', self.host))

    class _ProbeConnector:
        async def connect(
            self,
            host: str,
            _endpoints: Sequence[UpstreamEndpoint],
            _ssl_ctx: ssl.SSLContext | None,
            timeout: float,
        ) -> UpstreamConnectResult:
            assert timeout == 3.0
            events.append(('connect', host))
            return UpstreamConnectResult(
                reader=cast(asyncio.StreamReader, object()),
                writer=cast(asyncio.StreamWriter, _ProbeWriter(host)),
                method='direct_ip',
                endpoint='192.0.2.1',
            )

    proxy = FleasionProxy.__new__(FleasionProxy)
    direct = _ProbeConnector()
    proxy.__dict__['_upstream_endpoints'] = {
        'a.example': [UpstreamEndpoint(host='a.example', ip='192.0.2.1')],
        'b.example': [UpstreamEndpoint(host='b.example', ip='192.0.2.2')],
    }
    proxy.__dict__['_direct_connector'] = direct
    proxy.__dict__['_system_http_connector'] = None
    proxy.__dict__['_manual_http_connector'] = None
    proxy.__dict__['_manual_socks5_connector'] = None
    proxy.__dict__['_upstream_ssl_ctx'] = None
    proxy.__dict__['_connector'] = direct

    asyncio.run(proxy.log_upstream_self_test({'b.example', 'a.example'}))

    assert events == [
        ('connect', 'a.example'),
        ('close', 'a.example'),
        ('wait_closed', 'a.example'),
        ('connect', 'b.example'),
        ('close', 'b.example'),
        ('wait_closed', 'b.example'),
    ]


class ProxyServerRawHttpTests(unittest.TestCase):
    def test_raw_header_preservation_duplicate_headers_and_casing(self) -> None:
        data = (
            b'GET /asset HTTP/1.1\r\n'
            b'Host: assetdelivery.roblox.com\r\n'
            b'X-Dupe: one\r\n'
            b'x-dupe: two\r\n'
            b'\r\n'
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertEqual(headers.raw_header_block, data)
        self.assertEqual(headers.first_line, b'GET /asset HTTP/1.1')
        self.assertEqual(headers.headers[b'x-dupe'], b'two')
        self.assertEqual(headers.raw_header_block.count(b'X-Dupe'), 1)
        self.assertEqual(headers.raw_header_block.count(b'x-dupe'), 1)
        self.assertEqual(body.wire, b'')
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_bodyless_get_passthrough_does_not_inject_content_length(self) -> None:
        data = b'GET /v1/assets/batch HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n\r\n'

        headers, body = asyncio.run(_read_message(data))

        forwarded = headers.raw_header_block + body.wire
        self.assertEqual(forwarded, data)
        self.assertNotIn(b'content-length', forwarded.lower())

    def test_content_length_post_exact_passthrough(self) -> None:
        data = (
            b'POST /v1/assets/batch HTTP/1.1\r\n'
            b'Host: assetdelivery.roblox.com\r\n'
            b'Content-Length: 11\r\n'
            b'\r\n'
            b'hello world'
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertEqual(body.payload, b'hello world')
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_chunked_request_exact_wire_preservation(self) -> None:
        data = (
            b'POST /chunk HTTP/1.1\r\n'
            b'Host: assetdelivery.roblox.com\r\n'
            b'Transfer-Encoding: chunked\r\n'
            b'\r\n'
            b'5;ext=1\r\nhello\r\n'
            b'6\r\n world\r\n'
            b'0\r\nTrailer: value\r\n\r\n'
        )

        headers, body = asyncio.run(_read_message(data))

        self.assertTrue(body.was_chunked)
        self.assertEqual(body.payload, b'hello world')
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_chunked_response_exact_wire_preservation(self) -> None:
        data = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n'

        headers, body = asyncio.run(_read_message(data))

        self.assertTrue(body.was_chunked)
        self.assertEqual(body.payload, b'abc')
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_modified_request_strips_transfer_encoding_and_sets_content_length(self) -> None:
        request = _build_modified_request(
            b'POST /v1/assets/batch HTTP/1.1',
            {
                b'host': b'assetdelivery.roblox.com',
                b'transfer-encoding': b'chunked',
                b'content-length': b'999',
                b'content-encoding': b'gzip',
            },
            b'{}',
        )

        head = request.split(b'\r\n\r\n', 1)[0].lower()
        self.assertNotIn(b'transfer-encoding', head)
        self.assertNotIn(b'content-encoding', head)
        self.assertNotIn(b'content-length: 999', head)
        self.assertIn(b'content-length: 2', head)
        self.assertTrue(request.endswith(b'\r\n\r\n{}'))

    def test_empty_json_array_detection_for_filtered_batches(self) -> None:
        self.assertTrue(_is_empty_json_array(b' [] \r\n'))
        self.assertFalse(_is_empty_json_array(b'[{"assetId":1}]'))
        self.assertFalse(_is_empty_json_array(b''))

    def test_local_extensionless_roblox_file_strips_metadata_prefix(self) -> None:
        expected = b'<roblox version="4"></roblox>'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset_hash'
            path.write_bytes(b'RBXH metadata bytes\r\n' + expected)

            response = _serve_local_file(str(path))

        self.assertIn(f'Content-Length: {len(expected)}'.encode(), response)
        self.assertEqual(_response_body(response), expected)

    def test_local_bin_roblox_file_strips_metadata_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.bin'
            path.write_bytes(b'metadata\n\n<roblox><Item /></roblox>')

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), b'<roblox><Item /></roblox>')

    def test_local_non_target_extension_keeps_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.rbxmx'
            content = b'metadata\n<roblox><Item /></roblox>'
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)

    def test_local_target_extension_without_roblox_marker_keeps_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.bin'
            content = b'not a roblox document'
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)


def test_profile_api_has_upstream_connection_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_cert_chain(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_alpn_protocols(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_servername_callback(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
    )

    limits = cast('dict[str, int]', proxy.__dict__['_upstream_host_limits'])
    assert PROFILE_API_HOST in limits


def test_profile_api_preserves_unmodified_browser_wire(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_cert_chain(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_alpn_protocols(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_servername_callback(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
        wire_preserving_passthrough=False,
    )

    assert _preserve_wire(proxy, PROFILE_API_HOST) is True
    assert _preserve_wire(proxy, 'assetdelivery.roblox.com') is False


def test_upstream_failure_notification_is_emitted_only_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_cert_chain(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_alpn_protocols(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_servername_callback(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )
    notifications: list[tuple[str, str]] = []
    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
        on_upstream_connect_failure=_record_failure(notifications),
    )

    _notify_upstream_failure(proxy, 'contentdelivery.roblox.com', 'blocked')
    _notify_upstream_failure(proxy, 'fts.rbxcdn.com', 'also blocked')

    assert notifications == [('contentdelivery.roblox.com', 'blocked')]


@pytest.mark.threaded_asyncio
def test_direct_upstream_refresh_retries_a_fresh_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def load_cert_chain(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_alpn_protocols(self, *_args: object, **_kwargs: object) -> None:
            pass

        def set_servername_callback(self, *_args: object, **_kwargs: object) -> None:
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    host = 'gamejoin.roblox.com'
    refreshed_ip = '93.184.216.35'
    bypass_updates: list[dict[str, list[str]]] = []

    class _RefreshAwareDirectConnector:
        async def connect(
            self,
            request_host: str,
            endpoints: Sequence[UpstreamEndpoint],
            _ssl_ctx: ssl.SSLContext | None,
            timeout: float,
        ) -> UpstreamConnectResult:
            assert request_host == host
            assert timeout <= 1.0
            endpoint_ips = [cast(str, endpoint.ip) for endpoint in endpoints]
            if refreshed_ip in endpoint_ips:
                return UpstreamConnectResult(
                    reader=asyncio.StreamReader(),
                    writer=cast(asyncio.StreamWriter, _FakeUpstreamWriter()),
                    method='direct_ip',
                    endpoint=refreshed_ip,
                )
            return UpstreamConnectResult(
                reader=None,
                writer=None,
                method='direct_ip',
                endpoint=endpoint_ips[0],
                error='TimeoutError',
            )

    def refresh_endpoints(_host: str) -> list[UpstreamEndpoint]:
        return [UpstreamEndpoint(host=host, ip=refreshed_ip)]

    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(update_real_ips=_record_real_ips(bypass_updates)),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={host: [UpstreamEndpoint(host=host, ip='93.184.216.34')]},
        upstream_endpoint_refresher=refresh_endpoints,
    )
    direct = _RefreshAwareDirectConnector()
    proxy.__dict__['_direct_connector'] = direct
    proxy.__dict__['_connector'] = AutoConnector(direct=cast(BaseUpstreamConnector, direct))

    result = asyncio.run(_connect_upstream(proxy, host, timeout=1.0))

    assert result.writer is not None
    assert result.endpoint == refreshed_ip
    endpoint_map = cast('dict[str, list[UpstreamEndpoint]]', proxy.__dict__['_upstream_endpoints'])
    assert [endpoint.ip for endpoint in endpoint_map[host]] == [refreshed_ip]
    assert bypass_updates == [{host: [refreshed_ip]}]
    connector = proxy.__dict__['_connector']
    assert connector.state_for(host).direct_ip_unhealthy_until == 0.0


def test_local_tls_max_version_can_be_relaxed(tmp_path: Path) -> None:
    ca_cert, ca_key = generate_ca(tmp_path)
    host_cert = generate_host_cert(ASSET_DELIVERY_HOST, ca_cert, ca_key, tmp_path)
    default_cert = generate_multi_host_cert(
        'default',
        {ASSET_DELIVERY_HOST},
        ca_cert,
        ca_key,
        tmp_path,
    )
    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(enabled=False),
        host_certs={ASSET_DELIVERY_HOST: host_cert},
        default_cert=default_cert,
        upstream_endpoints={},
        explicit_proxy=True,
        port=0,
        ca_cert_path=ca_cert,
        ca_key_path=ca_key,
        cert_cache_dir=tmp_path,
    )

    proxy.set_local_tls_max_version(ssl.TLSVersion.MAXIMUM_SUPPORTED)
    generated_ctx = _host_context(proxy, 'dynamic.example')
    server_ctx = cast(ssl.SSLContext, proxy.__dict__['_server_ssl_ctx'])
    host_contexts = cast('dict[str, ssl.SSLContext]', proxy.__dict__['_host_ssl_ctxs'])

    assert server_ctx.maximum_version is ssl.TLSVersion.MAXIMUM_SUPPORTED
    assert host_contexts[ASSET_DELIVERY_HOST].maximum_version is ssl.TLSVersion.MAXIMUM_SUPPORTED
    assert generated_ctx is not None
    assert generated_ctx.maximum_version is ssl.TLSVersion.MAXIMUM_SUPPORTED


def test_explicit_proxy_connect_upgrades_to_tls_and_serves_http(tmp_path: Path) -> None:
    async def run_test() -> None:
        ca_cert, ca_key = generate_ca(tmp_path)
        host_cert = generate_host_cert(ASSET_DELIVERY_HOST, ca_cert, ca_key, tmp_path)
        default_cert = generate_multi_host_cert(
            'default',
            {ASSET_DELIVERY_HOST},
            ca_cert,
            ca_key,
            tmp_path,
        )
        proxy = FleasionProxy(
            texture_stripper=_texture_stub(
                config_manager=SimpleNamespace(get_all_replacements=_empty_replacements),
                process_batch_request=_identity_batch_request,
            ),
            cache_scraper=_cache_stub(enabled=False),
            host_certs={ASSET_DELIVERY_HOST: host_cert},
            default_cert=default_cert,
            upstream_endpoints={},
            explicit_proxy=True,
            port=0,
        )

        async def fake_connect_upstream(
            host: str,
            *,
            timeout: float = 10.0,
            max_targets: int | None = None,
        ) -> UpstreamConnectResult:
            del host, timeout, max_targets
            reader = asyncio.StreamReader()
            reader.feed_data(
                b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
            )
            reader.feed_eof()
            return UpstreamConnectResult(
                reader=reader,
                writer=cast(asyncio.StreamWriter, _FakeUpstreamWriter()),
                endpoint='test',
                method='direct_ip',
            )

        proxy.__dict__['_connect_upstream'] = fake_connect_upstream
        await proxy.start()
        port = _server_port(cast(object, proxy.__dict__['_server']))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', port)
            connect_request = (
                f'CONNECT {ASSET_DELIVERY_HOST}:443 HTTP/1.1\r\n'
                f'Host: {ASSET_DELIVERY_HOST}:443\r\n'
                '\r\n'
            ).encode('ascii')
            writer.write(connect_request)
            await writer.drain()
            response = await reader.readuntil(b'\r\n\r\n')
            assert response.startswith(b'HTTP/1.1 200 ')

            import ssl

            ctx = ssl.create_default_context(cafile=str(ca_cert))
            await writer.start_tls(ctx, server_hostname=ASSET_DELIVERY_HOST)
            request = (
                f'GET /test HTTP/1.1\r\nHost: {ASSET_DELIVERY_HOST}\r\nConnection: close\r\n\r\n'
            ).encode('ascii')
            writer.write(request)
            await writer.drain()
            tunneled_response = await reader.read()
            assert tunneled_response.startswith(b'HTTP/1.1 204 No Content')
            writer.close()
        finally:
            await proxy.stop()

    asyncio.run(run_test())


def test_explicit_proxy_tunnels_non_intercept_hosts(tmp_path: Path) -> None:
    async def run_test() -> None:
        async def handle_echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            data = await reader.read(1024)
            writer.write(b'upstream:' + data)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(handle_echo, '127.0.0.1', 0)
        upstream_port = _server_port(upstream)

        ca_cert, ca_key = generate_ca(tmp_path)
        default_cert = generate_multi_host_cert(
            'default',
            {ASSET_DELIVERY_HOST},
            ca_cert,
            ca_key,
            tmp_path,
        )
        proxy = FleasionProxy(
            texture_stripper=_texture_stub(),
            cache_scraper=_cache_stub(enabled=False),
            host_certs={},
            default_cert=default_cert,
            upstream_endpoints={},
            explicit_proxy=True,
            port=0,
        )

        await proxy.start()
        proxy_port = _server_port(cast(object, proxy.__dict__['_server']))
        try:
            reader, writer = await asyncio.open_connection('127.0.0.1', proxy_port)
            writer.write(
                (
                    f'CONNECT 127.0.0.1:{upstream_port} HTTP/1.1\r\n'
                    f'Host: 127.0.0.1:{upstream_port}\r\n'
                    '\r\n'
                ).encode('ascii')
            )
            await writer.drain()
            response = await reader.readuntil(b'\r\n\r\n')
            assert response.startswith(b'HTTP/1.1 200 ')

            writer.write(b'hello')
            await writer.drain()
            assert await reader.read(14) == b'upstream:hello'
            writer.close()
        finally:
            await proxy.stop()
            upstream.close()
            await upstream.wait_closed()

    asyncio.run(run_test())


def test_explicit_tunnel_dialer_prefers_ipv4_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_test() -> None:
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args: object, **_kwargs: object):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.2', 443)),
            ]

        calls: list[tuple[str, int, int]] = []

        async def fake_open_connection(
            host: str, port: int, *, family: int = 0, **_kwargs: object
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            calls.append((host, port, family))
            if host == '192.0.2.1':
                raise asyncio.TimeoutError()
            return asyncio.StreamReader(), cast(asyncio.StreamWriter, _FakeUpstreamWriter())

        monkeypatch.setattr(loop, 'getaddrinfo', fake_getaddrinfo)
        monkeypatch.setattr(asyncio, 'open_connection', fake_open_connection)

        result = await _open_explicit_proxy_tunnel('silver.roblox.com', 443)

        assert result.writer is not None
        assert result.endpoint == 'IPv4 192.0.2.2'
        assert calls == [
            ('192.0.2.1', 443, socket.AF_INET),
            ('192.0.2.2', 443, socket.AF_INET),
        ]

    asyncio.run(run_test())


def test_explicit_tunnel_single_candidate_receives_full_connection_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_test() -> None:
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args: object, **_kwargs: object):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
            ]

        async def fake_open_connection(
            *_args: object, **_kwargs: object
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            return asyncio.StreamReader(), cast(asyncio.StreamWriter, _FakeUpstreamWriter())

        timeouts: list[float] = []

        async def recording_wait_for[T](awaitable: Awaitable[T], timeout: float | None) -> T:
            assert timeout is not None
            timeouts.append(timeout)
            return await awaitable

        monkeypatch.setattr(loop, 'getaddrinfo', fake_getaddrinfo)
        monkeypatch.setattr(asyncio, 'open_connection', fake_open_connection)
        monkeypatch.setattr(asyncio, 'wait_for', recording_wait_for)

        result = await _open_explicit_proxy_tunnel(
            'silver.roblox.com',
            443,
            timeout=10.0,
        )

        assert result.writer is not None
        assert timeouts[0] == 10.0
        assert timeouts[1] > 9.9

    asyncio.run(run_test())


def test_explicit_tunnel_dialer_falls_back_to_ipv6(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_test() -> None:
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args: object, **_kwargs: object):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
            ]

        calls: list[tuple[str, int, int]] = []

        async def fake_open_connection(
            host: str, port: int, *, family: int = 0, **_kwargs: object
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            calls.append((host, port, family))
            if family == socket.AF_INET:
                raise OSError('network unreachable')
            return asyncio.StreamReader(), cast(asyncio.StreamWriter, _FakeUpstreamWriter())

        monkeypatch.setattr(loop, 'getaddrinfo', fake_getaddrinfo)
        monkeypatch.setattr(asyncio, 'open_connection', fake_open_connection)

        result = await _open_explicit_proxy_tunnel('silver.roblox.com', 443)

        assert result.writer is not None
        assert result.endpoint == 'IPv6 2001:db8::1'
        assert calls == [
            ('192.0.2.1', 443, socket.AF_INET),
            ('2001:db8::1', 443, socket.AF_INET6),
        ]

    asyncio.run(run_test())


def test_explicit_tunnel_dialer_reserves_an_ipv6_attempt_after_many_ipv4_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run_test() -> None:
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args: object, **_kwargs: object):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.2', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.3', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.4', 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
            ]

        calls: list[tuple[str, int, int]] = []

        async def fake_open_connection(
            host: str, port: int, *, family: int = 0, **_kwargs: object
        ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
            calls.append((host, port, family))
            if family == socket.AF_INET:
                raise OSError('network unreachable')
            return asyncio.StreamReader(), cast(asyncio.StreamWriter, _FakeUpstreamWriter())

        monkeypatch.setattr(loop, 'getaddrinfo', fake_getaddrinfo)
        monkeypatch.setattr(asyncio, 'open_connection', fake_open_connection)

        result = await _open_explicit_proxy_tunnel('silver.roblox.com', 443)

        assert result.writer is not None
        assert result.endpoint == 'IPv6 2001:db8::1'
        assert calls == [
            ('192.0.2.1', 443, socket.AF_INET),
            ('192.0.2.2', 443, socket.AF_INET),
            ('2001:db8::1', 443, socket.AF_INET6),
        ]

    asyncio.run(run_test())


def test_explicit_proxy_excludes_pinned_bootstrap_hosts_from_intercept_all(tmp_path: Path) -> None:
    ca_cert, ca_key = generate_ca(tmp_path)
    default_cert = generate_multi_host_cert(
        'default',
        {ASSET_DELIVERY_HOST},
        ca_cert,
        ca_key,
        tmp_path,
    )
    proxy = FleasionProxy(
        texture_stripper=_texture_stub(),
        cache_scraper=_cache_stub(enabled=False),
        host_certs={},
        default_cert=default_cert,
        upstream_endpoints={},
        explicit_proxy=True,
        intercept_all_hosts=True,
        intercept_excluded_hosts={'SOBER.VINEGARHQ.ORG.'},
    )

    assert not _should_intercept(proxy, 'sober.vinegarhq.org', 443)
    assert _should_intercept(proxy, 'api.example.test', 443)
    assert not _should_intercept(proxy, 'sober.vinegarhq.org', 80)


if __name__ == '__main__':
    unittest.main()
