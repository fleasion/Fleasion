import json
import os
import stat
import sys
import types
import threading
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from fleasion.modifications import fflag_manager, platform_targets
from fleasion.modifications import manager as modifications_manager
from fleasion.cache.tools.rgba_ktx2 import read_rgba8_ktx2, read_rgba8_ktx2_levels
from fleasion.modifications.fflag_manager import FastFlagManager
from fleasion.modifications.manager import ModificationManager, normalise_target_path
from fleasion.modifications.stash_paths import resource_stash_dir


class _SignalSpy:
    def __init__(self):
        self.calls = []

    def emit(self, *args):
        self.calls.append(args)


def _manager_for_entry(entry):
    manager = ModificationManager.__new__(ModificationManager)
    manager._data = {"entries": [entry]}
    manager._save_json = lambda: None
    manager.entry_status_changed = _SignalSpy()
    manager.restore_finished = _SignalSpy()
    return manager


def _raise_permission_denied(_entry):
    raise PermissionError("Permission denied")


def test_cdn_modification_download_uses_hard_size_cap(tmp_path, monkeypatch):
    cache_dir = tmp_path / 'ModCache'
    calls = []

    def fake_http_get(url, timeout, headers, *, max_bytes):
        calls.append((url, timeout, headers, max_bytes))
        return b'modification'

    monkeypatch.setattr(modifications_manager, 'MOD_CACHE_DIR', cache_dir)
    monkeypatch.setattr('fleasion.utils.http.http_get', fake_http_get)
    manager = ModificationManager.__new__(ModificationManager)

    assert manager._fetch_cdn_url('https://cdn.example/cursor.png') == b'modification'
    assert calls == [
        (
            'https://cdn.example/cursor.png',
            30,
            {'User-Agent': 'Mozilla/5.0'},
            modifications_manager.MODIFICATION_DOWNLOAD_MAX_BYTES,
        )
    ]
    assert next(cache_dir.iterdir()).read_bytes() == b'modification'


def test_normalise_target_path_converts_windows_separators_on_posix():
    assert normalise_target_path(r"content\textures\MouseLockedCursor.png").as_posix() == (
        "content/textures/MouseLockedCursor.png"
    )


@pytest.mark.parametrize(
    "target",
    [
        "",
        "/tmp/outside.bin",
        r"C:\Windows\outside.bin",
        "content/../outside.bin",
        "../outside.bin",
        ".",
    ],
)
def test_normalise_target_path_rejects_escape_paths(target):
    with pytest.raises(ValueError):
        normalise_target_path(target)


@pytest.mark.parametrize('target', ['/tmp/outside.bin', r'C:\outside.bin', r'\\server\share\file.bin'])
def test_resource_target_resolution_preserves_absolute_path_rejection(tmp_path, target):
    with pytest.raises(ValueError):
        modifications_manager.target_path_for_roblox_dir(target, tmp_path / 'resources')


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX path-separator fixture')
def test_stash_write_and_restore_use_normalised_target_paths(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    target = roblox_dir / "content" / "textures" / "MouseLockedCursor.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._stash_dir = tmp_path / "stash"
    manager._fs_lock = threading.Lock()

    manager._stash_and_write(r"content\textures\MouseLockedCursor.png", b"modified")

    assert target.read_bytes() == b"modified"
    assert not (roblox_dir / r"content\textures\MouseLockedCursor.png").exists()
    assert (
        resource_stash_dir(tmp_path / "stash", roblox_dir)
        / "content"
        / "textures"
        / "MouseLockedCursor.png"
    ).read_bytes() == b"original"

    manager._restore_entry({"target_path": r"content\textures\MouseLockedCursor.png"})

    assert target.read_bytes() == b"original"


def test_stash_write_records_permission_denials_and_continues(tmp_path, monkeypatch):
    denied_dir = tmp_path / 'denied'
    writable_dir = tmp_path / 'writable'
    for path in (denied_dir, writable_dir):
        (path / 'RobloxPlayerBeta.exe').parent.mkdir(parents=True)
        (path / 'RobloxPlayerBeta.exe').write_bytes(b'')

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [denied_dir, writable_dir]
    manager._stash_dir = tmp_path / 'stash'
    manager._fs_lock = threading.Lock()
    manager._permission_denied_lock = threading.Lock()
    manager._permission_denied_dirs = set()
    manager._unlock_managed_files_locked = lambda: None
    manager._protect_managed_files_locked = lambda: None

    original_write_bytes = Path.write_bytes

    def fake_write_bytes(path, data):
        if path.is_relative_to(denied_dir):
            raise PermissionError('protected install')
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, 'write_bytes', fake_write_bytes)

    with pytest.raises(PermissionError, match='denied'):
        manager._stash_and_write('content/example.bin', b'modified')

    assert manager.take_permission_denied_dirs() == [denied_dir.resolve()]
    assert (writable_dir / 'content' / 'example.bin').read_bytes() == b'modified'


