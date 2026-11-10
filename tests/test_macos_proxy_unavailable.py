"""Native macOS stays usable without any proxy or certificate patching."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QWidget

from fleasion.app import tray as tray_module
from fleasion.app.dialogs.startup import prepare_env_proxy_migration
from fleasion.config import manager as config_module
from fleasion.gui.settings_tab import SettingsTab
from fleasion.localization import tr
from fleasion.proxy import master as proxy_module
from fleasion.proxy import splice_keys
from fleasion.utils import macos_rosemary_gate
from fleasion.utils import macos_rosemary_launcher as managed_launcher

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_dir = tmp_path / 'settings'
    monkeypatch.setattr(config_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    return config_dir / 'settings.json'


def test_macos_proxy_cannot_be_enabled_even_with_stale_settings(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    config_paths.parent.mkdir(parents=True)
    config_paths.write_text(json.dumps({'proxy_features_enabled': True}), encoding='utf-8')

    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.proxy_features_enabled is False
        assert json.loads(config_paths.read_text(encoding='utf-8'))['proxy_features_enabled'] is False

        config.proxy_features_enabled = True
        assert config.settings['proxy_features_enabled'] is False
        config.settings['proxy_features_enabled'] = True
        assert config.proxy_features_enabled is False

    with monkeypatch.context() as linux:
        linux.setattr(config_module.sys, 'platform', 'linux')
        config.proxy_features_enabled = True
        assert config.proxy_features_enabled is True


def test_macos_proxy_master_does_not_start(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = proxy_module.ProxyMaster.__new__(proxy_module.ProxyMaster)
    monkeypatch.setattr(proxy_module, 'IS_MACOS', True)

    proxy.start()

    assert not hasattr(proxy, '_thread')


def test_macos_proxy_settings_are_not_interactive(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])

    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        settings = SettingsTab(config)
        toggle = cast('QCheckBox', getattr(settings, '_proxy_features_chk'))
        mode = cast('QWidget', getattr(settings, '_proxy_mode_combo'))
        upstream = cast('QWidget', getattr(settings, '_upstream_mode_combo'))
        message = cast('QLabel', getattr(settings, '_proxy_warning_label'))
        assert toggle.isChecked() is False
        assert toggle.isEnabled() is False
        assert mode.isEnabled() is False
        assert upstream.isEnabled() is False
        assert message.text() == tr('settings.proxy.macos_unavailable')
        settings.refresh_from_config()
        assert toggle.isEnabled() is False
        settings.close()


def test_macos_tray_cannot_bypass_disabled_proxy(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        calls: list[str] = []

        class StubProxy:
            def start(self) -> None:
                calls.append('start')

            def stop(self) -> None:
                calls.append('stop')

        tray = tray_module.SystemTray.__new__(tray_module.SystemTray)
        tray.config_manager = config
        tray.proxy_master = StubProxy()  # type: ignore[assignment]
        tray.dashboard_window = None
        monkeypatch.setattr(tray, 'update_status', lambda: None)
        monkeypatch.setattr(tray, 'refresh_settings_tab', lambda: None)
        def run_synchronously[T](action: Callable[[], T]) -> Callable[[], T]:
            return action

        monkeypatch.setattr(tray_module, 'run_in_thread', run_synchronously)

        tray.set_proxy_features_enabled(True)

        assert config.proxy_features_enabled is False
        assert calls == ['stop']


def test_macos_skips_obsolete_env_proxy_migration(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        config.proxy_mode = 'hosts'
        assert config.env_proxy_migration_v1_complete is False

        assert prepare_env_proxy_migration(config) is False
        assert config.env_proxy_migration_v1_complete is True
        assert config.proxy_mode == 'hosts'


def test_env_proxy_migration_persists_silently_and_preserves_later_choice(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as linux:
        linux.setattr(config_module.sys, 'platform', 'linux')
        config = config_module.ConfigManager()
        config.first_time_setup_complete = True
        config.proxy_mode = 'hosts'

        assert prepare_env_proxy_migration(config) is True
        assert config.proxy_mode == 'env'
        assert config.env_proxy_migration_v1_complete is True

        reloaded = config_module.ConfigManager()
        assert reloaded.proxy_mode == 'env'
        assert reloaded.env_proxy_migration_v1_complete is True

        reloaded.proxy_mode = 'hosts'
        assert prepare_env_proxy_migration(reloaded) is False
        assert config_module.ConfigManager().proxy_mode == 'hosts'


def test_stale_managed_rosemary_opt_in_is_revoked_until_rendering_is_ready(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    assert macos_rosemary_gate.MACOS_ROSEMARY_AVAILABLE is False
    config_paths.parent.mkdir(parents=True)
    config_paths.write_text(
        json.dumps({'proxy_features_enabled': True, 'macos_rosemary_enabled': True}),
        encoding='utf-8',
    )
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.macos_rosemary_enabled is False
        assert config.proxy_features_enabled is False
        assert json.loads(config_paths.read_text(encoding='utf-8')) == {
            **config.settings,
            'proxy_features_enabled': False,
            'macos_rosemary_enabled': False,
        }
        config.macos_rosemary_enabled = True
        config.proxy_features_enabled = True
        assert config.macos_rosemary_enabled is False
        assert config.proxy_features_enabled is False
        assert config.settings['macos_rosemary_enabled'] is False
        assert config.settings['proxy_features_enabled'] is False


def test_dormant_macos_proxy_rejects_direct_stale_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    assert macos_rosemary_gate.MACOS_ROSEMARY_AVAILABLE is False
    monkeypatch.setattr(proxy_module, 'IS_MACOS', True)
    proxy = proxy_module.ProxyMaster.__new__(proxy_module.ProxyMaster)
    proxy.config_manager = cast('object', SimpleNamespace(macos_rosemary_enabled=True))
    proxy.start()
    assert not hasattr(proxy, '_thread')
    assert proxy._use_splice_transport() is False


def test_dormant_macos_does_not_scan_guest_memory_for_tls_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(splice_keys.sys, 'platform', 'darwin')
    from fleasion.utils import macos_rosemary_memory

    monkeypatch.setattr(
        macos_rosemary_memory,
        'scan_active',
        lambda **_kwargs: pytest.fail('disabled macOS attempted memory IPC'),
    )
    assert splice_keys.KeyProvider()._read('00' * 32) == {}


def test_disabled_startup_does_not_import_macos_rosemary_memory() -> None:
    """Regular app imports must not register experimental memory cleanup."""
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            '-c',
            'import sys; import fleasion.app.core; '
            'assert not ({'
            '"fleasion.utils.macos_rosemary_memory", '
            '"fleasion.utils.macos_rosemary_installer", '
            '"fleasion.utils.macos_rosemary_launcher", '
            '"fleasion.gui.macos_rosemary_launch"'
            '} & set(sys.modules))',
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr


def test_dormant_settings_add_no_new_default_keys(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.proxy_features_enabled is False
        assert 'macos_rosemary_enabled' not in config.settings
        assert 'macos_rosemary_notice_shown' not in config.settings
        assert config_paths.is_file()
        on_disk = json.loads(config_paths.read_text(encoding='utf-8'))
        assert 'macos_rosemary_enabled' not in on_disk
        assert 'macos_rosemary_notice_shown' not in on_disk
