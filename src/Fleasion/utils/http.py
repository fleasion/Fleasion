"""Small urllib helpers for verified HTTPS downloads."""

from __future__ import annotations

import ssl
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path
import shutil


_USER_AGENT = 'FleasionNT/1.2.0'


@lru_cache(maxsize=1)
def _certifi_context() -> ssl.SSLContext | None:
    try:
        import certifi
    except Exception:
        return None
    return ssl.create_default_context(cafile=certifi.where())


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


def _open_verified(
    req: urllib.request.Request,
    url: str,
    timeout: int,
):
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.URLError as exc:
        if not url.lower().startswith('https://') or not _is_certificate_verify_error(exc):
            raise

        ctx = _certifi_context()
        if ctx is None:
            raise
        return urllib.request.urlopen(req, timeout=timeout, context=ctx)


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

    with _open_verified(req, url, timeout) as resp, dest.open('wb') as out:
        shutil.copyfileobj(resp, out)
