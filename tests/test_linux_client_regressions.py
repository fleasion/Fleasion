from __future__ import annotations

# Pytest discovers this directory as standalone modules
# ruff: file-ignore[implicit-namespace-package]
import json
import stat
import sys
import threading
from pathlib import Path
from subprocess import CompletedProcess  # ruff: ignore[suspicious-subprocess-import]
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import certifi
import pytest

from fleasion.gui import modifications_tab as mod_tab_module
from fleasion.gui.settings_tab import SettingsTab
from fleasion.modifications import manager as modification_module
from fleasion.modifications.fflag_manager import FastFlagManager
from fleasion.proxy import master
from fleasion.proxy.addons import custom_fflags
from fleasion.utils import mocktail_config, platform_linux
from fleasion.utils.certs import generate_ca, get_ca_pem
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    MOCKTAIL_CLIENT,
    SOBER_CLIENT,
    LinuxClientInstallation,
)
from fleasion.utils.linux_fflags import (
    FlagLayer,
    linux_flag_stash_paths,
    managed_linux_flag_paths,
    set_linux_flag_layer,
)

if TYPE_CHECKING:
    from fleasion.utils.linux_clients import LinuxClientDescriptor

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith('linux'), reason='Linux client runtime integration'
)

CONFIG = (
    'version: 1\nintegrations:\n  fleasion:\n'
    '    enabled: false # Keep my comment\n'
    '    proxy_mode: env\n    proxy_port: 58443\n'
)


def _override_ok(*_args: object, **_kwargs: object) -> bool:
    return True


def _override_fail(*_args: object, **_kwargs: object) -> bool:
    return False


def _installation(client: LinuxClientDescriptor, home: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client, client.paths(home=home, environ={}), Path('/usr/bin/flatpak')
    )


@pytest.fixture
def mocktail_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[LinuxClientInstallation, Path]:
    installed = _installation(MOCKTAIL_CLIENT, tmp_path)
    installed.paths.config_root.mkdir(parents=True)
    installed.paths.config_file.write_text(CONFIG, encoding='utf-8')
    ca = tmp_path / 'ca.crt'
    ca.write_text('public certificate', encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'MOCKTAIL_INTEGRATION_STATE', tmp_path / 'original.json')
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', tmp_path / 'override.json')
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)

    def selected_installation(_key: str) -> LinuxClientInstallation:
        return installed

    monkeypatch.setattr(platform_linux, '_installation_for_client_key', selected_installation)
    monkeypatch.setattr(platform_linux, '_flatpak_override', _override_ok)

    def environment(_installation: LinuxClientInstallation) -> dict[str, str | None]:
        return dict.fromkeys(platform_linux.MOCKTAIL_INTEGRATION_ENV_NAMES)

    monkeypatch.setattr(platform_linux, '_read_mocktail_proxy_environment', environment)
    return installed, ca


def test_failed_mocktail_override_restores_config_and_preserves_pending_recovery(
    mocktail_environment: tuple[LinuxClientInstallation, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed, ca = mocktail_environment
    monkeypatch.setattr(platform_linux, '_flatpak_override', _override_fail)
    assert not platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
    )
    assert installed.paths.config_file.read_text() == CONFIG
    assert platform_linux.MOCKTAIL_INTEGRATION_STATE.exists()
    assert not platform_linux.LINUX_PROXY_OVERRIDE_STATE.exists()
    monkeypatch.setattr(platform_linux, '_flatpak_override', _override_ok)
    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert installed.paths.config_file.read_text() == CONFIG
    assert not platform_linux.MOCKTAIL_INTEGRATION_STATE.exists()


def test_mocktail_recovers_config_changed_before_ownership_marker(
    mocktail_environment: tuple[LinuxClientInstallation, Path],
) -> None:
    installed, _ca = mocktail_environment
    platform_linux.MOCKTAIL_INTEGRATION_STATE.write_text(
        json.dumps({'original': mocktail_config.read_integration(CONFIG)})
    )
    installed.paths.config_file.write_text(
        mocktail_config.set_integration(CONFIG, {'enabled': 'true', 'proxy_port': '60001'})
    )
    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert installed.paths.config_file.read_text() == CONFIG


@pytest.mark.parametrize(
    'original',
    [
        'version: 1\n',
        'version: 1\nintegrations:\n  discord_rpc:\n    enabled: false\n',
    ],
)
def test_mocktail_restores_absent_mapping_blocks(
    mocktail_environment: tuple[LinuxClientInstallation, Path], original: str
) -> None:
    installed, ca = mocktail_environment
    installed.paths.config_file.write_text(original)
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
    )
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='mocktail')
    assert installed.paths.config_file.read_text() == original


