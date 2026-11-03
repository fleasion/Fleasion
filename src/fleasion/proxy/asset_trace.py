"""Opt-in asset-delivery diagnostics without URLs, credentials, or payload bodies."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit

from fleasion.utils import log_buffer

if TYPE_CHECKING:
    from fleasion.config.types import ReplacementMaps

_ASSET_HOST = 'assetdelivery.roblox.com'
_CDN_HOSTS = frozenset({'fts.rbxcdn.com', 'contentdelivery.roblox.com'})
_RECENT_CDN_LIMIT = 8192
_RECENT_CDN_TTL_SECONDS = 15 * 60


class AssetTrace:
    """Record asset requests and correlate one-shot CDN routes across connections."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = (
            os.environ.get('FLEASION_ASSET_TRACE', '').strip().lower() in {'1', 'true', 'yes'}
            if enabled is None
            else enabled
        )
        self._start = time.monotonic()
        self._recent_cdn: OrderedDict[str, tuple[float, str]] = OrderedDict()
        if self.enabled:
            self._emit('started schema=1 cdn_paths=hashed query_strings=omitted')

    def _emit(self, message: str) -> None:
        if self.enabled:
            elapsed_ms = int((time.monotonic() - self._start) * 1000)
            log_buffer.log('AssetTrace', f't_ms={elapsed_ms} {message}')

    @staticmethod
    def _path_key(host: str, path: str) -> str:
        base_path = path.split('?', 1)[0].split('#', 1)[0]
        data = f'{host.lower()}:{base_path}'.encode('utf-8', errors='replace')
        return hashlib.blake2s(data, digest_size=8).hexdigest()

    def request(
        self,
        host: str,
        method: str,
        path: str,
        replacements: ReplacementMaps,
        *,
        rewritten: bool = False,
    ) -> None:
        """Trace the endpoint and identify exact-ID replacements bypassed by direct requests."""
        if not self.enabled:
            return
        if host in _CDN_HOSTS:
            return
        if host != _ASSET_HOST:
            return
        parsed = urlsplit(path)
        if parsed.path.rstrip('/') == '/v1/asset':
            ids = parse_qs(parsed.query).get('id', [])
            asset_id = (
                int(ids[0]) if len(ids) == 1 and ids[0].isascii() and ids[0].isdigit() else None
            )
            maps = (replacements[0], replacements[2], replacements[3])
            has_replacement = asset_id is not None and any(
                asset_id in mapping or str(asset_id) in mapping for mapping in maps
            )
            has_removal = asset_id is not None and (
                asset_id in replacements[1] or str(asset_id) in replacements[1]
            )
            self._emit(
                f'http_request host=assetdelivery kind=direct method={method} '
                f'asset_id={asset_id if asset_id is not None else "unknown"} '
                f'exact_replacement={has_replacement} '
                f'replacement_bypassed={has_replacement and not rewritten} '
                f'removal_bypassed={has_removal} '
                f'asset_id_rewritten={rewritten}'
            )
        else:
            kind = 'batch' if parsed.path == '/v1/assets/batch' else 'other'
            self._emit(
                f'http_request host=assetdelivery kind={kind} method={method} '
                f'path_key={self._path_key(host, path)}'
            )

    def batch(self, original: bytes, modified: bytes, replacements: ReplacementMaps) -> None:
        """Trace matched asset IDs and ID swaps without retaining request JSON."""
        if not self.enabled:
            return
        try:
            before, after = json.loads(original), json.loads(modified)
        except UnicodeError, ValueError, TypeError:
            self._emit('batch_decode_failed')
            return
        if not isinstance(before, list) or not isinstance(after, list):
            self._emit('batch_non_array')
            return
        before_items = cast('list[object]', before)
        after_items = cast('list[object]', after)
        all_keys = (
            set(replacements[0]) | replacements[1] | set(replacements[2]) | set(replacements[3])
        )
        targets: list[str] = []
        for entry in before_items:
            if not isinstance(entry, dict):
                continue
            aid = cast('dict[str, object]', entry).get('assetId')
            if not isinstance(aid, int | str) or isinstance(aid, bool):
                continue
            normalized_id = int(aid) if str(aid).isascii() and str(aid).isdigit() else aid
            if (
                (aid in all_keys or normalized_id in all_keys)
                and str(aid).isascii()
                and str(aid).isdigit()
            ):
                targets.append(str(aid))
        original_ids = [
            cast('dict[str, object]', entry).get('assetId')
            for entry in before_items
            if isinstance(entry, dict)
        ]
        modified_ids = [
            cast('dict[str, object]', entry).get('assetId')
            for entry in after_items
            if isinstance(entry, dict)
        ]
        swaps = sum(a != b for a, b in zip(original_ids, modified_ids, strict=False))
        self._emit(
            f'batch_request items={len(before_items)} forwarded_items={len(after_items)} '
            f'id_swaps={swaps} target_count={len(targets)} '
            f'target_ids={",".join(targets[:24]) if targets else "none"}'
        )

    def cdn(self, host: str, path: str, action: str | None) -> None:
        """Report CDN coverage, including later misses after a consumed replacement route."""
        if not self.enabled or host not in _CDN_HOSTS:
            return
        now = time.monotonic()
        key = self._path_key(host, path)
        previous = self._recent_cdn.get(key)
        if previous is not None and now - previous[0] > _RECENT_CDN_TTL_SECONDS:
            self._recent_cdn.pop(key, None)
            previous = None
        if action is not None:
            self._recent_cdn[key] = (now, action)
            self._recent_cdn.move_to_end(key)
            if len(self._recent_cdn) > _RECENT_CDN_LIMIT:
                self._recent_cdn.popitem(last=False)
        self._emit(
            f'cdn_request host={host} path_key={key} '
            f'route={action if action is not None else "upstream"} '
            f'previously_replaced={previous is not None} '
            f'prior_route={previous[1] if previous is not None else "none"} '
            f'age_ms={int((now - previous[0]) * 1000) if previous is not None else -1}'
        )

    def response(
        self,
        host: str,
        path: str,
        status: int,
        body_length: int,
        *,
        location: bytes | None = None,
    ) -> None:
        """Trace response status and destination fingerprints, never signed URLs."""
        if not self.enabled or (host != _ASSET_HOST and host not in _CDN_HOSTS):
            return
        redirect = ''
        if location and 300 <= status < 400:
            try:
                destination = urlsplit(location.decode('ascii'))
            except UnicodeError, ValueError:
                destination = None
            if (
                destination is not None
                and destination.scheme == 'https'
                and destination.hostname in _CDN_HOSTS
            ):
                redirect = (
                    f' redirect_host={destination.hostname} '
                    f'redirect_path_key={self._path_key(destination.hostname, destination.path)}'
                )
        self._emit(
            f'http_response host={host} path_key={self._path_key(host, path)} '
            f'status={status} body_bytes={body_length}{redirect}'
        )

    def splice(self, host: str, phase: str, *, detail: str = '') -> None:
        """Correlate reconnects with asset requests around TLS disruption events."""
        if not self.enabled:
            return
        self._emit(f'splice host={host} phase={phase} {detail}'.rstrip())
