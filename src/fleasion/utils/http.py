"""Small urllib helpers for bounded, verified public HTTPS downloads."""

from __future__ import annotations

import ipaddress
import shutil
import socket
import ssl
import subprocess
import threading
import urllib.error
import urllib.request
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin, urlparse

_USER_AGENT: Final = 'FleasionNT/1.2.0'
_DEFAULT_MAX_BYTES: Final = 64 * 1024 * 1024
_DEFAULT_DOWNLOAD_MAX_BYTES: Final = 128 * 1024 * 1024
_STREAM_CHUNK_BYTES: Final = 64 * 1024
_MAX_REDIRECTS: Final = 5
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_CREDENTIAL_HEADERS: Final = frozenset(
    {'authorization', 'cookie', 'cookie2', 'proxy-authorization'}
)


class HttpSafetyError(ValueError):
    """Raised when a remote URL can reach a non-public network endpoint."""


class HttpSizeLimitError(ValueError):
    """Raised before a remote response can exceed its configured byte limit."""


class HttpCancelledError(RuntimeError):
    """Raised when a caller cancels a bounded HTTP operation."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Expose redirects to the caller so each destination can be validated."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _log_http(message: str) -> None:
    try:
        from .logging import log_buffer
    except Exception:
        return
    log_buffer.log('HTTP', message)


@lru_cache(maxsize=1)
def _certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except Exception:
        return None
    return ssl.create_default_context(cafile=certifi.where())


@lru_cache(maxsize=1)
def _tls12_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


@lru_cache(maxsize=1)
def _certifi_tls12_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except Exception:
        return None
    ctx = ssl.create_default_context(cafile=certifi.where())
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


def _is_certificate_verify_error(exc: BaseException) -> bool:
    seen: set[int] = set()
    pending: list[BaseException] = [exc]

    while pending:
        current = pending.pop()
        ident = id(current)
        if ident in seen:
            continue
        seen.add(ident)

        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        if isinstance(current, urllib.error.URLError) and isinstance(current.reason, BaseException):
            pending.append(current.reason)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    return 'CERTIFICATE_VERIFY_FAILED' in str(exc)


def _is_tls_record_layer_error(exc: BaseException) -> bool:
    text = str(exc).upper()
    return 'RECORD_LAYER_FAILURE' in text or 'RECORD LAYER FAILURE' in text


def _resolved_public_https_target(url: str) -> tuple[str, str, int, tuple[str, ...]]:
    cleaned = url.strip()
    if not cleaned or any(character in cleaned for character in '\r\n\x00'):
        raise HttpSafetyError('Enter a valid HTTPS URL.')

    parsed = urlparse(cleaned)
    if parsed.scheme.casefold() != 'https' or parsed.hostname is None:
        raise HttpSafetyError('Remote sources must use HTTPS.')
    if parsed.username is not None or parsed.password is not None:
        raise HttpSafetyError('Remote URLs cannot contain embedded credentials.')

    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise HttpSafetyError('The remote URL contains an invalid port.') from exc

    hostname = parsed.hostname.rstrip('.')
    if not hostname or '%' in hostname:
        raise HttpSafetyError('The remote URL contains an invalid hostname.')

    try:
        resolved = socket.getaddrinfo(
            hostname,
            port,
            family=socket.AF_UNSPEC,
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise HttpSafetyError(f'The remote hostname could not be resolved: {hostname}') from exc

    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for result in resolved:
        sockaddr = result[4]
        if not sockaddr:
            continue
        try:
            addresses.add(ipaddress.ip_address(sockaddr[0]))
        except ValueError as exc:
            raise HttpSafetyError('The remote hostname resolved to an invalid address.') from exc

    if not addresses:
        raise HttpSafetyError(f'The remote hostname did not resolve to an address: {hostname}')
    if any(not address.is_global for address in addresses):
        raise HttpSafetyError(
            'Private, local, link-local, reserved, and non-global network URLs are not allowed.'
        )
    return cleaned, hostname, port, tuple(str(address) for address in sorted(addresses, key=str))


def validate_public_https_url(url: str) -> str:
    """Return a normalized URL after resolving every host address as public.

    A hostname is rejected when any returned address is loopback, private,
    link-local, reserved, unspecified, multicast, or otherwise non-global.
    """
    return _resolved_public_https_target(url)[0]


def _open_once(
    req: urllib.request.Request,
    timeout: int,
    context: ssl.SSLContext | None,
) -> Any:
    handlers: list[Any] = [_NoRedirectHandler()]
    if context is not None:
        handlers.append(urllib.request.HTTPSHandler(context=context))
    opener = urllib.request.build_opener(*handlers)
    return opener.open(req, timeout=timeout)


def _open_with_contexts(
    req: urllib.request.Request,
    timeout: int,
    contexts: list[ssl.SSLContext | None],
) -> Any:
    last_exc: urllib.error.URLError | None = None
    seen: set[int] = set()

    for ctx in contexts:
        if ctx is None:
            continue
        ident = id(ctx)
        if ident in seen:
            continue
        seen.add(ident)
        try:
            return _open_once(req, timeout, ctx)
        except urllib.error.URLError as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError('No HTTPS fallback context available')


def _open_verified(
    req: urllib.request.Request,
    url: str,
    timeout: int,
) -> Any:
    try:
        return _open_once(req, timeout, None)
    except urllib.error.URLError as exc:
        if _is_certificate_verify_error(exc):
            _log_http(f'Certificate verification failed for {url}; retrying with certifi')
            ctx = _certifi_context()
            if ctx is None:
                raise
            return _open_with_contexts(req, timeout, [ctx])

        if _is_tls_record_layer_error(exc):
            _log_http(f'TLS record layer failure for {url}; retrying with TLS 1.2')
            return _open_with_contexts(
                req,
                timeout,
                [_tls12_context(), _certifi_tls12_context()],
            )

        raise


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urlparse(url)
    hostname = parsed.hostname or ''
    return (parsed.scheme.casefold(), hostname.rstrip('.').casefold(), parsed.port or 443)


def _without_cross_origin_credentials(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.casefold() not in _CREDENTIAL_HEADERS and key.casefold() != 'host'
    }


def _redirect_destination(response: Any, current_url: str) -> str | None:
    status = getattr(response, 'status', None)
    if status is None:
        getcode = getattr(response, 'getcode', None)
        status = getcode() if callable(getcode) else None
    if status not in _REDIRECT_STATUSES:
        return None
    location = response.headers.get('Location')
    if not location:
        raise HttpSafetyError('The remote server returned a redirect without a destination.')
    return urljoin(current_url, location)


def _open_request(
    url: str,
    *,
    method: str,
    timeout: int,
    headers: dict[str, str],
    cancel_event: threading.Event | None = None,
) -> Any:
    current_url = validate_public_https_url(url)
    current_headers = dict(headers)

    for redirect_count in range(_MAX_REDIRECTS + 1):
        if cancel_event is not None and cancel_event.is_set():
            raise HttpCancelledError('The HTTPS request was cancelled.')
        req = urllib.request.Request(current_url, headers=current_headers, method=method)
        try:
            response = _open_verified(req, current_url, timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_STATUSES:
                raise
            response = exc

        try:
            destination = _redirect_destination(response, current_url)
        except Exception:
            response.close()
            raise
        if destination is None:
            return response

        response.close()
        if redirect_count >= _MAX_REDIRECTS:
            raise HttpSafetyError('The remote server exceeded the redirect limit.')
        validated_destination = validate_public_https_url(destination)
        if _origin(validated_destination) != _origin(current_url):
            current_headers = _without_cross_origin_credentials(current_headers)
        current_url = validated_destination

    raise HttpSafetyError('The remote server exceeded the redirect limit.')


def _content_length(response: Any) -> int | None:
    value = response.headers.get('Content-Length')
    if value is None:
        return None
    try:
        length = int(value)
    except TypeError, ValueError:
        return None
    return length if length >= 0 else None


def _raise_if_declared_oversized(response: Any, max_bytes: int) -> None:
    declared = _content_length(response)
    if declared is not None and declared > max_bytes:
        raise HttpSizeLimitError(f'The remote response exceeds the {max_bytes} byte safety limit.')


def _read_limited(
    response: Any,
    max_bytes: int,
    cancel_event: threading.Event | None = None,
) -> bytes:
    if max_bytes < 0:
        raise ValueError('max_bytes must not be negative')
    _raise_if_declared_oversized(response, max_bytes)
    data = bytearray()
    while True:
        if cancel_event is not None and cancel_event.is_set():
            raise HttpCancelledError('The HTTPS request was cancelled.')
        remaining = max_bytes - len(data)
        chunk = response.read(min(_STREAM_CHUNK_BYTES, remaining + 1))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > max_bytes:
            raise HttpSizeLimitError(
                f'The remote response exceeds the {max_bytes} byte safety limit.'
            )


def _copy_limited(response: Any, out: Any, max_bytes: int) -> None:
    if max_bytes < 0:
        raise ValueError('max_bytes must not be negative')
    _raise_if_declared_oversized(response, max_bytes)
    written = 0
    while True:
        remaining = max_bytes - written
        chunk = response.read(min(_STREAM_CHUNK_BYTES, remaining + 1))
        if not chunk:
            return
        written += len(chunk)
        if written > max_bytes:
            raise HttpSizeLimitError(
                f'The remote response exceeds the {max_bytes} byte safety limit.'
            )
        out.write(chunk)


def http_get(
    url: str,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
    *,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    cancel_event: threading.Event | None = None,
) -> bytes:
    """Fetch one public HTTPS resource without buffering beyond ``max_bytes``."""
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)

    with _open_request(
        url,
        method='GET',
        timeout=timeout,
        headers=request_headers,
        cancel_event=cancel_event,
    ) as response:
        return _read_limited(response, max_bytes, cancel_event)


def http_head_status(
    url: str,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> int:
    """Return the final status of one validated public HTTPS resource."""
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)

    with _open_request(
        url,
        method='HEAD',
        timeout=timeout,
        headers=request_headers,
    ) as response:
        return int(response.status)


def http_download_to(
    url: str,
    dest: Path,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
    *,
    max_bytes: int = _DEFAULT_DOWNLOAD_MAX_BYTES,
) -> None:
    """Stream one public HTTPS resource into an atomic bounded destination."""
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    temporary = dest.with_name(f'.{dest.name}.{uuid.uuid4().hex}.download')

    try:
        try:
            with (
                _open_request(
                    url,
                    method='GET',
                    timeout=timeout,
                    headers=request_headers,
                ) as response,
                temporary.open('wb') as out,
            ):
                _copy_limited(response, out, max_bytes)
        except urllib.error.URLError as exc:
            temporary.unlink(missing_ok=True)
            _curl_download_to(
                url,
                temporary,
                timeout,
                request_headers,
                max_bytes,
                exc,
            )
        temporary.replace(dest)
    finally:
        temporary.unlink(missing_ok=True)


def _curl_download_to(
    url: str,
    temporary: Path,
    timeout: int,
    headers: dict[str, str],
    max_bytes: int,
    original_exc: urllib.error.URLError,
) -> None:
    curl = shutil.which('curl')
    if curl is None:
        raise original_exc

    cleaned, hostname, port, addresses = _resolved_public_https_target(url)
    address = addresses[0]
    curl_address = f'[{address}]' if ':' in address else address
    _log_http(f'urllib download failed for {cleaned}; retrying with bounded curl')
    cmd = [
        curl,
        '--fail',
        '--silent',
        '--show-error',
        '--max-time',
        str(max(1, int(timeout))),
        '--max-filesize',
        str(max_bytes),
        '--proto',
        '=https',
        '--proto-redir',
        '=https',
        '--resolve',
        f'{hostname}:{port}:{curl_address}',
        '--output',
        str(temporary),
        '--write-out',
        '%{http_code}',
    ]
    for key, value in headers.items():
        if key.casefold() == 'user-agent':
            cmd.extend(['--user-agent', value])
        else:
            cmd.extend(['--header', f'{key}: {value}'])
    cmd.append(cleaned)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        status_text = result.stdout.strip()
        status = int(status_text[-3:]) if len(status_text) >= 3 else 0
        if result.returncode != 0 or not 200 <= status < 300:
            detail = result.stderr.strip()
            raise RuntimeError(
                detail or f'curl returned HTTP {status or "unknown"} (redirects are disabled)'
            )
        if temporary.stat().st_size > max_bytes:
            raise HttpSizeLimitError(
                f'The remote response exceeds the {max_bytes} byte safety limit.'
            )
    except Exception as exc:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            f'urllib download failed: {original_exc}; curl fallback failed: {exc}'
        ) from original_exc


__all__ = [
    'HttpSafetyError',
    'HttpSizeLimitError',
    'HttpCancelledError',
    'http_download_to',
    'http_get',
    'http_head_status',
    'validate_public_https_url',
]
