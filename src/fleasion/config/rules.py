"""Validate persisted replacement rule records."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

from fleasion.utils.json_types import as_object_dict, as_object_list

if TYPE_CHECKING:
    from .types import GroupRule, ProfileRule, RuleEntry


def is_rule_entry(value: object) -> TypeGuard[RuleEntry]:
    rule = as_object_dict(value)
    if rule is None:
        return False
    if 'name' in rule and not isinstance(rule['name'], str):
        return False
    if rule.get('type') == 'group':
        return isinstance(rule.get('name'), str) and (
            'expanded' not in rule or isinstance(rule['expanded'], bool)
        )
    return _is_profile_record(rule)


def _is_profile_record(rule: dict[str, object]) -> bool:
    string_fields = ('mode', 'cdn_url', 'local_path')
    bool_fields = ('enabled', 'remove')
    if (
        rule.get('type', 'profile') != 'profile'
        or any(key in rule and not isinstance(rule[key], bool) for key in bool_fields)
        or any(key in rule and not isinstance(rule[key], str) for key in string_fields)
    ):
        return False
    if 'with_id' in rule and (
        not isinstance(rule['with_id'], int) or isinstance(rule['with_id'], bool)
    ):
        return False
    if 'replace_ids' not in rule:
        return True
    ids = as_object_list(rule['replace_ids'])
    return ids is not None and all(
        isinstance(item, int | str) and not isinstance(item, bool) for item in ids
    )


def is_group_rule(entry: object) -> TypeGuard[GroupRule]:
    return is_rule_entry(entry) and entry.get('type') == 'group'


def is_profile_rule(entry: object) -> TypeGuard[ProfileRule]:
    return is_rule_entry(entry) and entry.get('type') != 'group'
