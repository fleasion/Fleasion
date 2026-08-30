import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest

from fleasion.modifications import platform_targets
from fleasion.modifications.manager import ModificationManager
from fleasion.utils import platform_linux


def _sober_client_key(_resource_dir: Path) -> str:
    return 'sober'


def _future_client_key(_resource_dir: Path) -> str:
    return 'future'


def _migrate_targets(manager: ModificationManager) -> bool:
    callback = cast(
        'Callable[[], bool]',
        getattr(manager, '_migrate_target_paths_for_current_platform'),
    )
    return callback()


def _manager_entries(manager: ModificationManager) -> list[dict[str, object]]:
    data = cast('dict[str, object]', manager.__dict__['_data'])
    return cast('list[dict[str, object]]', data['entries'])


def test_linux_sober_resource_target_maps_pc_sky_to_android(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')
    monkeypatch.setattr(
        platform_targets,
        '_linux_resource_client_key',
        _sober_client_key,
    )

    assert (
        platform_targets.target_path_for_resource_dir(
            r'PlatformContent\pc\textures\sky\sky512_bk.tex',
            tmp_path / 'sober-resources',
        )
        == 'android/textures/sky/sky512_bk.tex'
    )


def test_linux_resource_target_seam_preserves_logical_content_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')
    monkeypatch.setattr(
        platform_targets,
        '_linux_resource_client_key',
        _future_client_key,
    )
    resource_root = tmp_path / 'future-client' / 'resources'

    assert (
        platform_targets.target_path_for_resource_dir(r'content\sounds\oof.ogg', resource_root)
        == 'content/sounds/oof.ogg'
    )
    assert (
        platform_targets.target_path_for_resource_dir(
            r'PlatformContent\pc\textures\sky\sky512_bk.tex', resource_root
        )
        == 'PlatformContent/pc/textures/sky/sky512_bk.tex'
    )


def test_non_linux_target_path_keeps_existing_storage_form(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform_targets.sys, 'platform', 'win32')

    assert (
        platform_targets.target_path_for_current_platform(
            r'PlatformContent\pc\textures\sky\sky512_bk.tex'
        )
        == r'PlatformContent\pc\textures\sky\sky512_bk.tex'
    )


def test_read_linux_sober_original_asset_from_apk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sober_data = tmp_path / 'sober'
    apk = sober_data / 'packages' / 'x86_64' / 'com.roblox.client' / 'base.apk'
    apk.parent.mkdir(parents=True)
    with zipfile.ZipFile(apk, 'w') as archive:
        archive.writestr('assets/android/textures/sky/sky512_bk.tex', b'sky')

    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')
    monkeypatch.setattr(platform_linux, 'SOBER_DATA_DIR', sober_data)
    monkeypatch.setattr(platform_linux, 'SOBER_LEGACY_EXE_DIR', sober_data / 'exe')

    assert (
        platform_targets.read_current_platform_original_asset(
            r'PlatformContent\pc\textures\sky\sky512_bk.tex'
        )
        == b'sky'
    )


def test_modification_manager_migrates_saved_sober_path_to_logical_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')
    manager = ModificationManager.__new__(ModificationManager)
    manager.__dict__['_data'] = {
        'entries': [
            {
                'target_path': 'android/textures/sky/sky512_bk.tex',
                'source_type': 'local_file',
            }
        ]
    }

    assert _migrate_targets(manager) is True
    entries = _manager_entries(manager)
    assert entries[0]['target_path'] == ('PlatformContent/pc/textures/sky/sky512_bk.tex')


def test_read_linux_sober_original_directory_from_apk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sober_data = tmp_path / 'sober'
    apk = sober_data / 'packages' / 'x86_64' / 'com.roblox.client' / 'base.apk'
    apk.parent.mkdir(parents=True)
    with zipfile.ZipFile(apk, 'w') as archive:
        archive.writestr('assets/content/fonts/families/BuilderSans.json', b'{"faces": []}')
        archive.writestr('assets/content/fonts/families/Arimo.json', b'{"faces": []}')
        archive.writestr('assets/content/fonts/families/nested/ignored.json', b'{}')

    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')
    monkeypatch.setattr(platform_linux, 'SOBER_DATA_DIR', sober_data)
    monkeypatch.setattr(platform_linux, 'SOBER_LEGACY_EXE_DIR', sober_data / 'exe')
    monkeypatch.setattr(
        platform_targets,
        '_linux_resource_client_key',
        _sober_client_key,
    )

    result = platform_targets.read_current_platform_original_directory(
        'content/fonts/families',
        resource_dir=tmp_path / 'asset_overlay',
    )

    assert result == {
        'Arimo.json': b'{"faces": []}',
        'BuilderSans.json': b'{"faces": []}',
    }
