"""Translate Roblox v3 asset batch traffic to and from the v1 batch shape."""

from __future__ import annotations

import json
from typing import Any, cast

V1_BATCH_PATH = b'/v1/assets/batch'
V3_BATCH_PATH = b'/v3/assets/batch'
EMPTY_V1_RESPONSE = b'[]'
EMPTY_V3_RESPONSE = b'{"assets":[]}'

type BatchItem = dict[str, Any]


def is_batch_request(request_line: bytes) -> bool:
    """Return whether the request line targets a v1 or v3 asset batch."""
    return V1_BATCH_PATH in request_line or V3_BATCH_PATH in request_line


def is_v3_batch_request(request_line: bytes) -> bool:
    """Return whether the request line targets the v3 asset batch endpoint."""
    return V3_BATCH_PATH in request_line


def _dump(value: object) -> bytes:
    return json.dumps(value, separators=(',', ':')).encode('utf-8')


def _v3_items(body: bytes) -> list[BatchItem] | None:
    try:
        data: object = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    assets = cast('dict[str, object]', data).get('assets')
    if not isinstance(assets, list):
        return None
    return [
        cast('BatchItem', item) for item in cast('list[object]', assets) if isinstance(item, dict)
    ]


def v3_request_to_v1(body: bytes) -> tuple[bytes, dict[str, BatchItem]] | None:
    """Flatten a v3 batch request into a v1 request list.

    Returns the v1 body and the original v3 items keyed by request ID, or
    ``None`` when the body is not a v3 batch request.
    """
    items = _v3_items(body)
    if items is None:
        return None
    flattened: list[BatchItem] = []
    originals: dict[str, BatchItem] = {}
    for item in items:
        entry = {key: value for key, value in item.items() if key != 'asset'}
        asset = item.get('asset')
        if isinstance(asset, dict) and 'assetId' in asset:
            entry['assetId'] = cast('BatchItem', asset)['assetId']
        originals[str(item.get('requestId'))] = item
        flattened.append(entry)
    return _dump(flattened), originals


def v1_request_to_v3(body: bytes, originals: dict[str, BatchItem]) -> bytes:
    """Rebuild a v3 batch request from a v1 request list."""
    try:
        data: object = json.loads(body)
    except ValueError:
        return body
    if not isinstance(data, list):
        return body
    rebuilt: list[BatchItem] = []
    for raw_item in cast('list[object]', data):
        if not isinstance(raw_item, dict):
            continue
        item = cast('BatchItem', raw_item)
        original = originals.get(str(item.get('requestId')), {})
        original_asset = original.get('asset')
        asset: BatchItem = (
            dict(cast('BatchItem', original_asset)) if isinstance(original_asset, dict) else {}
        )
        entry = {key: value for key, value in item.items() if key != 'assetId'}
        if 'assetId' in item:
            asset['assetId'] = item['assetId']
        entry['asset'] = asset
        rebuilt.append(entry)
    return _dump({'assets': rebuilt})


def v3_response_to_v1(body: bytes) -> bytes:
    """Unwrap a v3 batch response into a v1 response list."""
    items = _v3_items(body)
    if items is None:
        return body
    return _dump(items)
