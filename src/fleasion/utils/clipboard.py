"""Clipboard helpers."""

from __future__ import annotations

import struct
import sys
import time
from typing import TYPE_CHECKING, Protocol, cast

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMimeData
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

if TYPE_CHECKING:
    from collections.abc import Callable

_BI_BITFIELDS = 3
_LCS_sRGB = 0x73524742
_LCS_GM_IMAGES = 4


class _Win32Clipboard(Protocol):
    error: type[Exception]
    RegisterClipboardFormat: Callable[[str], int]
    OpenClipboard: Callable[[], object]
    EmptyClipboard: Callable[[], object]
    SetClipboardData: Callable[[int, bytes], object]
    CloseClipboard: Callable[[], object]


class _Win32Con(Protocol):
    CF_DIBV5: int


win32clipboard: _Win32Clipboard | None = None
win32con: _Win32Con | None = None
if sys.platform == 'win32':
    try:
        import win32clipboard as _win32clipboard_module  # pyright: ignore[reportMissingImports]
        import win32con as _win32con_module  # pyright: ignore[reportMissingImports]
    except ImportError:
        pass
    else:
        win32clipboard = cast('_Win32Clipboard', _win32clipboard_module)
        win32con = cast('_Win32Con', _win32con_module)


def _pixmap_to_rgba_image(pixmap: QPixmap) -> QImage:
    return pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _encode_png(image: QImage) -> bytes:
    png_data = QByteArray()
    buffer = QBuffer(png_data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        msg = 'Failed to prepare clipboard image data'
        raise RuntimeError(msg)
    try:
        save_image = cast('Callable[[QIODevice, str], bool]', image.save)
        if not save_image(buffer, 'PNG'):
            msg = 'Failed to encode clipboard image as PNG'
            raise RuntimeError(msg)
    finally:
        buffer.close()

    return bytes(png_data.data())


def _image_to_dibv5(image: QImage) -> bytes:
    """Return top-down 32-bit CF_DIBV5 bytes with an explicit alpha mask."""
    bgra_image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = bgra_image.width()
    height = bgra_image.height()
    row_stride = width * 4
    size_image = row_stride * height

    header = struct.pack(
        '<IiiHHIIiiIIIIIII36sIIIIIII',
        124,  # bV5Size
        width,  # bV5Width
        -height,  # bV5Height, negative means top-down rows
        1,  # bV5Planes
        32,  # bV5BitCount
        _BI_BITFIELDS,  # bV5Compression
        size_image,  # bV5SizeImage
        0,  # bV5XPelsPerMeter
        0,  # bV5YPelsPerMeter
        0,  # bV5ClrUsed
        0,  # bV5ClrImportant
        0x00FF0000,  # bV5RedMask
        0x0000FF00,  # bV5GreenMask
        0x000000FF,  # bV5BlueMask
        0xFF000000,  # bV5AlphaMask
        _LCS_sRGB,  # bV5CSType
        b'\0' * 36,  # bV5Endpoints
        0,  # bV5GammaRed
        0,  # bV5GammaGreen
        0,  # bV5GammaBlue
        _LCS_GM_IMAGES,  # bV5Intent
        0,  # bV5ProfileData
        0,  # bV5ProfileSize
        0,  # bV5Reserved
    )

    ptr = bgra_image.bits()
    raw = bytes(ptr)
    bytes_per_line = bgra_image.bytesPerLine()
    pixels = bytearray(size_image)
    for row in range(height):
        src_start = row * bytes_per_line
        dst_start = row * row_stride
        pixels[dst_start : dst_start + row_stride] = raw[src_start : src_start + row_stride]

    return header + bytes(pixels)


def _write_windows_clipboard_data(
    win32clipboard: _Win32Clipboard,
    win32con: _Win32Con,
    png_format: int,
    png_data: bytes,
    dibv5_data: bytes,
) -> None:
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(png_format, png_data)
        win32clipboard.SetClipboardData(win32con.CF_DIBV5, dibv5_data)
    finally:
        win32clipboard.CloseClipboard()


def _copy_windows_image_to_clipboard(image: QImage, png_data: bytes) -> None:
    clipboard = win32clipboard
    constants = win32con
    if clipboard is None or constants is None:
        msg = 'Windows clipboard support is unavailable'
        raise ImportError(msg)

    png_format = clipboard.RegisterClipboardFormat('PNG')
    dibv5_data = _image_to_dibv5(image)

    last_error: Exception | None = None
    for _ in range(10):
        try:
            clipboard.OpenClipboard()
            break
        except clipboard.error as exc:
            last_error = exc
            time.sleep(0.025)
    else:
        msg = f'Failed to open clipboard: {last_error}'
        raise RuntimeError(msg)

    try:
        _write_windows_clipboard_data(
            clipboard,
            constants,
            png_format,
            png_data,
            dibv5_data,
        )
    except clipboard.error as exc:
        msg = f'Failed to write image to clipboard: {exc}'
        raise RuntimeError(msg) from exc


def copy_pixmap_to_clipboard(pixmap: QPixmap) -> None:
    """Copy a pixmap while preserving transparent pixels for PNG-aware targets."""
    image = _pixmap_to_rgba_image(pixmap)
    png_data = _encode_png(image)

    if sys.platform == 'win32':
        copied = False
        try:
            _copy_windows_image_to_clipboard(image, png_data)
        except ImportError, RuntimeError:
            copied = False
        else:
            copied = True
        if copied:
            return

    mime_data = QMimeData()
    mime_data.setData('image/png', png_data)
    mime_data.setImageData(image)
    QApplication.clipboard().setMimeData(mime_data)
