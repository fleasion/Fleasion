"""Proxy trust overrides across platforms, startup, routing, and live settings."""

# ruff: file-ignore[implicit-namespace-package, private-member-access]

from __future__ import annotations

import json
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from fleasion.proxy import master as proxy_master
from fleasion.proxy.addons import custom_fflags as custom_fflags_module
from fleasion.proxy.addons.custom_fflags import (
    DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG,
    EXTERNAL_CA_FLAG,
    CustomFFlagModifier,
)
from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import SOBER_CLIENT

if TYPE_CHECKING:
    from pathlib import Path


pytestmark = pytest.mark.usefixtures('windows_compatibility')


@pytest.fixture
def windows_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(custom_fflags_module, 'IS_WINDOWS', True)


@pytest.mark.parametrize('custom_enabled', [False, True])
def test_trust_override_cannot_be_disabled_or_saved(*, custom_enabled: bool) -> None:
    saved_flags = {EXTERNAL_CA_FLAG: 'True', 'FFlagExample': 'True'}
    config = SimpleNamespace(
        custom_fflags_enabled=custom_enabled,
        custom_fflags=saved_flags.copy(),
        custom_fflag_disabled=[EXTERNAL_CA_FLAG, DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG],
        custom_fflag_folders={'Disabled': [EXTERNAL_CA_FLAG]},
        custom_fflag_disabled_folders=['Disabled'],
    )
    modifier = CustomFFlagModifier(config)

    assert modifier.is_enabled()
    assert modifier.custom_flags_enabled() is custom_enabled
    expected = {
        EXTERNAL_CA_FLAG: 'False',
        DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
    }
    if custom_enabled:
        expected['FFlagExample'] = 'True'
    assert modifier.runtime_flags() == expected
    assert config.custom_fflags == saved_flags
    assert config.custom_fflags_enabled is custom_enabled


def test_disabling_custom_flags_keeps_trust_in_startup_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagExample': 'True'})
    cache_path = tmp_path / 'flag_cache.dat'
    monkeypatch.setattr(custom_fflags_module, 'WINDOWS_FLAG_CACHE_PATH', cache_path)
    header = b'\x03\x00\x00\x00sig\x00'
    cache_path.write_bytes(
        header
        + json.dumps(
            {
                'applicationSettings': {EXTERNAL_CA_FLAG: 'True', 'Existing': 'untouched'},
                'otherMetadata': 'untouched',
            }
        ).encode()
    )
    modifier = CustomFFlagModifier(config)

    assert modifier.prime_startup_flag_cache()
    assert b'FFlagExample' in cache_path.read_bytes()
    config.custom_fflags_enabled = False
    assert modifier.prime_startup_flag_cache()

    raw = cache_path.read_bytes()
    assert raw[: len(header)] == header
    assert json.loads(raw[len(header) :]) == {
        'applicationSettings': {
            EXTERNAL_CA_FLAG: 'False',
            DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
            'Existing': 'untouched',
        },
        'otherMetadata': 'untouched',
    }
    assert not modifier.prime_startup_flag_cache()


def test_startup_cache_is_seeded_when_saved_toggle_is_already_off(tmp_path: Path) -> None:
    settings_path = tmp_path / 'settings.json'
    settings = {
        'custom_fflags_enabled': False,
        'custom_fflags': {EXTERNAL_CA_FLAG: 'True', 'FFlagExample': 'True'},
    }
    settings_path.write_text(json.dumps(settings), encoding='utf-8')
    cache_path = tmp_path / 'flag_cache.dat'
    header = b'\x00\x00\x00\x00\x00'
    cache_path.write_bytes(
        header + b'{"applicationSettings":{"FFlagExample":"True"}}',
    )
    modifier = CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=True, custom_fflags={}),
        flag_cache_path=cache_path,
        settings_path=settings_path,
    )

    assert modifier.prime_startup_flag_cache()
    assert json.loads(cache_path.read_bytes()[len(header) :])['applicationSettings'] == {
        EXTERNAL_CA_FLAG: 'False',
        DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
    }
    assert json.loads(settings_path.read_text(encoding='utf-8')) == settings


def test_macos_seeds_trust_with_user_flags_off_and_preserves_other_settings(tmp_path: Path) -> None:
    resources = tmp_path / 'Roblox.app' / 'Contents' / 'Resources'
    settings_path = resources / 'ClientSettings' / 'ClientAppSettings.json'
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        json.dumps({'FFlagExample': 'True', EXTERNAL_CA_FLAG: 'True', 'Unrelated': 'kept'}),
        encoding='utf-8',
    )
    modifier = CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=False, custom_fflags={'FFlagExample': 'True'}),
        macos_resource_dirs=[resources],
    )

    assert modifier.prime_startup_flag_cache()
    assert json.loads(settings_path.read_text(encoding='utf-8')) == {
        EXTERNAL_CA_FLAG: 'False',
        DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG: '1',
        'Unrelated': 'kept',
    }
    assert not modifier.prime_startup_flag_cache()


