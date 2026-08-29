from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex, QObject, Qt, QUrl, Signal

from fleasion.qml_api import cache as cache_api_module
from fleasion.config import manager as manager_module
from fleasion.modifications.fflag_profiles import FastFlagProfileManager
from fleasion.qml_api.cache import CacheApi
from fleasion.qml_api.models import DictListModel, SelectionModel
from fleasion.qml_api.modifications import ModificationsApi
from fleasion.qml_api.proxy import ProxyApi
from fleasion.qml_api.replacer import ReplacerApi
from fleasion.qml_api.settings import SettingsApi

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def config_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    return manager_module.ConfigManager()


def test_dict_list_model_exposes_roles_and_emits_structural_changes():
    model = DictListModel(
        ('key', 'label'),
        ({'key': 'one', 'label': 'First'},),
    )
    count_changes: list[None] = []
    model.countChanged.connect(lambda: count_changes.append(None))

    role_by_name = {
        bytes(name.data()).decode('utf-8'): role for role, name in model.roleNames().items()
    }
    assert model.count == 1
    assert model.data(model.index(0), role_by_name['label']) == 'First'
    assert model.data(QModelIndex(), int(Qt.ItemDataRole.DisplayRole)) is None

    model.append_item({'key': 'two', 'label': 'Second'})
    assert model.update_item(1, {'label': 'Updated'})
    model.remove_rows([0])

    assert model.snapshot() == [{'key': 'two', 'label': 'Updated'}]
    assert count_changes == [None, None]


def test_selection_model_keeps_unique_stable_keys():
    selection = SelectionModel()
    changes: list[None] = []
    selection.selectionChanged.connect(lambda: changes.append(None))

    selection.setSelected('second', True)
    selection.setSelected('first', True)
    selection.setSelected('first', True)

    assert selection.values() == ['first', 'second']
    assert selection.contains('first')
    assert len(changes) == 2

    selection.clear()
    assert selection.values() == []
    assert len(changes) == 3


def test_replacer_bridge_parses_multiline_targets_and_supports_history(config_manager):
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    assert controller.addRule('Multiline IDs', '101\n202\t303;404,505 606', '707')
    assert config_manager.replacement_rules == [
        {
            'name': 'Multiline IDs',
            'replace_ids': [101, 202, 303, 404, 505, 606],
            'enabled': True,
            'mode': 'id',
            'with_id': 707,
        }
    ]
    assert controller.model.get(0)['targetCount'] == 6
    assert controller.canUndo

    controller.undo()
    assert config_manager.replacement_rules == []
    assert controller.canRedo

    controller.redo()
    assert controller.entry('0') == {
        'name': 'Multiline IDs',
        'targets': '101, 202, 303, 404, 505, 606',
        'replacement': '707',
        'action': 'Asset ID',
    }


def test_replacer_bridge_filters_and_toggles_nested_groups(config_manager):
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'Characters',
            'children': [
                {
                    'name': 'Face',
                    'replace_ids': [18],
                    'enabled': True,
                    'mode': 'remove',
                },
                {
                    'name': 'Walk',
                    'replace_ids': [55],
                    'enabled': False,
                    'mode': 'id',
                    'with_id': 100,
                },
            ],
        }
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    assert controller.model.count == 3
    assert controller.model.get(0)['state'] == 'mixed'

    controller.query = 'walk'
    assert controller.model.count == 1
    assert controller.model.get(0)['path'] == '0/1'

    controller.query = ''
    assert controller.setEntryEnabled('0', True)
    assert [child['enabled'] for child in config_manager.replacement_rules[0]['children']] == [
        True,
        True,
    ]


def test_cached_asset_draft_is_consumed_once(config_manager):
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    controller.prepareCachedAsset('123', True)
    assert controller.hasDraft
    assert controller.takeDraft() == {
        'name': 'Cached asset 123',
        'targets': '',
        'replacement': '123',
    }
    assert not controller.hasDraft
    assert controller.takeDraft() == {}

    controller.prepareCachedAsset('456', False)
    assert controller.takeDraft() == {
        'name': 'Cached asset 456',
        'targets': '456',
        'replacement': '',
    }


