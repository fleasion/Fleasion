import asyncio
import socket
import ssl
import threading
import time
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from fleasion.proxy import upstream as upstream_module, windows_proxy as windows_proxy_module
from fleasion.proxy.upstream import (
    AutoConnector,
    BaseUpstreamConnector,
    DirectIpConnector,
    UpstreamConnectResult,
    UpstreamEndpoint,
)
from fleasion.proxy.windows_proxy import parse_static_http_proxy
from fleasion.utils.certs import generate_ca, generate_host_cert


def _blocking_http_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
) -> socket.socket:
    callback = cast(
        'Callable[[str, int, str, int, float], socket.socket]',
        upstream_module.__dict__['_blocking_http_connect_socket'],
    )
    return callback(proxy_host, proxy_port, target_host, target_port, timeout)


def _blocking_socks5_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
) -> socket.socket:
    callback = cast(
        'Callable[[str, int, str, int, float], socket.socket]',
        upstream_module.__dict__['_blocking_socks5_connect_socket'],
    )
    return callback(proxy_host, proxy_port, target_host, target_port, timeout)


def _parse_scutil_proxy_output(
    text: str,
) -> tuple[bool, str | None, bool, str | None, str | None]:
    callback = cast(
        'Callable[[str], tuple[bool, str | None, bool, str | None, str | None]]',
        windows_proxy_module.__dict__['_parse_scutil_proxy_output'],
    )
    return callback(text)


class _OneShotServer:
    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self.handler = handler
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error: BaseException | None = None
        self.port = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self) -> _OneShotServer:
        self.thread.start()
        self.ready.wait(2.0)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        self.done.wait(2.0)
        if self.error is not None:
            raise self.error

    def _run(self) -> None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                self.port = listener.getsockname()[1]
                self.ready.set()
                conn, _ = listener.accept()
                with conn:
                    self.handler(conn)
        except Exception as exc:
            self.error = exc
        finally:
            self.done.set()


def _recv_until(conn: socket.socket, marker: bytes) -> bytes:
    buf = bytearray()
    while marker not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def test_direct_ip_connector_supports_verified_tls_with_hostname_sni(tmp_path: Path) -> None:
    async def run_test() -> None:
        host = 'assetdelivery.roblox.com'
        ca_cert, ca_key = generate_ca(tmp_path)
        host_cert, host_key = generate_host_cert(host, ca_cert, ca_key, tmp_path)

        server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_ctx.load_cert_chain(str(host_cert), str(host_key))

        async def handler(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b'ok')
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(handler, '127.0.0.1', 0, ssl=server_ctx)
        sockets = server.sockets
        assert sockets
        port = int(sockets[0].getsockname()[1])
        client_ctx = ssl.create_default_context(cafile=str(ca_cert))

        try:
            result = await DirectIpConnector().connect(
                host,
                [UpstreamEndpoint(host=host, ip='127.0.0.1', port=port)],
                client_ctx,
                2.0,
            )
            assert result.reader is not None
            assert result.writer is not None
            assert await result.reader.read(2) == b'ok'
            result.writer.close()
            await result.writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(run_test())


