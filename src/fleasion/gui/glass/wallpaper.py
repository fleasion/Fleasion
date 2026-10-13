"""Discover and render wallpapers outside the GUI thread."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from operator import itemgetter
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Final
from urllib.parse import unquote, urlparse

from PIL import Image, ImageFilter, UnidentifiedImageError
from PySide6.QtGui import QImage

if TYPE_CHECKING:
    from collections.abc import Sequence

_TIMEOUT: Final = 2
_BLUR_RADIUS: Final = 18
_REFRESH_SECONDS: Final = 5
_MAX_SIZES: Final = 8
_MACOS_WALLPAPER_SCRIPT: Final = """
ObjC.import('AppKit');
var screens = $.NSScreen.screens;
screens.count > 0
    ? ObjC.unwrap($.NSWorkspace.sharedWorkspace.desktopImageURLForScreen(screens.objectAtIndex(0)).path)
    : '';
"""
log = logging.getLogger(__name__)


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


def _xfce_workspace() -> int:
    """Get the current EWMH workspace when X11 exposes one."""
    executable = shutil.which('xprop')
    if executable is None:
        return 0
    try:
        result = subprocess.run(
            [executable, '-root', '_NET_CURRENT_DESKTOP'],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return 0
    value = result.stdout.rpartition('=')[2].strip()
    return int(value) if result.returncode == 0 and value.isdigit() else 0


def _xfce_wallpaper() -> Path | None:
    """Read XFCE's per-monitor, per-workspace wallpaper from xfconf."""
    executable = shutil.which('xfconf-query')
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, '-c', 'xfce4-desktop', '-lv'],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0:
        return None
    workspace = _xfce_workspace()
    candidates: list[tuple[int, Path]] = []
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=1)
        if len(fields) != 2:
            continue
        key, value = fields
        if not key.startswith('/backdrop/') or not key.endswith(('/last-image', '/image-path')):
            continue
        value = value.strip().strip('"')
        path = _file_from_uri(value) if value.startswith('file://') else Path(value).expanduser()
        if path is None or not path.is_file() or path.suffix.lower() == '.xml':
            continue
        score = (
            (100 if f'/workspace{workspace}/' in key else 0)
            + (20 if '/workspace0/' in key else 0)
            + (10 if '/screen0/' in key else 0)
            + (2 if key.endswith('/last-image') else 0)
        )
        candidates.append((score, path))
    return max(candidates, key=itemgetter(0))[1] if candidates else None


def _linux_wallpaper() -> Path | None:
    desktop = os.environ.get('XDG_CURRENT_DESKTOP', '').lower()
    if 'xfce' in desktop and (xfce := _xfce_wallpaper()) is not None:
        return xfce
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
    return _xfce_wallpaper() if 'xfce' not in desktop else None


def wallpaper_path() -> Path | None:
    """Best-effort lookup of the current desktop wallpaper."""
    if sys.platform.startswith('linux'):
        return _linux_wallpaper()
    if sys.platform == 'win32':
        themes = Path(os.environ.get('APPDATA', '')) / 'Microsoft' / 'Windows' / 'Themes'
        path = themes / 'TranscodedWallpaper'
        return path if path.is_file() else None
    if sys.platform == 'darwin':
        return _macos_wallpaper()
    return None


