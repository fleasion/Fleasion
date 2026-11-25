from collections.abc import Callable
from typing import cast

from PySide6.QtGui import QColor, QImage

from fleasion.utils import clipboard as clipboard_module


def _encode_png(image: QImage) -> bytes:
    callback = cast('Callable[[QImage], bytes]', clipboard_module.__dict__['_encode_png'])
    return callback(image)


def _image_to_dibv5(image: QImage) -> bytes:
    callback = cast('Callable[[QImage], bytes]', clipboard_module.__dict__['_image_to_dibv5'])
    return callback(image)


def test_encode_png_produces_png_bytes() -> None:
    image = QImage(1, 1, QImage.Format.Format_ARGB32)
    image.fill(QColor(17, 34, 51, 255))

    encoded = _encode_png(image)

    assert encoded.startswith(b'\x89PNG\r\n\x1a\n')


def test_image_to_dibv5_supports_pyside_memoryview() -> None:
    image = QImage(2, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor(17, 34, 51, 255))

    encoded = _image_to_dibv5(image)

    assert len(encoded) == 124 + (2 * 2 * 4)
    assert encoded[:4] == (124).to_bytes(4, 'little')
