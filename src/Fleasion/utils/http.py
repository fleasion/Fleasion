"""Small urllib helpers for verified HTTPS downloads."""

from __future__ import annotations

import ssl
import subprocess
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
import shutil


_USER_AGENT = 'FleasionNT/1.2.0'


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
            return urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except urllib.error.URLError as exc:
            last_exc = exc

    if last_exc is not None:
        raise last_exc
    raise RuntimeError('No HTTPS fallback context available')


def _open_verified(
    req: urllib.request.Request,
    url: str,
    timeout: int,
):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
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
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)

    with _open_verified(req, url, timeout) as resp:
        return resp.read()


def http_head_status(url: str, timeout: int = 15, headers: dict[str, str] | None = None) -> int:
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers, method='HEAD')

    with _open_verified(req, url, timeout) as resp:
        return resp.status


def http_download_to(
    url: str,
    dest: Path,
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> None:
    request_headers = {'User-Agent': _USER_AGENT}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, headers=request_headers)

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
    cmd.append(url)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or '').strip()
            raise RuntimeError(detail or f'curl exited with code {result.returncode}')
        tmp.replace(dest)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f'urllib download failed: {original_exc}; curl fallback failed: {exc}'
        ) from original_exc
