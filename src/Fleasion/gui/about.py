"""About window."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QVBoxLayout

from ..utils import APP_AUTHOR, APP_CONCEPT, APP_LOGIC, APP_NAME, APP_REPO, APP_VERSION, get_icon_path


class AboutWindow(QDialog):
    """About dialog window."""

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f'About {APP_NAME}')
        self.setFixedSize(360, 240)
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        self._setup_ui()
        self._set_icon()

    def _set_icon(self):
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(24, 20, 24, 20)

        # App name
        name_label = QLabel(APP_NAME)
        name_label.setStyleSheet('font-size: 16pt; font-weight: bold;')
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        # Version
        version_label = QLabel(f'Version {APP_VERSION}')
        version_label.setStyleSheet('color: palette(placeholder-text); font-size: 9pt;')
        version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version_label)

        layout.addSpacing(14)

        # Divider
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(line)

        layout.addSpacing(10)

        # Credits
        by_label = QLabel(f'<b>By:</b> {APP_AUTHOR}')
        by_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(by_label)

        logic_label = QLabel(f'<b>Logic:</b> {APP_LOGIC}')
        logic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logic_label)

        concept_label = QLabel(f'<b>Concept:</b> {APP_CONCEPT}')
        concept_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(concept_label)

        handles_label = QLabel('(Discord handles)')
        handles_label.setStyleSheet('color: palette(placeholder-text); font-size: 8pt;')
        handles_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(handles_label)

        layout.addSpacing(10)

        # Repo link
        repo_label = QLabel(
            f'<b>Distributed at:</b> '
            f'<a href="{APP_REPO}">{APP_REPO}</a>'
        )
        repo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        repo_label.setOpenExternalLinks(True)
        repo_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
        )
        repo_label.setWordWrap(True)
        layout.addWidget(repo_label)

        layout.addSpacing(10)

        # Close button
        close_btn = QPushButton('Close')
        close_btn.setFixedWidth(100)
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setLayout(layout)
