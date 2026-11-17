"""macOS-only footer launch and opt-in proxy settings remain visible and safe."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

from fleasion.config import manager as config_module
from fleasion.gui import replacer_config as replacer_module
from fleasion.gui.settings_tab import SettingsTab
from fleasion.proxy import master as proxy_module
from fleasion.utils import macos_rosemary_gate
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.localization import tr

if TYPE_CHECKING:
    from pathlib import Path


def test_production_macos_rosemary_gate_enabled() -> None:
    """Production builds expose the user-consented managed launcher."""
    assert macos_rosemary_gate.MACOS_ROSEMARY_AVAILABLE is True


@pytest.fixture(autouse=True)
def enable_managed_gui_for_internal_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', True)


@pytest.fixture
def config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / 'FleasionNT'
    monkeypatch.setattr(config_module, 'CONFIG_DIR', root)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', root / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', root / 'configs')
    return root


def test_mac_managed_proxy_opt_in_persists_and_native_default_stays_disabled(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.macos_rosemary_enabled is False
        assert config.proxy_features_enabled is False
        # Default proxy preference stays enabled, while interception remains
        # gated until Fleasion explicitly launches a managed Rosemary session
        assert config.settings['proxy_features_enabled'] is True
        assert 'macos_rosemary_enabled' not in config.settings
        config.proxy_features_enabled = True
        assert config.proxy_features_enabled is False
        config.macos_rosemary_enabled = True
        config.proxy_features_enabled = True
        assert config.proxy_features_enabled is True
        reloaded = config_module.ConfigManager()
        assert reloaded.macos_rosemary_enabled is True
        assert reloaded.proxy_features_enabled is True
        reloaded.macos_rosemary_enabled = False
        assert reloaded.proxy_features_enabled is False


def test_production_macos_preserves_existing_proxy_preference_without_migration(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    import json

    file = config_paths / 'settings.json'
    config_paths.mkdir(parents=True)
    file.write_text(json.dumps({'proxy_features_enabled': False}), encoding='utf-8')
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.settings['proxy_features_enabled'] is False
        assert config.proxy_features_enabled is False
        assert config.macos_rosemary_enabled is False
        assert 'macos_rosemary_enabled' not in config.settings
        assert json.loads(file.read_text(encoding='utf-8'))['proxy_features_enabled'] is False


def test_production_mac_proxy_remains_unstarted_before_managed_launch(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        assert config.settings['proxy_features_enabled'] is True
        assert not config.macos_rosemary_enabled
        macos.setattr(proxy_module, 'IS_MACOS', True)
        proxy = proxy_module.ProxyMaster.__new__(proxy_module.ProxyMaster)
        proxy.config_manager = config
        proxy.start()
        assert not hasattr(proxy, '_thread')


def test_production_macos_settings_enable_after_managed_launch_opt_in(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    with monkeypatch.context() as macos:
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        settings = SettingsTab(config)
        toggle = settings.findChild(QCheckBox)
        warning = settings.findChildren(QLabel)
        assert toggle is not None
        assert any(label.text() == tr('settings.proxy.macos_managed') for label in warning)
        proxy_toggle = getattr(settings, '_proxy_features_chk')
        assert not proxy_toggle.isEnabled()
        config.macos_rosemary_enabled = True
        config.proxy_features_enabled = True
        settings.refresh_from_config()
        assert proxy_toggle.isEnabled()
        assert proxy_toggle.isChecked()
        settings.close()
    app.processEvents()


def test_replacer_footer_has_managed_roblox_button_only_on_mac(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    with monkeypatch.context() as macos:
        macos.setattr(replacer_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        window = ReplacerConfigWindow(config)
        button = window.findChild(QPushButton, 'LaunchRobloxWithFleasion')
        assert button is not None
        assert button.text() == tr('macos_rosemary.button')
        assert button.isEnabled()
        assert hasattr(window, '_macos_rosemary_launch')
        window.close()
    with monkeypatch.context() as linux:
        linux.setattr(replacer_module.sys, 'platform', 'linux')
        other = ReplacerConfigWindow(config_module.ConfigManager())
        assert other.findChild(QPushButton, 'LaunchRobloxWithFleasion') is None
        other.close()
    app.processEvents()


def test_dormant_macos_gui_hides_managed_launch(
    monkeypatch: pytest.MonkeyPatch, config_paths: Path
) -> None:
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', False)
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    with monkeypatch.context() as macos:
        macos.setattr(replacer_module.sys, 'platform', 'darwin')
        macos.setattr(config_module.sys, 'platform', 'darwin')
        config = config_module.ConfigManager()
        window = ReplacerConfigWindow(config)
        assert window.findChild(QPushButton, 'LaunchRobloxWithFleasion') is None
        assert not hasattr(window, '_macos_rosemary_launch')
        assert config.proxy_features_enabled is False
        window.close()
    app.processEvents()
