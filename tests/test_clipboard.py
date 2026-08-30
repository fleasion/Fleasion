from collections.abc import Callable
from typing import cast

from PySide6.QtGui import QColor, QImage

from fleasion.utils import clipboard as clipboard_module


def _encode_png(image: QImage) -> bytes:
    callback = cast('Callable[[QImage], bytes]', clipboard_module.__dict__['_encode_png'])
    return callback(image)


def test_encode_png_produces_png_bytes() -> None:
    image = QImage(1, 1, QImage.Format.Format_ARGB32)
    image.fill(QColor(17, 34, 51, 255))

    encoded = _encode_png(image)

    assert encoded.startswith(b'\x89PNG\r\n\x1a\n')