def test_mocktail_first_launch_recovers_environment_without_config(
    mocktail_environment: tuple[LinuxClientInstallation, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed, ca = mocktail_environment
    installed.paths.config_file.unlink()
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
    )
    platform_linux.LINUX_PROXY_OVERRIDE_STATE.unlink()
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert not installed.paths.config_file.exists()
    assert not platform_linux.MOCKTAIL_INTEGRATION_STATE.exists()


def test_mocktail_native_ca_health_and_rotation(
    mocktail_environment: tuple[LinuxClientInstallation, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed, ca = mocktail_environment
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(master, '_current_proxy_ca_dir', lambda: ca.parent)
    proxy = master.ProxyMaster.__new__(master.ProxyMaster)
    monkeypatch.setattr(proxy, '_active_linux_client_installation', installed, raising=False)
    monkeypatch.setattr(proxy, '_active_linux_client_key', 'mocktail', raising=False)
    assert not proxy.ensure_env_proxy_roblox_ca(Path(installed.app_id))['success']
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
    )
    assert proxy.ensure_env_proxy_roblox_ca(Path(installed.app_id))['success']
    assert proxy.monitor_env_proxy_roblox_ca(
        Path(installed.app_id), threading.Event(), duration=0.01
    )['success']
    ca.write_text('rotated public certificate')
    assert not proxy.ensure_env_proxy_roblox_ca(Path(installed.app_id))['success']


def test_hosts_mocktail_trust_failure_is_propagated(
    mocktail_environment: tuple[LinuxClientInstallation, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    installed, _ca = mocktail_environment
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(platform_linux, 'set_linux_client_env_proxy_override', _override_fail)
    proxy = master.ProxyMaster.__new__(master.ProxyMaster)
    monkeypatch.setattr(proxy, '_active_linux_client_installation', installed, raising=False)
    monkeypatch.setattr(proxy, '_active_linux_client_key', 'mocktail', raising=False)
    monkeypatch.setattr(proxy, '_active_proxy_port', 60001, raising=False)
    # Exercise the private startup gate without starting privileged helpers
    assert not proxy._arm_linux_hosts_client_trust()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]


def test_cordial_repairs_extracted_bundle_with_healthy_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _installation(CORDIAL_CLIENT, tmp_path)
    ca_dir = tmp_path / 'certificates'
    ca_path, _key = generate_ca(ca_dir)
    ca_pem = get_ca_pem(ca_path)
    roots = Path(certifi.where()).read_text(encoding='utf-8')
    overlay = installed.paths.resource_roots[0]
    extracted = installed.paths.cache_root / 'assets'
    for root in (overlay, extracted):
        (root / 'ssl').mkdir(parents=True)
        (root / 'ssl' / 'cacert.pem').write_text(roots + '\n' + ca_pem)
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, '_current_proxy_ca_dir', lambda: ca_dir)

    def resource_dirs(**_kwargs: object) -> list[Path]:
        return [overlay]

    monkeypatch.setattr(platform_linux, 'find_roblox_resource_dirs', resource_dirs)
    monkeypatch.setattr(platform_linux, 'find_linux_ca_bundle_dirs', lambda: [extracted])
    proxy = master.ProxyMaster.__new__(master.ProxyMaster)
    assert proxy.inspect_env_proxy_roblox_ca(Path(installed.app_id), 'before update')['healthy']
    (extracted / 'ssl' / 'cacert.pem').write_text(roots)
    assert not proxy.inspect_env_proxy_roblox_ca(Path(installed.app_id), 'after update')['healthy']
    result = proxy.ensure_env_proxy_roblox_ca(Path(installed.app_id))
    assert result['success']
    assert result['changed']
    assert ca_pem.strip() in (extracted / 'ssl' / 'cacert.pem').read_text()


def test_cordial_discovers_quest_settings_and_storage(tmp_path: Path) -> None:
    installed = _installation(CORDIAL_CLIENT, tmp_path)
    data = (
        installed.paths.data_root / 'profiles' / 'default' / 'quest' / 'data' / 'files' / 'appData'
    )
    storage = data / 'rbx-storage'
    storage.mkdir(parents=True)
    assert installed.paths.settings_dirs() == (data,)
    assert installed.paths.storage_paths() == (storage,)


@pytest.mark.parametrize('original_exists', [True, False])
def test_native_flag_layers_preserve_each_other_and_restore_original(
    tmp_path: Path, *, original_exists: bool
) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    original = b'{"FIntShared": 3, "FStringUser": "unchanged"}\n'
    if original_exists:
        path.write_bytes(original)
        path.chmod(stat.S_IRUSR)
    assert set_linux_flag_layer(path, stash, 'presets', {'FIntShared': 10})
    assert set_linux_flag_layer(path, stash, 'custom', {'FIntShared': '20', 'FFlagCustom': 'True'})
    assert json.loads(path.read_text())['FIntShared'] == '20'
    presets = {'FIntShared': 30}
    assert set_linux_flag_layer(path, stash, 'presets', presets)
    assert json.loads(path.read_text())['FIntShared'] == '20'
    assert set_linux_flag_layer(path, stash, 'custom', {})
    assert json.loads(path.read_text())['FIntShared'] == presets['FIntShared']
    assert 'FFlagCustom' not in json.loads(path.read_text())
    assert set_linux_flag_layer(path, stash, 'presets', {})
    if original_exists:
        assert path.read_bytes() == original
        assert stat.S_IMODE(path.stat().st_mode) == stat.S_IRUSR
    else:
        assert not path.exists()


@pytest.mark.parametrize('client', [SOBER_CLIENT, MOCKTAIL_CLIENT, CORDIAL_CLIENT])
def test_custom_flag_startup_seeding_is_client_specific(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: LinuxClientDescriptor
) -> None:
    installed = _installation(client, tmp_path)
    installed.paths.config_root.mkdir(parents=True)
    (installed.paths.data_root / 'profiles' / 'default').mkdir(parents=True)
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'stash')
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installed)
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagStartup': 'True'})
    modifier = custom_fflags.CustomFFlagModifier(config)
    assert modifier.prime_startup_flag_cache() is (client.key != 'sober')
    for path in installed.paths.fflag_files():
        flags = json.loads(path.read_text())
        assert flags['FFlagStartup'] == 'True'
        assert flags[custom_fflags.DYNAMIC_VARIABLE_RELOAD_INTERVAL_FLAG] == '1'
    config.custom_fflags_enabled = False
    modifier.prime_startup_flag_cache()
    assert all(not path.exists() for path in installed.paths.fflag_files())


def test_custom_native_flags_are_restored_on_shutdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'fflags.json'
    original = '{"FFlagUser": true}\n'
    path.write_text(original)
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [path])
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'stash')
    modifier = custom_fflags.CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagStartup': 'True'})
    )
    assert modifier.prime_linux_client_flags()
    modifier.restore_linux_client_flags()
    assert path.read_text() == original


