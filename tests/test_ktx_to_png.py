from __future__ import annotations

import io
import struct

import numpy as np
import zstandard
from PIL import Image

from fleasion.cache.tools.ktx_to_png.ktx_to_png import (
    KTX2_MAGIC,
    _decode_etc_rgb,
    _decode_etc_rgba,
    _gamma2_ycocg_to_rgba,
    convert,
)


def _make_etc2_ktx2(
    vk_format: int,
    raw_blocks: bytes,
    *,
    zstd: bool,
    color_space: str | None = None,
) -> bytes:
    payload = zstandard.ZstdCompressor().compress(raw_blocks) if zstd else raw_blocks
    supercompression = 2 if zstd else 0
    kvd = b''
    if color_space is not None:
        entry = b'colorSpace\x00' + color_space.encode('utf-8') + b'\x00'
        kvd = struct.pack('<I', len(entry)) + entry
        kvd += b'\x00' * ((-len(kvd)) % 4)
    kvd_offset = 80 + 24 if kvd else 0
    level_offset = 80 + 24 + len(kvd)
    return b''.join(
        (
            KTX2_MAGIC,
            struct.pack(
                '<9I',
                vk_format,
                1,
                4,
                4,
                0,
                0,
                1,
                1,
                supercompression,
            ),
            struct.pack('<IIIIQQ', 0, 0, kvd_offset, len(kvd), 0, 0),
            struct.pack('<QQQ', level_offset, len(payload), len(raw_blocks)),
            kvd,
            payload,
        )
    )


def _png_rgba(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as image:
        return np.array(image.convert('RGBA'), dtype=np.uint8)


def test_convert_ktx2_etc2_rgb_without_supercompression():
    raw_blocks = bytes.fromhex('0011223344556677')
    data = _make_etc2_ktx2(147, raw_blocks, zstd=False)

    png = convert(data)

    assert png is not None
    expected = _decode_etc_rgb(raw_blocks, 4, 4, punchthrough=False)
    assert np.array_equal(_png_rgba(png), expected)


def test_convert_ktx2_etc2_rgba_with_zstd_supercompression():
    raw_blocks = bytes.fromhex('00112233445566778899aabbccddeeff')
    data = _make_etc2_ktx2(151, raw_blocks, zstd=True)

    png = convert(data)

    assert png is not None
    expected = _decode_etc_rgba(raw_blocks, 4, 4)
    assert np.array_equal(_png_rgba(png), expected)


def test_convert_ktx2_etc2_undoes_roblox_gamma2_ycocg_layout():
    raw_blocks = bytes.fromhex('0011223344556677')
    data = _make_etc2_ktx2(
        147,
        raw_blocks,
        zstd=True,
        color_space='Gamma2YCoCg',
    )

    png = convert(data)

    assert png is not None
    encoded = _decode_etc_rgb(raw_blocks, 4, 4, punchthrough=False)
    expected = _gamma2_ycocg_to_rgba(encoded)
    assert np.array_equal(_png_rgba(png), expected)


def test_convert_ktx2_etc2_rejects_wrong_uncompressed_length():
    data = bytearray(_make_etc2_ktx2(147, bytes(8), zstd=True))
    struct.pack_into('<Q', data, 80 + 16, 16)

    assert convert(bytes(data)) is None
