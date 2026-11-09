from __future__ import annotations

import tracemalloc

import pytest

from fleasion.proxy import server


@pytest.mark.parametrize('header_size', [0, 100, 512 * 1024, 512 * 1024 + 1])
def test_request_preview_preserves_truncated_bytes_with_bounded_allocation(
    header_size: int,
) -> None:
    limit = 512 * 1024
    header = b'H' * header_size
    body = b'B' * (16 * 1024 * 1024)
    expected = (header + body)[:limit]
    tracemalloc.start()
    try:
        actual = server._capture_request_preview(header, body)  # pyright: ignore[reportPrivateUsage]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert actual == expected
    assert peak < 1024 * 1024
