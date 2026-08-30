import sys
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Literal, cast
from unittest.mock import patch

import pytest

from fleasion.proxy.env_lifecycle import EnvProxyLifecycleController


type HealthResult = dict[str, object]
type PlayerIdentity = tuple[int, str] | tuple[str, int] | tuple[str, int, float]
type RelaunchCall = tuple[str, str | None, bool, Path, bool]
type LifecycleCall = RelaunchCall | tuple[Literal['terminate']]


def _relaunch_calls(calls: list[LifecycleCall]) -> list[RelaunchCall]:
    return [call for call in calls if len(call) == 5]


def _mark_intentional(controller: EnvProxyLifecycleController) -> None:
    callback = cast(
        'Callable[[EnvProxyLifecycleController], None]',
        EnvProxyLifecycleController.__dict__['_mark_intentional_relaunch'],
    )
    callback(controller)


def _finish_intentional(controller: EnvProxyLifecycleController) -> None:
    callback = cast(
        'Callable[[EnvProxyLifecycleController], None]',
        EnvProxyLifecycleController.__dict__['_finish_intentional_relaunch'],
    )
    callback(controller)


def _is_gdk_path(_path: Path) -> bool:
    return True


class _ProxyStub:
    def __init__(self, health_results: Iterable[HealthResult]) -> None:
        self.health_results: list[HealthResult] = list(health_results)
        self.prepares: list[tuple[Path, bool]] = []
        self.monitors = 0
        self.fflag_launch_preparations = 0

    def wait_for_env_proxy_ready(self, timeout: float = 15.0) -> bool:
        return True

    def roblox_env_proxy_url(self) -> str:
        return 'http://127.0.0.1:58443'

    def prepare_custom_fflags_for_player_launch(self) -> None:
        self.fflag_launch_preparations += 1

    def ensure_env_proxy_roblox_ca(self, exe_path: Path, *, settle: bool = False) -> HealthResult:
        self.prepares.append((Path(exe_path), settle))
        return {'success': True, 'healthy': True, 'path': 'cacert.pem'}

    def monitor_env_proxy_roblox_ca(
        self, exe_path: Path, cancel_event: threading.Event
    ) -> HealthResult:
        del exe_path, cancel_event
        self.monitors += 1
        return self.health_results.pop(0)


def _controller(
    health_results: Iterable[HealthResult], *, adopted: bool = False
) -> tuple[
    EnvProxyLifecycleController,
    _ProxyStub,
    list[LifecycleCall],
    dict[str, bool],
    dict[str, PlayerIdentity | None],
]:
    proxy = _ProxyStub(health_results)
    calls: list[LifecycleCall] = []
    running = {'value': True}
    identity: dict[str, PlayerIdentity | None] = {'value': (100, 'player')}

    def relaunch(
        url: str,
        target: str | None,
        force: bool,
        _cancel_event: threading.Event,
        source_exe_path: Path | None,
        already_stopped: bool,
    ) -> bool:
        assert source_exe_path is not None
        calls.append((url, target, force, Path(source_exe_path), already_stopped))
        running['value'] = True
        return True

    def terminate() -> bool:
        calls.append(('terminate',))
        running['value'] = False
        identity['value'] = None
        return True

    def resolve_exe() -> Path:
        return Path('/Roblox/RobloxPlayerBeta.exe')

    def is_running() -> bool:
        return running['value']

    def get_identity() -> PlayerIdentity | None:
        return identity['value'] if running['value'] else None

    def wait_exit(_timeout: float) -> bool:
        return not running['value']

    factory = cast('Callable[..., EnvProxyLifecycleController]', EnvProxyLifecycleController)
    controller = factory(
        config_manager=SimpleNamespace(proxy_features_enabled=True, proxy_mode='env'),
        proxy_master=proxy,
        resolve_player_exe=resolve_exe,
        relaunch_player=relaunch,
        is_player_running=is_running,
        get_player_identity=get_identity,
        terminate_player=terminate,
        wait_for_player_exit=wait_exit,
        adopted_player=adopted,
        max_repairs=2,
    )
    return controller, proxy, calls, running, identity


def test_env_lifecycle_converts_once_when_ca_stays_healthy() -> None:
    controller, proxy, calls, _running, _identity = _controller([{'success': True}])

    assert controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert calls == [
        (
            'http://127.0.0.1:58443',
            None,
            False,
            Path('/Roblox/RobloxPlayerBeta.exe'),
            False,
        )
    ]
    assert proxy.monitors == 1
    assert proxy.fflag_launch_preparations == 1
    assert controller.owns_player


def test_windows_env_lifecycle_does_not_settle_before_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('fleasion.proxy.env_lifecycle.sys.platform', 'win32')
    controller, proxy, _calls, _running, _identity = _controller([{'success': True}])

    assert controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert proxy.prepares == [(Path('/Roblox/RobloxPlayerBeta.exe'), False)]


