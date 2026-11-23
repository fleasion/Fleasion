"""About window."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QLabel, QPushButton, QVBoxLayout

from fleasion.localization import tr
from fleasion.utils import (
    APP_AUTHOR,
    APP_CONCEPT,
    APP_LOGIC,
    APP_NAME,
    APP_REPO,
    APP_VERSION,
)
from fleasion.widgets.text import style_text


class AboutWindow(QDialog):
    """About dialog window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(tr('ui.gui.about.about'))
        self.setFixedSize(360, 240)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setSpacing(0)
        layout.setContentsMargins(24, 20, 24, 20)

        # App name
        name_label = QLabel(APP_NAME)
        style_text(name_label, point_size=16, bold=True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name_label)

        # Version
        version_label = QLabel(tr('ui.gui.about.version_value', value0=APP_VERSION))
        style_text(version_label, tone='muted', point_size=9)
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
        by_label = QLabel(tr('ui.gui.about.b_by_b_value', value0=APP_AUTHOR))
        by_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(by_label)

        logic_label = QLabel(tr('ui.gui.about.b_logic_b_value', value0=APP_LOGIC))
        logic_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logic_label)

        concept_label = QLabel(tr('ui.gui.about.b_concept_b_value', value0=APP_CONCEPT))
        concept_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(concept_label)

        handles_label = QLabel(tr('ui.gui.about.discord_handles'))
        style_text(handles_label, tone='muted', point_size=8)
        handles_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(handles_label)

        layout.addSpacing(10)

        # Repo link
        repo_label = QLabel(
            tr('ui.gui.about.b_distributed_at_b_a_href_value', value0=APP_REPO, value1=APP_REPO)
        )
        repo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        repo_label.setOpenExternalLinks(True)
        repo_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        repo_label.setWordWrap(True)
        layout.addWidget(repo_label)

        layout.addSpacing(10)

        # Close button
        close_btn = QPushButton(tr('ui.gui.about.close'))
        close_btn.setMinimumWidth(max(100, close_btn.sizeHint().width()))
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignCenter)

        self.setLayout(layout)