@pytest.mark.parametrize('platform', ['darwin', 'linux'])
@pytest.mark.parametrize('enabled', [False, True])
def test_other_platforms_receive_mandatory_trust_companions(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    *,
    enabled: bool,
) -> None:
    monkeypatch.setattr(custom_fflags_module, 'IS_WINDOWS', False)
    monkeypatch.setattr(custom_fflags_module.sys, 'platform', platform)
    modifier = CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=enabled, custom_fflags={'FFlagExample': 'True'}),
    )
    original = b'{"applicationSettings":{"DFFlagHttpUseEmbeddedCaCerts":"True"}}'
    modified = modifier.modify_response('/v2/settings/application/PCDesktopClient', original)

    assert modifier.is_enabled()
    assert modifier.runtime_flags()[EXTERNAL_CA_FLAG] == 'False'
    assert json.loads(modified)['applicationSettings'][EXTERNAL_CA_FLAG] == 'False'
    assert json.loads(modified)['applicationSettings'][DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    assert ('FFlagExample' in json.loads(modified)['applicationSettings']) is enabled


@pytest.mark.parametrize(
    'path',
    [
        '/v2/settings/application/PCClientBootstrapper',
        '/v2/settings-compressed/application/PCClientBootstrapper.zst',
        '/v2/client-version/WindowsPlayer',
    ],
)
def test_windows_compatibility_leaves_bootstrapper_documents_unchanged(path: str) -> None:
    modifier = CustomFFlagModifier(SimpleNamespace(custom_fflags_enabled=False, custom_fflags={}))
    original = b'{"applicationSettings":{"DFFlagHttpUseEmbeddedCaCerts":"True"}}'

    assert modifier.modify_response(path, original) is original


def test_windows_routes_clientsettings_before_launch_with_user_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', True)
    logs: list[tuple[str, str]] = []

    def record_log(category: str, message: str) -> None:
        logs.append((category, message))

    monkeypatch.setattr(proxy_master, 'log_buffer', SimpleNamespace(log=record_log))
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(settings={}))
    proxy.custom_fflag_modifier = CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=False, custom_fflags={}),
    )
    hosts = proxy._desired_intercept_hosts()  # pyright: ignore[reportPrivateUsage]
    assert {
        'assetdelivery.roblox.com',
        'clientsettings.roblox.com',
        'clientsettingscdn.roblox.com',
    } <= hosts
    proxy._log_intercept_configuration(  # pyright: ignore[reportPrivateUsage]
        'Windows compatibility',
        hosts,
    )
    assert 'custom_fflags=disabled; clientsettings_intercepted=yes' in logs[-1][1]


def test_sober_arms_delayed_interception_with_custom_flags_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def running_process(_installation: object) -> tuple[int, float]:
        return 1001, 100.0

    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(proxy_master, 'IS_WINDOWS', False)
    monkeypatch.setattr(proxy_master, 'IS_MACOS', False)
    monkeypatch.setattr(platform_linux, 'linux_client_main_process', running_process)
    proxy = proxy_master.ProxyMaster.__new__(proxy_master.ProxyMaster)
    setattr(proxy, 'config_manager', SimpleNamespace(custom_fflags_enabled=False))
    monkeypatch.setattr(
        proxy,
        '_active_linux_client_installation',
        SimpleNamespace(client=SOBER_CLIENT),
        raising=False,
    )
    monkeypatch.setattr(proxy, '_sober_fflag_timer_thread', None, raising=False)
    monkeypatch.setattr(proxy, '_sober_boottime', lambda: 130.0)
    armed = threading.Event()

    def record_passthrough(enabled: object) -> None:
        if enabled is False:
            armed.set()

    monkeypatch.setattr(proxy, '_set_linux_sober_clientsettings_passthrough', record_passthrough)
    monkeypatch.setattr(proxy, 'refresh_username_spoofer_interception', lambda: None)
    proxy._start_linux_sober_custom_fflag_timer()  # pyright: ignore[reportPrivateUsage]
    try:
        assert armed.wait(timeout=1.0)
    finally:
        proxy._stop_linux_sober_custom_fflag_timer()  # pyright: ignore[reportPrivateUsage]
