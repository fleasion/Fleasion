"""Reusable rich preview controller for bounded in-memory payloads."""

from __future__ import annotations

import io
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Final

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtQml import QmlElement

from ..cache.cache_manager import CacheManager
from ..cache.roblox_document import classify_roblox_document
from ..utils.clipboard import copy_pixmap_to_clipboard
from .animation_preview import AnimationPreviewApi
from .font_preview import FontPreviewApi
from .json_preview import JsonPreviewApi
from .roblox_document_preview import RobloxDocumentPreviewApi
from .tasks import TaskState
from .texture_pack_preview import TexturePackPreviewApi

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_MAX_PREVIEW_BYTES: Final = 64 * 1024 * 1024
_MAX_TEXT_BYTES: Final = 500_000
_MAX_HEX_BYTES: Final = 4096
_MAX_IMAGE_PIXELS: Final = 64 * 1024 * 1024
_ANIMATION_TYPES: Final = frozenset(
    {24, 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 78}
)
_IMAGE_TYPES: Final = frozenset({1, 2, 11, 12, 13, 18, 22})
_MESH_TYPES: Final = frozenset({4, 39, 40, 75})
_IMAGE_MAGIC: Final = (
    b'\x89PNG\r\n\x1a\n',
    b'\xff\xd8\xff',
    b'GIF87a',
    b'GIF89a',
    b'BM',
)
_KTX_MAGIC: Final = (
    b'\xabKTX 11\xbb\r\n\x1a\n',
    b'\xabKTX 20\xbb\r\n\x1a\n',
)
_FONT_MAGIC: Final = (b'\x00\x01\x00\x00', b'OTTO', b'ttcf', b'wOFF', b'wOF2')
_AUDIO_MAGIC: Final = (b'OggS', b'ID3', b'fLaC')


@dataclass(frozen=True, slots=True)
class PreviewPayload:
    """Bytes and non-sensitive source metadata for one preview."""

    data: bytes
    label: str = ''
    source_value: str = ''
    source_kind: str = ''
    asset_id: str = ''
    asset_type: int = 0
    type_name: str = ''


type PreviewLoader = Callable[[threading.Event], PreviewPayload]


@dataclass(frozen=True, slots=True)
class _PreviewRequest:
    generation: int
    purpose: str
    child_asset_id: str
    message: str
    loader: PreviewLoader


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or suffix == 'GB':
            return f'{size:.0f} {suffix}' if suffix == 'B' else f'{size:.1f} {suffix}'
        size /= 1024
    return '0 B'


def _bounded_decompress(data: bytes) -> bytes:
    if len(data) > _MAX_PREVIEW_BYTES:
        raise ValueError('The preview payload exceeds the 64 MB safety limit.')
    if not data.startswith(b'\x1f\x8b'):
        return data
    import gzip

    with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
        result = stream.read(_MAX_PREVIEW_BYTES + 1)
    if len(result) > _MAX_PREVIEW_BYTES:
        raise ValueError('The compressed preview expands beyond the 64 MB safety limit.')
    return result


def _looks_like_webp(data: bytes) -> bool:
    return len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WEBP'


def _looks_like_wave(data: bytes) -> bool:
    return len(data) >= 12 and data.startswith(b'RIFF') and data[8:12] == b'WAVE'


