"""JSON value types and runtime boundary validation."""

from __future__ import annotations

from typing import TypeIs, cast

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]
type ObjectDict = dict[str, object]


def is_json_value(value: object) -> TypeIs[JsonValue]:
    """Return whether a value can be encoded as JSON without custom handling."""
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(is_json_value(item) for item in cast('list[object]', value))
    mapping = as_object_dict(value)
    return mapping is not None and all(is_json_value(item) for item in mapping.values())


def as_object_dict(value: object) -> ObjectDict | None:
    """Return a dictionary with string keys, or ``None`` for another shape."""
    if not isinstance(value, dict):
        return None
    mapping = cast('dict[object, object]', value)
    if not all(isinstance(key, str) for key in mapping):
        return None
    return cast('ObjectDict', mapping)


def as_object_list(value: object) -> list[object] | None:
    """Return a list without changing its values, or ``None`` for another shape."""
    if not isinstance(value, list):
        return None
    return cast('list[object]', value)


def is_object_dict(value: object) -> TypeIs[ObjectDict]:
    """Return whether a value is a dictionary with string keys."""
    return as_object_dict(value) is not None


def is_object_list(value: object) -> TypeIs[list[object]]:
    """Return whether a value is a list."""
    return isinstance(value, list)


def as_json_object(value: object) -> JsonObject | None:
    """Return a recursively validated JSON object, or ``None``."""
    mapping = as_object_dict(value)
    if mapping is None or not all(is_json_value(item) for item in mapping.values()):
        return None
    return cast('JsonObject', mapping)


def as_json_array(value: object) -> JsonArray | None:
    """Return a recursively validated JSON array, or ``None``."""
    if not isinstance(value, list):
        return None
    values = cast('list[object]', value)
    if not all(is_json_value(item) for item in values):
        return None
    return cast('JsonArray', values)


def require_json_value(value: object) -> JsonValue:
    """Return a JSON value or raise ``TypeError`` for an invalid value."""
    if not is_json_value(value):
        msg = 'Expected a JSON-compatible value'
        raise TypeError(msg)
    return value


def require_object_dict(value: object) -> ObjectDict:
    """Return a string-keyed dictionary or raise ``TypeError``."""
    mapping = as_object_dict(value)
    if mapping is None:
        msg = 'Expected an object with string keys'
        raise TypeError(msg)
    return mapping


def require_object_list(value: object) -> list[object]:
    """Return a list or raise ``TypeError``."""
    values = as_object_list(value)
    if values is None:
        msg = 'Expected a list'
        raise TypeError(msg)
    return values
