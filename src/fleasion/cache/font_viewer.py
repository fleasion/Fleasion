"""Font viewer widget for previewing TrueType and OpenType fonts."""

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr
from fleasion.utils import log_buffer


class FontViewerWidget(QWidget):
    """Widget for previewing font files (TTF, OTF, TTC)."""

    def __init__(self, font_data: bytes, parent: QWidget | None = None) -> None:
        """
        Initialize font viewer.

        Args:
            font_data: Raw bytes of the font file
            parent: Parent widget
        """
        super().__init__(parent)
        self.font_data = font_data
        self.font_id = -1
        self.font_family = 'Unknown'

        self._load_font()
        self._setup_ui()

    def _load_font(self) -> None:
        """Load font from bytes and register with Qt."""
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            log_buffer.log('FontViewer', f'Loading font ({len(self.font_data)} bytes)')

            # Write to temporary file so Qt can load it
            temp_dir = Path(tempfile.gettempdir()) / 'fleasion_fonts'
            temp_dir.mkdir(exist_ok=True)

            # Determine extension based on magic bytes
            ext = '.ttf'
            if self.font_data.startswith(b'\x00\x01\x00\x00'):
                ext = '.ttf'
            elif self.font_data.startswith(b'OTTO'):
                ext = '.otf'
            elif self.font_data.startswith(b'ttcf'):
                ext = '.ttc'

            temp_file = temp_dir / f'preview_font{ext}'
            temp_file.write_bytes(self.font_data)

            # Register font with Qt
            self.font_id = QFontDatabase.addApplicationFont(str(temp_file))

            if self.font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(self.font_id)
                if families:
                    self.font_family = families[0]
                    log_buffer.log('FontViewer', f'Font loaded: {self.font_family}')
                else:
                    log_buffer.log('FontViewer', 'Font loaded but no family names found')
            else:
                log_buffer.log('FontViewer', 'Failed to load font')

        except Exception as e:  # ruff: ignore[blind-except]
            log_buffer.log('FontViewer', f'Font load error: {e}')

    def _setup_ui(self) -> None:  # ruff: ignore[too-many-locals, too-many-statements]
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # Font info section
        info_group = QGroupBox(tr('ui.cache.font_viewer.font_information'))
        info_layout = QVBoxLayout()

        escaped_family = self.font_family.replace('&', '&amp;')
        family_label = QLabel(tr('ui.cache.font_viewer.b_family_b_value', value0=escaped_family))
        family_label.setWordWrap(True)
        info_layout.addWidget(family_label)

        # Check if font loaded successfully
        if self.font_id < 0:
            error_label = QLabel(tr('ui.cache.font_viewer.font_could_not_be_loaded_for_preview'))
            error_label.setStyleSheet('color: #c90;')
            info_layout.addWidget(error_label)

        info_group.setLayout(info_layout)
        layout.addWidget(info_group)

        # Font size slider
        size_layout = QHBoxLayout()
        size_layout.addWidget(QLabel(tr('ui.cache.font_viewer.size')))

        self.size_slider = QSlider(Qt.Orientation.Horizontal)
        self.size_slider.setRange(8, 72)
        self.size_slider.setValue(24)
        self.size_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.size_slider.setTickInterval(8)
        self.size_slider.valueChanged.connect(self._update_preview)
        size_layout.addWidget(self.size_slider)

        self.size_label = QLabel(tr('ui.cache.font_viewer.24_pt'))
        self.size_label.setFixedWidth(50)
        size_layout.addWidget(self.size_label)

        layout.addLayout(size_layout)

        # Preview text editor
        preview_group = QGroupBox(tr('ui.cache.font_viewer.preview'))
        preview_layout = QVBoxLayout()

        self.preview_text = QTextEdit()
        self.preview_text.setPlainText(
            'The Quick Brown Fox\nJumps Over The Lazy Dog\n\n0123456789\n!@#$%^&*()'
        )
        self.preview_text.setMinimumHeight(200)

        # Use the loaded font for preview
        if self.font_id >= 0:
            preview_font = QFont(self.font_family)
            preview_font.setPointSize(self.size_slider.value())
            self.preview_text.setFont(preview_font)

        self.preview_text.textChanged.connect(self._on_preview_text_changed)
        preview_layout.addWidget(self.preview_text)

        preview_group.setLayout(preview_layout)
        layout.addWidget(preview_group)

        # Sample text section
        samples_group = QGroupBox(tr('ui.cache.font_viewer.common_samples'))
        samples_layout = QVBoxLayout()

        # Create a scrollable area for samples
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        samples_container = QWidget()
        samples_container_layout = QVBoxLayout()
        samples_container_layout.setContentsMargins(0, 0, 0, 0)
        samples_container_layout.setSpacing(4)

        samples = [
            tr('font_viewer.sample.lowercase'),
            tr('font_viewer.sample.uppercase'),
            tr('font_viewer.sample.digits'),
            tr('font_viewer.sample.symbols'),
            tr('font_viewer.sample.quick_brown_fox'),
            tr('font_viewer.sample.pack_my_box'),
            tr('font_viewer.sample.quick_zebras'),
        ]

        for sample_text in samples:
            sample_label = QLabel(sample_text)
            if self.font_id >= 0:
                sample_font = QFont(self.font_family)
                sample_font.setPointSize(14)
                sample_label.setFont(sample_font)
            sample_label.setWordWrap(True)
            samples_container_layout.addWidget(sample_label)

        samples_container_layout.addStretch()
        samples_container.setLayout(samples_container_layout)
        scroll.setWidget(samples_container)

        samples_layout.addWidget(scroll)
        samples_group.setLayout(samples_layout)
        layout.addWidget(samples_group)

        self.setLayout(layout)

    def _update_preview(self) -> None:
        """Update preview text size based on slider."""
        size = self.size_slider.value()
        self.size_label.setText(tr('ui.cache.font_viewer.value_pt', value0=size))

        if self.font_id >= 0:
            preview_font = QFont(self.font_family)
            preview_font.setPointSize(size)
            self.preview_text.setFont(preview_font)

    def _on_preview_text_changed(self) -> None:
        """Handle preview text changes."""
        # Text can be changed by user, just update the font if needed
        if self.font_id >= 0:
            current_font = self.preview_text.font()
            if current_font.family() != self.font_family:
                current_font.setFamily(self.font_family)
                self.preview_text.setFont(current_font)