@pytest.mark.parametrize('original_ca', ['missing.pem', 'stale.pem'])
def test_mocktail_arms_effective_ca_and_restores_both_sources(
    mocktail_environment: tuple[LinuxClientInstallation, Path],
    monkeypatch: pytest.MonkeyPatch,
    original_ca: str,
) -> None:
    installed, ca = mocktail_environment
    previous = ca.parent / original_ca
    if original_ca == 'stale.pem':
        previous.write_text('stale certificate', encoding='utf-8')
    original_config = CONFIG + f'    ca_certificate: {json.dumps(str(previous))} # Original path\n'
    installed.paths.config_file.write_text(original_config, encoding='utf-8')
    environment: dict[str, str | None] = dict.fromkeys(
        platform_linux.MOCKTAIL_INTEGRATION_ENV_NAMES
    )
    environment['MOCKTAIL_FLEASION_CA_CERTIFICATE'] = str(previous)
    environment['MOCKTAIL_FLEASION_ENABLED'] = 'false'
    environment['MOCKTAIL_FLEASION_PROXY_MODE'] = 'hosts'
    environment['MOCKTAIL_FLEASION_PROXY_PORT'] = '59999'
    original_environment = dict(environment)

    def read_environment(_installed: LinuxClientInstallation) -> dict[str, str | None]:
        return dict(environment)

    def override(_installed: LinuxClientInstallation, args: list[str], _action: str) -> bool:
        for arg in args:
            if arg.startswith('--env='):
                name, value = arg.removeprefix('--env=').split('=', 1)
                environment[name] = value
            else:
                environment[arg.removeprefix('--unset-env=')] = None
        return True

    monkeypatch.setattr(platform_linux, '_read_mocktail_proxy_environment', read_environment)
    monkeypatch.setattr(platform_linux, '_flatpak_override', override)
    assert platform_linux.set_linux_client_env_proxy_override(
        'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
    )
    active_path = mocktail_config.read_certificate_path(installed.paths.config_file.read_text())
    assert active_path == environment['MOCKTAIL_FLEASION_CA_CERTIFICATE']
    assert active_path is not None
    assert Path(active_path).read_bytes() == ca.read_bytes()
    assert platform_linux.inspect_mocktail_integration_ca(installed, ca)['healthy']
    environment['MOCKTAIL_FLEASION_CA_CERTIFICATE'] = str(previous)
    assert not platform_linux.inspect_mocktail_integration_ca(installed, ca)['healthy']
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='mocktail')
    assert installed.paths.config_file.read_text() == original_config
    assert environment == original_environment


@pytest.mark.parametrize('rearm', [False, True])
def test_mocktail_legacy_snapshot_preserves_unmanaged_ca(
    mocktail_environment: tuple[LinuxClientInstallation, Path],
    monkeypatch: pytest.MonkeyPatch,
    *,
    rearm: bool,
) -> None:
    installed, ca = mocktail_environment
    original_path = ca.parent / 'user-ca.pem'
    original_config = CONFIG + f'    ca_certificate: {json.dumps(str(original_path))}\n'
    installed.paths.config_file.write_text(original_config, encoding='utf-8')
    original = mocktail_config.read_integration(CONFIG)
    del original['ca_certificate']
    platform_linux.MOCKTAIL_INTEGRATION_STATE.write_text(json.dumps({'original': original}))
    environment = {'MOCKTAIL_FLEASION_CA_CERTIFICATE': str(original_path)}

    def read_environment(_installed: LinuxClientInstallation) -> dict[str, str | None]:
        return dict(environment)

    def override(_installed: LinuxClientInstallation, args: list[str], _action: str) -> bool:
        for arg in args:
            if arg.startswith('--env='):
                name, value = arg.removeprefix('--env=').split('=', 1)
                environment[name] = value
            else:
                environment.pop(arg.removeprefix('--unset-env='), None)
        return True

    monkeypatch.setattr(platform_linux, '_read_mocktail_proxy_environment', read_environment)
    monkeypatch.setattr(platform_linux, '_flatpak_override', override)
    if rearm:
        assert platform_linux.set_linux_client_env_proxy_override(
            'http://127.0.0.1:60001', client_key='mocktail', ca_cert_path=ca
        )
        assert environment['MOCKTAIL_FLEASION_CA_CERTIFICATE'] != str(original_path)
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='mocktail')
    assert installed.paths.config_file.read_text() == original_config
    assert environment == {'MOCKTAIL_FLEASION_CA_CERTIFICATE': str(original_path)}


def test_mocktail_reads_case_sensitive_flatpak_ca_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installed = _installation(MOCKTAIL_CLIENT, tmp_path)

    def which(_name: str) -> str:
        return '/usr/bin/flatpak'

    monkeypatch.setattr(platform_linux.shutil, 'which', which)

    def run(args: list[str], **_kwargs: object) -> CompletedProcess[str]:
        assert args[-2:] == ['--show', installed.app_id]
        return CompletedProcess(
            args,
            0,
            '[Environment]\nMOCKTAIL_FLEASION_CA_CERTIFICATE=/old/ca.pem\n'
            'MOCKTAIL_USE_SYSTEM_PROXY=1\nMOCKTAIL_HTTP_PROXY_HOST=example.org\n'
            'MOCKTAIL_HTTP_PROXY_PORT=8123\n',
            '',
        )

    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    environment = platform_linux._read_mocktail_proxy_environment(installed)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert environment['MOCKTAIL_FLEASION_CA_CERTIFICATE'] == '/old/ca.pem'
    assert environment['MOCKTAIL_USE_SYSTEM_PROXY'] == '1'
    assert environment['MOCKTAIL_HTTP_PROXY_HOST'] == 'example.org'
    assert environment['MOCKTAIL_HTTP_PROXY_PORT'] == '8123'


