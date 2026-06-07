"""Logging utilities."""

import threading
from datetime import datetime
from typing import Any

from .paths import LOG_FILE, LOGS_DIR


class LogBuffer:
    """Thread-safe log buffer with batched callback notifications."""

    def __init__(self):
        self._buffer: list[str] = []
        self._callbacks: list[Any] = []
        self._lock = threading.Lock()
        # Batching state
        self._pending_notifications = False
        self._batch_timer = None
        self._prepare_log_file()

    def _prepare_log_file(self):
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
                rotated = LOG_FILE.with_suffix('.log.1')
                rotated.unlink(missing_ok=True)
                LOG_FILE.replace(rotated)
        except OSError:
            pass

    def log(self, category: str, message: str):
        """Add a log entry (callbacks are batched to reduce overhead)."""
        now = datetime.now()
        timestamp = now.strftime('%H:%M:%S')
        entry = f'[{timestamp}] [{category}] {message}'

        with self._lock:
            self._buffer.append(entry)
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                with LOG_FILE.open('a', encoding='utf-8') as handle:
                    handle.write(f'{now:%Y-%m-%d} {entry}\n')
            except OSError:
                pass

            # Schedule batched callback notification
            if not self._pending_notifications:
                self._pending_notifications = True
                # Use timer to batch notifications (reduces UI callback overhead)
                self._batch_timer = threading.Timer(0.05, self._notify_callbacks)  # 50ms batch window
                self._batch_timer.daemon = True
                self._batch_timer.start()

    def _notify_callbacks(self):
        """Notify all callbacks (called after batch window)."""
        with self._lock:
            self._pending_notifications = False
            # Execute callbacks outside lock to prevent deadlock
            callbacks_copy = self._callbacks.copy()

        for callback in callbacks_copy:
            try:
                callback()
            except Exception:
                pass  # Ignore callback errors

    def get_all(self) -> list[str]:
        """Get all log entries."""
        return self._buffer.copy()

    def get_text(self) -> str:
        """Get all logs as a single text string."""
        return '\n'.join(self._buffer) if self._buffer else 'No logs yet.'

    def add_callback(self, callback: Any):
        """Add a callback to be notified when new logs are added."""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Any):
        """Remove a callback."""
        if callback in self._callbacks:
            self._callbacks.remove(callback)


# Global log buffer
log_buffer = LogBuffer()
