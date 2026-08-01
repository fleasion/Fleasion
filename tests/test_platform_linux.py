from pathlib import Path

from fleasion.utils import platform_linux
from fleasion.utils.roblox_dirs import _normalise_roblox_dir


def test_arch_gui_dependency_check_reports_missing_qt6_base(tmp_path, monkeypatch):
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=cachyos\nID_LIKE="arch"\n', encoding="utf-8")
    calls = []

    monkeypatch.setattr(platform_linux.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return platform_linux.subprocess.CompletedProcess(command, 1, "", "not installed")

    monkeypatch.setattr(platform_linux.subprocess, "run", run)

    assert platform_linux.missing_linux_gui_packages(
        os_release_path=os_release
    ) == ["qt6-base"]
    assert calls[0][0] == ["pacman", "-Q", "qt6-base"]


def test_arch_gui_dependency_check_accepts_installed_qt6_base(tmp_path, monkeypatch):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=arch\n", encoding="utf-8")
    monkeypatch.setattr(platform_linux.shutil, "which", lambda _name: "/usr/bin/pacman")
    monkeypatch.setattr(
        platform_linux.subprocess,
        "run",
        lambda command, **_kwargs: platform_linux.subprocess.CompletedProcess(
            command, 0, "qt6-base 6.11.1-1\n", ""
        ),
    )

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def test_non_arch_gui_dependency_check_does_not_query_pacman(tmp_path, monkeypatch):
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=fedora\n", encoding="utf-8")
    monkeypatch.setattr(
        platform_linux.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected pacman query")
        ),
    )

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def _detached_kwargs_with_env(env: dict[str, str] | None = None) -> dict:
    kwargs = dict(platform_linux._DETACHED_POPEN_KWARGS)
    kwargs["env"] = env or platform_linux._host_subprocess_env()
    return kwargs


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


def test_sober_main_process_uses_pid_and_start_time_to_identify_engine(tmp_path, monkeypatch):
    process = tmp_path / "4242"
    process.mkdir()
    (process / "cgroup").write_text(
        "0::/user.slice/app-flatpak-org.vinegarhq.Sober-test.scope\n", encoding="utf-8"
    )
    fields = ["S", *(["0"] * 18), "54321"]
    (process / "stat").write_text(f"4242 (Main) {' '.join(fields)}\n", encoding="utf-8")

    monkeypatch.setattr(platform_linux, "PROC_ROOT", tmp_path)
    monkeypatch.setattr(platform_linux, "_process_pids", lambda name: [4242] if name == "Main" else [])
    monkeypatch.setattr(platform_linux.os, "sysconf", lambda _name: 100)

    assert platform_linux.sober_main_process() == (4242, 543.21)


def test_normalise_linux_sober_resource_dir(tmp_path, monkeypatch):
    overlay = tmp_path / "asset_overlay"
    overlay.mkdir()

    monkeypatch.setattr("fleasion.utils.roblox_dirs.sys.platform", "linux")
    monkeypatch.setattr(platform_linux, "SOBER_ASSET_OVERLAY_DIR", overlay)
    monkeypatch.setattr(platform_linux, "SOBER_LEGACY_EXE_DIR", tmp_path / "exe")

    assert _normalise_roblox_dir(overlay) == overlay