@pytest.fixture
def modification_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> modification_module.ModificationManager:
    cached = tmp_path / 'cached-sober-root'
    cached.mkdir()
    monkeypatch.setattr(modification_module, '_find_roblox_dirs', lambda: [cached])
    monkeypatch.setattr(modification_module, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    monkeypatch.setattr(modification_module, 'MOD_CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(modification_module, 'MODIFICATIONS_JSON', tmp_path / 'mods.json')
    monkeypatch.setattr(modification_module, 'READ_ONLY_STATE_FILE', tmp_path / 'modes.json')
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', list)
    return modification_module.ModificationManager()


def test_native_flag_target_change_reapplies_with_unchanged_resource_roots(
    modification_manager: modification_module.ModificationManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modification_manager.write_fast_flags({'grey_sky': True})
    modification_manager.restore_all()
    native = tmp_path / 'fflags.json'
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [native])
    assert modification_manager.refresh_roblox_dirs(reapply_if_changed=True)
    assert json.loads(native.read_text())['FFlagDebugSkyGray'] is True
    assert not modification_manager.refresh_roblox_dirs(reapply_if_changed=True)


def test_client_switch_restores_custom_flags_and_reapplies_presets_with_proxy_stopped(
    modification_manager: modification_module.ModificationManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    files = {name: tmp_path / f'{name}.json' for name in ('cordial', 'mocktail')}
    originals = {name: f'{{"FStringUser": "{name}"}}\n' for name in files}
    for name, path in files.items():
        path.write_text(originals[name], encoding='utf-8')
    config = SimpleNamespace(
        linux_client='cordial', custom_fflags_enabled=True, custom_fflags={'FFlagCustom': 'True'}
    )
    monkeypatch.setattr(
        platform_linux, 'find_linux_fflag_files', lambda: [files[config.linux_client]]
    )
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    modifier = custom_fflags.CustomFFlagModifier(config)
    assert modifier.prime_linux_client_flags()
    modification_manager.write_fast_flags({'grey_sky': True})
    proxy = SimpleNamespace(
        is_running=False,
        restore_linux_custom_fflags=modifier.restore_linux_client_flags,
        prime_custom_fflag_cache=modifier.prime_startup_flag_cache,
    )
    view = cast(
        'SettingsTab',
        SimpleNamespace(
            _config=config,
            _linux_client_combo=SimpleNamespace(currentData=lambda: 'mocktail'),
            _tray=SimpleNamespace(proxy_master=proxy, mod_manager=modification_manager),
            _refresh_linux_client_status=lambda: None,
        ),
    )
    monkeypatch.setattr(platform_linux, '_linux_client_preference', None)
    SettingsTab._on_linux_client_changed(view)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert files['cordial'].read_text() == originals['cordial']
    flags = json.loads(files['mocktail'].read_text())
    assert flags['FFlagDebugSkyGray'] is True
    assert flags['FFlagCustom'] == 'True'
    config.custom_fflags_enabled = False
    modifier.prime_linux_client_flags()
    assert 'FFlagCustom' not in json.loads(files['mocktail'].read_text())
    assert files['cordial'].read_text() == originals['cordial']


def test_disabling_custom_flags_restores_previously_seeded_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = tmp_path / 'old.json'
    new = tmp_path / 'new.json'
    original = '{"FStringUser": "preserved"}\n'
    old.write_text(original, encoding='utf-8')
    targets = [old]
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: list(targets))
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagCustom': 'True'})
    modifier = custom_fflags.CustomFFlagModifier(config)
    assert modifier.prime_linux_client_flags()
    targets[:] = [new]
    config.custom_fflags_enabled = False
    assert modifier.prime_linux_client_flags()
    assert old.read_text() == original
    assert not new.exists()


def test_native_custom_flag_cleanup_retries_failed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'fflags.json'
    original = '{"FStringUser": "preserved"}\n'
    path.write_text(original, encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [path])
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    modifier = custom_fflags.CustomFFlagModifier(
        SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagCustom': 'True'})
    )
    assert modifier.prime_linux_client_flags()
    with monkeypatch.context() as failed_cleanup:
        failed_cleanup.setattr(custom_fflags, 'set_linux_flag_layer', _override_fail)
        assert not modifier.restore_linux_client_flags()
    assert modifier.restore_linux_client_flags()
    assert path.read_text() == original


def test_client_switch_retains_selection_when_flag_cleanup_fails() -> None:
    config = SimpleNamespace(linux_client='cordial')
    selection: list[int] = []
    signals: list[bool] = []

    def index_for(client: str) -> int:
        assert client == 'cordial'
        return 2

    view = cast(
        'SettingsTab',
        SimpleNamespace(
            _config=config,
            _linux_client_combo=SimpleNamespace(
                currentData=lambda: 'mocktail',
                findData=index_for,
                setCurrentIndex=selection.append,
                blockSignals=signals.append,
            ),
            _tray=SimpleNamespace(
                proxy_master=SimpleNamespace(
                    is_running=False,
                    restore_linux_custom_fflags=_override_fail,
                )
            ),
            _refresh_linux_client_status=lambda: None,
        ),
    )
    SettingsTab._on_linux_client_changed(view)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert config.linux_client == 'cordial'
    assert selection == [2]
    assert signals == [True, False]


def test_cached_resource_roots_only_belong_to_selected_linux_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sober = _installation(SOBER_CLIENT, tmp_path)
    cordial = _installation(CORDIAL_CLIENT, tmp_path)
    sober_root = sober.paths.resource_roots[0]
    cordial_root = cordial.paths.resource_roots[0]
    for root in (sober_root, cordial_root):
        root.mkdir(parents=True)

    saved = [sober_root, cordial_root]
    persisted: list[list[Path]] = []
    selected = [cordial]
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: selected[0]
    )

    def empty_resource_dirs(*, include_studio: bool = True) -> list[Path]:
        del include_studio
        return []

    monkeypatch.setattr(platform_linux, 'find_roblox_resource_dirs', empty_resource_dirs)
    monkeypatch.setattr(modification_module, 'load_saved_roblox_dirs', lambda: saved)
    monkeypatch.setattr(modification_module, 'save_saved_roblox_dirs', persisted.append)

    assert modification_module._find_roblox_dirs() == [cordial_root]  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert set(persisted[-1]) == {cordial_root, sober_root}
    monkeypatch.setattr(modification_module, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    monkeypatch.setattr(modification_module, 'MOD_CACHE_DIR', tmp_path / 'cache')
    monkeypatch.setattr(modification_module, 'MODIFICATIONS_JSON', tmp_path / 'mods.json')
    monkeypatch.setattr(modification_module, 'READ_ONLY_STATE_FILE', tmp_path / 'modes.json')
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', list)
    manager = modification_module.ModificationManager()
    manager.write_fast_flags({'grey_sky': True})
    cordial_flags = cordial_root / 'ClientSettings' / 'ClientAppSettings.json'
    sober_flags = sober_root / 'ClientSettings' / 'ClientAppSettings.json'
    assert cordial_flags.is_file()
    assert not sober_flags.exists()

    manager.restore_all()
    selected[0] = sober
    assert modification_module._find_roblox_dirs() == [sober_root]  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert set(persisted[-1]) == {sober_root, cordial_root}
    manager.refresh_roblox_dirs()
    manager.reapply_all()
    assert sober_flags.is_file()
    assert not cordial_flags.exists()


@pytest.mark.parametrize('enabled_after_restart', [False, True])
def test_crashed_custom_flags_cleanup_recovers_other_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled_after_restart: bool
) -> None:
    old = tmp_path / 'old.json'
    new = tmp_path / 'new.json'
    original = '{"FStringUser": "preserved"}\n'
    old.write_text(original, encoding='utf-8')
    new.write_text(original, encoding='utf-8')
    selected = [old]
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: list(selected))
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagCustom': 'True'})
    assert custom_fflags.CustomFFlagModifier(config).prime_linux_client_flags()
    assert json.loads(old.read_text())['FFlagCustom'] == 'True'

    # New modifier has no remembered paths after the previous process crashed
    selected[:] = [new]
    config.custom_fflags_enabled = enabled_after_restart
    reloaded = custom_fflags.CustomFFlagModifier(config)
    assert reloaded.prime_linux_client_flags()
    assert old.read_text() == original
    flags = json.loads(new.read_text())
    assert ('FFlagCustom' in flags) is enabled_after_restart
    assert reloaded.restore_linux_client_flags()
    assert new.read_text() == original


