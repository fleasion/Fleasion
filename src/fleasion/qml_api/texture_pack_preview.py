"""TexturePack inspection and captured-slot export bridge for QML."""

from __future__ import annotations

import gzip
import io
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtQml import QmlElement

from ..cache.cache_manager import CacheManager
from ..localization import tr
from ..utils import APP_CACHE_DIR
from ..utils.clipboard import copy_pixmap_to_clipboard
from .models import DictListModel

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_MAX_TEXTURE_PACK_XML_BYTES: Final = 2 * 1024 * 1024
_MAX_CLIPBOARD_IMAGE_BYTES: Final = 64 * 1024 * 1024
_TEXTURE_ROLES: Final = (
    'name',
    'slotIndex',
    'slotLabel',
    'slotKey',
    'assetId',
    'hash',
    'sizeText',
    'imageSource',
    'cached',
    'captured',
    'capturedSizeText',
)
_TAG_DETAILS: Final[dict[str, tuple[int, str]]] = {
    'color': (0, 'Color'),
    'albedo': (0, 'Color'),
    'diffuse': (0, 'Color'),
    'basecolor': (0, 'Color'),
    'normal': (1, 'Normal'),
    'normalmap': (1, 'Normal'),
    'bumpmap': (1, 'Normal'),
    'metalness': (2, 'Metalness'),
    'orm': (2, 'ORM'),
    'roughness': (3, 'Roughness'),
    'emissive': (4, 'Emissive'),
    'emissivemap': (4, 'Emissive'),
    'height': (5, 'Height'),
    'heightmap': (5, 'Height'),
    'displacement': (5, 'Height'),
}
_SLOT_FILE_NAMES: Final = {
    0: 'Color',
    1: 'Normal',
    2: 'ORM',
    3: 'Roughness',
    4: 'Emissive',
    5: 'Height',
}


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for suffix in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or suffix == 'GB':
            return f'{size:.0f} {suffix}' if suffix == 'B' else f'{size:.1f} {suffix}'
        size /= 1024
    return '0 B'


def _bounded_xml(data: bytes) -> bytes:
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            result = stream.read(_MAX_TEXTURE_PACK_XML_BYTES + 1)
    else:
        result = data
    if len(result) > _MAX_TEXTURE_PACK_XML_BYTES:
        raise ValueError('TexturePack XML exceeds the 2 MB preview limit.')
    return result


