"""Value controls that leave the mouse wheel to the scroll area around them."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from PySide6.QtWidgets import QComboBox, QSlider, QSpinBox

if TYPE_CHECKING:
    from PySide6.QtGui import QWheelEvent


class NoWheelSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events to prevent accidental value changes."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()


class NoWheelSlider(QSlider):
    """QSlider that ignores wheel events to prevent accidental value changes."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()


class DropdownComboBox(QComboBox):
    """A standard combo box that does not change selection on wheel scroll."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()
