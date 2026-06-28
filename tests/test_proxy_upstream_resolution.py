import socket
from types import SimpleNamespace

from Fleasion.proxy import master as proxy_master


def test_resolve_real_endpoints_prefers_ipv4_when_os_returns_ipv6_first(monkeypatch):
    logs = []

    def fake_getaddrinfo(host, port, family, socktype):
        assert host == "assetdelivery.roblox.com"
        assert port == 443
        assert family == socket.AF_UNSPEC
        assert socktype == socket.SOCK_STREAM
        return [
            (socket.AF_INET6, socket.SOCK_STREAM, 0, "", ("2606:2800:220:1:248:1893:25c8:1946", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 443)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda category, message: logs.append((category, message))))

    endpoints = proxy_master._resolve_real_endpoints({"assetdelivery.roblox.com"})

    resolved = endpoints["assetdelivery.roblox.com"]
    assert [endpoint.family for endpoint in resolved] == [socket.AF_INET, socket.AF_INET6]
    assert [endpoint.ip for endpoint in resolved] == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]
    assert any("93.184.216.34" in message for _category, message in logs)


def test_public_dns_fallback_prefers_ipv4_before_ipv6(monkeypatch):
    queries = []

    def fake_getaddrinfo(*_args, **_kwargs):
        return []

    def fake_dns_query(host, server, port=53, timeout=3.0, qtype=1):
        queries.append((host, server, qtype))
        if qtype == 1:
            return ["93.184.216.34"]
        if qtype == 28:
            return ["2606:2800:220:1:248:1893:25c8:1946"]
        return []

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(proxy_master, "_DNS_FALLBACK_SERVERS", ["dns.test"])
    monkeypatch.setattr(proxy_master, "_dns_query_udp", fake_dns_query)
    monkeypatch.setattr(proxy_master, "log_buffer", SimpleNamespace(log=lambda *_args: None))

    endpoints = proxy_master._resolve_real_endpoints({"assetdelivery.roblox.com"})

    resolved = endpoints["assetdelivery.roblox.com"]
    assert queries == [
        ("assetdelivery.roblox.com", "dns.test", 1),
        ("assetdelivery.roblox.com", "dns.test", 28),
    ]
    assert [endpoint.family for endpoint in resolved] == [socket.AF_INET, socket.AF_INET6]
    assert [endpoint.ip for endpoint in resolved] == [
        "93.184.216.34",
        "2606:2800:220:1:248:1893:25c8:1946",
    ]
