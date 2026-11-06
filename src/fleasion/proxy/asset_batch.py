"""Lossless envelope conversion for Roblox's V3 asset-delivery batches."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True)
class V3BatchRequest:
    """Validated V3 request with its original per-asset metadata."""

    envelope: dict[str, object]
    by_request_id: dict[str, dict[str, object]]


def _dump(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


def _items(body: bytes) -> tuple[dict[str, object], list[dict[str, object]]] | None:
    try:
        decoded: object = json.loads(body)
    except ValueError, UnicodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    envelope = cast('dict[str, object]', decoded)
    assets = envelope.get('assets')
    if not isinstance(assets, list):
        return None
    raw_assets = cast('list[object]', assets)
    if not all(isinstance(value, dict) for value in raw_assets):
        return None
    entries = [cast('dict[str, object]', value) for value in raw_assets]
    return envelope, entries


def decode_v3_request(body: bytes) -> tuple[bytes, V3BatchRequest] | None:
    """Flatten V3 requests for existing V1/V2 replacer and scraper hooks.

    Fail closed for ambiguous request IDs, rather than associating replaced
    assets with the wrong metadata or changing unrelated hash-backed entries
    """
    decoded = _items(body)
    if decoded is None:
        return None
    envelope, entries = decoded
    flattened: list[dict[str, object]] = []
    originals: dict[str, dict[str, object]] = {}
    for entry in entries:
        request_id = entry.get('requestId')
        asset = entry.get('asset')
        if not isinstance(request_id, str | int) or isinstance(request_id, bool):
            return None
        if not isinstance(asset, dict):
            return None
        key = str(request_id)
        if key in originals:
            return None
        originals[key] = entry
        item = {k: v for k, v in entry.items() if k != 'asset'}
        asset_value = cast('dict[str, object]', asset)
        if 'assetId' in asset_value:
            item['assetId'] = asset_value['assetId']
        flattened.append(item)
    return _dump(flattened), V3BatchRequest(envelope, originals)


def encode_v3_request(  # ruff: ignore[too-many-return-statements]
    body: bytes, original: V3BatchRequest
) -> bytes | None:
    """Rebuild nested asset IDs without discarding unknown V3 envelope fields."""
    try:
        decoded: object = json.loads(body)
    except ValueError, UnicodeError:
        return None
    if not isinstance(decoded, list):
        return None
    entries: list[dict[str, object]] = []
    seen: set[str] = set()
    for raw_item in cast('list[object]', decoded):
        if not isinstance(raw_item, dict):
            return None
        item = cast('dict[str, object]', raw_item)
        request_id = item.get('requestId')
        if not isinstance(request_id, str | int) or isinstance(request_id, bool):
            return None
        key = str(request_id)
        if key in seen or key not in original.by_request_id:
            return None
        seen.add(key)
        previous = original.by_request_id[key]
        previous_asset = previous.get('asset')
        if not isinstance(previous_asset, dict):
            return None
        asset = dict(cast('dict[str, object]', previous_asset))
        if 'assetId' in item:
            asset['assetId'] = item['assetId']
        rebuilt = {k: v for k, v in item.items() if k != 'assetId'}
        rebuilt['asset'] = asset
        entries.append(rebuilt)
    envelope = dict(original.envelope)
    envelope['assets'] = entries
    return _dump(envelope)


def decode_v3_response(body: bytes) -> tuple[bytes, dict[str, object]] | None:
    """Expose V3 result entries while retaining response envelope metadata."""
    decoded = _items(body)
    if decoded is None:
        return None
    envelope, entries = decoded
    return _dump(entries), envelope


def encode_v3_response(body: bytes, envelope: dict[str, object]) -> bytes | None:
    """Restore the V3 response schema after replacement URLs are rewritten."""
    try:
        decoded: object = json.loads(body)
    except ValueError, UnicodeError:
        return None
    if not isinstance(decoded, list) or not all(
        isinstance(entry, dict) for entry in cast('list[object]', decoded)
    ):
        return None
    response = dict(envelope)
    response['assets'] = decoded
    return _dump(response)
