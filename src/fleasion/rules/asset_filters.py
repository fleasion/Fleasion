"""Whole-game texture filters applied to images as they download through the proxy.

A filter is an ordered stack of steps (tint, grayscale, hue shift, ...) that is
applied to every PNG and JPEG texture Roblox downloads, and optionally to KTX
material textures.  Replacement files the user picked are never filtered: the
proxy serves those locally before this module sees them.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import io
import json
import math
import os
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypedDict, cast

import numpy as np
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from numpy.typing import NDArray

    type FloatImage = NDArray[np.float32]

type FilterEffect = Literal[
    'tint', 'grayscale', 'hue_shift', 'saturation', 'pixelate', 'posterize', 'invert'
]

FILTER_EFFECTS: tuple[FilterEffect, ...] = (
    'tint',
    'grayscale',
    'hue_shift',
    'saturation',
    'pixelate',
    'posterize',
    'invert',
)

# (minimum, maximum, default) for each effect's ``amount``; effects without an
# amount ignore it.
EFFECT_AMOUNT_RANGES: Mapping[FilterEffect, tuple[float, float, float]] = {
    'hue_shift': (-180.0, 180.0, 90.0),
    'saturation': (0.0, 3.0, 1.5),
    'pixelate': (2.0, 32.0, 4.0),
    'posterize': (2.0, 16.0, 6.0),
}

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
JPEG_MAGIC = b'\xff\xd8\xff'
MAX_FILTER_PIXELS = 4096 * 4096
MAX_FILTER_BYTES = 32 * 1024 * 1024
_MEMORY_CACHE_ENTRIES = 128
_DEFAULT_TINT = '#8a5cff'


class FilterStep(TypedDict):
    """One effect in a filter stack."""

    effect: FilterEffect
    enabled: bool
    strength: float
    """How much of the effect to mix over the original, 0..1."""
    color: str
    """``#rrggbb`` target colour for ``tint``."""
    amount: float
    """Effect parameter: hue degrees, saturation factor, pixel size or colour levels."""


class FilterSettings(TypedDict):
    """Filter configuration stored in settings and in game profiles."""

    enabled: bool
    include_materials: bool
    """Also filter KTX material textures (colour maps only; normal maps are skipped)."""
    steps: list[FilterStep]


def make_step(
    effect: FilterEffect,
    *,
    strength: float = 1.0,
    color: str = _DEFAULT_TINT,
    amount: float | None = None,
) -> FilterStep:
    """Build a step with the effect's default amount."""
    default_amount = EFFECT_AMOUNT_RANGES.get(effect, (0.0, 0.0, 0.0))[2]
    return {
        'effect': effect,
        'enabled': True,
        'strength': strength,
        'color': color,
        'amount': default_amount if amount is None else amount,
    }


PRESETS: Mapping[str, tuple[FilterStep, ...]] = {
    'purple_haze': (make_step('tint', strength=0.55, color='#8a5cff'),),
    'noir': (make_step('grayscale'),),
    'retro_pixels': (make_step('pixelate', amount=4), make_step('posterize', amount=6)),
    'vaporwave': (
        make_step('hue_shift', amount=150, strength=0.8),
        make_step('saturation', amount=1.4),
    ),
    'sunset': (make_step('tint', strength=0.4, color='#ff8a4c'),),
    'inverted': (make_step('invert'),),
}


def default_filter_settings() -> FilterSettings:
    return {'enabled': False, 'include_materials': False, 'steps': []}


