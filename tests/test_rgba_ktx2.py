import io
import struct

from PIL import Image

from Fleasion.cache.tools.ktx_to_png import convert
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
