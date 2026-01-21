"""Logs window."""

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QTextEdit, QVBoxLayout

from ..utils import APP_NAME, get_icon_path, log_buffer


class LogsWindow(QDialog):
    """Logs viewer window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'{APP_NAME} - Logs')
        self.resize(600, 400)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._last_count = 0
        self._setup_ui()
        self._set_icon()
        self._start_updates()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Text area
        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(self._get_monospace_font())
        layout.addWidget(self.text_edit)

        self.setLayout(layout)

    def _get_monospace_font(self):
        """Get a monospace font."""
        from PyQt6.QtGui import QFont

        font = QFont('Consolas', 10)
        font.setStyleHint(QFont.StyleHint.Monospace)
        return font

    def _start_updates(self):
        """Start periodic updates."""
        self.timer = QTimer()
        self.timer.timeout.connect(self._update_logs)
        self.timer.start(250)
        self._update_logs()

    def _update_logs(self):
        """Update the logs display."""
        logs = log_buffer.get_all()
        if len(logs) != self._last_count:
            self.text_edit.setPlainText(log_buffer.get_text())
            # Scroll to bottom
            scrollbar = self.text_edit.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            self._last_count = len(logs)

    def closeEvent(self, event):
        """Handle window close event."""
        self.timer.stop()
        super().closeEvent(event)
