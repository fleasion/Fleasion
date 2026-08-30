"""Raster QWidget backed by an offscreen OpenGL framebuffer.

The renderer deliberately keeps OpenGL out of the native dashboard window.
Some Windows/driver combinations render a QOpenGLWindow child framebuffer
correctly but present that native child as solid black. Rendering into an
FBO on a QOffscreenSurface and painting the resulting QImage through the
normal QWidget raster path avoids both that presentation issue and the
QOpenGLWidget top-level-window recreation behavior.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QCloseEvent,
    QImage,
    QOffscreenSurface,
    QOpenGLContext,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSurfaceFormat,
)
from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
from PySide6.QtWidgets import QWidget

from ..utils.logging import log_buffer


class OffscreenOpenGLWidget(QWidget):
    """QWidget that renders OpenGL offscreen and presents it as a raster image."""

    framePresented = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(120, 120)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

        self._requested_gl_format = QSurfaceFormat()
        self._gl_context: QOpenGLContext | None = None
        self._gl_surface: QOffscreenSurface | None = None
        self._gl_fbo: QOpenGLFramebufferObject | None = None
        self._gl_initialized = False
        self._gl_render_size = (0, 0)
        self._frame_image = QImage()
        self._offscreen_error_logged = False
        self._rendering = False

    def setFormat(self, fmt: QSurfaceFormat) -> None:
        """Store the requested context format until the first rendered frame."""
        if self._gl_context is not None:
            log_buffer.log('OpenGL', 'Ignoring GL format change after context creation')
            return
        self._requested_gl_format = QSurfaceFormat(fmt)

    def context(self) -> QOpenGLContext | None:
        return self._gl_context

    def defaultFramebufferObject(self) -> int:
        return self._gl_fbo.handle() if self._gl_fbo is not None else 0

    def isExposed(self) -> bool:
        """Compatibility helper for diagnostics retained from QOpenGLWindow."""
        return self.isVisible() and self.window().isVisible()

    def makeCurrent(self) -> bool:
        if not self._ensure_context() or self._gl_context is None or self._gl_surface is None:
            return False
        return bool(self._gl_context.makeCurrent(self._gl_surface))

    def doneCurrent(self) -> None:
        if self._gl_context is not None:
            self._gl_context.doneCurrent()

    def _ensure_context(self) -> bool:
        if self._gl_context is not None:
            return self._gl_context.isValid()

        context = QOpenGLContext(self)
        context.setFormat(self._requested_gl_format)
        if not context.create() or not context.isValid():
            raise RuntimeError('Could not create offscreen OpenGL context')

        surface = QOffscreenSurface()
        surface.setFormat(context.format())
        surface.create()
        if not surface.isValid():
            raise RuntimeError('Could not create offscreen OpenGL surface')

        self._gl_context = context
        self._gl_surface = surface
        if not context.makeCurrent(surface):
            raise RuntimeError('Could not make offscreen OpenGL context current')
        try:
            self.initializeGL()
            self._gl_initialized = True
        finally:
            context.doneCurrent()
        return True

    def _ensure_fbo(self, width: int, height: int) -> None:
        if self._gl_context is None:
            raise RuntimeError('OpenGL context is not available')

        width = max(1, int(width))
        height = max(1, int(height))
        if self._gl_fbo is not None and self._gl_render_size == (width, height):
            return

        self._gl_fbo = None
        fbo_format = QOpenGLFramebufferObjectFormat()
        fbo_format.setAttachment(QOpenGLFramebufferObject.Attachment.CombinedDepthStencil)
        fbo_format.setSamples(max(0, self._gl_context.format().samples()))
        fbo = QOpenGLFramebufferObject(width, height, fbo_format)
        if not fbo.isValid():
            raise RuntimeError(f'Could not create {width}x{height} OpenGL framebuffer')
        self._gl_fbo = fbo
        self._gl_render_size = (width, height)
        self.resizeGL(width, height)

    def _render_to_image(self) -> QImage:
        if not self._ensure_context() or self._gl_context is None or self._gl_surface is None:
            raise RuntimeError('OpenGL context is unavailable')
        if not self._gl_context.makeCurrent(self._gl_surface):
            raise RuntimeError('Could not make offscreen OpenGL context current')

        try:
            self._ensure_fbo(self.width(), self.height())
            assert self._gl_fbo is not None
            if not self._gl_fbo.bind():
                raise RuntimeError('Could not bind offscreen OpenGL framebuffer')
            try:
                self.paintGL()
                image = self._gl_fbo.toImage(True)
                if image.isNull():
                    raise RuntimeError('OpenGL framebuffer readback returned a null image')
                return image
            finally:
                self._gl_fbo.release()
        finally:
            self._gl_context.doneCurrent()

    def paintEvent(self, event: QPaintEvent) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            try:
                self._frame_image = self._render_to_image()
            except Exception as exc:
                if not self._offscreen_error_logged:
                    self._offscreen_error_logged = True
                    log_buffer.log(
                        'OpenGL',
                        f'Offscreen raster presentation failed: {type(exc).__name__}: {exc}',
                    )

            painter = QPainter(self)
            try:
                if self._frame_image.isNull():
                    painter.fillRect(self.rect(), self.palette().window())
                    painter.setPen(self.palette().windowText().color())
                    painter.drawText(
                        self.rect(),
                        Qt.AlignmentFlag.AlignCenter,
                        '3D preview unavailable',
                    )
                else:
                    painter.drawImage(self.rect(), self._frame_image)
            finally:
                painter.end()

            if not self._frame_image.isNull():
                self.framePresented.emit()
        finally:
            self._rendering = False

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def closeEvent(self, event: QCloseEvent) -> None:
        context = self._gl_context
        surface = self._gl_surface
        if context is not None and surface is not None:
            try:
                context.makeCurrent(surface)
                self._gl_fbo = None
            except Exception:
                pass
            finally:
                try:
                    context.doneCurrent()
                except Exception:
                    pass
        if surface is not None:
            try:
                surface.destroy()
            except Exception:
                pass
        self._gl_surface = None
        self._gl_context = None
        super().closeEvent(event)

    # Hooks implemented by the concrete renderer classes.
    def initializeGL(self) -> None:  # pragma: no cover - abstract hook
        raise NotImplementedError

    def resizeGL(self, width: int, height: int) -> None:  # pragma: no cover - abstract hook
        raise NotImplementedError

    def paintGL(self) -> None:  # pragma: no cover - abstract hook
        raise NotImplementedError
