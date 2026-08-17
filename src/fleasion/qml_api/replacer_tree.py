"""Pure tree operations for replacement-profile organization."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

type ReplacementEntry = dict[str, Any]
type ReplacementPath = tuple[int, ...]


def entry_at_path(
    entries: list[ReplacementEntry],
    path: ReplacementPath,
) -> ReplacementEntry | None:
    current = entries
    entry: ReplacementEntry | None = None
    for index in path:
        if not 0 <= index < len(current):
            return None
        candidate = current[index]
        if not isinstance(candidate, dict):
            return None
        entry = candidate
        children = candidate.get('children', [])
        current = children if isinstance(children, list) else []
    return entry


def parse_path(value: str) -> ReplacementPath:
    try:
        return tuple(int(part) for part in value.split('/') if part != '')
    except ValueError:
        return ()


def format_path(path: ReplacementPath) -> str:
    return '/'.join(str(index) for index in path)


def entries_at_parent_path(
    entries: list[ReplacementEntry],
    parent_path: ReplacementPath,
) -> list[ReplacementEntry] | None:
    if not parent_path:
        return entries
    parent = entry_at_path(entries, parent_path)
    if parent is None or parent.get('type') != 'group':
        return None
    children = parent.get('children')
    if not isinstance(children, list):
        children = []
        parent['children'] = children
    return children


def valid_paths(
    entries: list[ReplacementEntry],
    path_values: Iterable[str],
) -> list[ReplacementPath]:
    paths = {
        path
        for value in path_values
        if (path := parse_path(value)) and entry_at_path(entries, path) is not None
    }
    return sorted(paths)


def prune_descendant_paths(paths: Iterable[ReplacementPath]) -> list[ReplacementPath]:
    pruned: list[ReplacementPath] = []
    for path in sorted(set(paths), key=lambda candidate: (len(candidate), candidate)):
        if not any(path[: len(parent)] == parent for parent in pruned):
            pruned.append(path)
    return sorted(pruned)


def adjust_path_after_removals(
    path: ReplacementPath,
    removed_paths: Iterable[ReplacementPath],
) -> ReplacementPath:
    removed = tuple(removed_paths)
    adjusted: list[int] = []
    for depth, index in enumerate(path):
        parent = path[:depth]
        removed_before = sum(
            1
            for candidate in removed
            if len(candidate) == depth + 1
            and candidate[:depth] == parent
            and candidate[depth] < index
        )
        adjusted.append(index - removed_before)
    return tuple(adjusted)


def iter_entries(
    entries: list[ReplacementEntry],
    parent_path: ReplacementPath = (),
) -> Iterator[tuple[ReplacementPath, ReplacementEntry]]:
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            continue
        path = (*parent_path, index)
        yield path, entry
        children = entry.get('children')
        if entry.get('type') == 'group' and isinstance(children, list):
            yield from iter_entries(children, path)


def iter_groups(
    entries: list[ReplacementEntry],
    parent_path: ReplacementPath = (),
    depth: int = 0,
) -> Iterator[tuple[ReplacementPath, ReplacementEntry, int]]:
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or entry.get('type') != 'group':
            continue
        path = (*parent_path, index)
        yield path, entry, depth
        children = entry.get('children')
        if isinstance(children, list):
            yield from iter_groups(children, path, depth + 1)


def summarize_entries(entries: list[ReplacementEntry]) -> tuple[int, int]:
    profile_count = 0
    enabled_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get('type') == 'group':
            children = entry.get('children')
            if isinstance(children, list):
                child_profiles, child_enabled = summarize_entries(children)
                profile_count += child_profiles
                enabled_count += child_enabled
            continue
        profile_count += 1
        if bool(entry.get('enabled', True)):
            enabled_count += 1
    return profile_count, enabled_count


def set_entries_enabled(entries: list[ReplacementEntry], enabled: bool) -> bool:
    changed = False
    for entry in entries:
        if entry.get('type') == 'group':
            children = entry.get('children')
            if isinstance(children, list):
                changed = set_entries_enabled(children, enabled) or changed
        elif bool(entry.get('enabled', True)) != enabled:
            entry['enabled'] = enabled
            changed = True
    return changed


def entry_ids_for_paths(
    entries: list[ReplacementEntry],
    path_values: Iterable[str],
) -> set[int]:
    entry_ids: set[int] = set()
    for path_value in path_values:
        entry = entry_at_path(entries, parse_path(path_value))
        if entry is not None:
            entry_ids.add(id(entry))
    return entry_ids


def paths_for_entry_ids(
    entries: list[ReplacementEntry],
    entry_ids: set[int],
) -> list[str]:
    return [format_path(path) for path, entry in iter_entries(entries) if id(entry) in entry_ids]