def test_legacy_custom_flag_manifest_recovers_known_client_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    mocktail = MOCKTAIL_CLIENT.paths(home=tmp_path, environ={})
    path = mocktail.config_root / 'fflags.json'
    path.parent.mkdir(parents=True)
    original = '{"FStringUser": "preserved"}\n'
    path.write_text(original, encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [path])
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', tmp_path / 'originals')
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagCustom': 'True'})
    assert custom_fflags.CustomFFlagModifier(config).prime_linux_client_flags()
    manifest = next((tmp_path / 'originals' / 'linux_client_fflags').glob('*.layers.json'))
    previous_format = json.loads(manifest.read_text())
    previous_format.pop('_target')
    manifest.write_text(json.dumps(previous_format), encoding='utf-8')

    # The old manifest has no target path, so recover it through known client paths
    assert custom_fflags.CustomFFlagModifier(config).restore_linux_client_flags()
    assert path.read_text() == original


def test_cordial_launcher_without_engine_is_not_running_roblox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _installation(CORDIAL_CLIENT, tmp_path)
    proc = tmp_path / 'proc'
    for pid in (301, 302):
        directory = proc / str(pid)
        directory.mkdir(parents=True)
        (directory / 'cgroup').write_text(
            f'0::/user.slice/{CORDIAL_CLIENT.cgroup_marker}-test.scope\n', encoding='utf-8'
        )
    processes: dict[str, list[int]] = {'cordial-shell': [301], 'cordial-run': []}

    def process_pids(name: str) -> list[int]:
        return processes.get(name, [])

    monkeypatch.setattr(platform_linux, 'PROC_ROOT', proc)
    monkeypatch.setattr(platform_linux, '_process_pids', process_pids)
    monkeypatch.setattr(
        platform_linux, 'get_selected_linux_client_installation', lambda: installation
    )
    assert not platform_linux.is_roblox_running()
    processes['cordial-run'] = [302]
    assert platform_linux.is_roblox_running()


@pytest.mark.parametrize('layer', ['presets', 'custom'])
@pytest.mark.parametrize('original_exists', [False, True])
def test_native_flag_restoration_preserves_unrelated_external_edits(
    tmp_path: Path, layer: FlagLayer, *, original_exists: bool
) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    if original_exists:
        path.write_text('{"FStringExisting":"keep","FFlagManaged":9}\n', encoding='utf-8')
    assert set_linux_flag_layer(path, stash, layer, {'FFlagManaged': 12})
    edited = json.loads(path.read_text())
    edited['FStringAdded'] = 'external'
    edited['FStringExisting'] = 'edited' if original_exists else 'new'
    path.write_text(json.dumps(edited), encoding='utf-8')
    assert set_linux_flag_layer(path, stash, layer, {})
    result = json.loads(path.read_text())
    assert result['FStringAdded'] == 'external'
    assert result['FStringExisting'] == ('edited' if original_exists else 'new')
    if original_exists:
        assert result['FFlagManaged'] == 9
    else:
        assert 'FFlagManaged' not in result
    assert not managed_linux_flag_paths(stash, layer)


