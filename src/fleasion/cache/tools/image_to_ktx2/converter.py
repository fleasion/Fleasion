import hashlib
import io
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]

from PIL import Image

from fleasion.cache.tools.rgba_ktx2 import (
    RGBA8_KTX2_CACHE_VERSION,
    MipmapMode,
    write_rgba8_ktx2,
)
from fleasion.utils import log_buffer
from fleasion.utils.paths import APP_CACHE_DIR


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
    except Exception as e:  # ruff: ignore[blind-except]
        log_buffer.log('Proxy', f'image_to_ktx2: failed to read file {image_path}: {e}')
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

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        # Load image via Pillow (supports PNG, JPG, WebP, etc.)
        img = Image.open(io.BytesIO(original_bytes))
        if img.mode != 'RGBA':
            img = img.convert('RGBA')

        width, height = img.size
        rgba_bytes = img.tobytes()
        expected_size = width * height * 4
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 convert start: input={image_path.name} mode={img.mode} size={width}x{height} bytes={original_size}',
        )

        if len(rgba_bytes) != expected_size:
            log_buffer.log(
                'Proxy',
                f'image_to_ktx2: size mismatch {len(rgba_bytes)} vs {expected_size}',
            )
            log_buffer.log(
                'TexPackTrace',
                f'image_to_ktx2 size mismatch: input={image_path.name} rgba={len(rgba_bytes)} expected={expected_size}',
            )
            return image_path

        write_rgba8_ktx2(
            rgba_bytes,
            width,
            height,
            ktx2_path,
            mipmap_mode=mipmap_mode,
        )

        # Log completion and file sizes
        try:
            ktx2_size = ktx2_path.stat().st_size
            log_buffer.log(
                'Proxy',
                f'Converted {image_path.name} -> KTX2 (Original: {original_size:,} bytes | KTX2: {ktx2_size:,} bytes)',
            )
            log_buffer.log(
                'TexPackTrace',
                f'image_to_ktx2 convert complete: input={image_path.name} output={ktx2_path.name} bytes={ktx2_size}',
            )
        except Exception:  # ruff: ignore[blind-except, try-except-pass]
            pass

        return ktx2_path  # ruff: ignore[try-consider-else]

    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Proxy', f'image_to_ktx2: Exception during conversion: {exc}')
        log_buffer.log(
            'TexPackTrace',
            f'image_to_ktx2 convert failed: input={image_path.name} error={exc}',
        )
        return image_path
