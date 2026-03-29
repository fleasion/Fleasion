"""KTX/KTX2 -> PNG conversion pipeline.

KTX1:  ETC1 / ETC2 / EAC decoded by a pure-Python numpy decoder.
KTX2:  BasisU and UASTC super-compressed formats transcoded to raw RGBA32
       via ktx.dll (libktx), then written to PNG via Pillow.
       Non-basis formats (raw BC7 etc.) return None so the caller can fall
       back to the Roblox API.

Both paths return PNG bytes on success, or None on failure / unsupported format.
Never raises -- all exceptions are caught internally.

Credits
-------
The ETC1/ETC2/EAC decompression algorithm is a Python port of the C#
implementation in BloxDump by EmK530:
  https://github.com/EmK530/BloxDump
ktx.dll (libktx) is redistributed from the same project under its original
license.
"""

import ctypes
import io
import logging
import os
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

# -------------------------------------------------------------------------------
# Magic bytes
# -------------------------------------------------------------------------------
KTX1_MAGIC = b'\xabKTX 11\xbb\r\n\x1a\n'
KTX2_MAGIC = b'\xabKTX 20\xbb\r\n\x1a\n'

# -------------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------------
def convert(data: bytes) -> bytes | None:
    """Convert KTX1 or KTX2 *data* to PNG bytes.

    Returns PNG bytes on success, or ``None`` if the format is unsupported or
    an error occurs.  Never raises.
    """
    try:
        if data[:12] == KTX1_MAGIC:
            return _convert_ktx1(data)
        if data[:12] == KTX2_MAGIC:
            return _convert_ktx2(data)
        logger.debug('ktx_to_png: unrecognised magic bytes')
        return None
    except Exception as exc:
        logger.debug('ktx_to_png: conversion failed: %s', exc)
        return None


# -------------------------------------------------------------------------------
# KTX1 -- header parsing + ETC/EAC dispatch
# -------------------------------------------------------------------------------
# GL internal format constants (from KTX1/Main.cs KtxTextureFormat enum)
_GL_RGB8_ETC1                        = 0x8D64
_GL_R11_EAC                          = 0x9270
_GL_SIGNED_R11_EAC                   = 0x9271
_GL_RG11_EAC                         = 0x9272
_GL_SIGNED_RG11_EAC                  = 0x9273
_GL_RGB8_ETC2                        = 0x9274
_GL_SRGB8_ETC2                       = 0x9275
_GL_RGB8_PUNCHTHROUGH_ALPHA1_ETC2    = 0x9276
_GL_SRGB8_PUNCHTHROUGH_ALPHA1_ETC2  = 0x9277
_GL_RGBA8_ETC2_EAC                   = 0x9278
_GL_SRGB8_ALPHA8_ETC2_EAC           = 0x9279


