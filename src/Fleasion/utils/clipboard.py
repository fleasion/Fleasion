"""Clipboard helpers."""

import struct
import sys
import time

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QMimeData
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication


_BI_BITFIELDS = 3
_LCS_sRGB = 0x73524742
_LCS_GM_IMAGES = 4


def _pixmap_to_rgba_image(pixmap: QPixmap) -> QImage:
    return pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)


def _encode_png(image: QImage) -> bytes:
    png_data = QByteArray()
    buffer = QBuffer(png_data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError('Failed to prepare clipboard image data')
    try:
        if not image.save(buffer, 'PNG'):
            raise RuntimeError('Failed to encode clipboard image as PNG')
    finally:
        buffer.close()

    return bytes(png_data)


def _image_to_dibv5(image: QImage) -> bytes:
    """Return top-down 32-bit CF_DIBV5 bytes with an explicit alpha mask."""
    bgra_image = image.convertToFormat(QImage.Format.Format_ARGB32)
    width = bgra_image.width()
    height = bgra_image.height()
    row_stride = width * 4
    size_image = row_stride * height

    header = struct.pack(
        '<IiiHHIIiiIIIIIII36sIIIIIII',
        124,                         # bV5Size
        width,                       # bV5Width
        -height,                     # bV5Height, negative means top-down rows
        1,                           # bV5Planes
        32,                          # bV5BitCount
        _BI_BITFIELDS,               # bV5Compression
        size_image,                  # bV5SizeImage
        0,                           # bV5XPelsPerMeter
        0,                           # bV5YPelsPerMeter
        0,                           # bV5ClrUsed
        0,                           # bV5ClrImportant
        0x00FF0000,                  # bV5RedMask
        0x0000FF00,                  # bV5GreenMask
        0x000000FF,                  # bV5BlueMask
        0xFF000000,                  # bV5AlphaMask
        _LCS_sRGB,                   # bV5CSType
        b'\0' * 36,                  # bV5Endpoints
        0,                           # bV5GammaRed
        0,                           # bV5GammaGreen
        0,                           # bV5GammaBlue
        _LCS_GM_IMAGES,              # bV5Intent
        0,                           # bV5ProfileData
        0,                           # bV5ProfileSize
        0,                           # bV5Reserved
    )

    ptr = bgra_image.bits()
    ptr.setsize(bgra_image.sizeInBytes())
    raw = bytes(ptr)
    bytes_per_line = bgra_image.bytesPerLine()
    pixels = bytearray(size_image)
    for row in range(height):
        src_start = row * bytes_per_line
        dst_start = row * row_stride
        pixels[dst_start:dst_start + row_stride] = raw[src_start:src_start + row_stride]

    return header + bytes(pixels)


def _copy_windows_image_to_clipboard(image: QImage, png_data: bytes) -> None:
    import win32clipboard
    import win32con

    png_format = win32clipboard.RegisterClipboardFormat('PNG')
    dibv5_data = _image_to_dibv5(image)

    last_error = None
    for _ in range(10):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception as e:
            last_error = e
            time.sleep(0.025)
    else:
        raise RuntimeError(f'Failed to open clipboard: {last_error}')

    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(png_format, png_data)
        win32clipboard.SetClipboardData(getattr(win32con, 'CF_DIBV5', 17), dibv5_data)
    finally:
        win32clipboard.CloseClipboard()


def copy_pixmap_to_clipboard(pixmap: QPixmap) -> None:
    """Copy a pixmap while preserving transparent pixels for PNG-aware targets."""
    image = _pixmap_to_rgba_image(pixmap)
    png_data = _encode_png(image)

    if sys.platform == 'win32':
        try:
            _copy_windows_image_to_clipboard(image, png_data)
            return
        except Exception:
            pass

    mime_data = QMimeData()
    mime_data.setData('image/png', png_data)
    mime_data.setImageData(image)
    QApplication.clipboard().setMimeData(mime_data)
