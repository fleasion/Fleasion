"""Background threads that turn cached asset bytes into something a preview can show."""

from __future__ import annotations

import contextlib
import gzip as gzip_module
import io
import tempfile
import zlib
from pathlib import Path

from PySide6.QtCore import QThread, Signal
lazy from PIL import Image

from fleasion.localization import tr
from fleasion.utils import log_buffer

from . import mesh_processing
lazy from .tools.ktx_to_png import convert as ktx_convert, strip_prefixed_ktx
lazy from .tools.solidmodel_converter.converter import deserialize_rbxm, export_obj_from_doc


class ImageLoaderThread(QThread):
    """Worker thread for loading and processing image bytes."""

    image_ready = Signal(bytes, int, int)
    error = Signal(str)

    def __init__(self, data: bytes) -> None:
        super().__init__()
        self.data = data
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _load_rgba(self) -> tuple[bytes, int, int] | None:
        log_buffer.log('Preview', f'Loading image ({len(self.data)} bytes)')
        data = self.data

        ktx_payload = strip_prefixed_ktx(data)
        if ktx_payload is not None:
            log_buffer.log('Preview', 'KTX detected, converting to PNG...')
            data = ktx_convert(ktx_payload)
            if data is None:
                if not self._stop_requested:
                    self.error.emit(tr('cache.preview.ktx_conversion_failed'))
                return None

        image = Image.open(io.BytesIO(data))
        if self._stop_requested:
            return None
        if image.mode not in {'RGB', 'RGBA'} or image.mode == 'RGB':
            image = image.convert('RGBA')
        if self._stop_requested:
            return None

        return image.tobytes(), image.width, image.height

    def run(self) -> None:
        try:
            result = self._load_rgba()
        except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Image load error: {exc}')
                self.error.emit(str(exc))
            return
        if result is not None and not self._stop_requested:
            rgba, width, height = result
            log_buffer.log('Preview', f'Image loaded: {width}x{height}')
            self.image_ready.emit(rgba, width, height)


class MeshLoaderThread(QThread):
    """Worker thread for loading and converting meshes."""

    mesh_ready = Signal(str)  # OBJ content
    error = Signal(str)

    def __init__(self, data: bytes, asset_id: str = '') -> None:
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _convert_mesh(self) -> str | None:
        log_buffer.log('Preview', f'Loading mesh {self.asset_id} ({len(self.data)} bytes)')
        decompressed = self.data
        if self.data.startswith(b'\x1f\x8b'):
            decompressed = gzip_module.decompress(self.data)
            log_buffer.log('Preview', f'Decompressed mesh: {len(decompressed)} bytes')
        if self._stop_requested:
            return None
        return mesh_processing.convert(decompressed)

    def run(self) -> None:
        try:
            obj_content = self._convert_mesh()
        except (EOFError, OSError, RuntimeError, TypeError, ValueError, zlib.error) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'Mesh conversion error: {exc}')
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            log_buffer.log('Preview', 'Mesh converted successfully')
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('cache.preview.mesh_conversion_failed'))


class SolidModelLoaderThread(QThread):
    """Worker thread for loading and converting solid models (CSG)."""

    mesh_ready = Signal(str)  # OBJ content
    error = Signal(str)

    def __init__(self, data: bytes, asset_id: str = '') -> None:
        super().__init__()
        self.data = data
        self.asset_id = asset_id
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def _convert_solid_model(self) -> str | None:
        log_buffer.log(
            'Preview',
            f'Loading SolidModel {self.asset_id} ({len(self.data)} bytes)',
        )
        if self._stop_requested:
            return None

        decompressed = self.data
        if self.data.startswith(b'\x1f\x8b'):
            decompressed = gzip_module.decompress(self.data)
            log_buffer.log('Preview', f'Decompressed SolidModel: {len(decompressed)} bytes')

        doc = deserialize_rbxm(decompressed)
        with tempfile.NamedTemporaryFile(suffix='.obj', delete=False) as f:
            temp_obj_path = Path(f.name)
        try:
            export_obj_from_doc(doc, temp_obj_path, decompose=False)
            return temp_obj_path.read_text(encoding='utf-8')
        finally:
            with contextlib.suppress(OSError):
                temp_obj_path.unlink(missing_ok=True)

    def run(self) -> None:
        try:
            obj_content = self._convert_solid_model()
        except (EOFError, ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
            if not self._stop_requested:
                log_buffer.log('Preview', f'SolidModel conversion error: {exc}')
                self.error.emit(str(exc))
            return
        if self._stop_requested:
            return
        if obj_content:
            log_buffer.log('Preview', 'SolidModel converted successfully')
            self.mesh_ready.emit(obj_content)
        else:
            self.error.emit(tr('cache.preview.solidmodel_conversion_failed'))