def test_native_flag_reapply_preserves_other_user_changes(tmp_path: Path) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    path.write_text('{"FStringOld":"preserve"}\n', encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagA': 1})
    edited = json.loads(path.read_text())
    edited.pop('FStringOld')
    edited['FFlagExternal'] = True
    path.write_text(json.dumps(edited), encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagB': 2})
    assert json.loads(path.read_text()) == {'FFlagExternal': True, 'FFlagB': 2}
    assert set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text()) == {'FFlagExternal': True}


def test_native_flag_conflicting_managed_edit_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    path.write_text('{"FFlagShared":7}\n', encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagShared': 10})
    edited = {'FFlagShared': 55, 'FStringAdded': 'leave alone'}
    path.write_text(json.dumps(edited), encoding='utf-8')
    assert not set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text()) == edited
    assert managed_linux_flag_paths(stash, 'presets') == {path}
    assert not set_linux_flag_layer(path, stash, 'presets', {'FFlagShared': 33})
    assert json.loads(path.read_text()) == edited

    # Restoring the last Fleasion-managed value permits safe cleanup
    edited['FFlagShared'] = 10
    path.write_text(json.dumps(edited), encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text()) == {'FFlagShared': 7, 'FStringAdded': 'leave alone'}


def test_native_flags_restore_external_value_when_later_managed(tmp_path: Path) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    path.write_text('{"FStringInitial":"base"}', encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagA': 1})
    updated = json.loads(path.read_text())
    updated['FFlagB'] = 23
    path.write_text(json.dumps(updated), encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'custom', {'FFlagB': 50})
    assert set_linux_flag_layer(path, stash, 'custom', {})
    assert json.loads(path.read_text())['FFlagB'] == 23
    assert set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text()) == {'FStringInitial': 'base', 'FFlagB': 23}


def _find_mocktail_index(_key: object) -> int:
    return 2


def _ignore_signal_block(_blocked: bool) -> None:  # ruff: ignore[boolean-type-hint-positional-argument]
    return None


@pytest.mark.parametrize('legacy', [False, True])
def test_preset_cleanup_after_restart_visits_previous_clients(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, legacy: bool
) -> None:
    monkeypatch.setenv('HOME', str(tmp_path))
    mocktail = MOCKTAIL_CLIENT.paths(home=tmp_path, environ={})
    cordial = CORDIAL_CLIENT.paths(home=tmp_path, environ={})
    old = mocktail.config_root / 'fflags.json'
    assert cordial.flatpak_root is not None
    new = cordial.flatpak_root / 'data' / 'cordial' / 'profiles' / 'default' / 'flags.json'
    old.parent.mkdir(parents=True)
    new.parent.mkdir(parents=True)
    old.write_text('{"FStringOriginal":"mocktail"}\n', encoding='utf-8')
    new.write_text('{"FStringOriginal":"cordial"}\n', encoding='utf-8')
    stash = tmp_path / 'stash'
    manager = FastFlagManager([], stash)
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [old])
    assert manager._write_linux_flag_file(old, {'FFlagDebugSkyGray': 'True'})  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    if legacy:
        manifest = linux_flag_stash_paths(stash, old)[0].with_suffix('.layers.json')
        payload = json.loads(manifest.read_text())
        payload.pop('_target')
        payload.pop('_original')
        manifest.write_text(json.dumps(payload), encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [new])
    assert manager._restore_linux_flag_files() == 0  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert json.loads(old.read_text()) == {'FStringOriginal': 'mocktail'}
    assert json.loads(new.read_text()) == {'FStringOriginal': 'cordial'}
    assert not managed_linux_flag_paths(stash, 'presets')


def test_proxy_certificate_discovery_does_not_select_cached_other_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cordial = _installation(CORDIAL_CLIENT, tmp_path)
    sober = _installation(SOBER_CLIENT, tmp_path)
    selected = cordial.paths.resource_roots[0]
    foreign = sober.paths.resource_roots[0]
    selected.mkdir(parents=True)
    foreign.mkdir(parents=True)
    saved: list[list[Path]] = []
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: cordial)

    def selected_resource_dirs(*, include_studio: bool = True) -> list[Path]:
        del include_studio
        return [selected]

    monkeypatch.setattr(platform_linux, 'find_roblox_resource_dirs', selected_resource_dirs)
    monkeypatch.setattr(platform_linux, 'find_linux_ca_bundle_dirs', list)
    monkeypatch.setattr(master, 'load_saved_roblox_dirs', lambda: [foreign])
    monkeypatch.setattr(master, 'save_saved_roblox_dirs', saved.append)
    assert master._find_roblox_dirs() == [selected]  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage, reportUnknownMemberType]
    assert saved == [[selected, foreign]]


