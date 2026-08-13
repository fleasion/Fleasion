"""Pure JSON value extraction for community preset payloads."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlparse

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]

_WINDOWS_PATH: Final = re.compile(r'^[A-Za-z]:[\\/]')


@dataclass(frozen=True, slots=True)
class PresetValue:
    """One importable leaf from a community preset JSON document."""

    row_id: str
    path: str
    label: str
    value: int | str
    kind: str

    @property
    def value_text(self) -> str:
        """Return the value formatted for display and QML transport."""
        return str(self.value)

    @property
    def search_text(self) -> str:
        """Return searchable text for the value row."""
        return f'{self.path} {self.value_text}'


def _is_path_or_url(value: str) -> tuple[bool, str]:
    parsed = urlparse(value)
    if parsed.scheme in {'http', 'https', 'file'}:
        return True, 'url' if parsed.scheme in {'http', 'https'} else 'path'
    if value.startswith('/') or _WINDOWS_PATH.match(value) or '/' in value or '\\' in value:
        return True, 'path'
    return False, ''


def _importable_value(value: JsonValue) -> tuple[int | str, str] | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value, 'id'
    if isinstance(value, float):
        return (int(value), 'id') if value.is_integer() else None
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned), 'id'
    except ValueError:
        importable, kind = _is_path_or_url(cleaned)
        return (cleaned, kind) if importable else None


def flatten_preset_values(
    document: JsonValue,
    *,
    maximum_values: int = 100_000,
    maximum_depth: int = 64,
) -> list[PresetValue]:
    """Return importable leaves from a preset document.

    Parameters
    ----------
    document
        Parsed JSON payload to traverse.
    maximum_values
        Safety limit for values returned to the QML model.
    maximum_depth
        Safety limit for nested lists and objects.

    Returns
    -------
    list[PresetValue]
        Importable numeric identifiers, URLs, and file paths.

    Raises
    ------
    ValueError
        If the document exceeds a traversal safety limit.
    """
    if maximum_values < 1 or maximum_depth < 1:
        raise ValueError('Preset traversal limits must be positive.')

    results: list[PresetValue] = []

    def visit(value: JsonValue, segments: tuple[str, ...], depth: int) -> None:
        if depth > maximum_depth:
            raise ValueError('The preset JSON is nested too deeply.')
        if isinstance(value, dict):
            for key, child in value.items():
                visit(child, (*segments, str(key)), depth + 1)
            return
        if isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, (*segments, str(index + 1)), depth + 1)
            return

        converted = _importable_value(value)
        if converted is None:
            return
        if len(results) >= maximum_values:
            raise ValueError('The preset contains too many importable values.')
        imported_value, kind = converted
        path = ' › '.join(segments) if segments else 'Value'
        label = segments[-1] if segments else 'Value'
        digest = hashlib.sha256(f'{len(results)}\0{path}\0{imported_value}'.encode()).hexdigest()[
            :20
        ]
        results.append(
            PresetValue(
                row_id=digest,
                path=path,
                label=label,
                value=imported_value,
                kind=kind,
            )
        )

    visit(document, (), 0)
    return results


__all__ = ['JsonValue', 'PresetValue', 'flatten_preset_values']
