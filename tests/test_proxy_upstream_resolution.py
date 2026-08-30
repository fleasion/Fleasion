import socket
from collections.abc import Callable
from typing import Protocol, cast

from fleasion.proxy import master as proxy_master
from fleasion.proxy.upstream import UpstreamEndpoint

_HTTPS_PORT = 443
_DNS_A = 1
_DNS_AAAA = 28
_RUNTIME_DNS_TIMEOUT = 0.75

type _SockAddr = tuple[str, int] | tuple[str, int, int, int]
type _AddrInfo = tuple[int, int, int, str, _SockAddr]
type _ResolveRealEndpoints = Callable[[set[str]], dict[str, list[UpstreamEndpoint]]]
type _RefreshRealUpstreamEndpoints = Callable[[str], list[UpstreamEndpoint]]


class _MonkeyPatch(Protocol):
    def setattr(
        self,
        target: object,
        name: str,
        value: object,
        *,
        raising: bool = True,
    ) -> None: ...


class _LogBufferStub:
    def __init__(self, logs: list[tuple[str, str]] | None = None) -> None:
        self._logs = logs

    def log(self, category: str, message: str) -> None:
        if self._logs is not None:
            self._logs.append((category, message))


def _resolve_real_endpoints(hosts: set[str]) -> dict[str, list[UpstreamEndpoint]]:
    resolver = cast('_ResolveRealEndpoints', vars(proxy_master)['_resolve_real_endpoints'])
    return resolver(hosts)


def _refresh_real_upstream_endpoints(host: str) -> list[UpstreamEndpoint]:
    refresher = cast(
        '_RefreshRealUpstreamEndpoints',
        vars(proxy_master)['_refresh_real_upstream_endpoints'],
    )
    return refresher(host)


def test_resolve_real_endpoints_prefers_ipv4_when_os_returns_ipv6_first(
    monkeypatch: _MonkeyPatch,
) -> None:
    logs: list[tuple[str, str]] = []

    def fake_getaddrinfo(host: str, port: int, family: int, socktype: int) -> list[_AddrInfo]:
        assert host == 'assetdelivery.roblox.com'
        assert port == _HTTPS_PORT
        assert family == socket.AF_UNSPEC
        assert socktype == socket.SOCK_STREAM
        return [
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                0,
                '',
                ('2606:2800:220:1:248:1893:25c8:1946', _HTTPS_PORT, 0, 0),
            ),
            (socket.AF_INET, socket.SOCK_STREAM, 0, '', ('93.184.216.34', _HTTPS_PORT)),
        ]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)
    monkeypatch.setattr(proxy_master, 'log_buffer', _LogBufferStub(logs))

    endpoints = _resolve_real_endpoints({'assetdelivery.roblox.com'})

    resolved = endpoints['assetdelivery.roblox.com']
    assert [endpoint.family for endpoint in resolved] == [socket.AF_INET, socket.AF_INET6]
    assert [endpoint.ip for endpoint in resolved] == [
        '93.184.216.34',
        '2606:2800:220:1:248:1893:25c8:1946',
    ]
    assert any('93.184.216.34' in message for _category, message in logs)


def test_public_dns_fallback_prefers_ipv4_before_ipv6(monkeypatch: _MonkeyPatch) -> None:
    queries: list[tuple[str, str, int]] = []

    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[_AddrInfo]:
        return []

    def fake_dns_query(
        host: str,
        server: str,
        port: int = 53,
        timeout: float = 3.0,
        qtype: int = _DNS_A,
    ) -> list[str]:
        del port, timeout
        queries.append((host, server, qtype))
        if qtype == _DNS_A:
            return ['93.184.216.34']
        if qtype == _DNS_AAAA:
            return ['2606:2800:220:1:248:1893:25c8:1946']
        return []

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)
    monkeypatch.setattr(proxy_master, '_DNS_FALLBACK_SERVERS', ['dns.test'])
    monkeypatch.setattr(proxy_master, '_dns_query_udp', fake_dns_query)
    monkeypatch.setattr(proxy_master, 'log_buffer', _LogBufferStub())

    endpoints = _resolve_real_endpoints({'assetdelivery.roblox.com'})

    resolved = endpoints['assetdelivery.roblox.com']
    assert queries == [
        ('assetdelivery.roblox.com', 'dns.test', _DNS_A),
        ('assetdelivery.roblox.com', 'dns.test', _DNS_AAAA),
    ]
    assert [endpoint.family for endpoint in resolved] == [socket.AF_INET, socket.AF_INET6]
    assert [endpoint.ip for endpoint in resolved] == [
        '93.184.216.34',
        '2606:2800:220:1:248:1893:25c8:1946',
    ]


def test_runtime_endpoint_refresh_collects_candidates_from_all_public_resolvers(
    monkeypatch: _MonkeyPatch,
) -> None:
    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[_AddrInfo]:
        return []

    def fake_dns_query(
        host: str,
        server: str,
        port: int = 53,
        timeout: float = 3.0,
        qtype: int = _DNS_A,
    ) -> list[str]:
        del port
        assert host == 'assetdelivery.roblox.com'
        assert timeout.hex() == _RUNTIME_DNS_TIMEOUT.hex()
        if qtype != _DNS_A:
            return []
        return {
            'dns-one.test': ['93.184.216.34'],
            'dns-two.test': ['93.184.216.35'],
        }[server]

    monkeypatch.setattr(socket, 'getaddrinfo', fake_getaddrinfo)
    monkeypatch.setattr(proxy_master, '_DNS_FALLBACK_SERVERS', ['dns-one.test', 'dns-two.test'])
    monkeypatch.setattr(proxy_master, '_dns_query_udp', fake_dns_query)
    monkeypatch.setattr(proxy_master, 'log_buffer', _LogBufferStub())

    endpoints = _refresh_real_upstream_endpoints('assetdelivery.roblox.com')

    assert [endpoint.ip for endpoint in endpoints] == ['93.184.216.34', '93.184.216.35']
