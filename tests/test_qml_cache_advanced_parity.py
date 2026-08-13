from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QCoreApplication

from fleasion.qml_api import cache as cache_module
from fleasion.qml_api.cache import CacheApi
from fleasion.qml_api.texture_pack_preview import TexturePackPreviewApi

TEXTURE_PACK_XML = b'''<TexturePack>
<Color>101</Color>
<NormalMap>202</NormalMap>
<Roughness>303</Roughness>
</TexturePack>'''


def _wait_for_task(task: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while task.busy and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert not task.busy


class _SettingsStub:
    def __init__(self) -> None:
        self.settings: dict[str, Any] = {
            'scraper_column_visibility': {
                'hash_name': True,
                'type': False,
                'size': True,
                'cached_at': True,
            },
            'scraper_search_columns': ['creator'],
            'qml_cache_sort_key': 'name',
            'qml_cache_sort_descending': False,
        }
        self.scraper_blacklist: list[str] = []
        self.save_count = 0

    def save(self) -> None:
        self.save_count += 1


class _AdvancedCacheStub:
    def __init__(self, root: Path) -> None:
        self.cache_dir = root / 'cache'
        self.export_dir = root / 'exports'
        self.config_manager = _SettingsStub()
        self.assets = [
            {
                'id': '20',
                'type': 1,
                'type_name': 'Image',
                'resolved_name': 'Sunset',
                'creator_name': 'Zed',
                'hash': 'bbb',
                'size': 20,
                'cached_at': '2026-01-02T00:00:00',
                'url': 'https://assetdelivery.roblox.com/v1/asset/?id=20',
            },
            {
                'id': '3',
                'type': 1,
                'type_name': 'Image',
                'resolved_name': 'Arch',
                'creator_name': 'Ada',
                'hash': 'aaa',
                'size': 30,
                'cached_at': '2026-01-01T00:00:00',
                'url': 'https://assetdelivery.roblox.com/v1/asset/?id=3',
            },
        ]
        self.index = {'assets': {f'1_{asset["id"]}': asset for asset in self.assets}}
        self.export_calls: list[tuple[str, str]] = []

    def list_assets(self) -> list[dict[str, Any]]:
        return [dict(asset) for asset in self.assets]

    def get_cache_stats(self) -> dict[str, int]:
        return {'total_assets': len(self.assets), 'total_size': 50}

    def get_available_export_formats_for_asset(
        self,
        _asset_id: str,
        _asset_type: int,
    ) -> list[str]:
        return ['converted_png', 'raw']

    def export_asset(
        self,
        asset_id: str,
        _asset_type: int,
        *,
        resolved_name: str,
        export_format: str,
    ) -> Path:
        self.export_calls.append((asset_id, export_format))
        self.export_dir.mkdir(parents=True, exist_ok=True)
        destination = self.export_dir / f'{resolved_name}.png'
        destination.write_bytes(b'png')
        return destination

    def _flush_index(self) -> None:
        return


class _TextureCacheStub:
    def __init__(self, root: Path) -> None:
        self.export_dir = root / 'exports'
        self.info = {
            '101': {'hash': 'color-hash', 'size': 1024},
            '303': {'hash': 'rough-hash', 'size': 2048},
        }
        self.payloads = {
            '101': b'not-decoded-in-this-test',
            '303': b'not-decoded-in-this-test',
        }

    def get_asset_info(self, asset_id: str, _asset_type: int) -> dict[str, Any] | None:
        return self.info.get(asset_id)

    def get_asset(self, asset_id: str, _asset_type: int) -> bytes | None:
        return self.payloads.get(asset_id)


def test_texture_pack_preview_exposes_fixed_slots_and_captured_exports(tmp_path: Path) -> None:
    cache = _TextureCacheStub(tmp_path)
    slots = tmp_path / 'slots'
    slots.mkdir()
    (slots / '900_slot0.ktx2').write_bytes(b'color')
    (slots / '900_slot2.ktx2').write_bytes(b'orm')

    preview = TexturePackPreviewApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    preview.set_slot_directory(slots)
    preview.set_export_directory(cache.export_dir)

    assert preview.load_bytes(TEXTURE_PACK_XML, '900')
    assert preview.loaded
    assert preview.model.count == 3
    assert preview.model.get(0) == {
        'name': 'Color',
        'slotIndex': 0,
        'slotLabel': 'Slot 0',
        'slotKey': '900:0',
        'assetId': '101',
        'hash': 'color-hash',
        'sizeText': '1.0 KB',
        'imageSource': 'image://fleasion-cache/1/101?v=color-hash',
        'cached': True,
        'captured': True,
        'capturedSizeText': '5 B',
    }
    assert preview.model.get(1)['slotKey'] == '900:1'
    assert not preview.model.get(1)['cached']
    assert preview.model.get(2)['slotKey'] == '900:3'
    assert preview.capturedCount == 2

    requested: list[str] = []
    preview.loadRequested.connect(requested.append)
    preview.requestMap(1)
    assert requested == ['202']

    assert preview.exportCapturedSlot(0)
    destination = cache.export_dir / 'converted' / 'TexturePack' / '900_slots'
    assert (destination / '900_slot0_Color.ktx2').read_bytes() == b'color'
    assert preview.exportAllCapturedSlots() == 2
    assert (destination / '900_slot2_ORM.ktx2').read_bytes() == b'orm'


def test_cache_view_search_sort_and_columns_persist(tmp_path: Path) -> None:
    cache = _AdvancedCacheStub(tmp_path)
    controller = CacheApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        assert [controller.model.get(row)['name'] for row in range(2)] == ['Arch', 'Sunset']
        assert 'type' not in controller.visibleColumns
        assert controller.searchColumns == ['creator']

        controller.query = 'sunset'
        assert controller.model.count == 0
        controller.query = 'zed'
        assert controller.model.get(0)['name'] == 'Sunset'

        controller.setSearchColumnEnabled('name', True)
        controller.query = 'sunset'
        assert controller.model.get(0)['name'] == 'Sunset'
        controller.setColumnVisible('type', True)
        controller.setSortKey('assetId')
        controller.sortDescending = True
        controller._persist_view_settings()

        assert cache.config_manager.save_count == 1
        assert cache.config_manager.settings['qml_cache_query'] == 'sunset'
        assert cache.config_manager.settings['qml_cache_sort_key'] == 'assetId'
        assert cache.config_manager.settings['scraper_search_columns'] == ['creator', 'name']
        assert cache.config_manager.settings['scraper_column_visibility']['type']
    finally:
        controller.shutdown()

    restored = CacheApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    try:
        assert restored.query == 'sunset'
        assert restored.sortKey == 'assetId'
        assert restored.sortDescending
        assert 'type' in restored.visibleColumns
        assert restored.searchColumns == ['creator', 'name']
    finally:
        restored.shutdown()


def test_cache_copy_converted_files_uses_export_pipeline_and_file_clipboard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    cache = _AdvancedCacheStub(tmp_path)
    copied: list[list[Path]] = []
    monkeypatch.setattr(cache_module, '_copy_file_urls', lambda paths: copied.append(paths) or True)
    controller = CacheApi(cache)  # pyright: ignore[reportArgumentType, reportCallIssue]
    notifications: list[tuple[str, str, str]] = []
    controller.notificationRequested.connect(lambda *values: notifications.append(values))
    try:
        assert controller.copyConvertedAssets(['1_20', '1_3'])
        _wait_for_task(controller.task)
        assert cache.export_calls == [('20', 'converted_png'), ('3', 'converted_png')]
        assert copied == [
            [cache.export_dir / 'Sunset.png', cache.export_dir / 'Arch.png']
        ]
        assert notifications[-1][0] == 'Converted files copied'
    finally:
        controller.shutdown()
