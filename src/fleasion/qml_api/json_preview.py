"""Searchable JSON preview state for the QML cache browser."""

from __future__ import annotations

import json
from typing import Final

from PySide6.QtCore import QObject, Property, Signal

from .preset_tree import PresetJsonTreeModel

_MAX_JSON_BYTES: Final = 4 * 1024 * 1024
_MAX_JSON_NODES: Final = 25_000
_MAX_JSON_DEPTH: Final = 128


class JsonPreviewApi(QObject):
    """Own a bounded JSON document and expose its hierarchical model."""

    queryChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model = PresetJsonTreeModel(self)
        self._query = ''

    @Property(QObject, constant=True)
    def model(self) -> PresetJsonTreeModel:
        return self._model

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self._model.set_query(normalized)
        self.queryChanged.emit()

    def load_bytes(self, payload: bytes) -> bool:
        if not payload or len(payload) > _MAX_JSON_BYTES:
            self.clear()
            return False
        try:
            document = json.loads(payload)
            self._validate_document(document)
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
            self.clear()
            return False
        self._query = ''
        self._model.set_document(document, ())
        self.queryChanged.emit()
        return True

    def clear(self) -> None:
        query_changed = bool(self._query)
        self._query = ''
        self._model.clear()
        if query_changed:
            self.queryChanged.emit()

    @staticmethod
    def _validate_document(document: object) -> None:
        stack: list[tuple[object, int]] = [(document, 0)]
        count = 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if count > _MAX_JSON_NODES:
                raise ValueError('JSON contains too many values to preview')
            if depth > _MAX_JSON_DEPTH:
                raise ValueError('JSON nesting is too deep to preview')
            if isinstance(value, dict):
                stack.extend((child, depth + 1) for child in value.values())
            elif isinstance(value, list):
                stack.extend((child, depth + 1) for child in value)


__all__ = ['JsonPreviewApi']
