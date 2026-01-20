"""Delete cache window."""

import threading
import time

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QTextEdit, QVBoxLayout

from ..utils import APP_NAME, delete_cache, get_icon_path, log_buffer


class DeleteCacheWindow(QDialog):
    """Delete cache result window."""

    log_signal = pyqtSignal(str)
    done_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} - Delete Cache')
        self.setFixedSize(400, 200)
        self._setup_ui()
        self._set_icon()
        self._start_deletion()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)

        # Title
        title_label = QLabel('Deleting Cache...')
        title_label.setStyleSheet('font-size: 11pt; font-weight: bold;')
        layout.addWidget(title_label)

        # Status text area
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setFont(self._get_monospace_font())
        self.status_text.setFixedHeight(90)
        layout.addWidget(self.status_text)

        # Close button
        self.close_btn = QPushButton('Close')
        self.close_btn.setEnabled(False)
        self.close_btn.clicked.connect(self.accept)
        layout.addWidget(self.close_btn)

        self.setLayout(layout)

        # Connect signals
        self.log_signal.connect(self._append_log)
        self.done_signal.connect(self._on_done)

    def _get_monospace_font(self):
        """Get a monospace font."""
        from PyQt6.QtGui import QFont

        font = QFont('Consolas', 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def _append_log(self, message: str):
        """Append a log message."""
        self.status_text.append(message)

    def _on_done(self):
        """Called when deletion is complete."""
        self.status_text.append('\nDone.')
        self.close_btn.setEnabled(True)

    def _start_deletion(self):
        """Start the cache deletion in a background thread."""

        def perform():
            for msg in delete_cache():
                log_buffer.log('Cache', msg)
                self.log_signal.emit(msg)
                time.sleep(0.3)
            self.done_signal.emit()

        thread = threading.Thread(target=perform, daemon=True)
        thread.start()
