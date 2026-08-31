"""Upstream transport connectors for Fleasion's local TLS proxy."""

from __future__ import annotations

import asyncio
import base64
import functools
import socket
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ssl
    from collections.abc import Mapping, Sequence


class UpstreamMode(StrEnum):
    AUTO = 'auto'
    DIRECT_IP = 'direct_ip'
    SYSTEM_PROXY = 'system_proxy'
    HTTP_CONNECT = 'http_connect'
    SOCKS5 = 'socks5'


@dataclass(frozen=True)
class UpstreamEndpoint:
    host: str
    port: int = 443
    ip: str | None = None
    family: int | None = None


@dataclass
class UpstreamConnectResult:
    reader: asyncio.StreamReader | None
    writer: asyncio.StreamWriter | None
    method: str
    endpoint: str
    error: str | None = None
    prior_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class HttpProxyConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class Socks5ProxyConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None


@dataclass
class HostTransportState:
    preferred_method: str | None = None
    preferred_until: float = 0.0
    direct_ip_unhealthy_until: float = 0.0
    last_success_method: str | None = None


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
    return f'{type(exc).__name__}: {text}' if text else type(exc).__name__


def _target_port(endpoints: Sequence[UpstreamEndpoint]) -> int:
    return endpoints[0].port if endpoints else 443


def normalize_upstream_mode(value: object) -> UpstreamMode:
    try:
        return UpstreamMode(str(value or UpstreamMode.AUTO.value).lower())
    except ValueError:
        return UpstreamMode.AUTO


def _string_value(value: object) -> str | None:
    return value if isinstance(value, str) else None


def normalize_endpoints(
    upstream_endpoints: Mapping[str, Sequence[UpstreamEndpoint | str]] | None,
) -> dict[str, list[UpstreamEndpoint]]:
    normalized: dict[str, list[UpstreamEndpoint]] = {}
    for host, endpoints in (upstream_endpoints or {}).items():
        items: list[UpstreamEndpoint] = []
        for ep in endpoints or ():
            if isinstance(ep, UpstreamEndpoint):
                items.append(ep)
            else:
                endpoint_ip = _string_value(ep)
                if endpoint_ip is not None:
                    items.append(UpstreamEndpoint(host=host, ip=endpoint_ip))
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
            except OSError as exc:
                failures.append(f'{target}={_format_exc(exc)}')

        return UpstreamConnectResult(
            reader=None,
            writer=None,
            method=UpstreamMode.DIRECT_IP.value,
            endpoint=', '.join(ep.ip or ep.host for ep in targets),
            error=', '.join(failures),
        )


def _recv_until_header_end(sock: socket.socket) -> bytes:
    buf = bytearray()
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            msg = 'proxy closed during CONNECT'
            raise OSError(msg)
        buf += chunk
        if len(buf) > 65536:
            msg = 'proxy CONNECT response too large'
            raise OSError(msg)
    return bytes(buf)


def _http_connect_request(
    target_host: str,
    target_port: int,
    *,
    username: str | None,
    password: str | None,
) -> bytes:
    headers = [
        f'CONNECT {target_host}:{target_port} HTTP/1.1',
        f'Host: {target_host}:{target_port}',
        'Proxy-Connection: Keep-Alive',
    ]
    if username:
        raw = f'{username}:{password or ""}'.encode('utf-8', errors='replace')
        token = base64.b64encode(raw).decode('ascii')
        headers.append(f'Proxy-Authorization: Basic {token}')
    return ('\r\n'.join(headers) + '\r\n\r\n').encode('ascii')


def _http_connect_handshake(
    sock: socket.socket,
    target_host: str,
    target_port: int,
    *,
    username: str | None,
    password: str | None,
) -> None:
    request = _http_connect_request(
        target_host,
        target_port,
        username=username,
        password=password,
    )
    sock.sendall(request)
    response = _recv_until_header_end(sock)
    status_line = response.split(b'\r\n', 1)[0]
    if b' 200 ' not in status_line and not status_line.startswith(b'HTTP/1.1 200'):
        msg = f'proxy CONNECT failed: {status_line!r}'
        raise OSError(msg)


