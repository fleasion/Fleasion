"""Replacement profile bridge for the QML dashboard."""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterator
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
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
from .replacer_tree import (
    adjust_path_after_removals as _adjust_path_after_removals,
    entries_at_parent_path as _entries_at_parent_path,
    entry_at_path as _entry_at_path,
    entry_ids_for_paths as _entry_ids_for_paths,
    format_path as _path_to_string,
    iter_entries as _iter_entries,
    iter_groups as _iter_groups,
    parse_path as _path_from_string,
    paths_for_entry_ids as _paths_for_entry_ids,
    prune_descendant_paths as _prune_descendant_paths,
    set_entries_enabled as _set_entries_enabled,
    summarize_entries as _summarize_entries,
    valid_paths as _valid_paths,
)

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_RULE_ROLES: Final = (
    'path',
    'parentPath',
    'kind',
    'depth',
    'name',
    'enabled',
    'state',
    'expanded',
    'childCount',
    'canMoveUp',
    'canMoveDown',
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


@dataclass(slots=True)
class _ReplacerState:
    rules: list[dict[str, Any]]
    selection: tuple[str, ...]


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
        *,
        cache_manager: CacheManager | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager or ConfigManager()
        self._model = DictListModel(_RULE_ROLES, parent=self)
        self._selection = SelectionModel(self)
        self._undo: list[_ReplacerState] = []
        self._redo: list[_ReplacerState] = []
        self._query = ''
        self._draft: dict[str, str] = {}
        self._community_presets = CommunityPresetsApi(  # pyright: ignore[reportCallIssue]
            parent=self,
            cache_manager=cache_manager,
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

    @Property(list, notify=modelChanged)
    def groupDestinations(self) -> list[dict[str, Any]]:  # noqa: N802
        destinations: list[dict[str, Any]] = [
            {'path': '', 'label': 'Profile root', 'name': 'Profile root', 'depth': 0}
        ]
        for path, group, depth in _iter_groups(self._config.replacement_rules):
            name = str(group.get('name', 'Group'))
            destinations.append(
                {
                    'path': _path_to_string(path),
                    'label': f'{"  " * depth}› {name}',
                    'name': name,
                    'depth': depth,
                }
            )
        return destinations

    @Slot()
    def refresh(self) -> None:
        self._config.refresh_config_names()
        self._prune_selection()
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
        rules.append({'type': 'group', 'name': clean_name, 'expanded': True, 'children': []})
        self._save(rules)
        return True

    @Slot(str, str, result=bool)
    def renameGroup(self, path_value: str, name: str) -> bool:  # noqa: N802
        clean_name = name.strip()
        if not clean_name:
            self.errorOccurred.emit('Enter a group name.')
            return False
        path = _path_from_string(path_value)
        rules = deepcopy(self._config.replacement_rules)
        group = _entry_at_path(rules, path)
        if not path or group is None or group.get('type') != 'group':
            self.errorOccurred.emit('The selected group no longer exists.')
            return False
        if group.get('name') == clean_name:
            return True
        group['name'] = clean_name
        self._save(rules)
        return True

    @Slot(str, bool, result=bool)
    def setGroupExpanded(self, path_value: str, expanded: bool) -> bool:  # noqa: N802
        path = _path_from_string(path_value)
        rules = deepcopy(self._config.replacement_rules)
        group = _entry_at_path(rules, path)
        if not path or group is None or group.get('type') != 'group':
            return False
        if bool(group.get('expanded', True)) == expanded:
            return True
        group['expanded'] = expanded
        self._config.replacement_rules = rules
        self._refresh_model()
        return True

    @Slot(bool)
    def setAllGroupsExpanded(self, expanded: bool) -> None:  # noqa: N802
        rules = deepcopy(self._config.replacement_rules)
        changed = False
        for _path, group, _depth in _iter_groups(rules):
            if bool(group.get('expanded', True)) != expanded:
                group['expanded'] = expanded
                changed = True
        if not changed:
            return
        self._config.replacement_rules = rules
        self._refresh_model()

    @Slot(list, result=bool)
    def canGroupEntries(self, path_values: list[str]) -> bool:  # noqa: N802
        paths = _valid_paths(self._config.replacement_rules, path_values)
        if not paths or len({path[:-1] for path in paths}) != 1:
            return False
        return all(
            (entry := _entry_at_path(self._config.replacement_rules, path)) is not None
            and entry.get('type') != 'group'
            for path in paths
        )

    @Slot(list, str, result=bool)
    def groupEntries(self, path_values: list[str], name: str) -> bool:  # noqa: N802
        clean_name = name.strip()
        if not clean_name:
            self.errorOccurred.emit('Enter a group name.')
            return False
        paths = _valid_paths(self._config.replacement_rules, path_values)
        if not self.canGroupEntries([_path_to_string(path) for path in paths]):
            self.errorOccurred.emit('Select replacement rules from the same level to group them.')
            return False

        rules = deepcopy(self._config.replacement_rules)
        selected_ids = _entry_ids_for_paths(rules, self._selection.values())
        parent_path = paths[0][:-1]
        siblings = _entries_at_parent_path(rules, parent_path)
        if siblings is None:
            return False
        selected_indices = sorted(path[-1] for path in paths)
        children = [siblings[index] for index in selected_indices]
        for index in reversed(selected_indices):
            siblings.pop(index)
        insert_at = selected_indices[0]
        siblings.insert(
            insert_at,
            {
                'type': 'group',
                'name': clean_name,
                'expanded': True,
                'children': children,
            },
        )
        selection_after = _paths_for_entry_ids(rules, selected_ids)
        self._save(rules, selection_after=selection_after)
        self.notificationRequested.emit(
            'Group created',
            f'{clean_name} · {len(children)} replacement(s)',
            'success',
        )
        return True

    @Slot(str, bool, result=bool)
    def setEntryEnabled(self, path_value: str, enabled: bool) -> bool:  # noqa: N802
        path = _path_from_string(path_value)
        rules = deepcopy(self._config.replacement_rules)
        entry = _entry_at_path(rules, path)
        if entry is None:
            return False
        if entry.get('type') == 'group':
            _set_entries_enabled(entry.get('children', []), enabled)
        else:
            entry['enabled'] = enabled
        self._save(rules)
        return True

    @Slot(list, bool, result=bool)
    def setEntriesEnabled(self, path_values: list[str], enabled: bool) -> bool:  # noqa: N802
        paths = _prune_descendant_paths(
            _valid_paths(self._config.replacement_rules, path_values)
        )
        if not paths:
            return False
        rules = deepcopy(self._config.replacement_rules)
        changed = False
        for path in paths:
            entry = _entry_at_path(rules, path)
            if entry is None:
                continue
            if entry.get('type') == 'group':
                changed = _set_entries_enabled(entry.get('children', []), enabled) or changed
            elif bool(entry.get('enabled', True)) != enabled:
                entry['enabled'] = enabled
                changed = True
        if not changed:
            return True
        self._save(rules)
        return True

    @Slot(str, int, result=bool)
    def moveEntry(self, path_value: str, direction: int) -> bool:  # noqa: N802
        path = _path_from_string(path_value)
        if not path or direction not in {-1, 1}:
            return False
        rules = deepcopy(self._config.replacement_rules)
        siblings = _entries_at_parent_path(rules, path[:-1])
        if siblings is None:
            return False
        source_index = path[-1]
        target_index = source_index + direction
        if not 0 <= source_index < len(siblings) or not 0 <= target_index < len(siblings):
            return False
        selected_ids = _entry_ids_for_paths(rules, self._selection.values())
        siblings[source_index], siblings[target_index] = siblings[target_index], siblings[source_index]
        selection_after = _paths_for_entry_ids(rules, selected_ids)
        self._save(rules, selection_after=selection_after)
        return True

    @Slot(list, str, int, result=bool)
    def moveEntries(  # noqa: N802
        self,
        path_values: list[str],
        destination_path_value: str,
        insert_index: int,
    ) -> bool:
        source_paths = _prune_descendant_paths(
            _valid_paths(self._config.replacement_rules, path_values)
        )
        if not source_paths:
            return False
        if destination_path_value and not _path_from_string(destination_path_value):
            self.errorOccurred.emit('Choose an existing group or the profile root.')
            return False
        destination_path = _path_from_string(destination_path_value)
        destination = (
            _entry_at_path(self._config.replacement_rules, destination_path)
            if destination_path
            else None
        )
        if destination_path and (destination is None or destination.get('type') != 'group'):
            self.errorOccurred.emit('Choose an existing group or the profile root.')
            return False
        if any(
            destination_path == path or destination_path[: len(path)] == path
            for path in source_paths
        ):
            self.errorOccurred.emit('A group cannot be moved into itself or one of its children.')
            return False

        rules = deepcopy(self._config.replacement_rules)
        selected_ids = _entry_ids_for_paths(rules, self._selection.values())
        moving_entries = [
            entry
            for path in source_paths
            if (entry := _entry_at_path(rules, path)) is not None
        ]
        if not moving_entries:
            return False
        for path in sorted(source_paths, reverse=True):
            siblings = _entries_at_parent_path(rules, path[:-1])
            if siblings is not None and 0 <= path[-1] < len(siblings):
                siblings.pop(path[-1])

        adjusted_destination = _adjust_path_after_removals(destination_path, source_paths)
        destination_entries = _entries_at_parent_path(rules, adjusted_destination)
        if destination_entries is None:
            self.errorOccurred.emit('The destination group is no longer available.')
            return False
        if adjusted_destination:
            destination_group = _entry_at_path(rules, adjusted_destination)
            if destination_group is not None:
                destination_group['expanded'] = True
        resolved_index = (
            len(destination_entries)
            if insert_index < 0
            else max(0, min(insert_index, len(destination_entries)))
        )
        for offset, entry in enumerate(moving_entries):
            destination_entries.insert(resolved_index + offset, entry)

        if rules == self._config.replacement_rules:
            return True
        selection_after = _paths_for_entry_ids(rules, selected_ids)
        self._save(rules, selection_after=selection_after)
        self.notificationRequested.emit(
            'Replacements moved',
            f'{len(moving_entries)} item(s) moved',
            'success',
        )
        return True

    @Slot(list, result=bool)
    def deleteEntries(self, path_values: list[str]) -> bool:  # noqa: N802
        paths = set(_valid_paths(self._config.replacement_rules, path_values))
        if not paths:
            return False
        pruned = _prune_descendant_paths(paths)
        rules = deepcopy(self._config.replacement_rules)
        selected_ids = _entry_ids_for_paths(rules, self._selection.values())
        for path in sorted(pruned, reverse=True):
            siblings = _entries_at_parent_path(rules, path[:-1])
            if siblings is not None and 0 <= path[-1] < len(siblings):
                siblings.pop(path[-1])
        selection_after = _paths_for_entry_ids(rules, selected_ids)
        self._save(rules, selection_after=selection_after)
        return True

    @Slot()
    def undo(self) -> None:
        if not self._undo:
            return
        current = self._capture_state()
        previous = self._undo.pop()
        self._redo.append(current)
        self._apply_state(previous)
        self.historyChanged.emit()

    @Slot()
    def redo(self) -> None:
        if not self._redo:
            return
        current = self._capture_state()
        next_state = self._redo.pop()
        self._undo.append(current)
        self._apply_state(next_state)
        self.historyChanged.emit()

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

    @Slot(list)
    def prepareCachedTargets(self, asset_ids: list[str]) -> None:  # noqa: N802
        unique_ids: list[str] = []
        seen: set[str] = set()
        for value in asset_ids:
            candidate = str(value).strip()
            if not candidate.isdecimal() or candidate == '0':
                continue
            candidate = str(int(candidate))
            if candidate in seen:
                continue
            seen.add(candidate)
            unique_ids.append(candidate)
        if not unique_ids:
            return
        self._draft = {
            'name': f'{len(unique_ids)} cached assets',
            'targets': ', '.join(unique_ids),
            'replacement': '',
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

    def _save(
        self,
        rules: list[dict[str, Any]],
        *,
        selection_after: list[str] | None = None,
    ) -> None:
        current = self._capture_state()
        if rules == current.rules:
            if selection_after is not None:
                self._replace_selection(selection_after)
            return
        self._undo.append(current)
        if len(self._undo) > 80:
            self._undo.pop(0)
        self._redo.clear()
        self._config.replacement_rules = rules
        if selection_after is None:
            self._prune_selection()
        else:
            self._replace_selection(selection_after)
        self.historyChanged.emit()
        self._refresh_model()

    def _refresh_model(self) -> None:
        rules = self._config.replacement_rules
        if self._query:
            all_rows = list(self._flatten(rules, respect_expanded=False))
            rows = [
                row
                for row in all_rows
                if self._query in str(row['searchText']).casefold()
            ]
        else:
            rows = list(self._flatten(rules, respect_expanded=True))
        self._model.replace_items(rows)
        self.modelChanged.emit()

    def _flatten(
        self,
        entries: list[dict[str, Any]],
        parent_path: tuple[int, ...] = (),
        depth: int = 0,
        *,
        respect_expanded: bool,
    ) -> Iterator[dict[str, Any]]:
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            path = (*parent_path, index)
            path_value = _path_to_string(path)
            parent_path_value = _path_to_string(parent_path)
            name = str(entry.get('name', f'Profile {index + 1}'))
            if entry.get('type') == 'group':
                children = entry.get('children', [])
                if not isinstance(children, list):
                    children = []
                profile_count, enabled_count = _summarize_entries(children)
                state = 'mixed'
                if profile_count > 0 and enabled_count == profile_count:
                    state = 'on'
                elif profile_count > 0 and enabled_count == 0:
                    state = 'off'
                expanded = bool(entry.get('expanded', True))
                yield {
                    'path': path_value,
                    'parentPath': parent_path_value,
                    'kind': 'group',
                    'depth': depth,
                    'name': name,
                    'enabled': state == 'on',
                    'state': state,
                    'expanded': expanded,
                    'childCount': len(children),
                    'canMoveUp': index > 0,
                    'canMoveDown': index + 1 < len(entries),
                    'action': 'Group',
                    'replacement': '',
                    'targets': '',
                    'targetCount': profile_count,
                    'searchText': name,
                }
                if not respect_expanded or expanded:
                    yield from self._flatten(
                        children,
                        path,
                        depth + 1,
                        respect_expanded=respect_expanded,
                    )
                continue
            action, replacement = _replacement_display(entry)
            targets = ', '.join(str(value) for value in entry.get('replace_ids', []))
            enabled = bool(entry.get('enabled', True))
            yield {
                'path': path_value,
                'parentPath': parent_path_value,
                'kind': 'rule',
                'depth': depth,
                'name': name,
                'enabled': enabled,
                'state': 'on' if enabled else 'off',
                'expanded': False,
                'childCount': 0,
                'canMoveUp': index > 0,
                'canMoveDown': index + 1 < len(entries),
                'action': action,
                'replacement': replacement,
                'targets': targets,
                'targetCount': len(entry.get('replace_ids', [])),
                'searchText': f'{name} {targets} {action} {replacement}',
            }

    def _capture_state(self) -> _ReplacerState:
        return _ReplacerState(
            rules=deepcopy(self._config.replacement_rules),
            selection=tuple(self._selection.values()),
        )

    def _apply_state(self, state: _ReplacerState) -> None:
        self._config.replacement_rules = deepcopy(state.rules)
        self._replace_selection(list(state.selection))
        self._prune_selection()
        self._refresh_model()

    def _replace_selection(self, path_values: list[str]) -> None:
        current = set(self._selection.values())
        replacement = set(path_values)
        if current == replacement:
            return
        signals_were_blocked = self._selection.blockSignals(True)
        try:
            for path_value in current - replacement:
                self._selection.setSelected(path_value, False)
            for path_value in replacement - current:
                self._selection.setSelected(path_value, True)
        finally:
            self._selection.blockSignals(signals_were_blocked)
        if not signals_were_blocked:
            self._selection.selectionChanged.emit()

    def _prune_selection(self) -> None:
        valid_paths = {
            _path_to_string(path) for path, _entry in _iter_entries(self._config.replacement_rules)
        }
        self._replace_selection(
            [path_value for path_value in self._selection.values() if path_value in valid_paths]
        )

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

    @staticmethod
    def _local_path(url_or_path: str) -> Path:
        url = QUrl(url_or_path)
        if url.isLocalFile():
            return Path(url.toLocalFile())
        return Path(url_or_path).expanduser()
