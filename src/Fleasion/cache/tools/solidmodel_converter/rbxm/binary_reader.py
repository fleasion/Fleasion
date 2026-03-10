"""Low-level binary reading utilities for the RBXM format.

Handles zigzag encoding, byte interleaving, sign-rotated floats,
and delta-encoded instance IDs.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


def read_u8(data: bytes, offset: int) -> tuple[int, int]:
    """Read a single unsigned byte."""
    return data[offset], offset + 1


def read_u16(data: bytes, offset: int) -> tuple[int, int]:
    """Read a little-endian unsigned 16-bit integer."""
    val = struct.unpack_from('<H', data, offset)[0]
    return val, offset + 2


def read_u32(data: bytes, offset: int) -> tuple[int, int]:
    """Read a little-endian unsigned 32-bit integer."""
    val = struct.unpack_from('<I', data, offset)[0]
    return val, offset + 4


def read_i32(data: bytes, offset: int) -> tuple[int, int]:
    """Read a little-endian signed 32-bit integer."""
    val = struct.unpack_from('<i', data, offset)[0]
    return val, offset + 4


def read_f32(data: bytes, offset: int) -> tuple[float, int]:
    """Read a little-endian 32-bit float."""
    val = struct.unpack_from('<f', data, offset)[0]
    return val, offset + 4


def read_f64(data: bytes, offset: int) -> tuple[float, int]:
    """Read a little-endian 64-bit double."""
    val = struct.unpack_from('<d', data, offset)[0]
    return val, offset + 8


def read_bytes(data: bytes, offset: int, length: int) -> tuple[bytes, int]:
    """Read a fixed number of raw bytes."""
    return data[offset : offset + length], offset + length


def read_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read a length-prefixed string (uint32 length + UTF-8 bytes)."""
    length, offset = read_u32(data, offset)
    raw, offset = read_bytes(data, offset, length)
    return raw.decode('utf-8', errors='replace'), offset


def read_binary_string(data: bytes, offset: int) -> tuple[bytes, int]:
    """Read a length-prefixed binary string (uint32 length + raw bytes)."""
    length, offset = read_u32(data, offset)
    raw, offset = read_bytes(data, offset, length)
    return raw, offset


# --- Zigzag encoding/decoding ---


def decode_zigzag(value: int) -> int:
    """Decode a zigzag-encoded 32-bit integer."""
    return (value >> 1) ^ (-(value & 1))


def encode_zigzag(value: int) -> int:
    """Encode a signed 32-bit integer with zigzag encoding."""
    return (value << 1) ^ (value >> 31)


# --- Byte interleaving ---


def deinterleave_u32(data: bytes, offset: int, count: int) -> list[int]:
    """Read `count` byte-interleaved big-endian uint32 values.

    The format stores all MSBs first, then all second bytes, etc.
    Each resulting value is big-endian within its byte group.
    """
    values: list[int] = []
    total = count * 4
    block = data[offset : offset + total]
    for i in range(count):
        b0 = block[i]
        b1 = block[count + i]
        b2 = block[2 * count + i]
        b3 = block[3 * count + i]
        values.append((b0 << 24) | (b1 << 16) | (b2 << 8) | b3)
    return values


def deinterleave_i32(data: bytes, offset: int, count: int) -> list[int]:
    """Read `count` byte-interleaved zigzag-encoded int32 values."""
    raw = deinterleave_u32(data, offset, count)
    return [decode_zigzag(v) for v in raw]


def deinterleave_f32(data: bytes, offset: int, count: int) -> list[float]:
    """Read `count` byte-interleaved sign-rotated float32 values."""
    raw = deinterleave_u32(data, offset, count)
    result: list[float] = []
    for v in raw:
        # Undo sign rotation: sign bit was moved from MSB to LSB
        bits = (v >> 1) | ((v & 1) << 31)
        result.append(struct.unpack('<f', struct.pack('<I', bits))[0])
    return result


def deinterleave_i64(data: bytes, offset: int, count: int) -> list[int]:
    """Read `count` byte-interleaved zigzag-encoded int64 values."""
    values: list[int] = []
    total = count * 8
    block = data[offset : offset + total]
    for i in range(count):
        val = 0
        for byte_idx in range(8):
            val = (val << 8) | block[byte_idx * count + i]
        # Zigzag decode for 64-bit
        values.append((val >> 1) ^ (-(val & 1)))
    return values


def decode_ids(data: bytes, offset: int, count: int) -> tuple[list[int], int]:
    """Read delta-encoded + zigzag + interleaved instance IDs."""
    deltas = deinterleave_i32(data, offset, count)
    ids: list[int] = []
    acc = 0
    for d in deltas:
        acc += d
        ids.append(acc)
    return ids, offset + count * 4