def test_stale_background_apply_cannot_write_after_restore_generation_changes(tmp_path):
    roblox_dir = tmp_path / 'Roblox'
    target = roblox_dir / 'content' / 'example.bin'
    target.parent.mkdir(parents=True)
    target.write_bytes(b'original')
    entry = {'_apply_gen': 2}

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._stash_dir = tmp_path / 'stash'
    manager._fs_lock = threading.RLock()

    written = manager._stash_and_write(
        'content/example.bin',
        b'stale modification',
        entry=entry,
        apply_gen=1,
    )

    assert not written
    assert target.read_bytes() == b'original'


def test_reapply_all_stops_before_next_entry_after_restore_generation_changes():
    first = {
        'id': 'first',
        'source_type': 'local_file',
        'source_value': 'first.bin',
    }
    second = {
        'id': 'second',
        'source_type': 'local_file',
        'source_value': 'second.bin',
    }
    manager = ModificationManager.__new__(ModificationManager)
    manager._fs_lock = threading.RLock()
    manager._bulk_apply_gen = 0
    manager._data = {
        'entries': [first, second],
        'fast_flags_enabled': False,
        'fast_flags': {},
    }
    applied: list[str] = []

    def apply_entry(entry):
        applied.append(entry['id'])
        manager._bulk_apply_gen += 1

    manager._process_and_apply_entry = apply_entry

    manager.reapply_all()

    assert applied == ['first']


def test_cancel_pending_operations_invalidates_bulk_and_entry_workers():
    entries = [{'_apply_gen': 1}, {}]
    manager = ModificationManager.__new__(ModificationManager)
    manager._fs_lock = threading.RLock()
    manager._bulk_apply_gen = 4
    manager._data = {'entries': entries}

    manager.cancel_pending_operations()

    assert manager._bulk_apply_gen == 5
    assert [entry['_apply_gen'] for entry in entries] == [2, 1]


def test_orphan_restore_removes_conflicting_new_file_marker(tmp_path):
    roblox_dir = tmp_path / 'Roblox'
    target_path = Path('content/example.bin')
    destination = roblox_dir / target_path
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b'modified')
    stash_root = tmp_path / 'stash'
    stash = resource_stash_dir(stash_root, roblox_dir) / target_path
    stash.parent.mkdir(parents=True)
    stash.write_bytes(b'original')
    marker = stash.with_name(stash.name + '.fleasion_new')
    marker.touch()
    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._stash_dir = stash_root
    manager._fs_lock = threading.RLock()

    assert manager.restore_orphaned_stash(target_path.as_posix())

    assert destination.read_bytes() == b'original'
    assert not stash.exists()
    assert not marker.exists()


