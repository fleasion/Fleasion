"""Asynchronous QML bridge for local Roblox animation conversion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from ..utils.animation_conversion import (
    AnimationRig,
    PreparedAnimation,
    convert_animation_rig,
    prepare_animation_source,
    save_animation_conversion,
)
from .tasks import TaskState

if TYPE_CHECKING:
    from collections.abc import Callable

    type PrepareSource = Callable[[Path], PreparedAnimation]
    type ConvertAnimation = Callable[[bytes, AnimationRig], bytes]
    type SaveAnimation = Callable[[bytes, Path], Path]

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0


@dataclass(frozen=True, slots=True)
class _ConversionResult:
    destination: Path
    target: AnimationRig


@QmlElement
class AnimationConversionApi(QObject):
    """Own one loaded animation and run conversions off the UI thread."""

    sourceChanged = Signal()
    statusChanged = Signal()
    outputChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(
        self,
        prepare: PrepareSource = prepare_animation_source,
        convert: ConvertAnimation = convert_animation_rig,
        save: SaveAnimation = save_animation_conversion,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._prepare: Final = prepare
        self._convert: Final = convert
        self._save: Final = save
        self._task = TaskState(self)
        self._source: PreparedAnimation | None = None
        self._status = ''
        self._last_output_path = ''
        self._operation = ''
        self._disposed = False
        self._task.succeeded.connect(self._on_task_succeeded)
        self._task.failed.connect(self._on_task_failed)

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Property(bool, notify=sourceChanged)
    def sourceLoaded(self) -> bool:  # noqa: N802
        return self._source is not None

    @Property(str, notify=sourceChanged)
    def sourcePath(self) -> str:  # noqa: N802
        return str(self._source.source_path) if self._source is not None else ''

    @Property(str, notify=sourceChanged)
    def sourceName(self) -> str:  # noqa: N802
        return self._source.source_path.name if self._source is not None else ''

    @Property(str, notify=sourceChanged)
    def detectedRig(self) -> str:  # noqa: N802
        return self._source.detected_rig if self._source is not None else 'unknown'

    @Property(bool, notify=sourceChanged)
    def canConvertToR6(self) -> bool:  # noqa: N802
        return self.detectedRig == 'R15'

    @Property(bool, notify=sourceChanged)
    def canConvertToR15(self) -> bool:  # noqa: N802
        return self.detectedRig == 'R6'

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:  # noqa: N802
        return self._status

    @Property(str, notify=outputChanged)
    def lastOutputPath(self) -> str:  # noqa: N802
        return self._last_output_path

    @Slot(str, result=bool)
    def loadSource(self, source: str) -> bool:  # noqa: N802
        if self._task.busy or self._disposed:
            return False
        try:
            path = _local_path(source)
        except ValueError as exc:
            self._set_status(str(exc))
            self.errorOccurred.emit(str(exc))
            return False
        self._operation = 'load'
        self._set_status('Reading and inspecting animation…')
        return self._task.run(
            'Reading and inspecting animation…',
            lambda: self._prepare(path),
        )

    @Slot(str, str, result=bool)
    def convert(self, target: str, destination: str) -> bool:
        if self._task.busy or self._disposed:
            return False
        source = self._source
        if source is None:
            self.errorOccurred.emit('Load an animation before converting it.')
            return False
        if target not in {'R6', 'R15'}:
            self.errorOccurred.emit('Animation target must be R6 or R15.')
            return False
        typed_target = cast('AnimationRig', target)
        expected_source = 'R15' if typed_target == 'R6' else 'R6'
        if source.detected_rig != expected_source:
            self.errorOccurred.emit(
                f'This conversion requires a detected {expected_source} animation.'
            )
            return False
        try:
            output_path = _local_path(destination)
        except ValueError as exc:
            self.errorOccurred.emit(str(exc))
            return False

        def run_conversion() -> _ConversionResult:
            converted = self._convert(source.xml_bytes, typed_target)
            return _ConversionResult(self._save(converted, output_path), typed_target)

        self._operation = 'convert'
        self._set_status(f'Converting animation to {typed_target}…')
        return self._task.run(
            f'Converting animation to {typed_target}…',
            run_conversion,
        )

    @Slot(str, result=str)
    def suggestedOutputUrl(self, target: str) -> str:  # noqa: N802
        if self._source is None or target not in {'R6', 'R15'}:
            return ''
        suffix = target.casefold()
        destination = self._source.source_path.with_name(
            f'{self._source.source_path.stem}_{suffix}.rbxmx'
        )
        return QUrl.fromLocalFile(str(destination)).toString()

    @Slot()
    def clearSource(self) -> None:  # noqa: N802
        if self._task.busy:
            return
        changed = self._source is not None
        self._source = None
        self._last_output_path = ''
        self._set_status('')
        if changed:
            self.sourceChanged.emit()
        self.outputChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._task.shutdown()

    @Slot(object)
    def _on_task_succeeded(self, result: object) -> None:
        if self._disposed:
            return
        operation = self._operation
        self._operation = ''
        if operation == 'load' and isinstance(result, PreparedAnimation):
            self._source = result
            self._last_output_path = ''
            if result.converted_from_binary:
                status = 'Binary RBXM converted to editable RBXMX.'
            elif result.detected_rig == 'unknown':
                status = 'The player rig could not be detected.'
            else:
                status = f'{result.detected_rig} animation ready.'
            self._set_status(status)
            self.sourceChanged.emit()
            self.outputChanged.emit()
            return
        if operation == 'convert' and isinstance(result, _ConversionResult):
            self._last_output_path = str(result.destination)
            self._set_status(f'Saved {result.destination.name}.')
            self.outputChanged.emit()
            self.notificationRequested.emit(
                'Animation converted',
                str(result.destination),
                'success',
            )

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        if self._disposed:
            return
        self._operation = ''
        self._set_status(message)
        self.errorOccurred.emit(message)

    def _set_status(self, value: str) -> None:
        if value == self._status:
            return
        self._status = value
        self.statusChanged.emit()


def _local_path(value: str) -> Path:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError('Choose a local animation file.')
    url = QUrl(cleaned)
    if url.isLocalFile():
        return Path(url.toLocalFile())
    if url.scheme() and not (len(url.scheme()) == 1 and cleaned[1:2] == ':'):
        raise ValueError('Animation conversion supports local files only.')
    return Path(cleaned).expanduser()


__all__ = ['AnimationConversionApi']
