import json
import threading

import pytest

from Fleasion.modifications.fflag_manager import FastFlagManager
from Fleasion.modifications.manager import ModificationManager, normalise_target_path


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


def test_fast_flags_write_to_clientsettings_under_resource_root(tmp_path):
    roblox_dir = tmp_path / "Roblox.app" / "Contents" / "Resources"
    manager = FastFlagManager([roblox_dir], tmp_path / "stash")

    manager.write({"grey_sky": True})

    settings_path = roblox_dir / "ClientSettings" / "ClientAppSettings.json"
    assert json.loads(settings_path.read_text(encoding="utf-8")) == {
        "FFlagDebugSkyGray": "True",
    }