def _macos_wallpaper() -> Path | None:
    """Read AppKit's primary-screen wallpaper without automating another app."""
    executable = shutil.which('osascript')
    if executable is None:
        return None
    try:
        result = subprocess.run(
            [executable, '-l', 'JavaScript', '-e', _MACOS_WALLPAPER_SCRIPT],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.SubprocessError:
        return None
    if result.returncode != 0 or not (value := result.stdout.strip()):
        return None
    path = Path(value)
    return path if path.is_file() else None


@dataclass(frozen=True)
class WallpaperSource:
    path: Path | None = None
    stamp: tuple[int, int] | None = None


def _discover_source() -> WallpaperSource:
    path = wallpaper_path()
    if path is None:
        return WallpaperSource()
    try:
        stat = path.stat()
    except OSError:
        log.warning('Wallpaper became unavailable: %s', path)
        return WallpaperSource()
    return WallpaperSource(path, (stat.st_mtime_ns, stat.st_size))


def _load_image(source: Path) -> Image.Image:
    try:
        with Image.open(source) as original:
            return original.convert('RGB')
    except UnidentifiedImageError:
        # macOS desktop pictures can use HEIC, which Pillow cannot decode by default
        executable = shutil.which('sips') if sys.platform == 'darwin' else None
        if executable is None:
            raise
        with TemporaryDirectory(prefix='fleasion-wallpaper-') as directory:
            converted = Path(directory) / 'wallpaper.png'
            try:
                subprocess.run(
                    [executable, '-s', 'format', 'png', str(source), '--out', str(converted)],
                    capture_output=True,
                    text=True,
                    timeout=_TIMEOUT,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                message = 'Could not convert the macOS wallpaper to PNG'
                raise OSError(message) from exc
            with Image.open(converted) as original:
                return original.convert('RGB')


def render_wallpaper(source: Path, width: int, height: int) -> QImage:
    """Decode, scale and blur an image in a worker, without persistent disk caches."""
    img = _load_image(source)
    scale = max(width / img.width, height / img.height)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    img = img.resize(size, Image.Resampling.BILINEAR)
    left, top = (img.width - width) // 2, (img.height - height) // 2
    img = img.crop((left, top, left + width, top + height))
    half = (max(1, width // 2), max(1, height // 2))
    small = img.resize(half, Image.Resampling.BILINEAR).filter(
        ImageFilter.GaussianBlur(_BLUR_RADIUS)
    )
    rendered = small.resize((width, height), Image.Resampling.BICUBIC)
    # Own the pixel buffer before the temporary Pillow image and bytes disappear
    return QImage(rendered.tobytes(), width, height, width * 3, QImage.Format.Format_RGB888).copy()


class WallpaperCache:
    """Retain images for multiple screen sizes; discovery and rendering are asynchronous."""

    def __init__(self) -> None:
        self._source = WallpaperSource()
        self._executor: ThreadPoolExecutor | None = None
        self._pending: Future[WallpaperSource] | None = None
        self._entries: OrderedDict[tuple[int, int], QImage | Future[QImage]] = OrderedDict()
        self._next_refresh = 0.0
        self._notify_ready = False

    def _pool(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='wallpaper')
        return self._executor

    @staticmethod
    def _completed(future: Future[QImage]) -> QImage:
        try:
            return future.result()
        except OSError, ValueError, Image.DecompressionBombError:
            log.exception('Could not render the desktop wallpaper')
            return QImage()

    def refresh_source(self) -> bool:
        """Poll ready results on the GUI thread and schedule periodic discovery."""
        changed = self._notify_ready
        self._notify_ready = False
        if self._pending is not None and self._pending.done():
            try:
                source = self._pending.result()
            except OSError:
                log.exception('Could not discover the desktop wallpaper')
                source = WallpaperSource()
            self._pending = None
            if source != self._source:
                for entry in self._entries.values():
                    if isinstance(entry, Future):
                        entry.cancel()
                self._entries.clear()
                self._source = source
                changed = True
        for size, entry in list(self._entries.items()):
            if isinstance(entry, Future) and entry.done():
                self._entries[size] = self._completed(entry)
                changed = True
        if self._pending is None and monotonic() >= self._next_refresh:
            self._pending = self._pool().submit(_discover_source)
            self._next_refresh = monotonic() + _REFRESH_SECONDS
        return changed

    def image(self, width: int, height: int) -> QImage:
        """Return a cached image immediately, or queue one and return an empty image."""
        size = (max(1, width), max(1, height))
        entry = self._entries.get(size)
        if entry is not None:
            self._entries.move_to_end(size)
            if isinstance(entry, QImage):
                return entry
            if entry.done():
                image = self._completed(entry)
                self._entries[size] = image
                self._notify_ready = True
                return image
            return QImage()
        if self._source.path is not None:
            self._entries[size] = self._pool().submit(render_wallpaper, self._source.path, *size)
            while len(self._entries) > _MAX_SIZES:
                _, oldest = self._entries.popitem(last=False)
                if isinstance(oldest, Future):
                    oldest.cancel()
        return QImage()

    def close(self) -> None:
        """Release cached images and cancel queued jobs without blocking the GUI."""
        if self._executor is not None:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        self._entries.clear()
        self._pending = None
        self._next_refresh = 0.0
        self._notify_ready = False


wallpapers = WallpaperCache()
