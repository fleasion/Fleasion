"""Roblox file modification bridge for QML."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from ..modifications.catalog import (
    ModificationCatalogEntry,
    built_in_modifications,
    detect_modification_source,
    head_variant_entries,
)
from ..modifications.fflag_catalog import FastFlagCatalog
from ..modifications.fflag_profiles import FastFlagProfileManager
from ..modifications.manager import MOD_ORIGINALS_DIR, normalise_target_path
from ..modifications.stash_paths import resource_stash_dir
from ..proxy.addons.custom_fflags import normalize_custom_fflags
from .modification_hotkeys import CustomFastFlagHotkeys
from .modification_inspector import ModificationInspector
from .models import DictListModel, SelectionModel
from .tasks import TaskState

if TYPE_CHECKING:
    from ..config.manager import ConfigManager
    from ..modifications.manager import ModificationManager

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_ENTRY_ROLES: Final = (
    'entryId',
    'name',
    'targetPath',
    'sourceType',
    'sourceValue',
    'sourceName',
    'status',
    'errorMessage',
)
_FAST_FLAG_ROLES: Final = (
    'name',
    'value',
    'family',
    'searchText',
    'enabled',
    'keybind',
    'hasKeybind',
)
_CATALOG_ROLES: Final = ('name', 'value', 'family', 'published', 'searchText')
_PROFILE_ROLES: Final = ('name',)
_ORPHAN_ROLES: Final = (
    'name',
    'targetPath',
    'installationCount',
    'backupCount',
    'createdCount',
    'sizeText',
    'kind',
)
_BUILT_IN_ROLES: Final = (
    'catalogKey',
    'category',
    'name',
    'targetPath',
    'fileFilter',
    'muteAvailable',
    'supported',
    'limitation',
    'configured',
    'entryId',
    'sourceType',
    'sourceValue',
    'sourceName',
    'status',
    'errorMessage',
    'optional',
)
_HEAD_VARIANT_ROLES: Final = ('catalogKey', 'name')
_RENDERING_MODES: Final = frozenset({'Default', 'D3D11', 'Vulkan', 'OpenGL'})
_MSAA_LEVELS: Final = frozenset({'Default', '1', '2', '4'})
_TEXTURE_LEVELS: Final = frozenset({'Default', '0', '1', '2', '3'})
_FAST_FLAG_FAMILIES: Final = ('All', *FastFlagCatalog.FAMILIES, 'Other')
_MAX_FAST_FLAG_IMPORT_BYTES: Final = 8 * 1024 * 1024
_MAX_FAST_FLAGS: Final = 10_000
_PRESET_DEFAULTS: Final[dict[str, str | bool | int | None]] = {
    'rendering_mode': 'Default',
    'msaa': 'Default',
    'disable_dpi_scale': False,
    'alt_enter_fullscreen': False,
    'texture_quality': 'Default',
    'mesh_lod_enabled': False,
    'mesh_lod': 4,
    'frm_quality_enabled': False,
    'frm_quality': 21,
    'grey_sky': False,
    'pause_voxelizer': False,
    'grass_max': None,
    'grass_min': None,
    'grass_motion': None,
}


def _bounded_int(value: object, *, default: int, maximum: int) -> int:
    if not isinstance(value, str | int | float):
        return default
    try:
        parsed = int(value)
    except OverflowError, ValueError:
        parsed = default
    return max(0, min(parsed, maximum))


def _format_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{value} B'


@QmlElement
class ModificationsApi(QObject):
    """Adapt ``ModificationManager`` entries to a QML list model."""

    modelChanged = Signal()
    fastFlagsEnabledChanged = Signal()
    customFastFlagsWarningAcceptedChanged = Signal()
    allowlistedFastFlagsEnabledChanged = Signal()
    presetSettingsChanged = Signal()
    presetDirtyChanged = Signal()
    statusChanged = Signal()
    hotkeyCaptureChanged = Signal()
    hotkeyCaptureCompleted = Signal(str, str)
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(
        self,
        manager: ModificationManager | None = None,
        parent: QObject | None = None,
        profile_manager: FastFlagProfileManager | None = None,
        *,
        config_manager: ConfigManager | None = None,
        proxy_master: Any | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._config = config_manager
        self._proxy_master = proxy_master
        self._model = DictListModel(_ENTRY_ROLES, parent=self)
        self._custom_model = DictListModel(_ENTRY_ROLES, parent=self)
        self._fast_flags_model = DictListModel(_FAST_FLAG_ROLES, parent=self)
        self._catalog_model = DictListModel(_CATALOG_ROLES, parent=self)
        self._profiles_model = DictListModel(_PROFILE_ROLES, parent=self)
        self._orphaned_model = DictListModel(_ORPHAN_ROLES, parent=self)
        self._orphaned_targets: set[str] = set()
        self._inspector = ModificationInspector(manager, self)
        self._hotkeys = CustomFastFlagHotkeys(config_manager, proxy_master, self)
        self._catalog_selection = SelectionModel(self)
        self._built_in_entries = built_in_modifications()
        self._built_in_by_key = {entry.key: entry for entry in self._built_in_entries}
        self._head_variant_keys = {entry.key for entry in head_variant_entries()}
        self._visible_head_variants = self._configured_head_variant_keys()
        self._built_in_models = {
            category: DictListModel(_BUILT_IN_ROLES, parent=self)
            for category in (
                'skybox',
                'indoor_skybox',
                'textures',
                'avatar_meshes',
                'sounds',
                'fonts',
            )
        }
        self._available_head_variants_model = DictListModel(_HEAD_VARIANT_ROLES, parent=self)
        self._catalog_task = TaskState(self)
        self._preset_task = TaskState(self)
        self._catalog_values: dict[str, str | None] = {}
        self._catalog_query = ''
        self._fast_flag_query = ''
        self._fast_flag_family = 'All'
        self._preset_settings = self._normalise_preset_settings(
            {} if manager is None else manager.fast_flags
        )
        self._migrate_misfiled_custom_flags()
        self._preset_revision = 1 if self._preset_settings != _PRESET_DEFAULTS else 0
        self._preset_applied_revision = (
            self._preset_revision if manager is not None and manager.fast_flags_enabled else 0
        )
        self._preset_apply_pending = False
        self._disposed = False
        self._profiles = profile_manager or FastFlagProfileManager()
        self._catalog_task.succeeded.connect(self._apply_catalog)
        self._catalog_task.failed.connect(self.errorOccurred)
        self._preset_task.succeeded.connect(self._on_preset_task_succeeded)
        self._preset_task.failed.connect(self._on_preset_task_failed)
        self._inspector.errorOccurred.connect(self.errorOccurred)
        self._inspector.notificationRequested.connect(self.notificationRequested)
        self._hotkeys.errorOccurred.connect(self.errorOccurred)
        self._hotkeys.captureChanged.connect(self.hotkeyCaptureChanged)
        self._hotkeys.captureCompleted.connect(self._on_hotkey_capture_completed)
        self._hotkeys.toggled.connect(self._on_hotkey_toggled)
        self._manager_connected = False
        if manager is not None:
            manager.entry_status_changed.connect(self._on_entry_status)
            manager.apply_finished.connect(self._on_apply_finished)
            manager.restore_finished.connect(self.refresh)
            self._manager_connected = True
        self.refresh()

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(QObject, constant=True)
    def customModel(self) -> QObject:  # noqa: N802
        return self._custom_model

    @Property(QObject, constant=True)
    def orphanedModel(self) -> QObject:  # noqa: N802
        return self._orphaned_model

    @Property(QObject, constant=True)
    def inspector(self) -> QObject:
        return self._inspector

    @Property(QObject, constant=True)
    def skyboxModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['skybox']

    @Property(QObject, constant=True)
    def indoorSkyboxModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['indoor_skybox']

    @Property(QObject, constant=True)
    def texturesModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['textures']

    @Property(QObject, constant=True)
    def avatarMeshesModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['avatar_meshes']

    @Property(QObject, constant=True)
    def soundsModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['sounds']

    @Property(QObject, constant=True)
    def fontsModel(self) -> QObject:  # noqa: N802
        return self._built_in_models['fonts']

    @Property(QObject, constant=True)
    def availableHeadVariantsModel(self) -> QObject:  # noqa: N802
        return self._available_head_variants_model

    @Property(str, constant=True)
    def soberMeshLimitation(self) -> str:  # noqa: N802
        return next(
            (
                entry.limitation
                for entry in self._built_in_entries
                if entry.category == 'avatar_meshes' and entry.limitation
            ),
            '',
        )

    @Property(QObject, constant=True)
    def fastFlagsModel(self) -> QObject:  # noqa: N802
        return self._fast_flags_model

    @Property(list, constant=True)
    def fastFlagFamilies(self) -> list[str]:  # noqa: N802
        return list(_FAST_FLAG_FAMILIES)

    @Property(bool, constant=True)
    def linuxHotkeyPermissionSetupAvailable(self) -> bool:  # noqa: N802
        return self._hotkeys.permission_setup_available

    @Property(bool, constant=True)
    def hotkeysSupported(self) -> bool:  # noqa: N802
        return self._hotkeys.supported

    @Property(bool, notify=hotkeyCaptureChanged)
    def hotkeyCaptureBusy(self) -> bool:  # noqa: N802
        return self._hotkeys.capture_busy

    @Property(str, notify=hotkeyCaptureChanged)
    def hotkeyCaptureMessage(self) -> str:  # noqa: N802
        return self._hotkeys.capture_message

    @Property(str, constant=True)
    def primaryRobloxDirectoryUrl(self) -> str:  # noqa: N802
        if self._manager is None:
            return ''
        directories = list(getattr(self._manager, 'roblox_dirs', ()))
        return QUrl.fromLocalFile(str(directories[0])).toString() if directories else ''

    @Property(QObject, constant=True)
    def catalogModel(self) -> QObject:  # noqa: N802
        return self._catalog_model

    @Property(QObject, constant=True)
    def catalogSelection(self) -> QObject:  # noqa: N802
        return self._catalog_selection

    @Property(QObject, constant=True)
    def catalogTask(self) -> QObject:  # noqa: N802
        return self._catalog_task

    @Property(QObject, constant=True)
    def presetTask(self) -> QObject:  # noqa: N802
        return self._preset_task

    @Property(QObject, constant=True)
    def profilesModel(self) -> QObject:  # noqa: N802
        return self._profiles_model

    @Property(bool, notify=fastFlagsEnabledChanged)
    def fastFlagsEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._config is not None and self._config.custom_fflags_enabled)

    @fastFlagsEnabled.setter  # pyright: ignore[reportRedeclaration]
    def fastFlagsEnabled(self, enabled: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if self._config is None or enabled == self._config.custom_fflags_enabled:
            return
        self._config.custom_fflags_enabled = enabled
        if enabled:
            warning_was_accepted = bool(
                getattr(self._config, 'custom_fflags_warning_accepted', False)
            )
            self._config.custom_fflags_warning_accepted = True
            if not warning_was_accepted:
                self.customFastFlagsWarningAcceptedChanged.emit()
        self._hotkeys.sync()
        self._refresh_custom_fflag_interception()
        self.fastFlagsEnabledChanged.emit()

    @Property(bool, notify=customFastFlagsWarningAcceptedChanged)
    def customFastFlagsWarningAccepted(self) -> bool:  # noqa: N802
        return bool(
            self._config is not None
            and getattr(self._config, 'custom_fflags_warning_accepted', False)
        )

    @Property(bool, constant=True)
    def customFastFlagsAvailable(self) -> bool:  # noqa: N802
        return self._config is not None and self._proxy_master is not None

    @Property(bool, notify=allowlistedFastFlagsEnabledChanged)
    def allowlistedFastFlagsEnabled(self) -> bool:  # noqa: N802
        return bool(self._manager is not None and self._manager.fast_flags_enabled)

    @Property(bool, notify=presetDirtyChanged)
    def presetDirty(self) -> bool:  # noqa: N802
        return self._preset_revision != self._preset_applied_revision

    @Property(str, notify=presetSettingsChanged)
    def presetRenderingMode(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return str(self._preset_settings['rendering_mode'])

    @presetRenderingMode.setter  # pyright: ignore[reportRedeclaration]
    def presetRenderingMode(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('rendering_mode', value if value in _RENDERING_MODES else 'Default')

    @Property(str, notify=presetSettingsChanged)
    def presetMsaa(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return str(self._preset_settings['msaa'])

    @presetMsaa.setter  # pyright: ignore[reportRedeclaration]
    def presetMsaa(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('msaa', value if value in _MSAA_LEVELS else 'Default')

    @Property(bool, notify=presetSettingsChanged)
    def presetDisableDpiScale(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['disable_dpi_scale'])

    @presetDisableDpiScale.setter  # pyright: ignore[reportRedeclaration]
    def presetDisableDpiScale(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('disable_dpi_scale', value)

    @Property(bool, notify=presetSettingsChanged)
    def presetAltEnterFullscreen(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['alt_enter_fullscreen'])

    @presetAltEnterFullscreen.setter  # pyright: ignore[reportRedeclaration]
    def presetAltEnterFullscreen(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('alt_enter_fullscreen', value)

    @Property(str, notify=presetSettingsChanged)
    def presetTextureQuality(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return str(self._preset_settings['texture_quality'])

    @presetTextureQuality.setter  # pyright: ignore[reportRedeclaration]
    def presetTextureQuality(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('texture_quality', value if value in _TEXTURE_LEVELS else 'Default')

    @Property(bool, notify=presetSettingsChanged)
    def presetMeshLodEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['mesh_lod_enabled'])

    @presetMeshLodEnabled.setter  # pyright: ignore[reportRedeclaration]
    def presetMeshLodEnabled(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('mesh_lod_enabled', value)

    @Property(int, notify=presetSettingsChanged)
    def presetMeshLod(self) -> int:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return int(self._preset_settings['mesh_lod'] or 0)

    @presetMeshLod.setter  # pyright: ignore[reportRedeclaration]
    def presetMeshLod(self, value: int) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('mesh_lod', max(0, min(value, 4)))

    @Property(bool, notify=presetSettingsChanged)
    def presetFrmEnabled(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['frm_quality_enabled'])

    @presetFrmEnabled.setter  # pyright: ignore[reportRedeclaration]
    def presetFrmEnabled(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('frm_quality_enabled', value)

    @Property(int, notify=presetSettingsChanged)
    def presetFrmQuality(self) -> int:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return int(self._preset_settings['frm_quality'] or 0)

    @presetFrmQuality.setter  # pyright: ignore[reportRedeclaration]
    def presetFrmQuality(self, value: int) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('frm_quality', max(0, min(value, 21)))

    @Property(bool, notify=presetSettingsChanged)
    def presetGreySky(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['grey_sky'])

    @presetGreySky.setter  # pyright: ignore[reportRedeclaration]
    def presetGreySky(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('grey_sky', value)

    @Property(bool, notify=presetSettingsChanged)
    def presetPauseVoxelizer(self) -> bool:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return bool(self._preset_settings['pause_voxelizer'])

    @presetPauseVoxelizer.setter  # pyright: ignore[reportRedeclaration]
    def presetPauseVoxelizer(self, value: bool) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('pause_voxelizer', value)

    @Property(int, notify=presetSettingsChanged)
    def presetGrassMax(self) -> int:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return int(self._preset_settings['grass_max'] or 0)

    @presetGrassMax.setter  # pyright: ignore[reportRedeclaration]
    def presetGrassMax(self, value: int) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('grass_max', max(0, min(value, 100_000)) or None)

    @Property(int, notify=presetSettingsChanged)
    def presetGrassMin(self) -> int:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return int(self._preset_settings['grass_min'] or 0)

    @presetGrassMin.setter  # pyright: ignore[reportRedeclaration]
    def presetGrassMin(self, value: int) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('grass_min', max(0, min(value, 100_000)) or None)

    @Property(int, notify=presetSettingsChanged)
    def presetGrassMotion(self) -> int:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return int(self._preset_settings['grass_motion'] or 0)

    @presetGrassMotion.setter  # pyright: ignore[reportRedeclaration]
    def presetGrassMotion(self, value: int) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        self._set_preset_value('grass_motion', max(0, min(value, 100_000)) or None)

    @Property(int, notify=statusChanged)
    def appliedCount(self) -> int:  # noqa: N802
        return sum(row.get('status') == 'applied' for row in self._model.snapshot())

    @Property(int, notify=statusChanged)
    def problemCount(self) -> int:  # noqa: N802
        return sum(
            row.get('status') in {'error', 'restore_failed'} for row in self._model.snapshot()
        )

    @Property(int, notify=statusChanged)
    def framerateCap(self) -> int:  # noqa: N802
        return 0 if self._manager is None else self._manager.framerate_cap

    @Slot()
    def refresh(self) -> None:
        if self._disposed:
            return
        entries = [] if self._manager is None else self._manager.entries
        self._refresh_orphaned_stashes(entries)
        rows: list[dict[str, Any]] = []
        for entry in entries:
            source = str(entry.get('source_value') or '')
            source_type = str(entry.get('source_type') or '')
            rows.append(
                {
                    'entryId': str(entry.get('id', '')),
                    'name': str(
                        entry.get('display_name')
                        or Path(str(entry.get('target_path', '')).replace('\\', '/')).name
                    ),
                    'targetPath': str(entry.get('target_path', '')),
                    'sourceType': source_type,
                    'sourceValue': source,
                    'sourceName': self._source_name(source_type, source),
                    'status': str(entry.get('status', 'not_set')),
                    'errorMessage': str(entry.get('error_message') or ''),
                }
            )
        self._model.replace_items(rows)
        built_in_targets = {self._target_key(entry.target_path) for entry in self._built_in_entries}
        self._custom_model.replace_items(
            row for row in rows if self._target_key(str(row['targetPath'])) not in built_in_targets
        )
        self._refresh_built_in_models()
        self._refresh_fast_flags()
        self.refreshProfiles()
        self.modelChanged.emit()
        self.statusChanged.emit()

    @Slot(str, str, str, result=bool)
    def addModification(self, name: str, target_path: str, source_value: str) -> bool:  # noqa: N802
        if self._manager is None:
            return False
        try:
            target = normalise_target_path(target_path).as_posix()
            source_type, source = detect_modification_source(
                target, self._source_input(source_value)
            )
            self._manager.add_entry(
                {
                    'display_name': name.strip() or Path(target).name,
                    'target_path': target,
                    'source_type': source_type,
                    'source_value': source,
                }
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.refresh()
        return True

    @Slot(str, str, result=bool)
    def replaceSource(self, entry_id: str, source_value: str) -> bool:  # noqa: N802
        if self._manager is None:
            return False
        entry = next(
            (value for value in self._manager.entries if str(value.get('id', '')) == entry_id),
            None,
        )
        if entry is None:
            self.errorOccurred.emit('The selected modification no longer exists.')
            return False
        try:
            source_type, source = detect_modification_source(
                str(entry.get('target_path', '')),
                self._source_input(source_value),
            )
            result = self._manager.update_entry(
                entry_id,
                source_type=source_type,
                source_value=source,
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.refresh()
        return result

    @Slot(str, result=str)
    def relativeTargetPath(self, value: str) -> str:  # noqa: N802
        if self._manager is None:
            return ''
        selected = self._local_path(value)
        try:
            selected = selected.resolve(strict=True)
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return ''
        for raw_root in getattr(self._manager, 'roblox_dirs', ()):
            try:
                root = Path(raw_root).resolve(strict=True)
                relative = normalise_target_path(selected.relative_to(root)).as_posix()
            except OSError, ValueError:
                continue
            return relative
        self.errorOccurred.emit(
            'Choose a target file inside one of the detected Roblox resource directories.'
        )
        return ''

    @Slot(str, result=bool)
    def restoreOrphanedStash(self, target_path: str) -> bool:  # noqa: N802
        if self._manager is None:
            return False
        restore = getattr(self._manager, 'restore_orphaned_stash', None)
        if not callable(restore) or not restore(target_path):
            self.errorOccurred.emit('The original backup could not be restored.')
            return False
        self.refresh()
        self.notificationRequested.emit(
            'Original restored',
            target_path,
            'success',
        )
        return True

    @Slot(str, result=bool)
    def resetEntry(self, entry_id: str) -> bool:  # noqa: N802
        if self._manager is None:
            return False
        result = self._manager.clear_entry(entry_id)
        self.refresh()
        return result

    @Slot(str, str, result=bool)
    def applyBuiltIn(self, catalog_key: str, source: str) -> bool:  # noqa: N802
        definition = self._built_in_by_key.get(catalog_key)
        if definition is None:
            self.errorOccurred.emit('The selected built-in modification does not exist.')
            return False
        if not definition.supported:
            self.errorOccurred.emit(definition.limitation)
            return False
        try:
            source_type, source_value = detect_modification_source(definition.target_path, source)
        except (FileNotFoundError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self._apply_built_in_source(definition, source_type, source_value)
        self.notificationRequested.emit(
            'Modification queued',
            definition.name,
            'success',
        )
        return True

    @Slot(str, result=bool)
    def muteBuiltIn(self, catalog_key: str) -> bool:  # noqa: N802
        definition = self._built_in_by_key.get(catalog_key)
        if definition is None or not definition.mute_source:
            self.errorOccurred.emit('This built-in modification does not have a mute source.')
            return False
        self._apply_built_in_source(definition, 'bundled', definition.mute_source)
        return True

    @Slot(str, result=bool)
    def resetBuiltIn(self, catalog_key: str) -> bool:  # noqa: N802
        definition = self._built_in_by_key.get(catalog_key)
        if definition is None or self._manager is None:
            return False
        entry = self._manager_entry_for_target(definition.target_path)
        if entry is None:
            if self._target_key(definition.target_path) in self._orphaned_targets:
                return self.restoreOrphanedStash(definition.target_path)
            return True
        result = self._manager.clear_entry(str(entry.get('id', '')))
        self.refresh()
        return result

    @Slot(str, result=bool)
    def addHeadVariant(self, catalog_key: str) -> bool:  # noqa: N802
        if catalog_key not in self._head_variant_keys:
            return False
        self._visible_head_variants.add(catalog_key)
        self._refresh_built_in_models()
        return True

    @Slot(str, result=bool)
    def removeHeadVariant(self, catalog_key: str) -> bool:  # noqa: N802
        if catalog_key not in self._head_variant_keys:
            return False
        definition = self._built_in_by_key[catalog_key]
        entry = self._manager_entry_for_target(definition.target_path)
        if entry is not None and self._manager is not None:
            if not self._manager.clear_entry(str(entry.get('id', ''))):
                return False
        self._visible_head_variants.discard(catalog_key)
        self._refresh_built_in_models()
        return True

    @Slot(str, result=bool)
    def applySkyToAll(self, source: str) -> bool:  # noqa: N802
        sky_entries = [entry for entry in self._built_in_entries if entry.category == 'skybox']
        try:
            resolved = [
                (entry, *detect_modification_source(entry.target_path, source))
                for entry in sky_entries
            ]
        except (FileNotFoundError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        for entry, source_type, source_value in resolved:
            self._apply_built_in_source(entry, source_type, source_value)
        self.notificationRequested.emit(
            'Skybox queued',
            'The source will be applied to all six outdoor sky faces.',
            'success',
        )
        return True

    @Slot()
    def reapplyAll(self) -> None:  # noqa: N802
        if self._manager is not None:
            self._manager.reapply_all()

    @Slot()
    def restoreAll(self) -> None:  # noqa: N802
        if self._manager is not None:
            self._manager.restore_all()
            self.refresh()

    @Slot(int)
    def setFramerateCap(self, value: int) -> None:  # noqa: N802
        if self._manager is not None:
            self._manager.framerate_cap = max(0, value)
            self._manager.sync_saved_global_settings()
            self.statusChanged.emit()

    @Slot(result=bool)
    def applyAllowlistedFastFlags(self) -> bool:  # noqa: N802
        """Persist and write the allowlisted preset draft without blocking QML."""
        manager = self._manager
        if manager is None:
            return False
        if self._preset_task.busy:
            self._preset_apply_pending = True
            return True
        self._preset_apply_pending = False
        settings = dict(self._preset_settings)
        revision = self._preset_revision

        def operation() -> tuple[str, int]:
            manager.write_fast_flags(settings)
            return ('apply', revision)

        return self._preset_task.run('Applying allowlisted FastFlags…', operation)

    @Slot(result=bool)
    def resetAllowlistedFastFlags(self) -> bool:  # noqa: N802
        """Restore preset-managed files and return every preset to its default."""
        manager = self._manager
        if manager is None or self._preset_task.busy:
            return False
        was_dirty = self.presetDirty
        self._preset_settings = dict(_PRESET_DEFAULTS)
        self._preset_revision += 1
        self.presetSettingsChanged.emit()
        if self.presetDirty != was_dirty:
            self.presetDirtyChanged.emit()
        revision = self._preset_revision

        def operation() -> tuple[str, int]:
            manager.fast_flags = dict(_PRESET_DEFAULTS)
            manager.framerate_cap = 0
            manager.fast_flags_enabled = False
            manager.reset_framerate_cap()
            return ('reset', revision)

        return self._preset_task.run('Restoring default FastFlag settings…', operation)

    @Slot(str, str, result=bool)
    def setFastFlag(self, name: str, value: str) -> bool:  # noqa: N802
        if self._config is None or not name.strip():
            return False
        flags = self._custom_fast_flags()
        flags[name.strip()] = value.strip()
        self._save_custom_fast_flags(flags)
        return True

    @Slot(str)
    def removeFastFlag(self, name: str) -> None:  # noqa: N802
        if self._config is None:
            return
        flags = self._custom_fast_flags()
        flags.pop(name, None)
        self._save_custom_fast_flags(flags)

    @Slot(str, str)
    def filterFastFlags(self, query: str, family: str) -> None:  # noqa: N802
        normalized_query = query.strip().casefold()
        normalized_family = family if family in _FAST_FLAG_FAMILIES else 'All'
        if (
            normalized_query == self._fast_flag_query
            and normalized_family == self._fast_flag_family
        ):
            return
        self._fast_flag_query = normalized_query
        self._fast_flag_family = normalized_family
        self._refresh_fast_flags()

    @Slot(str, bool, result=bool)
    def setFastFlagEnabled(self, name: str, enabled: bool) -> bool:  # noqa: N802
        if self._config is None or name not in self._config.custom_fflags:
            return False
        disabled = set(self._config.custom_fflag_disabled)
        if enabled:
            disabled.discard(name)
        else:
            disabled.add(name)
        self._config.custom_fflag_disabled = sorted(disabled)
        self._hotkeys.sync()
        self._refresh_custom_fflag_interception()
        self._refresh_fast_flags()
        return True

    @Slot(str, result=bool)
    def beginFastFlagHotkeyCapture(self, name: str) -> bool:  # noqa: N802
        return self._hotkeys.begin_capture(name)

    @Slot()
    def cancelFastFlagHotkeyCapture(self) -> None:  # noqa: N802
        self._hotkeys.cancel_capture()

    @Slot(int, int, int, result=bool)
    def captureFastFlagNativeKey(  # noqa: N802
        self,
        native_scan_code: int,
        qt_key: int,
        modifiers: int,
    ) -> bool:
        return self._hotkeys.capture_native_key(
            native_scan_code,
            qt_key,
            modifiers,
        )

    @Slot(int, int, result=bool)
    def releaseFastFlagNativeKey(  # noqa: N802
        self,
        native_scan_code: int,
        qt_key: int,
    ) -> bool:
        return self._hotkeys.release_native_key(native_scan_code, qt_key)

    @Slot(str, int, int, result=bool)
    def captureFastFlagPointer(  # noqa: N802
        self, kind: str, code: int, modifiers: int
    ) -> bool:
        return self._hotkeys.capture_pointer(kind, code, modifiers)

    @Slot(str, result=bool)
    def clearFastFlagHotkey(self, name: str) -> bool:  # noqa: N802
        result = self._hotkeys.clear_binding(name)
        if result:
            self._refresh_fast_flags()
        return result

    @Slot(result=bool)
    def setupLinuxHotkeyPermissions(self) -> bool:  # noqa: N802
        return self._hotkeys.setup_linux_permissions()

    @Slot(str, bool, result=bool)
    def importFastFlagsJson(self, text: str, replace: bool = False) -> bool:  # noqa: N802
        if len(text.encode('utf-8')) > _MAX_FAST_FLAG_IMPORT_BYTES:
            self.errorOccurred.emit('The FastFlag JSON exceeds the 8 MiB import limit.')
            return False
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            self.errorOccurred.emit(f'The FastFlag JSON is invalid: {exc.msg}.')
            return False
        if not isinstance(payload, dict):
            self.errorOccurred.emit('The JSON root must contain FastFlag name/value pairs.')
            return False
        if len(payload) > _MAX_FAST_FLAGS:
            self.errorOccurred.emit('The FastFlag JSON exceeds the 10,000-entry limit.')
            return False
        normalized = normalize_custom_fflags(payload)
        if len(normalized) != len(payload):
            self.errorOccurred.emit('Every FastFlag value must be a string, number, or boolean.')
            return False
        current = {} if replace else self._custom_fast_flags()
        current.update(normalized)
        if self._config is None:
            return False
        self._save_custom_fast_flags(current)
        self.notificationRequested.emit(
            'FastFlags imported',
            f'{len(normalized)} value{"" if len(normalized) == 1 else "s"} loaded',
            'success',
        )
        return True

    @Slot(str, bool, result=bool)
    def importFastFlagsFile(self, value: str, replace: bool = False) -> bool:  # noqa: N802
        path = self._local_path(value)
        try:
            if path.stat().st_size > _MAX_FAST_FLAG_IMPORT_BYTES:
                self.errorOccurred.emit('The FastFlag JSON exceeds the 8 MiB import limit.')
                return False
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            self.errorOccurred.emit('The FastFlag file must contain UTF-8 JSON text.')
            return False
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        return self.importFastFlagsJson(text, replace)

    @Slot(result=str)
    def fastFlagsJson(self) -> str:  # noqa: N802
        return json.dumps(self._custom_fast_flags(), indent=2, ensure_ascii=False) + '\n'

    @Slot(str, result=bool)
    def exportFastFlags(self, value: str) -> bool:  # noqa: N802
        destination = self._local_path(value)
        if destination.suffix.casefold() != '.json':
            destination = destination.with_suffix('.json')
        try:
            destination.write_text(self.fastFlagsJson(), encoding='utf-8')
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.notificationRequested.emit('FastFlags exported', str(destination), 'success')
        return True

    @Slot()
    def refreshProfiles(self) -> None:  # noqa: N802
        self._profiles_model.replace_items(
            {'name': name} for name in self._profiles.list_profiles()
        )

    @Slot(str, result=bool)
    def saveProfile(self, name: str) -> bool:  # noqa: N802
        try:
            saved_name = self._profiles.save(name, self._custom_fast_flags())
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.refreshProfiles()
        self.notificationRequested.emit('FastFlag profile saved', saved_name, 'success')
        return True

    @Slot(str, bool, result=bool)
    def loadProfile(self, name: str, replace: bool) -> bool:  # noqa: N802
        if self._config is None:
            return False
        try:
            loaded = self._profiles.load(name)
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        flags = {} if replace else self._custom_fast_flags()
        flags.update(loaded)
        self._save_custom_fast_flags(flags)
        self.notificationRequested.emit('FastFlag profile loaded', name, 'success')
        return True

    @Slot(str, str, result=bool)
    def renameProfile(self, old_name: str, new_name: str) -> bool:  # noqa: N802
        try:
            self._profiles.rename(old_name, new_name)
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.refreshProfiles()
        return True

    @Slot(str, result=bool)
    def deleteProfile(self, name: str) -> bool:  # noqa: N802
        try:
            self._profiles.delete(name)
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.refreshProfiles()
        return True

    @Slot()
    def shutdown(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        if self._manager is not None and self._manager_connected:
            for signal, slot in (
                (self._manager.entry_status_changed, self._on_entry_status),
                (self._manager.apply_finished, self._on_apply_finished),
                (self._manager.restore_finished, self.refresh),
            ):
                try:
                    signal.disconnect(slot)
                except RuntimeError, TypeError:
                    pass
            self._manager_connected = False
        self._catalog_task.shutdown()
        self._preset_task.shutdown()
        self._hotkeys.shutdown()
        self._inspector.shutdown()

    @Slot(bool)
    def loadFastFlagCatalog(self, force: bool = False) -> None:  # noqa: N802
        self._catalog_task.run(
            'Retrieving Roblox FastFlags…',
            lambda: FastFlagCatalog.load(force=force),
        )

    @Slot(str)
    def filterFastFlagCatalog(self, query: str) -> None:  # noqa: N802
        normalized = query.strip().casefold()
        if normalized == self._catalog_query:
            return
        self._catalog_query = normalized
        self._refresh_catalog_model()

    @Slot(list, result=int)
    def addCatalogFlags(self, names: list[str]) -> int:  # noqa: N802
        if self._config is None:
            return 0
        flags = self._custom_fast_flags()
        added = 0
        for name in names:
            if name not in self._catalog_values or name in flags:
                continue
            flags[name] = self._catalog_values[name] or ''
            added += 1
        if added:
            self._save_custom_fast_flags(flags)
            self.notificationRequested.emit(
                'FastFlags added',
                f'{added} catalog entr{"y" if added == 1 else "ies"} added',
                'success',
            )
        self._catalog_selection.clear()
        return added

    @Slot(object)
    def _apply_catalog(self, values: object) -> None:
        if not isinstance(values, dict):
            self.errorOccurred.emit('The FastFlag catalog returned invalid data.')
            return
        self._catalog_values = {
            str(name): value if isinstance(value, str) else None
            for name, value in values.items()
            if isinstance(name, str)
        }
        self._refresh_catalog_model()

    def _refresh_fast_flags(self) -> None:
        flags = self._custom_fast_flags()
        disabled = set(self._config.custom_fflag_disabled) if self._config is not None else set()
        bindings = self._config.custom_fflag_keybinds if self._config is not None else {}
        self._fast_flags_model.replace_items(
            {
                'name': name,
                'value': str(value),
                'family': family,
                'searchText': f'{name} {value}',
                'enabled': name not in disabled,
                'keybind': self._hotkeys.binding_text(name),
                'hasKeybind': name in bindings,
            }
            for name, value in sorted(flags.items(), key=lambda item: item[0].casefold())
            if (
                (family := FastFlagCatalog.family_for(name))
                and (self._fast_flag_family == 'All' or family == self._fast_flag_family)
                and (
                    not self._fast_flag_query
                    or self._fast_flag_query in f'{name} {value}'.casefold()
                )
            )
        )

    def _refresh_orphaned_stashes(self, entries: list[dict[str, Any]]) -> None:
        manager = self._manager
        if manager is None:
            self._orphaned_targets = set()
            self._orphaned_model.replace_items(())
            return
        tracked = {self._target_key(str(entry.get('target_path', ''))) for entry in entries}
        stash_root = Path(getattr(manager, '_stash_dir', MOD_ORIGINALS_DIR))
        grouped: dict[str, dict[str, Any]] = {}
        for raw_roblox_dir in getattr(manager, 'roblox_dirs', ()):
            roblox_dir = Path(raw_roblox_dir)
            install_stash = resource_stash_dir(stash_root, roblox_dir)
            try:
                files = tuple(path for path in install_stash.rglob('*') if path.is_file())
            except OSError:
                continue
            for stash in files:
                try:
                    relative = stash.relative_to(install_stash).as_posix()
                except ValueError:
                    continue
                marker = relative.endswith('.fleasion_new')
                target_path = relative.removesuffix('.fleasion_new') if marker else relative
                target_key = self._target_key(target_path)
                if not target_key or target_key in tracked:
                    continue
                row = grouped.setdefault(
                    target_key,
                    {
                        'name': Path(target_path).name,
                        'targetPath': target_path,
                        'installations': set(),
                        'backups': set(),
                        'created': set(),
                        'size': 0,
                    },
                )
                row['installations'].add(roblox_dir.name)
                if marker:
                    row['created'].add(roblox_dir.name)
                else:
                    row['backups'].add(roblox_dir.name)
                    try:
                        row['size'] = int(row['size']) + stash.stat().st_size
                    except OSError:
                        pass
        self._orphaned_targets = set(grouped)
        for key in self._head_variant_keys:
            if self._target_key(self._built_in_by_key[key].target_path) in grouped:
                self._visible_head_variants.add(key)
        self._orphaned_model.replace_items(
            {
                'name': str(row['name']),
                'targetPath': str(row['targetPath']),
                'installationCount': len(row['installations']),
                'backupCount': len(row['backups']),
                'createdCount': len(row['created']),
                'sizeText': _format_bytes(int(row['size'])),
                'kind': (
                    'mixed'
                    if row['backups'] and row['created']
                    else 'backup'
                    if row['backups']
                    else 'created'
                ),
            }
            for _key, row in sorted(grouped.items())
        )

    @staticmethod
    def _target_key(value: str) -> str:
        return value.replace('\\', '/').strip('/').casefold()

    def _configured_head_variant_keys(self) -> set[str]:
        if self._manager is None:
            return set()
        configured_targets = {
            self._target_key(str(entry.get('target_path', ''))) for entry in self._manager.entries
        }
        return {
            key
            for key in self._head_variant_keys
            if self._target_key(self._built_in_by_key[key].target_path) in configured_targets
        }

    def _manager_entry_for_target(self, target_path: str) -> dict[str, Any] | None:
        if self._manager is None:
            return None
        target_key = self._target_key(target_path)
        return next(
            (
                entry
                for entry in self._manager.entries
                if self._target_key(str(entry.get('target_path', ''))) == target_key
            ),
            None,
        )

    def _refresh_built_in_models(self) -> None:
        rows_by_category: dict[str, list[dict[str, Any]]] = {
            category: [] for category in self._built_in_models
        }
        for definition in self._built_in_entries:
            optional = definition.key in self._head_variant_keys
            if optional and definition.key not in self._visible_head_variants:
                continue
            manager_entry = self._manager_entry_for_target(definition.target_path)
            orphaned = self._target_key(definition.target_path) in self._orphaned_targets
            source_type = (
                '' if manager_entry is None else str(manager_entry.get('source_type') or '')
            )
            source_value = (
                '' if manager_entry is None else str(manager_entry.get('source_value') or '')
            )
            rows_by_category[definition.category].append(
                {
                    'catalogKey': definition.key,
                    'category': definition.category,
                    'name': definition.name,
                    'targetPath': definition.target_path,
                    'fileFilter': definition.file_filter,
                    'muteAvailable': bool(definition.mute_source),
                    'supported': definition.supported,
                    'limitation': definition.limitation,
                    'configured': manager_entry is not None,
                    'entryId': '' if manager_entry is None else str(manager_entry.get('id', '')),
                    'sourceType': source_type,
                    'sourceValue': source_value,
                    'sourceName': self._source_name(source_type, source_value),
                    'status': (
                        'orphaned_stash'
                        if manager_entry is None and orphaned
                        else 'not_set'
                        if manager_entry is None
                        else str(manager_entry.get('status', 'not_set'))
                    ),
                    'errorMessage': ''
                    if manager_entry is None
                    else str(manager_entry.get('error_message') or ''),
                    'optional': optional,
                }
            )
        for category, model in self._built_in_models.items():
            model.replace_items(rows_by_category[category])
        self._available_head_variants_model.replace_items(
            {
                'catalogKey': key,
                'name': self._built_in_by_key[key].name,
            }
            for key in sorted(
                self._head_variant_keys - self._visible_head_variants,
                key=lambda value: self._built_in_by_key[value].name.casefold(),
            )
        )

    @staticmethod
    def _source_name(source_type: str, source_value: str) -> str:
        if not source_value:
            return ''
        if source_type == 'bundled':
            return 'Muted' if 'empty' in source_value else 'Bundled replacement'
        if source_type == 'asset_id':
            return f'Asset {source_value}'
        if source_type == 'cdn_url':
            return source_value
        return Path(source_value).name or source_value

    def _apply_built_in_source(
        self,
        definition: ModificationCatalogEntry,
        source_type: str,
        source_value: str,
    ) -> None:
        if self._manager is None:
            return
        manager_entry = self._manager_entry_for_target(definition.target_path)
        attributes: dict[str, Any] = {
            'display_name': definition.name,
            'source_type': source_type,
            'source_value': source_value,
        }
        if definition.is_font:
            attributes['_is_font'] = True
        if manager_entry is None:
            self._manager.add_entry(
                {
                    **attributes,
                    'target_path': definition.target_path,
                }
            )
        else:
            self._manager.update_entry(str(manager_entry.get('id', '')), **attributes)
        if definition.key in self._head_variant_keys:
            self._visible_head_variants.add(definition.key)
        self.refresh()

    @staticmethod
    def _normalise_preset_settings(values: dict[str, Any]) -> dict[str, str | bool | int | None]:
        settings = dict(_PRESET_DEFAULTS)
        mode = str(values.get('rendering_mode', 'Default'))
        settings['rendering_mode'] = mode if mode in _RENDERING_MODES else 'Default'
        msaa = str(values.get('msaa', 'Default'))
        settings['msaa'] = msaa if msaa in _MSAA_LEVELS else 'Default'
        texture = str(values.get('texture_quality', 'Default'))
        settings['texture_quality'] = texture if texture in _TEXTURE_LEVELS else 'Default'
        for key in (
            'disable_dpi_scale',
            'alt_enter_fullscreen',
            'mesh_lod_enabled',
            'frm_quality_enabled',
            'grey_sky',
            'pause_voxelizer',
        ):
            settings[key] = bool(values.get(key, _PRESET_DEFAULTS[key]))
        settings['mesh_lod'] = _bounded_int(values.get('mesh_lod'), default=4, maximum=4)
        settings['frm_quality'] = _bounded_int(values.get('frm_quality'), default=21, maximum=21)
        for key in ('grass_max', 'grass_min', 'grass_motion'):
            value = _bounded_int(values.get(key), default=0, maximum=100_000)
            settings[key] = value or None
        return settings

    def _set_preset_value(self, key: str, value: str | bool | int | None) -> None:
        if self._preset_settings[key] == value:
            return
        was_dirty = self.presetDirty
        self._preset_settings[key] = value
        self._preset_revision += 1
        if self._manager is not None:
            self._manager.fast_flags = dict(self._preset_settings)
        self.presetSettingsChanged.emit()
        if self.presetDirty != was_dirty:
            self.presetDirtyChanged.emit()

    def _custom_fast_flags(self) -> dict[str, str]:
        if self._config is None:
            return {}
        return dict(self._config.custom_fflags)

    def _save_custom_fast_flags(self, flags: dict[str, str]) -> None:
        if self._config is None:
            return
        self._config.custom_fflags = flags
        names = set(self._config.custom_fflags)
        disabled = set(getattr(self._config, 'custom_fflag_disabled', ())) & names
        self._config.custom_fflag_disabled = sorted(disabled)
        bindings = getattr(self._config, 'custom_fflag_keybinds', {})
        if isinstance(bindings, dict):
            self._config.custom_fflag_keybinds = {
                name: value for name, value in bindings.items() if name in names
            }
        self._hotkeys.sync()
        self._refresh_custom_fflag_interception()
        self._refresh_fast_flags()

    def _refresh_custom_fflag_interception(self) -> None:
        refresh = getattr(self._proxy_master, 'refresh_custom_fflag_interception', None)
        if not callable(refresh):
            return
        try:
            refresh()
        except Exception as exc:
            self.errorOccurred.emit(f'Could not refresh custom FastFlags: {exc}')

    def _migrate_misfiled_custom_flags(self) -> None:
        """Recover custom flags stored in the preset mapping by the first QML bridge."""
        if self._manager is None or self._config is None:
            return
        known_names = set(_PRESET_DEFAULTS)
        misplaced = {
            str(name): value
            for name, value in self._manager.fast_flags.items()
            if name not in known_names
        }
        normalized = normalize_custom_fflags(misplaced)
        if not normalized:
            return
        merged = dict(self._config.custom_fflags)
        for name, value in normalized.items():
            merged.setdefault(name, value)
        self._config.custom_fflags = merged
        self._manager.fast_flags = dict(self._preset_settings)

    @Slot(object)
    def _on_preset_task_succeeded(self, result: object) -> None:
        if not isinstance(result, tuple) or len(result) != 2:
            return
        operation, revision = result
        if not isinstance(operation, str) or not isinstance(revision, int):
            return
        was_dirty = self.presetDirty
        self._preset_applied_revision = revision
        if self.presetDirty != was_dirty:
            self.presetDirtyChanged.emit()
        self.allowlistedFastFlagsEnabledChanged.emit()
        if operation == 'reset':
            self.statusChanged.emit()
            self.notificationRequested.emit(
                'Allowlisted FastFlags reset',
                'Roblox ClientSettings and the framerate cap were restored.',
                'success',
            )
        else:
            self.notificationRequested.emit(
                'Allowlisted FastFlags applied',
                'The preset values were written to detected Roblox installations.',
                'success',
            )
        if self._preset_apply_pending:
            self._preset_apply_pending = False
            QTimer.singleShot(0, self.applyAllowlistedFastFlags)

    @Slot(str)
    def _on_preset_task_failed(self, message: str) -> None:
        self._preset_apply_pending = False
        self.errorOccurred.emit(f'Could not update allowlisted FastFlags: {message}')

    def _refresh_catalog_model(self) -> None:
        rows = []
        for name, value in sorted(
            self._catalog_values.items(), key=lambda item: item[0].casefold()
        ):
            search_text = f'{name} {value or ""}'
            if self._catalog_query and self._catalog_query not in search_text.casefold():
                continue
            rows.append(
                {
                    'name': name,
                    'value': value or '',
                    'family': FastFlagCatalog.family_for(name),
                    'published': value is not None,
                    'searchText': search_text,
                }
            )
        self._catalog_model.replace_items(rows)

    def _on_entry_status(self, entry_id: str, status: str, error: str) -> None:
        if self._disposed:
            return
        row = self._model.indexOf('entryId', entry_id)
        if row >= 0:
            self._model.update_item(row, {'status': status, 'errorMessage': error})
            self._refresh_built_in_models()
            self.statusChanged.emit()

    def _on_apply_finished(self, _entry_id: str) -> None:
        if self._disposed:
            return
        self.refresh()

    def _on_hotkey_capture_completed(self, name: str, label: str) -> None:
        self._refresh_fast_flags()
        self.hotkeyCaptureCompleted.emit(name, label)

    def _on_hotkey_toggled(self, _name: str) -> None:
        if not self._disposed:
            self._refresh_fast_flags()

    @staticmethod
    def _source_input(value: str) -> str:
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value

    @staticmethod
    def _local_path(value: str) -> Path:
        url = QUrl(value)
        return Path(url.toLocalFile()) if url.isLocalFile() else Path(value).expanduser()
