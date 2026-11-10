"""macOS-only footer launch and opt-in proxy settings remain visible and safe."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QPushButton

from fleasion.config import manager as config_module
from fleasion.gui import replacer_config as replacer_module
from fleasion.gui.replacer_config import ReplacerConfigWindow
from fleasion.localization import tr

if TYPE_CHECKING:
    from pathlib import Path


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
