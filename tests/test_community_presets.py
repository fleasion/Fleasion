from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication, QModelIndex, Qt

from fleasion.config import manager as manager_module
from fleasion.prejsons import (
    CommunityPresetCatalog,
    CommunityPreset,
    CustomPresetRequest,
    PresetValue,
    RobloxPresetMetadataClient,
    flatten_preset_values,
    normalize_catalog,
)
from fleasion.prejsons import catalog as preset_catalog
from fleasion.qml_api.replacer import ReplacerApi
from fleasion.qml_api.community_presets import CommunityPresetsApi
from fleasion.qml_api.preset_tree import PresetJsonTreeModel


def test_remote_preset_sources_use_the_32_mib_streaming_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, int, int]] = []

    def fake_http_get(
        url: str,
        timeout: int = 15,
        _headers: dict[str, str] | None = None,
        *,
        max_bytes: int,
    ) -> bytes:
        calls.append((url, timeout, max_bytes))
        return b'{}'

    monkeypatch.setattr(preset_catalog, 'http_get', fake_http_get)

    assert preset_catalog.read_source_bytes('https://presets.example/data.json') == b'{}'
    assert calls == [
        (
            'https://presets.example/data.json',
            15,
            preset_catalog.MAX_JSON_BYTES,
        )
    ]


def test_catalog_normalizes_legacy_clog_keys_and_shapes() -> None:
    presets = normalize_catalog(
        {
            'games': {
                'Phantom Forces': {
                    'Owner': 'T4T',
                    'id': 292439477,
                    'github': 'https://example.invalid/originals.json',
                    'Replacement': 'https://example.invalid/replacements.json',
                },
                'Metadata name': {'placeId': '123'},
                'Invalid place': {'placeId': float('inf')},
            }
        }
    )

    assert [preset.name for preset in presets] == [
        'Phantom Forces',
        'Metadata name',
        'Invalid place',
    ]
    assert presets[0].credit == 'T4T'
    assert presets[0].place_id == 292439477
    assert presets[0].replacements_source.endswith('replacements.json')
    assert presets[1].place_id == 123
    assert presets[2].place_id is None
    assert presets[0].preset_id != presets[1].preset_id


def test_preset_value_flattening_keeps_ids_urls_and_paths() -> None:
    values = flatten_preset_values(
        {
            'Audio': {'Hit': '123', 'Ignored label': 'hello'},
            'Texture': ['https://assets.invalid/texture.png', '/tmp/local.png'],
            'Enabled': True,
            'Fraction': 4.5,
        }
    )

    assert [(value.value, value.kind) for value in values] == [
        (123, 'id'),
        ('https://assets.invalid/texture.png', 'url'),
        ('/tmp/local.png', 'path'),
    ]
    assert values[0].path == 'Audio › Hit'


def test_preset_tree_preserves_json_hierarchy_and_filters_with_ancestors() -> None:
    document = {
        'Audio': {'Hit': '123', 'Description': 'A loud impact'},
        'Texture': ['https://assets.invalid/game.png', True],
    }
    values = tuple(flatten_preset_values(document))
    model = PresetJsonTreeModel()
    model.set_document(document, values)
    roles = {bytes(name.data()).decode('utf-8'): role for role, name in model.roleNames().items()}

    assert model.rowCount() == 2
    assert model.count == 6
    audio = model.index(0, 0)
    hit = model.index(0, 0, audio)
    description = model.index(1, 0, audio)
    assert model.data(audio, int(Qt.ItemDataRole.DisplayRole)) == 'Audio'
    assert model.rowCount(audio) == 2
    assert model.parent(hit) == audio
    assert model.data(hit, roles['valueText']) == '123'
    assert model.data(hit, roles['valueKind']) == 'id'
    assert model.data(hit, roles['importable']) is True
    assert model.data(description, roles['valueKind']) == 'string'
    assert model.data(description, roles['importable']) is False

    model.set_query('impact')

    assert model.rowCount() == 1
    filtered_audio = model.index(0, 0)
    assert model.data(filtered_audio, roles['nodeName']) == 'Audio'
    assert model.rowCount(filtered_audio) == 1
    assert model.data(model.index(0, 0, filtered_audio), roles['nodeName']) == 'Description'
    assert model.parent(QModelIndex()) == QModelIndex()


def test_custom_import_validates_and_materializes_json_without_overwriting(tmp_path: Path) -> None:
    cache_file = tmp_path / 'PreJsons' / 'CLOG.json'
    cache_file.parent.mkdir(parents=True)
    cache_file.write_text('{"games": {}}', encoding='utf-8')
    originals_source = tmp_path / 'originals.json'
    replacements_source = tmp_path / 'replacements.json'
    originals_source.write_text('{"Audio": {"Hit": "123"}}', encoding='utf-8')
    replacements_source.write_text('{"Audio": {"Hit": "456"}}', encoding='utf-8')
    originals_dir = tmp_path / 'originals'
    replacements_dir = tmp_path / 'replacements'
    custom_dir = tmp_path / 'custom'
    store = CommunityPresetCatalog(
        catalog_url='https://example.invalid/CLOG.json',
        cache_file=cache_file,
        custom_dumps_dir=custom_dir,
        originals_dir=originals_dir,
        replacements_dir=replacements_dir,
        fetch=lambda _url, _timeout: (_ for _ in ()).throw(RuntimeError('offline')),
    )

    imported = store.import_custom(
        CustomPresetRequest(
            name='My:Preset',
            place_id='101',
            originals_source=str(originals_source),
            replacements_source=str(replacements_source),
            credit='Builder',
        )
    )

    assert len(imported) == 1
    assert imported[0].is_custom
    assert imported[0].custom_path is not None
    assert imported[0].custom_path.parent == custom_dir
    assert Path(imported[0].originals_source).parent == originals_dir
    assert Path(imported[0].replacements_source).parent == replacements_dir
    assert ':' not in Path(imported[0].originals_source).name
    assert json.loads(Path(imported[0].originals_source).read_text())['Audio']['Hit'] == '123'
    assert store.load(refresh=False).presets[0].name == 'My:Preset'

    second = store.import_custom(
        CustomPresetRequest(
            name='My:Preset',
            originals_source=str(originals_source),
        )
    )
    assert second[0].originals_source != imported[0].originals_source


