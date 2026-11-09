"""Invalid settings must require an explicit user decision before reset."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from fleasion.app.dialogs.startup import prompt_reset_unreadable_settings
from fleasion.config import manager as config_module
from fleasion.config.manager import ConfigManager, SettingsLoadError

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture
def app() -> Iterator[QApplication]:
    current = QApplication.instance()
    instance = current if isinstance(current, QApplication) else QApplication([])
    yield instance
    instance.processEvents()


def _choose_dialog_button(label: str) -> None:
    modal = QApplication.activeModalWidget()
    assert isinstance(modal, QMessageBox)
    buttons = [button for button in modal.findChildren(QPushButton) if button.text() == label]
    assert len(buttons) == 1
    buttons[0].click()


@pytest.mark.parametrize(
    ('choice', 'expected'),
    [('Exit Without Changes', False), ('Reset Settings to Defaults', True)],
)
def test_invalid_settings_dialog_requires_reset_click(
    app: QApplication, choice: str, expected: bool
) -> None:
    QTimer.singleShot(0, lambda: _choose_dialog_button(choice))
    assert prompt_reset_unreadable_settings('/synthetic/invalid-settings.json') is expected


def test_invalid_file_is_preserved_until_reset_is_explicitly_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    path = tmp_path / 'settings.json'
    damaged = b'{"custom_fflags":{"Important":"True"}, "enabled_configs": ['
    path.write_bytes(damaged)
    with pytest.raises(SettingsLoadError) as failure:
        ConfigManager()
    assert failure.value.path == path

    QTimer.singleShot(0, lambda: _choose_dialog_button('Exit Without Changes'))
    assert not prompt_reset_unreadable_settings(str(failure.value.path))
    assert path.read_bytes() == damaged

    QTimer.singleShot(0, lambda: _choose_dialog_button('Reset Settings to Defaults'))
    assert prompt_reset_unreadable_settings(str(failure.value.path))
    ConfigManager.reset_invalid_settings(failure.value.path)
    settings = json.loads(path.read_text(encoding='utf-8'))
    assert settings['first_time_setup_complete'] is False
    assert settings['enabled_configs'] == []
    assert ConfigManager().enabled_configs == []
    assert not (tmp_path / 'settings.json.bak').exists()


def test_startup_exits_without_writing_when_reset_is_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    from fleasion.app import core

    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    path = tmp_path / 'settings.json'
    damaged = b'{invalid json'
    path.write_bytes(damaged)
    def decline_reset(_path: str) -> bool:
        return False

    monkeypatch.setattr(core, 'prompt_reset_unreadable_settings', decline_reset)
    with pytest.raises(SystemExit) as result:
        core._load_config_manager_with_reset_prompt(suppress_dashboard=False)  # pyright: ignore[reportPrivateUsage]
    assert result.value.code == 1
    assert path.read_bytes() == damaged


def test_startup_only_resets_after_explicit_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    from fleasion.app import core

    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    path = tmp_path / 'settings.json'
    path.write_bytes(b'{invalid json')
    def accept_reset(_path: str) -> bool:
        return True

    monkeypatch.setattr(core, 'prompt_reset_unreadable_settings', accept_reset)
    config = core._load_config_manager_with_reset_prompt(suppress_dashboard=False)  # pyright: ignore[reportPrivateUsage]
    assert config.first_time_setup_complete is False
    assert json.loads(path.read_text(encoding='utf-8'))['enabled_configs'] == []


def test_background_launch_refuses_to_reset_without_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    from fleasion.app import core

    monkeypatch.setattr(config_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(config_module, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(config_module, 'CONFIGS_FOLDER', tmp_path / 'configs')
    path = tmp_path / 'settings.json'
    contents = b'{invalid json'
    path.write_bytes(contents)
    def must_not_prompt(_path: str) -> bool:
        raise AssertionError('Headless launches must never open a destructive prompt')
    monkeypatch.setattr(core, 'prompt_reset_unreadable_settings', must_not_prompt)
    with pytest.raises(SystemExit) as result:
        core._load_config_manager_with_reset_prompt(suppress_dashboard=True)  # pyright: ignore[reportPrivateUsage]
    assert result.value.code == 1
    assert path.read_bytes() == contents
