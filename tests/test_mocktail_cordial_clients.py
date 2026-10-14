"""Mocktail and Cordial Linux client support."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fleasion.modifications import fflag_manager as fflag_module, platform_targets
from fleasion.proxy import master as proxy_master
from fleasion.utils import mocktail_config, platform_linux, roblox_auth
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    MOCKTAIL_CLIENT,
    SOBER_CLIENT,
    LinuxClientInstallation,
    select_linux_client,
)

if TYPE_CHECKING:
    from fleasion.utils.linux_clients import LinuxClientDescriptor

MocktailHome = tuple[LinuxClientInstallation, list[list[str]], Path]

MOCKTAIL_CONFIG = """\
# Mocktail configuration.
version: 1

integrations:
  fleasion:
    # Boolean (default: false): trust Fleasion's CA without editing Roblox files.
    enabled: false
    # String: match Fleasion's routing mode: env or hosts.
    proxy_mode: env
    # Integer: Fleasion's local HTTP proxy port in env mode.
    proxy_port: 58443
  discord_rpc:
    enabled: false

network:
  use_system_proxy: false
"""

CA_PEM = '-----BEGIN CERTIFICATE-----\nZmxlYXNpb24=\n-----END CERTIFICATE-----\n'


def _installation(client: LinuxClientDescriptor, home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=home, environ={}),
        executable=Path('/usr/bin/flatpak'),
    )


# Registry paths


def test_cordial_overlay_lives_under_config_and_extracted_bundles_are_found(tmp_path):
    root = tmp_path / '.var' / 'app' / 'io.github.luohoa97.Cordial'
    paths = CORDIAL_CLIENT.paths(home=tmp_path, environ={})
    assert paths.resource_roots == (root / 'config' / 'cordial' / 'overlay',)
    assert paths.resource_base_root == root / 'config' / 'cordial'

    cache_assets = root / 'cache' / 'cordial' / 'assets'
    build_assets = root / 'data' / 'cordial' / 'builds' / '700.1' / 'assets'
    for assets in (cache_assets, build_assets):
        (assets / 'ssl').mkdir(parents=True)
        (assets / 'ssl' / 'cacert.pem').write_text('roots', encoding='utf-8')
    # An extraction without a bundle is not a certificate target
    (root / 'cache' / 'cordial' / 'assets-arm64-v8a').mkdir(parents=True)

    assert paths.ca_bundle_dirs() == (cache_assets, build_assets)
    assert paths.original_asset_dirs() == (
        cache_assets,
        root / 'cache' / 'cordial' / 'assets-arm64-v8a',
        build_assets,
    )


def test_cordial_profiles_supply_flags_settings_and_storage(tmp_path):
    root = tmp_path / '.var' / 'app' / 'io.github.luohoa97.Cordial'
    profiles = root / 'data' / 'cordial' / 'profiles'
    for name in ('default', 'alt'):
        (profiles / name / 'data' / 'files' / 'appData' / 'rbx-storage').mkdir(parents=True)
    (profiles / 'default' / 'data' / 'files' / 'appData' / 'rbx-storage.db').write_bytes(b'')
    paths = CORDIAL_CLIENT.paths(home=tmp_path, environ={})

    assert paths.fflag_files() == (
        profiles / 'alt' / 'flags.json',
        profiles / 'default' / 'flags.json',
    )
    assert paths.settings_dirs() == (
        profiles / 'alt' / 'data' / 'files' / 'appData',
        profiles / 'default' / 'data' / 'files' / 'appData',
    )
    assert set(paths.storage_paths()) == {
        profiles / 'alt' / 'data' / 'files' / 'appData' / 'rbx-storage',
        profiles / 'default' / 'data' / 'files' / 'appData' / 'rbx-storage',
        profiles / 'default' / 'data' / 'files' / 'appData' / 'rbx-storage.db',
    }


def test_mocktail_has_no_overlay_and_uses_its_own_files(tmp_path):
    root = tmp_path / '.var' / 'app' / 'space.bigrat.mocktail'
    (root / 'config' / 'mocktail').mkdir(parents=True)
    paths = MOCKTAIL_CLIENT.paths(home=tmp_path, environ={})

    assert paths.resource_roots == ()
    assert paths.config_file == root / 'config' / 'mocktail' / 'config.yaml'
    assert paths.fflag_files() == (root / 'config' / 'mocktail' / 'fflags.json',)
    assert MOCKTAIL_CLIENT.proxy_integration == 'mocktail_config'


def test_auto_selection_follows_the_roblox_desktop_handler(tmp_path):
    installed = tuple(
        _installation(c, tmp_path) for c in (SOBER_CLIENT, MOCKTAIL_CLIENT, CORDIAL_CLIENT)
    )

    def run(command, **_kwargs):
        return subprocess.CompletedProcess(command, 0, 'io.github.luohoa97.Cordial.desktop\n', '')

    selected = select_linux_client(
        'auto',
        installed=installed,
        home=tmp_path,
        environ={},
        which=lambda name: f'/usr/bin/{name}',
        run=run,
    )
    assert selected is not None
    assert selected.key == 'cordial'


# Mocktail config editing


def test_mocktail_config_edit_keeps_comments_and_other_keys():
    updated = mocktail_config.set_integration(
        MOCKTAIL_CONFIG, {'enabled': 'true', 'proxy_mode': 'env', 'proxy_port': '60001'}
    )
    assert mocktail_config.read_integration(updated) == {
        'enabled': 'true',
        'proxy_mode': 'env',
        'proxy_port': '60001',
        'ca_certificate': None,
    }
    assert "# Boolean (default: false): trust Fleasion's CA" in updated
    assert updated.count('enabled: false') == 1  # discord_rpc untouched
    restored = mocktail_config.set_integration(
        updated, {'enabled': 'false', 'proxy_mode': 'env', 'proxy_port': '58443'}
    )
    assert restored == MOCKTAIL_CONFIG


def test_mocktail_config_adds_missing_integration_block():
    updated = mocktail_config.set_integration('version: 1\n', {'enabled': 'true'})
    assert updated == 'version: 1\nintegrations:\n  fleasion:\n    enabled: true\n'


@pytest.mark.parametrize(
    ('network', 'port', 'conflict'),
    [
        ('  use_system_proxy: true\n', 58443, 'network.use_system_proxy'),
        ('  proxy_host: 10.0.0.2\n  proxy_port: 8080\n', 58443, 'network.proxy_host'),
        ('  proxy_host: 127.0.0.1\n  proxy_port: 58443\n', 58443, None),
        ('  proxy_host: 127.0.0.1\n  proxy_port: 58443\n', None, 'network.proxy_host'),
        ('  use_system_proxy: false\n', 58443, None),
    ],
)
def test_mocktail_network_conflicts(network, port, conflict):
    text = f'version: 1\nnetwork:\n{network}'
    assert mocktail_config.conflicting_network_setting(text, port) == conflict


# Mocktail arming


@pytest.fixture
def mocktail_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MocktailHome:
    overrides: list[list[str]] = []
    monkeypatch.setattr(
        platform_linux,
        '_flatpak_override',
        lambda _installation, args, _action: overrides.append(args) or True,
    )
    monkeypatch.setattr(
        platform_linux, 'MOCKTAIL_INTEGRATION_STATE', tmp_path / 'fleasion' / 'mocktail.json'
    )
    monkeypatch.setattr(
        platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'fleasion' / 'override.json'
    )
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)

    def environment(_installation: LinuxClientInstallation) -> dict[str, str | None]:
        return dict.fromkeys(platform_linux.MOCKTAIL_INTEGRATION_ENV_NAMES)

    monkeypatch.setattr(platform_linux, '_read_mocktail_proxy_environment', environment)
    installation = _installation(MOCKTAIL_CLIENT, tmp_path)
    monkeypatch.setattr(platform_linux, '_installation_for_client_key', lambda _key: installation)
    ca_file = tmp_path / 'proxy_ca' / 'ca.crt'
    ca_file.parent.mkdir()
    ca_file.write_text(CA_PEM, encoding='utf-8')
    return installation, overrides, ca_file


def test_mocktail_arm_and_disarm_round_trip(mocktail_home: MocktailHome) -> None:
    installation, overrides, ca_file = mocktail_home
    config = installation.paths.config_file
    config.parent.mkdir(parents=True)
    config.write_text(MOCKTAIL_CONFIG, encoding='utf-8')
    config.chmod(0o600)

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file
    )
    sandbox_ca = installation.paths.flatpak_root / 'config' / 'Fleasion' / 'proxy_ca' / 'ca.crt'
    assert sandbox_ca.read_text(encoding='utf-8') == CA_PEM
    assert mocktail_config.read_integration(config.read_text(encoding='utf-8')) == {
        'enabled': 'true',
        'proxy_mode': 'env',
        'proxy_port': '60001',
        'ca_certificate': json.dumps(str(sandbox_ca)),
    }
    assert config.stat().st_mode & 0o777 == 0o600
    assert overrides[-1] == [
        '--env=MOCKTAIL_FLEASION_ENABLED=true',
        '--env=MOCKTAIL_FLEASION_PROXY_MODE=env',
        f'--env=MOCKTAIL_FLEASION_CA_CERTIFICATE={sandbox_ca}',
        '--env=MOCKTAIL_FLEASION_PROXY_PORT=60001',
    ]

    # A second arm must not record Fleasion's own values as the originals
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60002', client_key='mocktail', ca_cert_path=ca_file
    )
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='mocktail')
    assert config.read_text(encoding='utf-8') == MOCKTAIL_CONFIG
    assert overrides[-1] == [
        f'--unset-env={name}' for name in platform_linux.MOCKTAIL_INTEGRATION_ENV_NAMES
    ]
    assert not platform_linux.MOCKTAIL_INTEGRATION_STATE.exists()


def test_mocktail_hosts_mode_drops_the_port(mocktail_home: MocktailHome) -> None:
    installation, overrides, ca_file = mocktail_home
    config = installation.paths.config_file
    config.parent.mkdir(parents=True)
    config.write_text(MOCKTAIL_CONFIG, encoding='utf-8')

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file, proxy_mode='hosts'
    )
    assert mocktail_config.read_integration(config.read_text(encoding='utf-8')) == {
        'enabled': 'true',
        'proxy_mode': 'hosts',
        'proxy_port': None,
        'ca_certificate': json.dumps(str(
            installation.paths.flatpak_root / 'config' / 'Fleasion' / 'proxy_ca' / 'ca.crt'
        )),
    }
    assert '--env=MOCKTAIL_FLEASION_PROXY_MODE=hosts' in overrides[-1]


def test_mocktail_arm_refuses_a_conflicting_proxy(mocktail_home):
    installation, overrides, ca_file = mocktail_home
    config = installation.paths.config_file
    config.parent.mkdir(parents=True)
    text = MOCKTAIL_CONFIG.replace('use_system_proxy: false', 'use_system_proxy: true')
    config.write_text(text, encoding='utf-8')

    assert not platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file
    )
    assert config.read_text(encoding='utf-8') == text
    assert overrides == []


def test_mocktail_first_launch_uses_environment_only(mocktail_home):
    installation, overrides, ca_file = mocktail_home
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca_file
    )
    assert not installation.paths.config_file.exists()
    assert '--env=MOCKTAIL_FLEASION_ENABLED=true' in overrides[-1]


def test_cordial_uses_standard_proxy_environment(tmp_path, monkeypatch):
    overrides: list[list[str]] = []
    monkeypatch.setattr(
        platform_linux,
        '_flatpak_override',
        lambda _installation, args, _action: overrides.append(args) or True,
    )
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'override.json')
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    installation = _installation(CORDIAL_CLIENT, tmp_path)
    monkeypatch.setattr(platform_linux, '_installation_for_client_key', lambda _key: installation)

    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='cordial', proxy_mode='hosts'
    )
    assert overrides == []  # nothing to arm in hosts mode
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='cordial'
    )
    assert '--env=HTTPS_PROXY=http://127.0.0.1:60001' in overrides[-1]
    assert '--env=no_proxy=localhost,127.0.0.1,::1' in overrides[-1]


# Engine process detection


def test_engine_process_is_scoped_to_the_client_cgroup(tmp_path, monkeypatch):
    for pid, cgroup in (
        (10, 'app-flatpak-org.vinegarhq.Sober-1.scope'),
        (11, 'app-flatpak-io.github.luohoa97.Cordial-2.scope'),
    ):
        process = tmp_path / str(pid)
        process.mkdir()
        (process / 'cgroup').write_text(f'0::/user.slice/{cgroup}\n', encoding='utf-8')
        fields = ' '.join(['S'] + ['0'] * 18 + ['500'])
        (process / 'stat').write_text(f'{pid} (cordial-run) {fields}', encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(
        platform_linux, '_process_pids', lambda name: [10, 11] if name == 'cordial-run' else []
    )

    engine = platform_linux.linux_client_main_process(_installation(CORDIAL_CLIENT, tmp_path))
    assert engine is not None
    assert engine[0] == 11


# Login cookies


def _private_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    path.chmod(0o600)
    return path


def test_mocktail_cookie_is_read_and_switched(tmp_path, monkeypatch):
    installation = _installation(MOCKTAIL_CLIENT, tmp_path)
    provider = roblox_auth.MOCKTAIL_LOCAL_AUTH_PROVIDER
    cookie_path = provider.cookie_path(installation)
    assert cookie_path == installation.paths.data_root / 'auth' / 'roblox.cookie'
    _private_file(cookie_path, '.ROBLOSECURITY=old-token\n')

    assert provider.read_roblosecurity(cookie_path) == 'old-token'
    assert provider.write_roblosecurity(cookie_path, 'new-token') is True
    assert cookie_path.read_text(encoding='utf-8') == '.ROBLOSECURITY=new-token\n'
    assert cookie_path.stat().st_mode & 0o777 == 0o600


def test_mocktail_cookie_writer_rejects_unknown_formats(tmp_path):
    installation = _installation(MOCKTAIL_CLIENT, tmp_path)
    provider = roblox_auth.MOCKTAIL_LOCAL_AUTH_PROVIDER
    cookie_path = _private_file(provider.cookie_path(installation), 'something else\n')

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        provider.write_roblosecurity(cookie_path, 'new-token')
    assert exc_info.value.code == 'cookie_store_unknown_format'
    assert cookie_path.read_text(encoding='utf-8') == 'something else\n'


def test_cordial_cookie_is_read_from_most_recent_profile_and_never_written(tmp_path):
    installation = _installation(CORDIAL_CLIENT, tmp_path)
    provider = roblox_auth.CORDIAL_LOCAL_AUTH_PROVIDER
    profiles = installation.paths.data_root / 'profiles'
    old = _private_file(profiles / 'default' / 'cookies', 'roblox.com\t.ROBLOSECURITY=old\n')
    new = _private_file(
        profiles / 'alt' / 'cookies',
        '# cordial cookies v1\nwww.roblox.com\tRBXEventTrackerV2=x; .ROBLOSECURITY=newest; a=b\n',
    )
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))

    cookie_path = provider.cookie_path(installation)
    assert cookie_path == new
    assert provider.read_roblosecurity(cookie_path) == 'newest'
    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        provider.write_roblosecurity(cookie_path, 'other')
    assert exc_info.value.code == 'account_switching_unsupported'
    assert '.ROBLOSECURITY=newest' in new.read_text(encoding='utf-8')


def test_cordial_without_file_store_falls_back_to_default_profile_path(tmp_path):
    installation = _installation(CORDIAL_CLIENT, tmp_path)
    provider = roblox_auth.CORDIAL_LOCAL_AUTH_PROVIDER
    expected = installation.paths.data_root / 'profiles' / 'default' / 'cookies'
    assert provider.cookie_path(installation) == expected
    assert provider.read_roblosecurity(expected) is None


# FastFlags


def test_client_flag_files_merge_over_user_flags_and_restore(tmp_path, monkeypatch):
    existing = tmp_path / 'profiles' / 'default' / 'flags.json'
    existing.parent.mkdir(parents=True)
    existing.write_text('{"FFlagUserChoice": true, "FIntShared": 1}', encoding='utf-8')
    fresh = tmp_path / 'mocktail' / 'fflags.json'
    fresh.parent.mkdir(parents=True)
    monkeypatch.setattr(fflag_module, '_linux_client_fflag_files', lambda: [existing, fresh])

    manager = fflag_module.FastFlagManager([], tmp_path / 'stash')
    monkeypatch.setattr(
        manager, 'build_json', lambda _settings: {'FIntShared': '60', 'FFlagX': 'false'}
    )
    assert manager.write({}) == set()

    assert json.loads(existing.read_text(encoding='utf-8')) == {
        'FFlagUserChoice': True,
        'FIntShared': 60,
        'FFlagX': False,
    }
    assert json.loads(fresh.read_text(encoding='utf-8')) == {'FIntShared': 60, 'FFlagX': False}

    manager.restore()
    assert json.loads(existing.read_text(encoding='utf-8')) == {
        'FFlagUserChoice': True,
        'FIntShared': 1,
    }
    assert not fresh.exists()


# Asset modifications


def test_cordial_shares_sobers_android_target_mapping(tmp_path, monkeypatch):
    root = tmp_path / '.var' / 'app' / 'io.github.luohoa97.Cordial'
    overlay = root / 'config' / 'cordial' / 'overlay'
    overlay.mkdir(parents=True)
    monkeypatch.setattr('fleasion.utils.paths.USER_HOME', tmp_path)
    monkeypatch.setattr(platform_targets.sys, 'platform', 'linux')

    target = r'PlatformContent\pc\textures\sky\sky512_bk.tex'
    assert (
        platform_targets.target_path_for_resource_dir(target, overlay)
        == 'android/textures/sky/sky512_bk.tex'
    )

    extracted = root / 'cache' / 'cordial' / 'assets' / 'android' / 'textures' / 'sky'
    extracted.mkdir(parents=True)
    (extracted / 'sky512_bk.tex').write_bytes(b'original-sky')
    assert platform_targets.read_current_platform_original_asset(target, overlay) == b'original-sky'
    assert platform_targets.read_current_platform_original_directory(
        'android/textures/sky', overlay
    ) == {'sky512_bk.tex': b'original-sky'}


# Certificates


def test_extracted_cordial_bundles_get_the_fleasion_ca(tmp_path, monkeypatch):
    bundle_dir = tmp_path / 'assets'
    (bundle_dir / 'ssl').mkdir(parents=True)
    bundle = bundle_dir / 'ssl' / 'cacert.pem'
    bundle.write_text(
        '-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n', encoding='utf-8'
    )
    monkeypatch.setattr(proxy_master, 'IS_LINUX', True)
    monkeypatch.setattr(platform_linux, 'find_linux_ca_bundle_dirs', lambda: [bundle_dir])
    monkeypatch.setattr(
        proxy_master, '_analyze_and_strip_fleasion_cas', lambda text, _ca: (text, 0, 0)
    )

    assert proxy_master._patch_linux_extra_ca_bundles(CA_PEM) is True
    text = bundle.read_text(encoding='utf-8')
    assert text.startswith('-----BEGIN CERTIFICATE-----\nAAAA')
    assert 'ZmxlYXNpb24=' in text
