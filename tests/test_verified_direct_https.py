"""Hosts-file bypass preserves the original SNI, Host header and TLS trust."""

from __future__ import annotations

import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
import requests

from fleasion.proxy.master import ProxyMaster
from fleasion.proxy.upstream import UpstreamEndpoint
from fleasion.utils.certs import generate_ca, generate_host_cert
from fleasion.utils.verified_direct_https import VerifiedDirectHTTPSAdapter

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get('Content-Length', '0'))
        self.server.received = (self.headers.get('Host'), self.path, self.rfile.read(length))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', '12')
        self.end_headers()
        self.wfile.write(b'{"status":2}')

    def log_message(self, format: str, *args: object) -> None:
        del format, args
        return


@pytest.fixture
def verified_server(tmp_path: Path) -> Iterator[tuple[int, Path, ThreadingHTTPServer]]:
    ca, ca_key = generate_ca(tmp_path)
    certificate, key = generate_host_cert('gamejoin.roblox.com', ca, ca_key, tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(key))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    runner = threading.Thread(target=server.serve_forever, daemon=True)
    runner.start()
    try:
        yield server.server_port, ca, server
    finally:
        server.shutdown()
        server.server_close()
        runner.join(timeout=3)


def test_verified_pinned_https_preserves_tls_hostname_and_http_host(
    verified_server: tuple[int, Path, ThreadingHTTPServer],
) -> None:
    port, trusted_ca, server = verified_server
    with requests.Session() as session:
        session.trust_env = False
        session.mount('https://gamejoin.roblox.com/', VerifiedDirectHTTPSAdapter('gamejoin.roblox.com', '127.0.0.1', port=port))
        response = session.post(
            'https://gamejoin.roblox.com/v1/join-game',
            json={'placeId': 123},
            timeout=5,
            verify=str(trusted_ca),
        )
        assert response.status_code == 200
        assert response.json()['status'] == 2
        host, path, payload = cast('tuple[str, str, bytes]', server.received)  # type: ignore[attr-defined]
        assert host == 'gamejoin.roblox.com'
        assert path == '/v1/join-game'
        assert b'123' in payload


def test_verified_pinned_https_refuses_untrusted_peer(
    verified_server: tuple[int, Path, ThreadingHTTPServer],
) -> None:
    port, _ca, _server = verified_server
    with requests.Session() as session:
        session.trust_env = False
        session.mount('https://gamejoin.roblox.com/', VerifiedDirectHTTPSAdapter('gamejoin.roblox.com', '127.0.0.1', port=port))
        with pytest.raises(requests.exceptions.SSLError):
            session.post('https://gamejoin.roblox.com/v1/join-game', timeout=5, verify=True)
        with pytest.raises(requests.exceptions.SSLError, match='Unverified TLS'):
            session.post('https://gamejoin.roblox.com/v1/join-game', timeout=5, verify=False)


def test_upstream_ip_only_reveals_pre_hosts_routable_address() -> None:
    proxy = ProxyMaster.__new__(ProxyMaster)
    state = vars(proxy)
    state['_lock'] = threading.Lock()
    state['_running'] = True
    state['_hosts_installed'] = True
    state['_active_intercept_hosts'] = {'gamejoin.roblox.com'}

    class UpstreamStub:
        def upstream_endpoints_for_hosts(self, hosts: list[str]) -> dict[str, list[UpstreamEndpoint]]:
            return {hosts[0]: [
                UpstreamEndpoint('gamejoin.roblox.com', ip='127.0.0.1'),
                UpstreamEndpoint('gamejoin.roblox.com', ip='128.116.13.3'),
            ]}

    state['_proxy'] = UpstreamStub()
    assert proxy.hosts_upstream_ip('GAMEJOIN.ROBLOX.COM.') == '128.116.13.3'
    assert proxy.hosts_upstream_ip('other.roblox.com') is None
    state['_hosts_installed'] = False
    assert proxy.hosts_upstream_ip('gamejoin.roblox.com') is None
