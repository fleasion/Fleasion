"""Application localization with English as the canonical fallback language."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .translations.en import ENGLISH

DEFAULT_LANGUAGE = 'en'

LANGUAGES: dict[str, str] = {
    'en': 'English',
}

_TRANSLATIONS: dict[str, Mapping[str, str]] = {
    DEFAULT_LANGUAGE: ENGLISH,
}
_current_language = DEFAULT_LANGUAGE


def normalize_language(language: Any) -> str:
    """Return a supported language code, falling back to English."""
    value = str(language or '').strip().replace('_', '-').casefold()
    if value in _TRANSLATIONS:
        return value
    base = value.split('-', 1)[0]
    return base if base in _TRANSLATIONS else DEFAULT_LANGUAGE


def available_languages() -> tuple[tuple[str, str], ...]:
    """Return supported language codes and their display names."""
    return tuple((code, LANGUAGES.get(code, code)) for code in _TRANSLATIONS)


def set_language(language: Any) -> str:
    """Select the active language and return the normalized code."""
    global _current_language
    _current_language = normalize_language(language)
    return _current_language


def get_language() -> str:
    return _current_language


def tr(identifier: str, /, **values: Any) -> str:
    """Look up a string identifier with English fallback and safe formatting."""
    english = ENGLISH.get(identifier, identifier)
    table = _TRANSLATIONS.get(_current_language, ENGLISH)
    text = table.get(identifier, english)
    if not values:
        return text
    try:
        return text.format(**values)
    except KeyError, IndexError, ValueError:
        try:
            return english.format(**values)
        except KeyError, IndexError, ValueError:
            return english


def tr_count(
    count_or_items: int | object,
    singular_identifier: str,
    plural_identifier: str,
    /,
    **values: Any,
) -> str:
    """Translate a count-aware noun phrase using explicit singular/plural identifiers."""
    count = count_or_items if isinstance(count_or_items, int) else len(count_or_items)  # type: ignore[arg-type]
    identifier = singular_identifier if count == 1 else plural_identifier
    return tr(identifier, count=count, **values)


def verbatim(value: Any) -> str:
    """Mark technical/user data that is intentionally not translated."""
    return str(value)
