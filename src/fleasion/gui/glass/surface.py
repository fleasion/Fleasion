"""Painting the glass window surface, including the optional wallpaper backdrop."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, override
from urllib.parse import unquote, urlparse

from PIL import Image, ImageFilter
from PySide6.QtCore import QEvent, QObject, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QPushButton, QWidget

from .desktop_blur import blur
from .state import UNBLURRED_OPACITY, state
from .style import ICON_DIR, RADIUS

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtGui import QScreen

_TIMEOUT: Final = 2
_BLUR_RADIUS: Final = 18
_COMPACT_BUTTON_MAX: Final = 40
_ARROW_BUTTON_MAX: Final = 24
_FALLBACK_BACKDROP: Final = (20, 22, 34)


def effective_opacity() -> float:
    """Tint strength: the user's choice when something blurs behind us, else denser."""
    settings = state.settings
    if settings.backdrop == 'wallpaper' or blur.status() == 'on':
        return settings.opacity
    return max(settings.opacity, UNBLURRED_OPACITY)


# ---------------------------------------------------------------- wallpaper


def _gsettings(schema: str, key: str) -> str:
    executable = shutil.which('gsettings')
    if executable is None:
        return ''
    try:
        result = subprocess.run(
            [executable, 'get', schema, key],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return ''
    return result.stdout.strip().strip("'")


def _file_from_uri(uri: str) -> Path | None:
    if not uri.startswith('file://'):
        return None
    path = Path(unquote(urlparse(uri).path))
    return path if path.is_file() and path.suffix.lower() != '.xml' else None


def _linux_wallpaper() -> Path | None:
    keys: Sequence[str] = ('picture-uri', 'picture-uri-dark')
    if 'dark' in _gsettings('org.gnome.desktop.interface', 'color-scheme'):
        keys = tuple(reversed(keys))
    for key in keys:
        if path := _file_from_uri(_gsettings('org.gnome.desktop.background', key)):
            return path
    kde = Path.home() / '.config' / 'plasma-org.kde.plasma.desktop-appletsrc'
    if kde.is_file():
        for line in kde.read_text(encoding='utf-8', errors='ignore').splitlines():
            if line.startswith('Image=') and (path := _file_from_uri(line.split('=', 1)[1])):
                return path
    return None


def wallpaper_path() -> Path | None:
    """Best-effort lookup of the current desktop wallpaper."""
    if sys.platform.startswith('linux'):
        return _linux_wallpaper()
    if sys.platform == 'win32':
        themes = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Themes'
        path = themes / 'TranscodedWallpaper'
        return path if path.is_file() else None
    return None


class _WallpaperCache:
    """One blurred, screen-sized wallpaper image, rebuilt when either changes."""

    def __init__(self) -> None:
        self._key = ''
        self._image = QImage()

    def image(self, width: int, height: int) -> QImage:
        source = wallpaper_path()
        stamp = ''
        if source is not None:
            stat = source.stat()
            stamp = f'{source}{stat.st_mtime_ns}{stat.st_size}'
        key = hashlib.sha256(f'{stamp}|{width}x{height}'.encode()).hexdigest()[:16]
        if key != self._key:
            self._image = QImage(str(self._render(key, source, width, height)))
            self._key = key
        return self._image

    @staticmethod
    def _render(key: str, source: Path | None, width: int, height: int) -> Path:
        target = ICON_DIR / f'wallpaper-{key}.png'
        if target.is_file():
            return target
        ICON_DIR.mkdir(parents=True, exist_ok=True)
        for stale in ICON_DIR.glob('wallpaper-*.png'):
            stale.unlink(missing_ok=True)
        if source is None:
            img = Image.new('RGB', (width, height), _FALLBACK_BACKDROP)
        else:
            img = Image.open(source).convert('RGB')
            scale = max(width / img.width, height / img.height)
            size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
            img = img.resize(size, Image.Resampling.BILINEAR)
            left, top = (img.width - width) // 2, (img.height - height) // 2
            img = img.crop((left, top, left + width, top + height))
        # Blurring at half size looks the same and is a quarter of the work
        half = (max(1, width // 2), max(1, height // 2))
        small = img.resize(half, Image.Resampling.BILINEAR).filter(
            ImageFilter.GaussianBlur(_BLUR_RADIUS)
        )
        small.resize((width, height), Image.Resampling.BICUBIC).save(target)
        return target


_wallpapers = _WallpaperCache()


def _screen(window: QWidget) -> QScreen | None:
    # Qt returns no screen for a window that is not shown yet
    return window.screen()


def _origin_in_screen(window: QWidget, width: int, height: int) -> QPoint:
    """Where the window sits on its screen; centred when the platform hides it."""
    screen = _screen(window)
    if QApplication.platformName().startswith('wayland') or screen is None:
        # Wayland clients cannot learn their global position
        return QPoint((width - window.width()) // 2, (height - window.height()) // 2)
    return window.mapToGlobal(QPoint(0, 0)) - screen.geometry().topLeft()


def _paint_wallpaper(painter: QPainter, window: QWidget, path: QPainterPath) -> None:
    screen = _screen(window)
    if screen is None:
        return
    geometry = screen.geometry()
    # Cover the screen, or the window if it is larger than the screen
    width = max(geometry.width(), window.width())
    height = max(geometry.height(), window.height())
    image = _wallpapers.image(width, height)
    if image.isNull():
        return
    origin = _origin_in_screen(window, width, height)
    source = QRectF(origin.x(), origin.y(), window.width(), window.height())
    painter.save()
    painter.setClipPath(path)
    painter.drawImage(QRectF(window.rect()), image, source)
    painter.restore()


# ---------------------------------------------------------------- surface


def paint_glass(
    painter: QPainter, rect: QRectF, radius: float, window: QWidget | None = None
) -> None:
    """Backdrop, tint, top-down sheen and a specular rim."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if window is not None and state.settings.backdrop == 'wallpaper':
        _paint_wallpaper(painter, window, path)
    painter.fillPath(path, QColor(16, 17, 24, round(255 * effective_opacity())))
    sheen = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
    sheen.setColorAt(0.25, QColor(255, 255, 255, 10))
    sheen.setColorAt(1.0, QColor(255, 255, 255, 4))
    painter.fillPath(path, sheen)
    rim = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    rim.setColorAt(0.0, QColor(255, 255, 255, 120))
    rim.setColorAt(0.12, QColor(255, 255, 255, 40))
    rim.setColorAt(1.0, QColor(255, 255, 255, 22))
    painter.setPen(QPen(rim, 1.0))
    painter.drawPath(path)


def _repolish_later(widget: QWidget) -> None:
    def repolish() -> None:
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)

    QTimer.singleShot(0, repolish)


def _is_window(obj: QObject | None) -> bool:
    return isinstance(obj, (QMainWindow, QDialog)) and obj.isWindow()


class GlassWindows(QObject):
    """Gives top-level windows an alpha surface and paints the glass on them."""

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isinstance(watched, QWidget):
            return False
        kind = event.type()
        if kind == QEvent.Type.Polish:
            self._polish(watched)
        elif kind == QEvent.Type.Paint and self._paints(watched):
            painter = QPainter(watched)
            framed = bool(watched.property('glassChrome')) and not watched.isMaximized()
            rect = QRectF(watched.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
            paint_glass(painter, rect, RADIUS if framed else 0, watched)
            painter.end()
        elif (
            kind == QEvent.Type.Move
            and state.settings.backdrop == 'wallpaper'
            and _is_window(watched)
        ):
            watched.update()  # The wallpaper slice follows the window
        return False

    @staticmethod
    def _paints(widget: QWidget) -> bool:
        return (
            _is_window(widget)
            and widget.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            and not widget.property('glassOwnSurface')
        )

    @staticmethod
    def _polish(widget: QWidget) -> None:
        if _is_window(widget):
            # Polish runs before the native surface exists, so it gets an alpha channel
            widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        elif not isinstance(widget, QPushButton):
            return
        elif (
            widget.isFlat()
            and widget.maximumWidth() <= _ARROW_BUTTON_MAX
            and widget.property('glassArrow') is None
        ):
            widget.setProperty('glassArrow', True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
            _repolish_later(widget)
        elif (
            0 < widget.maximumWidth() <= _COMPACT_BUTTON_MAX and widget.property('compact') is None
        ):
            widget.setProperty('compact', True)  # ruff: ignore[boolean-positional-value-in-call] - Qt API
            _repolish_later(widget)