@QmlElement
class TexturePackPreviewApi(QObject):
    """Expose TexturePack maps and captured fixed-index KTX2 slots."""

    changed = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)
    loadRequested = Signal(str)

    def __init__(
        self,
        cache_manager: CacheManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._cache = cache_manager
        self._model = DictListModel(_TEXTURE_ROLES, parent=self)
        self._pack_asset_id = ''
        self._xml_text = ''
        self._error_text = ''
        self._maps: list[tuple[str, int, str]] = []
        self._loaded_maps: dict[str, bytes] = {}
        self._loaded_map_urls: dict[str, str] = {}
        self._preview_files: set[Path] = set()
        self._slot_directory = APP_CACHE_DIR / 'texpack_slots'
        self._export_directory: Path | None = None

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return bool(self._pack_asset_id and self._maps)

    @Property(str, notify=changed)
    def packAssetId(self) -> str:  # noqa: N802
        return self._pack_asset_id

    @Property(str, notify=changed)
    def xmlText(self) -> str:  # noqa: N802
        return self._xml_text

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    @Property(int, notify=changed)
    def capturedCount(self) -> int:  # noqa: N802
        if not self._pack_asset_id:
            return 0
        return sum(1 for slot_index in range(6) if self._captured_path(slot_index).is_file())

    def set_cache_manager(self, cache_manager: CacheManager) -> None:
        """Set the cache used to resolve TexturePack child images."""
        self._cache = cache_manager

    def set_export_directory(self, directory: object) -> None:
        """Set the root used by automatic per-slot exports."""
        self._export_directory = directory if isinstance(directory, Path) else None

    def set_slot_directory(self, directory: Path) -> None:
        """Override the captured-slot directory for an isolated runtime or test."""
        self._slot_directory = directory

    def load_bytes(self, data: bytes, pack_asset_id: str) -> bool:
        """Parse a TexturePack XML payload and expose its fixed global slots."""
        self.clear()
        try:
            xml_data = _bounded_xml(data)
            root = ET.fromstring(xml_data)
        except (ET.ParseError, OSError, ValueError) as exc:
            self._error_text = tr('qml.dynamic.texture_pack.metadata_read_failed', error=exc)
            self.changed.emit()
            return False

        seen_slots: set[int] = set()
        maps: list[tuple[str, int, str]] = []
        for element in root.iter():
            tag = str(element.tag).rsplit('}', 1)[-1].casefold()
            details = _TAG_DETAILS.get(tag)
            asset_id = str(element.text or '').strip()
            if details is None or details[0] in seen_slots:
                continue
            if not asset_id.isdecimal() or asset_id == '0':
                continue
            slot_index, display_name = details
            seen_slots.add(slot_index)
            maps.append((display_name, slot_index, str(int(asset_id))))

        if not maps:
            self._error_text = tr('qml.dynamic.texture_pack.no_supported_maps')
            self.changed.emit()
            return False

        self._pack_asset_id = pack_asset_id
        self._xml_text = xml_data.decode('utf-8', errors='replace')
        self._maps = sorted(maps, key=lambda item: item[1])
        self._replace_rows()
        self.changed.emit()
        return True

    @Slot()
    def refresh(self) -> None:
        if not self._maps:
            return
        self._replace_rows()
        self.changed.emit()

    @Slot(int)
    def requestMap(self, row: int) -> None:  # noqa: N802
        entry = self._model.get(row)
        asset_id = str(entry.get('assetId') or '')
        if asset_id and not entry.get('cached'):
            self.loadRequested.emit(asset_id)

    @Slot(int, result=bool)
    def copyMapImage(self, row: int) -> bool:  # noqa: N802
        entry = self._model.get(row)
        asset_id = str(entry.get('assetId') or '')
        if not asset_id:
            return False
        data = self._loaded_maps.get(asset_id)
        if data is None and self._cache is not None:
            data = self._cache.get_asset(asset_id, 1)
        if not data:
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.load_map_before_copy'))
            return False
        if len(data) > _MAX_CLIPBOARD_IMAGE_BYTES:
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.map_too_large_to_copy'))
            return False
        image = QImage.fromData(data)
        if image.isNull():
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.map_decode_failed'))
            return False
        try:
            copy_pixmap_to_clipboard(QPixmap.fromImage(image))
        except RuntimeError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.notificationRequested.emit(
            tr('qml.dynamic.texture_pack.copied_title'),
            tr(
                'qml.dynamic.texture_pack.map_copied_detail',
                name=entry.get('name') or tr('qml.dynamic.texture_pack.texture_fallback_name'),
            ),
            'success',
        )
        return True

    def set_map_bytes(self, asset_id: str, data: bytes) -> bool:
        """Attach one bounded, decoded map image without persisting it to cache."""
        normalized = asset_id.strip()
        if (
            not normalized.isdecimal()
            or normalized == '0'
            or len(data) > _MAX_CLIPBOARD_IMAGE_BYTES
        ):
            return False
        image = QImage.fromData(data)
        if image.isNull() or image.width() * image.height() > 64 * 1024 * 1024:
            return False
        previous_url = self._loaded_map_urls.get(normalized, '')
        previous_path = Path(QUrl(previous_url).toLocalFile()) if previous_url else None
        with NamedTemporaryFile(
            prefix='fleasion-texture-map-preview-',
            suffix='.png',
            delete=False,
        ) as handle:
            path = Path(handle.name)
        if not image.save(str(path)):
            path.unlink(missing_ok=True)
            return False
        self._loaded_maps[normalized] = data
        self._loaded_map_urls[normalized] = QUrl.fromLocalFile(str(path)).toString()
        self._preview_files.add(path)
        if previous_path is not None:
            previous_path.unlink(missing_ok=True)
            self._preview_files.discard(previous_path)
        self._replace_rows()
        self.changed.emit()
        return True

    @Slot(int, result=bool)
    def exportCapturedSlot(self, row: int) -> bool:  # noqa: N802
        entry = self._model.get(row)
        slot_index = entry.get('slotIndex')
        if not isinstance(slot_index, int) or not self._pack_asset_id:
            return False
        source = self._captured_path(slot_index)
        if not source.is_file():
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.slot_not_captured'))
            return False
        destination_directory = self._slot_export_directory()
        destination_directory.mkdir(parents=True, exist_ok=True)
        destination = destination_directory / self._export_name(slot_index)
        try:
            shutil.copy2(source, destination)
        except OSError as exc:
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.export_slot_failed', error=exc))
            return False
        self.notificationRequested.emit(
            tr('qml.dynamic.texture_pack.slot_exported_title'), str(destination), 'success'
        )
        return True

    @Slot(result=int)
    def exportAllCapturedSlots(self) -> int:  # noqa: N802
        if not self._pack_asset_id:
            return 0
        sources = [
            (slot_index, self._captured_path(slot_index))
            for slot_index in range(6)
            if self._captured_path(slot_index).is_file()
        ]
        if not sources:
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.slots_not_captured'))
            return 0
        destination_directory = self._slot_export_directory()
        try:
            destination_directory.mkdir(parents=True, exist_ok=True)
            for slot_index, source in sources:
                shutil.copy2(source, destination_directory / self._export_name(slot_index))
        except OSError as exc:
            self.errorOccurred.emit(tr('qml.dynamic.texture_pack.export_slots_failed', error=exc))
            return 0
        self.notificationRequested.emit(
            tr('qml.dynamic.texture_pack.slots_exported_title'),
            tr(
                'qml.dynamic.texture_pack.files_saved',
                count=len(sources),
                destination=destination_directory,
            ),
            'success',
        )
        return len(sources)

    @Slot()
    def clear(self) -> None:
        changed = bool(
            self._pack_asset_id
            or self._xml_text
            or self._error_text
            or self._maps
            or self._loaded_maps
        )
        self._pack_asset_id = ''
        self._xml_text = ''
        self._error_text = ''
        self._maps = []
        self._loaded_maps = {}
        self._loaded_map_urls = {}
        for path in tuple(self._preview_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._preview_files.discard(path)
        self._model.replace_items(())
        if changed:
            self.changed.emit()

    def _replace_rows(self) -> None:
        rows: list[dict[str, Any]] = []
        for name, slot_index, asset_id in self._maps:
            info = self._asset_info(asset_id)
            loaded_url = self._loaded_map_urls.get(asset_id, '')
            cached = bool(info) or bool(loaded_url)
            hash_value = str(info.get('hash') or '')
            size = int(info.get('raw_size', info.get('size', 0)) or 0)
            captured_path = self._captured_path(slot_index)
            captured_size = captured_path.stat().st_size if captured_path.is_file() else 0
            rows.append(
                {
                    'name': tr(f'qml.dynamic.texture_pack.slot_name.{slot_index}'),
                    'slotIndex': slot_index,
                    'slotLabel': tr('qml.dynamic.texture_pack.slot_label', slot=slot_index),
                    'slotKey': f'{self._pack_asset_id}:{slot_index}',
                    'assetId': asset_id,
                    'hash': hash_value,
                    'sizeText': _format_bytes(size) if cached else '',
                    'imageSource': (
                        loaded_url
                        or (f'image://fleasion-cache/1/{asset_id}?v={hash_value}' if info else '')
                    ),
                    'cached': cached,
                    'captured': captured_size > 0,
                    'capturedSizeText': _format_bytes(captured_size) if captured_size else '',
                }
            )
        self._model.replace_items(rows)

    def _asset_info(self, asset_id: str) -> dict[str, Any]:
        if self._cache is None:
            return {}
        getter = getattr(self._cache, 'get_asset_info', None)
        if not callable(getter):
            return {}
        value = getter(asset_id, 1)
        return dict(value) if isinstance(value, dict) else {}

    def _captured_path(self, slot_index: int) -> Path:
        return self._slot_directory / f'{self._pack_asset_id}_slot{slot_index}.ktx2'

    def _slot_export_directory(self) -> Path:
        root = self._export_directory or APP_CACHE_DIR / 'exports'
        return root / 'converted' / 'TexturePack' / f'{self._pack_asset_id}_slots'

    def _export_name(self, slot_index: int) -> str:
        slot_name = _SLOT_FILE_NAMES.get(slot_index, f'Slot{slot_index}')
        return f'{self._pack_asset_id}_slot{slot_index}_{slot_name}.ktx2'
