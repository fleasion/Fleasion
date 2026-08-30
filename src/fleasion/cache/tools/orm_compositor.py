"""ORM slot KTX2 compositor for per-channel PNG replacement.

Reads a captured baseline ORM KTX/KTX2 (including Roblox BC and ETC2 variants),
substitutes individual channels with R-channel values from user-supplied PNG
files, and writes an uncompressed RGBA32 KTX2 which Roblox accepts as a
TexturePack slot replacement. Roughness mips can additionally use the matching
Normal map for Roblox-style normal-variance/specular-AA broadening.

Channel map (empirically confirmed):
  R = Metalness  (R-channel of source PNG, 0 = non-metallic)
  G = Roughness  (R-channel of source PNG, 255 = fully rough)
  B = Emissive   (R-channel of source PNG, 0 = none)
  A = Height     (R-channel of source PNG, 128 = neutral)
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import struct
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]

import numpy as np
from PIL import Image

from fleasion.utils import log_buffer
from fleasion.utils.paths import APP_CACHE_DIR

_CHANNEL_MAP: dict[str, int] = {
    'metalness': 0,
    'roughness': 1,
    'emissive': 2,
    'height': 3,
}
# Values used when a channel is explicitly "removed" (set to None path).
# metalness=0 (non-metallic), roughness=0 (fully smooth), emissive=0 (off),
# height=128 (neutral displacement).
_CHANNEL_ZERO: dict[str, int] = {
    'metalness': 0,
    'roughness': 0,
    'emissive': 0,
    'height': 128,
}

_VK_BC1 = 131  # VK_FORMAT_BC1_RGB_UNORM_BLOCK  (DXT1, no alpha)
_VK_BC3 = 137  # VK_FORMAT_BC3_UNORM_BLOCK       (DXT5, with alpha)

# Roblox TexturePack captures show that roughness is not mipmapped as an
# independent scalar. It is filtered in squared/perceptual-roughness space and
# broadened by the variance of the corresponding tangent-space normal map.
# Adjacent Roblox 7rdo mip transitions fit a coefficient very close to 1.0;
# because each generated roughness level already carries the previous level's
# accumulated variance in r², that adjacent coefficient is the appropriate one
# for sequential chain generation.
_ROUGHNESS_NORMAL_VARIANCE_SCALE = 1.0
_ORM_COMPOSITOR_CACHE_VERSION = b'orm-mips-v2-normal-variance'


def composite_orm(  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
    baseline: Path | None,
    channels: dict[str, Path | None],
    cache_dir: Path = APP_CACHE_DIR,
    *,
    normal_source: Path | None = None,
    normal_baseline: Path | None = None,
) -> str | None:
    """Composite a new ORM KTX2 from a baseline slot file plus per-channel PNG overrides.

    Parameters
    ----------
    baseline:
        Path to an existing ``{parent_id}_slot{N}.ktx2`` (BC1 or BC3,
        zstd-compressed level data).  May be ``None`` if the Roblox CDN has
        not yet delivered the slot — sensible defaults are used in that case.
    channels:
        Mapping of channel name to source PNG path (or ``None`` to remove /
        zero out that channel).  Valid names: ``metalness``, ``roughness``,
        ``emissive``, ``height``.  A ``None`` value sets the channel to its
        "removed" default (metalness=0, roughness=0, emissive=0, height=128).
        The **R-channel** of each non-None PNG is extracted.
    cache_dir:
        Root of the Fleasion cache directory.
    normal_source:
        Optional replacement Normal map.  When supplied, its vectors drive the
        roughness normal-variance/specular-AA mip adjustment.
    normal_baseline:
        Original captured Normal slot used when ``normal_source`` is absent.

    Returns
    -------
    str or None
        Absolute path to the output ``.ktx2`` file, or ``None`` on failure.
    """

    # ── Cache key: inputs + compositor algorithm version ────────────────────
    h = hashlib.md5(usedforsecurity=False)
    h.update(_ORM_COMPOSITOR_CACHE_VERSION)
    if baseline and baseline.exists():
        h.update(baseline.name.encode())
        with contextlib.suppress(OSError):
            h.update(struct.pack('<Q', int(baseline.stat().st_mtime * 1e9)))
    for normal_path in (normal_source, normal_baseline):
        if normal_path and normal_path.exists():
            h.update(normal_path.name.encode())
            with contextlib.suppress(OSError):
                h.update(struct.pack('<Q', int(normal_path.stat().st_mtime * 1e9)))
    for name in sorted(channels):
        p = channels[name]
        h.update(name.encode())
        if p is None:
            h.update(b'\xff')  # sentinel for "remove"
        elif p.exists():
            with contextlib.suppress(OSError):
                h.update(struct.pack('<Q', int(p.stat().st_mtime * 1e9)))
    cache_key = h.hexdigest()[:16]

    out_dir = cache_dir / 'orm_composites'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{cache_key}.ktx2'

    if out_path.exists():
        log_buffer.log('TexPackTrace', f'ORM compositor cache hit: output={out_path.name}')
        return str(out_path)

    # ── Decode baseline or synthesise defaults ───────────────────────────────
    width = height = 512
    rgba = None

    if baseline and baseline.exists():
        try:
            log_buffer.log('TexPackTrace', f'ORM compositor decoding baseline={baseline.name}')
            rgba, width, height = _decode_texture_rgba(baseline)
            log_buffer.log('TexPackTrace', f'ORM compositor baseline decoded: {width}x{height}')
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('ORM', f'Baseline decode failed ({baseline.name}): {exc}')
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor baseline decode failed: baseline={baseline.name} error={exc}',
            )

    if rgba is None:
        # Default: non-metallic (R=0), fully-rough (G=255), no-emissive (B=0),
        # neutral height (A=128).
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 1] = 255
        rgba[:, :, 3] = 128
        log_buffer.log(
            'TexPackTrace',
            f'ORM compositor using synthesized default baseline: {width}x{height}',
        )

    # ── Apply per-channel PNG overrides ─────────────────────────────────────
    applied: list[str] = []
    for ch_name, png_path in channels.items():
        ch_idx = _CHANNEL_MAP.get(ch_name.lower())
        if ch_idx is None:
            log_buffer.log('ORM', f'Unknown channel "{ch_name}" — valid: {list(_CHANNEL_MAP)}')
            continue
        if png_path is None:
            # "Remove" / zero-out: set channel to its neutral default value.
            rgba[:, :, ch_idx] = _CHANNEL_ZERO.get(ch_name.lower(), 0)
            applied.append(f'{ch_name}=zero')
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor channel {ch_name}: remove/default value applied',
            )
            continue
        if not png_path.exists():
            log_buffer.log('ORM', f'Channel PNG not found: {png_path}')
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor channel {ch_name}: missing file={png_path}',
            )
            continue
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            img = Image.open(png_path)
            # Extract R-channel.  For grayscale images, the only channel IS R.
            if img.mode == 'L':
                r_arr = np.array(img, dtype=np.uint8)
            else:
                r_arr = np.array(img.getchannel('R'), dtype=np.uint8)
            if r_arr.shape != (height, width):
                r_arr = np.array(
                    Image.fromarray(r_arr).resize((width, height), Image.Resampling.BILINEAR),
                    dtype=np.uint8,
                )
                log_buffer.log(
                    'TexPackTrace',
                    f'ORM compositor channel {ch_name}: resized source to {width}x{height} file={png_path.name}',  # ruff: ignore[line-too-long]
                )
            rgba[:, :, ch_idx] = r_arr
            applied.append(ch_name)
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor channel {ch_name}: applied file={png_path.name}',
            )
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('ORM', f'Failed to apply channel "{ch_name}": {exc}')
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor channel {ch_name}: failed file={png_path.name} error={exc}',
            )

    if not applied:
        log_buffer.log('ORM', 'No channels were applied — skipping composite')
        return None

    # Prefer a replacement Normal map when one exists; otherwise use the
    # original captured Normal slot. Roughness mip filtering can still fall
    # back to ordinary linear filtering when neither input is available.
    normal_rgba = None
    normal_path = normal_source if normal_source and normal_source.exists() else normal_baseline
    if normal_path and normal_path.exists():
        try:
            normal_rgba, normal_width, normal_height = _decode_texture_rgba(normal_path)
            if (normal_width, normal_height) != (width, height):
                normal_rgba = np.array(
                    Image.fromarray(normal_rgba, 'RGBA').resize(
                        (width, height),
                        Image.Resampling.BILINEAR,
                    ),
                    dtype=np.uint8,
                )
            log_buffer.log(
                'TexPackTrace',
                f'ORM compositor roughness variance source={normal_path.name}',
            )
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('ORM', f'Normal decode failed ({normal_path.name}): {exc}')
            normal_rgba = None

    # ── Write uncompressed RGBA32 KTX2 ──────────────────────────────────────
    try:
        _write_ktx2(rgba, width, height, out_path, normal_rgba=normal_rgba)
        log_buffer.log(
            'ORM',
            f'Composited [{", ".join(applied)}] → {out_path.name} ({width}×{height})',  # ruff: ignore[ambiguous-unicode-character-string]
        )
        return str(out_path)
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('ORM', f'KTX2 write failed: {exc}')
        with contextlib.suppress(OSError):
            out_path.unlink(missing_ok=True)
        return None


# ── Internal helpers ─────────────────────────────────────────────────────────


def _decode_bc_ktx2(data: bytes) -> tuple[np.ndarray, int, int]:
    """Decode the base level of a BC1/BC3 KTX2 to RGBA8.

    The selected base-level payload may be Zstd-compressed
    (supercompressionScheme=2) or uncompressed (supercompressionScheme=0).
    Additional mip levels in the container are ignored here.
    """

    if len(data) < 96:  # ruff: ignore[magic-value-comparison]
        msg = 'KTX2 data too short'
        raise ValueError(msg)

    vk_fmt = struct.unpack_from('<I', data, 12)[0]
    width = struct.unpack_from('<I', data, 20)[0]
    height = struct.unpack_from('<I', data, 24)[0]
    supercompression = struct.unpack_from('<I', data, 44)[0]

    if width == 0 or height == 0:
        msg = f'Invalid KTX2 dimensions {width}×{height}'  # ruff: ignore[ambiguous-unicode-character-string]
        raise ValueError(msg)

    # Level-index entry 0 is always at offset 80 (fixed KTX2 header size).
    byte_offset = struct.unpack_from('<Q', data, 80)[0]
    byte_length = struct.unpack_from('<Q', data, 88)[0]
    level_data = data[byte_offset : byte_offset + byte_length]

    if supercompression == 2:  # zstd  # ruff: ignore[magic-value-comparison]
        import zstandard  # ruff: ignore[import-outside-top-level]

        level_data = zstandard.ZstdDecompressor().decompress(
            level_data,
            max_output_size=64 * 1024 * 1024,
        )
    elif supercompression != 0:
        msg = f'Unsupported KTX2 supercompressionScheme {supercompression}'
        raise ValueError(msg)

    if vk_fmt == _VK_BC1:
        fourcc = b'DXT1'
    elif vk_fmt == _VK_BC3:
        fourcc = b'DXT5'
    else:
        msg = f'Unsupported vkFormat {vk_fmt} (need BC1=131 or BC3=137)'
        raise ValueError(msg)

    def _u32(v: int) -> bytes:
        return struct.pack('<I', v)

    # Minimal DDS header (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH |
    #                     DDSD_PIXELFORMAT | DDSD_LINEARSIZE = 0x1007)
    dds = (
        b'DDS '
        + _u32(124)
        + _u32(0x1007)
        + _u32(height)
        + _u32(width)
        + _u32(len(level_data))
        + _u32(0)
        + _u32(1)
        + b'\x00' * 44
        + _u32(32)
        + _u32(4)
        + fourcc
        + _u32(0)
        + _u32(0)
        + _u32(0)
        + _u32(0)
        + _u32(0)
        + _u32(0x1000)
        + _u32(0)
        + _u32(0)
        + _u32(0)
        + _u32(0)
    )
    img = Image.open(io.BytesIO(dds + level_data)).convert('RGBA')
    return np.array(img, dtype=np.uint8), width, height


def _decode_texture_rgba(path: Path) -> tuple[np.ndarray, int, int]:
    """Decode a normal image or supported KTX/KTX2 base level to RGBA8."""

    data = path.read_bytes()
    from .ktx_to_png import convert  # ruff: ignore[import-outside-top-level]
    from .ktx_to_png.ktx_to_png import (  # ruff: ignore[import-outside-top-level]
        KTX2_MAGIC,
        strip_prefixed_ktx,
    )

    stripped = strip_prefixed_ktx(data)
    if stripped is not None and stripped[:12] == KTX2_MAGIC:
        try:
            return _decode_bc_ktx2(stripped)
        except ValueError:
            pass

    if stripped is not None:
        png = convert(stripped)
        if png is None:
            msg = f'Unsupported KTX texture: {path.name}'
            raise ValueError(msg)
        img = Image.open(io.BytesIO(png)).convert('RGBA')
    else:
        img = Image.open(io.BytesIO(data)).convert('RGBA')

    width, height = img.size
    return np.array(img, dtype=np.uint8), width, height


def _resize_float_channel(values: np.ndarray, width: int, height: int) -> np.ndarray:
    image = Image.fromarray(values.astype(np.float32, copy=False), 'F')
    return np.array(
        image.resize((width, height), Image.Resampling.BOX),
        dtype=np.float32,
        copy=True,
    )


def _resize_float_channel_bilinear(
    values: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    image = Image.fromarray(values.astype(np.float32, copy=False), 'F')
    return np.array(
        image.resize((width, height), Image.Resampling.BILINEAR),
        dtype=np.float32,
        copy=True,
    )


def _round_u8(values: np.ndarray) -> np.ndarray:
    values = np.rint(values)
    return np.clip(values, 0, 255).astype(np.uint8)


def _normal_vectors_from_rgba(normal_rgba: np.ndarray) -> np.ndarray:
    """Return unit tangent-space XYZ vectors from RGB or Roblox DXT5nm data."""

    rgba = normal_rgba.astype(np.float32)
    red = rgba[:, :, 0]
    green = rgba[:, :, 1]
    blue = rgba[:, :, 2]
    alpha = rgba[:, :, 3]

    # Native-Windows Roblox BC3 normal maps use DXT5nm-style packing:
    # R≈255, B≈0, G=Y, A=X. Sober ETC2 and user PNG normals use RGB XYZ.
    is_dxt5nm = (
        float(red.mean()) > 245.0  # ruff: ignore[magic-value-comparison]
        and float(blue.mean()) < 10.0  # ruff: ignore[magic-value-comparison]
        and float(red.std()) < 12.0  # ruff: ignore[magic-value-comparison]
        and float(blue.std()) < 12.0  # ruff: ignore[magic-value-comparison]
        and float(alpha.std()) > 0.5  # ruff: ignore[magic-value-comparison]
    )
    if is_dxt5nm:
        x = alpha / 127.5 - 1.0
        y = green / 127.5 - 1.0
        z = np.sqrt(np.clip(1.0 - x * x - y * y, 0.0, 1.0))
        vectors = np.stack((x, y, z), axis=2)
    else:
        vectors = rgba[:, :, :3] / 127.5 - 1.0

    lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
    valid = lengths > np.finfo(np.float32).eps
    np.divide(vectors, lengths, out=vectors, where=valid)
    invalid = ~valid[:, :, 0]
    if np.any(invalid):
        vectors[invalid] = (0.0, 0.0, 1.0)
    return vectors


def _resample_normal_vectors(
    base_vectors: np.ndarray,
    width: int,
    height: int,
) -> np.ndarray:
    """Bilinearly resample base normal vectors and renormalize the result."""

    vectors = np.stack(
        [
            _resize_float_channel_bilinear(base_vectors[:, :, component], width, height)
            for component in range(3)
        ],
        axis=2,
    )
    lengths = np.linalg.norm(vectors, axis=2, keepdims=True)
    valid = lengths > np.finfo(np.float32).eps
    np.divide(vectors, lengths, out=vectors, where=valid)
    invalid = ~valid[:, :, 0]
    if np.any(invalid):
        vectors[invalid] = (0.0, 0.0, 1.0)
    return vectors


def _downsample_linear_rgba(rgba: np.ndarray, width: int, height: int) -> np.ndarray:
    output = np.empty((height, width, 4), dtype=np.uint8)
    for channel_index in range(4):
        output[:, :, channel_index] = _round_u8(
            _resize_float_channel(rgba[:, :, channel_index], width, height)
        )
    return output


def generate_orm_mip_chain(  # ruff: ignore[too-many-locals]
    rgba: np.ndarray,
    normal_rgba: np.ndarray | None = None,
    *,
    roughness_variance_scale: float = _ROUGHNESS_NORMAL_VARIANCE_SCALE,
) -> list[bytes]:
    """Generate Roblox-style packed material mips.

    Metalness, emissive and height are sequentially BOX-filtered. Roughness is
    accumulated in squared/perceptual-roughness space and each transition adds
    the variance of the corresponding current normal level. Normal levels used
    for the next transition are bilinearly resampled from the base and
    renormalized, matching Fleasion's standalone Normal mip path.
    """

    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:  # ruff: ignore[magic-value-comparison]
        msg = 'ORM base must be an HxWx4 uint8 array'
        raise ValueError(msg)
    height, width, _ = rgba.shape
    if width <= 0 or height <= 0:
        msg = f'invalid ORM dimensions {width}x{height}'
        raise ValueError(msg)
    if roughness_variance_scale < 0:
        msg = 'roughness variance scale must be non-negative'
        raise ValueError(msg)
    if normal_rgba is not None:
        if normal_rgba.shape != rgba.shape or normal_rgba.dtype != np.uint8:
            msg = 'Normal base must match ORM dimensions and be uint8 RGBA'
            raise ValueError(msg)
        base_normals = _normal_vectors_from_rgba(normal_rgba)
        current_normals = base_normals
    else:
        base_normals = None
        current_normals = None

    current = rgba.copy()
    levels = [current.tobytes()]
    current_width = width
    current_height = height

    while current_width > 1 or current_height > 1:
        next_width = max(1, current_width // 2)
        next_height = max(1, current_height // 2)
        next_rgba = _downsample_linear_rgba(current, next_width, next_height)

        current_roughness = current[:, :, 1].astype(np.float32) / 255.0
        mean_roughness_sq = _resize_float_channel(
            current_roughness * current_roughness,
            next_width,
            next_height,
        )
        variance: float | np.ndarray = 0.0
        if current_normals is not None:
            mean_components = [
                _resize_float_channel(current_normals[:, :, component], next_width, next_height)
                for component in range(3)
            ]
            mean_length_sq = sum(component * component for component in mean_components)
            variance = np.clip(1.0 - mean_length_sq, 0.0, 1.0)

        roughness_sq = np.clip(
            mean_roughness_sq + roughness_variance_scale * variance,
            0.0,
            1.0,
        )
        next_rgba[:, :, 1] = _round_u8(np.sqrt(roughness_sq) * 255.0)
        levels.append(next_rgba.tobytes())
        current = next_rgba
        if base_normals is not None:
            current_normals = _resample_normal_vectors(base_normals, next_width, next_height)
        current_width = next_width
        current_height = next_height

    return levels


def _write_ktx2(
    rgba: np.ndarray,
    width: int,
    height: int,
    out_path: Path,
    *,
    normal_rgba: np.ndarray | None = None,
) -> None:
    """Write packed material RGBA32 as a full-mip uncompressed KTX2."""
    from .rgba_ktx2 import write_rgba8_ktx2_levels  # ruff: ignore[import-outside-top-level]

    expected_shape = (height, width, 4)
    if rgba.shape != expected_shape or rgba.dtype != np.uint8:
        msg = f'RGBA buffer shape mismatch: {rgba.shape} != {expected_shape}'
        raise ValueError(msg)

    levels = generate_orm_mip_chain(rgba, normal_rgba)
    write_rgba8_ktx2_levels(levels, width, height, out_path)
