"""Validate values read from persisted settings."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fleasion.utils.json_types import JsonValue


def bool_value(value: object, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def int_value(value: object, default: int = 0) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def str_value(value: object, default: str = '') -> str:
    return value if isinstance(value, str) else default


def string_list(value: JsonValue, default: tuple[str, ...] = ()) -> list[str]:
    if not isinstance(value, list):
        return list(default)
    return [item for item in value if isinstance(item, str)]


def parse_int(value: object, default: int = 0) -> int:
    if not isinstance(value, str | int | float):
        return default
    try:
        return int(value)
    except ValueError, OverflowError:
        return default