def _looks_like_mp3(data: bytes) -> bool:
    return data.startswith((b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'))


def _looks_like_animation(data: bytes) -> bool:
    head = data[:2_000_000]
    return b'KeyframeSequence' in head or b'CurveAnimation' in head


def _looks_like_texture_pack(data: bytes) -> bool:
    stripped = data.lstrip()
    if not stripped.startswith((b'<', b'<?xml')):
        return False
    head = stripped[:2_000_000].lower()
    return any(
        marker in head
        for marker in (
            b'<color',
            b'<normal',
            b'<metalness',
            b'<roughness',
            b'<emissive',
        )
    )


def _printable_text(data: bytes) -> str | None:
    if not data or b'\x00' in data[:4096]:
        return None
    try:
        text = data[:_MAX_TEXT_BYTES].decode('utf-8')
    except UnicodeDecodeError:
        return None
    sample = text[:4096]
    if sample and sum(character.isprintable() or character in '\r\n\t' for character in sample) / len(
        sample
    ) < 0.9:
        return None
    return text


def _hex_dump(data: bytes) -> str:
    preview = data[:_MAX_HEX_BYTES]
    lines = [f'Size: {_format_bytes(len(data))}', '']
    for offset in range(0, len(preview), 16):
        chunk = preview[offset : offset + 16]
        hexadecimal = ' '.join(f'{byte:02x}' for byte in chunk)
        ascii_text = ''.join(chr(byte) if 32 <= byte < 127 else '.' for byte in chunk)
        lines.append(f'{offset:08x}  {hexadecimal:<47}  {ascii_text}')
    if len(data) > len(preview):
        lines.extend(('', f'… {_format_bytes(len(data) - len(preview))} more'))
    return '\n'.join(lines)


@QmlElement
class PayloadPreviewApi(QObject):
    """Classify bytes and expose the rich preview contract consumed by QML."""

    previewChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)
    childAssetRequested = Signal(str)

    def __init__(
        self,
        cache_manager: CacheManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache_manager
        self._task = TaskState(self)
        self._font_preview = FontPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._json_preview = JsonPreviewApi(self)
        self._document_preview = RobloxDocumentPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._animation_preview = AnimationPreviewApi(self)  # pyright: ignore[reportCallIssue]
        self._texture_pack_preview = TexturePackPreviewApi(  # pyright: ignore[reportCallIssue]
            cache_manager,
            self,
        )
        export_directory = getattr(cache_manager, 'export_dir', None)
        self._document_preview.set_export_directory(export_directory)
        self._animation_preview.set_export_directory(export_directory)
        self._texture_pack_preview.set_export_directory(export_directory)
        self._mesh_geometry: QObject | None = None
        self._preview_kind = 'none'
        self._preview_text = ''
        self._preview_source = ''
        self._source_label = ''
        self._source_value = ''
        self._source_kind = ''
        self._size_text = ''
        self._error_text = ''
        self._image_bytes = b''
        self._preview_files: set[Path] = set()
        self._generation = 0
        self._pending_request: _PreviewRequest | None = None
        self._active_request: _PreviewRequest | None = None
        self._disposed = False
        self._task.succeeded.connect(self._on_task_succeeded)
        self._task.failed.connect(self._on_task_failed)
        self._document_preview.errorOccurred.connect(self.errorOccurred)
        self._document_preview.notificationRequested.connect(self.notificationRequested)
        self._animation_preview.errorOccurred.connect(self.errorOccurred)
        self._animation_preview.notificationRequested.connect(self.notificationRequested)
        self._texture_pack_preview.errorOccurred.connect(self.errorOccurred)
        self._texture_pack_preview.notificationRequested.connect(self.notificationRequested)
        self._texture_pack_preview.loadRequested.connect(self.childAssetRequested)

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Property(str, notify=previewChanged)
    def previewKind(self) -> str:  # noqa: N802
        return self._preview_kind

    @Property(str, notify=previewChanged)
    def previewText(self) -> str:  # noqa: N802
        return self._preview_text

    @Property(str, notify=previewChanged)
    def previewSource(self) -> str:  # noqa: N802
        return self._preview_source

    @Property(QObject, notify=previewChanged)
    def meshGeometry(self) -> QObject | None:  # noqa: N802
        return self._mesh_geometry

    @Property(QObject, constant=True)
    def fontPreview(self) -> QObject:  # noqa: N802
        return self._font_preview

    @Property(QObject, constant=True)
    def jsonPreview(self) -> QObject:  # noqa: N802
        return self._json_preview

    @Property(QObject, constant=True)
    def documentPreview(self) -> QObject:  # noqa: N802
        return self._document_preview

    @Property(QObject, constant=True)
    def animationPreview(self) -> QObject:  # noqa: N802
        return self._animation_preview

    @Property(QObject, constant=True)
    def texturePackPreview(self) -> QObject:  # noqa: N802
        return self._texture_pack_preview

    @Property(str, notify=previewChanged)
    def sourceLabel(self) -> str:  # noqa: N802
        return self._source_label

    @Property(str, notify=previewChanged)
    def sourceValue(self) -> str:  # noqa: N802
        return self._source_value

    @Property(str, notify=previewChanged)
    def sourceKind(self) -> str:  # noqa: N802
        return self._source_kind

    @Property(str, notify=previewChanged)
    def sizeText(self) -> str:  # noqa: N802
        return self._size_text

    @Property(str, notify=previewChanged)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    @Property(bool, notify=previewChanged)
    def canCopyImage(self) -> bool:  # noqa: N802
        return self._preview_kind == 'image' and bool(self._image_bytes)

    def load_async(self, message: str, loader: PreviewLoader) -> bool:
        """Replace any active load with the newest cancellable payload request."""
        if self._disposed:
            return False
        self._generation += 1
        request = _PreviewRequest(self._generation, 'main', '', message, loader)
        self._pending_request = request
        self._task.cancel()
        self._reset_content()
        self._set_preview('none', '', '', error='')
        self._schedule_pending()
        return True

    def load_child_async(
        self,
        asset_id: str,
        message: str,
        loader: PreviewLoader,
    ) -> bool:
        """Fetch one TexturePack map without replacing the parent preview."""
        normalized = asset_id.strip()
        if (
            self._disposed
            or self._preview_kind != 'texturepack'
            or not normalized.isdecimal()
            or normalized == '0'
        ):
            return False
        request = _PreviewRequest(
            self._generation,
            'child',
            str(int(normalized)),
            message,
            loader,
        )
        self._pending_request = request
        self._task.cancel()
        self._schedule_pending()
        return True

    def load_payload(self, payload: PreviewPayload) -> None:
        """Synchronously apply already-bounded bytes on the owning Qt thread."""
        data = _bounded_decompress(payload.data)
        self._reset_content()
        self._source_label = payload.label
        self._source_value = payload.source_value
        self._source_kind = payload.source_kind
        self._size_text = _format_bytes(len(data))
        self._classify(data, payload)

    @Slot()
    def cancel(self) -> None:
        self._generation += 1
        self._pending_request = None
        self._task.cancel()

    @Slot()
    def clear(self) -> None:
        self.cancel()
        self._reset_content()
        self._source_label = ''
        self._source_value = ''
        self._source_kind = ''
        self._size_text = ''
        self._set_preview('none', '', '', error='')

    @Slot(result=bool)
    def copyImage(self) -> bool:  # noqa: N802
        if not self.canCopyImage:
            return False
        image = QImage.fromData(self._image_bytes)
        if image.isNull() or image.width() * image.height() > _MAX_IMAGE_PIXELS:
            self.errorOccurred.emit('Qt could not safely decode this preview image.')
            return False
        try:
            copy_pixmap_to_clipboard(QPixmap.fromImage(image))
        except RuntimeError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.notificationRequested.emit('Image copied', 'Preview copied to the clipboard', 'success')
        return True

    @Slot()
    def shutdown(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._generation += 1
        self._pending_request = None
        self._task.shutdown()
        self._release_mesh_geometry()
        self._font_preview.shutdown()
        self._json_preview.clear()
        self._document_preview.reset()
        self._animation_preview.shutdown()
        self._texture_pack_preview.clear()
        self._remove_preview_files()

    def _schedule_pending(self) -> None:
        if not self._task.busy:
            self._start_pending()

    def _start_pending(self) -> None:
        if self._disposed or self._task.busy or self._pending_request is None:
            return
        request = self._pending_request
        self._pending_request = None
        self._active_request = request
        if not self._task.run_cancellable(request.message, request.loader):
            self._pending_request = request

    @Slot(object)
    def _on_task_succeeded(self, result: object) -> None:
        request = self._active_request
        self._active_request = None
        if (
            request is not None
            and request.generation == self._generation
            and isinstance(result, PreviewPayload)
        ):
            if request.purpose == 'child':
                self._apply_child_payload(request.child_asset_id, result.data)
            else:
                try:
                    self.load_payload(result)
                except Exception as exc:
                    self._reset_content()
                    self._set_preview('error', '', '', error=str(exc))
        QTimer.singleShot(0, self._start_pending)

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        request = self._active_request
        self._active_request = None
        if request is not None and request.generation == self._generation:
            if request.purpose == 'child':
                self._error_text = message
                self.previewChanged.emit()
            else:
                self._reset_content()
                self._set_preview('error', '', '', error=message)
        QTimer.singleShot(0, self._start_pending)

    def _apply_child_payload(self, asset_id: str, data: bytes) -> None:
        try:
            working = _bounded_decompress(data)
        except ValueError as exc:
            self._error_text = str(exc)
            self.previewChanged.emit()
            return
        if not self._texture_pack_preview.set_map_bytes(asset_id, working):
            self._error_text = 'The TexturePack map was not a supported image.'
        else:
            self._error_text = ''
        self.previewChanged.emit()

    def _classify(self, data: bytes, payload: PreviewPayload) -> None:
        type_name = payload.type_name.strip().casefold()
        asset_type = payload.asset_type
        label = payload.label or payload.asset_id or 'Preview'

        if asset_type == 74 or data.startswith(_FONT_MAGIC):
            if self._font_preview.load_bytes(data):
                self._set_preview('font', '', '', error='')
                return

        image = self._image_payload(data)
        if image is not None and (
            asset_type in _IMAGE_TYPES
            or type_name in {'image', 'decal', 'texture'}
            or data.startswith(_IMAGE_MAGIC)
            or _looks_like_webp(data)
            or data[:12] in _KTX_MAGIC
        ):
            image_data, suffix = image
            self._image_bytes = image_data
            source = self._materialize(image_data, suffix)
            self._set_preview('image', '', source, error='')
            return

        if asset_type == 3 or type_name == 'audio' or data.startswith(_AUDIO_MAGIC) or _looks_like_mp3(
            data
        ) or _looks_like_wave(data):
            suffix = (
                '.ogg'
                if data.startswith(b'OggS')
                else '.mp3'
                if data.startswith((b'ID3', b'\xff\xfb', b'\xff\xf3', b'\xff\xf2'))
                else '.wav'
                if _looks_like_wave(data)
                else '.flac'
                if data.startswith(b'fLaC')
                else '.bin'
            )
            self._set_preview('audio', '', self._materialize(data, suffix), error='')
            return

        if asset_type == 63 or type_name == 'texturepack' or _looks_like_texture_pack(data):
            pack_id = payload.asset_id if payload.asset_id.isdecimal() else 'preview'
            if self._texture_pack_preview.load_bytes(data, pack_id):
                self._set_preview('texturepack', '', '', error='')
                return

        document_kind = classify_roblox_document(data)
        if (
            asset_type in _ANIMATION_TYPES
            or 'animation' in type_name
            or _looks_like_animation(data)
            or document_kind is not None
        ) and self._animation_preview.load_bytes(data, label):
            self._set_preview('animation', '', '', error='')
            return

        if document_kind is not None and self._document_preview.load_bytes(
            data,
            payload.source_value or payload.asset_id or label,
            label,
        ):
            self._set_preview('document', '', '', error='')
            return

        if asset_type in _MESH_TYPES or type_name in {
            'mesh',
            'meshpart',
            'solidmodel',
            'mesh hidden surface removal',
        } or data.startswith(b'version'):
            from .mesh_geometry import MeshGeometry

            geometry = MeshGeometry()  # pyright: ignore[reportCallIssue]
            geometry.setParent(self)
            if geometry.load(data):
                self._mesh_geometry = geometry
                self._set_preview('mesh', '', '', error='', force=True)
                return
            geometry.setParent(None)
            geometry.deleteLater()

        if data.lstrip().startswith((b'{', b'[')) and self._json_preview.load_bytes(data):
            self._set_preview('json', '', '', error='')
            return

        if text := _printable_text(data):
            self._set_preview('text', text, '', error='')
            return

        self._set_preview('hex', _hex_dump(data), '', error='')

    def _image_payload(self, data: bytes) -> tuple[bytes, str] | None:
        image_data = data
        if data[:12] in _KTX_MAGIC:
            try:
                from ..cache.tools.ktx_to_png import convert

                converted = convert(data)
            except Exception:
                converted = None
            if not converted or len(converted) > _MAX_PREVIEW_BYTES:
                return None
            image_data = converted

        buffer = QBuffer()
        buffer.setData(QByteArray(image_data))
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            return None
        reader = QImageReader(buffer)
        if not reader.canRead():
            return None
        dimensions = reader.size()
        if (
            not dimensions.isValid()
            or dimensions.width() <= 0
            or dimensions.height() <= 0
            or dimensions.width() * dimensions.height() > _MAX_IMAGE_PIXELS
        ):
            return None
        image_format = bytes(reader.format().data()).decode('ascii', errors='ignore').casefold()
        suffix = f'.{re.sub(r"[^a-z0-9]+", "", image_format) or "img"}'
        return image_data, suffix

    def _materialize(self, data: bytes, suffix: str) -> str:
        with NamedTemporaryFile(
            prefix='fleasion-payload-preview-',
            suffix=suffix,
            delete=False,
        ) as handle:
            handle.write(data)
            path = Path(handle.name)
        self._preview_files.add(path)
        return QUrl.fromLocalFile(str(path)).toString()

    def _reset_content(self) -> None:
        self._release_mesh_geometry()
        self._font_preview.clear()
        self._json_preview.clear()
        self._document_preview.detach()
        self._animation_preview.clear()
        self._texture_pack_preview.clear()
        self._remove_preview_files()
        self._image_bytes = b''

    def _set_preview(
        self,
        kind: str,
        text: str,
        source: str,
        *,
        error: str,
        force: bool = False,
    ) -> None:
        if not force and (
            kind == self._preview_kind
            and text == self._preview_text
            and source == self._preview_source
            and error == self._error_text
        ):
            return
        self._preview_kind = kind
        self._preview_text = text
        self._preview_source = source
        self._error_text = error
        self.previewChanged.emit()

    def _release_mesh_geometry(self) -> None:
        geometry = self._mesh_geometry
        self._mesh_geometry = None
        if geometry is not None:
            geometry.setParent(None)
            geometry.deleteLater()

    def _remove_preview_files(self) -> None:
        for path in tuple(self._preview_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._preview_files.discard(path)


__all__ = ['PayloadPreviewApi', 'PreviewLoader', 'PreviewPayload']
