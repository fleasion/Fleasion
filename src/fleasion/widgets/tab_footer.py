"""Shared tab footer geometry, based on the Replacer footer."""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QWidget


class TabFooter(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.content_layout = QHBoxLayout(self)
        self.content_layout.setContentsMargins(8, 4, 8, 4)