def test_read_only_guard_protects_managed_files_and_clears_on_close(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    target = roblox_dir / "content" / "textures" / "MouseLockedCursor.png"
    settings = roblox_dir / "ClientSettings" / "ClientAppSettings.json"
    cacert = roblox_dir / "ssl" / "cacert.pem"
    for path in (target, settings, cacert):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")
    cacert.chmod(0o444)

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._data = {
        "entries": [
            {
                "target_path": r"content\textures\MouseLockedCursor.png",
                "source_type": "local_file",
                "source_value": "replacement.png",
            }
        ],
        "fast_flags_enabled": True,
    }
    manager._read_only_original_modes = {}
    manager._read_only_lock_enabled = True

    manager.protect_managed_files([cacert])

    assert not (target.stat().st_mode & stat.S_IWRITE)
    assert not (settings.stat().st_mode & stat.S_IWRITE)
    assert not (cacert.stat().st_mode & stat.S_IWRITE)

    manager.clear_managed_file_read_only(clear_untracked=True)

    assert target.stat().st_mode & stat.S_IWRITE
    assert settings.stat().st_mode & stat.S_IWRITE
    assert not (cacert.stat().st_mode & stat.S_IWRITE)


def test_read_only_guard_is_off_until_explicitly_enabled(tmp_path):
    roblox_dir = tmp_path / "Roblox" / "Resources"
    target = roblox_dir / "content" / "textures" / "Cursor.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._data = {
        "entries": [
            {
                "target_path": r"content\textures\Cursor.png",
                "source_type": "local_file",
                "source_value": "cursor.png",
            }
        ]
    }
    manager._fs_lock = threading.Lock()
    manager._read_only_original_modes = {}
    manager._read_only_extra_paths = set()
    manager._read_only_lock_enabled = False

    manager.protect_managed_files()
    assert target.stat().st_mode & stat.S_IWRITE

    manager.set_read_only_lock_enabled(True)
    assert not (target.stat().st_mode & stat.S_IWRITE)

    manager.set_read_only_lock_enabled(False)
    assert target.stat().st_mode & stat.S_IWRITE


def test_read_only_guard_restores_modes_after_unclean_restart(tmp_path):
    roblox_dir = tmp_path / "Roblox" / "Resources"
    target = roblox_dir / "content" / "textures" / "Cursor.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    state_file = tmp_path / "read_only_modes.json"

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._data = {
        "entries": [
            {
                "target_path": r"content\textures\Cursor.png",
                "source_type": "local_file",
                "source_value": "cursor.png",
            }
        ]
    }
    manager._fs_lock = threading.Lock()
    manager._read_only_state_file = state_file
    manager._read_only_original_modes = {}
    manager._read_only_extra_paths = set()
    manager._read_only_lock_enabled = True
    manager.protect_managed_files()
    assert state_file.exists()
    assert not (target.stat().st_mode & stat.S_IWRITE)

    restarted = ModificationManager.__new__(ModificationManager)
    restarted._roblox_dirs = [roblox_dir]
    restarted._data = manager._data
    restarted._fs_lock = threading.Lock()
    restarted._read_only_state_file = state_file
    restarted._read_only_original_modes = restarted._load_read_only_original_modes()
    restarted._read_only_extra_paths = set()
    restarted._read_only_lock_enabled = False
    restarted.clear_managed_file_read_only(clear_untracked=False)

    assert target.stat().st_mode & stat.S_IWRITE
    assert not state_file.exists()

def test_restore_all_restores_guarded_cacert_original_mode(tmp_path):
    roblox_dir = tmp_path / "Fishstrap" / "Versions" / "WindowsPlayer"
    cacert = roblox_dir / "ssl" / "cacert.pem"
    cacert.parent.mkdir(parents=True)
    cacert.write_bytes(b"cert")
    cacert.chmod(0o444)

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._stash_dir = tmp_path / "stash"
    manager._fs_lock = threading.Lock()
    manager._data = {"entries": [], "fast_flags_enabled": False}
    manager._read_only_original_modes = {}
    manager._read_only_extra_paths = set()
    manager._read_only_lock_enabled = True
    manager.global_settings_manager = types.SimpleNamespace(restore=lambda: None)
    manager.restore_finished = _SignalSpy()

    manager.protect_managed_files([cacert])
    assert not (cacert.stat().st_mode & stat.S_IWRITE)

    manager.restore_all()

    assert not (cacert.stat().st_mode & stat.S_IWRITE)
    assert manager.restore_finished.calls == [()]


