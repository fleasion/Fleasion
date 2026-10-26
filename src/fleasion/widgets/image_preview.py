"""Image previews that fit their layout without forcing scrollbars."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QSizePolicy

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap, QResizeEvent
    from PySide6.QtWidgets import QWidget


class ImagePreviewLabel(QLabel):
    def __init__(
        self, text: str = '', parent: QWidget | None = None, *, fit_height: bool = True
    ) -> None:
        super().__init__(text, parent)
        self._source_pixmap: QPixmap | None = None
        self._fit_height = fit_height
        self.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored if fit_height else QSizePolicy.Policy.Fixed,
        )

    def set_image(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._update_pixmap()

    @override
    def clear(self) -> None:
        self._source_pixmap = None
        super().clear()

    @override
    def setText(self, text: str) -> None:
        self._source_pixmap = None
        super().setText(text)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_pixmap()

    @override
    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange:
            self._update_pixmap()

    def _update_pixmap(self) -> None:
        pixmap = self._source_pixmap
        available = self.contentsRect().size()
        if pixmap is None or pixmap.isNull() or available.isEmpty():
            return
        if self._fit_height:
            scaled = pixmap.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        else:
            scaled = pixmap.scaledToWidth(
                min(available.width(), pixmap.width()), Qt.TransformationMode.SmoothTransformation
            )
            height = scaled.height() + self.height() - available.height()
            if self.height() != height:
                self.setFixedHeight(height)
        super().setPixmap(scaled)
