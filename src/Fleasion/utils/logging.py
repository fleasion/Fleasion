"""Logging utilities."""

import threading
from datetime import datetime
from typing import Any


class LogBuffer:
    """Thread-safe log buffer with batched callback notifications."""

    def __init__(self):
        self._buffer: list[str] = []
        self._callbacks: list[Any] = []
        self._lock = threading.Lock()
        # Batching state
        self._pending_notifications = False
        self._batch_timer = None

    def log(self, category: str, message: str):
        """Add a log entry (callbacks are batched to reduce overhead)."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        entry = f'[{timestamp}] [{category}] {message}'

        with self._lock:
            self._buffer.append(entry)

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
