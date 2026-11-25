"""Drag/drop helpers for file path text fields."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QDir, QMimeData, Signal
from PySide6.QtWidgets import QLineEdit, QWidget

if TYPE_CHECKING:
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent


def local_file_path_example() -> str:
    """Return a recognizable local path example for the current OS."""
    if sys.platform == 'win32':
        return r'C:\Mods\file.ext'
    if sys.platform == 'darwin':
        return '/Users/name/Mods/file.ext'
    return '/home/name/Mods/file.ext'


def local_file_path_from_mime_data(mime_data: QMimeData) -> str | None:
    """Return the first local file path in dropped MIME data."""
    if not mime_data.hasUrls():
        return None
    for url in mime_data.urls():
        if not url.isLocalFile():
            continue
        path = url.toLocalFile()
        if path:
            return QDir.toNativeSeparators(path)
    return None


class FileDropLineEdit(QLineEdit):
    """QLineEdit that accepts a dragged local file and inserts its path."""

    fileDropped = Signal(str)

    def __init__(self, text: str | QWidget = '', parent: QWidget | None = None) -> None:
        if isinstance(text, QWidget):
            super().__init__(text)
        elif parent is None and not text:
            super().__init__()
        else:
            super().__init__(text, parent)
        self.setAcceptDrops(True)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_file_path_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if local_file_path_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        path = local_file_path_from_mime_data(event.mimeData())
        if not path:
            super().dropEvent(event)
            return
        self.setText(path)
        self.fileDropped.emit(path)
        event.acceptProposedAction()
