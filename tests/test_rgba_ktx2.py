import io
import struct

from PIL import Image

from Fleasion.cache.tools.ktx_to_png import convert
from Fleasion.cache.tools.ktx_to_png.ktx_to_png import _extend_sign
from Fleasion.cache.tools.rgba_ktx2 import (
    KTX2_MAGIC,
    VK_FORMAT_R8G8B8A8_UNORM,
    read_rgba8_ktx2,
    write_rgba8_ktx2,
)


def test_rgba8_ktx2_round_trips_to_png(tmp_path):
    rgba = bytes((
        255, 0, 0, 255,
        0, 255, 0, 255,
        0, 0, 255, 255,
        255, 255, 255, 128,
    ))
    ktx_path = tmp_path / 'sample.ktx2'

    write_rgba8_ktx2(rgba, 2, 2, ktx_path)
    data = ktx_path.read_bytes()

    assert data[:12] == KTX2_MAGIC
    assert struct.unpack_from('<I', data, 12)[0] == VK_FORMAT_R8G8B8A8_UNORM
    kvd_offset, kvd_length = struct.unpack_from('<II', data, 56)
    assert kvd_length == 60
    assert data[kvd_offset + 4:kvd_offset + 13] == b'KTXwriter'
    assert read_rgba8_ktx2(data) == (rgba, 2, 2)

    png = convert(data)
    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.mode == 'RGBA'
    assert image.size == (2, 2)
    assert image.tobytes() == rgba


def test_rgba8_ktx2_convert_handles_prefixed_magic(tmp_path):
    rgba = bytes((
        12, 34, 56, 255,
        78, 90, 123, 64,
    ))
    ktx_path = tmp_path / 'sample.ktx2'

    write_rgba8_ktx2(rgba, 1, 2, ktx_path)
    data = b'WRAP' + ktx_path.read_bytes()

    png = convert(data)
    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.mode == 'RGBA'
    assert image.size == (1, 2)
    assert image.tobytes() == rgba


def test_ktx1_header_uses_width_and_height_offsets():
    ktx1_magic = b'\xabKTX 11\xbb\r\n\x1a\n'
    header = (
        ktx1_magic
        + struct.pack(
            '<13I',
            0x04030201,  # endianness
            0,           # glType
            1,           # glTypeSize
            0,           # glFormat
            0x8D64,      # glInternalFormat: ETC1 RGB8
            6407,        # glBaseInternalFormat: GL_RGB, not image width
            4,
            4,
            0,
            0,
            1,
            1,
            0,
        )
    )
    data = header + struct.pack('<I', 8) + (b'\0' * 8)

    png = convert(data)

    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.size == (4, 4)


def test_ktx1_convert_handles_prefixed_magic():
    ktx1_magic = b'\xabKTX 11\xbb\r\n\x1a\n'
    header = (
        ktx1_magic
        + struct.pack(
            '<13I',
            0x04030201,
            0,
            1,
            0,
            0x8D64,
            6407,
            4,
            4,
            0,
            0,
            1,
            1,
            0,
        )
    )
    data = b'KTXP' + header + struct.pack('<I', 8) + (b'\0' * 8)

    png = convert(data)

    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.size == (4, 4)


def test_extend_sign_interprets_small_bitfields_as_signed_values():
    assert _extend_sign(0b000, 3) == 0
    assert _extend_sign(0b001, 3) == 1
    assert _extend_sign(0b011, 3) == 3
    assert _extend_sign(0b100, 3) == -4
    assert _extend_sign(0b101, 3) == -3
    assert _extend_sign(0b110, 3) == -2
    assert _extend_sign(0b111, 3) == -1
