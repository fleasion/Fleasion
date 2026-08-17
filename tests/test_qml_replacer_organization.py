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


def test_sorting_is_hierarchical_and_returns_to_manual_order(config_manager) -> None:
    config_manager.replacement_rules = [
        _rule('Zulu'),
        {
            'type': 'group',
            'name': 'Middle',
            'expanded': True,
            'children': [_rule('Bravo'), _rule('Alpha')],
        },
        _rule('Able'),
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    controller.toggleSort('name')
    assert controller.sortKey == 'name'
    assert not controller.sortDescending
    assert [controller.model.get(index)['name'] for index in range(controller.model.count)] == [
        'Able',
        'Middle',
        'Alpha',
        'Bravo',
        'Zulu',
    ]
    assert controller.model.get(2)['path'] == '1/1'
    assert not controller.manualOrder

    controller.toggleSort('name')
    assert controller.sortDescending
    assert [controller.model.get(index)['name'] for index in range(controller.model.count)] == [
        'Zulu',
        'Middle',
        'Bravo',
        'Alpha',
        'Able',
    ]

    controller.toggleSort('name')
    assert controller.manualOrder
    assert [controller.model.get(index)['name'] for index in range(controller.model.count)] == [
        'Zulu',
        'Middle',
        'Bravo',
        'Alpha',
        'Able',
    ]
    controller.shutdown()


def test_pointer_selection_supports_replace_toggle_and_visible_range(config_manager) -> None:
    config_manager.replacement_rules = [_rule('A'), _rule('B'), _rule('C'), _rule('D')]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    controller.selectEntry('1', False, False)
    assert controller.selection.values() == ['1']

    controller.selectEntry('3', True, False)
    assert controller.selection.values() == ['1', '3']

    controller.selectEntry('0', False, True)
    assert controller.selection.values() == ['0', '1', '2', '3']

    controller.selectEntry('2', False, False)
    controller.selectEntry('0', True, True)
    assert controller.selection.values() == ['0', '1', '2']

    controller.selectAllVisible()
    assert controller.selection.values() == ['0', '1', '2', '3']
    controller.selectForContext('2')
    assert controller.selection.values() == ['0', '1', '2', '3']
    controller.selectForContext('not-a-path')
    assert controller.selection.values() == ['0', '1', '2', '3']
    controller.shutdown()


def test_drop_reorders_same_parent_and_moves_into_group(config_manager) -> None:
    config_manager.replacement_rules = [
        _rule('A'),
        _rule('B'),
        _rule('C'),
        {'type': 'group', 'name': 'Group', 'expanded': False, 'children': []},
    ]
    controller = ReplacerApi(config_manager)  # pyright: ignore[reportCallIssue]

    assert controller.dropEntries(['0'], '2', 'before')
    assert [entry['name'] for entry in config_manager.replacement_rules] == [
        'B',
        'A',
        'C',
        'Group',
    ]

    assert controller.dropEntries(['2'], '3', 'into')
    assert [entry['name'] for entry in config_manager.replacement_rules] == [
        'B',
        'A',
        'Group',
    ]
    assert config_manager.replacement_rules[2]['expanded'] is True
    assert [entry['name'] for entry in config_manager.replacement_rules[2]['children']] == ['C']

    controller.toggleSort('name')
    assert not controller.dropEntries(['0'], '1', 'after')
    assert [entry['name'] for entry in config_manager.replacement_rules] == [
        'B',
        'A',
        'Group',
    ]
    controller.shutdown()
