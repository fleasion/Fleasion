"""Drag/drop helpers for file path text fields."""

from __future__ import annotations

from PyQt6.QtCore import QDir, QMimeData, pyqtSignal
from PyQt6.QtWidgets import QLineEdit


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

    fileDropped = pyqtSignal(str)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):  # noqa: N802
        if local_file_path_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        if local_file_path_from_mime_data(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        path = local_file_path_from_mime_data(event.mimeData())
        if not path:
            super().dropEvent(event)
            return
        self.setText(path)
        self.fileDropped.emit(path)
        event.acceptProposedAction()