def test_custom_import_rejects_invalid_payload_without_partial_files(tmp_path: Path) -> None:
    invalid = tmp_path / 'invalid.json'
    invalid.write_text('not JSON', encoding='utf-8')
    store = CommunityPresetCatalog(
        cache_file=tmp_path / 'CLOG.json',
        custom_dumps_dir=tmp_path / 'custom',
        originals_dir=tmp_path / 'originals',
        replacements_dir=tmp_path / 'replacements',
    )

    with pytest.raises(ValueError, match='not valid JSON'):
        store.import_custom(CustomPresetRequest(name='Broken', originals_source=str(invalid)))

    assert not list((tmp_path / 'custom').glob('*.json'))
    assert not list((tmp_path / 'originals').glob('*.json'))


def test_metadata_client_combines_game_and_thumbnail_endpoints() -> None:
    def fetch(url: str, _timeout: int) -> bytes:
        if url.endswith('/universe'):
            return b'{"universeId": 99}'
        if 'games?universeIds' in url:
            return b'{"data": [{"name": "Live name", "created": "2020-01-02"}]}'
        if 'thumbnails.roblox.com' in url:
            return b'{"data": [{"imageUrl": "https://images.invalid/game.png"}]}'
        raise AssertionError(url)

    metadata = RobloxPresetMetadataClient(fetch).fetch(101)

    assert metadata.name == 'Live name'
    assert metadata.created == '2020-01-02'
    assert metadata.thumbnail_url.endswith('game.png')


def test_metadata_workers_are_daemon_threads_and_stop_publishing_after_shutdown() -> None:
    entered = threading.Event()
    release = threading.Event()

    class MetadataClient:
        @staticmethod
        def fetch(_place_id: int):
            entered.set()
            release.wait(timeout=2.0)
            return None

    api = CommunityPresetsApi(metadata_client=MetadataClient())  # pyright: ignore[reportArgumentType]
    api._presets = [CommunityPreset('preset', 'Preset', place_id=101)]
    try:
        api._schedule_metadata()
        assert entered.wait(timeout=1.0)
        assert api._metadata_threads
        assert all(thread.daemon for thread in api._metadata_threads)
        api.shutdown()
    finally:
        release.set()
        api.shutdown()


def test_community_selection_prepares_a_replacer_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    replacer = ReplacerApi(  # pyright: ignore[reportCallIssue]
        manager_module.ConfigManager()
    )
    community: Any = replacer.communityPresets
    first = PresetValue('first', 'Audio › Hit', 'Hit', 123, 'id')
    duplicate = PresetValue('duplicate', 'Audio › Hit 2', 'Hit 2', 123, 'id')
    source_url = PresetValue(
        'source-url',
        'Texture › Source',
        'Source',
        'https://assets.invalid/texture.png',
        'url',
    )
    community._values = [first, duplicate, source_url]
    community._selected_preset_name = 'Phantom Forces'
    community.valueSelection.setSelected(first.row_id, True)  # pyright: ignore[reportAttributeAccessIssue]
    community.valueSelection.setSelected(  # pyright: ignore[reportAttributeAccessIssue]
        duplicate.row_id,
        True,
    )
    community.valueSelection.setSelected(  # pyright: ignore[reportAttributeAccessIssue]
        source_url.row_id,
        True,
    )
    try:
        assert community.useSelectedAsTargets()
        assert replacer.takeDraft() == {
            'name': 'Phantom Forces preset',
            'targets': '123, https://assets.invalid/texture.png',
            'replacement': '',
        }
    finally:
        replacer.shutdown()


def test_closing_dialog_ignores_in_flight_preset_payload(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()

    def fetch(_url: str, _timeout: int) -> bytes:
        entered.set()
        assert release.wait(2)
        return b'{"Audio": {"Hit": "123"}}'

    api = CommunityPresetsApi(  # pyright: ignore[reportCallIssue]
        CommunityPresetCatalog(
            cache_file=tmp_path / 'CLOG.json',
            custom_dumps_dir=tmp_path / 'custom',
            originals_dir=tmp_path / 'originals',
            replacements_dir=tmp_path / 'replacements',
            fetch=fetch,
        )
    )
    api._presets = [
        CommunityPreset(
            preset_id='race',
            name='Race test',
            originals_source='https://example.invalid/originals.json',
        )
    ]
    try:
        assert api.openPreset('race', 'originals')
        assert entered.wait(1)
        api.closePayload()
        release.set()

        application = QCoreApplication.instance()
        deadline = time.monotonic() + 2
        while api.task.busy and time.monotonic() < deadline:
            if application is not None:
                application.processEvents()
            time.sleep(0.01)

        assert not api.task.busy
        assert not api.payloadOpen
        assert api.valueModel.count == 0  # pyright: ignore[reportAttributeAccessIssue]
    finally:
        release.set()
        api.shutdown()
