"""Application localization with English as the canonical fallback language."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QCoreApplication, QTranslator

from .translations.en import ENGLISH
from .translations.es import SPANISH
from .translations.pt import PORTUGUESE
from .translations.ru import RUSSIAN
from .translations.kk import KAZAKH
from .translations.tr import TURKISH
from .translations.de import GERMAN
from .translations.fr import FRENCH
from .translations.zh import CHINESE
from .translations.pl import POLISH
from .translations.qml_de import QML_GERMAN
from .translations.qml_en import QML_ENGLISH
from .translations.qml_es import QML_SPANISH
from .translations.qml_fr import QML_FRENCH
from .translations.qml_kk import QML_KAZAKH
from .translations.qml_pl import QML_POLISH
from .translations.qml_pt import QML_PORTUGUESE
from .translations.qml_ru import QML_RUSSIAN
from .translations.qml_sources import QML_SOURCE_IDS
from .translations.qml_tr import QML_TURKISH
from .translations.qml_zh import QML_CHINESE

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

_ENGLISH_CATALOG: dict[str, str] = {**ENGLISH, **QML_ENGLISH}
_TRANSLATIONS: dict[str, Mapping[str, str]] = {
    DEFAULT_LANGUAGE: _ENGLISH_CATALOG,
    'es': {**SPANISH, **QML_SPANISH},
    'pt': {**PORTUGUESE, **QML_PORTUGUESE},
    'ru': {**RUSSIAN, **QML_RUSSIAN},
    'kk': {**KAZAKH, **QML_KAZAKH},
    'tr': {**TURKISH, **QML_TURKISH},
    'de': {**GERMAN, **QML_GERMAN},
    'fr': {**FRENCH, **QML_FRENCH},
    'zh': {**CHINESE, **QML_CHINESE},
    'pl': {**POLISH, **QML_POLISH},
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
_QML_PLACEHOLDER_RE = re.compile(r'%(?:n|\d+)')
_FORMAT_FIELD_RE = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)(?:![^}:]+)?(?::[^}]*)?\}')


def _qml_placeholder_mapping(identifier: str, source_text: str) -> dict[str, str] | None:
    english_fields = [
        match.group(1) for match in _FORMAT_FIELD_RE.finditer(_ENGLISH_CATALOG[identifier])
    ]
    qml_placeholders = _QML_PLACEHOLDER_RE.findall(source_text)
    if len(english_fields) != len(qml_placeholders):
        return None

    mapping: dict[str, str] = {}
    for field, placeholder in zip(english_fields, qml_placeholders, strict=True):
        previous = mapping.setdefault(field, placeholder)
        if previous != placeholder:
            return None
    return mapping


def _catalog_text_for_qml(identifier: str, source_text: str) -> str:
    table = _TRANSLATIONS.get(_current_language, _ENGLISH_CATALOG)
    translated = table.get(identifier, _ENGLISH_CATALOG[identifier])
    mapping = _qml_placeholder_mapping(identifier, source_text)
    if mapping is None:
        return source_text
    return _FORMAT_FIELD_RE.sub(
        lambda match: mapping.get(match.group(1), match.group(0)), translated
    )


class _CatalogQTranslator(QTranslator):
    """Serve QML ``qsTr`` lookups directly from Fleasion's Python catalogs."""

    def translate(
        self,
        _context: str,
        source_text: str,
        _disambiguation: str | None = None,
        _n: int = -1,
    ) -> str:
        identifier = QML_SOURCE_IDS.get(source_text)
        return _catalog_text_for_qml(identifier, source_text) if identifier is not None else ''


_qt_translator: _CatalogQTranslator | None = None


def _refresh_qt_translator() -> None:
    global _qt_translator

    app = QCoreApplication.instance()
    if app is None:
        return
    if _qt_translator is not None:
        app.removeTranslator(_qt_translator)
        _qt_translator.deleteLater()
        _qt_translator = None
    if _current_language == DEFAULT_LANGUAGE:
        return

    _qt_translator = _CatalogQTranslator(app)
    app.installTranslator(_qt_translator)


def normalize_language(language: Any) -> str:
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


def set_language(language: Any) -> str:
    """Select the active language and refresh Qt's QML translator when available."""
    global _current_language
    _current_language = normalize_language(language)
    _refresh_qt_translator()
    return _current_language


def get_language() -> str:
    return _current_language


def tr(identifier: str, /, **values: Any) -> str:
    """Look up a string identifier with English fallback and safe formatting."""
    english = _ENGLISH_CATALOG.get(identifier, identifier)
    table = _TRANSLATIONS.get(_current_language, _ENGLISH_CATALOG)
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
    english = _ENGLISH_CATALOG.get(identifier, identifier)
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


def verbatim(value: Any) -> str:
    """Mark technical/user data that is intentionally not translated."""
    return str(value)
