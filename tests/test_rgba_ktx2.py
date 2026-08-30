import io
import struct
from collections.abc import Callable
from pathlib import Path
from typing import cast

from PIL import Image

from fleasion.cache.tools.ktx_to_png import convert
from fleasion.cache.tools.ktx_to_png import ktx_to_png as ktx_module


def _extend_sign(value: int, bits: int) -> int:
    callback = cast('Callable[[int, int], int]', ktx_module.__dict__['_extend_sign'])
    return callback(value, bits)


from fleasion.cache.tools.rgba_ktx2 import (
    KTX2_MAGIC,
    VK_FORMAT_R8G8B8A8_UNORM,
    generate_rgba8_mip_chain,
    read_rgba8_ktx2,
    read_rgba8_ktx2_levels,
    write_rgba8_ktx2,
    write_rgba8_ktx2_levels,
)


def test_rgba8_ktx2_round_trips_to_png(tmp_path: Path) -> None:
    rgba = bytes(
        (
            255,
            0,
            0,
            255,
            0,
            255,
            0,
            255,
            0,
            0,
            255,
            255,
            255,
            255,
            255,
            128,
        )
    )
    ktx_path = tmp_path / 'sample.ktx2'

    write_rgba8_ktx2(rgba, 2, 2, ktx_path)
    data = ktx_path.read_bytes()

    assert data[:12] == KTX2_MAGIC
    assert struct.unpack_from('<I', data, 12)[0] == VK_FORMAT_R8G8B8A8_UNORM
    assert struct.unpack_from('<I', data, 40)[0] == 2
    kvd_offset, kvd_length = struct.unpack_from('<II', data, 56)
    assert kvd_length == 60
    assert data[kvd_offset + 4 : kvd_offset + 13] == b'KTXwriter'
    assert read_rgba8_ktx2(data) == (rgba, 2, 2)

    png = convert(data)
    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.mode == 'RGBA'
    assert image.size == (2, 2)
    assert image.tobytes() == rgba


def test_rgba8_ktx2_writes_full_level_index_smallest_level_first(tmp_path: Path) -> None:
    rgba = bytes(range(64))
    ktx_path = tmp_path / 'mipped.ktx2'

    write_rgba8_ktx2(rgba, 4, 4, ktx_path, mipmap_mode='linear')
    data = ktx_path.read_bytes()

    assert struct.unpack_from('<I', data, 40)[0] == 3
    entries = [struct.unpack_from('<QQQ', data, 80 + 24 * i) for i in range(3)]
    assert [entry[1] for entry in entries] == [64, 16, 4]
    assert [entry[2] for entry in entries] == [64, 16, 4]
    assert entries[2][0] < entries[1][0] < entries[0][0]

    parsed = read_rgba8_ktx2_levels(data)
    assert parsed is not None
    levels, width, height = parsed
    assert (width, height) == (4, 4)
    assert [len(level) for level in levels] == [64, 16, 4]
    assert levels[0] == rgba


def test_linear_mip_generation_uses_box_average_with_nearest_even_rounding() -> None:
    rgba = bytes(
        (
            0,
            0,
            0,
            0,
            1,
            0,
            255,
            255,
            2,
            1,
            0,
            0,
            3,
            1,
            255,
            255,
        )
    )

    levels = generate_rgba8_mip_chain(rgba, 2, 2, mipmap_mode='linear')

    assert levels == [rgba, bytes((2, 0, 128, 128))]


def test_color_mip_generation_filters_rgb_in_gamma2_space() -> None:
    rgba = bytes(
        (
            0,
            0,
            0,
            0,
            255,
            255,
            255,
            255,
            0,
            0,
            0,
            0,
            255,
            255,
            255,
            255,
        )
    )

    levels = generate_rgba8_mip_chain(rgba, 2, 2, mipmap_mode='color')

    assert levels == [rgba, bytes((180, 180, 180, 128))]


