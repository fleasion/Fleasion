"""OpenGL surface format helpers for legacy fixed-function viewers."""

from __future__ import annotations

import math
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
    # The offscreen framebuffer path isolates multisampling from native window
    # presentation, so Windows can retain the original 4x MSAA preview quality.
    if not sys.platform.startswith('linux'):
        fmt.setSamples(4)
    return fmt


def set_perspective(fov_y_degrees: float, aspect: float, near: float, far: float) -> None:
    """Set a perspective projection without relying on GLU."""
    # Keep PyOpenGL out of the normal dashboard startup path. Importing OpenGL.GL
    # can load the platform OpenGL runtime even when no 3D preview is ever opened.
    from OpenGL.GL import glFrustum

    if aspect <= 0.0:
        aspect = 1.0
    if near <= 0.0:
        raise ValueError('near must be positive')
    if far <= near:
        raise ValueError('far must be greater than near')

    half_angle = math.radians(fov_y_degrees) / 2.0
    top = near * math.tan(half_angle)
    bottom = -top
    right = top * aspect
    left = -right
    glFrustum(left, right, bottom, top, near, far)
