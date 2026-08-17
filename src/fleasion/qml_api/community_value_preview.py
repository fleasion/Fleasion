"""Safe value resolution for community-preset rich previews."""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final
from urllib.parse import urljoin, urlsplit

from PySide6.QtCore import QUrl

from ..cache.cache_manager import CacheManager
from ..prejsons import PresetValue
from ..utils.http import http_get, validate_public_https_url
from ..utils.roblox_auth import get_roblosecurity
from .payload_preview import PreviewPayload

_MAX_PREVIEW_BYTES: Final = 64 * 1024 * 1024
_STREAM_CHUNK_BYTES: Final = 64 * 1024
_MAX_REDIRECTS: Final = 5
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})
_ROBLOX_COOKIE_DOMAIN: Final = '.roblox.com'
_TRUSTED_ASSET_DOMAINS: Final = ('roblox.com', 'rbxcdn.com')

type PublicFetcher = Callable[..., bytes]
type CookieReader = Callable[[], str | None]


def _cancelled(cancel_event: threading.Event) -> None:
    if cancel_event.is_set():
        raise RuntimeError('The preview request was cancelled.')


def _is_trusted_asset_url(value: str) -> bool:
    parsed = urlsplit(value)
    host = (parsed.hostname or '').casefold().rstrip('.')
    try:
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == 'https'
        and port in {None, 443}
        and any(host == domain or host.endswith(f'.{domain}') for domain in _TRUSTED_ASSET_DOMAINS)
    )


def _type_name_for_source(value: str) -> str:
    suffix = Path(urlsplit(value).path).suffix.casefold()
    return {
        '.json': 'Json',
        '.png': 'Image',
        '.jpg': 'Image',
        '.jpeg': 'Image',
        '.webp': 'Image',
        '.gif': 'Image',
        '.bmp': 'Image',
        '.ogg': 'Audio',
        '.mp3': 'Audio',
        '.wav': 'Audio',
        '.flac': 'Audio',
        '.ttf': 'FontFace',
        '.otf': 'FontFace',
        '.ttc': 'FontFace',
        '.mesh': 'Mesh',
        '.rbxm': 'Model',
        '.rbxmx': 'Model',
        '.rbxl': 'Place',
        '.rbxlx': 'Place',
        '.xml': 'Xml',
    }.get(suffix, '')