class UpstreamHandshakeTests(unittest.TestCase):
    def test_auto_direct_only_retries_during_an_unhealthy_cooldown(self) -> None:
        attempts: list[tuple[str, list[UpstreamEndpoint], float]] = []

        class _FailingDirectConnector(BaseUpstreamConnector):
            async def connect(
                self,
                host: str,
                endpoints: Sequence[UpstreamEndpoint],
                ssl_ctx: ssl.SSLContext,
                timeout: float,
            ) -> UpstreamConnectResult:
                del ssl_ctx
                attempts.append((host, list(endpoints), timeout))
                return UpstreamConnectResult(
                    reader=None,
                    writer=None,
                    method='direct_ip',
                    endpoint=host,
                    error='timed out',
                )

        connector = AutoConnector(direct=_FailingDirectConnector(), cooldown_seconds=120.0)
        connector.state_for('apis.roblox.com').direct_ip_unhealthy_until = time.monotonic() + 60.0

        result = asyncio.run(
            connector.connect(
                'apis.roblox.com',
                [UpstreamEndpoint(host='apis.roblox.com', ip='93.184.216.34')],
                cast('ssl.SSLContext', None),
                1.0,
            )
        )

        self.assertEqual(len(attempts), 1)
        self.assertEqual(result.error, 'direct_ip: timed out')

    def test_http_connect_handshake_parser(self) -> None:
        seen: dict[str, bytes] = {}

        def handler(conn: socket.socket) -> None:
            request = _recv_until(conn, b'\r\n\r\n')
            seen['request'] = request
            conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            time.sleep(0.05)

        with _OneShotServer(handler) as server:
            sock = _blocking_http_connect_socket(
                '127.0.0.1',
                server.port,
                'assetdelivery.roblox.com',
                443,
                2.0,
            )
            sock.close()

        self.assertIn(b'CONNECT assetdelivery.roblox.com:443 HTTP/1.1', seen['request'])
        self.assertIn(b'Host: assetdelivery.roblox.com:443', seen['request'])

    def test_http_connect_rejects_non_200(self) -> None:
        def handler(conn: socket.socket) -> None:
            _recv_until(conn, b'\r\n\r\n')
            conn.sendall(b'HTTP/1.1 407 Proxy Authentication Required\r\n\r\n')

        with _OneShotServer(handler) as server, self.assertRaises(OSError):
            _blocking_http_connect_socket(
                '127.0.0.1',
                server.port,
                'assetdelivery.roblox.com',
                443,
                2.0,
            )

    def test_socks5_handshake_parser(self) -> None:
        seen: dict[str, bytes] = {}

        def handler(conn: socket.socket) -> None:
            seen['greeting'] = conn.recv(3)
            conn.sendall(b'\x05\x00')
            head = conn.recv(5)
            name_len = head[4]
            name = conn.recv(name_len)
            port = conn.recv(2)
            seen['request'] = head + name + port
            conn.sendall(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
            time.sleep(0.05)

        with _OneShotServer(handler) as server:
            sock = _blocking_socks5_connect_socket(
                '127.0.0.1',
                server.port,
                'assetdelivery.roblox.com',
                443,
                2.0,
            )
            sock.close()

        self.assertEqual(seen['greeting'], b'\x05\x01\x00')
        self.assertEqual(seen['request'][0:4], b'\x05\x01\x00\x03')
        self.assertIn(b'assetdelivery.roblox.com', seen['request'])
        self.assertEqual(seen['request'][-2:], (443).to_bytes(2, 'big'))

    def test_static_windows_proxy_parsing(self) -> None:
        proxy = parse_static_http_proxy('http=127.0.0.1:8080;https=127.0.0.1:8443')
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertEqual(proxy.host, '127.0.0.1')
        self.assertEqual(proxy.port, 8443)

        proxy = parse_static_http_proxy('127.0.0.1:8888')
        self.assertIsNotNone(proxy)
        assert proxy is not None
        self.assertEqual(proxy.host, '127.0.0.1')
        self.assertEqual(proxy.port, 8888)

    def test_macos_scutil_proxy_parsing_prefers_static_values(self) -> None:
        http_enabled, http_proxy, https_enabled, https_proxy, auto_url = _parse_scutil_proxy_output(
            """
            <dictionary> {
              HTTPEnable : 1
              HTTPProxy : proxy.local
              HTTPPort : 8080
              HTTPSEnable : 1
              HTTPSProxy : secure-proxy.local
              HTTPSPort : 8443
              ProxyAutoConfigEnable : 1
              ProxyAutoConfigURLString : https://proxy.local/proxy.pac
            }
            """
        )

        self.assertTrue(http_enabled)
        self.assertEqual(http_proxy, 'proxy.local:8080')
        self.assertTrue(https_enabled)
        self.assertEqual(https_proxy, 'secure-proxy.local:8443')
        self.assertEqual(auto_url, 'https://proxy.local/proxy.pac')


if __name__ == '__main__':
    unittest.main()
