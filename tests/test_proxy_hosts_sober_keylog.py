"""Sober Hosts File startup must intercept its first ClientSettings response."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fleasion.proxy import master as proxy_master
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import SOBER_CLIENT


def _new_sober_owner() -> proxy_master.ProxyMaster:
    owner = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(owner, '_active_linux_client_key', 'sober')
    setattr(owner, '_active_linux_client_installation', SimpleNamespace(
        key='sober', client=SOBER_CLIENT
    ))
    setattr(owner, '_sober_native_keylog_armed', False)
    setattr(owner, 'config_manager', SimpleNamespace(settings={}))
    setattr(owner, 'custom_fflag_modifier', SimpleNamespace(is_enabled=lambda: True))
    return owner


def _linux_splice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    def splice_transport(_owner: object) -> bool:
        return True

    monkeypatch.setattr(proxy_master.ProxyMaster, '_use_splice_transport', splice_transport)
    monkeypatch.delenv('FLEASION_SPLICE_KEYLOG', raising=False)


def test_hosts_native_keylog_arms_first_clientsettings_intercept_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linux_splice(monkeypatch)
    owner = _new_sober_owner()
    calls: list[str] = []
    def not_running(_installation: object) -> None:
        return None

    monkeypatch.setattr(platform_linux, 'linux_client_main_process', not_running)
    monkeypatch.setattr(
        platform_linux, 'arm_sober_native_keylog_override',
        lambda: calls.append('arm') or True,
    )
    monkeypatch.setattr(
        platform_linux, 'clear_sober_native_keylog_override',
        lambda: calls.append('clear') or True,
    )

    assert not getattr(owner, '_linux_sober_custom_fflag_routes_ready')()
    assert getattr(owner, '_arm_linux_hosts_sober_native_keylog')()
    assert getattr(owner, '_linux_sober_custom_fflag_routes_ready')()
    assert calls == ['arm']
    assert getattr(owner, '_desired_intercept_hosts')() >= set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS)
    assert getattr(owner, '_clear_linux_hosts_sober_native_keylog')()
    assert calls == ['arm', 'clear']
    assert not getattr(owner, '_sober_native_keylog_armed')


def test_hosts_native_keylog_never_claims_existing_sober_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linux_splice(monkeypatch)
    owner = _new_sober_owner()
    def running(_installation: object) -> tuple[int, float]:
        return 1234, 42.0

    monkeypatch.setattr(platform_linux, 'linux_client_main_process', running)

    def unexpected() -> bool:
        raise AssertionError('must not alter keylog for a running Sober session')

    monkeypatch.setattr(platform_linux, 'arm_sober_native_keylog_override', unexpected)
    assert not getattr(owner, '_arm_linux_hosts_sober_native_keylog')()
    assert not getattr(owner, '_sober_native_keylog_armed')


def test_hosts_native_keylog_failed_arm_keeps_delayed_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linux_splice(monkeypatch)
    owner = _new_sober_owner()
    def not_running(_installation: object) -> None:
        return None

    monkeypatch.setattr(platform_linux, 'linux_client_main_process', not_running)
    monkeypatch.setattr(platform_linux, 'arm_sober_native_keylog_override', lambda: False)
    assert not getattr(owner, '_arm_linux_hosts_sober_native_keylog')()
    assert not getattr(owner, '_sober_native_keylog_armed')
    assert not getattr(owner, '_linux_sober_custom_fflag_routes_ready')()


def test_hosts_native_keylog_never_changes_non_sober_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _linux_splice(monkeypatch)
    owner = _new_sober_owner()
    setattr(owner, '_active_linux_client_key', 'mocktail')
    assert not getattr(owner, '_arm_linux_hosts_sober_native_keylog')()
    assert not getattr(owner, '_hosts_sober_native_keylog_active', False)

@pytest.mark.parametrize('env_mode', [False, True])
def test_hosts_keylog_is_armed_before_initial_route_selection(
    monkeypatch: pytest.MonkeyPatch, env_mode: bool
) -> None:
    """Hosts must include ClientSettings in the first helper installation."""
    import asyncio

    _linux_splice(monkeypatch)
    owner = _new_sober_owner()
    sequence: list[str] = []
    routed_hosts: list[set[str]] = []

    def env_mode_selection(_owner: object) -> bool:
        return env_mode

    def arm(_owner: object) -> bool:
        sequence.append('arm')
        setattr(owner, '_sober_native_keylog_armed', True)
        return True

    def diagnostics(_owner: object, _loop: object, _env: bool) -> None:
        return None

    def prepare(_owner: object, _env: bool) -> dict[str, object]:
        sequence.append('prepare')
        return {}

    async def create(
        _owner: object, _env: bool, _startup: object
    ) -> dict[str, object]:
        sequence.append('create')
        routed_hosts.append(getattr(owner, '_desired_intercept_hosts')())
        return {'active_hosts': routed_hosts[-1]}

    async def verify(
        _owner: object, _env: bool, _startup: object, _server: object
    ) -> bool:
        sequence.append('verify')
        return False

    monkeypatch.setattr(proxy_master.ProxyMaster, '_use_env_proxy_mode', env_mode_selection)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_arm_linux_hosts_sober_native_keylog', arm)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_install_proxy_loop_diagnostics', diagnostics)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_prepare_proxy_startup', prepare)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_create_and_start_proxy_server', create)
    monkeypatch.setattr(proxy_master.ProxyMaster, '_verify_proxy_startup_tls', verify)

    asyncio.run(getattr(owner, '_run_proxy')())
    assert sequence == (
        ['prepare', 'create', 'verify']
        if env_mode else ['prepare', 'arm', 'create', 'verify']
    )
    if not env_mode:
        assert routed_hosts[0] >= set(proxy_master.CUSTOM_FFLAGS_INTERCEPT_HOSTS)