class _CacheStub:
    def __init__(self) -> None:
        self.index: dict[str, Any] = {'assets': {'1_123': {}}}
        self.deleted: list[tuple[str, int]] = []

    def list_assets(self) -> list[dict[str, Any]]:
        return [
            {
                'id': '123',
                'type': 1,
                'type_name': 'Image',
                'resolved_name': 'Sunset',
                'creator_name': 'Builder',
                'hash': 'abc',
                'raw_size': 2048,
                'cached_at': '2026-08-12T10:30:00',
                'url': 'https://example.invalid/123',
            }
        ]

    def get_cache_stats(self) -> dict[str, int]:
        return {'total_assets': 1, 'total_size': 2048}

    def delete_assets_batch(self, assets: list[tuple[str, int]]) -> tuple[int, int]:
        self.deleted.extend(assets)
        return len(assets), 0

    def get_available_export_formats_for_asset(
        self,
        _asset_id: str,
        _asset_type: int,
    ) -> list[str]:
        return ['raw', 'png']

    def get_asset(self, _asset_id: str, _asset_type: int) -> bytes:
        return b'OggS\x00preview'


def test_cache_bridge_maps_filters_and_deletes_assets():
    cache = _CacheStub()
    controller = CacheApi(cache)  # pyright: ignore[reportCallIssue]
    notifications: list[tuple[str, str, str]] = []
    controller.notificationRequested.connect(lambda *values: notifications.append(values))

    assert controller.totalAssets == 1
    assert controller.totalSizeText == '2.0 KB'
    assert controller.model.get(0)['previewUrl'].startswith('image://fleasion-cache/1/123')
    assert controller.exportFormats('1_123') == ['raw', 'png']

    controller.query = 'missing'
    assert controller.model.count == 0
    controller.query = 'builder'
    assert controller.model.count == 1

    assert controller.deleteAssets(['1_123'])
    assert cache.deleted == [('123', 1)]
    assert notifications[-1] == ('Cache updated', '1 cached assets deleted', 'success')


def test_audio_preview_materializes_decompressed_bytes_with_a_media_suffix():
    controller = CacheApi(_CacheStub())  # pyright: ignore[reportCallIssue]
    controller._model.update_item(0, {'typeName': 'Audio'})

    controller.loadPreview('1_123')
    path = Path(QUrl(controller.previewSource).toLocalFile())
    try:
        assert controller.previewKind == 'audio'
        assert path.suffix == '.ogg'
        assert path.read_bytes() == b'OggS\x00preview'
    finally:
        controller.shutdown()
    assert not path.exists()


class _CacheParityStub(_CacheStub):
    def __init__(self, root: Path) -> None:
        super().__init__()
        self.cache_dir = root / 'cache'
        self.export_dir = root / 'exports'
        self.config_manager = SimpleNamespace(scraper_blacklist=['123'])
        self.memory_clears = 0
        self.exports: list[tuple[str, int, str]] = []
        self._assets = [
            {
                'id': '123',
                'type': 1,
                'type_name': 'Image',
                'resolved_name': 'Sunset',
                'creator_name': 'Builder',
                'hash': 'abc',
                'size': 1024,
                'cached_at': '2026-08-12T10:30:00',
                'url': 'https://assetdelivery.roblox.com/v1/asset/?id=123',
            },
            {
                'id': '456',
                'type': 40,
                'type_name': 'MeshPart',
                'resolved_name': 'Bridge',
                'creator_name': 'Builder Group',
                'resolved_creator_id': 99,
                'resolved_creator_type': 2,
                'resolved_creator_name': 'Builder Group',
                'hash': 'def',
                'size': 2048,
                'cached_at': '2026-08-12T10:31:00',
                'url': 'https://assetdelivery.roblox.com/v1/asset/?id=456',
            },
        ]
        self._sync_index()

    def _sync_index(self) -> None:
        self.index = {'assets': {f'{asset["type"]}_{asset["id"]}': asset for asset in self._assets}}

    def list_assets(self) -> list[dict[str, Any]]:
        return [dict(asset) for asset in self._assets]

    def get_cache_stats(self) -> dict[str, int]:
        return {
            'total_assets': len(self._assets),
            'total_size': sum(int(asset['size']) for asset in self._assets),
        }

    def clear_memory_cache(self) -> int:
        self.memory_clears += 1
        return 0

    def clear_cache(self) -> int:
        count = len(self._assets)
        self._assets.clear()
        self._sync_index()
        return count

    def export_asset(
        self,
        asset_id: str,
        asset_type: int,
        *,
        resolved_name: str,
        export_format: str,
    ) -> Path:
        self.exports.append((asset_id, asset_type, export_format))
        return self.export_dir / f'{resolved_name or asset_id}.bin'


