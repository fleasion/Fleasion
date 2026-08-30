from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from fleasion.config import ConfigFolderWatcher, manager as manager_module

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


class _TestConfigManager(manager_module.ConfigManager):
    @property
    def replacements_cache_populated(self) -> bool:
        return self._all_replacements_cache is not None


class _TestConfigFolderWatcher(ConfigFolderWatcher):
    def scan(self) -> None:
        self._scan()

    def retry_pending(self) -> None:
        self._retry_pending()

    def directory_changed(self, path: str) -> None:
        self._on_directory_changed(path)

    def run_scheduled_scan(self) -> None:
        self._run_scheduled_scan()

    def retry_incomplete_watches(self) -> None:
        self._retry_incomplete_watches()

    @property
    def pending_names(self) -> set[str]:
        return self._pending_names

    @property
    def warning_names(self) -> dict[str, str]:
        return self._warning_names

    @property
    def ignored_names(self) -> set[str]:
        return self._ignored_names

    @property
    def filesystem_watcher(self) -> QFileSystemWatcher:
        return self._filesystem_watcher

    @property
    def unwatched_directories(self) -> set[str]:
        return self._unwatched_directories

    @property
    def watch_retry_timer(self) -> QTimer:
        return self._watch_retry_timer


def _qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        return QApplication([])
    if TYPE_CHECKING:
        assert isinstance(app, QApplication)
    return app


def _manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[_TestConfigManager, Path]:
    config_dir = tmp_path / 'Fleasion'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)
    return _TestConfigManager(), configs_dir


def _patch_message_box(monkeypatch: pytest.MonkeyPatch, messages: list[str]) -> None:
    def record_text(_dialog: QMessageBox, text: str) -> None:
        messages.append(text)

    def accept_dialog(_dialog: QMessageBox) -> int:
        return QMessageBox.DialogCode.Accepted.value

    monkeypatch.setattr(QMessageBox, 'setText', record_text)
    monkeypatch.setattr(QMessageBox, 'exec', accept_dialog)


def test_default_watcher_folder_matches_config_manager_active_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = _TestConfigFolderWatcher(manager)
    try:
        assert watcher.folder == configs_dir
    finally:
        watcher.stop()
    assert app is not None


def test_new_valid_files_are_renamed_and_external_configs_are_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    changed: list[bool] = []
    watcher.configs_changed.connect(lambda: changed.append(True))
    try:
        source = configs_dir / 'Imported profile.txt'
        source.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')

        watcher.scan()
        watcher.retry_pending()

        assert not source.exists()
        assert (configs_dir / 'Imported profile.json').exists()
        assert 'Imported profile' in manager.config_names
        assert changed == [True]
    finally:
        watcher.stop()
    assert app is not None


def test_uppercase_extension_is_normalized_without_overwriting_collisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    existing = configs_dir / 'Existing.json'
    existing.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    messages: list[str] = []
    _patch_message_box(monkeypatch, messages)
    try:
        uppercase = configs_dir / 'Upper.JSON'
        uppercase.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        watcher.scan()
        watcher.retry_pending()
        exact_names = {entry.name for entry in configs_dir.iterdir()}
        assert 'Upper.json' in exact_names
        assert 'Upper.JSON' not in exact_names

        collision = configs_dir / 'Existing.txt'
        collision.write_text(json.dumps({'replacement_rules': [{'name': 'new'}]}), encoding='utf-8')
        watcher.scan()
        watcher.retry_pending()

        assert collision.exists()
        assert json.loads(existing.read_text(encoding='utf-8')) == {'replacement_rules': []}
        assert messages
        assert 'destination name already exists' in messages[-1]
    finally:
        watcher.stop()
    assert app is not None


def test_editor_atomic_save_artifacts_are_not_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    changed: list[bool] = []
    watcher.configs_changed.connect(lambda: changed.append(True))
    try:
        temporary = configs_dir / '.goutputstream-ABCD1234.json'
        temporary.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        hidden_config = configs_dir / '.manually-created.json'
        hidden_config.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        hidden_non_json = configs_dir / '.manually-created.txt'
        hidden_non_json.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')

        watcher.scan()
        watcher.retry_pending()

        assert temporary.exists()
        assert hidden_config.exists()
        assert hidden_non_json.exists()
        assert '.manually-created' in manager.config_names
        assert changed == [True]
        assert not watcher.pending_names
        assert not watcher.warning_names
    finally:
        watcher.stop()
    assert app is not None


