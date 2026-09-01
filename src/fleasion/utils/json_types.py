"""JSON value types and runtime boundary validation."""

from __future__ import annotations

from typing import TypeIs, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def is_json_value(value: object) -> TypeIs[JsonValue]:
    """Return whether a value can be encoded as JSON without custom handling."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in cast('list[object]', value))
    mapping = as_object_dict(value)
    return mapping is not None and all(is_json_value(item) for item in mapping.values())


def as_object_dict(value: object) -> dict[str, object] | None:
    """Return a dictionary with string keys, or ``None`` for another shape."""
    if not isinstance(value, dict):
        return None
    mapping = cast('dict[object, object]', value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast('dict[str, object]', mapping)


def as_json_object(value: object) -> JsonObject | None:
    """Return a recursively validated JSON object, or ``None``."""
    mapping = as_object_dict(value)
    if mapping is None or not all(is_json_value(item) for item in mapping.values()):
        return None
    return cast('JsonObject', mapping)