class _ScraperResetStub:
    def __init__(self) -> None:
        self.reset_count = 0

    def reset_for_cache_clear(self) -> None:
        self.reset_count += 1


class _ManualFetchScraperStub:
    def __init__(self) -> None:
        self.requested: list[str] = []

    def _fetch_from_api(self, asset_id: str) -> bytes:
        self.requested.append(asset_id)
        return b'manual asset payload'


class _ManualFetchCacheStub(_CacheParityStub):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self._assets.clear()
        self._sync_index()

    def store_asset(
        self,
        asset_id: str,
        asset_type: int,
        data: bytes,
        *,
        url: str,
        metadata: dict[str, Any],
    ) -> bool:
        self._assets.append(
            {
                'id': asset_id,
                'type': asset_type,
                'type_name': 'Image',
                'resolved_name': metadata.get('name', ''),
                'hash': 'manual',
                'size': len(data),
                'cached_at': '2026-08-12T11:00:00',
                'url': url,
                'metadata': metadata,
            }
        )
        self._sync_index()
        return True


def test_cache_bridge_persists_blacklist_and_exposes_copyable_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache = _CacheParityStub(tmp_path)
    controller = CacheApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    opened: list[Path] = []
    monkeypatch.setattr(cache_api_module, 'open_folder', opened.append)
    try:
        assert controller.blacklistText == '123'
        assert controller.blacklistCount == 1
        assert controller.model.count == 1
        assert controller.assetTypes == ['MeshPart']
        assert controller.model.get(0)['creatorUrl'].endswith('/communities/99')
        assert controller.model.get(0)['sourceUrl'].endswith('id=456')

        assert controller.applyBlacklist('456; invalid; 000456') == 1
        assert cache.config_manager.scraper_blacklist == ['456']
        assert controller.model.get(0)['assetId'] == '123'

        controller.clearBlacklist()
        assert controller.model.count == 2
        assert cache.config_manager.scraper_blacklist == []

        controller.openCacheFolder()
        controller.openExportsFolder()
        assert opened == [cache.cache_dir, cache.export_dir]
    finally:
        controller.shutdown()


def test_cache_bridge_clears_assets_asynchronously(tmp_path: Path):
    cache = _CacheParityStub(tmp_path)
    cache.config_manager.scraper_blacklist = []
    scraper = _ScraperResetStub()
    controller = CacheApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        cache,
        scraper,
    )
    notifications: list[tuple[str, str, str]] = []
    controller.notificationRequested.connect(lambda *values: notifications.append(values))
    try:
        assert controller.clearCache()
        deadline = time.monotonic() + 2
        while controller.task.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        QCoreApplication.processEvents()

        assert not controller.task.busy
        assert controller.model.count == 0
        assert cache.memory_clears == 1
        assert scraper.reset_count == 1
        assert notifications[-1] == ('Cache cleared', '2 cached assets removed', 'success')
    finally:
        controller.shutdown()


def test_cache_bridge_bulk_exports_and_writes_compatible_game_dump(tmp_path: Path):
    cache = _CacheParityStub(tmp_path)
    cache.config_manager.scraper_blacklist = []
    controller = CacheApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        keys = ['7_missing', '1_123', '40_456']
        assert controller.commonExportFormats(keys) == ['raw', 'png']
        assert controller.exportAssets(keys, 'raw')
        deadline = time.monotonic() + 2
        while controller.task.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        QCoreApplication.processEvents()
        assert cache.exports == [('123', 1, 'raw'), ('456', 40, 'raw')]

        destination = tmp_path / 'game_dump.json'
        assert controller.exportGameDump(keys, str(destination))
        assert json.loads(destination.read_text(encoding='utf-8')) == {
            'Image': {'Sunset': 123},
            'MeshPart': {'Bridge': 456},
        }
    finally:
        controller.shutdown()


