from pathlib import Path

from Fleasion.utils import platform_linux
from Fleasion.utils.roblox_dirs import _normalise_roblox_dir


def test_find_sober_resource_dirs_prefers_asset_overlay(tmp_path, monkeypatch):
    sober_root = tmp_path / ".var" / "app" / "org.vinegarhq.Sober"
    data_dir = sober_root / "data" / "sober"
    overlay = data_dir / "asset_overlay"
    legacy = data_dir / "exe"
    legacy.mkdir(parents=True)

    monkeypatch.setattr(platform_linux, "SOBER_DATA_DIR", data_dir)
    monkeypatch.setattr(platform_linux, "SOBER_ASSET_OVERLAY_DIR", overlay)
    monkeypatch.setattr(platform_linux, "SOBER_LEGACY_EXE_DIR", legacy)

    assert platform_linux.find_roblox_resource_dirs() == [overlay, legacy]


def test_normalise_linux_sober_resource_dir(tmp_path, monkeypatch):
    overlay = tmp_path / "asset_overlay"
    overlay.mkdir()

    monkeypatch.setattr("Fleasion.utils.roblox_dirs.sys.platform", "linux")
    monkeypatch.setattr(platform_linux, "SOBER_ASSET_OVERLAY_DIR", overlay)
    monkeypatch.setattr(platform_linux, "SOBER_LEGACY_EXE_DIR", tmp_path / "exe")

    assert _normalise_roblox_dir(overlay) == overlay
