import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from fleasion.modifications.macos_bootstrapper_bridge import MacBootstrapperBridge


def _qapp() -> QApplication:
    app = QApplication.instance()
    return cast(QApplication, app) if app is not None else QApplication([])


def _make_bridge(
    manager: object,
    app: QApplication,
    *,
    custom_fflag_seed: Callable[[], object] | None = None,
) -> MacBootstrapperBridge:
    factory = cast('Callable[..., MacBootstrapperBridge]', MacBootstrapperBridge)
    return factory(manager, app, custom_fflag_seed=custom_fflag_seed)


def _timer(bridge: MacBootstrapperBridge, name: str) -> QTimer:
    return cast(QTimer, bridge.__dict__[name])


def _reconcile_launch_settings(bridge: MacBootstrapperBridge) -> None:
    callback = cast('Callable[[], None]', getattr(bridge, '_reconcile_launch_settings'))
    callback()


def _reconcile_managed_files(bridge: MacBootstrapperBridge) -> None:
    callback = cast('Callable[[], None]', getattr(bridge, '_reconcile_managed_files'))
    callback()


def _trigger_reapply(bridge: MacBootstrapperBridge) -> threading.Thread:
    callback = cast('Callable[[], threading.Thread]', getattr(bridge, '_trigger_managed_reapply'))
    return callback()


def _path_signatures(
    bridge: MacBootstrapperBridge, paths: list[Path]
) -> dict[Path, tuple[int, int] | None]:
    callback = cast(
        'Callable[[list[Path]], dict[Path, tuple[int, int] | None]]',
        getattr(bridge, '_path_signatures'),
    )
    return callback(paths)


def _directories_to_watch(bridge: MacBootstrapperBridge) -> set[str]:
    callback = cast('Callable[[], set[str]]', getattr(bridge, '_directories_to_watch'))
    return callback()


def _record_true(values: list[bool]) -> Callable[[], None]:
    def record() -> None:
        values.append(True)

    return record


def _not_running() -> bool:
    return False


class _ManagerStub:
    def __init__(self, resource_dir: Path, managed_file: Path) -> None:
        self.roblox_dirs = [resource_dir]
        self._managed_file = managed_file
        self.reassert_calls = 0

    def reassert_macos_bootstrapper_fast_flags(self) -> int:
        self.reassert_calls += 1
        target = self.roblox_dirs[0].parent / 'MacOS' / 'ClientSettings' / 'ClientAppSettings.json'
        payload = json.loads(target.read_text(encoding='utf-8'))
        payload['FFlagDebugSkyGray'] = 'True'
        target.write_text(json.dumps(payload), encoding='utf-8')
        return 1

    def managed_resource_paths(self) -> list[Path]:
        return [self._managed_file]

    def refresh_roblox_dirs(self, *, reapply_if_changed: bool = False) -> bool:
        return False

    def reapply_all(self) -> None:
        self._managed_file.write_bytes(b'fleasion')


def test_appleblox_launch_rewrite_starts_resource_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = resources.parent / 'MacOS'
    managed_file = resources / 'content' / 'textures' / 'cursor.png'
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b'fleasion')
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    bridge = _make_bridge(manager, app)
    _timer(bridge, '_settings_timer').stop()
    _timer(bridge, '_topology_timer').stop()
    bridge.__dict__['_settings_signatures'] = {
        macos / 'ClientSettings' / 'ClientAppSettings.json': None
    }
    reapply_requests: list[bool] = []
    prepare_calls: list[bool] = []
    bridge.__dict__['_custom_fflag_prepare'] = _record_true(prepare_calls)
    monkeypatch.setattr(bridge, '_trigger_managed_reapply', _record_true(reapply_requests))
    monkeypatch.setattr(
        'fleasion.modifications.macos_bootstrapper_bridge.is_roblox_running',
        _not_running,
    )

    settings = macos / 'ClientSettings' / 'ClientAppSettings.json'
    settings.parent.mkdir()
    settings.write_text('{"DFFlagDisableDPIScale": true}', encoding='utf-8')
    _reconcile_launch_settings(bridge)

    assert manager.reassert_calls == 1
    assert json.loads(settings.read_text(encoding='utf-8')) == {
        'DFFlagDisableDPIScale': True,
        'FFlagDebugSkyGray': 'True',
    }
    assert _timer(bridge, '_launch_guard_timer').isActive()
    assert cast(int, bridge.__dict__['_managed_reapply_passes']) == 2
    assert reapply_requests == [True]
    assert prepare_calls == [True]
    bridge.stop()


def test_launch_guard_reapplies_changed_managed_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = resources.parent / 'MacOS'
    managed_file = resources / 'content' / 'textures' / 'cursor.png'
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b'fleasion')
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    bridge = _make_bridge(manager, app)
    _timer(bridge, '_settings_timer').stop()
    _timer(bridge, '_topology_timer').stop()
    bridge.__dict__['_launch_guard_deadline'] = float('inf')
    bridge.__dict__['_managed_signatures'] = _path_signatures(bridge, [managed_file])
    bridge.__dict__['_managed_reapply_passes'] = 0
    reapply_requests: list[bool] = []
    monkeypatch.setattr(bridge, '_trigger_managed_reapply', _record_true(reapply_requests))
    monkeypatch.setattr(
        'fleasion.modifications.macos_bootstrapper_bridge.is_roblox_running',
        _not_running,
    )

    managed_file.write_bytes(b'appleblox')
    _reconcile_managed_files(bridge)

    assert cast(int, bridge.__dict__['_managed_reapply_passes']) == 2
    assert reapply_requests == [True]
    bridge.stop()


def test_resource_guard_reseeds_custom_flags_after_reapply(tmp_path: Path) -> None:
    app = _qapp()
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = resources.parent / 'MacOS'
    managed_file = resources / 'content' / 'textures' / 'cursor.png'
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b'fleasion')
    macos.mkdir(parents=True)
    manager = _ManagerStub(resources, managed_file)
    seed_calls: list[bool] = []
    bridge = _make_bridge(
        manager,
        app,
        custom_fflag_seed=_record_true(seed_calls),
    )
    _timer(bridge, '_settings_timer').stop()
    _timer(bridge, '_topology_timer').stop()

    thread = _trigger_reapply(bridge)
    thread.join(timeout=2)

    assert seed_calls == [True]
    bridge.stop()


def test_watches_appleblox_override_data_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    macos = resources.parent / 'MacOS'
    managed_file = resources / 'content' / 'textures' / 'cursor.png'
    managed_file.parent.mkdir(parents=True)
    managed_file.write_bytes(b'fleasion')
    macos.mkdir(parents=True)

    appleblox_root = tmp_path / 'AppleBlox Override'
    (appleblox_root / 'cache' / 'mods').mkdir(parents=True)
    monkeypatch.setattr(
        'fleasion.modifications.macos_bootstrapper_bridge.appleblox_data_dir',
        lambda: appleblox_root,
    )

    bridge = _make_bridge(_ManagerStub(resources, managed_file), app)
    _timer(bridge, '_settings_timer').stop()
    _timer(bridge, '_topology_timer').stop()

    watched = _directories_to_watch(bridge)
    assert str(appleblox_root) in watched
    assert str(appleblox_root / 'cache') in watched
    assert str(appleblox_root / 'cache' / 'mods') in watched
    bridge.stop()
