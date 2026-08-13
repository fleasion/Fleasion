"""On-demand cached image decoding for QML previews."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from ..cache.cache_manager import CacheManager


class CacheImageProvider(QQuickImageProvider):
    """Decode image-compatible cached assets only when a preview is visible."""

    def __init__(self, cache_manager: CacheManager) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._cache = cache_manager

    def requestImage(
        self,
        image_id: str,
        size: QSize,
        requested_size: QSize,
    ) -> QImage:  # noqa: N802
        clean_id = image_id.partition('?')[0]
        type_text, separator, asset_id = clean_id.partition('/')
        if not separator:
            return QImage()
        try:
            asset_type = int(type_text)
        except ValueError:
            return QImage()
        payload = self._cache.get_asset(asset_id, asset_type)
        if not payload:
            return QImage()
        image = QImage.fromData(payload)
        if image.isNull():
            return image
        if requested_size.isValid() and not requested_size.isEmpty():
            image = image.scaled(
                requested_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image
