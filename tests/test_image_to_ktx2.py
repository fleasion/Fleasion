from PIL import Image

from fleasion.cache.tools.image_to_ktx2 import converter
from fleasion.cache.tools.rgba_ktx2 import read_rgba8_ktx2_levels


def test_image_converter_generates_full_mips_and_keys_cache_by_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(converter, 'APP_CACHE_DIR', tmp_path / 'cache')
    image_path = tmp_path / 'checker.png'

    image = Image.new('RGBA', (4, 4))
    image.putdata(
        [
            (255, 255, 255, 255) if (x + y) % 2 else (0, 0, 0, 255)
            for y in range(4)
            for x in range(4)
        ]
    )
    image.save(image_path)

    color_path = converter.get_or_create_ktx2_from_image(image_path, mipmap_mode='color')
    linear_path = converter.get_or_create_ktx2_from_image(image_path, mipmap_mode='linear')

    assert color_path != linear_path
    assert converter.get_or_create_ktx2_from_image(image_path, mipmap_mode='color') == color_path

    color = read_rgba8_ktx2_levels(color_path.read_bytes())
    linear = read_rgba8_ktx2_levels(linear_path.read_bytes())
    assert color is not None
    assert linear is not None

    color_levels, width, height = color
    linear_levels, linear_width, linear_height = linear
    assert (width, height) == (4, 4)
    assert (linear_width, linear_height) == (4, 4)
    assert len(color_levels) == len(linear_levels) == 3
    assert color_levels[0] == linear_levels[0] == image.tobytes()
    assert color_levels[1] != linear_levels[1]
