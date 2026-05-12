"""Helpers for count-aware singular/plural labels."""

from __future__ import annotations

from collections.abc import Sized


def _count(value: int | Sized) -> int:
    """Return an integer count from either a number or a sized object."""
    if isinstance(value, int):
        return value
    return len(value)


def pluralize(count: int | Sized, singular: str, plural: str | None = None) -> str:
    """Return ``singular`` when count is 1, otherwise return the plural form."""
    if _count(count) == 1:
        return singular
    if plural is not None:
        return plural
    if singular.endswith('y') and len(singular) > 1 and singular[-2].lower() not in 'aeiou':
        return f'{singular[:-1]}ies'
    return f'{singular}s'


def format_count(count: int | Sized, singular: str, plural: str | None = None) -> str:
    """Format a count and count-aware noun, e.g. ``1 asset`` or ``2 assets``."""
    total = _count(count)
    return f'{total} {pluralize(total, singular, plural)}'