def test_stash_write_does_not_preserve_guarded_read_only_mode(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    target_path = r"content\textures\MouseLockedCursor.png"
    target = roblox_dir / "content" / "textures" / "MouseLockedCursor.png"
    cacert = roblox_dir / "ssl" / "cacert.pem"
    target.parent.mkdir(parents=True)
    cacert.parent.mkdir(parents=True)
    target.write_bytes(b"original")
    cacert.write_bytes(b"cert")

    entry = {
        "target_path": target_path,
        "source_type": "local_file",
        "source_value": "replacement.png",
    }
    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [roblox_dir]
    manager._stash_dir = tmp_path / "stash"
    manager._fs_lock = threading.Lock()
    manager._data = {"entries": [entry]}
    manager._read_only_original_modes = {}
    manager._read_only_extra_paths = set()
    manager._read_only_lock_enabled = True

    manager.protect_managed_files([cacert])
    assert not (target.stat().st_mode & stat.S_IWRITE)
    assert not (cacert.stat().st_mode & stat.S_IWRITE)

    manager._stash_and_write(target_path, b"modified")

    stash = (
        resource_stash_dir(manager._stash_dir, roblox_dir)
        / "content"
        / "textures"
        / "MouseLockedCursor.png"
    )
    assert target.read_bytes() == b"modified"
    assert not (target.stat().st_mode & stat.S_IWRITE)
    assert not (cacert.stat().st_mode & stat.S_IWRITE)
    assert stash.stat().st_mode & stat.S_IWRITE

    manager.clear_managed_file_read_only()
    manager._restore_entry(entry)

    assert target.read_bytes() == b"original"
    assert target.stat().st_mode & stat.S_IWRITE


def test_clear_entry_restore_failure_keeps_entry_and_reports_error(monkeypatch):
    entry = {
        "id": "entry-1",
        "display_name": "Sky Back",
        "target_path": r"PlatformContent\pc\textures\sky\sky512_bk.tex",
        "status": "error",
        "error_message": "File not found: invalid.png",
    }
    manager = _manager_for_entry(entry)
    monkeypatch.setattr(manager, "_restore_entry", _raise_permission_denied)

    assert manager.clear_entry("entry-1") is False

    assert manager.entries == [entry]
    assert entry["status"] == "error"
    assert "Failed to restore original file" in entry["error_message"]
    assert manager.entry_status_changed.calls == [
        ("entry-1", "error", entry["error_message"])
    ]
    assert manager.restore_finished.calls == []


def test_update_entry_restore_failure_keeps_existing_source_and_reports_error(monkeypatch):
    entry = {
        "id": "entry-1",
        "display_name": "Sky Back",
        "target_path": r"PlatformContent\pc\textures\sky\sky512_bk.tex",
        "source_type": "asset_id",
        "source_value": "123",
        "status": "applied",
        "error_message": None,
    }
    manager = _manager_for_entry(entry)
    monkeypatch.setattr(manager, "_restore_entry", _raise_permission_denied)

    assert manager.update_entry(
        "entry-1",
        source_type="local_file",
        source_value=r"C:\missing.png",
    ) is False

    assert entry["source_type"] == "asset_id"
    assert entry["source_value"] == "123"
    assert entry["status"] == "error"
    assert "Failed to restore original file" in entry["error_message"]
    assert manager.entry_status_changed.calls == [
        ("entry-1", "error", entry["error_message"])
    ]
    assert manager.restore_finished.calls == []


def test_remove_entry_restore_failure_keeps_entry_and_reports_error(monkeypatch):
    entry = {
        "id": "entry-1",
        "display_name": "Custom",
        "target_path": r"content\textures\cursor.png",
        "status": "applied",
        "error_message": None,
    }
    manager = _manager_for_entry(entry)
    monkeypatch.setattr(manager, "_restore_entry", _raise_permission_denied)

    assert manager.remove_entry("entry-1") is False

    assert manager.entries == [entry]
    assert entry["status"] == "error"
    assert "Failed to restore original file" in entry["error_message"]
    assert manager.entry_status_changed.calls == [
        ("entry-1", "error", entry["error_message"])
    ]
    assert manager.restore_finished.calls == []


def test_fast_flags_write_to_clientsettings_under_resource_root(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    manager = FastFlagManager([roblox_dir], tmp_path / "stash")

    manager.write({"grey_sky": True})

    settings_path = roblox_dir / "ClientSettings" / "ClientAppSettings.json"
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "FFlagDebugSkyGray": "True",
    }


def test_fast_flags_reports_permission_denied_installations(tmp_path, monkeypatch):
    roblox_dir = tmp_path / 'Roblox' / 'Versions' / 'version-protected'
    monkeypatch.setattr(fflag_manager.sys, 'platform', 'win32')

    original_write_bytes = Path.write_bytes

    def fake_write_bytes(path, data):
        if path.is_relative_to(roblox_dir):
            raise PermissionError('protected install')
        return original_write_bytes(path, data)

    monkeypatch.setattr(Path, 'write_bytes', fake_write_bytes)

    failed_dirs = FastFlagManager([roblox_dir], tmp_path / 'stash').write({'grey_sky': True})

    assert failed_dirs == {roblox_dir}


def test_macos_fast_flags_cover_resource_and_appleblox_locations(tmp_path, monkeypatch):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    roblox_dir.mkdir(parents=True)
    monkeypatch.setattr(fflag_manager.sys, "platform", "darwin")
    manager = FastFlagManager([roblox_dir], tmp_path / "stash")

    manager.write({"grey_sky": True})

    expected = {"FFlagDebugSkyGray": "True"}
    assert json.loads(
        (roblox_dir / "ClientSettings" / "ClientAppSettings.json").read_text(
            encoding="utf-8"
        )
    ) == expected
    assert json.loads(
        (
            roblox_dir.parent
            / "MacOS"
            / "ClientSettings"
            / "ClientAppSettings.json"
        ).read_text(encoding="utf-8")
    ) == expected

    manager.restore()
    assert not (roblox_dir / "ClientSettings" / "ClientAppSettings.json").exists()
    assert not (
        roblox_dir.parent
        / "MacOS"
        / "ClientSettings"
        / "ClientAppSettings.json"
    ).exists()


def test_macos_reassert_merges_fleasion_flags_into_appleblox_launch_file(
    tmp_path, monkeypatch
):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    launch_settings = (
        roblox_dir.parent
        / "MacOS"
        / "ClientSettings"
        / "ClientAppSettings.json"
    )
    launch_settings.parent.mkdir(parents=True)
    launch_settings.write_text(
        json.dumps(
            {
                "DFFlagDisableDPIScale": True,
                "FFlagDebugSkyGray": False,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(fflag_manager.sys, "platform", "darwin")
    manager = FastFlagManager([roblox_dir], tmp_path / "stash")

    assert manager.reassert_macos_bootstrapper_flags({"grey_sky": True}) == 1
    assert json.loads(launch_settings.read_text(encoding="utf-8")) == {
        "DFFlagDisableDPIScale": True,
        "FFlagDebugSkyGray": "True",
    }
    assert not (tmp_path / "stash").exists()
    assert manager.reassert_macos_bootstrapper_flags({"grey_sky": True}) == 0


def test_macos_same_named_resource_roots_have_distinct_stashes(tmp_path, monkeypatch):
    monkeypatch.setattr(modifications_manager.sys, "platform", "darwin")
    first = tmp_path / "Roblox.app" / "Contents" / "Resources"
    second = tmp_path / "RobloxPlayer.app" / "Contents" / "Resources"
    relative = Path("content") / "textures" / "cursor.png"
    for root, original in ((first, b"regular"), (second, b"froststrap")):
        target = root / relative
        target.parent.mkdir(parents=True)
        target.write_bytes(original)

    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [first, second]
    manager._stash_dir = tmp_path / "stash"
    manager._fs_lock = threading.Lock()
    manager._data = {"entries": []}
    manager._read_only_original_modes = {}
    manager._read_only_extra_paths = set()

    manager._stash_and_write(str(relative), b"modified")

    first_stash = resource_stash_dir(manager._stash_dir, first) / relative
    second_stash = resource_stash_dir(manager._stash_dir, second) / relative
    assert first_stash != second_stash
    assert first_stash.read_bytes() == b"regular"
    assert second_stash.read_bytes() == b"froststrap"

    manager._restore_entry({"target_path": str(relative)})
    assert (first / relative).read_bytes() == b"regular"
    assert (second / relative).read_bytes() == b"froststrap"


def test_linux_same_named_resource_roots_have_distinct_stashes(tmp_path, monkeypatch):
    monkeypatch.setattr(modifications_manager.sys, 'platform', 'linux')
    first = tmp_path / '.var' / 'app' / 'example.one' / 'data' / 'client' / 'asset_overlay'
    second = tmp_path / '.var' / 'app' / 'example.two' / 'data' / 'client' / 'asset_overlay'
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    first_stash = resource_stash_dir(tmp_path / 'stash', first)
    second_stash = resource_stash_dir(tmp_path / 'stash', second)

    assert first_stash != second_stash
    assert first_stash.name.startswith('Resources-LinuxRoblox-asset_overlay-')
    assert second_stash.name.startswith('Resources-LinuxRoblox-asset_overlay-')


def test_logical_target_resolves_per_linux_resource_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(modifications_manager.sys, 'platform', 'linux')
    sober_root = tmp_path / 'sober' / 'resources'
    future_root = tmp_path / 'future' / 'resources'
    sober_target = sober_root / 'android' / 'textures' / 'sky' / 'sky512_bk.tex'
    future_target = (
        future_root / 'PlatformContent' / 'pc' / 'textures' / 'sky' / 'sky512_bk.tex'
    )
    for target, original in ((sober_target, b'sober'), (future_target, b'future')):
        target.parent.mkdir(parents=True)
        target.write_bytes(original)

    monkeypatch.setattr(
        platform_targets,
        '_linux_resource_client_key',
        lambda resource_dir: 'sober' if Path(resource_dir) == sober_root else 'future',
    )
    manager = ModificationManager.__new__(ModificationManager)
    manager._roblox_dirs = [sober_root, future_root]
    manager._stash_dir = tmp_path / 'stash'
    manager._fs_lock = threading.Lock()

    logical = r'PlatformContent\pc\textures\sky\sky512_bk.tex'
    manager._stash_and_write(logical, b'modified')

    assert sober_target.read_bytes() == b'modified'
    assert future_target.read_bytes() == b'modified'
    manager._restore_entry({'target_path': logical})
    assert sober_target.read_bytes() == b'sober'
    assert future_target.read_bytes() == b'future'


def test_fast_flags_write_to_sober_config(tmp_path, monkeypatch):
    sober_root = tmp_path / ".var" / "app" / "org.vinegarhq.Sober"
    overlay = sober_root / "data" / "sober" / "asset_overlay"
    config_path = sober_root / "config" / "sober" / "config.json"
    overlay.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"close_on_leave": false, "fflags": {"Old": true}}', encoding="utf-8")
    config_path.chmod(0o444)

    monkeypatch.setattr(fflag_manager.sys, "platform", "linux")
    monkeypatch.setattr(
        "fleasion.utils.platform_linux.SOBER_ASSET_OVERLAY_DIR",
        overlay,
    )
    monkeypatch.setattr(
        "fleasion.utils.platform_linux.SOBER_LEGACY_EXE_DIR",
        sober_root / "data" / "sober" / "exe",
    )
    monkeypatch.setattr(
        "fleasion.utils.platform_linux.SOBER_CONFIG_FILE",
        config_path,
    )

    manager = FastFlagManager([overlay], tmp_path / "stash")

    manager.write({"grey_sky": True, "frm_quality_enabled": True, "frm_quality": 7})

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["close_on_leave"] is False
    assert payload["fflags"] == {
        "FFlagDebugSkyGray": True,
        "DFIntDebugFRMQualityLevelOverride": 7,
    }
    assert not (config_path.stat().st_mode & stat.S_IWRITE)

    manager.restore()

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "close_on_leave": False,
        "fflags": {"Old": True},
    }
    assert not (config_path.stat().st_mode & stat.S_IWRITE)


def test_ktx_backed_targets_convert_image_replacements_to_ktx2(monkeypatch, tmp_path):
    monkeypatch.setattr(modifications_manager, "MOD_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        modifications_manager,
        "read_current_platform_original_asset",
        lambda _target: b"\xabKTX 11\xbb\r\n\x1a\n" + b"original",
    )

    image = Image.new("RGBA", (4, 4), (1, 2, 3, 4))
    buf = BytesIO()
    image.save(buf, format="PNG")

    manager = ModificationManager.__new__(ModificationManager)
    converted = manager._coerce_replacement_for_target(
        "android/textures/sky/sky512_bk.tex",
        buf.getvalue(),
    )

    parsed = read_rgba8_ktx2_levels(converted)
    assert parsed is not None
    levels, width, height = parsed
    assert (width, height) == (4, 4)
    assert len(levels) == 3
    assert levels[0] == bytes((1, 2, 3, 4)) * 16


def test_prefixed_ktx_backed_targets_convert_image_replacements_to_ktx2(monkeypatch, tmp_path):
    monkeypatch.setattr(modifications_manager, "MOD_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        modifications_manager,
        "read_current_platform_original_asset",
        lambda _target: b"WRAP" + b"\xabKTX 11\xbb\r\n\x1a\n" + b"original",
    )

    image = Image.new("RGBA", (1, 1), (9, 8, 7, 6))
    buf = BytesIO()
    image.save(buf, format="PNG")

    manager = ModificationManager.__new__(ModificationManager)
    converted = manager._coerce_replacement_for_target(
        "android/textures/sky/sky512_bk.tex",
        buf.getvalue(),
    )

    assert read_rgba8_ktx2(converted) == (bytes((9, 8, 7, 6)), 1, 1)


def test_windows_find_roblox_dirs_ignores_invalid_registry_key_and_keeps_scanning(monkeypatch):
    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    fake_winreg = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        REG_SZ=1,
    )
    software_key = _Key()
    valid_key = _Key()
    valid_dir = Path('C:/ValidRoblox')
    valid_exe = os.path.join(str(valid_dir), modifications_manager.ROBLOX_PROCESS)

    def open_key(root, name):
        if root is fake_winreg.HKEY_CURRENT_USER and name == r'Software':
            return software_key
        if root is software_key and name == 'ValidVendor':
            return valid_key
        if root is software_key and name == 'corrupt\x00key':
            raise ValueError('embedded null character')
        raise OSError

    def enum_key(key, index):
        if key is software_key:
            if index == 0:
                return 'corrupt\x00key'
            if index == 1:
                return 'ValidVendor'
        raise OSError

    def query_value_ex(key, name):
        if key is valid_key and name == 'PlayerPath':
            return str(valid_dir / modifications_manager.ROBLOX_PROCESS), fake_winreg.REG_SZ
        raise OSError

    fake_winreg.OpenKey = open_key
    fake_winreg.EnumKey = enum_key
    fake_winreg.QueryValueEx = query_value_ex

    monkeypatch.setitem(sys.modules, 'winreg', fake_winreg)
    monkeypatch.setattr(modifications_manager.sys, 'platform', 'win32')
    monkeypatch.setattr(modifications_manager.os.path, 'isfile', lambda value: value == valid_exe)
    monkeypatch.setattr(modifications_manager, 'load_saved_roblox_dirs', lambda: [])
    monkeypatch.setattr(modifications_manager, 'save_saved_roblox_dirs', lambda _dirs: None)
    monkeypatch.setattr(modifications_manager, 'get_roblox_player_exe_path', lambda: None)

    assert modifications_manager._find_roblox_dirs() == [valid_dir]
