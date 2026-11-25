from __future__ import annotations

import json

import pytest

from fleasion.proxy import server


def test_json_path_replacement_preserves_nested_values_and_types() -> None:
    body = b'{"items": [{"id": 4, "values": [true, null, {"name": "asset"}]}]}'
    result = server._apply_auto_replace_body_rule(  # pyright: ignore[reportPrivateUsage]
        'json_path', 'items[0].id', '42', body
    )
    assert result is not None
    assert json.loads(result) == {'items': [{'id': 42, 'values': [True, None, {'name': 'asset'}]}]}


def test_flow_response_json_accepts_deep_decoded_values_and_rejects_invalid_json() -> None:
    body = b'[' * 600 + b'0' + b']' * 600
    response = server._FlowResponse(b'HTTP/1.1 200 OK', body)  # pyright: ignore[reportPrivateUsage]
    value = response.json()
    for _ in range(600):
        assert isinstance(value, list) and len(value) == 1
        value = value[0]
    assert value == 0
    response.content = b'{invalid'
    with pytest.raises(json.JSONDecodeError):
        response.json()