def test_existing_files_are_not_processed_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    source = configs_dir / 'already-here.txt'
    source.write_text('not json', encoding='utf-8')
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watcher.scan()
        assert source.exists()
        assert not watcher.pending_names
    finally:
        watcher.stop()
    assert app is not None


def test_asset_directories_are_watched_through_depth_ten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    levels = [f'level-{index}' for index in range(11)]
    deepest = configs_dir.joinpath(*levels)
    deepest.mkdir(parents=True)

    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watched = set(watcher.filesystem_watcher.directories())
        depth_ten = configs_dir.joinpath(*levels[:10])
        depth_eleven = configs_dir.joinpath(*levels)

        assert str(depth_ten) in watched
        assert str(depth_eleven) not in watched
    finally:
        watcher.stop()
    assert app is not None


def test_asset_directory_change_invalidates_replacements_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    asset_dir = configs_dir / 'StickObj'
    asset_dir.mkdir()
    manager.enabled_configs = ['Default']
    manager.replacement_rules = [
        {
            'name': 'Sticks',
            'enabled': True,
            'replace_ids': ['100'],
            'mode': 'local',
            'local_path': '/StickObj/stick.obj',
        }
    ]
    manager.get_all_replacements()
    assert manager.replacements_cache_populated

    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watcher.directory_changed(str(asset_dir))

        assert not manager.replacements_cache_populated
    finally:
        watcher.stop()
    assert app is not None


def test_new_asset_directories_are_added_to_watcher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    new_asset_dir = configs_dir / 'Pack' / 'Models'
    try:
        new_asset_dir.mkdir(parents=True)
        watcher.directory_changed(str(configs_dir))
        watcher.run_scheduled_scan()

        assert str(new_asset_dir) in watcher.filesystem_watcher.directories()
    finally:
        watcher.stop()
    assert app is not None


def test_watcher_registration_failure_uses_cache_invalidation_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    manager.enabled_configs = ['Default']
    manager.get_all_replacements()
    assert manager.replacements_cache_populated

    registration_fails = True
    original_add_paths = QFileSystemWatcher.addPaths

    def controlled_add_paths(watcher: QFileSystemWatcher, paths: list[str]) -> list[str]:
        if registration_fails:
            return list(paths)
        return original_add_paths(watcher, paths)

    monkeypatch.setattr(QFileSystemWatcher, 'addPaths', controlled_add_paths)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        assert watcher.unwatched_directories == {str(configs_dir)}
        assert watcher.watch_retry_timer.isActive()

        watcher.retry_incomplete_watches()

        assert not manager.replacements_cache_populated
        assert watcher.watch_retry_timer.isActive()

        registration_fails = False
        watcher.retry_incomplete_watches()

        assert watcher.unwatched_directories == set()
        assert not watcher.watch_retry_timer.isActive()
        assert str(configs_dir) in watcher.filesystem_watcher.directories()
    finally:
        watcher.stop()
    assert app is not None


def test_incomplete_text_is_retried_and_binary_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        incomplete = configs_dir / 'incomplete.data'
        incomplete.write_text('{"replacement_rules":', encoding='utf-8')
        binary = configs_dir / 'image.png'
        binary.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00binary')

        watcher.scan()
        assert watcher.pending_names == {'incomplete.data', 'image.png'}

        incomplete.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        watcher.retry_pending()

        assert (configs_dir / 'incomplete.json').exists()
        assert binary.exists()
        assert not watcher.warning_names
    finally:
        watcher.stop()
    assert app is not None


def test_invalid_names_are_ignored_until_they_disappear_and_reappear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    messages: list[str] = []
    _patch_message_box(monkeypatch, messages)
    watcher = _TestConfigFolderWatcher(manager, folder=configs_dir)
    try:
        names = ['one.txt', 'two.txt', 'three.txt', 'four.txt']
        for name in names:
            (configs_dir / name).write_text('not a config', encoding='utf-8')

        watcher.scan()
        watcher.retry_pending()

        assert len(messages) == 1
        assert 'and 1 more' in messages[0]
        assert watcher.ignored_names == set(names)

        watcher.scan()
        assert watcher.warning_names == {}

        (configs_dir / 'one.txt').unlink()
        watcher.scan()
        assert 'one.txt' not in watcher.ignored_names

        (configs_dir / 'one.txt').write_text('not a config', encoding='utf-8')
        watcher.scan()
        watcher.retry_pending()
        assert len(messages) == 2
    finally:
        watcher.stop()
    assert app is not None
