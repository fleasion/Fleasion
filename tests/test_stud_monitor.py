"""Externally launched Stud sessions enter Fleasion's regular Env lifecycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Callable, cast

from fleasion.app import roblox_launch, roblox_monitor
from fleasion.config.manager import ConfigManager
from fleasion.proxy.env_lifecycle import EnvProxyLifecycleController
from fleasion.proxy.master import ProxyMaster
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import ROSEMARY_CLIENT, LinuxClientInstallation

if TYPE_CHECKING:
    import pytest

PROXY = 'http://127.0.0.1:58443'


def _stud(home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=home),
        None, home / '.local/share/stud/client/version-abc123/RobloxPlayer.app',
        stud_managed=True, stud_command=(str(home / 'bin/stud'),),
    )


def _immediately[**P, T](method: Callable[P, T]) -> Callable[P, T]:
    return method


def test_external_stud_launch_is_restarted_when_unproxied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _stud(tmp_path)
    player_exe = installation.app_bundle / 'Contents/MacOS/RobloxPlayer' if installation.app_bundle else None
    calls: list[tuple[str, Path | None]] = []

    def relaunch(path: Path | None) -> bool:
        calls.append(('relaunch', path))
        return True

    def adopt(path: Path | None) -> bool:
        calls.append(('adopt', path))
        return True

    config = cast('ConfigManager', SimpleNamespace(proxy_mode='env', proxy_features_enabled=True))
    monitor = roblox_monitor.RobloxExitMonitor(config, proxy_master=None)
    monitor._proxy_master = cast(  # pyright: ignore[reportPrivateUsage]
        'ProxyMaster', SimpleNamespace(roblox_env_proxy_url=lambda: PROXY)
    )
    monitor.env_lifecycle = cast(
        'EnvProxyLifecycleController',
        SimpleNamespace(handle_player_launch=relaunch, handle_adopted_player_launch=adopt),
    )
    monkeypatch.setattr(roblox_monitor, 'run_in_thread', _immediately)
    monkeypatch.setattr(roblox_monitor, 'selected_linux_client_app_id', lambda: 'rosemary')
    monkeypatch.setattr(roblox_monitor, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(roblox_monitor, 'get_roblox_player_exe_path', lambda: player_exe)
    def missing_proxy(_url: str) -> str:
        return 'missing'

    monkeypatch.setattr(platform_linux, 'running_rosemary_proxy_status', missing_proxy)
    monitor._handle_player_launch_detected()  # pyright: ignore[reportPrivateUsage]
    assert calls == [('relaunch', player_exe)]
    monitor._handle_player_launch_detected()  # pyright: ignore[reportPrivateUsage]
    assert calls == [('relaunch', player_exe)]  # Never enter a restart loop
    assert monitor._suppress_next_player_exit_cache_delete  # pyright: ignore[reportPrivateUsage]

    calls.clear()
    def confirmed_proxy(_url: str) -> str:
        return 'configured'

    monkeypatch.setattr(platform_linux, 'running_rosemary_proxy_status', confirmed_proxy)
    monitor._handle_player_launch_detected()  # pyright: ignore[reportPrivateUsage]
    assert calls == [('adopt', player_exe)]
    monitor.stop()


def test_unproxied_existing_stud_receives_fleasion_uri_after_relaunch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _stud(tmp_path)
    target = 'roblox-player:synthetic-ticket'
    calls: list[tuple[Path | None, str | None]] = []

    def relaunch(path: Path | None, uri: str | None) -> bool:
        calls.append((path, uri))
        return True

    config = cast('ConfigManager', SimpleNamespace(proxy_mode='env', proxy_features_enabled=True))
    lifecycle = cast('EnvProxyLifecycleController', SimpleNamespace(handle_player_launch=relaunch))
    tray = cast('roblox_launch.SystemTray', SimpleNamespace(
        config_manager=config,
        roblox_monitor=SimpleNamespace(env_lifecycle=lifecycle),
        proxy_master=SimpleNamespace(roblox_env_proxy_url=lambda: PROXY),
    ))
    executable = installation.app_bundle / 'Contents/MacOS/RobloxPlayer' if installation.app_bundle else None
    monkeypatch.setattr(roblox_launch, 'get_roblox_player_exe_path', lambda: executable)
    monkeypatch.setattr(roblox_launch, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    def missing_proxy(_url: str) -> str:
        return 'missing'

    monkeypatch.setattr(platform_linux, 'running_rosemary_proxy_status', missing_proxy)
    def refuse_direct_launch(_target: str) -> bool:
        return False

    monkeypatch.setattr(roblox_launch, 'launch_as_standard_user', refuse_direct_launch)
    assert roblox_launch.launch_roblox_uri_for_instance(tray, target)
    assert calls == [(executable, target)]


def test_auto_switch_to_externally_launched_stud_relaunches_with_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _stud(tmp_path)
    events: list[str] = []
    player_exe = installation.app_bundle / 'Contents/MacOS/RobloxPlayer' if installation.app_bundle else None

    class Proxy:
        is_running = True
        _active_linux_client_key = 'sober'

        def wait_for_env_proxy_ready(self, timeout: float = 0.0) -> bool:
            del timeout
            return True

        def stop(self) -> None:
            events.append('stop')
            self.is_running = False

        def start(self) -> None:
            events.append('start')
            self.is_running = True
            self._active_linux_client_key = 'rosemary'

        def roblox_env_proxy_url(self) -> str:
            return PROXY

    class Lifecycle:
        operation_in_progress = False

        def note_unexpected_player_exit(self) -> None:
            events.append('old-owner-released')

        def handle_player_launch(self, path: Path | None) -> bool:
            assert path == player_exe
            events.append('relaunch')
            return True

    config = cast('ConfigManager', SimpleNamespace(
        proxy_mode='env', proxy_features_enabled=True, linux_client='auto',
    ))
    monitor = roblox_monitor.RobloxExitMonitor(config, proxy_master=None)
    monitor._proxy_master = cast('ProxyMaster', Proxy())  # pyright: ignore[reportPrivateUsage]
    monitor.env_lifecycle = cast('EnvProxyLifecycleController', Lifecycle())

    def missing(_url: str) -> str:
        return 'missing'

    def running(_installation: LinuxClientInstallation) -> bool:
        return True

    monkeypatch.setattr(roblox_monitor, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', running)
    monkeypatch.setattr(roblox_monitor, 'get_roblox_player_exe_path', lambda: player_exe)
    monkeypatch.setattr(roblox_monitor, 'run_in_thread', _immediately)
    monkeypatch.setattr(platform_linux, 'running_rosemary_proxy_status', missing)
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == ['old-owner-released', 'stop', 'start', 'relaunch']
    assert monitor._suppress_next_player_exit_cache_delete  # pyright: ignore[reportPrivateUsage]
    monitor.stop()
