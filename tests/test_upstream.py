import socket
import threading
import time
import unittest

from Fleasion.proxy.upstream import (
    _blocking_http_connect_socket,
    _blocking_socks5_connect_socket,
)
from Fleasion.proxy.windows_proxy import _parse_scutil_proxy_output, parse_static_http_proxy


class _OneShotServer:
    def __init__(self, handler):
        self.handler = handler
        self.ready = threading.Event()
        self.done = threading.Event()
        self.error = None
        self.port = 0
        self.thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self.thread.start()
        self.ready.wait(2.0)
        return self

    def __exit__(self, exc_type, exc, tb):
        self.done.wait(2.0)
        if self.error is not None:
            raise self.error

    def _run(self):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
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


class UpstreamHandshakeTests(unittest.TestCase):
    def test_http_connect_handshake_parser(self):
        seen = {}

        def handler(conn):
            request = _recv_until(conn, b"\r\n\r\n")
            seen["request"] = request
            conn.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            time.sleep(0.05)

        with _OneShotServer(handler) as server:
            sock = _blocking_http_connect_socket(
                "127.0.0.1",
                server.port,
                "assetdelivery.roblox.com",
                443,
                2.0,
            )
            sock.close()

        self.assertIn(b"CONNECT assetdelivery.roblox.com:443 HTTP/1.1", seen["request"])
        self.assertIn(b"Host: assetdelivery.roblox.com:443", seen["request"])

    def test_http_connect_rejects_non_200(self):
        def handler(conn):
            _recv_until(conn, b"\r\n\r\n")
            conn.sendall(b"HTTP/1.1 407 Proxy Authentication Required\r\n\r\n")

        with _OneShotServer(handler) as server:
            with self.assertRaises(OSError):
                _blocking_http_connect_socket(
                    "127.0.0.1",
                    server.port,
                    "assetdelivery.roblox.com",
                    443,
                    2.0,
                )

    def test_socks5_handshake_parser(self):
        seen = {}

        def handler(conn):
            seen["greeting"] = conn.recv(3)
            conn.sendall(b"\x05\x00")
            head = conn.recv(5)
            name_len = head[4]
            name = conn.recv(name_len)
            port = conn.recv(2)
            seen["request"] = head + name + port
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            time.sleep(0.05)

        with _OneShotServer(handler) as server:
            sock = _blocking_socks5_connect_socket(
                "127.0.0.1",
                server.port,
                "assetdelivery.roblox.com",
                443,
                2.0,
            )
            sock.close()

        self.assertEqual(seen["greeting"], b"\x05\x01\x00")
        self.assertEqual(seen["request"][0:4], b"\x05\x01\x00\x03")
        self.assertIn(b"assetdelivery.roblox.com", seen["request"])
        self.assertEqual(seen["request"][-2:], (443).to_bytes(2, "big"))

    def test_static_windows_proxy_parsing(self):
        proxy = parse_static_http_proxy("http=127.0.0.1:8080;https=127.0.0.1:8443")
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy.host, "127.0.0.1")
        self.assertEqual(proxy.port, 8443)

        proxy = parse_static_http_proxy("127.0.0.1:8888")
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy.host, "127.0.0.1")
        self.assertEqual(proxy.port, 8888)

    def test_macos_scutil_proxy_parsing_prefers_static_values(self):
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
        self.assertEqual(http_proxy, "proxy.local:8080")
        self.assertTrue(https_enabled)
        self.assertEqual(https_proxy, "secure-proxy.local:8443")
        self.assertEqual(auto_url, "https://proxy.local/proxy.pac")


if __name__ == "__main__":
    unittest.main()