def test_cache_bridge_manually_loads_deduplicated_asset_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    cache = _ManualFetchCacheStub(tmp_path)
    cache.config_manager.scraper_blacklist = []
    scraper = _ManualFetchScraperStub()
    monkeypatch.setattr(
        CacheApi,
        '_fetch_manual_metadata',
        staticmethod(
            lambda ids, _cancel_event=None: {
                asset_id: {'name': f'Asset {asset_id}', 'type': 1} for asset_id in ids
            }
        ),
    )
    controller = CacheApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        cache,
        scraper,
    )
    try:
        assert controller.loadAssets('002; 1, invalid, 2')
        deadline = time.monotonic() + 2
        while controller.task.busy and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.005)
        QCoreApplication.processEvents()

        assert scraper.requested == ['1', '2']
        assert [row['assetId'] for row in controller.model.snapshot()] == ['1', '2']
    finally:
        controller.shutdown()


class _ProxyStub:
    def __init__(self) -> None:
        self.is_running = False
        self.traffic = [
            {
                'id': 7,
                'time': 1_660_000_000,
                'method': 'GET',
                'host': 'assetdelivery.roblox.com',
                'path': '/v1/asset?id=123',
                'stage': 'response',
                'status': 200,
                'size': 1536,
                'ms': 18,
            }
        ]
        self.actions: list[str] = []

    def get_env_proxy_traffic(self) -> list[dict[str, Any]]:
        return self.traffic

    def clear_env_proxy_traffic(self) -> None:
        self.traffic.clear()

    def start(self) -> None:
        self.actions.append('start')
        self.is_running = True

    def stop(self) -> None:
        self.actions.append('stop')
        self.is_running = False


def test_proxy_bridge_maps_traffic_and_controls_lifecycle():
    proxy = _ProxyStub()
    controller = ProxyApi(proxy)  # pyright: ignore[reportCallIssue]

    assert controller.model.get(0)['sizeText'] == '1.5 KB'
    assert controller.trafficEntry('7')['host'] == 'assetdelivery.roblox.com'

    controller.query = 'not-present'
    assert controller.model.count == 0
    controller.query = 'assetdelivery'
    assert controller.model.count == 1

    controller.start()
    while controller.lifecycleTask.property('busy'):
        QCoreApplication.processEvents()
    assert controller.running
    controller.restart()
    controller.stop()
    while controller.lifecycleTask.property('busy') or controller.lifecycleAction:
        QCoreApplication.processEvents()
    QCoreApplication.processEvents()
    assert proxy.actions == ['start', 'stop', 'start', 'stop']

    controller.clear()
    assert controller.model.count == 0
    controller.shutdown()


class _ModificationStub(QObject):
    entry_status_changed = Signal(str, str, str)
    apply_finished = Signal(str)
    restore_finished = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[dict[str, Any]] = []
        self.fast_flags: dict[str, Any] = {'rendering_mode': 'D3D11'}
        self.fast_flags_enabled = False
        self.framerate_cap = 0
        self.written_fast_flags: list[dict[str, Any]] = []
        self.reset_framerate_calls = 0
        self.updated_entries: list[tuple[str, dict[str, Any]]] = []

    def write_fast_flags(self, settings: dict[str, Any]) -> None:
        self.fast_flags = dict(settings)
        self.fast_flags_enabled = True
        self.written_fast_flags.append(dict(settings))

    def reset_framerate_cap(self) -> None:
        self.reset_framerate_calls += 1

    def add_entry(self, entry: dict[str, Any]) -> str:
        stored = {'id': str(len(self.entries) + 1), 'status': 'pending', **entry}
        self.entries.append(stored)
        return str(stored['id'])

    def update_entry(self, entry_id: str, **values: Any) -> bool:
        entry = next((item for item in self.entries if item['id'] == entry_id), None)
        if entry is None:
            return False
        entry.update(values)
        self.updated_entries.append((entry_id, values))
        return True

    def clear_entry(self, entry_id: str) -> bool:
        self.entries = [item for item in self.entries if item['id'] != entry_id]
        return True

    def reapply_all(self) -> None:
        return None

    def restore_all(self) -> None:
        return None

    def sync_saved_global_settings(self) -> None:
        return None


