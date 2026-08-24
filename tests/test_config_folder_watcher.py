import json
import os

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PyQt6.QtCore import QFileSystemWatcher
from PyQt6.QtWidgets import QApplication, QMessageBox

from fleasion.config import ConfigFolderWatcher
from fleasion.config import manager as manager_module


def _qapp():
    return QApplication.instance() or QApplication([])


def _manager(tmp_path, monkeypatch):
    config_dir = tmp_path / 'Fleasion'
    configs_dir = config_dir / 'configs'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', configs_dir)
    return manager_module.ConfigManager(), configs_dir


def test_default_watcher_folder_matches_config_manager_active_folder(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = ConfigFolderWatcher(manager)
    try:
        assert watcher.folder == configs_dir
    finally:
        watcher.stop()
    assert app is not None


def test_new_valid_files_are_renamed_and_external_configs_are_visible(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    changed = []
    watcher.configs_changed.connect(lambda: changed.append(True))
    try:
        source = configs_dir / 'Imported profile.txt'
        source.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')

        watcher._scan()
        watcher._retry_pending()

        assert not source.exists()
        assert (configs_dir / 'Imported profile.json').exists()
        assert 'Imported profile' in manager.config_names
        assert changed == [True]
    finally:
        watcher.stop()
    assert app is not None


def test_uppercase_extension_is_normalized_without_overwriting_collisions(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    existing = configs_dir / 'Existing.json'
    existing.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    messages = []
    monkeypatch.setattr(QMessageBox, 'setText', lambda dialog, text: messages.append(text))
    monkeypatch.setattr(QMessageBox, 'exec', lambda _dialog: QMessageBox.DialogCode.Accepted.value)
    try:
        uppercase = configs_dir / 'Upper.JSON'
        uppercase.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        watcher._scan()
        watcher._retry_pending()
        exact_names = {entry.name for entry in configs_dir.iterdir()}
        assert 'Upper.json' in exact_names
        assert 'Upper.JSON' not in exact_names

        collision = configs_dir / 'Existing.txt'
        collision.write_text(json.dumps({'replacement_rules': [{'name': 'new'}]}), encoding='utf-8')
        watcher._scan()
        watcher._retry_pending()

        assert collision.exists()
        assert json.loads(existing.read_text(encoding='utf-8')) == {'replacement_rules': []}
        assert messages and 'destination name already exists' in messages[-1]
    finally:
        watcher.stop()
    assert app is not None


def test_editor_atomic_save_artifacts_are_not_imported(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    changed = []
    watcher.configs_changed.connect(lambda: changed.append(True))
    try:
        temporary = configs_dir / '.goutputstream-ABCD1234.json'
        temporary.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        hidden_config = configs_dir / '.manually-created.json'
        hidden_config.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        hidden_non_json = configs_dir / '.manually-created.txt'
        hidden_non_json.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')

        watcher._scan()
        watcher._retry_pending()

        assert temporary.exists()
        assert hidden_config.exists()
        assert hidden_non_json.exists()
        assert '.manually-created' in manager.config_names
        assert changed == [True]
        assert not watcher._pending_names
        assert not watcher._warning_names
    finally:
        watcher.stop()
    assert app is not None


def test_existing_files_are_not_processed_at_startup(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    source = configs_dir / 'already-here.txt'
    source.write_text('not json', encoding='utf-8')
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watcher._scan()
        assert source.exists()
        assert not watcher._pending_names
    finally:
        watcher.stop()
    assert app is not None


def test_asset_directories_are_watched_through_depth_ten(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    levels = [f'level-{index}' for index in range(11)]
    deepest = configs_dir.joinpath(*levels)
    deepest.mkdir(parents=True)

    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watched = set(watcher._filesystem_watcher.directories())
        depth_ten = configs_dir.joinpath(*levels[:10])
        depth_eleven = configs_dir.joinpath(*levels)

        assert str(depth_ten) in watched
        assert str(depth_eleven) not in watched
    finally:
        watcher.stop()
    assert app is not None


def test_asset_directory_change_invalidates_replacements_cache(tmp_path, monkeypatch):
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
    assert manager._all_replacements_cache is not None

    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        watcher._on_directory_changed(str(asset_dir))

        assert manager._all_replacements_cache is None
    finally:
        watcher.stop()
    assert app is not None


def test_new_asset_directories_are_added_to_watcher(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    new_asset_dir = configs_dir / 'Pack' / 'Models'
    try:
        new_asset_dir.mkdir(parents=True)
        watcher._on_directory_changed(str(configs_dir))
        watcher._run_scheduled_scan()

        assert str(new_asset_dir) in watcher._filesystem_watcher.directories()
    finally:
        watcher.stop()
    assert app is not None


def test_watcher_registration_failure_uses_cache_invalidation_fallback(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    manager.enabled_configs = ['Default']
    manager.get_all_replacements()
    assert manager._all_replacements_cache is not None

    registration_fails = True
    original_add_paths = QFileSystemWatcher.addPaths

    def controlled_add_paths(watcher, paths):
        if registration_fails:
            return list(paths)
        return original_add_paths(watcher, paths)

    monkeypatch.setattr(QFileSystemWatcher, 'addPaths', controlled_add_paths)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        assert watcher._unwatched_directories == {str(configs_dir)}
        assert watcher._watch_retry_timer.isActive()

        watcher._retry_incomplete_watches()

        assert manager._all_replacements_cache is None
        assert watcher._watch_retry_timer.isActive()

        registration_fails = False
        watcher._retry_incomplete_watches()

        assert watcher._unwatched_directories == set()
        assert not watcher._watch_retry_timer.isActive()
        assert str(configs_dir) in watcher._filesystem_watcher.directories()
    finally:
        watcher.stop()
    assert app is not None


def test_incomplete_text_is_retried_and_binary_is_ignored(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        incomplete = configs_dir / 'incomplete.data'
        incomplete.write_text('{"replacement_rules":', encoding='utf-8')
        binary = configs_dir / 'image.png'
        binary.write_bytes(b'\x89PNG\r\n\x1a\n\x00\x00binary')

        watcher._scan()
        assert watcher._pending_names == {'incomplete.data', 'image.png'}

        incomplete.write_text(json.dumps({'replacement_rules': []}), encoding='utf-8')
        watcher._retry_pending()

        assert (configs_dir / 'incomplete.json').exists()
        assert binary.exists()
        assert not watcher._warning_names
    finally:
        watcher.stop()
    assert app is not None


def test_invalid_names_are_ignored_until_they_disappear_and_reappear(tmp_path, monkeypatch):
    app = _qapp()
    manager, configs_dir = _manager(tmp_path, monkeypatch)
    messages = []
    monkeypatch.setattr(QMessageBox, 'setText', lambda dialog, text: messages.append(text))
    monkeypatch.setattr(QMessageBox, 'exec', lambda _dialog: QMessageBox.DialogCode.Accepted.value)
    watcher = ConfigFolderWatcher(manager, folder=configs_dir)
    try:
        names = ['one.txt', 'two.txt', 'three.txt', 'four.txt']
        for name in names:
            (configs_dir / name).write_text('not a config', encoding='utf-8')

        watcher._scan()
        watcher._retry_pending()

        assert len(messages) == 1
        assert 'and 1 more' in messages[0]
        assert watcher._ignored_names == set(names)

        watcher._scan()
        assert watcher._warning_names == {}

        (configs_dir / 'one.txt').unlink()
        watcher._scan()
        assert 'one.txt' not in watcher._ignored_names

        (configs_dir / 'one.txt').write_text('not a config', encoding='utf-8')
        watcher._scan()
        watcher._retry_pending()
        assert len(messages) == 2
    finally:
        watcher.stop()
    assert app is not None
