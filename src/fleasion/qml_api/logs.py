"""Thread-safe log stream model for QML."""

from __future__ import annotations

import re
from typing import Final

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot
from PySide6.QtQml import QmlElement

from ..utils import log_buffer
from .models import DictListModel

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_LOG_PATTERN: Final = re.compile(r'^\[(?P<time>[^]]+)] \[(?P<category>[^]]+)] (?P<message>.*)$')


@QmlElement
class LogsApi(QObject):
    """Queue log-buffer callbacks and publish structured rows."""

    _refreshRequested = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._model = DictListModel(('time', 'category', 'message', 'text'), parent=self)
        self._refreshRequested.connect(self.refresh, Qt.ConnectionType.QueuedConnection)
        log_buffer.add_callback(self._on_logs_changed)
        self.refresh()

    def _on_logs_changed(self) -> None:
        self._refreshRequested.emit()

    @Slot()
    def refresh(self) -> None:
        rows: list[dict[str, str]] = []
        for entry in log_buffer.get_all():
            match = _LOG_PATTERN.match(entry)
            if match is None:
                rows.append({'time': '', 'category': 'App', 'message': entry, 'text': entry})
                continue
            values = match.groupdict()
            rows.append({**values, 'text': entry})
        current = self._model.snapshot()
        if rows[: len(current)] == current:
            self._model.append_items(rows[len(current) :])
            return
        self._model.replace_items(rows)

    @Property(QObject, constant=True)
    def model(self) -> QObject:
        return self._model

    @Slot()
    def dispose(self) -> None:
        log_buffer.remove_callback(self._on_logs_changed)
