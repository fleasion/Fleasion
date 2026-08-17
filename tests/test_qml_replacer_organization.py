from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fleasion.config import manager as manager_module
from fleasion.qml_api.replacer import ReplacerApi


@pytest.fixture
def config_manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / 'FleasionNT'
    monkeypatch.setattr(manager_module, 'CONFIG_DIR', config_dir)
    monkeypatch.setattr(manager_module, 'CONFIG_FILE', config_dir / 'settings.json')
    monkeypatch.setattr(manager_module, 'CONFIGS_FOLDER', config_dir / 'configs')
    return manager_module.ConfigManager()


def _rule(name: str, *, enabled: bool = True) -> dict[str, Any]:
    return {
        'name': name,
        'replace_ids': [len(name)],
        'enabled': enabled,
        'mode': 'remove',
    }


def test_nested_groups_collapse_persist_and_search_hidden_rules(config_manager) -> None:
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'Characters',
            'expanded': False,
            'children': [
                _rule('Face'),
                {
                    'type': 'group',
                    'name': 'Motion',
                    'expanded': True,
                    'children': [_rule('Deep walk', enabled=False)],
                },
            ],
        }
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    assert controller.model.count == 1
    assert controller.model.get(0) == {
        'path': '0',
        'parentPath': '',
        'kind': 'group',
        'depth': 0,
        'name': 'Characters',
        'enabled': False,
        'state': 'mixed',
        'expanded': False,
        'childCount': 2,
        'canMoveUp': False,
        'canMoveDown': False,
        'action': 'Group',
        'replacement': '',
        'targets': '',
        'targetCount': 2,
        'searchText': 'Characters',
    }
    assert not controller.canUndo

    assert controller.setGroupExpanded('0', True)
    assert controller.model.count == 4
    assert config_manager.replacement_rules[0]['expanded'] is True
    assert not controller.canUndo

    controller.setAllGroupsExpanded(False)
    assert controller.model.count == 1
    assert config_manager.replacement_rules[0]['children'][1]['expanded'] is False
    controller.query = 'deep walk'
    assert controller.model.count == 1
    assert controller.model.get(0)['path'] == '0/1/0'
    controller.shutdown()


def test_group_selected_rules_preserves_selection_through_history(config_manager) -> None:
    config_manager.replacement_rules = [_rule('A'), _rule('B'), _rule('C')]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]
    controller.selection.setSelected('0', True)
    controller.selection.setSelected('2', True)

    assert controller.canGroupEntries(['0', '2'])
    assert controller.groupEntries(['0', '2'], 'Grouped')
    assert [entry['name'] for entry in config_manager.replacement_rules] == ['Grouped', 'B']
    assert [entry['name'] for entry in config_manager.replacement_rules[0]['children']] == [
        'A',
        'C',
    ]
    assert controller.selection.values() == ['0/0', '0/1']

    controller.undo()
    assert [entry['name'] for entry in config_manager.replacement_rules] == ['A', 'B', 'C']
    assert controller.selection.values() == ['0', '2']
    controller.redo()
    assert controller.selection.values() == ['0/0', '0/1']

    assert controller.renameGroup('0', 'Favorites')
    assert config_manager.replacement_rules[0]['name'] == 'Favorites'
    assert controller.setEntriesEnabled(['0'], False)
    assert all(
        child['enabled'] is False for child in config_manager.replacement_rules[0]['children']
    )
    assert controller.selection.values() == ['0/0', '0/1']
    controller.shutdown()


def test_move_selected_entries_across_nested_groups_and_reorder(config_manager) -> None:
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'Source',
            'expanded': True,
            'children': [_rule('A'), _rule('B')],
        },
        {
            'type': 'group',
            'name': 'Destination',
            'expanded': True,
            'children': [
                {
                    'type': 'group',
                    'name': 'Nested',
                    'expanded': False,
                    'children': [_rule('C')],
                }
            ],
        },
        _rule('Loose'),
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]
    controller.selection.setSelected('0/0', True)
    controller.selection.setSelected('2', True)

    assert controller.moveEntries(['0/0', '2'], '1/0', -1)
    nested = config_manager.replacement_rules[1]['children'][0]
    assert [entry['name'] for entry in nested['children']] == ['C', 'A', 'Loose']
    assert nested['expanded'] is True
    assert controller.selection.values() == ['1/0/1', '1/0/2']

    controller.undo()
    assert controller.selection.values() == ['0/0', '2']
    controller.redo()
    assert controller.selection.values() == ['1/0/1', '1/0/2']

    controller.selection.setSelected('1/0/2', False)
    assert controller.moveEntry('1/0/1', 1)
    nested = config_manager.replacement_rules[1]['children'][0]
    assert [entry['name'] for entry in nested['children']] == ['C', 'Loose', 'A']
    assert controller.selection.values() == ['1/0/2']

    errors: list[str] = []
    controller.errorOccurred.connect(errors.append)
    before = config_manager.replacement_rules
    assert not controller.moveEntries(['1'], '1/0', -1)
    assert config_manager.replacement_rules == before
    assert errors[-1] == 'A group cannot be moved into itself or one of its children.'
    controller.shutdown()


def test_reorder_and_delete_remap_all_selection_for_undo_redo(config_manager) -> None:
    config_manager.replacement_rules = [_rule('A'), _rule('B'), _rule('C')]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]
    controller.selection.setSelected('0', True)
    controller.selection.setSelected('2', True)

    assert controller.moveEntry('0', 1)
    assert [entry['name'] for entry in config_manager.replacement_rules] == ['B', 'A', 'C']
    assert controller.selection.values() == ['1', '2']
    controller.undo()
    assert controller.selection.values() == ['0', '2']
    controller.redo()
    assert controller.selection.values() == ['1', '2']

    controller.selection.clear()
    controller.selection.setSelected('0', True)
    assert controller.deleteEntries(['0'])
    assert controller.selection.values() == []
    controller.undo()
    assert controller.selection.values() == ['0']
    controller.redo()
    assert controller.selection.values() == []
    controller.shutdown()


def test_group_destinations_include_nested_paths(config_manager) -> None:
    config_manager.replacement_rules = [
        {
            'type': 'group',
            'name': 'Outer',
            'children': [
                {'type': 'group', 'name': 'Inner', 'children': []},
            ],
        }
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    assert [(row['path'], row['name'], row['depth']) for row in controller.groupDestinations] == [
        ('', 'Profile root', 0),
        ('0', 'Outer', 0),
        ('0/0', 'Inner', 1),
    ]
    controller.shutdown()