def _blocking_http_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
    *,
    username: str | None = None,
    password: str | None = None,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        _http_connect_handshake(
            sock,
            target_host,
            target_port,
            username=username,
            password=password,
        )
    except Exception:
        sock.close()
        raise
    sock.settimeout(0.0)
    return sock


class HttpConnectConnector(BaseUpstreamConnector):
    def __init__(
        self, proxy: HttpProxyConfig, method: str = UpstreamMode.HTTP_CONNECT.value
    ) -> None:
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
        endpoint = f'{self.proxy.host}:{self.proxy.port}->{host}:{target_port}'
        raw_sock: socket.socket | None = None
        try:
            connect_socket = functools.partial(
                _blocking_http_connect_socket,
                self.proxy.host,
                self.proxy.port,
                host,
                target_port,
                timeout,
                username=self.proxy.username,
                password=self.proxy.password,
            )
            raw_sock = await loop.run_in_executor(None, connect_socket)

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
        except OSError as exc:
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
            msg = 'SOCKS5 proxy closed connection'
            raise OSError(msg)
        data += chunk
    return bytes(data)


def _socks5_negotiate_auth(
    sock: socket.socket,
    *,
    username: str | None,
    password: str | None,
) -> None:
    methods = [0x00]
    if username:
        methods.append(0x02)
    sock.sendall(bytes([0x05, len(methods), *methods]))
    response = _recv_exact(sock, 2)
    if response[0] != 0x05:
        msg = f'SOCKS5 bad greeting response: {response!r}'
        raise OSError(msg)
    if response[1] == 0x02:
        user_bytes = username.encode('utf-8', errors='replace') if username else b''
        pass_bytes = (password or '').encode('utf-8', errors='replace')
        if len(user_bytes) > 255 or len(pass_bytes) > 255:
            msg = 'SOCKS5 username/password too long'
            raise OSError(msg)
        sock.sendall(
            b'\x01'
            + bytes([len(user_bytes)])
            + user_bytes
            + bytes([len(pass_bytes)])
            + pass_bytes
        )
        auth = _recv_exact(sock, 2)
        if auth != b'\x01\x00':
            msg = f'SOCKS5 username/password rejected: {auth!r}'
            raise OSError(msg)
    elif response[1] != 0x00:
        msg = f'SOCKS5 no-auth rejected: {response!r}'
        raise OSError(msg)


def _socks5_connect_target(sock: socket.socket, target_host: str, target_port: int) -> None:
    host_bytes = target_host.encode('idna')
    if len(host_bytes) > 255:
        msg = 'SOCKS5 target host too long'
        raise OSError(msg)
    request = (
        b'\x05\x01\x00'
        b'\x03' + bytes([len(host_bytes)]) + host_bytes + target_port.to_bytes(2, 'big')
    )
    sock.sendall(request)

    response = _recv_exact(sock, 4)
    if response[0] != 0x05 or response[1] != 0:
        msg = f'SOCKS5 connect failed: {response!r}'
        raise OSError(msg)

    address_type = response[3]
    if address_type == 1:
        _recv_exact(sock, 4)
    elif address_type == 3:
        length = _recv_exact(sock, 1)[0]
        _recv_exact(sock, length)
    elif address_type == 4:
        _recv_exact(sock, 16)
    else:
        msg = f'SOCKS5 unknown address type: {address_type}'
        raise OSError(msg)
    _recv_exact(sock, 2)


def _blocking_socks5_connect_socket(
    proxy_host: str,
    proxy_port: int,
    target_host: str,
    target_port: int,
    timeout: float,
    *,
    username: str | None = None,
    password: str | None = None,
) -> socket.socket:
    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        _socks5_negotiate_auth(sock, username=username, password=password)
        _socks5_connect_target(sock, target_host, target_port)
    except Exception:
        sock.close()
        raise
    sock.settimeout(0.0)
    return sock