def _ceil_mul(value: int, multiplier: int) -> int:
    return ((value + multiplier - 1) // multiplier) * multiplier


def _convert_ktx1(data: bytes) -> bytes | None:
    # Header layout (all little-endian uint32):
    # 0-11   identifier
    # 12     endianness
    # 16     glType
    # 20     glTypeSize
    # 24     glFormat
    # 28     glInternalFormat   -- format we care about
    # 32     glBaseInternalFormat
    # 36     pixelWidth
    # 40     pixelHeight
    # 44     pixelDepth
    # 48     numberOfArrayElements
    # 52     numberOfFaces
    # 56     numberOfMipmapLevels
    # 60     bytesOfKeyValueData
    # 64     [key-value pairs--]
    # 64+kvSize   imageSize (uint32)
    # 68+kvSize   imageData[imageSize]

    if len(data) < 64:
        return None

    (internal_fmt, real_width, real_height, kv_size) = struct.unpack_from('<IIII', data, 28)

    image_data_offset = 64 + kv_size
    if len(data) < image_data_offset + 4:
        return None
    (image_size,) = struct.unpack_from('<I', data, image_data_offset)
    image_data = data[image_data_offset + 4: image_data_offset + 4 + image_size]

    if real_width == 0 or real_height == 0:
        return None

    width  = _ceil_mul(real_width, 4)
    height = _ceil_mul(real_height, 4)

    rgba = _decode_ktx1(internal_fmt, image_data, width, height)
    if rgba is None:
        return None

    # Crop to real dimensions
    img = Image.fromarray(rgba[:real_height, :real_width], 'RGBA')
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _decode_ktx1(internal_fmt: int, image_data: bytes,
                 width: int, height: int) -> np.ndarray | None:
    if internal_fmt in (_GL_RGB8_ETC1, _GL_RGB8_ETC2, _GL_SRGB8_ETC2):
        return _decode_etc_rgb(image_data, width, height, punchthrough=False)

    if internal_fmt in (_GL_RGB8_PUNCHTHROUGH_ALPHA1_ETC2,
                        _GL_SRGB8_PUNCHTHROUGH_ALPHA1_ETC2):
        return _decode_etc_rgb(image_data, width, height, punchthrough=True)

    if internal_fmt in (_GL_RGBA8_ETC2_EAC, _GL_SRGB8_ALPHA8_ETC2_EAC):
        return _decode_etc_rgba(image_data, width, height)

    logger.debug('ktx_to_png: unsupported KTX1 glInternalFormat 0x%X', internal_fmt)
    return None


# -------------------------------------------------------------------------------
# ETC helpers  (port of Internal.cs + Public.cs)
# -------------------------------------------------------------------------------
def _bswap64(b8: bytes) -> int:
    """Read 8 bytes big-endian -- int (equivalent to C# BSwap)."""
    return int.from_bytes(b8[:8], 'big')


def _extend_sign(val: int, bits: int) -> int:
    shift = 32 - bits
    return (val << shift) >> shift    # arithmetic right-shift via Python int


def _clamp255(x) -> int:
    if x < 0:
        return 0
    if x > 255:
        return 255
    return int(x)


# Modifier tables used by Legacy/Individual ETC mode
_REMAP_TABLE_OPAQUE = [
    [  2,   8,  -2,   -8],
    [  5,  17,  -5,  -17],
    [  9,  29,  -9,  -29],
    [ 13,  42, -13,  -42],
    [ 18,  60, -18,  -60],
    [ 24,  80, -24,  -80],
    [ 33, 106, -33, -106],
    [ 47, 183, -47, -183],
]
_REMAP_TABLE_TRANSPARENT = [
    [ 0,   8, 0,   -8],
    [ 0,  17, 0,  -17],
    [ 0,  29, 0,  -29],
    [ 0,  42, 0,  -42],
    [ 0,  60, 0,  -60],
    [ 0,  80, 0,  -80],
    [ 0, 106, 0, -106],
    [ 0, 183, 0, -183],
]

# Distance table used by T/H mode
_DISTANCE_TABLE = [3, 6, 11, 16, 23, 31, 41, 64]

# EAC modifier table
_EAC_MODIFIER_TABLE = [
    [ -3,  -6,  -9, -15, 2, 5, 8, 14],
    [ -3,  -7, -10, -13, 2, 6, 9, 12],
    [ -2,  -5,  -8, -13, 1, 4, 7, 12],
    [ -2,  -4,  -6, -13, 1, 3, 5, 12],
    [ -3,  -6,  -8, -12, 2, 5, 7, 11],
    [ -3,  -7,  -9, -11, 2, 6, 8, 10],
    [ -4,  -7,  -8, -11, 3, 6, 7, 10],
    [ -3,  -5,  -8, -11, 2, 4, 7, 10],
    [ -2,  -6,  -8, -10, 1, 5, 7,  9],
    [ -2,  -5,  -8, -10, 1, 4, 7,  9],
    [ -2,  -4,  -8, -10, 1, 3, 7,  9],
    [ -2,  -5,  -7, -10, 1, 4, 6,  9],
    [ -3,  -4,  -7, -10, 2, 3, 6,  9],
    [ -1,  -2,  -3, -10, 0, 1, 2,  9],
    [ -4,  -6,  -8,  -9, 3, 5, 7,  8],
    [ -3,  -5,  -7,  -9, 2, 4, 6,  8],
]


def _legacy_etc(block: int, r0: int, g0: int, b0: int,
                r1: int, g1: int, b1: int,
                dest: bytearray, base_offset: int, pitch: int, opaque: bool) -> None:
    """Decompress one 4x4 ETC block in Individual or Differential mode."""
    remap = _REMAP_TABLE_OPAQUE if opaque else _REMAP_TABLE_TRANSPARENT
    flip_bit   = bool(block & 0x100000000)
    code_word0 = (block >> 37) & 0x7
    code_word1 = (block >> 34) & 0x7

    for i in range(2):
        for j in range(4):
            # block A
            x0 = i if flip_bit else j
            y0 = j if flip_bit else i
            x1 = (i + 2) if flip_bit else j
            y1 = j if flip_bit else (i + 2)

            m = x0 + y0 * 4
            idx = (((block >> (m + 16)) & 1) << 1) | ((block >> m) & 1)
            off = base_offset + x0 * pitch + y0 * 4

            if opaque or idx != 2:
                dest[off + 0] = _clamp255(r0 + remap[code_word0][idx])
                dest[off + 1] = _clamp255(g0 + remap[code_word0][idx])
                dest[off + 2] = _clamp255(b0 + remap[code_word0][idx])
                dest[off + 3] = 0xFF
            else:
                dest[off + 0] = 0
                dest[off + 1] = 0
                dest[off + 2] = 0
                dest[off + 3] = 0

            # block B
            m = x1 + y1 * 4
            idx = (((block >> (m + 16)) & 1) << 1) | ((block >> m) & 1)
            off = base_offset + x1 * pitch + y1 * 4

            if opaque or idx != 2:
                dest[off + 0] = _clamp255(r1 + remap[code_word1][idx])
                dest[off + 1] = _clamp255(g1 + remap[code_word1][idx])
                dest[off + 2] = _clamp255(b1 + remap[code_word1][idx])
                dest[off + 3] = 0xFF
            else:
                dest[off + 0] = 0
                dest[off + 1] = 0
                dest[off + 2] = 0
                dest[off + 3] = 0


def _etc_t_h(block: int, mode: int, dest: bytearray,
             base_offset: int, pitch: int, opaque: bool) -> None:
    """Decompress one 4x4 ETC block in T or H mode."""
    if mode == 1:  # T mode
        ra = (block >> 59) & 0x3
        rb = (block >> 56) & 0x3
        g0 = (block >> 52) & 0xF
        b0 = (block >> 48) & 0xF
        r1 = (block >> 44) & 0xF
        g1 = (block >> 40) & 0xF
        b1 = (block >> 36) & 0xF
        da = (block >> 34) & 0x3
        db = (block >> 32) & 0x1
        r0 = (ra << 2) | rb
    else:           # H mode
        r0 = (block >> 59) & 0xF
        ga = (block >> 56) & 0x7
        gb = (block >> 52) & 0x1
        ba = (block >> 51) & 0x1
        bb = (block >> 47) & 0x7
        r1 = (block >> 43) & 0xF
        g1 = (block >> 39) & 0xF
        b1 = (block >> 35) & 0xF
        da = (block >> 34) & 0x1
        db = (block >> 32) & 0x1
        g0 = (ga << 1) | gb
        b0 = (ba << 3) | bb

    r0 = (r0 << 4) | r0
    g0 = (g0 << 4) | g0
    b0 = (b0 << 4) | b0
    r1 = (r1 << 4) | r1
    g1 = (g1 << 4) | g1
    b1 = (b1 << 4) | b1

    if mode == 1:  # T
        dist_idx = (da << 1) | db
        dist = _DISTANCE_TABLE[dist_idx]
        paint = [
            (r0, g0, b0, 0xFF),
            (_clamp255(r1 + dist), _clamp255(g1 + dist), _clamp255(b1 + dist), 0xFF),
            (r1, g1, b1, 0xFF),
            (_clamp255(r1 - dist), _clamp255(g1 - dist), _clamp255(b1 - dist), 0xFF),
        ]
    else:           # H
        compare_a = (r0 << 16) | (g0 << 8) | b0
        compare_b = (r1 << 16) | (g1 << 8) | b1
        dist_idx  = (1 if compare_a >= compare_b else 0) | (da << 2) | (db << 1)
        dist = _DISTANCE_TABLE[dist_idx]
        paint = [
            (_clamp255(r0 + dist), _clamp255(g0 + dist), _clamp255(b0 + dist), 0xFF),
            (_clamp255(r0 - dist), _clamp255(g0 - dist), _clamp255(b0 - dist), 0xFF),
            (_clamp255(r1 + dist), _clamp255(g1 + dist), _clamp255(b1 + dist), 0xFF),
            (_clamp255(r1 - dist), _clamp255(g1 - dist), _clamp255(b1 - dist), 0xFF),
        ]

    for i in range(4):
        row_off = base_offset + i * pitch
        for j in range(4):
            k   = i + j * 4
            idx = (((block >> (k + 16)) & 1) << 1) | ((block >> k) & 1)
            off = row_off + j * 4
            if opaque or idx != 2:
                dest[off + 0] = paint[idx][0]
                dest[off + 1] = paint[idx][1]
                dest[off + 2] = paint[idx][2]
                dest[off + 3] = paint[idx][3]
            else:
                dest[off + 0] = 0
                dest[off + 1] = 0
                dest[off + 2] = 0
                dest[off + 3] = 0


def _etc_planar(block: int, dest: bytearray, base_offset: int, pitch: int) -> None:
    """Decompress one 4x4 ETC block in Planar mode."""
    ro  = (block >> 57) & 0x3F
    go1 = (block >> 56) & 0x01
    go2 = (block >> 49) & 0x3F
    bo1 = (block >> 48) & 0x01
    bo2 = (block >> 43) & 0x03
    bo3 = (block >> 39) & 0x07
    rh1 = (block >> 34) & 0x1F
    rh2 = (block >> 32) & 0x01
    gh  = (block >> 25) & 0x7F
    bh  = (block >> 19) & 0x3F
    rv  = (block >> 13) & 0x3F
    gv  = (block >>  6) & 0x7F
    bv  = (block >>  0) & 0x3F

    go = (go1 << 6) | go2
    bo = (bo1 << 5) | (bo2 << 3) | bo3
    rh = (rh1 << 1) | rh2

    ro = (ro << 2) | (ro >> 4)
    rh = (rh << 2) | (rh >> 4)
    rv = (rv << 2) | (rv >> 4)
    go = (go << 1) | (go >> 6)
    gh = (gh << 1) | (gh >> 6)
    gv = (gv << 1) | (gv >> 6)
    bo = (bo << 2) | (bo >> 4)
    bh = (bh << 2) | (bh >> 4)
    bv = (bv << 2) | (bv >> 4)

    for y in range(4):
        row_off = base_offset + y * pitch
        for x in range(4):
            rf = (x * (rh - ro) + y * (rv - ro) + (ro << 2) + 2) >> 2
            gf = (x * (gh - go) + y * (gv - go) + (go << 2) + 2) >> 2
            bf = (x * (bh - bo) + y * (bv - bo) + (bo << 2) + 2) >> 2
            off = row_off + x * 4
            dest[off + 0] = _clamp255(rf)
            dest[off + 1] = _clamp255(gf)
            dest[off + 2] = _clamp255(bf)
            dest[off + 3] = 0xFF


def _decompress_etc_block(compressed: bytes, dest: bytearray, dest_offset: int,
                          pitch: int, punchthrough: bool = False) -> None:
    """Main ETC block decompressor -- port of DecompressETCBlock in Internal.cs."""
    block = _bswap64(compressed)
    diff_bit = bool(block & 0x200000000)

    mode = 0  # legacy
    if not punchthrough and not diff_bit:
        # Individual mode
        r0 = (block >> 60) & 0xF
        r1 = (block >> 56) & 0xF
        g0 = (block >> 52) & 0xF
        g1 = (block >> 48) & 0xF
        b0 = (block >> 44) & 0xF
        b1 = (block >> 40) & 0xF
        r0 = (r0 << 4) | r0
        g0 = (g0 << 4) | g0
        b0 = (b0 << 4) | b0
        r1 = (r1 << 4) | r1
        g1 = (g1 << 4) | g1
        b1 = (b1 << 4) | b1
    else:
        # Differential / T / H / Planar modes
        r0 = (block >> 59) & 0x1F
        r1 = r0 + _extend_sign((block >> 56) & 0x7, 3)
        g0 = (block >> 51) & 0x1F
        g1 = g0 + _extend_sign((block >> 48) & 0x7, 3)
        b0 = (block >> 43) & 0x1F
        b1 = b0 + _extend_sign((block >> 40) & 0x7, 3)

        if r1 < 0 or r1 > 31:
            mode = 1   # T
        elif g1 < 0 or g1 > 31:
            mode = 2   # H
        elif b1 < 0 or b1 > 31:
            mode = 3   # Planar
        else:
            # Differential -- expand to 8-bit
            r0 = (r0 << 3) | (r0 >> 2)
            g0 = (g0 << 3) | (g0 >> 2)
            b0 = (b0 << 3) | (b0 >> 2)
            r1 = (r1 << 3) | (r1 >> 2)
            g1 = (g1 << 3) | (g1 >> 2)
            b1 = (b1 << 3) | (b1 >> 2)

    opaque = (not punchthrough) or diff_bit

    if mode == 0:
        _legacy_etc(block, r0, g0, b0, r1, g1, b1, dest, dest_offset, pitch, opaque)
    elif mode < 3:
        _etc_t_h(block, mode, dest, dest_offset, pitch, opaque)
    else:
        _etc_planar(block, dest, dest_offset, pitch)


def _decompress_eac_block(compressed: bytes, dest: bytearray, dest_offset: int,
                          pitch: int, pixel_size: int = 4) -> None:
    """Decompress one EAC alpha block -- port of DecompressEACBlock in Internal.cs."""
    block     = _bswap64(compressed)
    base_code = (block >> 56) & 0xFF
    mult      = (block >> 52) & 0xF
    modifiers = _EAC_MODIFIER_TABLE[(block >> 48) & 0xF]

    r_idx = dest_offset
    for y in range(4):
        for x in range(4):
            idx      = (block >> ((15 - (x * 4 + y)) * 3)) & 0x7
            modifier = modifiers[idx]
            d_value  = int(base_code) + modifier * int(mult)
            alpha    = _clamp255(d_value)
            dest[r_idx + x * pixel_size] = alpha
        r_idx += pitch


def _decode_etc_rgb(image_data: bytes, width: int, height: int,
                    punchthrough: bool = False) -> np.ndarray:
    """Decode a full ETC RGB (or punchthrough) image -- 8 bytes per block."""
    dest  = bytearray(width * height * 4)
    pitch = width * 4
    src_off = 0

    for block_y in range(0, height, 4):
        for block_x in range(0, width, 4):
            dst_off = (block_y * width + block_x) * 4
            _decompress_etc_block(image_data[src_off:src_off + 8],
                                  dest, dst_off, pitch, punchthrough)
            src_off += 8

    arr = np.frombuffer(dest, dtype=np.uint8).reshape(height, width, 4)
    return arr


def _decode_etc_rgba(image_data: bytes, width: int, height: int) -> np.ndarray:
    """Decode a full ETC2 RGBA8 EAC image -- 16 bytes per block.

    Calling convention matches EacRGBA in Public.cs:
      DecompressETCBlock(source[8:], dest)       -- color from bytes 8-15
      DecompressEACBlock(source[0:8], dest[3:])  -- alpha from bytes 0-7
    """
    dest  = bytearray(width * height * 4)
    pitch = width * 4
    src_off = 0

    for block_y in range(0, height, 4):
        for block_x in range(0, width, 4):
            dst_off = (block_y * width + block_x) * 4
            block_bytes = image_data[src_off:src_off + 16]
            # Color (bytes 8-15)
            _decompress_etc_block(block_bytes[8:], dest, dst_off, pitch, False)
            # Alpha (bytes 0-7), written to the alpha channel (offset +3)
            _decompress_eac_block(block_bytes[0:8], dest, dst_off + 3, pitch, 4)
            src_off += 16

    arr = np.frombuffer(dest, dtype=np.uint8).reshape(height, width, 4)
    return arr


# -------------------------------------------------------------------------------
# KTX2 -- ctypes path using the bundled ktx.dll (libktx)
# -------------------------------------------------------------------------------
# libktx constants (from Ktx.Enums.cs / libktx transcode_flags.h)
_KTX_CREATE_LOAD_IMAGE_DATA = 0x01   # KtxTextureCreateFlagBits.LoadImageDataBit
_KTX_TTF_RGBA32             = 13     # TranscodeFormat.Rgba32
_KTX_SUCCESS                = 0      # KtxErrorCode.KtxSuccess

# ktxTexture struct field offsets on 64-bit Windows (libktx 4.x, MSVC build).
# Layout derived from DECLARE_KTXTEXTURE_PUBLIC expansion in ktx.h:
#   classId(4) + pad(4) + vtbl(8) + vvtbl(8) + _protected(8)  -- 0-31
#   isArray/isCubemap/isCompressed/generateMipmaps (4x1)        -- 32-35
#   baseWidth(4) + baseHeight(4) + baseDepth(4) + ...             -- 36, 40
#   ... numFaces(4) ends at 64
#   orientation{x,y,z} each int32 (C enum default) = 12 bytes   -- 64-75
#   padding(4) to align 8-byte pointer                           -- 76-79
#   kvDataHead(8) + kvDataLen(4) + pad(4) + kvData(8)           -- 80-103
#   dataSize(size_t=8) + pData(ptr=8)                           -- 104, 112
_OFFSET_BASE_WIDTH  = 36
_OFFSET_BASE_HEIGHT = 40
_OFFSET_DATA_SIZE   = 104
_OFFSET_PDATA       = 112

_ktx_dll = None
_ktx_dll_loaded = False


def _find_ktx_dll() -> str | None:
    """Locate ktx.dll in both frozen (PyInstaller) and development environments."""
    # Development / installed: ktx.dll lives next to this Python file.
    candidate = Path(__file__).with_name('ktx.dll')
    if candidate.is_file():
        return str(candidate)

    # Frozen app: PyInstaller extracts binaries to _MEIPASS or next to the exe.
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidate = os.path.join(meipass, 'ktx.dll')
            if os.path.isfile(candidate):
                return candidate
        candidate = os.path.join(os.path.dirname(sys.executable), 'ktx.dll')
        if os.path.isfile(candidate):
            return candidate

    return None


def _get_ktx_dll():
    """Load and configure ktx.dll, returning the ctypes CDLL or None."""
    global _ktx_dll, _ktx_dll_loaded
    if _ktx_dll_loaded:
        return _ktx_dll
    _ktx_dll_loaded = True

    dll_path = _find_ktx_dll()
    if not dll_path:
        logger.debug('ktx_to_png: ktx.dll not found, KTX2 will use API fallback')
        return None

    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as exc:
        logger.debug('ktx_to_png: failed to load ktx.dll: %s', exc)
        return None

    try:
        # ktxTexture2_CreateFromMemory(data, size, flags, **texture) -- int
        dll.ktxTexture2_CreateFromMemory.restype  = ctypes.c_int
        dll.ktxTexture2_CreateFromMemory.argtypes = [
            ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint,
            ctypes.POINTER(ctypes.c_void_p),
        ]
        # ktxTexture2_NeedsTranscoding(texture) -- int (1 = yes)
        dll.ktxTexture2_NeedsTranscoding.restype  = ctypes.c_int
        dll.ktxTexture2_NeedsTranscoding.argtypes = [ctypes.c_void_p]
        # ktxTexture2_TranscodeBasis(texture, transcodeFormat, flags) -- int
        dll.ktxTexture2_TranscodeBasis.restype  = ctypes.c_int
        dll.ktxTexture2_TranscodeBasis.argtypes = [
            ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
        ]
        # ktxTexture2_Destroy(texture) -- void
        dll.ktxTexture2_Destroy.restype  = None
        dll.ktxTexture2_Destroy.argtypes = [ctypes.c_void_p]
    except Exception as exc:
        logger.debug('ktx_to_png: ktx.dll symbol setup failed: %s', exc)
        return None

    _ktx_dll = dll
    logger.debug('ktx_to_png: ktx.dll loaded from %s', dll_path)
    return dll


def _read_u32(ptr_int: int, offset: int) -> int:
    return ctypes.c_uint32.from_address(ptr_int + offset).value


def _read_u64(ptr_int: int, offset: int) -> int:
    return ctypes.c_uint64.from_address(ptr_int + offset).value


def _read_ptr(ptr_int: int, offset: int) -> int:
    return ctypes.c_uint64.from_address(ptr_int + offset).value


def _convert_ktx2(data: bytes) -> bytes | None:
    dll = _get_ktx_dll()
    if dll is None:
        return None

    texture = ctypes.c_void_p(0)
    err = dll.ktxTexture2_CreateFromMemory(
        data, len(data), _KTX_CREATE_LOAD_IMAGE_DATA,
        ctypes.byref(texture),
    )
    if err != _KTX_SUCCESS or not texture.value:
        logger.debug('ktx_to_png: KTX2 CreateFromMemory failed (err=%d)', err)
        return None

    tex_ptr = texture.value
    try:
        needs = dll.ktxTexture2_NeedsTranscoding(tex_ptr)
        if not needs:
            # Raw GPU format (e.g. BC7) -- no basis supercompression, skip
            logger.debug('ktx_to_png: KTX2 has no basis supercompression')
            return None

        err = dll.ktxTexture2_TranscodeBasis(tex_ptr, _KTX_TTF_RGBA32, 0)
        if err != _KTX_SUCCESS:
            logger.debug('ktx_to_png: KTX2 TranscodeBasis failed (err=%d)', err)
            return None

        # Read width and height from the struct
        width  = _read_u32(tex_ptr, _OFFSET_BASE_WIDTH)
        height = _read_u32(tex_ptr, _OFFSET_BASE_HEIGHT)
        if width == 0 or height == 0:
            logger.debug('ktx_to_png: KTX2 zero dimensions after transcode')
            return None

        expected_size = width * height * 4  # RGBA32

        # Read the pData pointer from the struct
        pdata_ptr = _read_ptr(tex_ptr, _OFFSET_PDATA)
        if pdata_ptr == 0:
            logger.debug('ktx_to_png: KTX2 pData is NULL')
            return None

        # Copy pixel data from native memory
        raw_bytes = (ctypes.c_uint8 * expected_size).from_address(pdata_ptr)
        rgba = np.frombuffer(raw_bytes, dtype=np.uint8).copy().reshape(height, width, 4)

        img = Image.fromarray(rgba, 'RGBA')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()

    finally:
        try:
            dll.ktxTexture2_Destroy(tex_ptr)
        except Exception:
            pass


