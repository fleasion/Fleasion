"""Shareable mod packs: export, validation of untrusted packs, and install."""

from __future__ import annotations

import json
import zipfile
from typing import TYPE_CHECKING

import pytest

from fleasion.config import manager
from fleasion.rules.asset_filters import make_step
from fleasion.rules.game_profiles import make_profile
from fleasion.rules.mod_packs import (
    PACK_EXTENSION,
    ModPackError,
    export_pack,
    install_pack,
    read_pack,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path / 'author')
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'author' / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'author' / 'configs')
    return manager.ConfigManager()


def _fresh_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    root = tmp_path / 'friend'
    monkeypatch.setattr(manager, 'CONFIG_DIR', root)
    monkeypatch.setattr(manager, 'CONFIG_FILE', root / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', root / 'configs')
    return manager.ConfigManager()


def _export(config: manager.ConfigManager, tmp_path: Path, **kwargs: object) -> Path:
    result = export_pack(
        config,
        ['Sky'],
        tmp_path / 'sky',
        name='Purple Sky',
        author='FleaGatto',
        description='A purple sky for every game',
        resolve_local_path=manager.resolve_local_replacement_path,
        **kwargs,  # type: ignore[arg-type]
    )
    return result.path


def _author_setup(config: manager.ConfigManager, tmp_path: Path) -> None:
    sky = tmp_path / 'sky_up.png'
    sky.write_bytes(b'\x89PNG\r\n\x1a\nfake-image')
    rules: list[manager.JsonValue] = [
        {
            'type': 'group',
            'name': 'Sky',
            'children': [
                {'replace_ids': [11111], 'mode': 'local', 'local_path': str(sky), 'enabled': True},
                {'replace_ids': [22222], 'mode': 'local', 'local_path': str(tmp_path / 'gone.png'), 'enabled': True},
            ],
        },
        {'replace_ids': [33333], 'mode': 'remove', 'enabled': True},
    ]
    config.create_config('Sky')
    config.set_replacement_rules('Sky', rules)
    config.asset_filters = {'enabled': True, 'include_materials': False, 'steps': [make_step('tint')]}
    config.game_profiles = [make_profile(5000, 'Surf', ['Sky']), make_profile(6000, 'Other', ['Unrelated'])]


def test_round_trip_moves_files_configs_profiles_and_filters(
    config: manager.ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _author_setup(config, tmp_path)
    result = export_pack(
        config,
        ['Sky', 'NotAConfig'],
        tmp_path / 'sky',
        name='Purple Sky',
        author='FleaGatto',
        resolve_local_path=manager.resolve_local_replacement_path,
    )
    assert result.path.suffix == PACK_EXTENSION
    assert result.file_count == 1
    assert result.missing_files == [str(tmp_path / 'gone.png')]

    preview = read_pack(result.path)
    assert preview.manifest['name'] == 'Purple Sky'
    assert preview.manifest['author'] == 'FleaGatto'
    assert list(preview.configs) == ['Sky']
    assert preview.rule_count == 3
    assert [profile['place_id'] for profile in preview.manifest['game_profiles']] == [5000]

    friend = _fresh_manager(tmp_path, monkeypatch)
    installed = install_pack(preview, friend, apply_filters=True)
    assert installed.configs == {'Sky': 'Sky'}
    assert 'Sky' in friend.enabled_configs
    assert installed.profiles_added == [5000]
    assert installed.filters_applied and friend.asset_filters['enabled']

    rule = friend.get_replacement_rules('Sky')[0]
    assert isinstance(rule, dict)
    children = rule['children']
    assert isinstance(children, list) and isinstance(children[0], dict)
    local_path = children[0]['local_path']
    assert isinstance(local_path, str) and local_path.startswith('/Pack - Purple Sky/')
    resolved = manager.resolve_local_replacement_path(local_path)
    assert resolved.read_bytes() == b'\x89PNG\r\n\x1a\nfake-image'
    assert friend.get_all_replacements()[3]  # the proxy now has a local replacement


def test_installing_twice_keeps_existing_configs_and_profiles(
    config: manager.ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _author_setup(config, tmp_path)
    pack = _export(config, tmp_path)
    friend = _fresh_manager(tmp_path, monkeypatch)
    install_pack(read_pack(pack), friend)
    again = install_pack(read_pack(pack), friend, enable_configs=False)
    assert again.configs == {'Sky': 'Sky (Purple Sky)'}
    assert again.profiles_skipped == [5000]
    assert 'Sky (Purple Sky)' not in friend.enabled_configs


def _write_pack(path: Path, members: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def _manifest(**extra: object) -> str:
    return json.dumps({'format': 1, 'name': 'Evil', **extra})


def _config(local_path: str) -> str:
    return json.dumps(
        {'name': 'Evil', 'replacement_rules': [{'replace_ids': [1], 'mode': 'local', 'local_path': local_path}]}
    )


def test_rejects_paths_that_escape_the_pack(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / 'evil.fleasionpack',
        {'manifest.json': _manifest(), 'configs/0.json': _config('pack:files/a.png'), '../../escape.png': b'x'},
    )
    with pytest.raises(ModPackError, match='unsafe path'):
        read_pack(pack)


def test_rejects_executable_files(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / 'evil.fleasionpack',
        {'manifest.json': _manifest(), 'configs/0.json': _config('pack:files/run.exe'), 'files/run.exe': b'MZ'},
    )
    with pytest.raises(ModPackError, match='file type'):
        read_pack(pack)


def test_paths_outside_the_pack_are_disabled_not_followed(tmp_path: Path) -> None:
    pack = _write_pack(
        tmp_path / 'evil.fleasionpack',
        {'manifest.json': _manifest(), 'configs/0.json': _config('/home/someone/.ssh/id_rsa')},
    )
    rule = read_pack(pack).configs['Evil'][0]
    assert isinstance(rule, dict)
    assert rule['local_path'] == '' and rule['enabled'] is False


def test_rejects_missing_files_newer_formats_and_non_packs(tmp_path: Path) -> None:
    missing = _write_pack(
        tmp_path / 'missing.fleasionpack',
        {'manifest.json': _manifest(), 'configs/0.json': _config('pack:files/nope.png')},
    )
    with pytest.raises(ModPackError, match='missing from the pack'):
        read_pack(missing)
    newer = _write_pack(tmp_path / 'newer.fleasionpack', {'manifest.json': json.dumps({'format': 99})})
    with pytest.raises(ModPackError, match='newer Fleasion'):
        read_pack(newer)
    (tmp_path / 'plain.txt').write_text('hello')
    with pytest.raises(ModPackError, match='not a Fleasion mod pack'):
        read_pack(tmp_path / 'plain.txt')
    empty = _write_pack(tmp_path / 'empty.fleasionpack', {'manifest.json': _manifest()})
    with pytest.raises(ModPackError, match='no replacement configs'):
        read_pack(empty)


def test_export_needs_at_least_one_existing_config(config: manager.ConfigManager, tmp_path: Path) -> None:
    with pytest.raises(ModPackError):
        export_pack(config, ['Nope'], tmp_path / 'x', name='x', resolve_local_path=manager.resolve_local_replacement_path)
