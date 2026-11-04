"""Asset-delivery endpoint and batch-response compatibility helpers."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from collections.abc import Mapping


def is_asset_batch_path(path: str) -> bool:
    """Recognize V1/V2 batch endpoints without substring matching."""
    return urlsplit(path).path.rstrip('/') in {'/v1/assets/batch', '/v2/assets/batch'}


_DIRECT_ASSET_ID_PATH = re.compile(r'/v[12]/assetId/([0-9]+)/?\Z')


def is_direct_asset_path(path: str) -> bool:
    """Recognize direct asset endpoints, excluding pinned versions."""
    parsed_path = urlsplit(path).path
    return parsed_path.rstrip('/') in {'/v1/asset', '/v2/asset'} or bool(
        _DIRECT_ASSET_ID_PATH.fullmatch(parsed_path)
    )


def direct_asset_id(path: str) -> str | None:
    """Get one unambiguous numeric asset ID, if a direct endpoint identifies one."""
    parsed = urlsplit(path)
    match = _DIRECT_ASSET_ID_PATH.fullmatch(parsed.path)
    if match is not None:
        if 'id' in parse_qs(parsed.query, keep_blank_values=True):
            return None
        return match.group(1)
    if parsed.path.rstrip('/') not in {'/v1/asset', '/v2/asset'}:
        return None
    values = parse_qs(parsed.query, keep_blank_values=True).get('id', [])
    if len(values) != 1 or not values[0].isascii() or not values[0].isdigit():
        return None
    return values[0]


def batch_response_locations(item: Mapping[str, object]) -> tuple[str, ...]:
    """Return URLs from V1 ``location`` or V2 ``locations`` without altering responses."""
    singular = item.get('location')
    if isinstance(singular, str) and singular:
        return (singular,)
    plural = item.get('locations')
    if not isinstance(plural, list):
        return ()
    urls: list[str] = []
    for raw_entry in cast('list[object]', plural):
        if not isinstance(raw_entry, dict):
            continue
        entry = cast('dict[str, object]', raw_entry)
        url = entry.get('location')
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return tuple(urls)
