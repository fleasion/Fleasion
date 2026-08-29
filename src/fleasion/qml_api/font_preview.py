"""Focused application-font bridge for QML cache previews."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QObject, Property, Signal, Slot
from PySide6.QtGui import QFontDatabase
from PySide6.QtQml import QmlElement

from ..localization import tr

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0


@QmlElement
class FontPreviewApi(QObject):
    """Register one cached font and expose its families to QML."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._font_id = -1
        self._families: list[str] = []
        self._selected_family = ''
        self._format_name = ''
        self._error_text = ''

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return self._font_id >= 0 and bool(self._families)

    @Property(list, notify=changed)
    def families(self) -> list[str]:
        return list(self._families)

    @Property(str, notify=changed)
    def selectedFamily(self) -> str:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        return self._selected_family

    @selectedFamily.setter  # pyright: ignore[reportRedeclaration]
    def selectedFamily(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]  # noqa: N802
        if value not in self._families or value == self._selected_family:
            return
        self._selected_family = value
        self.changed.emit()

    @Property(str, notify=changed)
    def formatName(self) -> str:  # noqa: N802
        return self._format_name

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    def load_bytes(self, data: bytes) -> bool:
        """Replace the registered preview font with bytes from the cache."""
        self._unregister()
        self._format_name = _font_format(data)
        self._error_text = ''
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        if font_id < 0:
            self._families = []
            self._selected_family = ''
            self._error_text = tr('qml.dynamic.font_preview.load_failed')
            self.changed.emit()
            return False
        families = list(QFontDatabase.applicationFontFamilies(font_id))
        if not families:
            QFontDatabase.removeApplicationFont(font_id)
            self._families = []
            self._selected_family = ''
            self._error_text = tr('qml.dynamic.font_preview.family_missing')
            self.changed.emit()
            return False
        self._font_id = font_id
        self._families = families
        self._selected_family = families[0]
        self.changed.emit()
        return True

    @Slot()
    def clear(self) -> None:
        changed = self._font_id >= 0 or bool(
            self._families or self._selected_family or self._format_name or self._error_text
        )
        self._unregister()
        self._families = []
        self._selected_family = ''
        self._format_name = ''
        self._error_text = ''
        if changed:
            self.changed.emit()

    @Slot()
    def shutdown(self) -> None:
        self.clear()

    def _unregister(self) -> None:
        if self._font_id >= 0:
            QFontDatabase.removeApplicationFont(self._font_id)
            self._font_id = -1


def _font_format(data: bytes) -> str:
    if data.startswith(b'OTTO'):
        return 'OpenType'
    if data.startswith(b'ttcf'):
        return 'TrueType Collection'
    if data.startswith(b'\x00\x01\x00\x00'):
        return 'TrueType'
    return 'Font'


__all__ = ['FontPreviewApi']
