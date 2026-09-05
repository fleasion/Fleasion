"""Cooperative, cancellable work on the Qt GUI thread."""

from __future__ import annotations

from time import perf_counter
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, QTimer, Signal

from fleasion.utils.logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Generator


class GuiWork(QObject):
    """Advance small GUI construction steps between input and paint events."""

    finished = Signal()

    def __init__(self, parent: QObject) -> None:
        super().__init__(parent)
        self._steps: Generator[None] | None = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

    def start(self, steps: Generator[None]) -> None:
        self.cancel()
        self._steps = steps
        self._timer.start(1)

    def cancel(self) -> None:
        self._timer.stop()
        steps, self._steps = self._steps, None
        if steps is not None:
            steps.close()

    def _advance(self) -> None:
        steps = self._steps
        if steps is None:
            return
        deadline = perf_counter() + 0.004
        try:
            while perf_counter() < deadline:
                next(steps)
        except StopIteration:
            self._steps = None
            self.finished.emit()
        except Exception as exc:  # ruff: ignore[blind-except]
            self.cancel()
            log_buffer.log('Startup', f'GUI preload failed: {exc}')
        else:
            self._timer.start(1)
