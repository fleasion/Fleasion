import asyncio
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.proxy.server import (
    ASSET_DELIVERY_HOST,
    PROFILE_API_HOST,
    FleasionProxy,
    _build_modified_request,
    _is_empty_json_array,
    _open_explicit_proxy_tunnel,
    _read_body_wire,
    _read_headers_raw,
    _serve_local_file,
)
from fleasion.proxy.upstream import AutoConnector, UpstreamConnectResult, UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert, generate_multi_host_cert


async def _read_message(data: bytes):
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    headers = await _read_headers_raw(reader)
    body = await _read_body_wire(reader, headers.headers)
    return headers, body


def _response_body(response: bytes) -> bytes:
    return response.split(b'\r\n\r\n', 1)[1]


class _FakeUpstreamWriter:
    def __init__(self):
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


def test_upstream_self_test_serializes_and_fully_closes_probes():
    events = []

    class _ProbeWriter:
        def __init__(self, host):
            self.host = host

        def close(self):
            events.append(('close', self.host))

        async def wait_closed(self):
            events.append(('wait_closed', self.host))

    class _ProbeConnector:
        async def connect(self, host, _endpoints, _ssl_ctx, timeout):
            assert timeout == 3.0
            events.append(('connect', host))
            return UpstreamConnectResult(
                reader=object(),
                writer=_ProbeWriter(host),
                method='direct_ip',
                endpoint='192.0.2.1',
            )

    proxy = FleasionProxy.__new__(FleasionProxy)
    proxy._upstream_endpoints = {
        'a.example': [UpstreamEndpoint(host='a.example', ip='192.0.2.1')],
        'b.example': [UpstreamEndpoint(host='b.example', ip='192.0.2.2')],
    }
    proxy._direct_connector = _ProbeConnector()
    proxy._system_http_connector = None
    proxy._manual_http_connector = None
    proxy._manual_socks5_connector = None
    proxy._upstream_ssl_ctx = None
    proxy._connector = proxy._direct_connector

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
    def test_raw_header_preservation_duplicate_headers_and_casing(self):
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

    def test_bodyless_get_passthrough_does_not_inject_content_length(self):
        data = b'GET /v1/assets/batch HTTP/1.1\r\nHost: assetdelivery.roblox.com\r\n\r\n'

        headers, body = asyncio.run(_read_message(data))

        forwarded = headers.raw_header_block + body.wire
        self.assertEqual(forwarded, data)
        self.assertNotIn(b'content-length', forwarded.lower())

    def test_content_length_post_exact_passthrough(self):
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

    def test_chunked_request_exact_wire_preservation(self):
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

    def test_chunked_response_exact_wire_preservation(self):
        data = b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabc\r\n0\r\n\r\n'

        headers, body = asyncio.run(_read_message(data))

        self.assertTrue(body.was_chunked)
        self.assertEqual(body.payload, b'abc')
        self.assertEqual(headers.raw_header_block + body.wire, data)

    def test_modified_request_strips_transfer_encoding_and_sets_content_length(self):
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

    def test_empty_json_array_detection_for_filtered_batches(self):
        self.assertTrue(_is_empty_json_array(b' [] \r\n'))
        self.assertFalse(_is_empty_json_array(b'[{"assetId":1}]'))
        self.assertFalse(_is_empty_json_array(b''))

    def test_local_extensionless_roblox_file_strips_metadata_prefix(self):
        expected = b'<roblox version="4"></roblox>'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset_hash'
            path.write_bytes(b'RBXH metadata bytes\r\n' + expected)

            response = _serve_local_file(str(path))

        self.assertIn(f'Content-Length: {len(expected)}'.encode(), response)
        self.assertEqual(_response_body(response), expected)

    def test_local_bin_roblox_file_strips_metadata_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.bin'
            path.write_bytes(b'metadata\n\n<roblox><Item /></roblox>')

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), b'<roblox><Item /></roblox>')

    def test_local_non_target_extension_keeps_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.rbxmx'
            content = b'metadata\n<roblox><Item /></roblox>'
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)

    def test_local_target_extension_without_roblox_marker_keeps_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'asset.bin'
            content = b'not a roblox document'
            path.write_bytes(content)

            response = _serve_local_file(str(path))

        self.assertEqual(_response_body(response), content)