def test_intercepted_launch_uses_source_bundle_without_waiting_for_running_player() -> None:
    controller, proxy, calls, running, _identity = _controller([{'success': True}])
    running['value'] = False
    source = Path('/Roblox/Custom/RobloxPlayer')
    target = 'roblox-player:1+launchmode:play+gameinfo:test'

    assert controller.handle_intercepted_player_launch(source, target)

    assert proxy.prepares == [(source, False)]
    assert calls == [
        (
            'http://127.0.0.1:58443',
            target,
            False,
            source,
            True,
        )
    ]


def test_env_lifecycle_allows_exactly_two_ca_repair_relaunches() -> None:
    controller, proxy, calls, _running, _identity = _controller(
        [
            {'success': False, 'path': 'cacert.pem'},
            {'success': False, 'path': 'cacert.pem'},
            {'success': True},
        ]
    )

    assert controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert [call[2] for call in _relaunch_calls(calls)] == [False, True, True]
    assert proxy.monitors == 3
    assert len(proxy.prepares) == 3
    assert proxy.fflag_launch_preparations == 3


def test_lifecycle_drops_one_time_target_before_ca_repair_relaunch() -> None:
    controller, _proxy, calls, _running, _identity = _controller(
        [{'success': False, 'path': 'cacert.pem'}, {'success': True}]
    )
    target = 'roblox-player:1+launchmode:play+gameinfo:test'

    assert controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'), target)

    assert [call[1] for call in _relaunch_calls(calls)] == [target, None]


def test_env_lifecycle_never_attempts_a_third_ca_repair() -> None:
    controller, proxy, calls, running, _identity = _controller(
        [
            {'success': False, 'path': 'cacert.pem'},
            {'success': False, 'path': 'cacert.pem'},
            {'success': False, 'path': 'cacert.pem'},
        ]
    )

    assert not controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert [call[2] for call in _relaunch_calls(calls)] == [False, True, True]
    assert calls[-1] == ('terminate',)
    assert proxy.monitors == 3
    assert not running['value']
    assert not controller.owns_player


def test_env_lifecycle_adopts_package_player_without_synthetic_relaunch() -> None:
    controller, proxy, calls, _running, _identity = _controller(
        [{'success': False, 'path': 'cacert.pem'}, {'success': True}]
    )

    assert controller.handle_adopted_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert calls == []
    assert proxy.monitors == 2
    assert proxy.prepares == [
        (Path('/Roblox/RobloxPlayerBeta.exe'), sys.platform != 'win32'),
        (Path('/Roblox/RobloxPlayerBeta.exe'), False),
    ]
    assert controller.owns_player


def test_env_lifecycle_relaunches_gdk_player_after_ca_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'fleasion.proxy.env_lifecycle._is_gdk_repair_path',
        _is_gdk_path,
    )
    controller, proxy, calls, _running, _identity = _controller(
        [
            {'success': False, 'path': 'cacert.pem'},
            {'success': False, 'path': 'cacert.pem'},
            {'success': True},
        ]
    )

    assert controller.handle_adopted_player_launch(
        Path(r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe')
    )
    assert [call[2] for call in _relaunch_calls(calls)] == [True, True]
    assert proxy.monitors == 3


def test_cancelled_env_lifecycle_cannot_relaunch_player() -> None:
    controller, _proxy, calls, _running, _identity = _controller([{'success': True}])
    controller.cancel()

    assert not controller.handle_player_launch(Path('/Roblox/RobloxPlayerBeta.exe'))
    assert calls == []


def test_exit_closes_adopted_player_but_restart_preserves_it() -> None:
    controller, _proxy, calls, running, _identity = _controller([], adopted=True)
    assert controller.owns_player
    assert controller.preserve_owned_player_for_restart()
    assert running['value']
    assert calls == []

    controller, _proxy, calls, running, _identity = _controller([], adopted=True)
    assert controller.close_owned_player_for_exit()
    assert calls == [('terminate',)]
    assert not running['value']


def test_exit_does_not_terminate_a_replacement_player_it_does_not_own() -> None:
    controller, _proxy, calls, running, identity = _controller([], adopted=True)
    identity['value'] = (200, 'player')

    assert controller.close_owned_player_for_exit()
    assert running['value']
    assert calls == []
    assert not controller.owns_player


def test_intentional_exit_uses_sample_time_not_delayed_monitor_processing() -> None:
    controller, _proxy, _calls, _running, _identity = _controller([], adopted=True)

    with patch('fleasion.proxy.env_lifecycle.time.monotonic', side_effect=[10.0, 12.0]):
        _mark_intentional(controller)
        _finish_intentional(controller)

    assert controller.consume_intentional_player_exit(11.0)
    assert not controller.consume_intentional_player_exit(20.0)
