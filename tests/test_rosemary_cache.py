"""Rosemary cache deletion is scoped to Nutmeg's Roblox Player storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import (
    ROSEMARY_CLIENT,
    LinuxClientInstallation,
    rosemary_guest_home,
    running_rosemary_studios,
)

if TYPE_CHECKING:
    import pytest


def _installation(home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=home), None)


def _prepare(home: Path, monkeypatch: pytest.MonkeyPatch) -> LinuxClientInstallation:
    installation = _installation(home)
    monkeypatch.delenv('ROSEMARY_PREFIX', raising=False)
    monkeypatch.delenv('ROSEMARY_PREFIX_HOME', raising=False)
    monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    monkeypatch.delenv('XDG_DATA_HOME', raising=False)
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: installation
    )
    monkeypatch.setattr(platform_linux, 'running_rosemary_studios', lambda **_kw: ())
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', home / 'fleasion-cache')
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', None)
    return installation


def _make_cache(guest_home: Path) -> tuple[Path, Path, Path]:
    storage = guest_home / 'Library/Roblox'
    storage.mkdir(parents=True)
    for name in (
        'rbx-storage.db', 'rbx-storage.db-wal', 'rbx-storage.db-shm',
        'rbx-storage.db-journal', 'rbx-storage.id'
    ):
        (storage / name).write_bytes(b'cached')
    for name in ('rbx-storage', 'rbx-storage-sc'):
        folder = storage / name
        folder.mkdir()
        (folder / 'chunk').write_bytes(b'cached')
    (storage / 'ClientSettings').mkdir()
    studio_settings = storage / 'GlobalBasicSettings_13_Studio.xml'
    studio_settings.write_text('keep')
    cookie = guest_home / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv'
    cookie.parent.mkdir()
    cookie.write_text('cookie-store-must-remain')
    studio_autosaves = guest_home / 'Library/Application Support/Roblox/RobloxStudio/AutoSaves'
    studio_autosaves.mkdir(parents=True)
    (studio_autosaves / 'work.rbxl').write_text('keep')
    cache_folder = guest_home / 'Library/Caches/com.roblox.RobloxPlayer'
    cache_folder.mkdir(parents=True)
    (cache_folder / 'item').write_bytes(b'cached')
    return storage, cookie, studio_settings


def test_rosemary_clear_cache_deletes_storage_but_keeps_cookies_and_studio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _prepare(tmp_path, monkeypatch)
    guest_home = rosemary_guest_home(installation)
    assert guest_home is not None
    storage, cookie, studio_settings = _make_cache(guest_home)
    sibling_file = guest_home / 'Library/Application Support/Roblox/RobloxStudio/AutoSaves/work.rbxl'
    messages = platform_linux.delete_cache()
    assert 'Storage database deleted successfully' in messages
    for name in (
        'rbx-storage.db', 'rbx-storage.db-wal', 'rbx-storage.db-shm',
        'rbx-storage.db-journal', 'rbx-storage.id', 'rbx-storage', 'rbx-storage-sc'
    ):
        assert not (storage / name).exists()
    assert not (guest_home / 'Library/Caches/com.roblox.RobloxPlayer').exists()
    assert cookie.read_text() == 'cookie-store-must-remain'
    assert studio_settings.read_text() == 'keep'
    assert sibling_file.read_text() == 'keep'
    assert (storage / 'ClientSettings').exists()


def test_rosemary_clear_cache_obeys_custom_nutmeg_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _prepare(tmp_path, monkeypatch)
    config = tmp_path / '.config/nutmeg/config.json'
    config.parent.mkdir(parents=True)
    prefix = tmp_path / 'another-volume/custom-prefix'
    config.write_text(json.dumps({'prefix_path': str(prefix)}))
    guest_home = rosemary_guest_home(installation)
    assert guest_home == prefix / 'root/Users' / tmp_path.name
    storage, cookie, _studio_settings = _make_cache(guest_home)
    default_storage = tmp_path / '.local/share/nutmeg/prefix/root/Users' / tmp_path.name / 'Library/Roblox'
    default_storage.mkdir(parents=True)
    (default_storage / 'rbx-storage.db').write_text('unrelated')
    platform_linux.delete_cache()
    assert not (storage / 'rbx-storage.db').exists()
    assert (default_storage / 'rbx-storage.db').read_text() == 'unrelated'
    assert cookie.exists()


def test_rosemary_clear_cache_refuses_running_studio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _prepare(tmp_path, monkeypatch)
    guest_home = rosemary_guest_home(installation)
    assert guest_home is not None
    storage, _, _ = _make_cache(guest_home)
    monkeypatch.setattr(platform_linux, 'running_rosemary_studios', lambda **_kw: (678,))
    called: list[str] = []
    monkeypatch.setattr(platform_linux, 'terminate_roblox', lambda: called.append('terminate'))
    messages = platform_linux.delete_cache()
    assert 'Cache deletion aborted' in ' '.join(messages)
    assert (storage / 'rbx-storage.db').exists()
    assert called == []


def test_rosemary_clear_cache_refuses_unknown_prefix_and_symlinks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _prepare(tmp_path, monkeypatch)
    config = tmp_path / '.config/nutmeg/config.json'
    config.parent.mkdir(parents=True)
    config.write_text('{not-json')
    assert rosemary_guest_home(installation) is None
    assert 'Cache deletion aborted' in ' '.join(platform_linux.delete_cache())
    config.unlink()
    guest_home = rosemary_guest_home(installation)
    assert guest_home is not None
    storage, cookie, _ = _make_cache(guest_home)
    external = tmp_path / 'external-storage'
    external.mkdir()
    (external / 'untouched').write_text('keep')
    import shutil
    shutil.rmtree(storage / 'rbx-storage')
    (storage / 'rbx-storage').symlink_to(external, target_is_directory=True)
    messages = platform_linux.delete_cache()
    assert any('symbolic link' in message for message in messages)
    assert (external / 'untouched').read_text() == 'keep'
    assert cookie.exists()


def test_rosemary_studio_pid_detection_excludes_other_rosemary_apps(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    for pid, argv in (
        (101, ['/usr/bin/rosemary', 'run', '/apps/RobloxStudio.app']),
        (102, ['/usr/bin/rosemary', 'run', '/apps/Roblox.app']),
        (103, ['/usr/bin/bash', 'rosemary', 'run', '/apps/RobloxStudio.app']),
    ):
        process = proc / str(pid)
        process.mkdir(parents=True)
        (process / 'cmdline').write_bytes(b'\0'.join(arg.encode() for arg in argv) + b'\0')
    assert running_rosemary_studios(proc_root=proc) == (101,)


def test_rosemary_configured_prefix_is_shared_by_cache_and_cookie_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fleasion.utils.roblox_auth import ROSEMARY_LOCAL_AUTH_PROVIDER

    installation = _prepare(tmp_path, monkeypatch)
    prefix = tmp_path / 'manually-installed-rosemary-prefix'
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', str(prefix))
    guest_home = prefix / 'root/Users' / tmp_path.name
    storage, _, _ = _make_cache(guest_home)
    targets = platform_linux._rosemary_cache_targets(installation)
    assert targets is not None
    assert any(path == storage / 'rbx-storage.db' for path, _ in targets)
    assert ROSEMARY_LOCAL_AUTH_PROVIDER.cookie_path(installation) == (
        guest_home / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv'
    )
    assert rosemary_guest_home(installation, prefix_path=str(prefix)) == guest_home

    messages = platform_linux.delete_cache()
    assert 'Storage database deleted successfully' in messages
    assert not (storage / 'rbx-storage.db').exists()
    assert (guest_home / 'Library/Cookies/com.roblox.RobloxPlayer.cookies.tsv').exists()
