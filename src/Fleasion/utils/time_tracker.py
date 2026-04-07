"""Session time tracker."""

import time


class TimeTracker:
    """Tracks cumulative 'time wasted' across sessions.

    Call ``init(saved_seconds)`` once at startup with the value loaded from
    settings.  ``get_total_seconds()`` returns stored + current-session elapsed.
    Call ``save(config_manager)`` to persist (done on exit and periodically).
    """

    def __init__(self):
        self._saved_seconds: int = 0
        self._session_start: float = time.monotonic()

    def init(self, saved_seconds: int) -> None:
        """Initialise with the previously saved total."""
        self._saved_seconds = max(0, int(saved_seconds))
        self._session_start = time.monotonic()

    def get_total_seconds(self) -> int:
        """Return total seconds wasted (all sessions including current)."""
        return self._saved_seconds + int(time.monotonic() - self._session_start)

    def save(self, config_manager) -> None:
        """Persist current total to settings."""
        config_manager.time_wasted_seconds = self.get_total_seconds()

    @staticmethod
    def format_duration(seconds: int) -> str:
        """Format seconds as a human-readable string."""
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f'{h}h {m}m {s}s'
        if m:
            return f'{m}m {s}s'
        return f'{s}s'


time_tracker = TimeTracker()

# This is a very important feature trust me bro!!