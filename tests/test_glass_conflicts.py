"""Conflict checker follows the proxy's precedence rules."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fleasion.gui.conflicts import find_conflicts, rule_mode

if TYPE_CHECKING:
    from collections.abc import Iterator


class _FakeConfigs:
    def __init__(self, configs: dict[str, list[dict[str, Any]]]) -> None:
        self._configs = configs

    def iter_enabled_rules(self) -> Iterator[tuple[str, int, dict[str, Any]]]:
        for order, (name, rules) in enumerate(self._configs.items()):
            for rule in rules:
                if rule.get('enabled', True):
                    yield name, order, rule

    def enabled_configs_signature(self) -> tuple[tuple[str, tuple[int, int] | None], ...]:
        return tuple((name, None) for name in self._configs)


def _rule(ids: list[int], mode: str = 'id', **extra: object) -> dict[str, Any]:
    return {'name': '', 'replace_ids': ids, 'mode': mode, 'enabled': True, **extra}


def test_later_config_wins_within_same_mode() -> None:
    source = _FakeConfigs(
        {
            'a': [_rule([1001], with_id=2005)],
            'b': [_rule([1001], with_id=2006)],
        }
    )
    (conflict,) = find_conflicts(source)
    assert conflict.asset == '1001'
    assert conflict.winner.config == 'b'


def test_local_beats_cdn_beats_id_beats_remove() -> None:
    source = _FakeConfigs(
        {
            'local': [_rule([1007], mode='local', local_path='/x.obj')],
            'cdn': [_rule([1007], mode='cdn', cdn_url='https://example.invalid/x')],
            'swap': [_rule([1007], with_id=2009)],
            'remove': [_rule([1007], mode='remove')],
        }
    )
    (conflict,) = find_conflicts(source)
    assert conflict.winner.config == 'local'
    assert {claim.config for claim in conflict.losers} == {'cdn', 'swap', 'remove'}


def test_empty_targets_are_removals() -> None:
    assert rule_mode(_rule([1001], mode='cdn', cdn_url='')) == 'remove'
    assert rule_mode(_rule([1001], mode='local', local_path='')) == 'remove'
    assert rule_mode(_rule([1001])) == 'remove'
    assert rule_mode({'replace_ids': [1001], 'remove': True}) == 'remove'


def test_disabled_rules_are_ignored() -> None:
    disabled = _rule([1003], with_id=2004)
    disabled['enabled'] = False
    source = _FakeConfigs(
        {
            'a': [_rule([1003], mode='cdn', cdn_url='')],
            'b': [disabled],
            'c': [_rule([1003], with_id=2004)],
        }
    )
    (conflict,) = find_conflicts(source)
    assert conflict.winner.config == 'c'
    assert [claim.config for claim in conflict.losers] == ['a']


def test_same_config_twice_is_not_a_conflict() -> None:
    source = _FakeConfigs({'a': [_rule([1001], with_id=2002), _rule([1001], with_id=2003)]})
    assert find_conflicts(source) == []
