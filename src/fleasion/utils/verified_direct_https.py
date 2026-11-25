"""Verified HTTPS to an upstream IP despite a managed hosts-file redirect.

The URL, HTTP Host, TLS SNI and peer-certificate hostname remain the original
hostname. Only the TCP destination uses Fleasion's pre-redirect DNS result.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any, override
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter

if TYPE_CHECKING:
    from urllib3.connectionpool import HTTPConnectionPool


class VerifiedDirectHTTPSAdapter(HTTPAdapter):
    """Connect to a known address without weakening HTTPS origin validation."""

    def __init__(self, hostname: str, address: str, *, port: int = 443) -> None:
        hostname = hostname.lower().rstrip('.')
        if not hostname or '/' in hostname or ':' in hostname:
            message = 'Invalid verified HTTPS hostname'
            raise ValueError(message)
        # Resolve no DNS inside the adapter: the caller supplies an IP captured
        # before Fleasion redirected the hostname to loopback.
        self._address = str(ipaddress.ip_address(address))
        self._hostname = hostname
        if not 1 <= port <= 65535:
            message = 'Invalid direct HTTPS destination port'
            raise ValueError(message)
        self._port = port
        super().__init__()

    @override
    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: bool | str,
        proxies: dict[str, str] | None = None,
        cert: str | tuple[str, str] | None = None,
    ) -> HTTPConnectionPool:
        url = urlsplit(request.url or '')
        if (
            url.scheme.lower() != 'https'
            or url.hostname != self._hostname
            or url.port not in {None, 443}
        ):
            message = 'Verified HTTPS destination does not match origin'
            raise requests.exceptions.InvalidURL(message)
        if verify is False:
            message = 'Unverified TLS is forbidden for direct-host bypass'
            raise requests.exceptions.SSLError(message)
        if proxies:
            message = 'Direct-host bypass cannot use a proxy'
            raise requests.exceptions.ProxyError(message)
        host_params, pool_kwargs = self.build_connection_pool_key_attributes(request, verify, cert)
        host_params['host'] = self._address
        host_params['port'] = self._port
        pool_kwargs['server_hostname'] = self._hostname
        pool_kwargs['assert_hostname'] = self._hostname
        return self.poolmanager.connection_from_host(**host_params, pool_kwargs=pool_kwargs)

    @override
    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: Any = None,
        verify: bool | str = True,
        cert: str | tuple[str, str] | None = None,
        proxies: dict[str, str] | None = None,
    ) -> requests.Response:
        # urllib3 would otherwise generate an IP-literal Host header from the
        # socket's destination, which does not identify the requested origin.
        request.headers['Host'] = self._hostname
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )
