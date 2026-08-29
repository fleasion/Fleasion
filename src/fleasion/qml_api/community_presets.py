"""QML-facing community preset catalog and value browser."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import TYPE_CHECKING, Final

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

from ..cache.cache_manager import CacheManager
from ..localization import tr
from ..prejsons import (
    CatalogSnapshot,
    CommunityPreset,
    CommunityPresetCatalog,
    CustomPresetRequest,
    PresetMetadata,
    PresetValue,
    RobloxPresetMetadataClient,
    flatten_preset_values,
)
from .community_value_preview import CommunityValueResolver
from .models import DictListModel, SelectionModel
from .payload_preview import PayloadPreviewApi
from .preset_tree import PresetJsonTreeModel
from .tasks import TaskState

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ..prejsons.values import JsonValue

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_PRESET_ROLES: Final = (
    'presetId',
    'name',
    'credit',
    'created',
    'updated',
    'placeId',
    'hasOriginals',
    'hasReplacements',
    'isCustom',
    'thumbnailUrl',
    'searchText',
)
_VALUE_ROLES: Final = (
    'rowId',
    'path',
    'label',
    'valueText',
    'kind',
    'searchText',
)


@dataclass(frozen=True, slots=True)
class _PresetPayload:
    generation: int
    preset_id: str
    preset_name: str
    kind: str
    document: JsonValue
    values: tuple[PresetValue, ...]


@dataclass(frozen=True, slots=True)
class _ImportResult:
    snapshot: CatalogSnapshot
    imported_count: int


@QmlElement
class CommunityPresetsApi(QObject):
    """Expose community catalog browsing and safe draft preparation to QML."""

    catalogModelChanged = Signal()
    valueModelChanged = Signal()
    queryChanged = Signal()
    valueQueryChanged = Signal()
    catalogLoadedChanged = Signal()
    payloadChanged = Signal()
    statusChanged = Signal()
    selectedCountChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)
    draftRequested = Signal(str, str, str)
    draftPrepared = Signal()
    _metadataResult = Signal(int, str, object)

    def __init__(
        self,
        store: CommunityPresetCatalog | None = None,
        metadata_client: RobloxPresetMetadataClient | None = None,
        parent: QObject | None = None,
        *,
        cache_manager: CacheManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._store = store or CommunityPresetCatalog()
        self._metadata_client = metadata_client or RobloxPresetMetadataClient()
        self._catalog_model = DictListModel(_PRESET_ROLES, parent=self)
        self._value_model = DictListModel(_VALUE_ROLES, parent=self)
        self._value_tree_model = PresetJsonTreeModel(self)
        self._value_selection = SelectionModel(self)
        self._value_preview = PayloadPreviewApi(  # pyright: ignore[reportCallIssue]
            cache_manager,
            self,
        )
        self._value_resolver = CommunityValueResolver(cache_manager)
        self._task = TaskState(self)
        self._metadata_threads: list[threading.Thread] = []
        self._presets: list[CommunityPreset] = []
        self._metadata: dict[str, PresetMetadata] = {}
        self._values: list[PresetValue] = []
        self._query = ''
        self._value_query = ''
        self._catalog_loaded = False
        self._payload_open = False
        self._selected_preset_id = ''
        self._selected_preset_name = ''
        self._selected_payload_kind = ''
        self._status = ''
        self._operation = ''
        self._metadata_generation = 0
        self._payload_generation = 0
        self._disposed = False
        self._task.succeeded.connect(self._on_task_succeeded)
        self._task.failed.connect(self._on_task_failed)
        self._value_selection.selectionChanged.connect(self.selectedCountChanged)
        self._value_selection.selectionChanged.connect(self._sync_value_preview)
        self._value_preview.errorOccurred.connect(self.errorOccurred)
        self._value_preview.notificationRequested.connect(self.notificationRequested)
        self._value_preview.childAssetRequested.connect(self._load_preview_child)
        self._metadataResult.connect(
            self._apply_metadata,
            Qt.ConnectionType.QueuedConnection,
        )

    @Property(QObject, constant=True)
    def catalogModel(self) -> QObject:  # noqa: N802
        return self._catalog_model

    @Property(QObject, constant=True)
    def valueModel(self) -> QObject:  # noqa: N802
        return self._value_model

    @Property(QObject, constant=True)
    def valueTreeModel(self) -> QObject:  # noqa: N802
        return self._value_tree_model

    @Property(QObject, constant=True)
    def valueSelection(self) -> QObject:  # noqa: N802
        return self._value_selection

    @Property(QObject, constant=True)
    def valuePreview(self) -> QObject:  # noqa: N802
        return self._value_preview

    @Property(QObject, constant=True)
    def task(self) -> QObject:
        return self._task

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self.queryChanged.emit()
        self._refresh_catalog_model()

    @Property(str, notify=valueQueryChanged)
    def valueQuery(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._value_query

    @valueQuery.setter  # pyright: ignore[reportRedeclaration]
    def valueQuery(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._value_query:
            return
        self._value_query = normalized
        self._value_tree_model.set_query(normalized)
        self.valueQueryChanged.emit()
        self._refresh_value_model()

    @Property(bool, notify=catalogLoadedChanged)
    def catalogLoaded(self) -> bool:  # noqa: N802
        return self._catalog_loaded

    @Property(bool, notify=payloadChanged)
    def payloadOpen(self) -> bool:  # noqa: N802
        return self._payload_open

    @Property(str, notify=payloadChanged)
    def selectedPresetName(self) -> str:  # noqa: N802
        return self._selected_preset_name

    @Property(str, notify=payloadChanged)
    def selectedPayloadKind(self) -> str:  # noqa: N802
        return self._selected_payload_kind

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:  # noqa: N802
        return self._status

    @Property(int, notify=selectedCountChanged)
    def selectedCount(self) -> int:  # noqa: N802
        return len(self._value_selection.values())

    @Property(int, notify=selectedCountChanged)
    def selectedIdCount(self) -> int:  # noqa: N802
        selected = set(self._value_selection.values())
        return sum(value.row_id in selected and value.kind == 'id' for value in self._values)

    @Property(str, notify=selectedCountChanged)
    def selectedValuePath(self) -> str:  # noqa: N802
        values = self._selected_values()
        return values[0].path if len(values) == 1 else ''

    @Property(str, notify=selectedCountChanged)
    def selectedValueText(self) -> str:  # noqa: N802
        values = self._selected_values()
        return values[0].value_text if len(values) == 1 else ''

    @Property(str, notify=selectedCountChanged)
    def selectedValueKind(self) -> str:  # noqa: N802
        values = self._selected_values()
        return values[0].kind if len(values) == 1 else ''

    @Slot(result=bool)
    def ensureLoaded(self) -> bool:  # noqa: N802
        return False if self._catalog_loaded else self.refresh()

    @Slot(result=bool)
    @Slot(bool, result=bool)
    def refresh(self, force: bool = True) -> bool:
        if self._task.busy:
            return False
        self._operation = 'catalog'
        self._set_status(tr('qml.dynamic.community.fetching_presets'))
        return self._task.run(
            tr('qml.dynamic.community.fetching_presets'),
            lambda: self._store.load(refresh=force),
        )

    @Slot(str, str, result=bool)
    def openPreset(self, preset_id: str, kind: str) -> bool:  # noqa: N802
        if self._task.busy:
            return False
        preset = self._preset(preset_id)
        source = self._source_for_kind(preset, kind) if preset is not None else ''
        if preset is None or not source:
            self.errorOccurred.emit(tr('qml.dynamic.community.selected_json_source_missing'))
            return False

        self._payload_generation += 1
        generation = self._payload_generation

        def load() -> _PresetPayload:
            document = self._store.load_payload(source)
            values = tuple(flatten_preset_values(document))
            return _PresetPayload(
                generation,
                preset.preset_id,
                preset.name,
                kind,
                document,
                values,
            )

        self._operation = 'payload'
        self._set_status(tr('qml.dynamic.community.loading_values'))
        return self._task.run(tr('qml.dynamic.community.loading_values'), load)

    @Slot()
    def closePayload(self) -> None:  # noqa: N802
        self._payload_generation += 1
        self._payload_open = False
        self._selected_preset_id = ''
        self._selected_preset_name = ''
        self._selected_payload_kind = ''
        self._values.clear()
        self._value_model.replace_items(())
        self._value_tree_model.clear()
        self._value_selection.clear()
        self._value_preview.clear()
        self._value_query = ''
        self.valueQueryChanged.emit()
        self.payloadChanged.emit()
        self.valueModelChanged.emit()

    @Slot(str, str, str, str, str, str, result=bool)
    def importCustom(
        self,
        catalog_source: str,
        name: str,
        place_id: str,
        originals_source: str,
        replacements_source: str,
        credit: str,
    ) -> bool:  # noqa: N802
        if self._task.busy:
            return False
        request = CustomPresetRequest(
            catalog_source=catalog_source,
            name=name,
            place_id=place_id,
            originals_source=originals_source,
            replacements_source=replacements_source,
            credit=credit,
        )

        def import_preset() -> _ImportResult:
            imported = self._store.import_custom(request)
            return _ImportResult(
                snapshot=self._store.load(refresh=False),
                imported_count=len(imported),
            )

        self._operation = 'import'
        self._set_status(tr('qml.dynamic.community.importing_custom_preset'))
        return self._task.run(tr('qml.dynamic.community.importing_custom_preset'), import_preset)

    @Slot(str, result=bool)
    def removeCustom(self, preset_id: str) -> bool:  # noqa: N802
        preset = self._preset(preset_id)
        if preset is None or preset.custom_path is None:
            return False
        if not self._store.delete_custom(preset.custom_path):
            self.errorOccurred.emit(tr('qml.dynamic.community.delete_custom_preset_failed'))
            return False
        self._presets = [
            entry for entry in self._presets if entry.custom_path != preset.custom_path
        ]
        self._refresh_catalog_model()
        self.notificationRequested.emit(
            tr('qml.dynamic.community.preset_removed_title'), preset.name, 'success'
        )
        return True

    @Slot(result=bool)
    def useSelectedAsTargets(self) -> bool:  # noqa: N802
        selected = set(self._value_selection.values())
        values = _unique_values(value.value for value in self._values if value.row_id in selected)
        if not values:
            self.errorOccurred.emit(tr('qml.dynamic.community.select_importable_value'))
            return False
        targets = ', '.join(str(value) for value in values)
        self.draftRequested.emit(self._draft_name(), targets, '')
        self.draftPrepared.emit()
        return True

    @Slot(result=bool)
    def useSelectedAsReplacement(self) -> bool:  # noqa: N802
        selected = set(self._value_selection.values())
        values = [value for value in self._values if value.row_id in selected]
        if len(values) != 1:
            self.errorOccurred.emit(tr('qml.dynamic.community.select_one_replacement_value'))
            return False
        self.draftRequested.emit(self._draft_name(), '', values[0].value_text)
        self.draftPrepared.emit()
        return True

    @Slot()
    def shutdown(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._metadata_generation += 1
        self._task.shutdown()
        self._value_preview.shutdown()

    @Slot(object)
    def _on_task_succeeded(self, result: object) -> None:
        operation = self._operation
        self._operation = ''
        if operation == 'catalog' and isinstance(result, CatalogSnapshot):
            self._apply_catalog(result)
        elif operation == 'payload' and isinstance(result, _PresetPayload):
            self._apply_payload(result)
        elif operation == 'import' and isinstance(result, _ImportResult):
            self._apply_catalog(result.snapshot)
            self.notificationRequested.emit(
                tr('qml.dynamic.community.preset_imported_title'),
                tr('qml.dynamic.community.presets_added', count=result.imported_count),
                'success',
            )

    @Slot(str)
    def _on_task_failed(self, message: str) -> None:
        self._operation = ''
        self._set_status(message)
        self.errorOccurred.emit(message)

    def _apply_catalog(self, snapshot: CatalogSnapshot) -> None:
        self._presets = list(snapshot.presets)
        self._catalog_loaded = True
        self._metadata.clear()
        self._refresh_catalog_model()
        self._set_status(snapshot.warning)
        self.catalogLoadedChanged.emit()
        self._schedule_metadata()

    def _apply_payload(self, payload: _PresetPayload) -> None:
        if payload.generation != self._payload_generation:
            self._set_status('')
            return
        self._selected_preset_id = payload.preset_id
        self._selected_preset_name = payload.preset_name
        self._selected_payload_kind = payload.kind
        self._values = list(payload.values)
        self._value_tree_model.set_document(payload.document, payload.values)
        self._payload_open = True
        self._value_query = ''
        self._value_selection.clear()
        self._sync_value_preview()
        self._refresh_value_model()
        self._set_status('')
        self.valueQueryChanged.emit()
        self.payloadChanged.emit()

    def _schedule_metadata(self) -> None:
        self._metadata_generation += 1
        generation = self._metadata_generation
        requests = [
            (preset.preset_id, preset.place_id)
            for preset in self._presets
            if preset.place_id is not None
        ]
        self._metadata_threads = [thread for thread in self._metadata_threads if thread.is_alive()]
        worker_count = min(4, len(requests))
        for worker_index in range(worker_count):
            batch = requests[worker_index::worker_count]
            thread = threading.Thread(
                target=self._fetch_metadata_batch,
                args=(generation, batch),
                name='fleasion-preset-metadata',
                daemon=True,
            )
            self._metadata_threads.append(thread)
            thread.start()

    def _fetch_metadata_batch(
        self,
        generation: int,
        requests: list[tuple[str, int]],
    ) -> None:
        for preset_id, place_id in requests:
            if self._disposed or generation != self._metadata_generation:
                return
            try:
                metadata = self._metadata_client.fetch(place_id)
            except Exception:
                continue
            if self._disposed or generation != self._metadata_generation:
                return
            self._metadataResult.emit(generation, preset_id, metadata)

    @Slot(int, str, object)
    def _apply_metadata(self, generation: int, preset_id: str, result: object) -> None:
        if generation != self._metadata_generation or not isinstance(result, PresetMetadata):
            return
        self._metadata[preset_id] = result
        self._refresh_catalog_model()

    def _refresh_catalog_model(self) -> None:
        rows = [self._preset_row(preset) for preset in self._presets]
        if self._query:
            rows = [row for row in rows if self._query in str(row['searchText']).casefold()]
        self._catalog_model.replace_items(rows)
        self.catalogModelChanged.emit()

    def _refresh_value_model(self) -> None:
        rows = [self._value_row(value) for value in self._values]
        if self._value_query:
            rows = [row for row in rows if self._value_query in str(row['searchText']).casefold()]
        self._value_model.replace_items(rows)
        self.valueModelChanged.emit()

    def _preset_row(self, preset: CommunityPreset) -> dict[str, object]:
        metadata = self._metadata.get(preset.preset_id, PresetMetadata())
        name = metadata.name or preset.name
        created = (metadata.created or preset.created)[:10]
        updated = (metadata.updated or preset.updated)[:10]
        return {
            'presetId': preset.preset_id,
            'name': name,
            'credit': preset.credit,
            'created': created,
            'updated': updated,
            'placeId': str(preset.place_id) if preset.place_id is not None else '',
            'hasOriginals': bool(preset.originals_source),
            'hasReplacements': bool(preset.replacements_source),
            'isCustom': preset.is_custom,
            'thumbnailUrl': metadata.thumbnail_url,
            'searchText': f'{name} {preset.search_text}',
        }

    @staticmethod
    def _value_row(value: PresetValue) -> dict[str, object]:
        return {
            'rowId': value.row_id,
            'path': value.path,
            'label': value.label,
            'valueText': value.value_text,
            'kind': value.kind,
            'searchText': value.search_text,
        }

    def _preset(self, preset_id: str) -> CommunityPreset | None:
        return next(
            (preset for preset in self._presets if preset.preset_id == preset_id),
            None,
        )

    @staticmethod
    def _source_for_kind(preset: CommunityPreset | None, kind: str) -> str:
        if preset is None:
            return ''
        if kind == 'originals':
            return preset.originals_source
        if kind == 'replacements':
            return preset.replacements_source
        return ''

    def _draft_name(self) -> str:
        return f'{self._selected_preset_name} preset'

    def _selected_values(self) -> list[PresetValue]:
        selected = set(self._value_selection.values())
        return [value for value in self._values if value.row_id in selected]

    @Slot()
    def _sync_value_preview(self) -> None:
        values = self._selected_values()
        if len(values) != 1:
            self._value_preview.clear()
            return
        selected = values[0]
        self._value_preview.load_async(
            'Loading selected value preview…',
            lambda cancel_event: self._value_resolver.resolve(selected, cancel_event),
        )

    @Slot(str)
    def _load_preview_child(self, asset_id: str) -> None:
        self._value_preview.load_child_async(
            asset_id,
            'Loading TexturePack map…',
            lambda cancel_event: self._value_resolver.resolve_asset_id(
                asset_id,
                cancel_event,
                label=f'Texture map {asset_id}',
            ),
        )

    def _set_status(self, value: str) -> None:
        if value == self._status:
            return
        self._status = value
        self.statusChanged.emit()


def _unique_values(values: Iterable[int | str]) -> list[int | str]:
    unique: list[int | str] = []
    seen: set[int | str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


__all__ = ['CommunityPresetsApi']