def test_normal_mip_generation_bilinearly_filters_and_renormalizes_vectors() -> None:
    rgba = bytes(
        (
            255,
            128,
            128,
            0,
            128,
            255,
            128,
            255,
            255,
            128,
            128,
            0,
            128,
            255,
            128,
            255,
        )
    )

    normal_levels = generate_rgba8_mip_chain(rgba, 2, 2, mipmap_mode='normal')
    linear_levels = generate_rgba8_mip_chain(rgba, 2, 2, mipmap_mode='linear')

    assert normal_levels == [rgba, bytes((218, 218, 128, 128))]
    assert linear_levels == [rgba, bytes((192, 192, 128, 128))]


def test_normal_mips_are_resampled_directly_from_base_level() -> None:
    rgba = bytes(
        (
            255,
            128,
            128,
            255,
            128,
            255,
            128,
            255,
            128,
            128,
            255,
            255,
            0,
            128,
            128,
            255,
            128,
            0,
            128,
            255,
            128,
            128,
            0,
            255,
            220,
            180,
            180,
            255,
            60,
            190,
            180,
            255,
            200,
            100,
            180,
            255,
            100,
            220,
            160,
            255,
            160,
            80,
            230,
            255,
            180,
            180,
            180,
            255,
            80,
            80,
            230,
            255,
            230,
            80,
            160,
            255,
            80,
            230,
            160,
            255,
            128,
            128,
            255,
            255,
        )
    )

    levels = generate_rgba8_mip_chain(rgba, 4, 4, mipmap_mode='normal')

    assert levels[-1] == bytes((168, 174, 239, 255))


def test_explicit_rgba8_mips_round_trip_without_regeneration(tmp_path: Path) -> None:
    base = bytes(range(16))
    authored_tail = bytes((9, 8, 7, 6))
    ktx_path = tmp_path / 'authored.ktx2'

    write_rgba8_ktx2_levels([base, authored_tail], 2, 2, ktx_path)

    assert read_rgba8_ktx2_levels(ktx_path.read_bytes()) == ([base, authored_tail], 2, 2)


def test_rgba8_ktx2_convert_handles_prefixed_magic(tmp_path: Path) -> None:
    rgba = bytes(
        (
            12,
            34,
            56,
            255,
            78,
            90,
            123,
            64,
        )
    )
    ktx_path = tmp_path / 'sample.ktx2'

    write_rgba8_ktx2(rgba, 1, 2, ktx_path)
    data = b'WRAP' + ktx_path.read_bytes()

    png = convert(data)
    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.mode == 'RGBA'
    assert image.size == (1, 2)
    assert image.tobytes() == rgba


def test_ktx1_header_uses_width_and_height_offsets() -> None:
    ktx1_magic = b'\xabKTX 11\xbb\r\n\x1a\n'
    header = ktx1_magic + struct.pack(
        '<13I',
        0x04030201,  # endianness
        0,  # glType
        1,  # glTypeSize
        0,  # glFormat
        0x8D64,  # glInternalFormat: ETC1 RGB8
        6407,  # glBaseInternalFormat: GL_RGB, not image width
        4,
        4,
        0,
        0,
        1,
        1,
        0,
    )
    data = header + struct.pack('<I', 8) + (b'\0' * 8)

    png = convert(data)

    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.size == (4, 4)


def test_ktx1_convert_handles_prefixed_magic() -> None:
    ktx1_magic = b'\xabKTX 11\xbb\r\n\x1a\n'
    header = ktx1_magic + struct.pack(
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
    data = b'KTXP' + header + struct.pack('<I', 8) + (b'\0' * 8)

    png = convert(data)

    assert png is not None
    image = Image.open(io.BytesIO(png))
    assert image.size == (4, 4)


def test_extend_sign_interprets_small_bitfields_as_signed_values() -> None:
    assert _extend_sign(0b000, 3) == 0
    assert _extend_sign(0b001, 3) == 1
    assert _extend_sign(0b011, 3) == 3
    assert _extend_sign(0b100, 3) == -4
    assert _extend_sign(0b101, 3) == -3
    assert _extend_sign(0b110, 3) == -2
    assert _extend_sign(0b111, 3) == -1
