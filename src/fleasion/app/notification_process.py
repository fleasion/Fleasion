"""Run notification commands without blocking the Qt event loop."""

from __future__ import annotations

from PySide6.QtCore import QObject, QProcess, QTimer, Signal, Slot


class NotificationProcess(QObject):
    """Own an asynchronous notification command and its timeout."""

    failed = Signal()
    finished = Signal()

    def __init__(self, parent: QObject, *, timeout_ms: int = 5000) -> None:
        super().__init__(parent)
        self._completed = False
        self._cancelled = False
        self._timed_out = False
        self._process = QProcess(self)
        self._process.setStandardOutputFile(QProcess.nullDevice())
        self._process.setStandardErrorFile(QProcess.nullDevice())
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(self._on_timeout)
        self.finished.connect(self.deleteLater)

    def start(self, command: str, arguments: list[str]) -> None:
        """Start the command and return immediately."""
        self._process.start(command, arguments)
        if not self._completed:
            self._timer.start()

    def cancel(self) -> None:
        """Stop a pending command without sending a shutdown notification."""
        self._cancelled = True
        self._timer.stop()
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        else:
            self._complete(success=False)

    @Slot()
    def _on_timeout(self) -> None:
        self._timed_out = True
        self._process.kill()
        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._complete(success=False)

    @Slot(QProcess.ProcessError)
    def _on_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._complete(success=False)

    @Slot(int, QProcess.ExitStatus)
    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:
        self._complete(
            success=not self._timed_out
            and exit_code == 0
            and status == QProcess.ExitStatus.NormalExit
        )

    def _complete(self, *, success: bool) -> None:
        if self._completed:
            return
        self._completed = True
        self._timer.stop()
        if not success and not self._cancelled:
            self.failed.emit()
        self.finished.emit()
