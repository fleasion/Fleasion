"""Low-level binary writing utilities for the RBXM format.

Exact inverse of binary_reader.py — every function here undoes the
corresponding reader function.
"""

from __future__ import annotations

import struct


def write_u8(value: int) -> bytes:
    return bytes([value & 0xFF])


def write_u16(value: int) -> bytes:
    return struct.pack('<H', value)


def write_u32(value: int) -> bytes:
    return struct.pack('<I', value)


def write_i32(value: int) -> bytes:
    return struct.pack('<i', value)


def write_f32(value: float) -> bytes:
    return struct.pack('<f', value)


def write_f64(value: float) -> bytes:
    return struct.pack('<d', value)


def write_string(value: str) -> bytes:
    """Write a length-prefixed UTF-8 string."""
    encoded = value.encode('utf-8')
    return write_u32(len(encoded)) + encoded


def write_binary_string(value: bytes) -> bytes:
    """Write a length-prefixed binary blob."""
    return write_u32(len(value)) + value


# ---------------------------------------------------------------------------
# Zigzag encoding (encode_zigzag already in binary_reader; duplicated here
# so binary_writer is self-contained)
# ---------------------------------------------------------------------------


def encode_zigzag32(value: int) -> int:
    """Zigzag-encode a signed 32-bit integer."""
    return ((value << 1) ^ (value >> 31)) & 0xFFFF_FFFF


def encode_zigzag64(value: int) -> int:
    """Zigzag-encode a signed 64-bit integer."""
    return ((value << 1) ^ (value >> 63)) & 0xFFFF_FFFF_FFFF_FFFF


# ---------------------------------------------------------------------------
# Byte interleaving — inverse of deinterleave_*
# ---------------------------------------------------------------------------


def interleave_u32(values: list[int]) -> bytes:
    """Write `count` uint32 values in byte-interleaved big-endian format.

    Inverse of deinterleave_u32: stores all MSBs first, then all second
    bytes, etc.
    """
    count = len(values)
    out = bytearray(count * 4)
    for i, v in enumerate(values):
        out[i]               = (v >> 24) & 0xFF
        out[count + i]       = (v >> 16) & 0xFF
        out[2 * count + i]   = (v >> 8)  & 0xFF
        out[3 * count + i]   =  v        & 0xFF
    return bytes(out)


def interleave_i32(values: list[int]) -> bytes:
    """Zigzag-encode then interleave signed 32-bit integers."""
    return interleave_u32([encode_zigzag32(v) for v in values])


def interleave_f32(values: list[float]) -> bytes:
    """Sign-rotate then interleave 32-bit floats.

    Inverse of deinterleave_f32.  The reader does:
        bits = (raw >> 1) | ((raw & 1) << 31)
    So the writer does the reverse rotation:
        raw = ((bits & 0x7FFFFFFF) << 1) | (bits >> 31)
    """
    raw_ints: list[int] = []
    for v in values:
        bits = struct.unpack('<I', struct.pack('<f', v))[0]
        rotated = (((bits & 0x7FFFFFFF) << 1) | (bits >> 31)) & 0xFFFF_FFFF
        raw_ints.append(rotated)
    return interleave_u32(raw_ints)


def interleave_i64(values: list[int]) -> bytes:
    """Zigzag-encode then byte-interleave signed 64-bit integers.

    Inverse of deinterleave_i64.  Storage order: byte[0] (MSB) of all
    values, then byte[1] of all values, … byte[7] (LSB) of all values.
    """
    count = len(values)
    out = bytearray(count * 8)
    for i, v in enumerate(values):
        zz = encode_zigzag64(v)
        for byte_idx in range(8):
            # MSB first: byte 0 is the most significant
            out[byte_idx * count + i] = (zz >> (56 - byte_idx * 8)) & 0xFF
    return bytes(out)


def encode_ids(ids: list[int]) -> bytes:
    """Delta-encode then zigzag-interleave a list of instance IDs.

    Inverse of decode_ids from binary_reader.
    """
    deltas: list[int] = []
    prev = 0
    for v in ids:
        deltas.append(v - prev)
        prev = v
    return interleave_i32(deltas)