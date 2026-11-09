"""Logging utilities."""

from __future__ import annotations

import contextlib
import threading
from datetime import datetime
from time import monotonic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from .paths import LOG_FILE, LOGS_DIR

MAX_LOG_FILE_BYTES = 1 * 1024 * 1024


class LogBuffer:
    """Thread-safe log buffer with batched callback notifications."""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._callbacks: list[Callable[[], None]] = []
        self._lock = threading.Lock()
        # Batching state
        self._pending_notifications = False
        self._batch_timer: threading.Timer | None = None
        self._batch_deadline = 0.0
        self._prepare_log_file()

    def _prepare_log_file(self) -> None:
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            self._rotate_log_file_if_needed()
        except OSError:
            pass

    def _rotate_log_file_if_needed(self, incoming_bytes: int = 0) -> None:
        try:
            current_size = LOG_FILE.stat().st_size
        except OSError, ValueError:
            return
        if current_size + incoming_bytes <= MAX_LOG_FILE_BYTES:
            return
        rotated = LOG_FILE.with_suffix('.log.1')
        rotated.unlink(missing_ok=True)
        LOG_FILE.replace(rotated)

    def _append_log_line(self, line: str) -> None:
        self._rotate_log_file_if_needed(len(line.encode('utf-8')))
        try:
            handle = LOG_FILE.open('a', encoding='utf-8')
        except FileNotFoundError:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            handle = LOG_FILE.open('a', encoding='utf-8')
        with handle:
            handle.write(line)

    def log(self, category: str, message: str) -> None:
        """Add a log entry (callbacks are batched to reduce overhead)."""
        now = datetime.now().astimezone()
        timestamp = now.strftime('%H:%M:%S')
        entry = f'[{timestamp}] [{category}] {message}'

        with self._lock:
            self._buffer.append(entry)
            try:
                line = f'{now:%Y-%m-%d} {entry}\n'
                self._append_log_line(line)
            except OSError:
                pass

            # Schedule batched callback notification
            now_monotonic = monotonic()
            if not self._pending_notifications or (
                self._batch_timer is None and now_monotonic >= self._batch_deadline
            ):
                self._pending_notifications = True
                self._batch_deadline = now_monotonic + 0.05
                if self._callbacks:
                    self._start_batch_timer(0.05)

    def _start_batch_timer(self, delay: float) -> None:
        self._batch_timer = threading.Timer(delay, self._notify_callbacks)
        self._batch_timer.daemon = True
        self._batch_timer.start()

    def _notify_callbacks(self) -> None:
        """Notify all callbacks (called after batch window)."""
        with self._lock:
            self._pending_notifications = False
            self._batch_timer = None
            # Execute callbacks outside lock to prevent deadlock
            callbacks_copy = self._callbacks.copy()

        for callback in callbacks_copy:
            with contextlib.suppress(Exception):
                callback()

    def get_all(self) -> list[str]:
        """Get all log entries."""
        return self._buffer.copy()

    def get_since(self, index: int) -> tuple[int, list[str]]:
        """Return the current entry count and entries from an earlier count."""
        count = len(self._buffer)
        return count, self._buffer[index:count]

    def get_text(self) -> str:
        """Get all logs as a single text string."""
        return '\n'.join(self._buffer) if self._buffer else 'No logs yet.'

    def add_callback(self, callback: Callable[[], None]) -> None:
        """Add a callback to be notified when new logs are added."""
        with self._lock:
            self._callbacks.append(callback)
            if self._pending_notifications and self._batch_timer is None:
                remaining = self._batch_deadline - monotonic()
                if remaining > 0:
                    self._start_batch_timer(remaining)
                else:
                    self._pending_notifications = False

    def remove_callback(self, callback: Callable[[], None]) -> None:
        """Remove a callback."""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)


# Global log buffer
log_buffer = LogBuffer()
