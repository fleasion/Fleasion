"""Helpers for painting from cached pixmaps."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPainter


def pixel_aligned(painter: QPainter) -> bool:
    """Whether a pixmap drawn at whole device pixels matches painting directly."""
    transform = painter.worldTransform()
    return transform.isIdentity() or not (
        transform.isScaling()
        or transform.isRotating()
        or transform.dx() != int(transform.dx())
        or transform.dy() != int(transform.dy())
    )
