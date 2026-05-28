"""Upstream transport connectors for Fleasion's local TLS proxy."""

from __future__ import annotations

import asyncio
import base64
import socket
import ssl
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Sequence


class UpstreamMode(str, Enum):
    AUTO = "auto"
    DIRECT_IP = "direct_ip"
    SYSTEM_PROXY = "system_proxy"
    HTTP_CONNECT = "http_connect"
    SOCKS5 = "socks5"


@dataclass(frozen=True)
class UpstreamEndpoint:
    host: str
    port: int = 443
    ip: Optional[str] = None
    family: Optional[int] = None


@dataclass
class UpstreamConnectResult:
    reader: Optional[asyncio.StreamReader]
    writer: Optional[asyncio.StreamWriter]
    method: str
    endpoint: str
    error: Optional[str] = None
    prior_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpProxyConfig:
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass(frozen=True)
class Socks5ProxyConfig:
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None


@dataclass
class HostTransportState:
    preferred_method: Optional[str] = None
    preferred_until: float = 0.0
    direct_ip_unhealthy_until: float = 0.0
    last_success_method: Optional[str] = None


class BaseUpstreamConnector:
    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        raise NotImplementedError


def _format_exc(exc: Exception) -> str:
    text = str(exc)
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _target_port(endpoints: Sequence[UpstreamEndpoint]) -> int:
    return endpoints[0].port if endpoints else 443


def normalize_upstream_mode(value: object) -> UpstreamMode:
    try:
        return UpstreamMode(str(value or UpstreamMode.AUTO.value).lower())
    except ValueError:
        return UpstreamMode.AUTO


def normalize_endpoints(
    upstream_endpoints: dict[str, Sequence[UpstreamEndpoint | str]] | None,
) -> dict[str, list[UpstreamEndpoint]]:
    normalized: dict[str, list[UpstreamEndpoint]] = {}
    for host, endpoints in (upstream_endpoints or {}).items():
        items: list[UpstreamEndpoint] = []
        for ep in endpoints or ():
            if isinstance(ep, UpstreamEndpoint):
                items.append(ep)
            elif isinstance(ep, str):
                items.append(UpstreamEndpoint(host=host, ip=ep))
        normalized[host] = items
    return normalized


class DirectIpConnector(BaseUpstreamConnector):
    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        failures: list[str] = []
        targets = list(endpoints) or [UpstreamEndpoint(host=host)]

        for ep in targets:
            target = ep.ip or ep.host
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(
                        target,
                        ep.port,
                        ssl=ssl_ctx,
                        server_hostname=host,
                        family=ep.family or 0,
                    ),
                    timeout=timeout,
                )
                return UpstreamConnectResult(
                    reader=reader,
                    writer=writer,
                    method=UpstreamMode.DIRECT_IP.value,
                    endpoint=target,
                )
            except Exception as exc:
                failures.append(f"{target}={_format_exc(exc)}")

        return UpstreamConnectResult(
            reader=None,
            writer=None,
            method=UpstreamMode.DIRECT_IP.value,
            endpoint=", ".join(ep.ip or ep.host for ep in targets),
            error=", ".join(failures),
        )


def _recv_until_header_end(sock: socket.socket) -> bytes:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise OSError("proxy closed during CONNECT")
        buf += chunk
        if len(buf) > 65536:
            raise OSError("proxy CONNECT response too large")
    return bytes(buf)


def _blocking_http_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        headers = [
            f"CONNECT {target_host}:{target_port} HTTP/1.1",
            f"Host: {target_host}:{target_port}",
            "Proxy-Connection: Keep-Alive",
        ]
        if username:
            raw = f"{username}:{password or ''}".encode("utf-8", errors="replace")
            token = base64.b64encode(raw).decode("ascii")
            headers.append(f"Proxy-Authorization: Basic {token}")
        req = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")

        sock.sendall(req)
        response = _recv_until_header_end(sock)
        status_line = response.split(b"\r\n", 1)[0]
        if b" 200 " not in status_line and not status_line.startswith(b"HTTP/1.1 200"):
            raise OSError(f"proxy CONNECT failed: {status_line!r}")

        sock.setblocking(False)
        return sock
    except Exception:
        sock.close()
        raise


