"""Animation summary and conversion bridge for QML cache previews."""

from __future__ import annotations

import re
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Final

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from ..cache.animation_document import load_animation_data
from ..localization import tr
from ..cache.roblox_document import RBXM_MAGIC, decompress_if_needed
from .animation_conversion import AnimationConversionApi
from .models import DictListModel

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_TRACK_ROLES: Final = ('name', 'sampleCount', 'coverageText')


@QmlElement
class AnimationPreviewApi(QObject):
    """Expose parsed animation timing plus the existing conversion workflow."""

    changed = Signal()
    converterChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tracks = DictListModel(_TRACK_ROLES, parent=self)
        self._converter = self._new_converter()
        self._preview_files: set[Path] = set()
        self._export_directory: Path | None = None
        self._loaded = False
        self._source_label = ''
        self._duration = 0.0
        self._keyframe_count = 0
        self._track_count = 0
        self._keyframe_markers: list[float] = []
        self._error_text = ''

    @Property(QObject, constant=True)
    def tracksModel(self) -> QObject:  # noqa: N802
        return self._tracks

    @Property(QObject, notify=converterChanged)
    def converter(self) -> QObject:
        return self._converter

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return self._loaded

    @Property(str, notify=changed)
    def sourceLabel(self) -> str:  # noqa: N802
        return self._source_label

    @Property(float, notify=changed)
    def duration(self) -> float:
        return self._duration

    @Property(int, notify=changed)
    def keyframeCount(self) -> int:  # noqa: N802
        return self._keyframe_count

    @Property(int, notify=changed)
    def trackCount(self) -> int:  # noqa: N802
        return self._track_count

    @Property(list, notify=changed)
    def keyframeMarkers(self) -> list[float]:  # noqa: N802
        return list(self._keyframe_markers)

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    def load_bytes(self, data: bytes, label: str = '') -> bool:
        """Parse cached animation bytes and prepare an isolated conversion source."""
        try:
            data = decompress_if_needed(data)
        except Exception:
            self._error_text = tr('qml.dynamic.animation_preview.compressed_read_failed')
            return False
        keyframes = load_animation_data(data)
        if not keyframes:
            self._error_text = tr('qml.dynamic.animation_preview.no_keyframes')
            return False

        duration = max(0.0, max(keyframe.time for keyframe in keyframes))
        track_times: dict[str, list[float]] = {}
        for keyframe in keyframes:
            for name in keyframe.pose_by_part_name:
                track_times.setdefault(name, []).append(keyframe.time)
        rows = []
        for name, times in sorted(track_times.items(), key=lambda item: item[0].casefold()):
            first = min(times)
            last = max(times)
            rows.append(
                {
                    'name': name,
                    'sampleCount': len(times),
                    'coverageText': f'{first:.2f}s – {last:.2f}s',
                }
            )

        unique_times = sorted({max(0.0, keyframe.time) for keyframe in keyframes})
        marker_times = unique_times[:200]
        self._loaded = True
        self._source_label = label
        self._duration = duration
        self._keyframe_count = len(keyframes)
        self._track_count = len(track_times)
        self._keyframe_markers = [
            (time / duration if duration > 0.0 else 0.0) for time in marker_times
        ]
        self._error_text = ''
        self._tracks.replace_items(rows)

        self._replace_converter()
        path = self._materialize(data)
        self._converter.loadSource(QUrl.fromLocalFile(str(path)).toString())
        self.changed.emit()
        return True

    def set_export_directory(self, value: object) -> None:
        self._export_directory = value if isinstance(value, Path) else None

    @Slot(str, result=str)
    def suggestedOutputUrl(self, target: str) -> str:  # noqa: N802
        normalized = target.strip().casefold()
        if not self._loaded or normalized not in {'r6', 'r15'}:
            return ''
        directory = self._export_directory or Path.cwd()
        stem = re.sub(r'[^A-Za-z0-9._-]+', '_', self._source_label.strip()).strip('._')
        stem = stem[:100] or 'animation'
        return QUrl.fromLocalFile(str(directory / f'{stem}_{normalized}.rbxmx')).toString()

    @Slot()
    def clear(self) -> None:
        if not self._loaded and not self._converter.sourceLoaded and not self._converter.task.busy:
            return
        self._loaded = False
        self._source_label = ''
        self._duration = 0.0
        self._keyframe_count = 0
        self._track_count = 0
        self._keyframe_markers = []
        self._error_text = ''
        self._tracks.replace_items([])
        self._replace_converter()
        self.changed.emit()

    @Slot()
    def shutdown(self) -> None:
        self._converter.shutdown()
        self._remove_preview_files()

    def _new_converter(self) -> Any:
        converter = AnimationConversionApi(parent=self)  # pyright: ignore[reportCallIssue]
        converter.errorOccurred.connect(self.errorOccurred)
        converter.notificationRequested.connect(self.notificationRequested)
        return converter

    def _replace_converter(self) -> None:
        previous = self._converter
        previous.shutdown()
        previous.setParent(None)
        previous.deleteLater()
        self._remove_preview_files()
        self._converter = self._new_converter()
        self.converterChanged.emit()

    def _materialize(self, data: bytes) -> Path:
        suffix = '.rbxm' if data.startswith(RBXM_MAGIC) else '.rbxmx'
        with NamedTemporaryFile(
            prefix='fleasion-animation-preview-',
            suffix=suffix,
            delete=False,
        ) as handle:
            handle.write(data)
            path = Path(handle.name)
        self._preview_files.add(path)
        return path

    def _remove_preview_files(self) -> None:
        for path in tuple(self._preview_files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            self._preview_files.discard(path)


__all__ = ['AnimationPreviewApi']
