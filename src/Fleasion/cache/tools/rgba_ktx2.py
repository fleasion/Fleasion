"""Small KTX2 helpers for uncompressed RGBA8 textures.

Roblox accepts uncompressed ``VK_FORMAT_R8G8B8A8_UNORM`` KTX2 files for local
TexturePack replacements. Writing that simple container directly avoids the
Windows-only libktx dependency used by the original app.
"""

from __future__ import annotations

import struct
from pathlib import Path

KTX2_MAGIC = b'\xabKTX 20\xbb\r\n\x1a\n'
VK_FORMAT_R8G8B8A8_UNORM = 37

_HEADER_AND_INDEX_SIZE = 104
_DFD_RGBSDA = 1
_DFD_PRIMARIES_BT709 = 1
_DFD_TRANSFER_LINEAR = 1
_DFD_FLAGS_ALPHA_STRAIGHT = 0
_DFD_CHANNEL_RED = 0
_DFD_CHANNEL_GREEN = 1
_DFD_CHANNEL_BLUE = 2
_DFD_CHANNEL_ALPHA = 15


def write_rgba8_ktx2(rgba: bytes, width: int, height: int, out_path: Path) -> None:
    """Write tightly packed RGBA8 bytes as a single-level KTX2 file."""

    if width <= 0 or height <= 0:
        raise ValueError(f'invalid KTX2 dimensions {width}x{height}')

    rgba = bytes(rgba)
    expected_size = width * height * 4
    if len(rgba) != expected_size:
        raise ValueError(f'RGBA buffer size mismatch: {len(rgba)} != {expected_size}')

    dfd = _make_rgba8_dfd()
    dfd_offset = _HEADER_AND_INDEX_SIZE
    level_offset = dfd_offset + len(dfd)
    level_padding = b'\x00' * _padding_for(level_offset, 4)
    level_offset += len(level_padding)

    header = (
        KTX2_MAGIC
        + struct.pack(
            '<9I',
            VK_FORMAT_R8G8B8A8_UNORM,
            1,      # typeSize
            width,
            height,
            0,      # pixelDepth
            0,      # layerCount: 0 means not an array texture
            1,      # faceCount
            1,      # levelCount
            0,      # supercompressionScheme: none
        )
        + struct.pack(
            '<IIIIQQ',
            dfd_offset,
            len(dfd),
            0,      # kvdByteOffset
            0,      # kvdByteLength
            0,      # sgdByteOffset
            0,      # sgdByteLength
        )
        + struct.pack('<QQQ', level_offset, expected_size, expected_size)
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(header + dfd + level_padding + rgba)


def read_rgba8_ktx2(data: bytes) -> tuple[bytes, int, int] | None:
    """Return ``(rgba, width, height)`` for simple uncompressed RGBA8 KTX2 data."""

    if len(data) < _HEADER_AND_INDEX_SIZE or data[:12] != KTX2_MAGIC:
        return None

    try:
        (
            vk_format,
            type_size,
            width,
            height,
            depth,
            layer_count,
            face_count,
            level_count,
            supercompression,
        ) = struct.unpack_from('<9I', data, 12)
        level_offset, byte_length, uncompressed_length = struct.unpack_from('<QQQ', data, 80)
    except struct.error:
        return None

    expected_size = width * height * 4
    if (
        vk_format != VK_FORMAT_R8G8B8A8_UNORM
        or type_size != 1
        or width <= 0
        or height <= 0
        or depth != 0
        or layer_count != 0
        or face_count != 1
        or level_count not in (0, 1)
        or supercompression != 0
        or byte_length != expected_size
        or uncompressed_length != expected_size
        or level_offset < _HEADER_AND_INDEX_SIZE
        or level_offset + byte_length > len(data)
    ):
        return None

    return bytes(data[level_offset:level_offset + byte_length]), width, height


def _make_rgba8_dfd() -> bytes:
    samples = b''.join(
        _make_sample(bit_offset, channel)
        for bit_offset, channel in (
            (0, _DFD_CHANNEL_RED),
            (8, _DFD_CHANNEL_GREEN),
            (16, _DFD_CHANNEL_BLUE),
            (24, _DFD_CHANNEL_ALPHA),
        )
    )
    descriptor_block_size = 24 + len(samples)
    dfd_total_size = 4 + descriptor_block_size
    return b''.join((
        struct.pack('<I', dfd_total_size),
        struct.pack('<I', 0),  # vendorId + descriptorType
        struct.pack('<HH', 2, descriptor_block_size),
        bytes((
            _DFD_RGBSDA,
            _DFD_PRIMARIES_BT709,
            _DFD_TRANSFER_LINEAR,
            _DFD_FLAGS_ALPHA_STRAIGHT,
        )),
        bytes((0, 0, 0, 0)),  # texelBlockDimension[0-3], stored as dimension-1
        bytes((4, 0, 0, 0)),  # bytesPlane[0-3]
        bytes((0, 0, 0, 0)),  # bytesPlane[4-7]
        samples,
    ))


def _make_sample(bit_offset: int, channel_type: int) -> bytes:
    return struct.pack(
        '<HBB4BII',
        bit_offset,
        7,             # bitLength is stored as length-1
        channel_type,
        0, 0, 0, 0,    # samplePosition[0-3]
        0,             # sampleLower
        255,           # sampleUpper
    )


def _padding_for(offset: int, alignment: int) -> int:
    return (-offset) % alignment
