"""GitHub update checker."""

import re
import threading
import webbrowser

import requests
from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QHBoxLayout, QVBoxLayout, QPushButton
from PyQt6.QtGui import QIcon

from .paths import APP_VERSION

_RELEASES_API = 'https://api.github.com/repos/qrhrqiohj/Fleasion/releases/latest'
_RELEASES_PAGE = 'https://github.com/qrhrqiohj/Fleasion/releases/latest'
_TIMEOUT = 10


class _UpdateSignal(QObject):
    """Qt bridge that lets the worker thread safely notify the main thread."""

    found = pyqtSignal(str, str)  # (tag, html_url)


def _display_version(tag: str) -> str:
    """Strip leading non-digit characters from a release tag (e.g. 'v1.5.2' -> '1.5.2').

    If the tag contains no digits at all, return it unchanged (e.g. 'OUT OF BETA').
    """
    stripped = re.sub(r'^[^\d]+', '', tag).strip()
    return stripped if stripped else tag.strip()


def _show_update_dialog(tag: str, html_url: str) -> None:
    """Called on the Qt main thread to display the update popup.

    Uses a small `QDialog` so button placement is deterministic (Cancel left, Open right).
    """
    latest_display = _display_version(tag)
    current = APP_VERSION.strip()

    dialog = QDialog()
    dialog.setWindowTitle('Update Available')

    # Try to set the window icon to match the rest of the app
    try:
        from .paths import get_icon_path

        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))
    except Exception:
        pass

    main_layout = QVBoxLayout(dialog)
    label = QLabel(
        f'A newer version of Fleasion is available!\n\n'
        f'Latest:   {latest_display}\n'
        f'Current:  {current}'
    )
    label.setWordWrap(True)
    main_layout.addWidget(label)

    btn_layout = QHBoxLayout()
    cancel_btn = QPushButton('Cancel')
    open_btn = QPushButton('Open')

    # Right-align both buttons: add a stretch first, then the buttons.
    btn_layout.addStretch(1)
    btn_layout.addWidget(cancel_btn)
    btn_layout.addWidget(open_btn)
    main_layout.addLayout(btn_layout)

    # Make Open the default so Enter triggers Open by default.
    open_btn.setDefault(True)

    open_btn.clicked.connect(lambda: (dialog.accept(), webbrowser.open(html_url)))
    cancel_btn.clicked.connect(dialog.reject)

    dialog.exec()


def _worker(signal: _UpdateSignal) -> None:
    """Background thread: fetch the latest GitHub release and emit if an update exists."""
    try:
        response = requests.get(
            _RELEASES_API,
            timeout=_TIMEOUT,
            headers={'Accept': 'application/vnd.github+json'},
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return

    tag: str = (data.get('tag_name') or '').strip()
    # TEST_ONLY: force tag for equality testing
    # tag = '1.5.1'
    if not tag:
        return

    html_url: str = (data.get('html_url') or _RELEASES_PAGE).strip()

    has_digits = bool(re.search(r'\d', tag))
    latest_cmp = _display_version(tag) if has_digits else tag.strip()
    current_cmp = APP_VERSION.strip()

    if latest_cmp == current_cmp:
        return

    # Thread-safe: PyQt6 signals can be emitted from any thread.
    signal.found.emit(tag, html_url)


def start_update_check() -> None:
    """Launch a non-blocking background thread that checks for a newer GitHub release."""
    bridge = _UpdateSignal()
    bridge.found.connect(_show_update_dialog)

    # Keep bridge alive for the duration of the thread by storing a reference on the thread.
    thread = threading.Thread(target=_worker, args=(bridge,), daemon=True)
    thread._bridge = bridge  # prevent GC before thread finishes
    thread.start()