class Socks5Connector(BaseUpstreamConnector):
    def __init__(self, proxy: Socks5ProxyConfig, method: str = UpstreamMode.SOCKS5.value) -> None:
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
        endpoint = f'{self.proxy.host}:{self.proxy.port}->{host}:{target_port}'
        raw_sock: socket.socket | None = None
        try:
            connect_socket = functools.partial(
                _blocking_socks5_connect_socket,
                self.proxy.host,
                self.proxy.port,
                host,
                target_port,
                timeout,
                username=self.proxy.username,
                password=self.proxy.password,
            )
            raw_sock = await loop.run_in_executor(None, connect_socket)
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
        except OSError as exc:
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
    def __init__(self, method: str, reason: str) -> None:
        self.method = method
        self.reason = reason

    async def connect(
        self,
        host: str,
        endpoints: Sequence[UpstreamEndpoint],
        ssl_ctx: ssl.SSLContext,
        timeout: float,
    ) -> UpstreamConnectResult:
        del endpoints, ssl_ctx, timeout
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
        direct: BaseUpstreamConnector | None = None,
        system_http_proxy: BaseUpstreamConnector | None = None,
        manual_http_proxy: BaseUpstreamConnector | None = None,
        manual_socks5: BaseUpstreamConnector | None = None,
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

    def note_direct_success(self, host: str) -> None:
        """Clear a previous direct-path failure after a refreshed retry works."""
        state = self.state_for(host)
        state.direct_ip_unhealthy_until = 0.0
        state.preferred_method = None
        state.preferred_until = 0.0
        state.last_success_method = UpstreamMode.DIRECT_IP.value

    def _has_fallback_transport(self) -> bool:
        """Whether a failed direct route can actually be bypassed.

        In ``auto`` mode without a detected/configured upstream proxy, a
        direct-IP cooldown used to turn one transient timeout into a two-minute
        outage: every later request skipped the only available transport.  Keep
        the cooldown only when there is a real alternate path to prefer.
        """
        return any(
            connector is not None
            for connector in (
                self.system_http_proxy,
                self.manual_http_proxy,
                self.manual_socks5,
            )
        )

    def _connectors_by_method(self) -> dict[str, BaseUpstreamConnector]:
        connectors: dict[str, BaseUpstreamConnector] = {
            UpstreamMode.DIRECT_IP.value: self.direct,
        }
        if self.system_http_proxy is not None:
            connectors['system_http_connect'] = self.system_http_proxy
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
        *,
        failures: list[str],
    ) -> UpstreamConnectResult:
        result = await connector.connect(host, endpoints, ssl_ctx, timeout)
        if result.writer is not None:
            if failures:
                result.prior_errors = tuple(failures)
            return result
        failures.append(f'{result.method}: {result.error or "failed"}')
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
                connectors_by_method[preferred],
                host,
                endpoints,
                ssl_ctx,
                timeout,
                failures=failures,
            )
            if result.writer is not None:
                state.last_success_method = result.method
                state.preferred_until = now + self.cooldown_seconds
                return result

        direct_unhealthy = state.direct_ip_unhealthy_until > now and self._has_fallback_transport()
        if UpstreamMode.DIRECT_IP.value not in attempted and not direct_unhealthy:
            attempted.add(UpstreamMode.DIRECT_IP.value)
            result = await self._try_connector(
                self.direct,
                host,
                endpoints,
                ssl_ctx,
                timeout,
                failures=failures,
            )
            if result.writer is not None:
                state.last_success_method = result.method
                state.preferred_method = None
                state.preferred_until = 0.0
                return result
            state.direct_ip_unhealthy_until = time.monotonic() + self.cooldown_seconds
        elif UpstreamMode.DIRECT_IP.value not in attempted and direct_unhealthy:
            failures.append('direct_ip: skipped during short unhealthy cooldown')

        for connector in (
            self.system_http_proxy,
            self.manual_http_proxy,
            self.manual_socks5,
        ):
            if connector is None:
                continue
            method = getattr(connector, 'method', '')
            if method in attempted:
                continue
            attempted.add(method)
            result = await self._try_connector(
                connector,
                host,
                endpoints,
                ssl_ctx,
                timeout,
                failures=failures,
            )
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
            error=' | '.join(failures) if failures else 'no upstream transport attempted',
        )
