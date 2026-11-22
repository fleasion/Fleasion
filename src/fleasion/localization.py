"""Application localization with English as the canonical fallback language."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt

from .translations.en import ENGLISH

if TYPE_CHECKING:
    from collections.abc import Mapping

    from PySide6.QtWidgets import QApplication

DEFAULT_LANGUAGE = 'en'

LANGUAGES: dict[str, str] = {
    'en': 'English',
    'ar': 'العربية',
    'es': 'Español',
    'pt': 'Português (Brasil)',
    'ru': 'Русский',
    'kk': 'Қазақша',
    'tr': 'Türkçe',
    'de': 'Deutsch',
    'nl': 'Nederlands',
    'fr': 'Français',
    'fil': 'Filipino',
    'zh': '简体中文',
    'pl': 'Polski',
    'vi': 'Tiếng Việt',
}

# Each table is a large literal, so only English and the active language load.
# Maps a language code to its fleasion.translations module and table name
_TRANSLATION_SOURCES: dict[str, tuple[str, str]] = {
    DEFAULT_LANGUAGE: ('en', 'ENGLISH'),
    'ar': ('ar', 'ARABIC'),
    'es': ('es', 'SPANISH'),
    'pt': ('pt', 'PORTUGUESE'),
    'ru': ('ru', 'RUSSIAN'),
    'kk': ('kk', 'KAZAKH'),
    'tr': ('tr', 'TURKISH'),
    'de': ('de', 'GERMAN'),
    'nl': ('nl', 'DUTCH'),
    'fr': ('fr', 'FRENCH'),
    'fil': ('fil', 'FILIPINO'),
    'zh': ('zh', 'CHINESE'),
    'pl': ('pl', 'POLISH'),
    'vi': ('vi', 'VIETNAMESE'),
}
_loaded_translations: dict[str, Mapping[str, str]] = {DEFAULT_LANGUAGE: ENGLISH}
_LANGUAGE_ALIASES = {
    'tl': 'fil',
    'tl-ph': 'fil',
    'pt-br': 'pt',
    'zh-cn': 'zh',
    'zh-hans': 'zh',
    'zh-hans-cn': 'zh',
    'zh-hans-sg': 'zh',
    'zh-sg': 'zh',
}
_state = {'language': DEFAULT_LANGUAGE}


def _translations(language: str) -> Mapping[str, str]:
    """Return a language's table, importing its module on first use."""
    table = _loaded_translations.get(language)
    if table is None:
        module_name, table_name = _TRANSLATION_SOURCES[language]
        module = importlib.import_module(f'{__package__}.translations.{module_name}')  # ruff: ignore[banned-api] - Runtime name
        table = cast('Mapping[str, str]', vars(module)[table_name])
        _loaded_translations[language] = table
    return table


def normalize_language(language: Any) -> str:
    """Return a supported language code, falling back to English."""
    value = str(language or '').strip().replace('_', '-').casefold()
    if value in _TRANSLATION_SOURCES:
        return value
    if value in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[value]
    base = value.split('-', 1)[0]
    if base in {'pt', 'zh'}:
        return DEFAULT_LANGUAGE
    return base if base in _TRANSLATION_SOURCES else DEFAULT_LANGUAGE


def available_languages() -> tuple[tuple[str, str], ...]:
    """Return supported language codes and their display names."""
    return tuple((code, LANGUAGES.get(code, code)) for code in _TRANSLATION_SOURCES)


def set_language(language: Any) -> str:
    """Select the active language and return the normalized code."""
    _state['language'] = normalize_language(language)
    return _state['language']


def get_language() -> str:
    return _state['language']


def is_right_to_left() -> bool:
    """Return whether the active language uses right-to-left layout."""
    return _state['language'] == 'ar'


def apply_language_direction(app: QApplication) -> None:
    """Apply the active language's reading direction to the Qt widget tree."""
    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if is_right_to_left() else Qt.LayoutDirection.LeftToRight
    )


def tr(identifier: str, /, **values: Any) -> str:
    """Look up a string identifier with English fallback and safe formatting."""
    english = ENGLISH.get(identifier, identifier)
    table = _translations(_state['language'])
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
    for language in _TRANSLATION_SOURCES:
        value = _translations(language).get(identifier, english)
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


def verbatim(value: Any) -> str:
    """Mark technical/user data that is intentionally not translated."""
    return str(value)
