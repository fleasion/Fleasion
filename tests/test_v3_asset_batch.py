from __future__ import annotations

import json
from typing import Any

from fleasion.proxy import asset_batch

V3_REQUEST: dict[str, Any] = {
    'assets': [
        {
            'requestId': '0',
            'asset': {'assetId': 106199999945022},
            'version': {'stable': {}},
            'assetType': 'Mesh',
            'representation': {
                'priorityList': [{'format': 'fmd', 'majorVersion': '1.0.1', 'fidelity': 'ALL'}]
            },
        },
        {
            'requestId': '1',
            'asset': {'hash': '01fbb35a1b3f53173f68a2d02bb735d2'},
            'version': {'stable': {}},
            'assetType': 'RuntimePropertySet',
        },
        {
            'requestId': '2',
            'asset': {'assetId': 83617541763532},
            'version': {'stable': {}},
            'assetType': 'TexturePack',
        },
    ]
}


def _convert() -> tuple[bytes, dict[str, asset_batch.BatchItem]]:
    converted = asset_batch.v3_request_to_v1(json.dumps(V3_REQUEST).encode())
    assert converted is not None
    return converted


def test_batch_request_lines_are_detected() -> None:
    assert asset_batch.is_batch_request(b'POST /v3/assets/batch HTTP/1.1')
    assert asset_batch.is_batch_request(b'POST /v1/assets/batch HTTP/1.1')
    assert not asset_batch.is_batch_request(b'GET /v1/asset/?id=1 HTTP/1.1')
    assert asset_batch.is_v3_batch_request(b'POST /v3/assets/batch HTTP/1.1')
    assert not asset_batch.is_v3_batch_request(b'POST /v1/assets/batch HTTP/1.1')


def test_v3_request_round_trips_through_v1_shape() -> None:
    v1_body, originals = _convert()
    v1: list[dict[str, Any]] = json.loads(v1_body)
    assert [item.get('assetId') for item in v1] == [106199999945022, None, 83617541763532]
    assert all('asset' not in item for item in v1)
    assert json.loads(asset_batch.v1_request_to_v3(v1_body, originals)) == V3_REQUEST


def test_v3_request_keeps_only_items_left_in_the_v1_request() -> None:
    v1_body, originals = _convert()
    v1: list[dict[str, Any]] = json.loads(v1_body)
    kept = [item for item in v1 if item.get('assetType') != 'TexturePack']
    kept[0]['assetId'] = 42
    rebuilt: dict[str, Any] = json.loads(
        asset_batch.v1_request_to_v3(json.dumps(kept).encode(), originals)
    )
    assert [item['requestId'] for item in rebuilt['assets']] == ['0', '1']
    assert rebuilt['assets'][0]['asset'] == {'assetId': 42}
    assert rebuilt['assets'][1]['asset'] == {'hash': '01fbb35a1b3f53173f68a2d02bb735d2'}


def test_v3_response_unwraps_to_v1_list() -> None:
    item = {'requestId': '0', 'assetId': 1, 'location': 'https://example.com/a'}
    body = json.dumps({'assets': [item]}).encode()
    assert json.loads(asset_batch.v3_response_to_v1(body)) == [item]
    assert asset_batch.v3_response_to_v1(b'[]') == b'[]'


def test_non_v3_bodies_are_left_alone() -> None:
    assert asset_batch.v3_request_to_v1(b'[]') is None
    assert asset_batch.v3_request_to_v1(b'not json') is None
    assert asset_batch.v1_request_to_v3(b'{"x":1}', {}) == b'{"x":1}'
