from __future__ import annotations

from compression.zstd import ZstdCompressor, ZstdDict, compress
from typing import TYPE_CHECKING

import pytest

from fleasion.utils.zstd import ZstdError, zstd_decompress_frame

if TYPE_CHECKING:
    from collections.abc import Callable

_PLAIN = bytes(range(256)) * 200


def _frame_without_content_size(data: bytes) -> bytes:
    # A streamed frame never learns its size up front, so the header omits it
    compressor = ZstdCompressor()
    return compressor.compress(data) + compressor.flush()


def test_decompress_frame_accepts_exact_size_limit() -> None:
    assert zstd_decompress_frame(compress(_PLAIN), max_size=len(_PLAIN)) == _PLAIN


@pytest.mark.parametrize('frame_builder', [compress, _frame_without_content_size])
def test_decompress_frame_rejects_output_past_limit(
    frame_builder: Callable[[bytes], bytes],
) -> None:
    frame = frame_builder(_PLAIN)

    with pytest.raises(ZstdError):
        zstd_decompress_frame(frame, max_size=len(_PLAIN) - 1)


def test_decompress_frame_reads_frames_without_content_size() -> None:
    frame = _frame_without_content_size(_PLAIN)

    assert zstd_decompress_frame(frame, max_size=1 << 20) == _PLAIN


def test_decompress_frame_ignores_bytes_after_first_frame() -> None:
    frame = compress(_PLAIN) + compress(b'second frame') + b'trailing junk'

    assert zstd_decompress_frame(frame, max_size=1 << 20) == _PLAIN


def test_decompress_frame_rejects_truncated_frame() -> None:
    with pytest.raises(ZstdError):
        zstd_decompress_frame(compress(_PLAIN)[:-8], max_size=1 << 20)


def test_raw_dictionary_round_trip() -> None:
    dictionary = b'"FFlagExample": "True", "DFIntExample": "10", ' * 40
    body = b'{"FFlagExample": "False", "DFIntExample": "20"}'

    frame = compress(body, zstd_dict=ZstdDict(dictionary, is_raw=True))

    assert zstd_decompress_frame(frame, max_size=1 << 20, raw_dictionary=dictionary) == body
    with pytest.raises(ZstdError):
        zstd_decompress_frame(frame, max_size=1 << 20)
