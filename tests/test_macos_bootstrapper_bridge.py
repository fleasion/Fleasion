import json
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from fleasion.modifications.macos_bootstrapper_bridge import MacBootstrapperBridge


class _ManagerStub:
    def __init__(self, resource_dir: Path, managed_file: Path):
        self.roblox_dirs = [resource_dir]
        self._managed_file = managed_file
        self.reassert_calls = 0

    def reassert_macos_bootstrapper_fast_flags(self) -> int:
        self.reassert_calls += 1
        target = (
            self.roblox_dirs[0].parent
            / "MacOS"
            / "ClientSettings"
            / "ClientAppSettings.json"
        )
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["FFlagDebugSkyGray"] = "True"
        target.write_text(json.dumps(payload), encoding="utf-8")
        return 1

    def managed_resource_paths(self) -> list[Path]:
        return [self._managed_file]

    def refresh_roblox_dirs(self, *, reapply_if_changed: bool = False) -> bool:
        return False

    def reapply_all(self) -> None:
        self._managed_file.write_bytes(b"fleasion")


def test_appleblox_launch_rewrite_starts_resource_guard(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    resources = tmp_path / "Roblox.app" / "Contents" / "Resources"
    macos = resources.parent / "MacOS"
    managed_file = resources / "content" / "textures" / "cursor.png"
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b"fleasion")
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    bridge = MacBootstrapperBridge(manager, app)
    bridge._settings_timer.stop()
    bridge._topology_timer.stop()
    bridge._settings_signatures = {
        macos / "ClientSettings" / "ClientAppSettings.json": None
    }
    reapply_requests = []
    monkeypatch.setattr(bridge, "_trigger_managed_reapply", lambda: reapply_requests.append(True))
    monkeypatch.setattr(
        "fleasion.modifications.macos_bootstrapper_bridge.is_roblox_running",
        lambda: False,
    )

    settings = macos / "ClientSettings" / "ClientAppSettings.json"
    settings.parent.mkdir()
    settings.write_text('{"DFFlagDisableDPIScale": true}', encoding="utf-8")
    bridge._reconcile_launch_settings()

    assert manager.reassert_calls == 1
    assert json.loads(settings.read_text(encoding="utf-8")) == {
        "DFFlagDisableDPIScale": True,
        "FFlagDebugSkyGray": "True",
    }
    assert bridge._launch_guard_timer.isActive()
    assert bridge._managed_reapply_passes == 2
    assert reapply_requests == [True]
    bridge.stop()


def test_launch_guard_reapplies_changed_managed_resource(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    resources = tmp_path / "Roblox.app" / "Contents" / "Resources"
    macos = resources.parent / "MacOS"
    managed_file = resources / "content" / "textures" / "cursor.png"
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b"fleasion")
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    bridge = MacBootstrapperBridge(manager, app)
    bridge._settings_timer.stop()
    bridge._topology_timer.stop()
    bridge._launch_guard_deadline = float("inf")
    bridge._managed_signatures = bridge._path_signatures([managed_file])
    bridge._managed_reapply_passes = 0
    reapply_requests = []
    monkeypatch.setattr(bridge, "_trigger_managed_reapply", lambda: reapply_requests.append(True))
    monkeypatch.setattr(
        "fleasion.modifications.macos_bootstrapper_bridge.is_roblox_running",
        lambda: False,
    )

    managed_file.write_bytes(b"appleblox")
    bridge._reconcile_managed_files()

    assert bridge._managed_reapply_passes == 2
    assert reapply_requests == [True]
    bridge.stop()


def test_resource_guard_reseeds_custom_flags_after_reapply(tmp_path):
    app = QApplication.instance() or QApplication([])
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = resources.parent / 'MacOS'
    managed_file = resources / 'content' / 'textures' / 'cursor.png'
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b'fleasion')
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    seed_calls = []
    bridge = MacBootstrapperBridge(
        manager,
        app,
        custom_fflag_seed=lambda: seed_calls.append(True),
    )
    bridge._settings_timer.stop()
    bridge._topology_timer.stop()

    thread = bridge._trigger_managed_reapply()
    thread.join(timeout=2)

    assert seed_calls == [True]
    bridge.stop()
