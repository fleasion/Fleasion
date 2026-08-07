from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fleasion.proxy.env_lifecycle import EnvProxyLifecycleController


class _ProxyStub:
    def __init__(self, health_results):
        self.health_results = list(health_results)
        self.prepares = []
        self.monitors = 0
        self.fflag_launch_preparations = 0

    def wait_for_env_proxy_ready(self, timeout=15.0):
        return True

    def roblox_env_proxy_url(self):
        return "http://127.0.0.1:58443"

    def prepare_custom_fflags_for_player_launch(self):
        self.fflag_launch_preparations += 1

    def ensure_env_proxy_roblox_ca(self, exe_path, *, settle=False):
        self.prepares.append((Path(exe_path), settle))
        return {"success": True, "healthy": True, "path": "cacert.pem"}

    def monitor_env_proxy_roblox_ca(self, exe_path, cancel_event):
        self.monitors += 1
        return self.health_results.pop(0)


def _controller(health_results, *, adopted=False):
    proxy = _ProxyStub(health_results)
    calls = []
    running = {"value": True}
    identity = {"value": (100, "player")}

    def relaunch(url, target, force, _cancel_event, source_exe_path, already_stopped):
        calls.append((url, target, force, Path(source_exe_path), already_stopped))
        running["value"] = True
        return True

    def terminate():
        calls.append(("terminate",))
        running["value"] = False
        identity["value"] = None
        return True

    controller = EnvProxyLifecycleController(
        config_manager=SimpleNamespace(proxy_features_enabled=True, proxy_mode="env"),
        proxy_master=proxy,
        resolve_player_exe=lambda: Path("/Roblox/RobloxPlayerBeta.exe"),
        relaunch_player=relaunch,
        is_player_running=lambda: running["value"],
        get_player_identity=lambda: identity["value"] if running["value"] else None,
        terminate_player=terminate,
        wait_for_player_exit=lambda _timeout: not running["value"],
        adopted_player=adopted,
        max_repairs=2,
    )
    return controller, proxy, calls, running, identity


def test_env_lifecycle_converts_once_when_ca_stays_healthy():
    controller, proxy, calls, _running, _identity = _controller([{"success": True}])

    assert controller.handle_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"))
    assert calls == [
        (
            "http://127.0.0.1:58443",
            None,
            False,
            Path("/Roblox/RobloxPlayerBeta.exe"),
            False,
        )
    ]
    assert proxy.monitors == 1
    assert proxy.fflag_launch_preparations == 1
    assert controller.owns_player


def test_intercepted_launch_uses_source_bundle_without_waiting_for_running_player():
    controller, proxy, calls, running, _identity = _controller([{"success": True}])
    running["value"] = False
    source = Path("/Roblox/Custom/RobloxPlayer")
    target = "roblox-player:1+launchmode:play+gameinfo:test"

    assert controller.handle_intercepted_player_launch(source, target)

    assert proxy.prepares == [(source, False)]
    assert calls == [
        (
            "http://127.0.0.1:58443",
            target,
            False,
            source,
            True,
        )
    ]


def test_env_lifecycle_allows_exactly_two_ca_repair_relaunches():
    controller, proxy, calls, _running, _identity = _controller(
        [
            {"success": False, "path": "cacert.pem"},
            {"success": False, "path": "cacert.pem"},
            {"success": True},
        ]
    )

    assert controller.handle_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"))
    assert [call[2] for call in calls] == [False, True, True]
    assert proxy.monitors == 3
    assert len(proxy.prepares) == 3
    assert proxy.fflag_launch_preparations == 3


def test_lifecycle_drops_one_time_target_before_ca_repair_relaunch():
    controller, _proxy, calls, _running, _identity = _controller(
        [{"success": False, "path": "cacert.pem"}, {"success": True}]
    )
    target = "roblox-player:1+launchmode:play+gameinfo:test"

    assert controller.handle_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"), target)

    assert [call[1] for call in calls] == [target, None]


def test_env_lifecycle_never_attempts_a_third_ca_repair():
    controller, proxy, calls, running, _identity = _controller(
        [
            {"success": False, "path": "cacert.pem"},
            {"success": False, "path": "cacert.pem"},
            {"success": False, "path": "cacert.pem"},
        ]
    )

    assert not controller.handle_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"))
    assert [call[2] for call in calls if len(call) == 5] == [False, True, True]
    assert calls[-1] == ("terminate",)
    assert proxy.monitors == 3
    assert not running["value"]
    assert not controller.owns_player


def test_env_lifecycle_adopts_package_player_without_synthetic_relaunch():
    controller, proxy, calls, _running, _identity = _controller(
        [{"success": False, "path": "cacert.pem"}, {"success": True}]
    )

    assert controller.handle_adopted_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"))
    assert calls == []
    assert proxy.monitors == 2
    assert proxy.prepares == [
        (Path("/Roblox/RobloxPlayerBeta.exe"), True),
        (Path("/Roblox/RobloxPlayerBeta.exe"), False),
    ]
    assert controller.owns_player


def test_env_lifecycle_relaunches_gdk_player_after_ca_repair(monkeypatch):
    monkeypatch.setattr(
        'fleasion.proxy.env_lifecycle._is_gdk_repair_path',
        lambda _path: True,
    )
    controller, proxy, calls, _running, _identity = _controller(
        [
            {"success": False, "path": "cacert.pem"},
            {"success": False, "path": "cacert.pem"},
            {"success": True},
        ]
    )

    assert controller.handle_adopted_player_launch(
        Path(r'C:\XboxGames\Roblox\Content\RobloxPlayerBeta.exe')
    )
    assert [call[2] for call in calls if len(call) == 5] == [True, True]
    assert proxy.monitors == 3


def test_cancelled_env_lifecycle_cannot_relaunch_player():
    controller, _proxy, calls, _running, _identity = _controller([{"success": True}])
    controller.cancel()

    assert not controller.handle_player_launch(Path("/Roblox/RobloxPlayerBeta.exe"))
    assert calls == []


def test_exit_closes_adopted_player_but_restart_preserves_it():
    controller, _proxy, calls, running, _identity = _controller([], adopted=True)
    assert controller.owns_player
    assert controller.preserve_owned_player_for_restart()
    assert running["value"]
    assert calls == []

    controller, _proxy, calls, running, _identity = _controller([], adopted=True)
    assert controller.close_owned_player_for_exit()
    assert calls == [("terminate",)]
    assert not running["value"]


def test_exit_does_not_terminate_a_replacement_player_it_does_not_own():
    controller, _proxy, calls, running, identity = _controller([], adopted=True)
    identity["value"] = (200, "player")

    assert controller.close_owned_player_for_exit()
    assert running["value"]
    assert calls == []
    assert not controller.owns_player


def test_intentional_exit_uses_sample_time_not_delayed_monitor_processing():
    controller, _proxy, _calls, _running, _identity = _controller([], adopted=True)

    with patch('fleasion.proxy.env_lifecycle.time.monotonic', side_effect=[10.0, 12.0]):
        controller._mark_intentional_relaunch()
        controller._finish_intentional_relaunch()

    assert controller.consume_intentional_player_exit(11.0)
    assert not controller.consume_intentional_player_exit(20.0)
