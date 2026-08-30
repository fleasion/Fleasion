"""Application localization with English as the canonical fallback language."""

from __future__ import annotations

from collections.abc import Mapping  # ruff: ignore[typing-only-standard-library-import]
from typing import Any

from .translations.de import GERMAN
from .translations.en import ENGLISH
from .translations.es import SPANISH
from .translations.fr import FRENCH
from .translations.kk import KAZAKH
from .translations.pl import POLISH
from .translations.pt import PORTUGUESE
from .translations.ru import RUSSIAN
from .translations.tr import TURKISH
from .translations.zh import CHINESE

DEFAULT_LANGUAGE = 'en'

LANGUAGES: dict[str, str] = {
    'en': 'English',
    'es': 'Español',
    'pt': 'Português (Brasil)',
    'ru': 'Русский',
    'kk': 'Қазақша',
    'tr': 'Türkçe',
    'de': 'Deutsch',
    'fr': 'Français',
    'zh': '简体中文',
    'pl': 'Polski',
}

_TRANSLATIONS: dict[str, Mapping[str, str]] = {
    DEFAULT_LANGUAGE: ENGLISH,
    'es': SPANISH,
    'pt': PORTUGUESE,
    'ru': RUSSIAN,
    'kk': KAZAKH,
    'tr': TURKISH,
    'de': GERMAN,
    'fr': FRENCH,
    'zh': CHINESE,
    'pl': POLISH,
}
_LANGUAGE_ALIASES = {
    'pt-br': 'pt',
    'zh-cn': 'zh',
    'zh-hans': 'zh',
    'zh-hans-cn': 'zh',
    'zh-hans-sg': 'zh',
    'zh-sg': 'zh',
}
_current_language = DEFAULT_LANGUAGE


def normalize_language(language: Any) -> str:  # ruff: ignore[any-type]
    """Return a supported language code, falling back to English."""
    value = str(language or '').strip().replace('_', '-').casefold()
    if value in _TRANSLATIONS:
        return value
    if value in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[value]
    base = value.split('-', 1)[0]
    if base in {'pt', 'zh'}:
        return DEFAULT_LANGUAGE
    return base if base in _TRANSLATIONS else DEFAULT_LANGUAGE


def available_languages() -> tuple[tuple[str, str], ...]:
    """Return supported language codes and their display names."""
    return tuple((code, LANGUAGES.get(code, code)) for code in _TRANSLATIONS)


def set_language(language: Any) -> str:  # ruff: ignore[any-type]
    """Select the active language and return the normalized code."""
    global _current_language  # ruff: ignore[global-statement]
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


def translation_values(identifier: str) -> tuple[str, ...]:
    """Return every registered language's value for an identifier, without duplicates."""
    english = ENGLISH.get(identifier, identifier)
    values: list[str] = []
    for table in _TRANSLATIONS.values():
        value = table.get(identifier, english)
        if value not in values:
            values.append(value)
    return tuple(values)


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


def verbatim(value: Any) -> str:  # ruff: ignore[any-type]
    """Mark technical/user data that is intentionally not translated."""
    return str(value)
