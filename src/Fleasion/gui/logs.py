"""Logs window."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QLabel, QTextEdit, QVBoxLayout

from ..utils import APP_NAME, get_icon_path, log_buffer, time_tracker


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

        # Time wasted label
        self.time_label = QLabel()
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.time_label.setStyleSheet('color: #888; font-size: 9pt;')
        self._refresh_time_label()
        layout.addWidget(self.time_label)

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

        self.time_timer = QTimer()
        self.time_timer.timeout.connect(self._refresh_time_label)
        self.time_timer.start(1000)

    def _update_logs(self):
        """Update the logs display.

        Appends only new entries using a background cursor so the viewport
        position is never disturbed.  Auto-scrolling to the bottom only
        happens when the user is already at (or within a few pixels of) the
        bottom, so reading older entries is never interrupted.
        """
        from PyQt6.QtGui import QTextCursor

        logs = log_buffer.get_all()
        count = len(logs)
        if count != self._last_count:
            scrollbar = self.text_edit.verticalScrollBar()
            was_at_bottom = scrollbar.value() >= scrollbar.maximum() - 4

            new_text = '\n'.join(logs[self._last_count:])
            prefix = '\n' if self._last_count > 0 else ''

            # Insert at the end via a cursor that is *not* the widget's
            # visible cursor -- Qt will not auto-scroll as a result.
            cursor = QTextCursor(self.text_edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(prefix + new_text)

            self._last_count = count

            if was_at_bottom:
                scrollbar.setValue(scrollbar.maximum())

    def _refresh_time_label(self):
        """Update the time wasted label."""
        total = time_tracker.get_total_seconds()
        self.time_label.setText(f'Time wasted: {time_tracker.format_duration(total)}')

    def closeEvent(self, event):
        """Handle window close event."""
        self.timer.stop()
        self.time_timer.stop()
        super().closeEvent(event)