class HttpConnectConnector(BaseUpstreamConnector):
    def __init__(self, proxy: HttpProxyConfig, method: str = UpstreamMode.HTTP_CONNECT.value):
        self.proxy = proxy
        self.method = method

    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        loop = asyncio.get_running_loop()
        target_port = _target_port(endpoints)
        endpoint = f"{self.proxy.host}:{self.proxy.port}->{host}:{target_port}"
        raw_sock: Optional[socket.socket] = None
        try:
            raw_sock = await loop.run_in_executor(
                None,
                _blocking_http_connect_socket,
                self.proxy.host,
                self.proxy.port,
                host,
                target_port,
                timeout,
                self.proxy.username,
                self.proxy.password,
            )

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    sock=raw_sock,
                    ssl=ssl_ctx,
                    server_hostname=host,
                ),
                timeout=timeout,
            )
            raw_sock = None

            return UpstreamConnectResult(
                reader=reader,
                writer=writer,
                method=self.method,
                endpoint=endpoint,
            )
        except Exception as exc:
            if raw_sock is not None:
                raw_sock.close()
            return UpstreamConnectResult(
                reader=None,
                writer=None,
                method=self.method,
                endpoint=endpoint,
                error=_format_exc(exc),
            )


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise OSError("SOCKS5 proxy closed connection")
        data += chunk
    return bytes(data)


def _blocking_socks5_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        methods = [0x00]
        if username:
            methods.append(0x02)
        sock.sendall(bytes([0x05, len(methods), *methods]))
        resp = _recv_exact(sock, 2)
        if resp[0] != 0x05:
            raise OSError(f"SOCKS5 bad greeting response: {resp!r}")
        if resp[1] == 0x02:
            user_bytes = username.encode("utf-8", errors="replace") if username else b""
            pass_bytes = (password or "").encode("utf-8", errors="replace")
            if len(user_bytes) > 255 or len(pass_bytes) > 255:
                raise OSError("SOCKS5 username/password too long")
            sock.sendall(b"\x01" + bytes([len(user_bytes)]) + user_bytes + bytes([len(pass_bytes)]) + pass_bytes)
            auth = _recv_exact(sock, 2)
            if auth != b"\x01\x00":
                raise OSError(f"SOCKS5 username/password rejected: {auth!r}")
        elif resp[1] != 0x00:
            raise OSError(f"SOCKS5 no-auth rejected: {resp!r}")

        host_bytes = target_host.encode("idna")
        if len(host_bytes) > 255:
            raise OSError("SOCKS5 target host too long")
        req = (
            b"\x05\x01\x00"
            + b"\x03"
            + bytes([len(host_bytes)])
            + host_bytes
            + target_port.to_bytes(2, "big")
        )
        sock.sendall(req)

        resp = _recv_exact(sock, 4)
        if resp[0] != 0x05 or resp[1] != 0:
            raise OSError(f"SOCKS5 connect failed: {resp!r}")

        atyp = resp[3]
        if atyp == 1:
            _recv_exact(sock, 4)
        elif atyp == 3:
            n = _recv_exact(sock, 1)[0]
            _recv_exact(sock, n)
        elif atyp == 4:
            _recv_exact(sock, 16)
        else:
            raise OSError(f"SOCKS5 unknown address type: {atyp}")
        _recv_exact(sock, 2)

        sock.setblocking(False)
        return sock
    except Exception:
        sock.close()
        raise


class Socks5Connector(BaseUpstreamConnector):
    def __init__(self, proxy: Socks5ProxyConfig, method: str = UpstreamMode.SOCKS5.value):
        self.proxy = proxy
        self.method = method

    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        loop = asyncio.get_running_loop()
        target_port = _target_port(endpoints)
        endpoint = f"{self.proxy.host}:{self.proxy.port}->{host}:{target_port}"
        raw_sock: Optional[socket.socket] = None
        try:
            raw_sock = await loop.run_in_executor(
                None,
                _blocking_socks5_connect_socket,
                self.proxy.host,
                self.proxy.port,
                host,
                target_port,
                timeout,
                self.proxy.username,
                self.proxy.password,
            )
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    sock=raw_sock,
                    ssl=ssl_ctx,
                    server_hostname=host,
                ),
                timeout=timeout,
            )
            raw_sock = None

            return UpstreamConnectResult(
                reader=reader,
                writer=writer,
                method=self.method,
                endpoint=endpoint,
            )
        except Exception as exc:
            if raw_sock is not None:
                raw_sock.close()
            return UpstreamConnectResult(
                reader=None,
                writer=None,
                method=self.method,
                endpoint=endpoint,
                error=_format_exc(exc),
            )


