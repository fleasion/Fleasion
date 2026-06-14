import json
import threading

import pytest

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
