"""V3 envelopes preserve request metadata and refuse ambiguous conversions."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.addons.cache_scraper import CacheScraper
from fleasion.proxy.asset_batch import (
    decode_v3_request,
    decode_v3_response,
    encode_v3_request,
    encode_v3_response,
)

if TYPE_CHECKING:
    from fleasion.cache.cache_manager import CacheManager


def _request() -> dict[str, object]:
    return {
        'assets': [
            {
                'requestId': '0',
                'asset': {'assetId': 111, 'access': {'assetVersion': 2}},
                'version': {'stable': {}},
                'assetType': 'Mesh',
                'insertContext': {'serverPlaceId': 123},
            },
            {
                'requestId': '1',
                'asset': {'hash': 'abcd1234'},
                'version': {'stable': {}},
                'assetType': 'RuntimePropertySet',
            },
            {
                'requestId': '2',
                'asset': {'assetId': 333},
                'assetType': 'TexturePack',
            },
        ],
        'batchMetadata': {'nonce': 'keep-me'},
    }


def test_round_trip_with_hashes_and_metadata() -> None:
    original = _request()
    converted = decode_v3_request(json.dumps(original).encode())
    assert converted is not None
    flattened, ctx = converted
    entries = cast('list[dict[str, object]]', json.loads(flattened))
    assert [x.get('assetId') for x in entries] == [111, None, 333]
    rebuilt = encode_v3_request(flattened, ctx)
    assert rebuilt is not None
    assert json.loads(rebuilt) == original
    entries[0]['assetId'] = 222
    entries.pop()
    changed = encode_v3_request(json.dumps(entries).encode(), ctx)
    assert changed is not None
    data = cast('dict[str, object]', json.loads(changed))
    assert data['batchMetadata'] == {'nonce': 'keep-me'}
    remaining = cast('list[dict[str, object]]', data['assets'])
    assert len(remaining) == 2
    assert remaining[0]['asset'] == {'assetId': 222, 'access': {'assetVersion': 2}}
    assert remaining[1]['asset'] == {'hash': 'abcd1234'}


def test_response_uses_original_v3_envelope() -> None:
    raw = {'assets': [{'requestId': '0', 'location': 'https://fts.rbxcdn.com/hash'}], 'next': 44}
    converted = decode_v3_response(json.dumps(raw).encode())
    assert converted is not None
    plain, envelope = converted
    entries = cast('list[dict[str, object]]', json.loads(plain))
    entries[0]['location'] = 'https://fts.rbxcdn.com/opaque-route'
    updated = encode_v3_response(json.dumps(entries).encode(), envelope)
    assert updated is not None
    assert json.loads(updated) == {
        'assets': [{'requestId': '0', 'location': 'https://fts.rbxcdn.com/opaque-route'}],
        'next': 44,
    }


@pytest.mark.parametrize('payload', [
    b'[]', b'not json', b'{"assets":{}}',
    b'{"assets":[null]}', b'{"assets":[{"requestId":"0","asset":{}} , {"requestId":0,"asset":{}}]}',
    b'{"assets":[{"requestId":"0","asset":42}]}',
])
def test_malformed_or_ambiguous_request_fails_closed(payload: bytes) -> None:
    assert decode_v3_request(payload) is None


def test_rebuild_rejects_unknown_or_repeated_items() -> None:
    converted = decode_v3_request(json.dumps(_request()).encode())
    assert converted is not None
    _, context = converted
    assert encode_v3_request(b'[{"requestId":"unmatched","assetId":99}]', context) is None
    assert encode_v3_request(b'[{"requestId":"0"},{"requestId":"0"}]', context) is None
    assert decode_v3_response(b'{"assets":[null]}') is None
    assert encode_v3_response(b'{"assets":[]}', {'assets': []}) is None


def test_v3_scraper_receives_flattened_ids_and_cdn_locations() -> None:
    request = {
        'assets': [
            {'requestId': '0', 'asset': {'assetId': 111}, 'assetType': 'Mesh'},
            {'requestId': '1', 'asset': {'assetId': 222}, 'assetType': 'Mesh'},
        ]
    }
    response = {
        'assets': [
            {'requestId': '0', 'assetTypeId': 4,
             'locations': [{'assetFormat': 'source', 'location': 'https://fts.rbxcdn.com/a?token=x'}]},
            {'requestId': '1', 'assetTypeId': 4,
             'locations': [{'assetFormat': 'source', 'location': 'https://fts.rbxcdn.com/b?token=y'}]},
        ]
    }
    converted_request = decode_v3_request(json.dumps(request).encode())
    converted_response = decode_v3_response(json.dumps(response).encode())
    assert converted_request is not None and converted_response is not None
    scraper = CacheScraper(cast('CacheManager', SimpleNamespace()))
    try:
        scraper.enabled = True
        scraper.process_batch_response(converted_request[0], converted_response[0])
        assert set(scraper.cache_logs) == {111, 222}
        paths = cast('dict[str, list[int]]', getattr(scraper, '_url_to_asset'))
        assert paths['https://fts.rbxcdn.com/a'] == [111]
        assert paths['https://fts.rbxcdn.com/b'] == [222]
    finally:
        getattr(scraper, '_executor').shutdown(wait=True, cancel_futures=True)