def test_profile_api_has_upstream_connection_limit(monkeypatch, tmp_path):
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args, **_kwargs):
            pass

        def load_cert_chain(self, *_args, **_kwargs):
            pass

        def set_alpn_protocols(self, *_args, **_kwargs):
            pass

        def set_servername_callback(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    proxy = FleasionProxy(
        texture_stripper=SimpleNamespace(),
        cache_scraper=SimpleNamespace(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
    )

    assert PROFILE_API_HOST in proxy._upstream_host_limits


def test_profile_api_preserves_unmodified_browser_wire(monkeypatch, tmp_path):
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args, **_kwargs):
            pass

        def load_cert_chain(self, *_args, **_kwargs):
            pass

        def set_alpn_protocols(self, *_args, **_kwargs):
            pass

        def set_servername_callback(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    proxy = FleasionProxy(
        texture_stripper=SimpleNamespace(),
        cache_scraper=SimpleNamespace(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
        wire_preserving_passthrough=False,
    )

    assert proxy._preserve_unmodified_wire_for_host(PROFILE_API_HOST) is True
    assert proxy._preserve_unmodified_wire_for_host('assetdelivery.roblox.com') is False


def test_upstream_failure_notification_is_emitted_only_once(monkeypatch, tmp_path):
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args, **_kwargs):
            pass

        def load_cert_chain(self, *_args, **_kwargs):
            pass

        def set_alpn_protocols(self, *_args, **_kwargs):
            pass

        def set_servername_callback(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )
    notifications = []
    proxy = FleasionProxy(
        texture_stripper=SimpleNamespace(),
        cache_scraper=SimpleNamespace(),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={},
        on_upstream_connect_failure=lambda host, error: notifications.append((host, error)),
    )

    proxy._notify_upstream_connect_failure_once('contentdelivery.roblox.com', 'blocked')
    proxy._notify_upstream_connect_failure_once('fts.rbxcdn.com', 'also blocked')

    assert notifications == [('contentdelivery.roblox.com', 'blocked')]


@pytest.mark.threaded_asyncio
def test_direct_upstream_refresh_retries_a_fresh_endpoint(monkeypatch, tmp_path):
    class FakeSSLContext:
        verify_mode = None
        minimum_version = None

        def __init__(self, *_args, **_kwargs):
            pass

        def load_cert_chain(self, *_args, **_kwargs):
            pass

        def set_alpn_protocols(self, *_args, **_kwargs):
            pass

        def set_servername_callback(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr('fleasion.proxy.server.ssl.SSLContext', FakeSSLContext)
    monkeypatch.setattr(
        'fleasion.proxy.server.ssl.create_default_context', lambda: FakeSSLContext()
    )

    host = 'gamejoin.roblox.com'
    refreshed_ip = '93.184.216.35'
    bypass_updates = []

    class _RefreshAwareDirectConnector:
        async def connect(self, request_host, endpoints, _ssl_ctx, timeout):
            assert request_host == host
            assert timeout <= 1.0
            endpoint_ips = [endpoint.ip for endpoint in endpoints]
            if refreshed_ip in endpoint_ips:
                return UpstreamConnectResult(
                    reader=asyncio.StreamReader(),
                    writer=_FakeUpstreamWriter(),
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

    proxy = FleasionProxy(
        texture_stripper=SimpleNamespace(),
        cache_scraper=SimpleNamespace(update_real_ips=lambda values: bypass_updates.append(values)),
        host_certs={},
        default_cert=(tmp_path / 'default.crt', tmp_path / 'default.key'),
        upstream_endpoints={host: [UpstreamEndpoint(host=host, ip='93.184.216.34')]},
        upstream_endpoint_refresher=lambda _host: [UpstreamEndpoint(host=host, ip=refreshed_ip)],
    )
    direct = _RefreshAwareDirectConnector()
    proxy._direct_connector = direct
    proxy._connector = AutoConnector(direct=direct)

    result = asyncio.run(proxy._connect_upstream(host, timeout=1.0))

    assert result.writer is not None
    assert result.endpoint == refreshed_ip
    assert [endpoint.ip for endpoint in proxy._upstream_endpoints[host]] == [refreshed_ip]
    assert bypass_updates == [{host: [refreshed_ip]}]
    assert proxy._connector.state_for(host).direct_ip_unhealthy_until == 0.0


def test_explicit_proxy_connect_upgrades_to_tls_and_serves_http(tmp_path):
    async def run_test():
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
            texture_stripper=SimpleNamespace(
                config_manager=SimpleNamespace(get_all_replacements=lambda: ({}, set(), {}, {})),
                process_batch_request=lambda body, replacements: body,
            ),
            cache_scraper=SimpleNamespace(enabled=False),
            host_certs={ASSET_DELIVERY_HOST: host_cert},
            default_cert=default_cert,
            upstream_endpoints={},
            explicit_proxy=True,
            port=0,
        )

        async def fake_connect_upstream(_host):
            reader = asyncio.StreamReader()
            reader.feed_data(
                b'HTTP/1.1 204 No Content\r\nContent-Length: 0\r\nConnection: close\r\n\r\n'
            )
            reader.feed_eof()
            return UpstreamConnectResult(
                reader=reader,
                writer=_FakeUpstreamWriter(),
                endpoint='test',
                method='direct_ip',
            )

        proxy._connect_upstream = fake_connect_upstream
        await proxy.start()
        port = proxy._server.sockets[0].getsockname()[1]
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


def test_explicit_proxy_tunnels_non_intercept_hosts(tmp_path):
    async def run_test():
        async def handle_echo(reader, writer):
            data = await reader.read(1024)
            writer.write(b'upstream:' + data)
            await writer.drain()
            writer.close()

        upstream = await asyncio.start_server(handle_echo, '127.0.0.1', 0)
        upstream_port = upstream.sockets[0].getsockname()[1]

        ca_cert, ca_key = generate_ca(tmp_path)
        default_cert = generate_multi_host_cert(
            'default',
            {ASSET_DELIVERY_HOST},
            ca_cert,
            ca_key,
            tmp_path,
        )
        proxy = FleasionProxy(
            texture_stripper=SimpleNamespace(),
            cache_scraper=SimpleNamespace(enabled=False),
            host_certs={},
            default_cert=default_cert,
            upstream_endpoints={},
            explicit_proxy=True,
            port=0,
        )

        await proxy.start()
        proxy_port = proxy._server.sockets[0].getsockname()[1]
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


def test_explicit_tunnel_dialer_prefers_ipv4_and_falls_back(monkeypatch):
    async def run_test():
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args, **_kwargs):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.2', 443)),
            ]

        calls = []

        async def fake_open_connection(host, port, *, family=0, **_kwargs):
            calls.append((host, port, family))
            if host == '192.0.2.1':
                raise asyncio.TimeoutError()
            return asyncio.StreamReader(), _FakeUpstreamWriter()

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


def test_explicit_tunnel_dialer_falls_back_to_ipv6(monkeypatch):
    async def run_test():
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args, **_kwargs):
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
            ]

        calls = []

        async def fake_open_connection(host, port, *, family=0, **_kwargs):
            calls.append((host, port, family))
            if family == socket.AF_INET:
                raise OSError('network unreachable')
            return asyncio.StreamReader(), _FakeUpstreamWriter()

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
    monkeypatch,
):
    async def run_test():
        loop = asyncio.get_running_loop()

        async def fake_getaddrinfo(*_args, **_kwargs):
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.1', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.2', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.3', 443)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.0.2.4', 443)),
                (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2001:db8::1', 443, 0, 0)),
            ]

        calls = []

        async def fake_open_connection(host, port, *, family=0, **_kwargs):
            calls.append((host, port, family))
            if family == socket.AF_INET:
                raise OSError('network unreachable')
            return asyncio.StreamReader(), _FakeUpstreamWriter()

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


def test_explicit_proxy_excludes_pinned_bootstrap_hosts_from_intercept_all(tmp_path):
    ca_cert, ca_key = generate_ca(tmp_path)
    default_cert = generate_multi_host_cert(
        'default',
        {ASSET_DELIVERY_HOST},
        ca_cert,
        ca_key,
        tmp_path,
    )
    proxy = FleasionProxy(
        texture_stripper=SimpleNamespace(),
        cache_scraper=SimpleNamespace(enabled=False),
        host_certs={},
        default_cert=default_cert,
        upstream_endpoints={},
        explicit_proxy=True,
        intercept_all_hosts=True,
        intercept_excluded_hosts={'SOBER.VINEGARHQ.ORG.'},
    )

    assert not proxy._should_intercept_explicit_host('sober.vinegarhq.org', 443)
    assert proxy._should_intercept_explicit_host('api.example.test', 443)
    assert not proxy._should_intercept_explicit_host('sober.vinegarhq.org', 80)


if __name__ == '__main__':
    unittest.main()
