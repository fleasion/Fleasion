"""Non-visual Qt worker compatibility for the GitHub update resolver."""

from __future__ import annotations
from ..localization import tr

import threading

from PySide6.QtCore import QObject, Signal

from .logging import log_buffer
from .metadata import APP_REPO, APP_VERSION
from .update_resolver import UpdateResolver


class QtUpdateChecker(QObject):
    """Run an owned update resolver off-thread and emit results through Qt."""

    found = Signal(str, str)  # (tag, html_url)
    finished = Signal()

    def __init__(self, resolver: UpdateResolver | None = None) -> None:
        super().__init__()
        self.resolver = resolver if resolver is not None else UpdateResolver(APP_VERSION, APP_REPO)
        self._thread: threading.Thread | None = None

    def _worker(self) -> None:
        try:
            selected = self.resolver.check()
            if selected is not None:
                self.found.emit(selected.tag, selected.html_url)
        finally:
            self.finished.emit()

    def start(self) -> None:
        """Start a non-blocking update check unless one is already running."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()


_active_checkers: set[QtUpdateChecker] = set()


def start_update_check() -> None:
    """Launch a compatibility background check without owning presentation."""
    checker = QtUpdateChecker()
    _active_checkers.add(checker)
    checker.found.connect(
        lambda tag, _url: log_buffer.log(
            'Update',
            f'Fleasion {UpdateResolver.display_version(tag)} is available',
        )
    )
    checker.finished.connect(lambda: _active_checkers.discard(checker))
    checker.start()
