"""Small KTX2 helpers for uncompressed RGBA8 textures.

Roblox accepts uncompressed ``VK_FORMAT_R8G8B8A8_UNORM`` KTX2 files for local
TexturePack replacements. Writing that simple container directly avoids the
Windows-only libktx dependency used by the original app.

Mip generation intentionally mirrors the behavior observed in Roblox texture
packs: color maps tagged ``Gamma2RGB`` are filtered in gamma-2 linear space,
normal maps are bilinearly filtered as vectors and renormalized, and packed
linear maps such as ORM/height are area-filtered channel-by-channel.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Literal

import numpy as np
from PIL import Image

KTX2_MAGIC = b'\xabKTX 20\xbb\r\n\x1a\n'
VK_FORMAT_R8G8B8A8_UNORM = 37

MipmapMode = Literal['color', 'linear', 'normal']
RGBA8_KTX2_CACHE_VERSION = b'rgba8-mips-v3-normal-base-renorm'

_KTX2_HEADER_SIZE = 80
_DFD_RGBSDA = 1
_DFD_PRIMARIES_BT709 = 1
_DFD_TRANSFER_LINEAR = 1
_DFD_FLAGS_ALPHA_STRAIGHT = 0
_DFD_CHANNEL_RED = 0
_DFD_CHANNEL_GREEN = 1
_DFD_CHANNEL_BLUE = 2
_DFD_CHANNEL_ALPHA = 15
_KTX_WRITER_VALUE = b'KTXwriter\x00Unidentified app / libktx v4.4.2-10-gac2edf19\x00'


def mipmap_mode_for_texture_name(name: str) -> MipmapMode:
    """Infer whether a texture path should use color or linear mip filtering."""

    lowered = name.lower().replace('\\', '/')
    if 'normal' in lowered:
        return 'normal'
    linear_markers = ('roughness', 'metalness', 'specular', '/orm', '_orm', 'heightmap')
    if any(marker in lowered for marker in linear_markers):
        return 'linear'
    return 'color'


def generate_rgba8_mip_chain(
    rgba: bytes,
    width: int,
    height: int,
    *,
    mipmap_mode: MipmapMode = 'color',
) -> list[bytes]:
    """Generate a complete RGBA8 mip chain, including the supplied base level.

    ``color`` uses sequential bilinear filtering after decoding RGB with
    ``linear = encoded**2`` and encodes with the inverse square root. Alpha
    remains a straight, linear channel. ``linear`` uses sequential area/BOX
    averaging per channel. ``normal`` resamples each mip directly from the base
    level in signed-vector space, renormalizes each pixel, and bilinearly filters
    alpha. That direct-from-base normal path matches current Roblox TexturePack
    captures more closely than recursively filtering already-generated normals.
    """

    if width <= 0 or height <= 0:
        raise ValueError(f'invalid KTX2 dimensions {width}x{height}')
    if mipmap_mode not in ('color', 'linear', 'normal'):
        raise ValueError(f'unsupported mipmap mode: {mipmap_mode}')

    rgba = bytes(rgba)
    expected_size = width * height * 4
    if len(rgba) != expected_size:
        raise ValueError(f'RGBA buffer size mismatch: {len(rgba)} != {expected_size}')

    current = np.frombuffer(rgba, dtype=np.uint8).reshape((height, width, 4))
    base = current
    levels = [rgba]
    current_width = width
    current_height = height

    while current_width > 1 or current_height > 1:
        next_width = max(1, current_width // 2)
        next_height = max(1, current_height // 2)
        if mipmap_mode == 'color':
            current = _downsample_gamma2_color(current, next_width, next_height)
        elif mipmap_mode == 'normal':
            current = _downsample_normal(base, next_width, next_height)
        else:
            current = _downsample_linear(current, next_width, next_height)
        levels.append(current.tobytes())
        current_width = next_width
        current_height = next_height

    return levels


def write_rgba8_ktx2(
    rgba: bytes,
    width: int,
    height: int,
    out_path: Path,
    *,
    mipmap_mode: MipmapMode = 'color',
) -> None:
    """Write tightly packed RGBA8 bytes as a full-mip KTX2 file."""

    levels = generate_rgba8_mip_chain(
        rgba,
        width,
        height,
        mipmap_mode=mipmap_mode,
    )
    write_rgba8_ktx2_levels(levels, width, height, out_path)


def write_rgba8_ktx2_levels(
    levels: list[bytes] | tuple[bytes, ...],
    width: int,
    height: int,
    out_path: Path,
) -> None:
    """Write an explicitly supplied RGBA8 mip chain without resampling it."""

    if width <= 0 or height <= 0:
        raise ValueError(f'invalid KTX2 dimensions {width}x{height}')
    if not levels:
        raise ValueError('KTX2 requires at least one mip level')

    level_bytes = [bytes(level) for level in levels]
    max_level_count = _full_mip_level_count(width, height)
    if len(level_bytes) > max_level_count:
        raise ValueError(
            f'too many KTX2 mip levels: {len(level_bytes)} > {max_level_count} for {width}x{height}'
        )

    for level_index, level in enumerate(level_bytes):
        level_width = max(1, width >> level_index)
        level_height = max(1, height >> level_index)
        expected_size = level_width * level_height * 4
        if len(level) != expected_size:
            raise ValueError(
                f'RGBA mip {level_index} size mismatch: {len(level)} != {expected_size} '
                f'({level_width}x{level_height})'
            )

    level_count = len(level_bytes)
    level_index_size = 24 * level_count
    dfd = _make_rgba8_dfd()
    dfd_offset = _KTX2_HEADER_SIZE + level_index_size
    kvd = _make_kvd()
    kvd_offset = dfd_offset + len(dfd)

    data_start = kvd_offset + len(kvd)
    metadata_padding = b'\x00' * _padding_for(data_start, 4)
    cursor = data_start + len(metadata_padding)

    level_offsets = [0] * level_count
    physical_parts: list[bytes] = []
    # KTX2 stores mip payloads from the smallest level to the base level. The
    # level index itself remains ordered base -> smallest and points at them.
    for level_index in range(level_count - 1, -1, -1):
        padding = b'\x00' * _padding_for(cursor, 4)
        if padding:
            physical_parts.append(padding)
            cursor += len(padding)
        level_offsets[level_index] = cursor
        physical_parts.append(level_bytes[level_index])
        cursor += len(level_bytes[level_index])

    header = (
        KTX2_MAGIC
        + struct.pack(
            '<9I',
            VK_FORMAT_R8G8B8A8_UNORM,
            1,  # typeSize
            width,
            height,
            0,  # pixelDepth
            0,  # layerCount: 0 means not an array texture
            1,  # faceCount
            level_count,
            0,  # supercompressionScheme: none
        )
        + struct.pack(
            '<IIIIQQ',
            dfd_offset,
            len(dfd),
            kvd_offset,
            len(kvd),
            0,  # sgdByteOffset
            0,  # sgdByteLength
        )
        + b''.join(
            struct.pack('<QQQ', offset, len(level), len(level))
            for offset, level in zip(level_offsets, level_bytes, strict=True)
        )
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(header + dfd + kvd + metadata_padding + b''.join(physical_parts))


def read_rgba8_ktx2_levels(data: bytes) -> tuple[list[bytes], int, int] | None:
    """Return ``(levels, width, height)`` for uncompressed RGBA8 KTX2 data."""

    if len(data) < _KTX2_HEADER_SIZE + 24 or data[:12] != KTX2_MAGIC:
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
    except struct.error:
        return None

    # Retain compatibility with the previous reader's treatment of a zero
    # levelCount as a single base level.
    effective_level_count = level_count or 1
    level_index_end = _KTX2_HEADER_SIZE + 24 * effective_level_count
    if (
        vk_format != VK_FORMAT_R8G8B8A8_UNORM
        or type_size != 1
        or width <= 0
        or height <= 0
        or depth != 0
        or layer_count != 0
        or face_count != 1
        or effective_level_count > _full_mip_level_count(width, height)
        or supercompression != 0
        or level_index_end > len(data)
    ):
        return None

    levels: list[bytes] = []
    for level_index in range(effective_level_count):
        try:
            level_offset, byte_length, uncompressed_length = struct.unpack_from(
                '<QQQ', data, _KTX2_HEADER_SIZE + 24 * level_index
            )
        except struct.error:
            return None

        level_width = max(1, width >> level_index)
        level_height = max(1, height >> level_index)
        expected_size = level_width * level_height * 4
        if (
            byte_length != expected_size
            or uncompressed_length != expected_size
            or level_offset < level_index_end
            or level_offset + byte_length > len(data)
        ):
            return None
        levels.append(bytes(data[level_offset : level_offset + byte_length]))

    return levels, width, height


def read_rgba8_ktx2(data: bytes) -> tuple[bytes, int, int] | None:
    """Return base-level ``(rgba, width, height)`` for uncompressed RGBA8 KTX2 data."""

    parsed = read_rgba8_ktx2_levels(data)
    if parsed is None:
        return None
    levels, width, height = parsed
    return levels[0], width, height


def _resize_float_channel(
    values: np.ndarray,
    width: int,
    height: int,
    resampling: Image.Resampling,
) -> np.ndarray:
    image = Image.fromarray(values.astype(np.float32, copy=False), 'F')
    return np.array(image.resize((width, height), resampling), dtype=np.float32, copy=True)


def _round_u8(values: np.ndarray) -> np.ndarray:
    np.rint(values, out=values)
    np.clip(values, 0, 255, out=values)
    return values.astype(np.uint8)


def _downsample_gamma2_color(rgba: np.ndarray, width: int, height: int) -> np.ndarray:
    output = np.empty((height, width, 4), dtype=np.uint8)
    for channel_index in range(3):
        channel = rgba[:, :, channel_index].astype(np.float32)
        channel /= 255.0
        np.square(channel, out=channel)
        resized = _resize_float_channel(
            channel,
            width,
            height,
            Image.Resampling.BILINEAR,
        )
        np.sqrt(np.clip(resized, 0.0, 1.0), out=resized)
        resized *= 255.0
        output[:, :, channel_index] = _round_u8(resized)

    alpha = rgba[:, :, 3].astype(np.float32)
    resized_alpha = _resize_float_channel(
        alpha,
        width,
        height,
        Image.Resampling.BILINEAR,
    )
    output[:, :, 3] = _round_u8(resized_alpha)
    return output


def _downsample_normal(rgba: np.ndarray, width: int, height: int) -> np.ndarray:
    """Bilinearly filter a tangent-space RGB normal map and renormalize it."""

    components: list[np.ndarray] = []
    for channel_index in range(3):
        channel = rgba[:, :, channel_index].astype(np.float32)
        channel /= 127.5
        channel -= 1.0
        components.append(
            _resize_float_channel(
                channel,
                width,
                height,
                Image.Resampling.BILINEAR,
            )
        )

    vectors = np.stack(components, axis=2)
    lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
    np.divide(
        vectors,
        lengths,
        out=vectors,
        where=lengths > np.finfo(np.float32).eps,
    )
    vectors += 1.0
    vectors *= 127.5

    output = np.empty((height, width, 4), dtype=np.uint8)
    for channel_index in range(3):
        output[:, :, channel_index] = _round_u8(vectors[:, :, channel_index])

    alpha = rgba[:, :, 3].astype(np.float32)
    resized_alpha = _resize_float_channel(
        alpha,
        width,
        height,
        Image.Resampling.BILINEAR,
    )
    output[:, :, 3] = _round_u8(resized_alpha)
    return output


def _downsample_linear(rgba: np.ndarray, width: int, height: int) -> np.ndarray:
    output = np.empty((height, width, 4), dtype=np.uint8)
    for channel_index in range(4):
        resized = _resize_float_channel(
            rgba[:, :, channel_index],
            width,
            height,
            Image.Resampling.BOX,
        )
        output[:, :, channel_index] = _round_u8(resized)
    return output


def _full_mip_level_count(width: int, height: int) -> int:
    return max(width, height).bit_length()


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
    return b''.join(
        (
            struct.pack('<I', dfd_total_size),
            struct.pack('<I', 0),  # vendorId + descriptorType
            struct.pack('<HH', 2, descriptor_block_size),
            bytes(
                (
                    _DFD_RGBSDA,
                    _DFD_PRIMARIES_BT709,
                    _DFD_TRANSFER_LINEAR,
                    _DFD_FLAGS_ALPHA_STRAIGHT,
                )
            ),
            bytes((0, 0, 0, 0)),  # texelBlockDimension[0-3], stored as dimension-1
            bytes((4, 0, 0, 0)),  # bytesPlane[0-3]
            bytes((0, 0, 0, 0)),  # bytesPlane[4-7]
            samples,
        )
    )


def _make_sample(bit_offset: int, channel_type: int) -> bytes:
    return struct.pack(
        '<HBB4BII',
        bit_offset,
        7,  # bitLength is stored as length-1
        channel_type,
        0,
        0,
        0,
        0,  # samplePosition[0-3]
        0,  # sampleLower
        255,  # sampleUpper
    )


def _make_kvd() -> bytes:
    entry = struct.pack('<I', len(_KTX_WRITER_VALUE)) + _KTX_WRITER_VALUE
    return entry + (b'\x00' * _padding_for(len(entry), 4))


def _padding_for(offset: int, alignment: int) -> int:
    return (-offset) % alignment
