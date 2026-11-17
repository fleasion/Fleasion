"""Quadra's Rosemary configuration and Fleasion's reversible proxy integration."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fleasion.utils import platform_linux, quadra_proxy_override
from fleasion.utils.linux_clients import (
    ROSEMARY_CLIENT,
    LinuxClientInstallation,
    detect_installed_clients,
    rosemary_guest_home,
)

if TYPE_CHECKING:
    import pytest

PROXY_A = 'http://127.0.0.1:58443'
PROXY_B = 'http://127.0.0.1:58444'


def _settings(path: Path) -> dict[str, object]:
    result: object = tomllib.loads(path.read_text(encoding='utf-8'))
    assert isinstance(result, dict)
    return cast('dict[str, object]', result)


def _variables(path: Path) -> list[dict[str, object]]:
    raw = _settings(path).get('environment_variables', [])
    assert isinstance(raw, list)
    assert all(isinstance(entry, dict) for entry in cast('list[object]', raw))
    return cast('list[dict[str, object]]', raw)


def _write_settings(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '''# User choices must be preserved
runner = 'Butter: Rosemary official'
prefix = 'Quadra'
launch_wrapper = ''

[[environment_variables]]
name = 'CUSTOM'
value = 'original'
enabled = true

[[environment_variables]]
name = 'HTTPS_PROXY'
value = 'http://user.example:1234'
enabled = false

[[environment_variables]]
name = 'ROSEMARY_ENVIRON'
value = "CUSTOM\\nLANG"
enabled = true
''', encoding='utf-8',
    )


def _make_quadra_home(home: Path) -> tuple[Path, Path]:
    bundle = home / '.local/share/quadra/roblox/Roblox.app'
    binary = bundle / 'Contents/MacOS/RobloxPlayer'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'Mach-O')
    cert = bundle / 'Contents/Resources/ssl/cacert.pem'
    cert.parent.mkdir(parents=True)
    cert.write_text('CA', encoding='utf-8')
    runner_dir = home / '.var/app/io.github.microlift.butter/data/butter/runners/rosemary-v0.5.0-official'
    rosemary = runner_dir / 'bin/rosemary'
    rosemary.parent.mkdir(parents=True)
    rosemary.write_bytes(b'binary')
    rosemary.chmod(0o755)
    (runner_dir / 'runner.toml').write_text('''name = 'Rosemary official'
executable = 'bin/rosemary'
''')
    config = home / '.config/quadra/config.toml'
    _write_settings(config)
    desktop = home / '.local/share/applications/org.quadrahq.Quadra.RobloxLinks.desktop'
    desktop.parent.mkdir(parents=True)
    desktop.write_text('[Desktop Entry]\nType=Application\nExec=/usr/bin/quadra --roblox %u\n')
    return bundle, rosemary


def test_quadras_environment_variables_roundtrip(tmp_path: Path) -> None:
    config = tmp_path / 'quadra/config.toml'
    state = tmp_path / 'fleasion/quadra-override.json'
    _write_settings(config)
    original = _settings(config)
    assert quadra_proxy_override.arm(PROXY_A, config, state)
    assert state.stat().st_mode & 0o777 == 0o600
    current = _variables(config)
    assert len(current) == 12
    by_name = {str(entry['name']): entry for entry in current if entry['enabled'] is True}
    assert by_name['HTTPS_PROXY']['value'] == PROXY_A
    assert by_name['no_proxy']['value'] == 'localhost,127.0.0.1,::1'
    assert by_name['ROSEMARY_ENVIRON']['value'] == 'CUSTOM\nLANG\n' + '\n'.join(
        ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy',
         'https_proxy', 'all_proxy', 'NO_PROXY', 'no_proxy')
    )
    assert quadra_proxy_override.clear(config, state)
    assert _settings(config) == original
    assert not state.exists()




def test_empty_array_quadras_default_settings_are_restored(tmp_path: Path) -> None:
    config = tmp_path / 'quadra/config.toml'
    state = tmp_path / 'fleasion/state.json'
    config.parent.mkdir()
    config.write_text("runner = 'Quadra'\nenvironment_variables = []\n")
    original = _settings(config)
    assert quadra_proxy_override.arm(PROXY_A, config, state)
    assert len(_variables(config)) == 9
    assert quadra_proxy_override.clear(config, state)
    assert _settings(config) == original
    assert 'environment_variables = []' in config.read_text()


def test_missing_environment_variables_are_restored(tmp_path: Path) -> None:
    config = tmp_path / 'quadra/config.toml'
    state = tmp_path / 'fleasion/state.json'
    config.parent.mkdir()
    config.write_text("runner = 'Quadra'\n")
    original = _settings(config)
    assert quadra_proxy_override.arm(PROXY_A, config, state)
    assert len(_variables(config)) == 9
    assert quadra_proxy_override.clear(config, state)
    assert _settings(config) == original

def test_quadras_rearm_and_user_changes_survive(tmp_path: Path) -> None:
    config = tmp_path / 'config.toml'
    state = tmp_path / 'state.json'
    _write_settings(config)
    assert quadra_proxy_override.arm(PROXY_A, config, state)
    assert quadra_proxy_override.arm(PROXY_B, config, state)
    assert any(row['value'] == PROXY_B for row in _variables(config))
    source = config.read_text()
    # A replacement value manually adjusted by the user is no longer owned by Fleasion.
    source = source.replace(PROXY_B, 'http://user-proxy.invalid:8000', 1)
    source += '\n# Additional unrelated user setting\n'
    config.write_text(source)
    assert quadra_proxy_override.clear(config, state)
    result = _variables(config)
    assert any(row.get('value') == 'http://user-proxy.invalid:8000' for row in result)
    assert any(row.get('name') == 'CUSTOM' for row in result)
    assert not state.exists()


def test_quadras_invalid_state_and_symlinks_fail_closed(tmp_path: Path) -> None:
    config = tmp_path / 'config.toml'
    state = tmp_path / 'state.json'
    config.write_text('this is invalid TOML = [')
    assert not quadra_proxy_override.arm(PROXY_A, config, state)
    assert not state.exists()
    _write_settings(config)
    state.write_text(json.dumps({'invalid': True}))
    assert not quadra_proxy_override.arm(PROXY_A, config, state)
    assert not quadra_proxy_override.clear(config, state)
    state.unlink()
    config_link = tmp_path / 'link.toml'
    config_link.symlink_to(config)
    assert not quadra_proxy_override.arm(PROXY_A, config_link, state)
    state.symlink_to(config)
    assert not quadra_proxy_override.arm(PROXY_A, config, state)


def test_quadra_auto_detection_and_handler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, rosemary = _make_quadra_home(tmp_path)
    def find_launcher(name: str) -> str | None:
        return '/usr/bin/gtk-launch' if name == 'gtk-launch' else None

    monkeypatch.setattr('fleasion.utils.linux_clients.shutil.which', find_launcher)
    proc = tmp_path / 'proc'
    proc.mkdir()
    installed = detect_installed_clients(
        home=tmp_path, environ={}, which=lambda _name: None, proc_root=proc,
    )
    assert len(installed) == 1
    installation = installed[0]
    assert installation.key == 'rosemary'
    assert installation.executable == rosemary
    assert installation.app_bundle == bundle
    assert installation.quadra_launcher
    assert installation.desktop_launcher
    assert installation.launch_command('roblox-player:test', require_inherited_environment=True) == [
        '/usr/bin/gtk-launch', 'org.quadrahq.Quadra.RobloxLinks', 'roblox-player:test',
    ]
    assert rosemary_guest_home(installation, environ={}) == (
        tmp_path / '.local/share/quadra/prefix/root/Users' / tmp_path.name
    )


def test_quadra_env_proxy_arms_quadra_without_writing_nutmeg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, rosemary = _make_quadra_home(tmp_path)
    config = tmp_path / '.config/quadra/config.toml'
    state = tmp_path / 'fleasion/quadra-state.json'
    nutmeg_config = tmp_path / '.config/nutmeg/config.json'
    nutmeg_state = tmp_path / 'fleasion/nutmeg-state.json'
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        rosemary, bundle, desktop_launcher=True, quadra_launcher=True,
    )
    def installation_for_key(_key: str) -> LinuxClientInstallation:
        return installation

    monkeypatch.setattr(platform_linux, '_installation_for_client_key', installation_for_key)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, 'QUADRA_CONFIG_PATH', config)
    monkeypatch.setattr(platform_linux, 'QUADRA_PROXY_OVERRIDE_STATE', state)
    monkeypatch.setattr(platform_linux, 'NUTMEG_CONFIG_PATH', nutmeg_config)
    monkeypatch.setattr(platform_linux, 'NUTMEG_PROXY_OVERRIDE_STATE', nutmeg_state)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', None)
    original = _settings(config)
    assert platform_linux.set_linux_client_env_proxy_override(PROXY_A, client_key='rosemary')
    assert _variables(config)[-1]['name'] == 'ROSEMARY_ENVIRON'
    assert platform_linux._active_rosemary_proxy_url == PROXY_A  # pyright: ignore[reportPrivateUsage]
    assert not nutmeg_config.exists()
    assert platform_linux.clear_linux_client_env_proxy_override(client_key='rosemary')
    assert _settings(config) == original
    assert not state.exists()


def test_quadras_recovery_after_partial_settings_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / 'quadra/config.toml'
    state = tmp_path / 'state.json'
    config.parent.mkdir()
    config.write_text("runner = 'Quadra'\nenvironment_variables = []\n")
    original = _settings(config)
    original_write = quadra_proxy_override._write  # pyright: ignore[reportPrivateUsage]

    def reject_settings_write(path: Path, content: str, *, mode: int) -> None:
        if path == config:
            msg = 'simulated interrupted write'
            raise OSError(msg)
        original_write(path, content, mode=mode)

    monkeypatch.setattr(quadra_proxy_override, '_write', reject_settings_write)
    assert not quadra_proxy_override.arm(PROXY_A, config, state)
    monkeypatch.setattr(quadra_proxy_override, '_write', original_write)
    assert state.exists()
    assert quadra_proxy_override.clear(config, state)
    assert _settings(config) == original
    assert not state.exists()


def test_quadra_launch_uses_launcher_not_raw_rosemary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, rosemary = _make_quadra_home(tmp_path)
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        rosemary, bundle, desktop_launcher=True, quadra_launcher=True,
    )

    def find_launcher(name: str) -> str | None:
        return '/usr/bin/gtk-launch' if name == 'gtk-launch' else None

    launches: list[tuple[list[str], dict[str, str] | None]] = []

    def record_launch(command: list[str], **kwargs: object) -> None:
        environment: object = kwargs.get('extra_env')
        assert environment is None or isinstance(environment, dict)
        launches.append((command, cast('dict[str, str] | None', environment)))

    monkeypatch.setattr('fleasion.utils.linux_clients.shutil.which', find_launcher)
    monkeypatch.setattr(platform_linux, 'get_selected_linux_client_installation', lambda: installation)
    monkeypatch.setattr(platform_linux, '_standard_user_popen', record_launch)
    monkeypatch.setattr(platform_linux, '_active_rosemary_proxy_url', PROXY_A)
    monkeypatch.setattr(platform_linux, '_rosemary_prefix_path', str(tmp_path / '.local/share/quadra/prefix'))
    assert platform_linux.launch_as_standard_user('roblox-player:test')
    assert launches[0][0] == [
        '/usr/bin/gtk-launch', 'org.quadrahq.Quadra.RobloxLinks', 'roblox-player:test',
    ]
    env = launches[0][1]
    assert env is not None
    assert env['HTTPS_PROXY'] == PROXY_A
    assert 'clientsettings.roblox.com' not in env['NO_PROXY']


def test_quadra_rosemary_default_prefix_is_not_guessed(tmp_path: Path) -> None:
    bundle, rosemary = _make_quadra_home(tmp_path)
    config = tmp_path / '.config/quadra/config.toml'
    config.write_text(config.read_text().replace("prefix = 'Quadra'", "prefix = 'Rosemary default'"))
    installation = LinuxClientInstallation(
        ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
        rosemary, bundle, desktop_launcher=True, quadra_launcher=True,
    )
    assert rosemary_guest_home(installation, environ={}) is None
