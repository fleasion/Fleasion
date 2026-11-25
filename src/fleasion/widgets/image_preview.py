"""Image previews that fit their layout without forcing scrollbars."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QLabel, QMenu, QSizePolicy

from fleasion.localization import tr
from fleasion.utils.clipboard import copy_pixmap_to_clipboard

if TYPE_CHECKING:
    from PySide6.QtGui import QContextMenuEvent, QPixmap, QResizeEvent
    from PySide6.QtWidgets import QWidget


class ImagePreviewLabel(QLabel):
    """An image scaled to fit, optionally offering to copy the full-size original."""

    def __init__(
        self,
        text: str = '',
        parent: QWidget | None = None,
        *,
        fit_height: bool = True,
        copyable: bool = False,
    ) -> None:
        super().__init__(text, parent)
        self._source_pixmap: QPixmap | None = None
        self._fit_height = fit_height
        self._copyable = copyable
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
    def contextMenuEvent(self, ev: QContextMenuEvent, /) -> None:
        pixmap = self._source_pixmap
        if not self._copyable or pixmap is None or pixmap.isNull():
            super().contextMenuEvent(ev)
            return
        menu = QMenu(self)
        copy_action = menu.addAction(tr('ui.cache.cache_viewer.copy_image'))
        if menu.exec(ev.globalPos()) == copy_action:
            copy_pixmap_to_clipboard(pixmap)

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
