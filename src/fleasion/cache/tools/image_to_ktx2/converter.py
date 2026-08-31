import hashlib
import io
from typing import TYPE_CHECKING

from PIL import Image

from fleasion.cache.tools.rgba_ktx2 import (
    RGBA8_KTX2_CACHE_VERSION,
    MipmapMode,
    write_rgba8_ktx2,
)
from fleasion.utils import log_buffer
from fleasion.utils.paths import APP_CACHE_DIR

if TYPE_CHECKING:
    from pathlib import Path


def _convert_image_bytes(
    original_bytes: bytes,
    image_path: Path,
    ktx2_path: Path,
    *,
    mipmap_mode: MipmapMode,
) -> bool:
    try:
        with Image.open(io.BytesIO(original_bytes)) as source:
            image = source.convert('RGBA') if source.mode != 'RGBA' else source.copy()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: failed to decode {image_path}: {exc}')
        return False

    width, height = image.size
    rgba_bytes = image.tobytes()
    expected_size = width * height * 4
    log_buffer.log(
        'TexPackTrace',
        f'image_to_ktx2 convert start: input={image_path.name} mode={image.mode} '
        f'size={width}x{height} bytes={len(original_bytes)}',
    )
    if len(rgba_bytes) != expected_size:
        log_buffer.log(
            'Proxy',
            f'image_to_ktx2: size mismatch {len(rgba_bytes)} vs {expected_size}',
        )
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 size mismatch: input={image_path.name} '
            f'rgba={len(rgba_bytes)} expected={expected_size}',
        )
        return False

    try:
        write_rgba8_ktx2(
            rgba_bytes,
            width,
            height,
            ktx2_path,
            mipmap_mode=mipmap_mode,
        )
    except (OSError, OverflowError, ValueError) as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: conversion failed for {image_path}: {exc}')
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 convert failed: input={image_path.name} error={exc}',
        )
        return False
    return True


def get_or_create_ktx2_from_image(
    image_path: Path,
    *,
    mipmap_mode: MipmapMode = 'color',
) -> Path:
    """
    Given a local path to an image (.png, .jpg, etc.), converts it to an uncompressed
    KTX2 texture (VK_FORMAT_R8G8B8A8_UNORM) keeping the original quality. Will cache
    the converted output using an MD5 hash.

    Returns the Path to the generated .ktx2 file. If anything fails, it returns the
    original image_path.
    """
    if not image_path.exists():
        return image_path

    # Read the file and calculate quick hash for caching
    try:
        original_bytes = image_path.read_bytes()
    except OSError as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: failed to read file {image_path}: {exc}')
        return image_path

    original_size = len(original_bytes)
    h = hashlib.md5(
        original_bytes + RGBA8_KTX2_CACHE_VERSION + mipmap_mode.encode('ascii'),
        usedforsecurity=False,
    ).hexdigest()[:16]

    ktx2_path = APP_CACHE_DIR / f'{image_path.stem}_{h}.ktx2'
    if ktx2_path.exists():
        # Already converted before
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 cache hit: input={image_path.name} output={ktx2_path.name}',
        )
        return ktx2_path

    if not _convert_image_bytes(
        original_bytes,
        image_path,
        ktx2_path,
        mipmap_mode=mipmap_mode,
    ):
        return image_path

    try:
        ktx2_size = ktx2_path.stat().st_size
    except OSError:
        ktx2_size = None
    if ktx2_size is not None:
        log_buffer.log(
            'Proxy',
            f'Converted {image_path.name} -> KTX2 '
            f'(Original: {original_size:,} bytes | KTX2: {ktx2_size:,} bytes)',
        )
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 convert complete: input={image_path.name} '
            f'output={ktx2_path.name} bytes={ktx2_size}',
        )
    return ktx2_path
