import zipfile

from Fleasion.modifications import platform_targets
from Fleasion.modifications.manager import ModificationManager
from Fleasion.utils import platform_linux


def test_linux_sober_target_path_maps_pc_sky_to_android(monkeypatch):
    monkeypatch.setattr(platform_targets.sys, "platform", "linux")

    assert platform_targets.target_path_for_current_platform(
        r"PlatformContent\pc\textures\sky\sky512_bk.tex"
    ) == "android/textures/sky/sky512_bk.tex"


def test_non_linux_target_path_keeps_existing_storage_form(monkeypatch):
    monkeypatch.setattr(platform_targets.sys, "platform", "win32")

    assert platform_targets.target_path_for_current_platform(
        r"PlatformContent\pc\textures\sky\sky512_bk.tex"
    ) == r"PlatformContent\pc\textures\sky\sky512_bk.tex"


def test_read_linux_sober_original_asset_from_apk(tmp_path, monkeypatch):
    sober_data = tmp_path / "sober"
    apk = sober_data / "packages" / "x86_64" / "com.roblox.client" / "base.apk"
    apk.parent.mkdir(parents=True)
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("assets/android/textures/sky/sky512_bk.tex", b"sky")

    monkeypatch.setattr(platform_targets.sys, "platform", "linux")
    monkeypatch.setattr(platform_linux, "SOBER_DATA_DIR", sober_data)
    monkeypatch.setattr(platform_linux, "SOBER_LEGACY_EXE_DIR", sober_data / "exe")

    assert platform_targets.read_current_platform_original_asset(
        r"PlatformContent\pc\textures\sky\sky512_bk.tex"
    ) == b"sky"


def test_modification_manager_migrates_saved_sober_builtin_targets(monkeypatch):
    monkeypatch.setattr(platform_targets.sys, "platform", "linux")
    manager = ModificationManager.__new__(ModificationManager)
    manager._data = {
        "entries": [
            {
                "target_path": r"PlatformContent\pc\textures\sky\sky512_bk.tex",
                "source_type": "local_file",
            }
        ]
    }

    assert manager._migrate_target_paths_for_current_platform() is True
    assert manager._data["entries"][0]["target_path"] == "android/textures/sky/sky512_bk.tex"