def test_mocktail_modification_warning_follows_selected_client(
    modification_manager: modification_module.ModificationManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    selected = ['cordial']
    monkeypatch.setattr(mod_tab_module, '_selected_linux_client_key', lambda: selected[0])
    tab = mod_tab_module.ModificationsTab(modification_manager)
    try:
        tab.show()
        application.processEvents()
        assert not tab._mocktail_note.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
        assert not tab._sober_mesh_warning.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
        tab.hide()
        selected[0] = 'mocktail'
        tab.show()
        application.processEvents()
        assert tab._mocktail_note.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
        assert not tab._sober_mesh_warning.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
        tab.hide()
        selected[0] = 'sober'
        tab.show()
        application.processEvents()
        assert not tab._mocktail_note.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
        assert tab._sober_mesh_warning.isVisible()  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    finally:
        tab.close()


def test_native_flag_cleanup_preserves_external_json_type_changes(tmp_path: Path) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    path.write_text('{"FIntExisting":1}\n', encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagOwned': True})
    values = json.loads(path.read_text())
    values['FIntExisting'] = True
    path.write_text(json.dumps(values), encoding='utf-8')
    assert set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text()) == {'FIntExisting': True}
    assert json.loads(path.read_text())['FIntExisting'] is True


def test_native_flag_conflict_detects_json_type_change(tmp_path: Path) -> None:
    path = tmp_path / 'flags.json'
    stash = tmp_path / 'stash'
    assert set_linux_flag_layer(path, stash, 'presets', {'FFlagOwned': 1})
    path.write_text('{"FFlagOwned":true}', encoding='utf-8')
    assert not set_linux_flag_layer(path, stash, 'presets', {})
    assert json.loads(path.read_text())['FFlagOwned'] is True
    assert managed_linux_flag_paths(stash, 'presets') == {path}


@pytest.mark.parametrize('original_exists', [False, True])
def test_deleted_native_flags_retire_all_layers_without_recreating_file(
    tmp_path: Path, *, original_exists: bool
) -> None:
    path = tmp_path / 'flags.json'
    stash_dir = tmp_path / 'stash'
    if original_exists:
        path.write_text('{"FStringUser":"original"}\n', encoding='utf-8')
    assert set_linux_flag_layer(path, stash_dir, 'presets', {'FFlagPreset': True})
    assert set_linux_flag_layer(path, stash_dir, 'custom', {'FFlagCustom': False})
    stash, absent = linux_flag_stash_paths(stash_dir, path)
    manifest = stash.with_suffix('.layers.json')
    assert manifest.exists()
    path.unlink()

    assert set_linux_flag_layer(path, stash_dir, 'custom', {})
    assert not path.exists()
    assert not stash.exists()
    assert not absent.exists()
    assert not manifest.exists()
    assert not managed_linux_flag_paths(stash_dir, 'presets')
    assert not managed_linux_flag_paths(stash_dir, 'custom')
    assert set_linux_flag_layer(path, stash_dir, 'presets', {})
    assert set_linux_flag_layer(path, stash_dir, 'custom', {})
    assert not path.exists()

    # The regenerated profile can subsequently be managed independently
    path.write_text('{"FStringNew":"client"}', encoding='utf-8')
    assert set_linux_flag_layer(path, stash_dir, 'custom', {'FFlagNew': 3})
    assert set_linux_flag_layer(path, stash_dir, 'custom', {})
    assert json.loads(path.read_text()) == {'FStringNew': 'client'}


def test_deleted_native_flags_can_restore_after_modifier_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'cordial' / 'flags.json'
    path.parent.mkdir(parents=True)
    path.write_text('{"FFlagUser":true}', encoding='utf-8')
    stash_dir = tmp_path / 'originals'
    monkeypatch.setattr(custom_fflags, 'MOD_ORIGINALS_DIR', stash_dir)
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [path])
    config = SimpleNamespace(custom_fflags_enabled=True, custom_fflags={'FFlagTest': 'True'})
    assert custom_fflags.CustomFFlagModifier(config).prime_linux_client_flags()
    path.unlink()

    # No in-memory ownership survives a process restart
    assert custom_fflags.CustomFFlagModifier(config).restore_linux_client_flags()
    assert not path.exists()
    assert not managed_linux_flag_paths(stash_dir, 'custom')


@pytest.mark.parametrize('broken', [False, True])
def test_native_flags_reject_symlink_without_modifying_link_or_target(
    tmp_path: Path, *, broken: bool
) -> None:
    path = tmp_path / 'flags.json'
    target = tmp_path / 'user-managed.json'
    stash_dir = tmp_path / 'stash'
    contents = '{"FFlagUser":true}\n'
    if not broken:
        target.write_text(contents, encoding='utf-8')
    path.symlink_to(target)

    assert not set_linux_flag_layer(path, stash_dir, 'presets', {'FFlagPreset': True})
    assert not set_linux_flag_layer(path, stash_dir, 'custom', {'FFlagCustom': False})
    assert path.is_symlink()
    assert path.readlink() == target
    assert target.exists() is not broken
    if not broken:
        assert target.read_text() == contents
    assert set_linux_flag_layer(path, stash_dir, 'presets', {})
    assert path.is_symlink()
    assert not managed_linux_flag_paths(stash_dir, 'presets')
    assert not managed_linux_flag_paths(stash_dir, 'custom')


def test_replaced_native_flags_symlink_releases_obsolete_ownership(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'flags.json'
    replacement = tmp_path / 'replacement.json'
    replacement.write_text('{"FFlagFromClient":17}', encoding='utf-8')
    path.write_text('{"FFlagOriginal":1}', encoding='utf-8')
    stash_dir = tmp_path / 'stash'
    assert set_linux_flag_layer(path, stash_dir, 'presets', {'FFlagPreset': True})
    assert set_linux_flag_layer(path, stash_dir, 'custom', {'FFlagCustom': True})
    path.unlink()
    path.symlink_to(replacement)

    assert set_linux_flag_layer(path, stash_dir, 'custom', {})
    assert path.is_symlink()
    assert path.resolve() == replacement
    assert json.loads(replacement.read_text()) == {'FFlagFromClient': 17}
    assert not managed_linux_flag_paths(stash_dir, 'presets')
    assert not managed_linux_flag_paths(stash_dir, 'custom')
    assert set_linux_flag_layer(path, stash_dir, 'presets', {})
    assert path.is_symlink()


@pytest.mark.parametrize('was_running', [True, False])
def test_failed_linux_client_switch_restores_proxy_running_state(
    was_running: bool,
) -> None:
    events: list[str] = []
    indexes: list[int] = []
    blocked_signals: list[bool] = []
    config = SimpleNamespace(linux_client='cordial')

    def find_client_index(key: str) -> int:
        return 3 if key == 'cordial' else -1

    proxy = SimpleNamespace(
        is_running=was_running,
        stop=lambda: events.append('stop'),
        restore_linux_custom_fflags=lambda: events.append('restore') or False,
        start=lambda: events.append('start'),
    )
    view = cast(
        'SettingsTab',
        SimpleNamespace(
            _config=config,
            _tray=SimpleNamespace(proxy_master=proxy),
            _linux_client_combo=SimpleNamespace(
                currentData=lambda: 'mocktail',
                findData=find_client_index,
                setCurrentIndex=indexes.append,
                blockSignals=blocked_signals.append,
            ),
            _refresh_linux_client_status=lambda: events.append('refresh'),
        ),
    )

    SettingsTab._on_linux_client_changed(view)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]

    assert events == (
        ['stop', 'restore', 'start', 'refresh'] if was_running else ['restore', 'refresh']
    )
    assert config.linux_client == 'cordial'
    assert indexes == [3]
    assert blocked_signals == [True, False]


