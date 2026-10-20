"""Whole-game texture filters."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from fleasion.rules import asset_filters
from fleasion.rules.asset_filters import (
    PRESETS,
    AssetFilterEngine,
    FilterSettings,
    filter_asset,
    make_step,
    normalize_filter_settings,
    settings_signature,
)

if TYPE_CHECKING:
    from pathlib import Path


def _png(color: tuple[int, int, int, int], size: tuple[int, int] = (8, 8)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGBA', size, color).save(buffer, format='PNG')
    return buffer.getvalue()


def _jpeg(color: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(buffer, format='JPEG', quality=95)
    return buffer.getvalue()


def _pixels(data: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(data)).convert('RGBA'), dtype=np.int16)


def _settings(*steps: asset_filters.FilterStep, materials: bool = False) -> FilterSettings:
    return {'enabled': True, 'include_materials': materials, 'steps': list(steps)}


def test_disabled_or_empty_filters_leave_assets_alone() -> None:
    data = _png((200, 30, 30, 255))
    assert filter_asset(data, {'enabled': False, 'include_materials': False, 'steps': [make_step('invert')]}) is None
    assert filter_asset(data, _settings()) is None
    assert filter_asset(data, _settings(make_step('invert', strength=0.0))) is None


def test_non_image_bodies_pass_through() -> None:
    settings = _settings(make_step('invert'))
    assert filter_asset(b'OggS\x00\x02rest-of-audio', settings) is None
    assert filter_asset(b'<roblox>model</roblox>', settings) is None


def test_grayscale_png_keeps_alpha_and_format() -> None:
    result = filter_asset(_png((255, 0, 0, 120)), _settings(make_step('grayscale')))
    assert result is not None and result.startswith(asset_filters.PNG_MAGIC)
    red, green, blue, alpha = _pixels(result)[0, 0]
    assert red == green == blue
    assert alpha == 120


def test_tint_moves_colour_toward_target_and_strength_mixes() -> None:
    gray = _png((128, 128, 128, 255))
    full = _pixels(filter_asset(gray, _settings(make_step('tint', color='#8a5cff'))) or b'')[0, 0]
    half = _pixels(
        filter_asset(gray, _settings(make_step('tint', color='#8a5cff', strength=0.5))) or b''
    )[0, 0]
    assert full[2] > full[1]  # purple: more blue than green
    assert abs(int(half[2]) - (128 + int(full[2])) // 2) <= 2


def test_invert_and_jpeg_round_trip() -> None:
    result = filter_asset(_jpeg((10, 200, 30)), _settings(make_step('invert')))
    assert result is not None and result.startswith(asset_filters.JPEG_MAGIC)
    red, green, blue, _alpha = _pixels(result)[0, 0]
    assert red > 200 and green < 80 and blue > 190


def test_pixelate_averages_blocks() -> None:
    image = Image.new('RGBA', (4, 4), (0, 0, 0, 255))
    image.putpixel((0, 0), (255, 255, 255, 255))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    result = _pixels(filter_asset(buffer.getvalue(), _settings(make_step('pixelate', amount=2))) or b'')
    assert (result[0:2, 0:2, 0] == result[0, 0, 0]).all()
    assert 0 < result[0, 0, 0] < 255


def test_hue_shift_preserves_gray() -> None:
    result = _pixels(filter_asset(_png((90, 90, 90, 255)), _settings(make_step('hue_shift', amount=120))) or b'')
    assert abs(int(result[0, 0, 0]) - 90) <= 1
    assert abs(int(result[0, 0, 2]) - 90) <= 1


def test_oversized_images_are_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asset_filters, 'MAX_FILTER_PIXELS', 16)
    assert filter_asset(_png((1, 2, 3, 255)), _settings(make_step('invert'))) is None


def test_material_textures_need_opt_in_and_skip_normal_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    ktx = b'\xabKTX 20\xbb\r\n\x1a\n' + b'\x00' * 64
    decoded = {'png': _png((200, 60, 60, 255))}
    def fake_decode(_data: bytes) -> bytes:
        return decoded['png']

    def fake_encode(image: Image.Image) -> bytes:
        return b'KTX2:' + image.tobytes()[:4]

    monkeypatch.setattr(asset_filters, '_ktx_to_png', fake_decode)
    monkeypatch.setattr(asset_filters, '_encode_ktx2', fake_encode)
    assert filter_asset(ktx, _settings(make_step('invert'))) is None
    assert filter_asset(ktx, _settings(make_step('invert'), materials=True)) == b'KTX2:' + bytes((55, 195, 195, 255))
    decoded['png'] = _png((128, 128, 255, 255))
    assert filter_asset(ktx, _settings(make_step('invert'), materials=True)) is None


def test_normalize_rejects_bad_input_and_clamps() -> None:
    settings = normalize_filter_settings(
        {
            'enabled': True,
            'steps': [
                {'effect': 'tint', 'strength': 7, 'color': 'abc'},
                {'effect': 'explode'},
                'nonsense',
                {'effect': 'pixelate', 'amount': 1000},
            ],
        }
    )
    assert [step['effect'] for step in settings['steps']] == ['tint', 'pixelate']
    assert settings['steps'][0]['strength'] == 1.0
    assert settings['steps'][0]['color'] == '#aabbcc'
    assert settings['steps'][1]['amount'] == 32.0
    assert normalize_filter_settings('junk') == asset_filters.default_filter_settings()


def test_presets_are_valid_and_change_images() -> None:
    data = _png((180, 120, 60, 255))
    for name, steps in PRESETS.items():
        assert normalize_filter_settings({'steps': list(steps)})['steps'] == list(steps), name
        assert filter_asset(data, _settings(*steps)) not in (None, data), name


def test_signature_ignores_disabled_steps() -> None:
    base = _settings(make_step('grayscale'))
    with_disabled = _settings(make_step('grayscale'), {**make_step('invert'), 'enabled': False})
    assert settings_signature(base) == settings_signature(with_disabled)
    assert settings_signature(base) != settings_signature(_settings(make_step('invert')))


def test_engine_caches_in_memory_and_on_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(make_step('invert'))
    calls: list[bytes] = []
    real = asset_filters.filter_asset

    def counting(data: bytes, current: FilterSettings) -> bytes | None:
        calls.append(data)
        return real(data, current)

    monkeypatch.setattr(asset_filters, 'filter_asset', counting)
    data = _png((1, 2, 3, 255))
    engine = AssetFilterEngine(lambda: settings, tmp_path)
    first = engine.process(data)
    assert first is not None and engine.process(data) == first
    assert len(calls) == 1
    fresh = AssetFilterEngine(lambda: settings, tmp_path)
    assert fresh.process(data) == first
    assert len(calls) == 1
    assert engine.is_active()
    assert not AssetFilterEngine(asset_filters.default_filter_settings).is_active()
