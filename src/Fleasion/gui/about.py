"""About window."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout

from ..utils import APP_AUTHOR, APP_DISCORD, APP_NAME, APP_VERSION, get_icon_path
from .theme import ThemeManager


class AboutWindow(QDialog):
    """About dialog window."""

    def __init__(self, proxy_running: bool = False):
        super().__init__()
        # Apply theme immediately to prevent white flicker
        ThemeManager.apply_to_widget(self)

        self.setWindowTitle(f'About {APP_NAME}')
        self.setFixedSize(350, 200)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self._setup_ui(proxy_running)
        self._set_icon()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self, proxy_running: bool):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setSpacing(5)
        layout.setContentsMargins(20, 20, 20, 20)

        # App name
        name_label = QLabel(APP_NAME)
        name_label.setStyleSheet('font-size: 14pt; font-weight: bold;')
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        # Version
        version_label = QLabel(f'Version {APP_VERSION}')
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        # Author
        author_label = QLabel(APP_AUTHOR)
        author_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(author_label)

        # Discord
        discord_label = QLabel(f'Distributed in {APP_DISCORD}')
        discord_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(discord_label)

        # Status
        status_text = 'Running' if proxy_running else 'Starting...'
        status_label = QLabel(f'\nStatus: {status_text}')
        status_label.setStyleSheet('font-weight: bold;')
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(status_label)

        layout.addStretch()

        # Close button
        close_btn = QPushButton('Close')
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setLayout(layout)
