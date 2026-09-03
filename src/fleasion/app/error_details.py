"""Typed accessors for application error details."""

from __future__ import annotations

from typing import TypeIs, cast

type ErrorDetails = dict[str, object]


def get_int_detail(details: ErrorDetails, key: str, default: int) -> int:
    """Return an integer detail or its default when the value is invalid."""
    value = details.get(key) or default
    if not isinstance(value, str | int | float):
        return default
    try:
        return int(value)
    except ValueError, OverflowError:
        return default


def is_error_details(value: object) -> TypeIs[ErrorDetails]:
    """Return whether a value is a string-keyed error details mapping."""
    if not isinstance(value, dict):
        return False
    details = cast('dict[object, object]', value)
    return all(isinstance(key, str) for key in details)


def is_object_list(value: object) -> TypeIs[list[object]]:
    """Return whether a value is a list of unvalidated objects."""
    return isinstance(value, list)
