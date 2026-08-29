from __future__ import annotations

from types import SimpleNamespace

from fleasion.qml_api import roblox_target_launcher


def test_target_launcher_preserves_one_time_uri_on_linux(monkeypatch) -> None:
    calls: list[str] = []
    proxy = SimpleNamespace(
        config_manager=SimpleNamespace(proxy_mode='env', proxy_features_enabled=True),
        roblox_env_proxy_url=lambda: 'http://127.0.0.1:8123',
    )
    monkeypatch.setattr(roblox_target_launcher.sys, 'platform', 'linux')
    monkeypatch.setattr(
        roblox_target_launcher,
        'launch_as_standard_user',
        lambda target: calls.append(target) or True,
    )

    assert roblox_target_launcher.launch_roblox_target(proxy, 'roblox-player:ticket')

    assert calls == ['roblox-player:ticket']


def test_target_launcher_uses_standard_handler_without_environment_proxy(monkeypatch) -> None:
    calls: list[str] = []
    proxy = SimpleNamespace(
        config_manager=SimpleNamespace(proxy_mode='direct', proxy_features_enabled=True),
    )
    monkeypatch.setattr(
        roblox_target_launcher,
        'launch_as_standard_user',
        lambda target: calls.append(target) or True,
    )

    assert roblox_target_launcher.launch_roblox_target(proxy, 'roblox:place')

    assert calls == ['roblox:place']
