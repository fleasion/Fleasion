"""ORM slot KTX2 compositor for per-channel PNG replacement.

Reads a baseline ORM KTX2 (BC1 or BC3, zstd-compressed) captured from the
Roblox CDN, substitutes individual channels with R-channel values from
user-supplied PNG files, and writes an uncompressed RGBA32 KTX2 which Roblox
accepts as a TexturePack slot replacement.

Channel map (empirically confirmed):
  R = Metalness  (R-channel of source PNG, 0 = non-metallic)
  G = Roughness  (R-channel of source PNG, 255 = fully rough)
  B = Emissive   (R-channel of source PNG, 0 = none)
  A = Height     (R-channel of source PNG, 128 = neutral; BC3 only)
"""

from __future__ import annotations

import hashlib
import io
import struct
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ...utils import log_buffer
from ...utils.paths import APP_CACHE_DIR

_CHANNEL_MAP: dict[str, int] = {
    'metalness': 0,
    'roughness': 1,
    'emissive':  2,
    'height':    3,
}
# Values used when a channel is explicitly "removed" (set to None path).
# metalness=0 (non-metallic), roughness=0 (fully smooth), emissive=0 (off),
# height=128 (neutral displacement).
_CHANNEL_ZERO: dict[str, int] = {
    'metalness': 0,
    'roughness': 0,
    'emissive':  0,
    'height':  128,
}

_VK_BC1 = 131  # VK_FORMAT_BC1_RGB_UNORM_BLOCK  (DXT1, no alpha)
_VK_BC3 = 137  # VK_FORMAT_BC3_UNORM_BLOCK       (DXT5, with alpha)


