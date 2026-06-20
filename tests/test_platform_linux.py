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


def test_launch_as_standard_user_opens_http_url(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux, "_standard_user_popen", lambda args: calls.append(args))

    assert platform_linux.launch_as_standard_user("https://www.roblox.com/login")
    assert calls == [["xdg-open", "https://www.roblox.com/login"]]


def test_delete_cache_clears_texpack_slots_but_preserves_predownloaded(tmp_path, monkeypatch):
    app_cache = tmp_path / "cache"
    predownloaded = app_cache / "predownloaded"
    texpack_slots = app_cache / "texpack_slots"
    converted_cache = app_cache / "converted"
    for path in (predownloaded, texpack_slots, converted_cache):
        path.mkdir(parents=True)
    (predownloaded / "asset.bin").write_bytes(b"keep")
    (texpack_slots / "88088208586015_slot0.ktx2").write_bytes(b"delete")
    (converted_cache / "mesh.obj").write_text("delete", encoding="utf-8")

    monkeypatch.setattr(platform_linux, "APP_CACHE_DIR", app_cache)
    monkeypatch.setattr(platform_linux, "STORAGE_DB", tmp_path / "rbx-storage.db")
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)

    platform_linux.delete_cache()

    assert predownloaded.exists()
    assert (predownloaded / "asset.bin").exists()
    assert not texpack_slots.exists()
    assert not converted_cache.exists()


def test_delete_cache_terminates_sober_before_cleanup(tmp_path, monkeypatch):
    app_cache = tmp_path / "cache"
    predownloaded = app_cache / "predownloaded"
    converted_cache = app_cache / "converted"
    predownloaded.mkdir(parents=True)
    converted_cache.mkdir(parents=True)
    (predownloaded / "asset.bin").write_bytes(b"keep")
    (converted_cache / "mesh.obj").write_text("delete", encoding="utf-8")

    storage_db = tmp_path / "rbx-storage.db"
    storage_db.write_bytes(b"cache")
    storage_folder = tmp_path / "rbx-storage"
    storage_folder.mkdir()
    (storage_folder / "db.dat").write_bytes(b"cache")

    calls = []

    monkeypatch.setattr(platform_linux, "APP_CACHE_DIR", app_cache)
    monkeypatch.setattr(platform_linux, "STORAGE_DB", storage_db)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: True)
    monkeypatch.setattr(platform_linux, "terminate_roblox", lambda: calls.append("terminate") or True)
    monkeypatch.setattr(platform_linux, "wait_for_roblox_exit", lambda timeout=10.0: True)

    messages = platform_linux.delete_cache()

    assert calls == ["terminate"]
    assert messages[:2] == [
        "Sober is running, terminating...",
        "Sober terminated successfully",
    ]
    assert not storage_db.exists()
    assert not storage_folder.exists()
    assert predownloaded.exists()
    assert (predownloaded / "asset.bin").exists()
    assert not converted_cache.exists()
