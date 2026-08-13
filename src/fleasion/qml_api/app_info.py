"""Static application metadata for QML."""

from __future__ import annotations

from PySide6.QtCore import QObject, Property
from PySide6.QtQml import QmlElement

from ..utils import APP_DISCORD, APP_NAME, APP_REPO, APP_VERSION

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0


@QmlElement
class AppInfo(QObject):
    """Expose stable build and community information."""

    @Property(str, constant=True)
    def name(self) -> str:
        return APP_NAME

    @Property(str, constant=True)
    def version(self) -> str:
        return APP_VERSION

    @Property(str, constant=True)
    def repositoryUrl(self) -> str:  # noqa: N802
        return APP_REPO if APP_REPO.startswith('http') else f'https://{APP_REPO}'

    @Property(str, constant=True)
    def discordUrl(self) -> str:  # noqa: N802
        return APP_DISCORD if APP_DISCORD.startswith('http') else f'https://{APP_DISCORD}'
