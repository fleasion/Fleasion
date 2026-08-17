"""Aggregate runtime services made available to the QML scene."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot

from .. import __version__
from ..utils import APP_DISCORD, APP_NAME, APP_REPO, get_icon_path, open_folder
from ..utils.paths import CONFIGS_FOLDER, LOGS_DIR
from .cache import CacheApi
from .logs import LogsApi
from .modifications import ModificationsApi
from .proxy import ProxyApi
from .repair import StartupRepairApi
from .replacer import ReplacerApi
from .settings import SettingsApi
from .subplaces import SubplacesApi
from .subplace_join import SubplaceJoinCoordinator
from .utilities import UtilitiesApi
from .update import UpdateApi

if TYPE_CHECKING:
    from ..config.manager import ConfigManager
    from ..modifications.manager import ModificationManager


class AppContext(QObject):
    """Hold strongly referenced controllers and application-wide commands."""

    pageRequested = Signal(str)
    dashboardVisibilityRequested = Signal(bool)
    quitRequested = Signal()
    restartRequested = Signal()
    cacheCleanupRequested = Signal()
    notificationRequested = Signal(str, str, str)
    errorOccurred = Signal(str)
    firstRunChanged = Signal()
    setupCompleted = Signal()

    def __init__(
        self,
        config_manager: ConfigManager,
        proxy_master: Any | None = None,
        modification_manager: ModificationManager | None = None,
        show_dashboard: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        cache_manager = getattr(proxy_master, 'cache_manager', None)
        cache_scraper = getattr(proxy_master, 'cache_scraper', None)
        self._replacer = ReplacerApi(  # pyright: ignore[reportCallIssue]
            config_manager,
            self,
            cache_manager=cache_manager,
        )
        self._cache = CacheApi(  # pyright: ignore[reportCallIssue]
            cache_manager, cache_scraper, self
        )
        self._modifications = ModificationsApi(  # pyright: ignore[reportCallIssue]
            modification_manager,
            self,
            config_manager=config_manager,
            proxy_master=proxy_master,
        )
        self._proxy = ProxyApi(  # pyright: ignore[reportCallIssue]
            proxy_master,
            self,
            config_manager=config_manager,
        )
        self._settings = SettingsApi(config_manager, self)  # pyright: ignore[reportCallIssue]
        self._logs = LogsApi(self)  # pyright: ignore[reportCallIssue]
        subplace_join = SubplaceJoinCoordinator()
        self._utilities = UtilitiesApi(  # pyright: ignore[reportCallIssue]
            config_manager,
            proxy_master,
            subplace_join=subplace_join,
            parent=self,
        )
        self._subplaces = SubplacesApi(  # pyright: ignore[reportCallIssue]
            proxy_master=proxy_master,
            join_coordinator=subplace_join,
            parent=self,
        )
        self._updates = UpdateApi(parent=self)  # pyright: ignore[reportCallIssue]
        self._startup_repair = StartupRepairApi(self)  # pyright: ignore[reportCallIssue]
        self._show_dashboard = show_dashboard
        self._wire_notifications()

    def _wire_notifications(self) -> None:
        for controller in (
            self._replacer,
            self._cache,
            self._modifications,
            self._subplaces,
            self._utilities,
            self._updates,
            self._startup_repair,
        ):
            controller.notificationRequested.connect(self.notificationRequested)
            controller.errorOccurred.connect(self.errorOccurred)
        self._proxy.errorOccurred.connect(self.errorOccurred)
        self._settings.errorOccurred.connect(self.errorOccurred)
        self._settings.restartRequired.connect(lambda _reason: self.restartRequested.emit())
        self._cache.sendToReplacerRequested.connect(self._prepare_replacer_from_cache)
        self._cache.sendSelectionToReplacerRequested.connect(
            self._prepare_replacer_selection_from_cache
        )
        self._startup_repair.presentationRequested.connect(
            lambda: self.dashboardVisibilityRequested.emit(True)
        )

    @Slot(str, bool)
    def _prepare_replacer_from_cache(self, asset_id: str, as_replacement: bool) -> None:
        self._replacer.prepareCachedAsset(asset_id, as_replacement)
        self.pageRequested.emit('replacer')

    @Slot(list)
    def _prepare_replacer_selection_from_cache(self, asset_ids: list[str]) -> None:
        self._replacer.prepareCachedTargets(asset_ids)
        self.pageRequested.emit('replacer')

    @Property(QObject, constant=True)
    def replacer(self) -> QObject:
        return self._replacer

    @Property(QObject, constant=True)
    def cache(self) -> QObject:
        return self._cache

    @Property(QObject, constant=True)
    def modifications(self) -> QObject:
        return self._modifications

    @Property(QObject, constant=True)
    def proxy(self) -> QObject:
        return self._proxy

    @Property(QObject, constant=True)
    def settings(self) -> QObject:
        return self._settings

    @Property(QObject, constant=True)
    def logs(self) -> QObject:
        return self._logs

    @Property(QObject, constant=True)
    def subplaces(self) -> QObject:
        return self._subplaces

    @Property(QObject, constant=True)
    def utilities(self) -> QObject:
        return self._utilities

    @Property(QObject, constant=True)
    def updates(self) -> QObject:
        return self._updates

    @Property(QObject, constant=True)
    def startupRepair(self) -> QObject:  # noqa: N802
        return self._startup_repair

    @Property(str, constant=True)
    def appName(self) -> str:  # noqa: N802
        return APP_NAME

    @Property(str, constant=True)
    def version(self) -> str:
        return __version__

    @Property(str, constant=True)
    def iconUrl(self) -> str:  # noqa: N802
        icon_path = get_icon_path()
        return QUrl.fromLocalFile(str(icon_path)).toString() if icon_path else ''

    @Property(str, constant=True)
    def platformName(self) -> str:  # noqa: N802
        return (
            'Windows'
            if sys.platform == 'win32'
            else 'macOS'
            if sys.platform == 'darwin'
            else 'Linux'
        )

    @Property(bool, constant=True)
    def showDashboardOnStart(self) -> bool:  # noqa: N802
        return self._show_dashboard

    @Property(bool, notify=firstRunChanged)
    def firstRun(self) -> bool:  # noqa: N802
        return not bool(self._settings._config.first_time_setup_complete)

    @Slot()
    def completeFirstRun(self) -> None:  # noqa: N802
        if not self.firstRun:
            return
        self._settings._config.first_time_setup_complete = True
        self._settings._config.env_proxy_migration_v1_complete = True
        self.firstRunChanged.emit()
        self.setupCompleted.emit()

    @Slot(str)
    def openUrl(self, value: str) -> None:  # noqa: N802
        from PySide6.QtGui import QDesktopServices

        url = QUrl(value)
        if not url.scheme():
            url.setScheme('https')
        QDesktopServices.openUrl(url)

    @Slot()
    def openRepository(self) -> None:  # noqa: N802
        value = APP_REPO if APP_REPO.startswith('http') else f'https://{APP_REPO}'
        self.openUrl(value)

    @Slot()
    def openDiscord(self) -> None:  # noqa: N802
        value = APP_DISCORD if APP_DISCORD.startswith('http') else f'https://{APP_DISCORD}'
        self.openUrl(value)

    @Slot()
    def openConfigsFolder(self) -> None:  # noqa: N802
        open_folder(CONFIGS_FOLDER)

    @Slot()
    def openLogsFolder(self) -> None:  # noqa: N802
        open_folder(LOGS_DIR)

    @Slot(str)
    def copyText(self, value: str) -> None:  # noqa: N802
        from PySide6.QtGui import QGuiApplication

        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(value)

    @Slot(str, result=str)
    def localPath(self, url_value: str) -> str:  # noqa: N802
        url = QUrl(url_value)
        return url.toLocalFile() if url.isLocalFile() else str(Path(url_value).expanduser())