class _CustomFastFlagConfigStub:
    def __init__(self) -> None:
        self.custom_fflags: dict[str, str] = {'FFlagExisting': 'True'}
        self.custom_fflags_enabled = False
        self.custom_fflags_warning_accepted = False
        self.custom_fflag_disabled: list[str] = []
        self.custom_fflag_keybinds: dict[str, dict[str, Any]] = {}


class _CustomFastFlagProxyStub:
    def __init__(self) -> None:
        self.refresh_count = 0

    def refresh_custom_fflag_interception(self) -> None:
        self.refresh_count += 1


def test_modifications_bridge_imports_exports_and_manages_profiles(tmp_path: Path):
    manager = _ModificationStub()
    config = _CustomFastFlagConfigStub()
    proxy = _CustomFastFlagProxyStub()
    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager,
        profile_manager=FastFlagProfileManager(tmp_path / 'profiles'),
        config_manager=config,  # pyright: ignore[reportArgumentType]
        proxy_master=proxy,
    )

    assert not controller.customFastFlagsWarningAccepted
    controller.fastFlagsEnabled = True
    assert controller.fastFlagsEnabled
    assert controller.customFastFlagsWarningAccepted
    controller.fastFlagsEnabled = False
    assert not controller.fastFlagsEnabled
    assert controller.customFastFlagsWarningAccepted

    assert controller.importFastFlagsJson('{"DFIntTarget": 144}', False)
    assert config.custom_fflags == {'FFlagExisting': 'True', 'DFIntTarget': '144'}
    assert manager.fast_flags['rendering_mode'] == 'D3D11'
    assert controller.saveProfile('Performance')
    assert controller.profilesModel.get(0)['name'] == 'Performance'

    config.custom_fflags = {'FFlagOther': 'False'}
    assert controller.loadProfile('Performance', True)
    assert config.custom_fflags == {'DFIntTarget': '144', 'FFlagExisting': 'True'}

    destination = tmp_path / 'ClientAppSettings'
    assert controller.exportFastFlags(str(destination))
    assert (
        json.loads(destination.with_suffix('.json').read_text(encoding='utf-8'))
        == config.custom_fflags
    )
    assert controller.renameProfile('Performance', 'Smooth')
    assert controller.deleteProfile('Smooth')
    assert controller.profilesModel.count == 0
    assert proxy.refresh_count >= 2
    controller.shutdown()


def test_modifications_bridge_applies_and_resets_allowlisted_presets_without_custom_flags():
    manager = _ModificationStub()
    config = _CustomFastFlagConfigStub()
    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager,
        config_manager=config,  # pyright: ignore[reportArgumentType]
    )

    controller.presetRenderingMode = 'Vulkan'
    controller.presetMsaa = '4'
    controller.presetMeshLodEnabled = True
    controller.presetMeshLod = 2
    controller.presetGreySky = True

    assert controller.presetDirty
    assert config.custom_fflags == {'FFlagExisting': 'True'}
    assert controller.applyAllowlistedFastFlags()
    while controller.presetTask.property('busy'):
        QCoreApplication.processEvents()
    QCoreApplication.processEvents()

    assert manager.written_fast_flags[-1]['rendering_mode'] == 'Vulkan'
    assert manager.written_fast_flags[-1]['mesh_lod'] == 2
    assert manager.fast_flags_enabled
    assert not controller.presetDirty
    assert config.custom_fflags == {'FFlagExisting': 'True'}

    assert controller.resetAllowlistedFastFlags()
    while controller.presetTask.property('busy'):
        QCoreApplication.processEvents()
    QCoreApplication.processEvents()

    assert manager.fast_flags['rendering_mode'] == 'Default'
    assert not manager.fast_flags_enabled
    assert manager.reset_framerate_calls == 1
    assert config.custom_fflags == {'FFlagExisting': 'True'}
    controller.shutdown()


def test_modifications_bridge_recovers_custom_flags_misfiled_by_early_qml_bridge():
    manager = _ModificationStub()
    manager.fast_flags = {
        'rendering_mode': 'OpenGL',
        'FFlagRecovered': 'True',
        'DFIntRecovered': '144',
    }
    config = _CustomFastFlagConfigStub()
    config.custom_fflags = {'FFlagExisting': 'False'}

    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager,
        config_manager=config,  # pyright: ignore[reportArgumentType]
    )

    assert config.custom_fflags == {
        'FFlagExisting': 'False',
        'FFlagRecovered': 'True',
        'DFIntRecovered': '144',
    }
    assert manager.fast_flags['rendering_mode'] == 'OpenGL'
    assert 'FFlagRecovered' not in manager.fast_flags
    controller.shutdown()


