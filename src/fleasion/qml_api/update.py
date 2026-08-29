"""Typed update-checking state and commands for QML."""

from __future__ import annotations

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QmlElement

from ..localization import tr
from ..utils.metadata import APP_REPO, APP_VERSION
from ..utils.update_resolver import ReleaseCandidate, UpdateResolver
from .tasks import TaskState

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0


@QmlElement
class UpdateApi(QObject):
    """Check GitHub releases in the background and expose presentation state."""

    stateChanged = Signal()
    checkingChanged = Signal()
    updateAvailable = Signal()
    checkCompleted = Signal(bool)
    notificationRequested = Signal(str, str, str)
    errorOccurred = Signal(str)

    def __init__(
        self,
        resolver: UpdateResolver | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._resolver = resolver or UpdateResolver(APP_VERSION, APP_REPO)
        self._task = TaskState(self)
        self._latest_version = ''
        self._release_url = ''
        self._status_text = tr('qml.dynamic.update.not_checked')
        self._manual_check = False
        self._task.busyChanged.connect(self.checkingChanged)
        self._task.succeeded.connect(self._apply_result)
        self._task.failed.connect(self._apply_failure)

    @Property(str, constant=True)
    def currentVersion(self) -> str:  # noqa: N802
        return self._resolver.current_version.strip()

    @Property(str, notify=stateChanged)
    def latestVersion(self) -> str:  # noqa: N802
        return self._latest_version

    @Property(str, notify=stateChanged)
    def releaseUrl(self) -> str:  # noqa: N802
        return self._release_url

    @Property(str, notify=stateChanged)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(bool, notify=stateChanged)
    def hasUpdate(self) -> bool:  # noqa: N802
        return bool(self._latest_version and self._release_url)

    @Property(bool, notify=checkingChanged)
    def checking(self) -> bool:
        return bool(self._task.property('busy'))

    @Slot(result=bool)
    def checkNow(self) -> bool:  # noqa: N802
        return self._start_check(manual=True)

    @Slot(result=bool)
    def checkAutomatic(self) -> bool:  # noqa: N802
        return self._start_check(manual=False)

    @Slot(result=bool)
    def openRelease(self) -> bool:  # noqa: N802
        if not self._release_url:
            return False
        return QDesktopServices.openUrl(QUrl(self._release_url))

    @Slot()
    def shutdown(self) -> None:
        self._task.shutdown()

    def _start_check(self, *, manual: bool) -> bool:
        if bool(self._task.property('busy')):
            return False
        self._manual_check = manual
        self._status_text = tr('qml.dynamic.update.checking_github')
        self.stateChanged.emit()
        return self._task.run(tr('qml.dynamic.update.checking_task'), self._resolver.check)

    @Slot(object)
    def _apply_result(self, result: object) -> None:
        manual = self._manual_check
        if isinstance(result, ReleaseCandidate):
            self._latest_version = UpdateResolver.display_version(result.tag)
            self._release_url = result.html_url
            self._status_text = tr('qml.dynamic.update.available', version=self._latest_version)
            self.stateChanged.emit()
            self.updateAvailable.emit()
            self.checkCompleted.emit(True)
            return
        self._latest_version = ''
        self._release_url = ''
        self._status_text = tr('qml.dynamic.update.up_to_date', version=self.currentVersion)
        self.stateChanged.emit()
        self.checkCompleted.emit(False)
        if manual:
            self.notificationRequested.emit(
                tr('qml.dynamic.update.none_available_title'),
                self._status_text,
                'success',
            )

    @Slot(str)
    def _apply_failure(self, message: str) -> None:
        self._status_text = tr('qml.dynamic.update.failed_status')
        self.stateChanged.emit()
        if self._manual_check:
            self.errorOccurred.emit(tr('qml.dynamic.update.failed_error', error=message))
