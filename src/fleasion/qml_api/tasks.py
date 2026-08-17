"""Non-blocking task state used by QML-facing services."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
import threading
from typing import Any

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot


class TaskState(QObject):
    """Run short background operations while exposing progress to QML."""

    busyChanged = Signal()
    messageChanged = Signal()
    failed = Signal(str)
    succeeded = Signal(object)
    _completed = Signal(object, object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._busy = False
        self._message = ''
        self._shutting_down = False
        self._thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._completed.connect(self._apply_result, Qt.ConnectionType.QueuedConnection)

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=messageChanged)
    def message(self) -> str:
        return self._message

    def run(self, message: str, operation: Callable[[], Any]) -> bool:
        return self.run_cancellable(message, lambda _cancel_event: operation())

    def run_cancellable(
        self,
        message: str,
        operation: Callable[[threading.Event], Any],
    ) -> bool:
        """Run one operation on a cancellable daemon worker."""
        if self._busy or self._shutting_down:
            return False
        self._busy = True
        self._message = message
        cancel_event = threading.Event()
        self._cancel_event = cancel_event
        self.busyChanged.emit()
        self.messageChanged.emit()
        future: Future[Any] = Future()
        future.add_done_callback(self._finish)

        def execute() -> None:
            if cancel_event.is_set():
                future.cancel()
                return
            try:
                result = operation(cancel_event)
            except Exception as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

        self._thread = threading.Thread(
            target=execute,
            name='fleasion-qml-task',
            daemon=True,
        )
        self._thread.start()
        return True

    @Slot()
    def cancel(self) -> None:
        """Request cancellation of the active operation."""
        self._cancel_event.set()

    def _finish(self, future: Future[Any]) -> None:
        if self._shutting_down:
            return
        try:
            result = future.result()
        except Exception as exc:
            try:
                self._completed.emit(None, exc)
            except RuntimeError:
                return
        else:
            try:
                self._completed.emit(result, None)
            except RuntimeError:
                return

    @Slot(object, object)
    def _apply_result(self, result: Any, error: Exception | None) -> None:
        if self._shutting_down:
            return
        if error is not None:
            self.failed.emit(str(error))
        else:
            self.succeeded.emit(result)
        self._busy = False
        self._message = ''
        self._thread = None
        self.busyChanged.emit()
        self.messageChanged.emit()

    @Slot()
    def shutdown(self, *, wait: bool = False, timeout: float = 2.0) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._cancel_event.set()
        thread = self._thread
        if (
            wait
            and thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=max(0.0, timeout))