class CommunityValueResolver:
    """Resolve IDs, public HTTPS URLs, and local regular files into bounded bytes."""

    def __init__(
        self,
        cache_manager: CacheManager | None = None,
        *,
        public_fetch: PublicFetcher = http_get,
        cookie_reader: CookieReader = get_roblosecurity,
        session_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._cache = cache_manager
        self._public_fetch = public_fetch
        self._cookie_reader = cookie_reader
        self._session_factory = session_factory

    def resolve(
        self,
        value: PresetValue,
        cancel_event: threading.Event,
    ) -> PreviewPayload:
        """Resolve one importable preset leaf without exposing credentials."""
        _cancelled(cancel_event)
        if value.kind == 'id' or isinstance(value.value, int):
            return self.resolve_asset_id(str(value.value), cancel_event, label=value.label)
        source = str(value.value).strip()
        if value.kind == 'url':
            return self._resolve_public_url(source, value.label, cancel_event)
        if value.kind == 'path':
            return self._resolve_local_file(source, value.label, cancel_event)
        raise ValueError('Only asset IDs, public HTTPS URLs, and local files can be previewed.')

    def resolve_asset_id(
        self,
        asset_id: str,
        cancel_event: threading.Event,
        *,
        label: str = '',
    ) -> PreviewPayload:
        """Resolve one positive Roblox asset ID, preferring the existing cache."""
        normalized = asset_id.strip()
        if not normalized.isdecimal() or normalized == '0':
            raise ValueError('The selected Roblox asset ID is invalid.')
        normalized = str(int(normalized))
        cached = self._cached_asset(normalized, label)
        if cached is not None:
            return cached
        data = self._fetch_roblox_asset(normalized, cancel_event)
        return PreviewPayload(
            data=data,
            label=label or f'Asset {normalized}',
            source_value=normalized,
            source_kind='Roblox asset ID',
            asset_id=normalized,
        )

    def _cached_asset(self, asset_id: str, label: str) -> PreviewPayload | None:
        cache = self._cache
        if cache is None:
            return None
        try:
            rows = cache.list_assets()
        except Exception:
            return None
        for row in rows:
            row_id = str(row.get('id') or row.get('asset_id') or '')
            if row_id != asset_id:
                continue
            try:
                asset_type = int(row.get('type') or row.get('asset_type') or 0)
            except (TypeError, ValueError):
                continue
            try:
                data = cache.get_asset(asset_id, asset_type)
            except Exception:
                continue
            if not isinstance(data, bytes) or not data or len(data) > _MAX_PREVIEW_BYTES:
                continue
            name = str(row.get('resolved_name') or row.get('name') or label or f'Asset {asset_id}')
            type_name = str(
                row.get('type_name')
                or row.get('detected_type')
                or cache.get_asset_type_name(asset_type)
            )
            return PreviewPayload(
                data=data,
                label=name,
                source_value=asset_id,
                source_kind='Cached Roblox asset',
                asset_id=asset_id,
                asset_type=asset_type,
                type_name=type_name,
            )
        return None

    def _resolve_public_url(
        self,
        source: str,
        label: str,
        cancel_event: threading.Event,
    ) -> PreviewPayload:
        _cancelled(cancel_event)
        validate_public_https_url(source)
        data = self._public_fetch(
            source,
            timeout=15,
            max_bytes=_MAX_PREVIEW_BYTES,
            cancel_event=cancel_event,
        )
        _cancelled(cancel_event)
        return PreviewPayload(
            data=data,
            label=label or Path(urlsplit(source).path).name or 'HTTPS preview',
            source_value=source,
            source_kind='Public HTTPS URL',
            type_name=_type_name_for_source(source),
        )

    def _resolve_local_file(
        self,
        source: str,
        label: str,
        cancel_event: threading.Event,
    ) -> PreviewPayload:
        parsed = urlsplit(source)
        if parsed.scheme.casefold() == 'file':
            host = (parsed.hostname or '').casefold()
            if host not in {'', 'localhost'}:
                raise ValueError('Remote file URLs cannot be previewed.')
            candidate = Path(QUrl(source).toLocalFile())
        else:
            candidate = Path(source).expanduser()
        if candidate.is_symlink():
            raise ValueError('Symbolic links are not accepted as local preview files.')
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise ValueError('The selected local preview file does not exist.') from exc

        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(resolved, flags)
        except OSError as exc:
            raise ValueError('The selected local preview file could not be opened safely.') from exc
        try:
            file_stat = os.fstat(descriptor)
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError('Local previews must be regular files.')
            if file_stat.st_size > _MAX_PREVIEW_BYTES:
                raise ValueError('The local preview file exceeds the 64 MB safety limit.')
            data = bytearray()
            with os.fdopen(descriptor, 'rb', closefd=False) as handle:
                while True:
                    _cancelled(cancel_event)
                    chunk = handle.read(
                        min(_STREAM_CHUNK_BYTES, _MAX_PREVIEW_BYTES - len(data) + 1)
                    )
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > _MAX_PREVIEW_BYTES:
                        raise ValueError('The local preview file exceeds the 64 MB safety limit.')
        finally:
            os.close(descriptor)
        return PreviewPayload(
            data=bytes(data),
            label=label or resolved.name,
            source_value=str(resolved),
            source_kind='Local file',
            type_name=_type_name_for_source(str(resolved)),
        )

    def _fetch_roblox_asset(
        self,
        asset_id: str,
        cancel_event: threading.Event,
    ) -> bytes:
        import requests

        session = self._session_factory() if self._session_factory is not None else requests.Session()
        session.trust_env = False
        session.proxies = {}
        session.headers.update(
            {
                'User-Agent': 'Roblox/WinInet',
                'Accept-Encoding': 'gzip, deflate',
            }
        )
        try:
            cookie = self._cookie_reader()
        except Exception:
            cookie = None
        if cookie:
            session.cookies.set(
                '.ROBLOSECURITY',
                cookie,
                domain=_ROBLOX_COOKIE_DOMAIN,
                path='/',
                secure=True,
            )

        url = f'https://assetdelivery.roblox.com/v1/asset/?id={asset_id}'
        try:
            for redirect_count in range(_MAX_REDIRECTS + 1):
                _cancelled(cancel_event)
                validate_public_https_url(url)
                if not _is_trusted_asset_url(url):
                    raise ValueError('Roblox redirected the asset preview to an untrusted host.')
                with session.get(
                    url,
                    stream=True,
                    timeout=(5, 15),
                    allow_redirects=False,
                ) as response:
                    status = int(response.status_code)
                    if status in _REDIRECT_STATUSES:
                        location = str(response.headers.get('Location') or '')
                        if not location:
                            raise ValueError('Roblox returned a redirect without a destination.')
                        if redirect_count >= _MAX_REDIRECTS:
                            raise ValueError('Roblox exceeded the asset preview redirect limit.')
                        url = urljoin(response.url or url, location)
                        continue
                    if status == 404:
                        raise ValueError('The selected Roblox asset was not found.')
                    if status == 403:
                        raise ValueError('The selected Roblox asset is private or unavailable.')
                    if status != 200:
                        raise ValueError(f'Roblox returned HTTP {status} for this asset.')
                    declared = response.headers.get('Content-Length')
                    if declared is not None:
                        try:
                            declared_size = int(declared)
                        except (TypeError, ValueError):
                            declared_size = 0
                        if declared_size > _MAX_PREVIEW_BYTES:
                            raise ValueError('The Roblox asset exceeds the 64 MB safety limit.')
                    data = bytearray()
                    for chunk in response.iter_content(chunk_size=_STREAM_CHUNK_BYTES):
                        _cancelled(cancel_event)
                        if not chunk:
                            continue
                        data.extend(chunk)
                        if len(data) > _MAX_PREVIEW_BYTES:
                            raise ValueError('The Roblox asset exceeds the 64 MB safety limit.')
                    if not data:
                        raise ValueError('Roblox returned an empty asset payload.')
                    return bytes(data)
        finally:
            session.close()
        raise ValueError('The Roblox asset could not be fetched.')


__all__ = ['CommunityValueResolver']