def composite_orm(
    baseline: Optional[Path],
    channels: dict[str, Optional[Path]],
    cache_dir: Path = APP_CACHE_DIR,
) -> Optional[str]:
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

    Returns
    -------
    str or None
        Absolute path to the output ``.ktx2`` file, or ``None`` on failure.
    """

    # ── Cache key: baseline mtime + channel PNG mtimes (None entries = sentinel 0xFF) ─
    h = hashlib.md5()
    if baseline and baseline.exists():
        h.update(baseline.name.encode())
        try:
            h.update(struct.pack('<Q', int(baseline.stat().st_mtime * 1e9)))
        except OSError:
            pass
    for name in sorted(channels):
        p = channels[name]
        h.update(name.encode())
        if p is None:
            h.update(b'\xff')  # sentinel for "remove"
        elif p.exists():
            try:
                h.update(struct.pack('<Q', int(p.stat().st_mtime * 1e9)))
            except OSError:
                pass
    cache_key = h.hexdigest()[:16]

    out_dir = cache_dir / 'orm_composites'
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f'{cache_key}.ktx2'

    if out_path.exists():
        return str(out_path)

    # ── Decode baseline or synthesise defaults ───────────────────────────────
    width = height = 512
    rgba = None

    if baseline and baseline.exists():
        try:
            rgba, width, height = _decode_bc_ktx2(baseline.read_bytes())
        except Exception as exc:
            log_buffer.log('ORM', f'Baseline decode failed ({baseline.name}): {exc}')

    if rgba is None:
        # Default: non-metallic (R=0), fully-rough (G=255), no-emissive (B=0),
        # neutral height (A=128).
        rgba = np.zeros((height, width, 4), dtype=np.uint8)
        rgba[:, :, 1] = 255
        rgba[:, :, 3] = 128

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
            continue
        if not png_path.exists():
            log_buffer.log('ORM', f'Channel PNG not found: {png_path}')
            continue
        try:
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
            rgba[:, :, ch_idx] = r_arr
            applied.append(ch_name)
        except Exception as exc:
            log_buffer.log('ORM', f'Failed to apply channel "{ch_name}": {exc}')

    if not applied:
        log_buffer.log('ORM', 'No channels were applied — skipping composite')
        return None

    # ── Write uncompressed RGBA32 KTX2 ──────────────────────────────────────
    try:
        _write_ktx2(rgba, width, height, out_path)
        log_buffer.log('ORM', f'Composited [{", ".join(applied)}] → {out_path.name} ({width}×{height})')
        return str(out_path)
    except Exception as exc:
        log_buffer.log('ORM', f'KTX2 write failed: {exc}')
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


# ── Internal helpers ─────────────────────────────────────────────────────────

def _decode_bc_ktx2(data: bytes) -> tuple[np.ndarray, int, int]:
    """Decode a BC1/BC3 KTX2 file to ``(rgba_ndarray, width, height)``.

    The KTX2 level data may be zstd-compressed (supercompressionScheme=2) or
    uncompressed (supercompressionScheme=0).  Only single-mip files are
    supported (which is always the case for Roblox TexturePack slots).
    """

    if len(data) < 96:
        raise ValueError('KTX2 data too short')

    vk_fmt          = struct.unpack_from('<I', data, 12)[0]
    width           = struct.unpack_from('<I', data, 20)[0]
    height          = struct.unpack_from('<I', data, 24)[0]
    supercompression = struct.unpack_from('<I', data, 44)[0]

    if width == 0 or height == 0:
        raise ValueError(f'Invalid KTX2 dimensions {width}×{height}')

    # Level-index entry 0 is always at offset 80 (fixed KTX2 header size).
    byte_offset = struct.unpack_from('<Q', data, 80)[0]
    byte_length  = struct.unpack_from('<Q', data, 88)[0]
    level_data   = data[byte_offset: byte_offset + byte_length]

    if supercompression == 2:        # zstd
        import zstandard
        level_data = zstandard.ZstdDecompressor().decompress(
            level_data, max_output_size=64 * 1024 * 1024,
        )
    elif supercompression != 0:
        raise ValueError(f'Unsupported KTX2 supercompressionScheme {supercompression}')

    if vk_fmt == _VK_BC1:
        fourcc = b'DXT1'
    elif vk_fmt == _VK_BC3:
        fourcc = b'DXT5'
    else:
        raise ValueError(f'Unsupported vkFormat {vk_fmt} (need BC1=131 or BC3=137)')

    def _u32(v: int) -> bytes:
        return struct.pack('<I', v)

    # Minimal DDS header (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH |
    #                     DDSD_PIXELFORMAT | DDSD_LINEARSIZE = 0x1007)
    dds = (
        b'DDS '
        + _u32(124) + _u32(0x1007) + _u32(height) + _u32(width)
        + _u32(len(level_data)) + _u32(0) + _u32(1) + b'\x00' * 44
        + _u32(32) + _u32(4) + fourcc
        + _u32(0) + _u32(0) + _u32(0) + _u32(0) + _u32(0)
        + _u32(0x1000) + _u32(0) + _u32(0) + _u32(0) + _u32(0)
    )
    img = Image.open(io.BytesIO(dds + level_data)).convert('RGBA')
    return np.array(img, dtype=np.uint8), width, height


def _write_ktx2(rgba: np.ndarray, width: int, height: int, out_path: Path) -> None:
    """Write a numpy RGBA32 array as an uncompressed VK_FORMAT_R8G8B8A8_UNORM KTX2."""
    import ctypes
    from .image_to_ktx2.converter import _get_ktx_dll, ktxTextureCreateInfo

    dll = _get_ktx_dll()
    if dll is None:
        raise RuntimeError('ktx.dll not available — cannot write KTX2')

    rgba_bytes = rgba.tobytes()  # type: ignore[union-attr]
    expected = width * height * 4
    if len(rgba_bytes) != expected:
        raise ValueError(f'RGBA buffer size mismatch: {len(rgba_bytes)} != {expected}')

    info = ktxTextureCreateInfo()
    info.glInternalformat = 0
    info.vkFormat         = 37      # VK_FORMAT_R8G8B8A8_UNORM
    info.pDfd             = None
    info.baseWidth        = width
    info.baseHeight       = height
    info.baseDepth        = 1
    info.numDimensions    = 2
    info.numLevels        = 1
    info.numLayers        = 1
    info.numFaces         = 1
    info.isArray          = 0
    info.generateMipmaps  = 0

    texture = ctypes.c_void_p()
    err = dll.ktxTexture2_Create(ctypes.byref(info), 1, ctypes.byref(texture))
    if err != 0 or not texture.value:
        raise RuntimeError(f'ktxTexture2_Create failed (err={err})')

    tex_ptr = texture.value
    try:
        # pData pointer is at struct offset +112 (verified for libktx 4.x 64-bit Windows).
        pdata_ptr = ctypes.c_uint64.from_address(tex_ptr + 112).value
        if pdata_ptr == 0:
            raise RuntimeError('ktxTexture2 pData is NULL')
        ctypes.memmove(
            (ctypes.c_uint8 * expected).from_address(pdata_ptr),
            rgba_bytes,
            expected,
        )
        err = dll.ktxTexture2_WriteToNamedFile(texture, str(out_path).encode('utf-8'))
        if err != 0:
            raise RuntimeError(f'ktxTexture2_WriteToNamedFile failed (err={err})')
    finally:
        try:
            dll.ktxTexture2_Destroy(tex_ptr)
        except Exception:
            pass
