"""Qt integration for the GitHub update resolver."""

from __future__ import annotations

import threading
import webbrowser

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from fleasion.localization import tr

from .metadata import APP_REPO, APP_VERSION
from .paths import get_icon_path
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


def _show_update_dialog(tag: str, html_url: str) -> None:
    """Display an available update on the Qt main thread."""
    latest_display = UpdateResolver.display_version(tag)
    current = APP_VERSION.strip()

    top_level_widgets = QApplication.topLevelWidgets()
    parent = next((widget for widget in top_level_widgets if widget.isVisible()), None)
    stays_on_top = any(
        widget.isVisible() and bool(widget.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)
        for widget in top_level_widgets
    )
    dialog = QDialog(parent)
    if stays_on_top:
        dialog.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
    dialog.setWindowTitle(tr('ui.utils.updater.update_available'))

    if icon_path := get_icon_path():
        dialog.setWindowIcon(QIcon(str(icon_path)))

    main_layout = QVBoxLayout(dialog)
    label = QLabel(
        tr(
            'ui.utils.updater.a_newer_version_of_fleasion_is_available',
            value0=latest_display,
            value1=current,
        )
    )
    label.setWordWrap(True)
    main_layout.addWidget(label)

    button_layout = QHBoxLayout()
    cancel_button = QPushButton(tr('ui.utils.updater.cancel'))
    open_button = QPushButton(tr('ui.utils.updater.open'))

    button_layout.addStretch(1)
    button_layout.addWidget(cancel_button)
    button_layout.addWidget(open_button)
    main_layout.addLayout(button_layout)

    open_button.setDefault(True)
    open_button.clicked.connect(lambda: (dialog.accept(), webbrowser.open(html_url)))
    cancel_button.clicked.connect(dialog.reject)

    dialog.exec()


def start_update_check() -> None:
    """Launch a non-blocking background check and show any available update."""
    checker = QtUpdateChecker()
    _active_checkers.add(checker)
    checker.found.connect(_show_update_dialog)
    checker.finished.connect(lambda: _active_checkers.discard(checker))
    checker.start()
