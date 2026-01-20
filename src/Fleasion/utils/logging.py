"""Logging utilities."""

from typing import Any


class LogBuffer:
    """Thread-safe log buffer for storing application logs."""

    def __init__(self):
        self._buffer: list[str] = []
        self._callbacks: list[Any] = []

    def log(self, category: str, message: str):
        """Add a log entry."""
        entry = f'[{category}] {message}'
        self._buffer.append(entry)
        # Notify any registered callbacks
        for callback in self._callbacks:
            callback()

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
