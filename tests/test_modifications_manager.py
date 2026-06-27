import json
import stat
import sys
import types
import threading
from io import BytesIO

import pytest
from PIL import Image

from Fleasion.modifications import manager as modifications_manager
from Fleasion.modifications import fflag_manager
from Fleasion.cache.tools.rgba_ktx2 import read_rgba8_ktx2
from Fleasion.modifications.fflag_manager import FastFlagManager
from Fleasion.modifications.manager import ModificationManager, normalise_target_path


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
        tmp_path
        / "stash"
        / roblox_dir.name
        / "content"
        / "textures"
        / "MouseLockedCursor.png"
    ).read_bytes() == b"original"

    manager._restore_entry({"target_path": r"content\textures\MouseLockedCursor.png"})

    assert target.read_bytes() == b"original"


def test_read_only_guard_protects_managed_files_and_restores_modes(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    target = roblox_dir / "content" / "textures" / "MouseLockedCursor.png"
    settings = roblox_dir / "ClientSettings" / "ClientAppSettings.json"
    cacert = roblox_dir / "ssl" / "cacert.pem"
    for path in (target, settings, cacert):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"original")

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

    manager.protect_managed_files([cacert])

    assert not (target.stat().st_mode & stat.S_IWRITE)
    assert not (settings.stat().st_mode & stat.S_IWRITE)
    assert not (cacert.stat().st_mode & stat.S_IWRITE)

    manager.clear_managed_file_read_only()

    assert target.stat().st_mode & stat.S_IWRITE
    assert settings.stat().st_mode & stat.S_IWRITE
    assert cacert.stat().st_mode & stat.S_IWRITE


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

    manager.protect_managed_files([cacert])
    assert not (target.stat().st_mode & stat.S_IWRITE)
    assert not (cacert.stat().st_mode & stat.S_IWRITE)

    manager._stash_and_write(target_path, b"modified")

    stash = manager._stash_dir / roblox_dir.name / "content" / "textures" / "MouseLockedCursor.png"
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


def test_fast_flags_write_to_sober_config(tmp_path, monkeypatch):
    sober_root = tmp_path / ".var" / "app" / "org.vinegarhq.Sober"
    overlay = sober_root / "data" / "sober" / "asset_overlay"
    config_path = sober_root / "config" / "sober" / "config.json"
    overlay.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    config_path.write_text('{"close_on_leave": false, "fflags": {"Old": true}}', encoding="utf-8")

    monkeypatch.setattr(fflag_manager.sys, "platform", "linux")
    monkeypatch.setattr(
        "Fleasion.utils.platform_linux.SOBER_ASSET_OVERLAY_DIR",
        overlay,
    )
    monkeypatch.setattr(
        "Fleasion.utils.platform_linux.SOBER_LEGACY_EXE_DIR",
        sober_root / "data" / "sober" / "exe",
    )
    monkeypatch.setattr(
        "Fleasion.utils.platform_linux.SOBER_CONFIG_FILE",
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

    manager.restore()

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "close_on_leave": False,
        "fflags": {"Old": True},
    }


def test_ktx_backed_targets_convert_image_replacements_to_ktx2(monkeypatch, tmp_path):
    monkeypatch.setattr(modifications_manager, "MOD_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        modifications_manager,
        "read_current_platform_original_asset",
        lambda _target: b"\xabKTX 11\xbb\r\n\x1a\n" + b"original",
    )

    image = Image.new("RGBA", (1, 1), (1, 2, 3, 4))
    buf = BytesIO()
    image.save(buf, format="PNG")

    manager = ModificationManager.__new__(ModificationManager)
    converted = manager._coerce_replacement_for_target(
        "android/textures/sky/sky512_bk.tex",
        buf.getvalue(),
    )

    assert read_rgba8_ktx2(converted) == (bytes((1, 2, 3, 4)), 1, 1)


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


def test_find_roblox_dirs_excludes_macos_studio_saved_dirs(tmp_path, monkeypatch):
    player = tmp_path / "Roblox.app" / "Contents" / "Resources"
    studio = tmp_path / "RobloxStudio.app" / "Contents" / "Resources"
    player.mkdir(parents=True)
    studio.mkdir(parents=True)
    discovery_calls = []
    persisted = []

    def fake_find_roblox_resource_dirs(include_studio: bool):
        discovery_calls.append(include_studio)
        return [player] + ([studio] if include_studio else [])

    monkeypatch.setattr(modifications_manager.sys, "platform", "darwin")
    fake_platform_macos = types.ModuleType("Fleasion.utils.platform_macos")
    fake_platform_macos.find_roblox_resource_dirs = fake_find_roblox_resource_dirs
    monkeypatch.setitem(sys.modules, "Fleasion.utils.platform_macos", fake_platform_macos)
    monkeypatch.setattr(modifications_manager, "load_saved_roblox_dirs", lambda: [studio])
    monkeypatch.setattr(modifications_manager, "save_saved_roblox_dirs", lambda dirs: persisted.extend(dirs))

    assert modifications_manager._find_roblox_dirs() == [player]
    assert discovery_calls == [False]
    assert persisted == [player]