def test_modifications_bridge_persists_custom_local_sources_as_local_file(tmp_path: Path):
    manager = _ModificationStub()
    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager
    )
    source = tmp_path / 'cursor.png'
    source.write_bytes(b'image')

    assert controller.addModification('Cursor', 'content/textures/cursor.png', str(source))
    assert manager.entries[0]['source_type'] == 'local_file'
    assert manager.entries[0]['source_value'] == str(source)

    replacement = tmp_path / 'cursor-2.png'
    replacement.write_bytes(b'image 2')
    assert controller.replaceSource('1', str(replacement))
    assert manager.entries[0]['source_type'] == 'local_file'
    assert manager.entries[0]['source_value'] == str(replacement)
    controller.shutdown()


def test_modifications_bridge_exposes_builtins_bulk_sky_mute_and_font(tmp_path: Path):
    manager = _ModificationStub()
    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager
    )
    sky = tmp_path / 'sky.png'
    sky.write_bytes(b'image')
    font = tmp_path / 'font.ttf'
    font.write_bytes(b'\x00\x01\x00\x00font')

    assert controller.skyboxModel.count == 6
    assert controller.applySkyToAll(str(sky))
    assert sum(entry['target_path'].endswith('.tex') for entry in manager.entries) == 6
    assert all(entry['source_type'] == 'local_file' for entry in manager.entries)

    assert controller.muteBuiltIn('sound-oof')
    muted = next(entry for entry in manager.entries if entry['display_name'] == 'Oof')
    assert muted['source_type'] == 'bundled'
    assert muted['source_value'] == 'bundled:empty.ogg'

    assert controller.applyBuiltIn('custom-font', str(font))
    custom_font = next(entry for entry in manager.entries if entry['display_name'] == 'Custom Font')
    assert custom_font['_is_font'] is True
    controller.shutdown()


def test_modifications_shutdown_disconnects_late_manager_signals():
    manager = _ModificationStub()
    controller = ModificationsApi(  # pyright: ignore[reportArgumentType, reportCallIssue]
        manager
    )
    model_changes: list[None] = []
    controller.modelChanged.connect(lambda: model_changes.append(None))

    controller.shutdown()
    manager.apply_finished.emit('late-worker')
    manager.restore_finished.emit()
    manager.entry_status_changed.emit('late-worker', 'error', 'late')
    QCoreApplication.processEvents()

    assert not model_changes


def test_settings_bridge_updates_typed_values_and_signals(config_manager):
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    changed: list[str] = []
    restarts: list[str] = []
    proxy_transitions: list[tuple[str, str]] = []
    controller.changed.connect(changed.append)
    controller.restartRequired.connect(restarts.append)
    controller.proxyModeTransitionRequested.connect(
        lambda previous, current: proxy_transitions.append((previous, current))
    )

    controller.theme = 'Dark'
    controller.accentColor = '#0067c0'
    controller.highContrast = True
    controller.reducedMotion = True
    controller.proxyMode = 'hosts'
    controller.proxyFeaturesEnabled = False
    controller.closeViewerOnReplace = False
    controller.setBool('wire_preserving_passthrough', True)
    controller.setExportNamingEnabled('hash', True)
    controller.setExportNamingEnabled('name', False)

    assert controller.theme == 'Dark'
    assert controller.accentColor == '#0067c0'
    assert controller.highContrast
    assert controller.reducedMotion
    assert controller.proxyMode == 'hosts'
    assert not controller.proxyFeaturesEnabled
    assert not controller.closeViewerOnReplace
    assert controller.value('wire_preserving_passthrough') is True
    assert not controller.exportNameEnabled
    assert controller.exportIdEnabled
    assert controller.exportHashEnabled
    assert changed == [
        'theme',
        'accent_color',
        'high_contrast',
        'reduced_motion',
        'proxy_mode',
        'proxy_features_enabled',
        'close_viewer_on_replace',
        'wire_preserving_passthrough',
        'export_naming',
        'export_naming',
    ]
    assert restarts == []
    assert proxy_transitions == [('env', 'hosts')]
    controller.shutdown()


