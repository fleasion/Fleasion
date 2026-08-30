"""Small urllib helpers for verified HTTPS downloads."""

from __future__ import annotations

import shutil
import ssl
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]

_USER_AGENT = 'FleasionNT/1.2.0'


def _validate_http_url(url: str) -> None:
    scheme = urllib.parse.urlsplit(url).scheme.lower()
    if scheme not in {'http', 'https'}:
        msg = f'Unsupported URL scheme: {scheme or "missing"}'
        raise ValueError(msg)


def _log_http(message: str) -> None:
    try:
        from .logging import log_buffer  # ruff: ignore[import-outside-top-level]
    except Exception:  # ruff: ignore[blind-except]
        return
    log_buffer.log('HTTP', message)


@lru_cache(maxsize=1)
def _certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi  # ruff: ignore[import-outside-top-level]
    except Exception:  # ruff: ignore[blind-except]
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
        import certifi  # ruff: ignore[import-outside-top-level]
    except Exception:  # ruff: ignore[blind-except]
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


def _open_with_contexts(
    req: urllib.request.Request,
    timeout: int,
    contexts: list[ssl.SSLContext | None],
):
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
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)  # ruff: ignore[suspicious-url-open-usage]
        except urllib.error.URLError as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    msg = 'No HTTPS fallback context available'
    raise RuntimeError(msg)


def _open_verified(
    req: urllib.request.Request,
    url: str,
    timeout: int,
):
    try:
        return urllib.request.urlopen(req, timeout=timeout)  # ruff: ignore[suspicious-url-open-usage]
    except urllib.error.URLError as exc:
        if not url.lower().startswith('https://'):
            raise

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


def http_get(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> bytes:
    _validate_http_url(url)
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)  # ruff: ignore[suspicious-url-open-usage]

    with _open_verified(req, url, timeout) as resp:
        return resp.read()


def http_head_status(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> int:
    _validate_http_url(url)
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(  # ruff: ignore[suspicious-url-open-usage]
        url, headers=request_headers, method='HEAD'
    )

    with _open_verified(req, url, timeout) as resp:
        return resp.status


def http_download_to(
    url: str,
    dest: Path,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> None:
    _validate_http_url(url)
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)  # ruff: ignore[suspicious-url-open-usage]

    try:
        with _open_verified(req, url, timeout) as resp, dest.open('wb') as out:
            shutil.copyfileobj(resp, out)
    except (urllib.error.URLError, OSError) as exc:
        _curl_download_to(url, dest, timeout, request_headers, exc)


def _curl_download_to(
    url: str,
    dest: Path,
    timeout: int,
    headers: dict[str, str],
    original_exc: Exception,
) -> None:
    _validate_http_url(url)
    curl = shutil.which('curl')
    if curl is None:
        raise original_exc

    _log_http(f'urllib download failed for {url}; retrying with curl')
    tmp = dest.with_name(f'{dest.name}.download')
    cmd = [
        curl,
        '--fail',
        '--location',
        '--silent',
        '--show-error',
        '--max-time',
        str(max(1, int(timeout))),
        '--output',
        str(tmp),
    ]
    for key, value in headers.items():
        if key.lower() == 'user-agent':
            cmd.extend(['--user-agent', value])
        else:
            cmd.extend(['--header', f'{key}: {value}'])
    cmd.extend(['--', url])

    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            cmd,
            capture_output=True,
            check=False,
            text=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise RuntimeError(detail or f'curl exited with code {result.returncode}')
        tmp.replace(dest)
    except Exception as exc:  # ruff: ignore[blind-except]
        tmp.unlink(missing_ok=True)
        msg = f'urllib download failed: {original_exc}; curl fallback failed: {exc}'
        raise RuntimeError(msg) from original_exc