def test_preset_restore_conflict_blocks_client_switch_and_restarts_proxy(
    tmp_path: Path,
    modification_manager: modification_module.ModificationManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native = tmp_path / 'cordial' / 'flags.json'
    native.parent.mkdir(parents=True)
    native.write_text('{"FFlagOriginal": true}', encoding='utf-8')
    monkeypatch.setattr(platform_linux, 'find_linux_fflag_files', lambda: [native])
    modification_manager._data['fast_flags_enabled'] = True  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    stash = cast('Path', getattr(modification_manager.fflag_manager, '_stash_dir'))  # ruff: ignore[get-attr-with-constant]
    assert set_linux_flag_layer(native, stash, 'presets', {'FFlagFirst': 1, 'FFlagSecond': 2})
    externally_modified = json.loads(native.read_text())
    externally_modified['FFlagFirst'] = 99
    native.write_text(json.dumps(externally_modified), encoding='utf-8')

    events: list[str] = []
    preferences: list[str] = []
    monkeypatch.setattr(platform_linux, 'set_linux_client_preference', preferences.append)
    proxy = SimpleNamespace(
        is_running=True,
        stop=lambda: events.append('stop'),
        restore_linux_custom_fflags=lambda: events.append('restore_custom') or True,
        start=lambda: events.append('start'),
        prime_custom_fflag_cache=lambda: events.append('prime_custom'),
    )
    selected_indexes: list[int] = []
    config = SimpleNamespace(linux_client='cordial')
    view = cast(
        'SettingsTab',
        SimpleNamespace(
            _config=config,
            _tray=SimpleNamespace(proxy_master=proxy, mod_manager=modification_manager),
            _linux_client_combo=SimpleNamespace(
                currentData=lambda: 'mocktail',
                findData=_find_mocktail_index,
                setCurrentIndex=selected_indexes.append,
                blockSignals=_ignore_signal_block,
            ),
            _refresh_linux_client_status=lambda: events.append('refresh'),
        ),
    )
    SettingsTab._on_linux_client_changed(view)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert config.linux_client == 'cordial'
    assert preferences == []
    assert selected_indexes == [2]
    assert events == ['stop', 'restore_custom', 'start', 'refresh']
    assert json.loads(native.read_text()) == externally_modified
    assert managed_linux_flag_paths(stash, 'presets') == {native}

    externally_modified['FFlagFirst'] = 1
    native.write_text(json.dumps(externally_modified), encoding='utf-8')
    assert modification_manager.restore_all()
    assert json.loads(native.read_text()) == {'FFlagOriginal': True}


@pytest.mark.parametrize('layer', ['custom', 'presets'])
def test_mocktail_refuses_oversized_native_flags_without_changes(
    tmp_path: Path, layer: FlagLayer
) -> None:
    native = MOCKTAIL_CLIENT.paths(home=tmp_path, environ={}).config_root / 'fflags.json'
    native.parent.mkdir(parents=True)
    native.write_text('{"FFlagUser": true}\n', encoding='utf-8')
    initial = native.read_bytes()
    stash = tmp_path / 'stash'
    flags = {f'FStringLong{i:04d}': 'x' * 80 for i in range(1000)}
    assert not set_linux_flag_layer(native, stash, layer, flags)
    assert native.read_bytes() == initial
    assert not managed_linux_flag_paths(stash, layer)
    assert not linux_flag_stash_paths(stash, native)[0].exists()

    assert set_linux_flag_layer(native, stash, layer, {'FFlagSmall': True})
    manifest = linux_flag_stash_paths(stash, native)[0].with_suffix('.layers.json')
    initial_managed = native.read_bytes()
    initial_manifest = manifest.read_bytes()
    assert not set_linux_flag_layer(native, stash, layer, flags)
    assert native.read_bytes() == initial_managed
    assert manifest.read_bytes() == initial_manifest
    assert set_linux_flag_layer(native, stash, layer, {})
    assert native.read_bytes() == initial


def test_mocktail_native_flags_size_limit_checks_utf8_serialized_bytes(tmp_path: Path) -> None:
    native = MOCKTAIL_CLIENT.paths(home=tmp_path, environ={}).config_root / 'fflags.json'
    native.parent.mkdir(parents=True)
    stash = tmp_path / 'stash'
    # Calculate a boundary payload against the writer's actual JSON formatting
    prefix_size = len((json.dumps({'FStringTest': ''}, indent=2) + '\n').encode('utf-8'))
    exact = 'x' * (64 * 1024 - prefix_size)
    assert set_linux_flag_layer(native, stash, 'custom', {'FStringTest': exact})
    assert native.stat().st_size == 64 * 1024
    assert not set_linux_flag_layer(native, stash, 'custom', {'FStringTest': exact + 'x'})
    assert native.stat().st_size == 64 * 1024
    assert set_linux_flag_layer(native, stash, 'custom', {})
    assert not native.exists()