def test_settings_bridge_validates_and_persists_advanced_upstream(config_manager):
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    errors: list[str] = []
    restarts: list[None] = []
    controller.errorOccurred.connect(errors.append)
    controller.proxyRestartRequested.connect(lambda: restarts.append(None))
    try:
        assert not controller.configureUpstream(
            'http_connect', '', 0, '', '', '', 0, '', '', 16, 32
        )
        assert errors[-1] == 'HTTP CONNECT requires a host and port.'

        assert controller.configureUpstream(
            'http_connect',
            'proxy.example',
            8443,
            'alice',
            'secret',
            'socks.example',
            1080,
            'bob',
            'second-secret',
            8,
            24,
        )
        assert config_manager.upstream_transport_mode == 'http_connect'
        assert controller.httpProxyHost == 'proxy.example'
        assert controller.httpProxyPasswordStored
        assert controller.assetConnectionLimit == 8
        assert restarts == [None]

        controller.clearUpstreamPassword('http')
        assert not controller.httpProxyPasswordStored
        assert restarts == [None, None]
    finally:
        controller.shutdown()


def test_settings_first_run_language_applies_immediately_without_restart(config_manager):
    from fleasion import localization

    config_manager.first_time_setup_complete = False
    previous_language = localization.get_language()
    localization.set_language('en')
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    restarts: list[str] = []
    controller.restartRequired.connect(restarts.append)
    try:
        controller.language = 'es'

        assert config_manager.language == 'es'
        assert localization.get_language() == 'es'
        assert controller.firstRunGuide == localization.tr('onboarding.welcome.body')
        assert restarts == []
    finally:
        localization.set_language(previous_language)
        controller.shutdown()


def test_settings_existing_install_language_waits_for_restart(config_manager):
    from fleasion import localization

    config_manager.first_time_setup_complete = True
    previous_language = localization.get_language()
    localization.set_language('en')
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    restarts: list[str] = []
    controller.restartRequired.connect(restarts.append)
    try:
        controller.language = 'de'

        assert config_manager.language == 'de'
        assert localization.get_language() == 'en'
        assert restarts == [localization.tr('settings.language.restart_required_body')]
    finally:
        localization.set_language(previous_language)
        controller.shutdown()


def test_macos_browser_auth_source_is_validated_before_persisting(
    config_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.qml_api import settings as settings_api_module
    from fleasion.utils import roblox_auth

    monkeypatch.setattr(settings_api_module.sys, 'platform', 'darwin')
    calls: list[tuple[bool, bool, str | None]] = []

    def discover(
        include_keychain: bool = False,
        *,
        explicit_import: bool = False,
        browser: str | None = None,
    ) -> tuple[str | None, str]:
        calls.append((include_keychain, explicit_import, browser))
        return 'cookie-value', browser or ''

    monkeypatch.setattr(roblox_auth, 'discover_browser_roblosecurity', discover)
    monkeypatch.setattr(roblox_auth, 'notify_auth_source_changed', lambda: None)
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    try:
        assert controller.selectMacosAuthSource('Chrome')
        deadline = time.monotonic() + 2.0
        while controller.authTask.property('busy') and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        QCoreApplication.processEvents()

        assert calls == [(True, True, 'Chrome')]
        assert config_manager.macos_auth_source == 'Chrome'
        assert controller.authStatus == 'Chrome'
    finally:
        controller.shutdown()


def test_macos_browser_auth_source_does_not_persist_missing_login(
    config_manager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.qml_api import settings as settings_api_module
    from fleasion.utils import roblox_auth

    monkeypatch.setattr(settings_api_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        roblox_auth,
        'discover_browser_roblosecurity',
        lambda **_kwargs: (None, ''),
    )
    controller = SettingsApi(config_manager)  # pyright: ignore[reportCallIssue]
    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    try:
        assert controller.selectMacosAuthSource('Firefox')
        deadline = time.monotonic() + 2.0
        while controller.authTask.property('busy') and time.monotonic() < deadline:
            QCoreApplication.processEvents()
            time.sleep(0.01)
        QCoreApplication.processEvents()

        assert config_manager.macos_auth_source == ''
        assert errors
        assert 'Firefox' in errors[-1]
    finally:
        controller.shutdown()
