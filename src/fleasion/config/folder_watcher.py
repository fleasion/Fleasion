"""Watch the Fleasion config folder for externally copied configuration files."""

from ..localization import tr

import os
from collections.abc import Callable
from pathlib import Path

from PyQt6.QtCore import QFileSystemWatcher, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from ..utils import log_buffer
from .manager import MAX_CONFIG_ASSET_FOLDER_DEPTH, ConfigManager

_WATCH_RETRY_INTERVAL_MS = 2000


def _is_ignored_name(name: str) -> bool:
    """Ignore hidden non-JSON files and known editor atomic-save artifacts."""
    folded = name.casefold()
    if not name.startswith('.'):
        return False
    if folded.startswith('.goutputstream-'):
        return True
    return not folded.endswith('.json')


class ConfigFolderWatcher(QObject):
    """Import newly appearing config files while Fleasion is running."""

    configs_changed = pyqtSignal()

    def __init__(
        self,
        config_manager: ConfigManager,
        parent: QObject | None = None,
        *,
        folder: Path | None = None,
        parent_provider: Callable[[], QWidget | None] | None = None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.folder = Path(config_manager.configs_folder if folder is None else folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self._parent_provider = parent_provider
        self._stopped = False
        self._scan_scheduled = False
        self._warning_active = False

        self._known_names = self._scan_names()
        self._pending_names: set[str] = set()
        self._ignored_names: set[str] = set()
        self._warning_names: dict[str, str] = {}
        self._last_import_failure: str | None = None
        self._unwatched_directories: set[str] = set()
        self._watch_fallback_logged = False

        self._scan_timer = QTimer(self)
        self._scan_timer.setSingleShot(True)
        self._scan_timer.timeout.connect(self._run_scheduled_scan)

        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_pending)

        self._watch_retry_timer = QTimer(self)
        self._watch_retry_timer.setInterval(_WATCH_RETRY_INTERVAL_MS)
        self._watch_retry_timer.timeout.connect(self._retry_incomplete_watches)

        self._filesystem_watcher = QFileSystemWatcher(self)
        self._filesystem_watcher.directoryChanged.connect(self._on_directory_changed)
        self._sync_watched_directories()

    def stop(self) -> None:
        """Stop watching and cancel pending import work."""
        if self._stopped:
            return
        self._stopped = True
        self._scan_timer.stop()
        self._retry_timer.stop()
        self._watch_retry_timer.stop()
        self._filesystem_watcher.removePaths(self._filesystem_watcher.directories())

    def _scan_names(self) -> set[str]:
        try:
            return {
                entry.name
                for entry in self.folder.iterdir()
                if entry.is_file() and not _is_ignored_name(entry.name)
            }
        except OSError:
            return set()

    def _directories_to_watch(self) -> set[str]:
        """Return Configs and asset directories through the supported depth."""
        watched = {str(self.folder)}
        try:
            for current_root, directory_names, _file_names in os.walk(self.folder):
                current = Path(current_root)
                try:
                    depth = len(current.relative_to(self.folder).parts)
                except ValueError:
                    directory_names[:] = []
                    continue
                if depth >= MAX_CONFIG_ASSET_FOLDER_DEPTH:
                    directory_names[:] = []
                    continue
                watched.update(str(current / name) for name in directory_names)
        except OSError:
            pass
        return watched

    def _sync_watched_directories(self) -> None:
        """Keep QFileSystemWatcher aligned with the current asset folder tree."""
        desired = self._directories_to_watch()
        current = set(self._filesystem_watcher.directories())
        failed_additions: set[str] = set()
        if removed := sorted(current - desired):
            self._filesystem_watcher.removePaths(removed)
        if added := sorted(desired - current):
            failed_additions.update(self._filesystem_watcher.addPaths(added))
        self._unwatched_directories = failed_additions | (
            desired - set(self._filesystem_watcher.directories())
        )
        if self._unwatched_directories:
            if not self._watch_fallback_logged:
                log_buffer.log(
                    'Config',
                    'Recursive asset watching is incomplete; '
                    f'{len(self._unwatched_directories)} Configs '
                    'directories will use the polling fallback.',
                )
                self._watch_fallback_logged = True
            self._watch_retry_timer.start()
        else:
            self._watch_retry_timer.stop()
            self._watch_fallback_logged = False

    def _retry_incomplete_watches(self) -> None:
        """Keep asset resolution current while OS watcher capacity is exhausted."""
        if self._stopped:
            return
        self.config_manager.invalidate_replacements_cache()
        self._sync_watched_directories()

    def _on_directory_changed(self, _path: str = '') -> None:
        """Invalidate resolved assets and process any root-level config changes."""
        if self._stopped:
            return
        self.config_manager.invalidate_replacements_cache()
        self._schedule_scan()

    def _schedule_scan(self, _path: str = '') -> None:
        if self._stopped or self._scan_scheduled:
            return
        self._scan_scheduled = True
        self._scan_timer.start(0)

    def _run_scheduled_scan(self) -> None:
        self._scan_scheduled = False
        self._sync_watched_directories()
        self._scan()

    def _current_files(self) -> dict[str, Path]:
        try:
            return {
                entry.name: entry
                for entry in self.folder.iterdir()
                if entry.is_file() and not _is_ignored_name(entry.name)
            }
        except OSError:
            return {}

    def _scan(self) -> None:
        if self._stopped:
            return

        files = self._current_files()
        current_names = set(files)
        self._ignored_names.intersection_update(current_names)
        self._pending_names.intersection_update(current_names)

        new_names = sorted((current_names - self._known_names) - self._ignored_names)
        self._known_names = current_names
        for name in new_names:
            self._check_new_file(name, files[name])

        if self._pending_names:
            self._retry_timer.start(1000)

    def _check_new_file(self, name: str, path: Path) -> None:
        # Defer every candidate until it has survived the one-second stability
        # window. Editors write through temporary files and rename them into
        # place; inspecting those files immediately races their atomic save.
        self._pending_names.add(name)

    def _try_import(self, name: str, path: Path) -> bool:
        self._last_import_failure = None
        try:
            destination = self.config_manager.import_config_file(path)
        except FileExistsError:
            self._last_import_failure = 'collision'
            return False
        except OSError:
            self._last_import_failure = 'import'
            return False

        self._known_names.discard(name)
        self._known_names.add(destination.name)
        self._pending_names.discard(name)
        self._warning_names.pop(name, None)
        self.config_manager.refresh_config_names()
        self.configs_changed.emit()
        return True

    def _retry_pending(self) -> None:
        if self._stopped:
            return

        files = self._current_files()
        current_names = set(files)
        self._ignored_names.intersection_update(current_names)
        new_names = sorted((current_names - self._known_names) - self._ignored_names)
        pending = sorted(self._pending_names)
        self._pending_names.clear()
        self._known_names = current_names

        for name in new_names:
            self._check_new_file(name, files[name])

        for name in pending:
            path = files.get(name)
            if path is None or name in self._ignored_names:
                continue

            inspection = self.config_manager.inspect_config_file(path)
            if inspection.status == 'valid':
                if not self._try_import(name, path):
                    self._warning_names[name] = self._last_import_failure or 'import'
            elif inspection.status == 'invalid':
                self._warning_names[name] = 'invalid'
            elif inspection.status == 'unreadable':
                self._warning_names[name] = 'unreadable'
            # Binary files are intentionally ignored after the delayed check.

        if self._pending_names:
            self._retry_timer.start(1000)
        self._show_next_warning()

    def _show_next_warning(self) -> None:
        if self._stopped or self._warning_active or not self._warning_names:
            return

        details = dict(sorted(self._warning_names.items(), key=lambda item: item[0].casefold()))
        self._warning_names.clear()
        names = list(details)
        message = self._warning_message(names, details)

        self._warning_active = True
        try:
            dialog = QMessageBox(self._parent_widget())
            dialog.setWindowTitle(tr('ui.config.folder_watcher.config_import_warning'))
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setText(message)
            ok_button = dialog.addButton(
                tr('ui.config.folder_watcher.ok'), QMessageBox.ButtonRole.AcceptRole
            )
            dialog.setDefaultButton(ok_button)
            result = dialog.exec()
            if result == int(QMessageBox.DialogCode.Accepted):
                self._ignored_names.update(names)
        finally:
            self._warning_active = False

        # The dialog is modal, so files may have disappeared while it was open.
        # A follow-up scan is what makes the gone-then-reappeared rule precise.
        self._schedule_scan()
        QTimer.singleShot(0, self._show_next_warning)

    @staticmethod
    def _warning_message(names: list[str], details: dict[str, str]) -> str:
        quoted = [tr('config_watcher.quoted_name', name=name) for name in names]
        if all(reason == 'invalid' for reason in details.values()):
            if len(quoted) == 1:
                return tr('config_watcher.invalid_one', name=quoted[0])
            if len(quoted) <= 3:
                return tr(
                    'config_watcher.invalid_many',
                    names=', '.join(quoted[:-1]),
                    last=quoted[-1],
                )
            return tr(
                'config_watcher.invalid_many_more',
                names=', '.join(quoted[:3]),
                count=len(quoted) - 3,
            )

        reason_text = {
            'collision': tr('config_watcher.reason.collision'),
            'import': tr('config_watcher.reason.import_failed'),
            'invalid': tr('config_watcher.reason.invalid_json'),
            'unreadable': tr('config_watcher.reason.unreadable'),
        }
        lines = [tr('config_watcher.some_failed')]
        lines.extend(
            tr(
                'config_watcher.detail_line',
                name=name,
                reason=reason_text.get(reason, tr('config_watcher.reason.import_failed')),
            )
            for name, reason in details.items()
        )
        return '\n'.join(lines)

    def _parent_widget(self) -> QWidget | None:
        if self._parent_provider is not None:
            try:
                parent = self._parent_provider()
                if parent is not None:
                    return parent
            except Exception:
                pass
        app = QApplication.instance()
        return app.activeWindow() if app is not None else None