def _clamp(value: object, low: float, high: float, default: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    number = float(value)
    if math.isnan(number):
        return default
    return min(max(number, low), high)


def normalize_color(value: object, default: str = _DEFAULT_TINT) -> str:
    """Return ``value`` as a lowercase ``#rrggbb`` string, or ``default``."""
    if not isinstance(value, str):
        return default
    text = value.strip().lower()
    if not text.startswith('#'):
        text = f'#{text}'
    if len(text) == 4:
        text = '#' + ''.join(char * 2 for char in text[1:])
    if len(text) != 7:
        return default
    try:
        int(text[1:], 16)
    except ValueError:
        return default
    return text


def _as_effect(value: object) -> FilterEffect | None:
    for effect in FILTER_EFFECTS:
        if value == effect:
            return effect
    return None


def normalize_step(value: object) -> FilterStep | None:
    """Validate one step from untrusted JSON; unknown effects are dropped."""
    if not isinstance(value, dict):
        return None
    raw = cast('dict[str, object]', value)
    effect = _as_effect(raw.get('effect'))
    if effect is None:
        return None
    low, high, default = EFFECT_AMOUNT_RANGES.get(effect, (0.0, 0.0, 0.0))
    return {
        'effect': effect,
        'enabled': raw.get('enabled', True) is not False,
        'strength': _clamp(raw.get('strength'), 0.0, 1.0, 1.0),
        'color': normalize_color(raw.get('color')),
        'amount': _clamp(raw.get('amount'), low, high, default),
    }


def normalize_filter_settings(value: object) -> FilterSettings:
    """Validate filter settings from untrusted JSON (settings files, mod packs)."""
    if not isinstance(value, dict):
        return default_filter_settings()
    raw = cast('dict[str, object]', value)
    raw_steps = raw.get('steps')
    steps: list[FilterStep] = []
    if isinstance(raw_steps, list):
        for item in cast('list[object]', raw_steps):
            step = normalize_step(item)
            if step is not None:
                steps.append(step)
    return {
        'enabled': raw.get('enabled') is True,
        'include_materials': raw.get('include_materials') is True,
        'steps': steps,
    }


def active_steps(settings: FilterSettings) -> list[FilterStep]:
    """Steps that would change an image; empty when the filter is off."""
    if not settings['enabled']:
        return []
    return [step for step in settings['steps'] if step['enabled'] and step['strength'] > 0]


def settings_signature(settings: FilterSettings) -> str:
    """A stable digest of everything that affects filter output."""
    payload = {
        'include_materials': settings['include_materials'],
        'steps': active_steps(settings),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


# Image math.  Colour channels are float32 in 0..1; alpha is never changed
# except by ``pixelate``, which has to move it with the colour.


def _luminance(rgb: FloatImage) -> FloatImage:
    weights = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    return cast('FloatImage', rgb @ weights)


def _hex_rgb(color: str) -> FloatImage:
    value = int(color[1:], 16)
    channels = ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
    return np.array(channels, dtype=np.float32) / 255.0


def _hue_rotation_matrix(degrees: float) -> FloatImage:
    """The SVG/CSS ``hue-rotate`` matrix, which keeps luminance constant."""
    radians = np.deg2rad(degrees)
    cos, sin = float(np.cos(radians)), float(np.sin(radians))
    return np.array(
        [
            [
                0.213 + cos * 0.787 - sin * 0.213,
                0.715 - cos * 0.715 - sin * 0.715,
                0.072 - cos * 0.072 + sin * 0.928,
            ],
            [
                0.213 - cos * 0.213 + sin * 0.143,
                0.715 + cos * 0.285 + sin * 0.140,
                0.072 - cos * 0.072 - sin * 0.283,
            ],
            [
                0.213 - cos * 0.213 - sin * 0.787,
                0.715 - cos * 0.715 + sin * 0.715,
                0.072 + cos * 0.928 + sin * 0.072,
            ],
        ],
        dtype=np.float32,
    )


def _pixelate(rgba: FloatImage, block: int) -> FloatImage:
    height, width = rgba.shape[:2]
    small = (max(1, width // block), max(1, height // block))
    image = Image.fromarray(np.round(rgba * 255).astype(np.uint8), 'RGBA')
    image = image.resize(small, Image.Resampling.BOX).resize(
        (width, height), Image.Resampling.NEAREST
    )
    return np.asarray(image, dtype=np.float32) / 255.0


def _effect_target(rgba: FloatImage, step: FilterStep) -> FloatImage:
    """Return the fully applied effect for ``rgba`` (before strength mixing)."""
    rgb = rgba[..., :3]
    effect = step['effect']
    if effect == 'pixelate':
        return _pixelate(rgba, round(step['amount']))
    if effect == 'tint':
        color = _hex_rgb(step['color'])
        color_luma = max(float(_luminance(color)), 0.05)
        target = _luminance(rgb)[..., None] * (color / color_luma)
    elif effect == 'grayscale':
        target = np.repeat(_luminance(rgb)[..., None], 3, axis=-1)
    elif effect == 'hue_shift':
        target = rgb @ _hue_rotation_matrix(step['amount']).T
    elif effect == 'saturation':
        luma = _luminance(rgb)[..., None]
        target = luma + (rgb - luma) * step['amount']
    elif effect == 'posterize':
        levels = round(step['amount']) - 1
        target = np.round(rgb * levels) / levels
    else:  # invert
        target = 1.0 - rgb
    out = rgba.copy()
    out[..., :3] = np.clip(target, 0.0, 1.0)
    return out


def apply_steps(rgba: FloatImage, steps: Iterable[FilterStep]) -> FloatImage:
    """Apply ``steps`` in order to a float RGBA image."""
    for step in steps:
        target = _effect_target(rgba, step)
        strength = step['strength']
        if strength >= 1.0:
            rgba = target
        else:
            rgba = cast('FloatImage', rgba + (target - rgba) * np.float32(strength))
    return rgba


def filter_image(image: Image.Image, steps: Iterable[FilterStep]) -> Image.Image:
    """Return a filtered RGBA copy of ``image``."""
    rgba = np.asarray(image.convert('RGBA'), dtype=np.float32) / 255.0
    result = apply_steps(rgba, steps)
    return Image.fromarray(np.round(np.clip(result, 0.0, 1.0) * 255).astype(np.uint8), 'RGBA')


def _looks_like_normal_map(image: Image.Image) -> bool:
    """Tangent-space normal maps are mostly (128, 128, 255) and must not be recoloured."""
    sample = image.convert('RGB').resize((32, 32), Image.Resampling.BOX)
    mean_r, mean_g, mean_b = np.asarray(sample, dtype=np.float32).reshape(-1, 3).mean(axis=0)
    return mean_b > 190 and abs(mean_r - 128) < 32 and abs(mean_g - 128) < 32


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', compress_level=3)
    return buffer.getvalue()


def _encode_jpeg(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, format='JPEG', quality=92)
    return buffer.getvalue()


def _open_image(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data))
        if image.width * image.height > MAX_FILTER_PIXELS:
            return None
        image.load()
    except OSError, ValueError, Image.DecompressionBombError:
        return None
    return image


def _ktx_to_png(data: bytes) -> bytes | None:
    module = importlib.import_module('fleasion.cache.tools.ktx_to_png.ktx_to_png')
    stripped = cast('Callable[[bytes], bytes | None]', module.strip_prefixed_ktx)(data)
    if stripped is None:
        return None
    return cast('Callable[[bytes], bytes | None]', module.convert)(stripped)


def _encode_ktx2(image: Image.Image) -> bytes:
    module = importlib.import_module('fleasion.cache.tools.rgba_ktx2')
    write = cast('Callable[..., None]', module.write_rgba8_ktx2)
    rgba = image.convert('RGBA')
    fd, name = tempfile.mkstemp(suffix='.ktx2')
    os.close(fd)
    path = Path(name)
    try:
        write(rgba.tobytes(), rgba.width, rgba.height, path, mipmap_mode='color')
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def filter_asset(data: bytes, settings: FilterSettings) -> bytes | None:
    """Return filtered asset bytes, or ``None`` when ``data`` should pass unchanged."""
    steps = active_steps(settings)
    if not steps or not data or len(data) > MAX_FILTER_BYTES:
        return None
    if data.startswith(PNG_MAGIC):
        image = _open_image(data)
        return _encode_png(filter_image(image, steps)) if image is not None else None
    if data.startswith(JPEG_MAGIC):
        image = _open_image(data)
        return _encode_jpeg(filter_image(image, steps)) if image is not None else None
    if not settings['include_materials']:
        return None
    png = _ktx_to_png(data)
    image = _open_image(png) if png is not None else None
    if image is None or _looks_like_normal_map(image):
        return None
    return _encode_ktx2(filter_image(image, steps))


class AssetFilterEngine:
    """Filters CDN asset bodies for the proxy, caching results by content and settings."""

    def __init__(
        self,
        settings_provider: Callable[[], FilterSettings],
        cache_dir: Path | None = None,
    ) -> None:
        self._settings_provider = settings_provider
        self._cache_dir = cache_dir
        self._memory: OrderedDict[str, bytes | None] = OrderedDict()
        self._lock = threading.Lock()

    def settings(self) -> FilterSettings:
        return self._settings_provider()

    def is_active(self) -> bool:
        return bool(active_steps(self.settings()))

    def _cache_key(self, data: bytes, settings: FilterSettings) -> str:
        digest = hashlib.sha256(data).hexdigest()[:32]
        return f'{digest}-{settings_signature(settings)}'

    def _read_disk(self, key: str) -> bytes | None:
        if self._cache_dir is None:
            return None
        try:
            return (self._cache_dir / key).read_bytes()
        except OSError:
            return None

    def _write_disk(self, key: str, value: bytes) -> None:
        if self._cache_dir is None:
            return
        with contextlib.suppress(OSError):
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            temporary = self._cache_dir / f'.{key}.{os.getpid()}.tmp'
            temporary.write_bytes(value)
            temporary.replace(self._cache_dir / key)

    def _remember(self, key: str, value: bytes | None) -> None:
        with self._lock:
            self._memory[key] = value
            self._memory.move_to_end(key)
            while len(self._memory) > _MEMORY_CACHE_ENTRIES:
                self._memory.popitem(last=False)

    def process(self, data: bytes) -> bytes | None:
        """Return the filtered body for ``data``, or ``None`` to send it unchanged."""
        settings = self.settings()
        if not active_steps(settings):
            return None
        key = self._cache_key(data, settings)
        with self._lock:
            if key in self._memory:
                self._memory.move_to_end(key)
                return self._memory[key]
        cached = self._read_disk(key)
        if cached is not None:
            self._remember(key, cached)
            return cached
        result = filter_asset(data, settings)
        self._remember(key, result)
        if result is not None:
            self._write_disk(key, result)
        return result
