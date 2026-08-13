"""Preview and export bridge for Roblox file modifications."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from ..cache.tools.ktx_to_png import convert as ktx_to_png
from ..modifications.dds_to_png import tex_to_png_bytes
from ..modifications.manager import MOD_ORIGINALS_DIR, normalise_target_path
from ..modifications.platform_targets import read_current_platform_original_asset
from ..modifications.stash_paths import resource_stash_dir

if TYPE_CHECKING:
    from ..modifications.manager import ModificationManager

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_MAX_INSPECTION_BYTES: Final = 128 * 1024 * 1024
_DIRECT_IMAGE_SUFFIXES: Final = frozenset({'.png', '.jpg', '.jpeg', '.gif', '.webp'})
_TEXT_SUFFIXES: Final = frozenset({'.json', '.xml', '.txt', '.csv', '.ini'})


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{value} B'


@QmlElement
class ModificationInspector(QObject):
    """Expose side-by-side modification metadata, previews, and exports."""

    infoChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(
        self,
        manager: ModificationManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._target_path = ''
        self._display_name = ''
        self._info: dict[str, object] = {}
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix='fleasion-modification-preview-'
        )

    @Property('QVariantMap', notify=infoChanged)
    def info(self) -> dict[str, object]:
        return dict(self._info)

    @Slot(str, str)
    def inspect(self, target_path: str, display_name: str) -> None:
        self._target_path = target_path
        self._display_name = display_name.strip() or Path(
            target_path.replace('\\', '/')
        ).name
        replacement = self._load_data('replacement')
        original = self._load_data('original')
        preview_kind = self._preview_kind(target_path)
        self._info = {
            'displayName': self._display_name,
            'targetPath': target_path,
            'previewKind': preview_kind,
            'replacementAvailable': replacement is not None,
            'originalAvailable': original is not None,
            'replacementSize': ''
            if replacement is None
            else _format_bytes(len(replacement)),
            'originalSize': '' if original is None else _format_bytes(len(original)),
            'replacementPreviewUrl': self._preview_url(
                replacement, target_path, 'replacement'
            ),
            'originalPreviewUrl': self._preview_url(original, target_path, 'original'),
            'replacementSummary': self._summary(replacement, preview_kind),
            'originalSummary': self._summary(original, preview_kind),
        }
        self.infoChanged.emit()

    @Slot(str, str, result=bool)
    def exportFile(self, version: str, value: str) -> bool:  # noqa: N802
        mode = 'original' if version == 'original' else 'replacement'
        data = self._load_data(mode)
        if data is None:
            self.errorOccurred.emit(f'The {mode} file is not available.')
            return False
        destination = self._local_path(value)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.notificationRequested.emit(
            'Modification exported',
            str(destination),
            'success',
        )
        return True

    @Slot()
    def shutdown(self) -> None:
        self._temporary_directory.cleanup()

    def _load_data(self, mode: str) -> bytes | None:
        manager = self._manager
        if manager is None or not self._target_path:
            return None
        roblox_dirs = list(getattr(manager, 'roblox_dirs', ()))
        if not roblox_dirs:
            return None
        try:
            target = normalise_target_path(self._target_path)
        except ValueError:
            return None

        if mode == 'original':
            stash_root = Path(getattr(manager, '_stash_dir', MOD_ORIGINALS_DIR))
            for roblox_dir in roblox_dirs:
                stash = resource_stash_dir(stash_root, Path(roblox_dir)) / target
                if data := self._read_bounded(stash):
                    return data
            if data := read_current_platform_original_asset(self._target_path):
                return data if len(data) <= _MAX_INSPECTION_BYTES else None
            if self._target_is_tracked():
                return None

        for roblox_dir in roblox_dirs:
            if data := self._read_bounded(Path(roblox_dir) / target):
                return data
        return (
            read_current_platform_original_asset(self._target_path)
            if mode == 'replacement'
            else None
        )

    def _target_is_tracked(self) -> bool:
        manager = self._manager
        if manager is None:
            return False
        target_key = self._target_path.replace('\\', '/').strip('/').casefold()
        return any(
            str(entry.get('target_path', ''))
            .replace('\\', '/')
            .strip('/')
            .casefold()
            == target_key
            for entry in manager.entries
        )

    @staticmethod
    def _read_bounded(path: Path) -> bytes | None:
        try:
            if not path.is_file() or path.stat().st_size > _MAX_INSPECTION_BYTES:
                return None
            return path.read_bytes()
        except OSError:
            return None

    @staticmethod
    def _preview_kind(target_path: str) -> str:
        suffix = Path(target_path.replace('\\', '/')).suffix.casefold()
        if suffix in _DIRECT_IMAGE_SUFFIXES or suffix in {'.tex', '.dds', '.ktx', '.ktx2'}:
            return 'image'
        if suffix in {'.mp3', '.ogg', '.wav'}:
            return 'audio'
        if suffix in _TEXT_SUFFIXES:
            return 'text'
        if suffix == '.mesh':
            return 'mesh'
        if suffix in {'.ttf', '.otf', '.ttc'}:
            return 'font'
        return 'binary'

    def _preview_url(self, data: bytes | None, target_path: str, mode: str) -> str:
        if data is None:
            return ''
        suffix = Path(target_path.replace('\\', '/')).suffix.casefold()
        preview_data = data
        preview_suffix = suffix
        if suffix in {'.ktx', '.ktx2'}:
            converted = ktx_to_png(data)
            if converted is None:
                return ''
            preview_data = converted
            preview_suffix = '.png'
        elif suffix in {'.tex', '.dds'}:
            if data.startswith((b'\x89PNG\r\n\x1a\n', b'\xff\xd8')):
                preview_suffix = '.png' if data.startswith(b'\x89PNG') else '.jpg'
            else:
                converted = ktx_to_png(data) or tex_to_png_bytes(data)
                if converted is None:
                    return ''
                preview_data = converted
                preview_suffix = '.png'
        elif suffix not in _DIRECT_IMAGE_SUFFIXES:
            return ''
        destination = (
            Path(self._temporary_directory.name)
            / f'{mode}{preview_suffix or ".bin"}'
        )
        try:
            destination.write_bytes(preview_data)
        except OSError:
            return ''
        return QUrl.fromLocalFile(str(destination)).toString()

    @staticmethod
    def _summary(data: bytes | None, preview_kind: str) -> str:
        if data is None:
            return 'Unavailable'
        if preview_kind == 'text':
            return data[:4096].decode('utf-8', errors='replace')
        labels = {
            'audio': 'Audio file ready to export',
            'mesh': 'Roblox mesh file ready to export',
            'font': 'Font file ready to export',
            'binary': 'Binary file ready to export',
            'image': 'Image preview',
        }
        return labels.get(preview_kind, 'File ready to export')

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        return Path(url.toLocalFile()) if url.isLocalFile() else Path(value).expanduser()
