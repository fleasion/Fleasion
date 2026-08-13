"""Replacement profile bridge for the QML dashboard."""

from __future__ import annotations

import re
import shutil
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtQml import QmlElement

from ..cache.cache_manager import CacheManager
from ..config.manager import (
    CONFIGS_FOLDER,
    ConfigManager,
    local_replacement_path_for_storage,
    resolve_local_replacement_path,
)
from .community_presets import CommunityPresetsApi
from .models import DictListModel, SelectionModel

if TYPE_CHECKING:
    from collections.abc import Iterable

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_RULE_ROLES: Final = (
    'path',
    'kind',
    'depth',
    'name',
    'enabled',
    'state',
    'action',
    'replacement',
    'targets',
    'targetCount',
    'searchText',
)
_TOKEN_SPLIT = re.compile(r'[,;\s]+')
_KNOWN_ASSET_TYPES: Final[dict[str, str]] = {
    value.casefold(): value for value in CacheManager.ASSET_TYPES.values()
}


def _entry_at_path(entries: list[dict[str, Any]], path: tuple[int, ...]) -> dict[str, Any] | None:
    current = entries
    entry: dict[str, Any] | None = None
    for index in path:
        if not 0 <= index < len(current):
            return None
        candidate = current[index]
        if not isinstance(candidate, dict):
            return None
        entry = candidate
        current = candidate.get('children', [])
        if not isinstance(current, list):
            return None
    return entry


def _path_from_string(value: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in value.split('/') if part != '')
    except ValueError:
        return ()


def _replacement_display(rule: dict[str, Any]) -> tuple[str, str]:
    mode = str(rule.get('mode', 'id'))
    if 'remove' in rule and 'mode' not in rule:
        mode = 'remove' if rule.get('remove') else 'id'
    if mode == 'id':
        replacement_id = rule.get('with_id')
        return ('Asset ID', str(replacement_id)) if replacement_id is not None else ('Remove', '')
    if mode == 'cdn':
        return 'CDN URL', str(rule.get('cdn_url', ''))
    if mode == 'local':
        return 'Local file', str(rule.get('local_path', ''))
    if mode == 'remove':
        return 'Remove', ''
    return mode.title(), ''


