from pathlib import Path

import numpy as np
from PIL import Image

from fleasion.cache.tools.orm_compositor import composite_orm, generate_orm_mip_chain
from fleasion.cache.tools.rgba_ktx2 import read_rgba8_ktx2_levels, write_rgba8_ktx2_levels


def _rgba_level(data: bytes, width: int, height: int) -> np.ndarray:
    return np.frombuffer(data, dtype=np.uint8).reshape(height, width, 4)


def test_orm_mips_filter_linear_channels_and_roughness_in_squared_space() -> None:
    orm = np.array(
        [
            [[0, 0, 0, 0], [1, 255, 255, 0]],
            [[2, 0, 0, 255], [3, 255, 255, 255]],
        ],
        dtype=np.uint8,
    )
    flat_normal = np.full((2, 2, 4), 255, dtype=np.uint8)
    flat_normal[:, :, 0] = 128
    flat_normal[:, :, 1] = 128

    levels = generate_orm_mip_chain(orm, flat_normal)
    child = _rgba_level(levels[1], 1, 1)[0, 0]

    # R/B/A remain ordinary linear BOX averages. Roughness is the RMS of the
    # base roughness values when the normal map contributes no meaningful variance.
    assert tuple(child) == (2, 180, 128, 128)


def test_orm_roughness_mips_increase_with_normal_variance() -> None:
    orm = np.zeros((2, 2, 4), dtype=np.uint8)
    divergent_normal = np.array(
        [
            [[255, 128, 128, 255], [0, 128, 128, 255]],
            [[255, 128, 128, 255], [0, 128, 128, 255]],
        ],
        dtype=np.uint8,
    )

    without_normals = _rgba_level(generate_orm_mip_chain(orm)[1], 1, 1)[0, 0]
    with_normals = _rgba_level(generate_orm_mip_chain(orm, divergent_normal)[1], 1, 1)[0, 0]

    assert without_normals[1] == 0
    assert with_normals[1] == 255


def test_composite_orm_uses_normal_source_for_roughness_mips(tmp_path: Path) -> None:
    baseline = tmp_path / 'orm.ktx2'
    base_orm = np.zeros((2, 2, 4), dtype=np.uint8)
    base_orm[:, :, 1] = 64
    base_orm[:, :, 3] = 128
    write_rgba8_ktx2_levels([base_orm.tobytes()], 2, 2, baseline)

    roughness = tmp_path / 'roughness.png'
    Image.fromarray(np.zeros((2, 2), dtype=np.uint8), 'L').save(roughness)

    normal = tmp_path / 'normal.png'
    normal_rgba = np.array(
        [
            [[255, 128, 128, 255], [0, 128, 128, 255]],
            [[255, 128, 128, 255], [0, 128, 128, 255]],
        ],
        dtype=np.uint8,
    )
    Image.fromarray(normal_rgba, 'RGBA').save(normal)

    output = composite_orm(
        baseline,
        {'roughness': roughness},
        cache_dir=tmp_path / 'cache',
        normal_source=normal,
    )

    assert output is not None
    parsed = read_rgba8_ktx2_levels(open(output, 'rb').read())
    assert parsed is not None
    levels, width, height = parsed
    assert (width, height) == (2, 2)
    assert len(levels) == 2
    child = _rgba_level(levels[1], 1, 1)[0, 0]
    assert child[1] == 255


def test_orm_roughness_variance_understands_roblox_dxt5nm_normals() -> None:
    orm = np.zeros((2, 2, 4), dtype=np.uint8)
    dxt5nm = np.zeros((2, 2, 4), dtype=np.uint8)
    dxt5nm[:, :, 0] = 255
    dxt5nm[:, :, 1] = 128
    dxt5nm[:, :, 2] = 0
    dxt5nm[:, 0, 3] = 255
    dxt5nm[:, 1, 3] = 0

    child = _rgba_level(generate_orm_mip_chain(orm, dxt5nm)[1], 1, 1)[0, 0]

    # Alpha carries X in Roblox's native-Windows DXT5nm encoding. If it were
    # ignored as ordinary RGBA alpha, every RGB texel above would be identical
    # and the roughness variance adjustment would remain zero.
    assert child[1] > 0
