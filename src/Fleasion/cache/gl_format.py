"""OpenGL surface format helpers for legacy fixed-function viewers."""

from __future__ import annotations

import sys

from PyQt6.QtGui import QSurfaceFormat


def legacy_gl_format() -> QSurfaceFormat:
    """Return a desktop OpenGL format compatible with fixed-function calls."""
    fmt = QSurfaceFormat()
    fmt.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    fmt.setVersion(2, 1)
    fmt.setProfile(QSurfaceFormat.OpenGLContextProfile.CompatibilityProfile)
    fmt.setOption(QSurfaceFormat.FormatOption.DeprecatedFunctions)
    fmt.setDepthBufferSize(24)
    if not sys.platform.startswith('linux'):
        fmt.setSamples(4)
    return fmt


def configure_default_legacy_gl_format() -> None:
    """Install the legacy GL format before Qt creates OpenGL contexts."""
    QSurfaceFormat.setDefaultFormat(legacy_gl_format())
