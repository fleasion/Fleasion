from __future__ import annotations

import gzip
import json
from typing import cast

import pytest

from fleasion.cache.cache_viewer import CacheViewerTab
from fleasion.gui import prejsons_dialog


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'])
@pytest.mark.parametrize('compressed', [False, True])
def test_cache_json_preview_retains_supported_encodings(encoding: str, compressed: bool) -> None:
    value = {'items': [{'name': 'café', 'enabled': True, 'value': None}]}
    data = json.dumps(value, ensure_ascii=False).encode(encoding)
    if compressed:
        data = gzip.compress(data)
    result = CacheViewerTab._is_json_data(  # pyright: ignore[reportPrivateUsage]
        cast('CacheViewerTab', None), data
    )
    assert result == (True, value)


def test_cache_json_preview_rejects_invalid_data() -> None:
    result = CacheViewerTab._is_json_data(  # pyright: ignore[reportPrivateUsage]
        cast('CacheViewerTab', None), b'{broken'
    )
    assert result == (False, None)


def test_json_fetch_worker_emits_decoded_values(monkeypatch: pytest.MonkeyPatch) -> None:
    body = b'{"nested": [true, null, {"id": 7}]}'

    def fetch(_url: str, *, timeout: float) -> bytes:
        del timeout
        return body

    monkeypatch.setattr(prejsons_dialog, '_http_get', fetch)
    worker = prejsons_dialog._JsonFetchWorker(  # pyright: ignore[reportPrivateUsage]
        'https://example.test/file.json'
    )
    results: list[tuple[object, str]] = []

    def receive(data: object, filename: str) -> None:
        results.append((data, filename))

    worker.done.connect(receive)
    worker.run()
    assert results == [({'nested': [True, None, {'id': 7}]}, 'file.json')]
