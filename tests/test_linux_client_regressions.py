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

from fleasion.gui.settings_tab import SettingsTab
from fleasion.modifications import manager as modification_module
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
from fleasion.utils.linux_fflags import set_linux_flag_layer

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
            args, 0, '[Environment]\nMOCKTAIL_FLEASION_CA_CERTIFICATE=/old/ca.pem\n', ''
        )

    monkeypatch.setattr(platform_linux, '_standard_user_run', run)
    environment = platform_linux._read_mocktail_proxy_environment(installed)  # ruff: ignore[private-member-access]  # pyright: ignore[reportPrivateUsage]
    assert environment['MOCKTAIL_FLEASION_CA_CERTIFICATE'] == '/old/ca.pem'


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