def test_launch_as_standard_user_opens_http_url(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    assert platform_linux.launch_as_standard_user("https://www.roblox.com/login")
    assert calls == [
        (
            ["/usr/bin/xdg-open", "https://www.roblox.com/login"],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_opens_http_url_with_gio_fallback(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "/usr/bin/gio" if name == "gio" else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    assert platform_linux.launch_as_standard_user("https://www.roblox.com/login")
    assert calls == [
        (
            ["/usr/bin/gio", "open", "https://www.roblox.com/login"],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_returns_false_when_no_desktop_opener(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        platform_linux.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append(args),
    )

    assert not platform_linux.launch_as_standard_user("https://www.roblox.com/login")
    assert calls == []


def test_launch_as_standard_user_runs_sober_flatpak_for_roblox_uri(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    uri = "roblox-player:1+launchmode:app"
    assert platform_linux.launch_as_standard_user(uri)
    assert calls == [
        (
            ["flatpak", "run", platform_linux.SOBER_APP_ID, uri],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_strips_pyinstaller_env_for_sober_uri(monkeypatch, tmp_path):
    calls = []
    bundle_root = tmp_path / "_MEI12345"
    host_libs = tmp_path / "host-libs"

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)
    monkeypatch.setattr(platform_linux.sys, "_MEIPASS", str(bundle_root), raising=False)
    monkeypatch.setenv("LD_LIBRARY_PATH", f"{bundle_root}:{host_libs}")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    uri = "roblox-player:1+launchmode:app"
    assert platform_linux.launch_as_standard_user(uri)

    assert calls[0][0] == ["flatpak", "run", platform_linux.SOBER_APP_ID, uri]
    assert calls[0][1]["env"]["LD_LIBRARY_PATH"] == str(host_libs)
    assert "LD_LIBRARY_PATH_ORIG" not in calls[0][1]["env"]


def test_launch_as_standard_user_restarts_running_sober_before_uri(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: True)
    monkeypatch.setattr(
        platform_linux,
        "terminate_roblox",
        lambda: calls.append("terminate") or True,
    )
    monkeypatch.setattr(
        platform_linux,
        "wait_for_roblox_exit",
        lambda timeout=10.0: calls.append("wait") or True,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    uri = "roblox://experiences/start?placeId=121814103864070"
    assert platform_linux.launch_as_standard_user(uri)

    assert calls == [
        "terminate",
        "wait",
        (
            ["flatpak", "run", platform_linux.SOBER_APP_ID, uri],
            _detached_kwargs_with_env(),
        ),
    ]


def test_launch_as_standard_user_aborts_uri_when_running_sober_does_not_exit(monkeypatch):
    calls = []

    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: True)
    monkeypatch.setattr(
        platform_linux,
        "terminate_roblox",
        lambda: calls.append("terminate") or True,
    )
    monkeypatch.setattr(
        platform_linux,
        "wait_for_roblox_exit",
        lambda timeout=10.0: calls.append("wait") or False,
    )
    monkeypatch.setattr(
        platform_linux.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append(args),
    )

    assert not platform_linux.launch_as_standard_user("roblox://experiences/start?placeId=1")
    assert calls == ["terminate", "wait"]


def _reset_env_proxy_relaunch_state(monkeypatch):
    monkeypatch.setattr(platform_linux, "_env_proxy_relaunch_at", None)
    monkeypatch.setattr(platform_linux, "_env_proxy_relaunch_in_progress", False)


def test_relaunch_sober_with_env_proxy_waits_for_proxy_and_passes_flatpak_env(monkeypatch):
    calls = []
    proxy_url = "http://127.0.0.1:58443"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(
        platform_linux,
        "_wait_for_local_proxy",
        lambda url: calls.append(("wait", url)) or True,
    )
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)
    monkeypatch.setattr(
        platform_linux,
        "_standard_user_popen",
        lambda args: calls.append(("popen", args)),
    )

    assert platform_linux.relaunch_roblox_with_proxy_env(proxy_url)

    assert calls[0] == ("wait", proxy_url)
    args = calls[1][1]
    assert args[:2] == ["flatpak", "run"]
    assert f"--env=HTTPS_PROXY={proxy_url}" in args
    assert f"--env=HTTP_PROXY={proxy_url}" in args
    assert "--env=FLEASION_PROXY_RELAUNCHED=1" in args
    assert args[-1] == platform_linux.SOBER_APP_ID


def test_relaunch_sober_with_env_proxy_preserves_launch_target(monkeypatch):
    calls = []
    proxy_url = "http://127.0.0.1:58443"
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(platform_linux, "_wait_for_local_proxy", lambda _url: True)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)
    monkeypatch.setattr(
        platform_linux,
        "_standard_user_popen",
        lambda args: calls.append(args),
    )

    target = "roblox://experiences/start?placeId=121814103864070"
    assert platform_linux.relaunch_roblox_with_proxy_env(proxy_url, target)
    assert calls[0][-1] == target


def test_relaunch_sober_with_env_proxy_does_not_repeat_recent_launch(monkeypatch):
    calls = []
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_linux,
        "_env_proxy_relaunch_at",
        platform_linux.time.monotonic(),
    )
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(
        platform_linux,
        "_wait_for_local_proxy",
        lambda *_args: calls.append("wait") or True,
    )
    monkeypatch.setattr(
        platform_linux,
        "_standard_user_popen",
        lambda *_args: calls.append("popen"),
    )

    assert not platform_linux.relaunch_roblox_with_proxy_env("http://127.0.0.1:58443")
    assert calls == []


def test_explicit_sober_uri_launch_can_replace_recent_env_proxy_launch(monkeypatch):
    calls = []
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_linux,
        "_env_proxy_relaunch_at",
        platform_linux.time.monotonic(),
    )
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(platform_linux, "_wait_for_local_proxy", lambda *_args: True)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)
    monkeypatch.setattr(
        platform_linux,
        "_standard_user_popen",
        lambda args: calls.append(args),
    )

    target = "roblox://experiences/start?placeId=2"
    assert platform_linux.relaunch_roblox_with_proxy_env(
        "http://127.0.0.1:58443", target
    )
    assert calls[0][-1] == target


def test_relaunch_sober_with_env_proxy_does_not_kill_when_proxy_is_not_ready(monkeypatch):
    calls = []
    _reset_env_proxy_relaunch_state(monkeypatch)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "flatpak" if name == "flatpak" else None,
    )
    monkeypatch.setattr(platform_linux, "_wait_for_local_proxy", lambda *_args: False)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: True)
    monkeypatch.setattr(
        platform_linux,
        "terminate_roblox",
        lambda: calls.append("terminate") or True,
    )

    assert not platform_linux.relaunch_roblox_with_proxy_env("http://127.0.0.1:58443")
    assert calls == []


def test_open_folder_uses_detached_standard_user_launch(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        "which",
        lambda name: "/usr/bin/xdg-open" if name == "xdg-open" else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, "Popen", fake_popen)

    target = tmp_path / "exports"
    platform_linux.open_folder(target)

    assert target.is_dir()
    assert calls == [
        (
            ["/usr/bin/xdg-open", str(target)],
            _detached_kwargs_with_env(),
        )
    ]


def test_open_folder_returns_false_when_no_desktop_opener(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        platform_linux.subprocess,
        "Popen",
        lambda *args, **kwargs: calls.append(args),
    )

    target = tmp_path / "exports"

    assert platform_linux.open_folder(target) is False
    assert target.is_dir()
    assert calls == []


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


def test_delete_cache_clears_sober_appdata_and_cache_storage(tmp_path, monkeypatch):
    storage_db = tmp_path / "data" / "sober" / "appData" / "rbx-storage.db"
    storage_db.parent.mkdir(parents=True)
    storage_db.write_bytes(b"cache")
    Path(str(storage_db) + "-wal").write_bytes(b"wal")

    appdata_storage = storage_db.parent / "rbx-storage"
    appdata_storage.mkdir()
    (appdata_storage / "entry").write_bytes(b"cache")

    cache_storage = tmp_path / "cache" / "sober" / "rbx-storage"
    cache_storage.mkdir(parents=True)
    (cache_storage / "entry").write_bytes(b"cache")

    monkeypatch.setattr(platform_linux, "APP_CACHE_DIR", tmp_path / "fleasion-cache")
    monkeypatch.setattr(platform_linux, "STORAGE_DB", storage_db)
    monkeypatch.setattr(platform_linux, "SOBER_CACHE_STORAGE_DIR", cache_storage)
    monkeypatch.setattr(platform_linux, "is_roblox_running", lambda: False)

    messages = platform_linux.delete_cache()

    assert "Storage database deleted successfully" in messages
    assert "Storage database -wal deleted successfully" in messages
    assert "Storage folder deleted successfully" in messages
    assert "Cache storage folder deleted successfully" in messages
    assert not storage_db.exists()
    assert not Path(str(storage_db) + "-wal").exists()
    assert not appdata_storage.exists()
    assert not cache_storage.exists()


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