class UnavailableConnector(BaseUpstreamConnector):
    def __init__(self, method: str, reason: str):
        self.method = method
        self.reason = reason

    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        return UpstreamConnectResult(
            reader=None,
            writer=None,
            method=self.method,
            endpoint=host,
            error=self.reason,
        )


class AutoConnector(BaseUpstreamConnector):
    def __init__(
        self,
        direct: Optional[BaseUpstreamConnector] = None,
        system_http_proxy: Optional[BaseUpstreamConnector] = None,
        manual_http_proxy: Optional[BaseUpstreamConnector] = None,
        manual_socks5: Optional[BaseUpstreamConnector] = None,
        cooldown_seconds: float = 120.0,
    ) -> None:
        self.direct = direct or DirectIpConnector()
        self.system_http_proxy = system_http_proxy
        self.manual_http_proxy = manual_http_proxy
        self.manual_socks5 = manual_socks5
        self.cooldown_seconds = cooldown_seconds
        self._host_state: dict[str, HostTransportState] = {}

    def state_for(self, host: str) -> HostTransportState:
        return self._host_state.setdefault(host, HostTransportState())

    def prime_host(self, host: str, method: str) -> None:
        now = time.monotonic()
        state = self.state_for(host)
        state.direct_ip_unhealthy_until = now + self.cooldown_seconds
        state.preferred_method = method
        state.preferred_until = now + self.cooldown_seconds
        state.last_success_method = method

    def _connectors_by_method(self) -> dict[str, BaseUpstreamConnector]:
        connectors: dict[str, BaseUpstreamConnector] = {
            UpstreamMode.DIRECT_IP.value: self.direct,
        }
        if self.system_http_proxy is not None:
            connectors["system_http_connect"] = self.system_http_proxy
        if self.manual_http_proxy is not None:
            connectors[UpstreamMode.HTTP_CONNECT.value] = self.manual_http_proxy
        if self.manual_socks5 is not None:
            connectors[UpstreamMode.SOCKS5.value] = self.manual_socks5
        return connectors

    async def _try_connector(
        self,
        connector: BaseUpstreamConnector,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
        failures: list[str],
    ) -> UpstreamConnectResult:
        result = await connector.connect(host, endpoints, ssl_ctx, timeout)
        if result.writer is not None:
            if failures:
                result.prior_errors = tuple(failures)
            return result
        failures.append(f"{result.method}: {result.error or 'failed'}")
        return result

    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        now = time.monotonic()
        state = self.state_for(host)
        failures: list[str] = []
        attempted: set[str] = set()
        connectors_by_method = self._connectors_by_method()

        preferred = state.preferred_method
        if preferred and state.preferred_until > now and preferred in connectors_by_method:
            attempted.add(preferred)
            result = await self._try_connector(
                connectors_by_method[preferred], host, endpoints, ssl_ctx, timeout, failures,
            )
            if result.writer is not None:
                state.last_success_method = result.method
                state.preferred_until = now + self.cooldown_seconds
                return result

        direct_unhealthy = state.direct_ip_unhealthy_until > now
        if UpstreamMode.DIRECT_IP.value not in attempted and not direct_unhealthy:
            attempted.add(UpstreamMode.DIRECT_IP.value)
            result = await self._try_connector(self.direct, host, endpoints, ssl_ctx, timeout, failures)
            if result.writer is not None:
                state.last_success_method = result.method
                state.preferred_method = None
                state.preferred_until = 0.0
                return result
            state.direct_ip_unhealthy_until = time.monotonic() + self.cooldown_seconds
        elif UpstreamMode.DIRECT_IP.value not in attempted and direct_unhealthy:
            failures.append("direct_ip: skipped during short unhealthy cooldown")

        for connector in (self.system_http_proxy, self.manual_http_proxy, self.manual_socks5):
            if connector is None:
                continue
            method = getattr(connector, "method", "")
            if method in attempted:
                continue
            attempted.add(method)
            result = await self._try_connector(connector, host, endpoints, ssl_ctx, timeout, failures)
            if result.writer is not None:
                state.preferred_method = result.method
                state.preferred_until = time.monotonic() + self.cooldown_seconds
                state.last_success_method = result.method
                return result

        return UpstreamConnectResult(
            reader=None,
            writer=None,
            method=UpstreamMode.AUTO.value,
            endpoint=host,
            error=" | ".join(failures) if failures else "no upstream transport attempted",
        )
