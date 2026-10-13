"""Resolve modifier conflicts among bindings for the same physical key."""

from __future__ import annotations


def most_specific_names(matches: list[tuple[str, int, int]]) -> list[str]:
    """Return the most specific bindings independently for each key."""
    best_by_key: dict[int, int] = {}
    for _name, modifiers, key in matches:
        best_by_key[key] = max(best_by_key.get(key, 0), modifiers.bit_count())
    return [name for name, modifiers, key in matches if modifiers.bit_count() == best_by_key[key]]