@QmlElement
class ReplacerApi(QObject):
    """Own replacement profile state and expose safe editing operations."""

    modelChanged = Signal()
    configsChanged = Signal()
    activeConfigChanged = Signal()
    historyChanged = Signal()
    queryChanged = Signal()
    draftChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager or ConfigManager()
        self._model = DictListModel(_RULE_ROLES, parent=self)
        self._selection = SelectionModel(self)
        self._undo: list[list[dict[str, Any]]] = []
        self._redo: list[list[dict[str, Any]]] = []
        self._query = ''
        self._draft: dict[str, str] = {}
        self._community_presets = CommunityPresetsApi(  # pyright: ignore[reportCallIssue]
            parent=self
        )
        self._community_presets.errorOccurred.connect(self.errorOccurred)
        self._community_presets.notificationRequested.connect(self.notificationRequested)
        self._community_presets.draftRequested.connect(self._prepare_community_draft)
        self.refresh()

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Property(QObject, constant=True)
    def selection(self) -> QObject:
        return self._selection

    @Property(QObject, constant=True)
    def communityPresets(self) -> QObject:  # noqa: N802
        return self._community_presets

    @Property(list, notify=configsChanged)
    def configs(self) -> list[str]:
        return self._config.config_names

    @Property(list, notify=configsChanged)
    def enabledConfigs(self) -> list[str]:  # noqa: N802
        return self._config.enabled_configs

    @Property(str, notify=activeConfigChanged)
    def activeConfig(self) -> str:  # noqa: N802
        return self._config.last_config

    @Property(bool, notify=historyChanged)
    def canUndo(self) -> bool:  # noqa: N802
        return bool(self._undo)

    @Property(bool, notify=historyChanged)
    def canRedo(self) -> bool:  # noqa: N802
        return bool(self._redo)

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self.queryChanged.emit()
        self._refresh_model()

    @Property(bool, notify=draftChanged)
    def hasDraft(self) -> bool:  # noqa: N802
        return bool(self._draft)

    @Slot()
    def refresh(self) -> None:
        self._config.refresh_config_names()
        self._refresh_model()
        self.configsChanged.emit()
        self.activeConfigChanged.emit()

    @Slot(str)
    def selectConfig(self, name: str) -> None:  # noqa: N802
        if name not in self._config.config_names or name == self._config.last_config:
            return
        self._config.last_config = name
        self._undo.clear()
        self._redo.clear()
        self._selection.clear()
        self.activeConfigChanged.emit()
        self.historyChanged.emit()
        self._refresh_model()

    @Slot(str, bool)
    def setConfigEnabled(self, name: str, enabled: bool) -> None:  # noqa: N802
        self._config.set_config_enabled(name, enabled)
        self.configsChanged.emit()

    @Slot(str, result=bool)
    def createConfig(self, name: str) -> bool:  # noqa: N802
        clean_name = name.strip()
        if not self._config.create_config(clean_name):
            self.errorOccurred.emit('Choose a unique profile name without reserved characters.')
            return False
        self._config.last_config = clean_name
        self._undo.clear()
        self._redo.clear()
        self.refresh()
        self.notificationRequested.emit('Profile created', clean_name, 'success')
        return True

    @Slot(str, str, result=bool)
    def renameConfig(self, old_name: str, new_name: str) -> bool:  # noqa: N802
        clean_name = new_name.strip()
        if not self._config.rename_config(old_name, clean_name):
            self.errorOccurred.emit('The profile could not be renamed.')
            return False
        self.refresh()
        return True

    @Slot(str, str, result=bool)
    def duplicateConfig(self, source_name: str, new_name: str) -> bool:  # noqa: N802
        clean_name = new_name.strip()
        if not self._config.duplicate_config(source_name, clean_name):
            self.errorOccurred.emit('The profile could not be duplicated.')
            return False
        self._config.last_config = clean_name
        self.refresh()
        return True

    @Slot(str, result=bool)
    def deleteConfig(self, name: str) -> bool:  # noqa: N802
        if not self._config.delete_config(name):
            self.errorOccurred.emit('At least one profile must remain.')
            return False
        self._undo.clear()
        self._redo.clear()
        self.refresh()
        return True

    @Slot(str, str, str, result=bool)
    def addRule(self, name: str, targets: str, replacement: str) -> bool:  # noqa: N802
        rule = self._make_rule(name, targets, replacement)
        if rule is None:
            return False
        rules = deepcopy(self._config.replacement_rules)
        rules.append(rule)
        self._save(rules)
        self.notificationRequested.emit('Replacement added', str(rule['name']), 'success')
        return True

    @Slot(str, str, str, str, result=bool)
    def updateRule(self, path_value: str, name: str, targets: str, replacement: str) -> bool:  # noqa: N802
        path = _path_from_string(path_value)
        rule = self._make_rule(name, targets, replacement)
        if not path or rule is None:
            return False
        rules = deepcopy(self._config.replacement_rules)
        existing = _entry_at_path(rules, path)
        if existing is None or existing.get('type') == 'group':
            return False
        path_parts = list(path)
        rule_index = path_parts.pop()
        rule['enabled'] = bool(existing.get('enabled', True))
        parent_entry = _entry_at_path(rules, tuple(path_parts)) if path_parts else None
        if path_parts and parent_entry is None:
            return False
        parent = rules if parent_entry is None else parent_entry.get('children', [])
        parent[rule_index] = rule
        self._save(rules)
        return True

    @Slot(str, result=bool)
    def addGroup(self, name: str) -> bool:  # noqa: N802
        clean_name = name.strip()
        if not clean_name:
            self.errorOccurred.emit('Enter a group name.')
            return False
        rules = deepcopy(self._config.replacement_rules)
        rules.append({'type': 'group', 'name': clean_name, 'children': []})
        self._save(rules)
        return True

    @Slot(str, bool, result=bool)
    def setEntryEnabled(self, path_value: str, enabled: bool) -> bool:  # noqa: N802
        path = _path_from_string(path_value)
        rules = deepcopy(self._config.replacement_rules)
        entry = _entry_at_path(rules, path)
        if entry is None:
            return False
        if entry.get('type') == 'group':
            self._set_children_enabled(entry.get('children', []), enabled)
        else:
            entry['enabled'] = enabled
        self._save(rules)
        return True

    @Slot(list, result=bool)
    def deleteEntries(self, path_values: list[str]) -> bool:  # noqa: N802
        paths: set[tuple[int, ...]] = {_path_from_string(value) for value in path_values}
        paths.discard(())
        if not paths:
            return False
        pruned = {
            path
            for path in paths
            if not any(path[:index] in paths for index in range(1, len(path)))
        }
        rules = deepcopy(self._config.replacement_rules)
        for path in sorted(pruned, reverse=True):
            path_parts = list(path)
            if not path_parts:
                continue
            entry_index = path_parts.pop()
            parent = rules
            if path_parts:
                parent_entry = _entry_at_path(rules, tuple(path_parts))
                if parent_entry is None:
                    continue
                parent = parent_entry.get('children', [])
            if 0 <= entry_index < len(parent):
                del parent[entry_index]
        self._save(rules)
        self._selection.clear()
        return True

    @Slot()
    def undo(self) -> None:
        if not self._undo:
            return
        current = deepcopy(self._config.replacement_rules)
        previous = self._undo.pop()
        self._redo.append(current)
        self._config.replacement_rules = previous
        self.historyChanged.emit()
        self._refresh_model()

    @Slot()
    def redo(self) -> None:
        if not self._redo:
            return
        current = deepcopy(self._config.replacement_rules)
        next_rules = self._redo.pop()
        self._undo.append(current)
        self._config.replacement_rules = next_rules
        self.historyChanged.emit()
        self._refresh_model()

    @Slot(str, result=bool)
    def importConfig(self, url_or_path: str) -> bool:  # noqa: N802
        source = self._local_path(url_or_path)
        try:
            destination = self._config.import_config_file(source)
        except (OSError, ValueError) as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self._config.last_config = destination.stem
        self.refresh()
        return True

    @Slot(str, str, result=bool)
    def exportConfig(self, name: str, url_or_path: str) -> bool:  # noqa: N802
        destination = self._local_path(url_or_path)
        if destination.is_dir():
            destination /= f'{name}.json'
        try:
            shutil.copy2(CONFIGS_FOLDER / f'{name}.json', destination)
        except OSError as exc:
            self.errorOccurred.emit(str(exc))
            return False
        self.notificationRequested.emit('Profile exported', str(destination), 'success')
        return True

    @Slot(str, result=dict)
    def entry(self, path_value: str) -> dict[str, Any]:
        entry = _entry_at_path(self._config.replacement_rules, _path_from_string(path_value))
        if entry is None:
            return {}
        action, replacement = _replacement_display(entry)
        return {
            'name': str(entry.get('name', '')),
            'targets': ', '.join(str(value) for value in entry.get('replace_ids', [])),
            'replacement': replacement,
            'action': action,
        }

    @Slot(str, bool)
    def prepareCachedAsset(self, asset_id: str, as_replacement: bool) -> None:  # noqa: N802
        clean_id = asset_id.strip()
        if not clean_id:
            return
        self._draft = {
            'name': f'Cached asset {clean_id}',
            'targets': '' if as_replacement else clean_id,
            'replacement': clean_id if as_replacement else '',
        }
        self.draftChanged.emit()

    @Slot(result=dict)
    def takeDraft(self) -> dict[str, str]:  # noqa: N802
        draft = dict(self._draft)
        if self._draft:
            self._draft.clear()
            self.draftChanged.emit()
        return draft

    @Slot()
    def shutdown(self) -> None:
        self._community_presets.shutdown()

    @Slot(str, str, str)
    def _prepare_community_draft(
        self,
        name: str,
        targets: str,
        replacement: str,
    ) -> None:
        self._draft = {
            'name': name,
            'targets': targets,
            'replacement': replacement,
        }
        self.draftChanged.emit()

    def _save(self, rules: list[dict[str, Any]]) -> None:
        self._undo.append(deepcopy(self._config.replacement_rules))
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()
        self._config.replacement_rules = rules
        self.historyChanged.emit()
        self._refresh_model()

    def _refresh_model(self) -> None:
        rows = list(self._flatten(self._config.replacement_rules))
        if self._query:
            rows = [row for row in rows if self._query in str(row['searchText']).casefold()]
        self._model.replace_items(rows)
        self.modelChanged.emit()

    def _flatten(
        self,
        entries: Iterable[dict[str, Any]],
        parent_path: tuple[int, ...] = (),
        depth: int = 0,
    ) -> Iterable[dict[str, Any]]:
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            path = (*parent_path, index)
            path_value = '/'.join(str(value) for value in path)
            name = str(entry.get('name', f'Profile {index + 1}'))
            if entry.get('type') == 'group':
                children = entry.get('children', [])
                enabled_states = [
                    bool(child.get('enabled', True))
                    for child in children
                    if isinstance(child, dict) and child.get('type') != 'group'
                ]
                state = 'mixed'
                if enabled_states and all(enabled_states):
                    state = 'on'
                elif enabled_states and not any(enabled_states):
                    state = 'off'
                yield {
                    'path': path_value,
                    'kind': 'group',
                    'depth': depth,
                    'name': name,
                    'enabled': state == 'on',
                    'state': state,
                    'action': 'Group',
                    'replacement': '',
                    'targets': '',
                    'targetCount': len(enabled_states),
                    'searchText': name,
                }
                yield from self._flatten(children, path, depth + 1)
                continue
            action, replacement = _replacement_display(entry)
            targets = ', '.join(str(value) for value in entry.get('replace_ids', []))
            enabled = bool(entry.get('enabled', True))
            yield {
                'path': path_value,
                'kind': 'rule',
                'depth': depth,
                'name': name,
                'enabled': enabled,
                'state': 'on' if enabled else 'off',
                'action': action,
                'replacement': replacement,
                'targets': targets,
                'targetCount': len(entry.get('replace_ids', [])),
                'searchText': f'{name} {targets} {action} {replacement}',
            }

    def _make_rule(
        self,
        name: str,
        target_text: str,
        replacement_text: str,
    ) -> dict[str, Any] | None:
        targets = self._parse_targets(target_text)
        if not targets:
            self.errorOccurred.emit('Enter at least one asset ID or asset type.')
            return None
        clean_name = name.strip() or f'Profile {len(self._config.replacement_rules) + 1}'
        replacement = replacement_text.strip().strip('"\'')
        rule: dict[str, Any] = {
            'name': clean_name,
            'replace_ids': targets,
            'enabled': True,
        }
        if not replacement:
            rule['mode'] = 'remove'
            return rule
        if replacement.startswith(('https://', 'http://')):
            parsed = urlparse(replacement)
            if not parsed.netloc:
                self.errorOccurred.emit('Enter a valid HTTP or HTTPS URL.')
                return None
            rule.update(mode='cdn', cdn_url=replacement)
            return rule
        try:
            rule.update(mode='id', with_id=int(replacement))
            return rule
        except ValueError:
            pass
        path = self._local_path(replacement)
        if not resolve_local_replacement_path(path).is_file():
            self.errorOccurred.emit('The selected replacement file does not exist.')
            return None
        rule.update(mode='local', local_path=local_replacement_path_for_storage(path))
        return rule

    @staticmethod
    def _parse_targets(value: str) -> list[int | str]:
        parsed: list[int | str] = []
        seen: set[int | str] = set()
        for token in _TOKEN_SPLIT.split(value.strip()):
            if not token:
                continue
            try:
                item: int | str = int(token)
            except ValueError:
                item = _KNOWN_ASSET_TYPES.get(token.casefold(), token)
            if item not in seen:
                seen.add(item)
                parsed.append(item)
        return parsed

    @classmethod
    def _set_children_enabled(cls, entries: list[dict[str, Any]], enabled: bool) -> None:
        for entry in entries:
            if entry.get('type') == 'group':
                cls._set_children_enabled(entry.get('children', []), enabled)
            else:
                entry['enabled'] = enabled

    @staticmethod
    def _local_path(url_or_path: str) -> Path:
        url = QUrl(url_or_path)
        if url.isLocalFile():
            return Path(url.toLocalFile())
        return Path(url_or_path).expanduser()
