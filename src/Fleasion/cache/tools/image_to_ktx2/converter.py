import ctypes
import os
import io
import hashlib
from pathlib import Path
from PIL import Image

from ....utils.paths import APP_CACHE_DIR
from ....utils import log_buffer
from ..ktx_to_png.ktx_to_png import _find_ktx_dll

_ktx_dll = None
_ktx_dll_loaded = False


class ktxTextureCreateInfo(ctypes.Structure):
    _fields_ = [
        ('glInternalformat', ctypes.c_uint32),
        ('vkFormat', ctypes.c_uint32),
        ('pDfd', ctypes.POINTER(ctypes.c_uint32)),
        ('baseWidth', ctypes.c_uint32),
        ('baseHeight', ctypes.c_uint32),
        ('baseDepth', ctypes.c_uint32),
        ('numDimensions', ctypes.c_uint32),
        ('numLevels', ctypes.c_uint32),
        ('numLayers', ctypes.c_uint32),
        ('numFaces', ctypes.c_uint32),
        ('isArray', ctypes.c_uint8),
        ('generateMipmaps', ctypes.c_uint8),
    ]


def _get_ktx_dll():
    global _ktx_dll, _ktx_dll_loaded
    if _ktx_dll_loaded:
        return _ktx_dll
    _ktx_dll_loaded = True

    dll_path = _find_ktx_dll()
    if not dll_path:
        log_buffer.log('Proxy', 'image_to_ktx2: ktx.dll not found, cannot convert image to KTX2.')
        return None

    try:
        dll = ctypes.CDLL(dll_path)
    except Exception as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: failed to load ktx.dll: {exc}')
        return None

    try:
        dll.ktxTexture2_Create.restype = ctypes.c_int
        dll.ktxTexture2_Create.argtypes = [
            ctypes.POINTER(ktxTextureCreateInfo), 
            ctypes.c_uint, 
            ctypes.POINTER(ctypes.c_void_p)
        ]
        
        dll.ktxTexture2_WriteToNamedFile.restype = ctypes.c_int
        dll.ktxTexture2_WriteToNamedFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

        dll.ktxTexture2_Destroy.restype = None
        dll.ktxTexture2_Destroy.argtypes = [ctypes.c_void_p]
    except Exception as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: ktx.dll symbol setup failed: {exc}')
        return None

    _ktx_dll = dll
    return dll


def get_or_create_ktx2_from_image(image_path: Path) -> Path:
    """
    Given a local path to an image (.png, .jpg, etc.), converts it to an uncompressed 
    KTX2 texture (VK_FORMAT_R8G8B8A8_UNORM) keeping the original quality, using the 
    bundled libktx C-bindings. Will cache the converted output using an MD5 hash.
    
    Returns the Path to the generated .ktx2 file. If anything fails, it returns the 
    original image_path.
    """
    if not image_path.exists():
        return image_path

    # Read the file and calculate quick hash for caching
    try:
        original_bytes = image_path.read_bytes()
    except Exception as e:
        log_buffer.log('Proxy', f'image_to_ktx2: failed to read file {image_path}: {e}')
        return image_path
        
    original_size = len(original_bytes)
    h = hashlib.md5(original_bytes).hexdigest()[:16]
    
    ktx2_path = APP_CACHE_DIR / f"{image_path.stem}_{h}.ktx2"
    if ktx2_path.exists():
        # Already converted before
        return ktx2_path

    dll = _get_ktx_dll()
    if not dll:
        return image_path

    try:
        # Load image via Pillow (supports PNG, JPG, WebP, etc.)
        img = Image.open(io.BytesIO(original_bytes)) if 'io' in globals() else Image.open(image_path)
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
            
        width, height = img.size
        rgba_bytes = img.tobytes()
        expected_size = width * height * 4
        
        if len(rgba_bytes) != expected_size:
            log_buffer.log('Proxy', f'image_to_ktx2: size mismatch {len(rgba_bytes)} vs {expected_size}')
            return image_path

        info = ktxTextureCreateInfo()
        info.glInternalformat = 0
        info.vkFormat = 37 # VK_FORMAT_R8G8B8A8_UNORM
        info.pDfd = None
        info.baseWidth = width
        info.baseHeight = height
        info.baseDepth = 1
        info.numDimensions = 2
        info.numLevels = 1
        info.numLayers = 1
        info.numFaces = 1
        info.isArray = 0
        info.generateMipmaps = 0

        texture = ctypes.c_void_p()
        # 1 = KTX_TEXTURE_CREATE_ALLOC_STORAGE
        err = dll.ktxTexture2_Create(ctypes.byref(info), 1, ctypes.byref(texture))
        if err != 0 or not texture.value:
            log_buffer.log('Proxy', f'image_to_ktx2: ktxTexture2_Create failed (err={err})')
            return image_path
            
        tex_ptr = texture.value
        try:
            # Reusing offset mappings verified for 64-bit windows MSVC libktx 4.x
            # pData is at +112 bytes
            pdata_ptr = ctypes.c_uint64.from_address(tex_ptr + 112).value
            if pdata_ptr == 0:
                log_buffer.log('Proxy', 'image_to_ktx2: pData is NULL')
                return image_path
                
            # Copy pixel data into native memory
            ctypes.memmove((ctypes.c_uint8 * expected_size).from_address(pdata_ptr), rgba_bytes, expected_size)
            
            # Write out to disk
            write_err = dll.ktxTexture2_WriteToNamedFile(texture, str(ktx2_path).encode('utf-8'))
            if write_err != 0:
                log_buffer.log('Proxy', f'image_to_ktx2: KTX2 WriteToNamedFile failed (err={write_err})')
                return image_path
                
        finally:
            dll.ktxTexture2_Destroy(texture)

        # Log completion and file sizes
        try:
            ktx2_size = ktx2_path.stat().st_size
            log_buffer.log('Proxy', f"Converted {image_path.name} -> KTX2 (Original: {original_size:,} bytes | KTX2: {ktx2_size:,} bytes)")
        except Exception:
            pass
            
        return ktx2_path

    except Exception as exc:
        log_buffer.log('Proxy', f'image_to_ktx2: Exception during conversion: {exc}')
        return image_path
