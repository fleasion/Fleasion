"""Modifications tab — combined Fishstrap Mods + FastFlags panel."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from pathlib import Path

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..cache.tools.ktx_to_png import convert as ktx_to_png, strip_prefixed_ktx
from ..modifications.manager import ModificationManager, normalise_target_path
from ..modifications.fflag_profiles import FastFlagProfileManager
from ..modifications.platform_targets import (
    read_current_platform_original_asset,
    target_path_for_current_platform,
)
from ..utils import APP_CACHE_DIR, format_count, log_buffer, open_folder
from ..utils.http import http_get
from ..utils.threading import run_in_thread
from .file_drop import FileDropLineEdit
from .theme import ThemeManager

# ---------------------------------------------------------------------------
# Built-in entry definitions
# ---------------------------------------------------------------------------

AVATAR_MESHES = [
    (
        'Left Arm',
        target_path_for_current_platform(r'content\avatar\meshes\leftarm.mesh'),
    ),
    (
        'Left Leg',
        target_path_for_current_platform(r'content\avatar\meshes\leftleg.mesh'),
    ),
    (
        'Right Arm',
        target_path_for_current_platform(r'content\avatar\meshes\rightarm.mesh'),
    ),
    (
        'Right Leg',
        target_path_for_current_platform(r'content\avatar\meshes\rightleg.mesh'),
    ),
    ('Torso', target_path_for_current_platform(r'content\avatar\meshes\torso.mesh')),
    ('Head', target_path_for_current_platform(r'content\avatar\heads\head.mesh')),
]

HEAD_VARIANTS = [f'head{chr(c)}.mesh' for c in range(ord('A'), ord('P') + 1)]

SKYBOX_FACES = [
    (
        'Sky \u2014 Back',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_bk.tex'),
    ),
    (
        'Sky \u2014 Down',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_dn.tex'),
    ),
    (
        'Sky \u2014 Front',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_ft.tex'),
    ),
    (
        'Sky \u2014 Left',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_lf.tex'),
    ),
    (
        'Sky \u2014 Right',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_rt.tex'),
    ),
    (
        'Sky \u2014 Up',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_up.tex'),
    ),
]

INDOOR_FACES = [
    (
        'Indoor \u2014 Back',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_bk.tex'),
    ),
    (
        'Indoor \u2014 Down',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_dn.tex'),
    ),
    (
        'Indoor \u2014 Front',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_ft.tex'),
    ),
    (
        'Indoor \u2014 Left',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_lf.tex'),
    ),
    (
        'Indoor \u2014 Right',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_rt.tex'),
    ),
    (
        'Indoor \u2014 Up',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_up.tex'),
    ),
]

SOUNDS = [
    (
        'Footsteps (Plastic)',
        target_path_for_current_platform(r'content\sounds\action_footsteps_plastic.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Falling',
        target_path_for_current_platform(r'content\sounds\action_falling.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'Get Up',
        target_path_for_current_platform(r'content\sounds\action_get_up.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Jump',
        target_path_for_current_platform(r'content\sounds\action_jump.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Jump Land',
        target_path_for_current_platform(r'content\sounds\action_jump_land.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Swim',
        target_path_for_current_platform(r'content\sounds\action_swim.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Explosion',
        target_path_for_current_platform(r'content\sounds\impact_explosion_03.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Water Impact',
        target_path_for_current_platform(r'content\sounds\impact_water.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'Oof',
        target_path_for_current_platform(r'content\sounds\oof.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'Ouch',
        target_path_for_current_platform(r'content\sounds\ouch.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'Volume Slider',
        target_path_for_current_platform(r'content\sounds\volume_slider.ogg'),
        'bundled:empty.ogg',
    ),
]

if sys.platform.startswith('linux'):
    SOUNDS = [
        sound
        for sound in SOUNDS
        if sound[1].replace('\\', '/').strip('/') != 'content/sounds/ouch.ogg'
    ]

# File-type filter strings for QFileDialog
MESH_FILTER = 'Mesh Files (*.mesh *.obj);;All Files (*)'
IMAGE_FILTER = 'Image Files (*.png *.jpg *.jpeg *.tex);;All Files (*)'
# DDS textures — Roblox accepts a .png renamed to .dds as a drop-in replacement
DDS_FILTER = 'Image Files (*.dds *.png);;All Files (*)'
# JPG textures (moon/sun) — Roblox also accepts a .png renamed to .jpg
JPG_FILTER = 'Image Files (*.jpg *.jpeg *.png);;All Files (*)'
SOUND_FILTER = 'Audio Files (*.mp3 *.ogg *.wav);;All Files (*)'
FONT_FILTER = 'Font Files (*.ttf *.otf *.ttc);;All Files (*)'

TEXTURES = [
    # (display_name, target_path, file_filter)
    (
        'High Quality Studs — Diffuse',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\diffuse.dds'),
        DDS_FILTER,
    ),
    (
        'High Quality Studs — Normal',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\normal.dds'),
        DDS_FILTER,
    ),
    (
        'High Quality Studs — Detail',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\normaldetail.dds'),
        DDS_FILTER,
    ),
    (
        'Low Quality Studs',
        target_path_for_current_platform(r'PlatformContent\pc\textures\studs.dds'),
        DDS_FILTER,
    ),
    (
        'Shiftlock Cursor',
        target_path_for_current_platform(r'content\textures\MouseLockedCursor.png'),
        IMAGE_FILTER,
    ),
    (
        'Cursor — Pointing',
        target_path_for_current_platform(r'content\textures\Cursors\KeyboardMouse\ArrowCursor.png'),
        IMAGE_FILTER,
    ),
    (
        'Cursor — Arrow',
        target_path_for_current_platform(
            r'content\textures\Cursors\KeyboardMouse\ArrowFarCursor.png'
        ),
        IMAGE_FILTER,
    ),
    (
        'Cursor — IBeam',
        target_path_for_current_platform(r'content\textures\Cursors\KeyboardMouse\IBeamCursor.png'),
        IMAGE_FILTER,
    ),
    ('Moon', target_path_for_current_platform(r'content\sky\moon.jpg'), JPG_FILTER),
    ('Sun', target_path_for_current_platform(r'content\sky\sun.jpg'), JPG_FILTER),
]

# Status badge styling
_STATUS_STYLES = {
    'not_set': 'color: #888; font-style: italic;',
    'pending': 'color: #4a9eda;',
    'applied': 'font-style: normal;',
    'orphaned_stash': 'color: #c90; font-weight: bold;',
}

# ═══════════════════════════════════════════════════════════════════
# _RichTextButton — QPushButton-like label that renders HTML/rich text
# ═══════════════════════════════════════════════════════════════════


class _RichTextButton(QPushButton):
    """QPushButton that draws a label and a larger suffix character, each independently
    vertically centred so mixed font sizes don't shift each other's position."""

    def __init__(
        self,
        label: str,
        suffix: str = '',
        suffix_size_offset: int = 0,
        y_offset: int = 0,
        suffix_x_offset: int = 0,
        suffix_pixel_size: int | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._label = label
        self._suffix = suffix
        self._suffix_size_offset = suffix_size_offset
        self._y_offset = y_offset
        self._suffix_x_offset = suffix_x_offset
        self._suffix_pixel_size = suffix_pixel_size
        # Non-empty text so Qt includes normal button padding in sizeHint.
        super().setText('\u200b')

    def paintEvent(self, a0):
        from PyQt6.QtGui import QFont, QFontMetrics, QPainter, QPalette
        from PyQt6.QtWidgets import QStyle, QStyleOptionButton

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = ''
        painter = QPainter(self)
        st = self.style()
        if st is None:
            painter.end()
            return
        st.drawControl(QStyle.ControlElement.CE_PushButton, opt, painter, self)

        cr = st.subElementRect(QStyle.SubElement.SE_PushButtonContents, opt, self)
        if cr.isNull():
            cr = self.rect()

        enabled = bool(opt.state & QStyle.StateFlag.State_Enabled)
        color_grp = QPalette.ColorGroup.Normal if enabled else QPalette.ColorGroup.Disabled
        painter.setPen(self.palette().color(color_grp, QPalette.ColorRole.ButtonText))

        base_font = self.font()
        center_y = cr.y() + cr.height() / 2

        if self._suffix and (self._suffix_size_offset or self._suffix_pixel_size):
            large_font = QFont(base_font)
            if self._suffix_pixel_size:
                large_font.setPixelSize(self._suffix_pixel_size)
            else:
                pt = large_font.pointSize()
                if pt < 0:
                    pt = 9
                large_font.setPointSize(pt + self._suffix_size_offset)

            fm_base = QFontMetrics(base_font)
            fm_large = QFontMetrics(large_font)
            label_text = self._label + ' '
            label_w = fm_base.horizontalAdvance(label_text)
            arrow_w = fm_large.horizontalAdvance(self._suffix)
            start_x = int(cr.x() + (cr.width() - label_w - arrow_w) / 2)

            # baseline = center_y + (ascent - descent) / 2 centres each piece independently
            baseline_label = (
                int(center_y + (fm_base.ascent() - fm_base.descent()) / 2) + self._y_offset
            )
            baseline_arrow = (
                int(center_y + (fm_large.ascent() - fm_large.descent()) / 2) + self._y_offset
            )

            painter.setFont(base_font)
            painter.drawText(start_x, baseline_label, label_text)
            painter.setFont(large_font)
            painter.drawText(
                start_x + label_w + self._suffix_x_offset, baseline_arrow, self._suffix
            )
        else:
            fm = QFontMetrics(base_font)
            label_text = self._label + (' ' if self._suffix else '')
            label_w = fm.horizontalAdvance(label_text)
            suffix_w = fm.horizontalAdvance(self._suffix)
            w = label_w + suffix_w + (self._suffix_x_offset if self._suffix else 0)
            start_x = int(cr.x() + (cr.width() - w) / 2)
            baseline = int(center_y + (fm.ascent() - fm.descent()) / 2) + self._y_offset
            painter.setFont(base_font)
            painter.drawText(start_x, baseline, label_text)
            if self._suffix:
                painter.drawText(start_x + label_w + self._suffix_x_offset, baseline, self._suffix)

        painter.end()


# ═══════════════════════════════════════════════════════════════════
# CollapsibleSection
# ═══════════════════════════════════════════════════════════════════


class CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands its content."""

    _EXPANDED_ARROW = '\u25bc'
    _COLLAPSED_ARROW = '\u25b6'
    _DEFAULT_ARROW_STYLE = 'border: none;'
    _WINDOWS_COLLAPSED_ARROW_SIZE = 19
    _WINDOWS_EXPANDED_ARROW_STYLE = 'font-size: 11px; border: none;'
    _WINDOWS_COLLAPSED_ARROW_STYLE = f'font-size: {_WINDOWS_COLLAPSED_ARROW_SIZE}px; border: none;'

    def __init__(
        self,
        title: str,
        parent=None,
        expanded: bool = True,
        header_widgets: list[QWidget] | None = None,
    ):
        super().__init__(parent)

        self._expanded = expanded
        self._animation: QPropertyAnimation | None = None

        # --- Header row ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 4, 4, 4)

        self._arrow = QPushButton()
        self._arrow.setFixedSize(22, 22)
        self._arrow.setFlat(True)
        self._set_arrow_state(expanded)
        self._arrow.clicked.connect(self.toggle)
        header_layout.addWidget(self._arrow)

        self._title_label = QLabel(f'<b>{title}</b>')
        self._title_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._title_label.mousePressEvent = lambda _: self.toggle()
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        if header_widgets:
            for w in header_widgets:
                header_layout.addWidget(w)

        # --- Content container ---
        self._content = QWidget()
        self._content_layout = QVBoxLayout()
        self._content_layout.setContentsMargins(8, 0, 8, 8)
        self._content_layout.setSpacing(4)
        self._content.setLayout(self._content_layout)

        if not expanded:
            self._content.setMaximumHeight(0)

        # --- Separator ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        # --- Assemble ---
        main = QVBoxLayout()
        main.setContentsMargins(0, 0, 0, 4)
        main.setSpacing(0)
        main.addLayout(header_layout)
        main.addWidget(sep)
        main.addWidget(self._content)
        self.setLayout(main)

    def paintEvent(self, a0):  # noqa: N802
        """Draw a rounded-rect card that adapts to dark and light themes."""
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter, QPainterPath

        colors = ThemeManager.panel_colors(self.palette())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.fillPath(path, colors.section_background)
        painter.setPen(colors.section_border)
        painter.drawPath(path)
        painter.end()

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def _arrow_style(self, expanded: bool) -> str:
        """Return platform-specific arrow styling for Unicode triangle glyphs."""
        if os.name == 'nt':
            return (
                self._WINDOWS_EXPANDED_ARROW_STYLE
                if expanded
                else self._WINDOWS_COLLAPSED_ARROW_STYLE
            )
        return self._DEFAULT_ARROW_STYLE

    def _set_arrow_state(self, expanded: bool):
        self._arrow.setText(self._EXPANDED_ARROW if expanded else self._COLLAPSED_ARROW)
        self._arrow.setStyleSheet(self._arrow_style(expanded))

    def toggle(self):
        self._expanded = not self._expanded
        self._set_arrow_state(self._expanded)

        self._animation = QPropertyAnimation(self._content, b'maximumHeight')
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        if self._expanded:
            self._animation.setStartValue(self._content.maximumHeight())
            self._animation.setEndValue(self._content.sizeHint().height())
            self._animation.finished.connect(lambda: self._content.setMaximumHeight(16777215))
        else:
            # Capture the real rendered height so the animation starts from
            # the actual visible size rather than QWIDGETSIZE_MAX.
            actual = self._content.height()
            self._content.setMaximumHeight(actual)
            self._animation.setStartValue(actual)
            self._animation.setEndValue(0)

        self._animation.start()


# ═══════════════════════════════════════════════════════════════════
# NoWheelSpinBox — QSpinBox that ignores mouse wheel events
# ═══════════════════════════════════════════════════════════════════


class NoWheelSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events to prevent accidental value changes."""

    def wheelEvent(self, e):
        e.ignore()


class NoWheelSlider(QSlider):
    """QSlider that ignores wheel events to prevent accidental value changes."""

    def wheelEvent(self, e):
        e.ignore()


# ═══════════════════════════════════════════════════════════════════
# DropdownComboBox — QComboBox with ▼ indicator instead of OS arrow
# ═══════════════════════════════════════════════════════════════════


class DropdownComboBox(QComboBox):
    """QComboBox that paints ▼ as the dropdown indicator and ignores wheel events."""

    def wheelEvent(self, e):
        """Ignore wheel events to prevent accidental value changes."""
        e.ignore()

    def paintEvent(self, e):
        from PyQt6.QtWidgets import QStyle, QStyleOptionComboBox, QStylePainter

        style = self.style()
        if style is None:
            super().paintEvent(e)
            return

        painter = QStylePainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)

        # Draw the full combo box (frame, edit field, and arrow button border)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt)
        # Draw the selected-item label
        painter.drawControl(QStyle.ControlElement.CE_ComboBoxLabel, opt)

        # Overdraw the default OS arrow indicator with ▼
        arrow_rect = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            opt,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        painter.fillRect(arrow_rect.adjusted(1, 1, -1, -1), self.palette().button())
        painter.setPen(self.palette().buttonText().color())
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignCenter, '\u25bc')


class CompactBooleanComboBox(QComboBox):
    """A borderless True/False selector that blends into a table cell."""

    def wheelEvent(self, e):
        e.ignore()

    def paintEvent(self, e):
        painter = QPainter(self)
        if self.hasFocus() or self.underMouse():
            painter.fillRect(self.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().text().color())
        painter.drawText(
            self.rect().adjusted(4, 0, -18, 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.currentText(),
        )
        arrow_x = self.width() - 10
        arrow_y = self.height() // 2
        painter.drawLine(arrow_x - 3, arrow_y - 1, arrow_x, arrow_y + 2)
        painter.drawLine(arrow_x, arrow_y + 2, arrow_x + 3, arrow_y - 1)


class FastFlagValueDelegate(QStyledItemDelegate):
    """Create boolean selectors only while their cell is being edited."""

    _BOOLEAN_FLAG_PREFIXES = ('FFlag', 'DFFlag')

    def createEditor(self, parent, option, index):
        name = str(index.sibling(index.row(), 0).data() or '')
        if name.startswith(self._BOOLEAN_FLAG_PREFIXES):
            editor = CompactBooleanComboBox(parent)
            editor.addItems(['True', 'False'])
            editor.activated.connect(
                partial(self._commit_and_close_boolean_editor, editor)
            )
            return editor
        return super().createEditor(parent, option, index)

    def _commit_and_close_boolean_editor(self, editor: QComboBox, _selected_index: int):
        """Finish the table edit as soon as a boolean is picked from its popup."""
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def setEditorData(self, editor, index):
        if isinstance(editor, QComboBox):
            editor.setCurrentText(
                'True' if str(index.data() or '').strip().lower() == 'true' else 'False'
            )
            # A table double-click creates the combo editor but does not pass
            # the click on to the combo itself.  Defer opening the popup until
            # the editor has been installed and shown by the view, otherwise
            # the first double-click only produces the collapsed editor.
            QTimer.singleShot(0, editor.showPopup)
            return
        super().setEditorData(editor, index)

    def setModelData(self, editor, model, index):
        if isinstance(editor, QComboBox):
            model.setData(index, editor.currentText())
            return
        super().setModelData(editor, model, index)


# ═══════════════════════════════════════════════════════════════════
# ModRowWidget — the reusable row for each modifiable file
# ═══════════════════════════════════════════════════════════════════


class ModRowWidget(QWidget):
    """A single row representing one modification entry."""

    delete_requested = pyqtSignal(str)  # entry_id

    def __init__(
        self,
        manager: ModificationManager,
        display_name: str,
        target_path: str,
        file_filter: str = 'All Files (*)',
        deletable: bool = False,
        mute_bundled: str | None = None,
        is_font: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = manager
        self._display_name = display_name
        self._target_path = target_path
        self._file_filter = file_filter
        self._deletable = deletable
        self._mute_bundled = mute_bundled
        self._is_font = is_font
        self._entry_id: str | None = None

        self._setup_ui()

        # Connect to manager signals for live status updates
        manager.entry_status_changed.connect(self._on_status_changed)

        # Try to find an existing entry for this target
        self._sync_from_manager()

    def _setup_ui(self):
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Display name
        self._name_label = QLabel(self._display_name)
        self._name_label.setFixedWidth(160)
        layout.addWidget(self._name_label)

        # Status badge — trimmed width keeps 'Applied' close to the textbox
        self._status_label = QLabel('Not Set')
        self._status_label.setFixedWidth(72)
        self._status_label.setStyleSheet(_STATUS_STYLES['not_set'])
        layout.addWidget(self._status_label)

        # Source text field (expands to fill remaining row space)
        self._source_edit = FileDropLineEdit()
        self._source_edit.setPlaceholderText(
            'ID, URL (http://...), path (C:\\...), or "remove" to remove'
        )
        self._source_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._source_edit)

        # Debounce timer: apply 1 s after the user stops typing
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(1000)
        self._debounce.timeout.connect(self._apply_from_text)
        self._source_edit.textChanged.connect(lambda _: self._debounce.start())
        self._source_edit.editingFinished.connect(self._on_editing_finished)

        # Pending-visibility timer: show 'Applying...' only if apply takes > 500 ms
        self._pending_timer = QTimer()
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(500)
        self._pending_timer.timeout.connect(lambda: self._update_status('pending'))

        # Reset button
        self._reset_btn = _RichTextButton('\u21ba', y_offset=-1)
        self._reset_btn.setToolTip('Reset to original')
        self._reset_btn.setFixedWidth(28)
        self._reset_btn.setVisible(False)
        self._reset_btn.clicked.connect(self._on_reset)
        layout.addWidget(self._reset_btn)

        # Browse button — to the right of reset (collapses next to textbox when reset hidden)
        self._browse_btn = QPushButton('Browse...')
        self._browse_btn.setFixedWidth(65)
        self._browse_btn.clicked.connect(self._on_browse)
        layout.addWidget(self._browse_btn)

        # Preview button
        preview_arrow_size = (
            CollapsibleSection._WINDOWS_COLLAPSED_ARROW_SIZE if os.name == 'nt' else None
        )
        self._preview_btn = _RichTextButton(
            'Preview',
            '\u25b6',
            suffix_x_offset=3,
            suffix_pixel_size=preview_arrow_size,
        )
        self._preview_btn.setFixedWidth(82)
        self._preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self._preview_btn)

        # Delete button (custom rows only)
        if self._deletable:
            self._del_btn = _RichTextButton('\u2715', y_offset=-1)
            self._del_btn.setFixedWidth(28)
            self._del_btn.setToolTip('Remove modification')
            self._del_btn.clicked.connect(self._on_delete)
            layout.addWidget(self._del_btn)

        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Sync with manager
    # ------------------------------------------------------------------

    def _sync_from_manager(self):
        """Find our entry in the manager (by target_path) and update UI."""
        for entry in self._manager.entries:
            if entry.get('target_path') == self._target_path:
                self._entry_id = entry['id']
                status = entry.get('status', 'not_set')
                self._update_status(status, entry.get('error_message', ''))
                # Populate textbox with the persisted source value.
                self._set_source_text_silent(self._get_source_display_text())
                # Even when the JSON says not_set, check for an orphaned stash:
                # the file may be modified on disk without a tracked entry.
                if status == 'not_set':
                    self._check_for_orphaned_stash()
                return
        # No entry in JSON at all — still check for an orphaned stash.
        self._check_for_orphaned_stash()

    def _check_for_orphaned_stash(self):
        """Show a warning if a stash file exists but Fleasion has no active record."""
        from ..modifications.manager import MOD_ORIGINALS_DIR, normalise_target_path

        roblox_dirs = self._manager.roblox_dirs
        if not roblox_dirs:
            return
        try:
            target_path = normalise_target_path(self._target_path)
        except ValueError:
            return
        stash = MOD_ORIGINALS_DIR / roblox_dirs[0].name / target_path
        if stash.is_file():
            self._update_status('orphaned_stash')
            self._status_label.setToolTip(
                'A stash of the original file was found on disk but Fleasion has '
                'no active record for this modification. This can happen if you '
                'manually replaced the file, or if Fleasion closed unexpectedly. '
                'Click \u21ba to restore the original file.'
            )

    def _on_status_changed(self, entry_id: str, status: str, error_msg: str):
        if entry_id == self._entry_id:
            self._update_status(status, error_msg)

    def _update_status(self, status: str, error_msg: str = ''):
        # Final status: stop the pending-visibility timer
        if status != 'pending':
            self._pending_timer.stop()

        # 'error' shows same label/style as 'not_set'; red textbox is the indicator
        display_status = 'not_set' if status == 'error' else status

        labels = {
            'not_set': 'Not Set',
            'pending': 'Applying...',
            'applied': 'Applied',
            'orphaned_stash': 'Ext. Modified',
        }
        self._status_label.setText(labels.get(display_status, display_status))
        self._status_label.setStyleSheet(_STATUS_STYLES.get(display_status, ''))

        if status == 'error':
            self._show_source_error(error_msg or 'Failed to apply')
        elif status in ('applied', 'not_set'):
            self._clear_source_error()

        if status not in ('orphaned_stash',):
            self._status_label.setToolTip('')

        self._reset_btn.setVisible(status in ('applied', 'error', 'orphaned_stash'))

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _apply_source(self, source_type: str, source_value: str):
        entry_data = {
            'display_name': self._display_name,
            'target_path': self._target_path,
            'source_type': source_type,
            'source_value': source_value,
        }
        if self._is_font:
            entry_data['_is_font'] = True

        if self._entry_id:
            self._manager.update_entry(
                self._entry_id, source_type=source_type, source_value=source_value
            )
        else:
            self._entry_id = self._manager.add_entry(entry_data)

        # Show 'Applying...' only if the apply takes longer than 500 ms
        self._pending_timer.start()

    def _on_edit(self):
        # Kept as a no-op stub — inline textbox replaced the Edit dialog.
        pass

    def _on_reset(self):
        self._debounce.stop()
        if self._entry_id:
            if not self._manager.clear_entry(self._entry_id):
                return
            # clear_entry deletes the entry from JSON; drop our reference
            # so _apply_source correctly calls add_entry next time.
            self._entry_id = None
        else:
            # Orphaned stash with no JSON entry at all — restore directly.
            if not self._manager.restore_orphaned_stash(self._target_path):
                return
        self._set_source_text_silent('')
        self._update_status('not_set')

    def _on_delete(self):
        if self._entry_id:
            if not self._manager.remove_entry(self._entry_id):
                return
        self.delete_requested.emit(self._entry_id or '')

    def _on_preview(self):
        dlg = ModPreviewDialog(
            self._manager,
            self._target_path,
            self._display_name,
            self,
        )
        dlg.exec()

    # ------------------------------------------------------------------
    # External helpers
    # ------------------------------------------------------------------

    def apply_source_external(self, source_type: str, source_value: str):
        """Called externally (e.g. by ‘Apply to All Sky Faces’)."""
        self._apply_source(source_type, source_value)
        display = source_value if source_type in ('local_file', 'asset_id', 'bundled') else ''
        self._set_source_text_silent(display)

    # ------------------------------------------------------------------
    # Inline source editing
    # ------------------------------------------------------------------

    def _get_source_display_text(self) -> str:
        """Return the textbox display string for the current entry’s source."""
        for entry in self._manager.entries:
            if entry.get('target_path') == self._target_path:
                src_type = entry.get('source_type')
                src_val = entry.get('source_value') or ''
                if src_type == 'bundled':
                    # Reverse-map any remove-class bundled value back to 'remove'.
                    if src_val == self._resolve_bundled_empty() or src_val == 'bundled:zero':
                        return 'remove'
                    return src_val
                if src_type in ('local_file', 'asset_id', 'cdn_url'):
                    return src_val
                return ''
        return ''

    def _set_source_text_silent(self, text: str) -> None:
        """Set textbox text without triggering the apply debounce."""
        self._debounce.stop()
        self._source_edit.blockSignals(True)
        self._source_edit.setText(text)
        self._source_edit.blockSignals(False)
        self._clear_source_error()

    def _show_source_error(self, tooltip: str = '') -> None:
        self._source_edit.setStyleSheet(
            'QLineEdit { border: 1px solid #d44; background-color: #3a1010; }'
        )
        self._source_edit.setToolTip(tooltip)

    def _clear_source_error(self) -> None:
        self._source_edit.setStyleSheet('')
        self._source_edit.setToolTip('')

    # Map target-file extensions to their bundled empty counterpart.
    _BUNDLED_EMPTY_BY_EXT: dict[str, str] = {
        '.mp3': 'bundled:empty.mp3',
        '.ogg': 'bundled:empty.ogg',
        '.wav': 'bundled:empty.mp3',
        '.mesh': 'bundled:empty.mesh',
        '.tex': 'bundled:empty.tex',
    }

    def _resolve_bundled_empty(self) -> str:
        """Return the fully-qualified bundled value for the 'bundled:empty' shorthand.

        Uses the target file's extension to pick the right silent asset.
        Falls back to 'bundled:zero' (zero-byte file) for unknown extensions.
        """
        ext = Path(self._target_path).suffix.lower()
        return self._BUNDLED_EMPTY_BY_EXT.get(ext, 'bundled:zero')

    def _detect_source_from_text(self, text: str) -> tuple[str, str]:
        """Detect source type and value from a textbox string."""
        text = text.strip().strip('"\'')
        # 'remove' (with or without surrounding quotes) replaces with the empty asset
        if text.lower() == 'remove':
            return 'bundled', self._resolve_bundled_empty()
        if text.isdigit():
            return 'asset_id', text
        if text.lower().startswith('rbxassetid://'):
            return 'asset_id', text[len('rbxassetid://') :]
        # 'bundled:empty' shorthand → resolve based on target extension
        if text.lower() == 'bundled:empty':
            return 'bundled', self._resolve_bundled_empty()
        if text.lower().startswith('bundled:'):
            return 'bundled', text
        if text.lower().startswith(('http://', 'https://')):
            return 'cdn_url', text
        return 'local_file', text

    def _apply_from_text(self) -> None:
        """Apply (or clear) the modification from the current textbox value."""
        self._debounce.stop()
        text = self._source_edit.text().strip().strip('"\'')

        if not text:
            self._clear_source_error()
            # Empty box = user wants to clear the modification.
            self._on_reset()
            return

        src_type, src_value = self._detect_source_from_text(text)

        if src_type == 'local_file' and not Path(src_value).is_file():
            # Show red border but still apply — the manager will fail and
            # the status indicator will show 'error', matching asset-ID behaviour.
            self._show_source_error(f'File not found: {src_value}')
        else:
            self._clear_source_error()

        self._apply_source(src_type, src_value)

    def _on_editing_finished(self) -> None:
        """Apply immediately on Return / focus-loss.

        Skip when focus moved to Browse or Mute — those buttons call
        _apply_from_text themselves after setting the text.
        """
        if self._browse_btn.hasFocus():
            return
        self._apply_from_text()

    def _on_browse(self) -> None:
        current_val = self._source_edit.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            p = Path(current_val)
            if p.parent.exists():
                initial_dir = str(p)
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select replacement file',
            initial_dir,
            self._file_filter,
        )
        if path:
            self._set_source_text_silent(path)
            self._apply_from_text()

    def _on_mute(self) -> None:
        """Apply the bundled silent file for this sound row."""
        if not self._mute_bundled:
            return
        self._set_source_text_silent(self._mute_bundled)
        self._apply_source('bundled', self._mute_bundled)


# ═══════════════════════════════════════════════════════════════════
# ModPreviewDialog
# ═══════════════════════════════════════════════════════════════════


class ModPreviewDialog(QDialog):
    """Preview dialog showing Modification vs Original side-by-side tabs."""

    def __init__(
        self,
        manager: ModificationManager,
        target_path: str,
        display_name: str,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = manager
        self._target_path = target_path
        self._mod_converted_bytes: bytes | None = None
        self._mod_converted_ext: str = ''
        self._orig_unavailable: bool = False
        self.setWindowTitle(f'Preview \u2014 {display_name}')
        self.resize(500, 400)

        layout = QVBoxLayout()
        tabs = QTabWidget()

        # Modification tab — build first so _mod_converted_bytes is populated
        mod_widget = self._build_preview_widget('mod')
        tabs.addTab(mod_widget, 'Modification')

        # Original tab
        orig_widget = self._build_preview_widget('original')
        tabs.addTab(orig_widget, 'Original')

        layout.addWidget(tabs)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_conv_btn = QPushButton('Export Converted\u2026')
        export_conv_btn.setEnabled(self._mod_converted_bytes is not None)
        export_conv_btn.clicked.connect(self._on_export_converted)
        btn_row.addWidget(export_conv_btn)
        export_btn = QPushButton('Export Original\u2026')
        export_btn.setEnabled(not self._orig_unavailable)
        export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _build_preview_widget(self, mode: str) -> QWidget:
        """Build a widget that previews the file based on its type."""
        container = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)

        data = self._load_data(mode)
        if data is None:
            lower_check = self._target_path.lower()
            if mode == 'original' and lower_check.endswith(('.ttf', '.otf', '.ttc')):
                lbl = QLabel(
                    'Preview of Roblox Original fonts are not supported because it includes multiple Font files'
                )
                lbl.setWordWrap(True)
                layout.addWidget(lbl)
                self._orig_unavailable = True
            else:
                layout.addWidget(QLabel('No data available'))
            container.setLayout(layout)
            return container

        lower = self._target_path.lower()

        # Image / Texture (including .dds)
        if lower.endswith(('.tex', '.dds', '.ktx', '.ktx2', '.png', '.jpg', '.jpeg')):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            display_bytes = data
            ktx_payload = strip_prefixed_ktx(data)
            if ktx_payload is not None:
                converted = ktx_to_png(ktx_payload)
                if converted:
                    display_bytes = converted
                    if mode == 'mod':
                        self._mod_converted_bytes = converted
                        self._mod_converted_ext = '.png'
                else:
                    layout.addWidget(QLabel('Could not decode KTX texture file'))
                    container.setLayout(layout)
                    return container
            elif lower.endswith(('.tex', '.dds')):
                # The replacement may be a plain image (PNG/JPEG) even though
                # the target path ends in .tex/.dds — detect by magic bytes first.
                _is_raw_image = (
                    data[:8] == b'\x89PNG\r\n\x1a\n'  # PNG
                    or data[:2] == b'\xff\xd8'  # JPEG
                    or data[:6] in (b'GIF87a', b'GIF89a')
                )
                if not _is_raw_image:
                    from ..modifications.dds_to_png import tex_to_png_bytes

                    converted = tex_to_png_bytes(data)
                    if converted:
                        display_bytes = converted
                        if mode == 'mod':
                            self._mod_converted_bytes = converted
                            self._mod_converted_ext = '.png'
                    else:
                        layout.addWidget(QLabel('Could not decode .tex/.dds file'))
                        container.setLayout(layout)
                        return container

            from PyQt6.QtGui import QPixmap

            pixmap = QPixmap()
            pixmap.loadFromData(display_bytes)
            if not pixmap.isNull():
                scaled = pixmap.scaled(
                    460,
                    350,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                label.setPixmap(scaled)
            else:
                label.setText('Could not render image')
            layout.addWidget(label)

        # Mesh
        elif lower.endswith('.mesh'):
            try:
                from ..cache import mesh_processing

                obj_text = mesh_processing.convert(data)
                if obj_text:
                    if mode == 'mod':
                        self._mod_converted_bytes = obj_text.encode()
                        self._mod_converted_ext = '.obj'
                    from ..cache.obj_viewer import ObjViewerPanel

                    viewer = ObjViewerPanel()
                    viewer.load_obj(obj_text)
                    layout.addWidget(viewer)
                else:
                    layout.addWidget(QLabel('Could not convert mesh for preview'))
            except Exception as exc:
                layout.addWidget(QLabel(f'Mesh preview error: {exc}'))

        # Audio
        elif lower.endswith(('.mp3', '.ogg', '.wav')):
            try:
                # Write to temp file for AudioPlayerWidget
                import tempfile

                suffix = Path(self._target_path).suffix
                tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
                tmp.write(data)
                tmp.close()
                from ..cache.audio_player import AudioPlayerWidget

                player = AudioPlayerWidget(tmp.name)
                layout.addWidget(player)
            except Exception as exc:
                layout.addWidget(QLabel(f'Audio preview error: {exc}'))

        # Fonts
        elif lower.endswith(('.ttf', '.otf', '.ttc')):
            try:
                # Check if it's actually JSON (FontFamily) instead of a font file
                try:
                    import json as json_module

                    decoded = data.decode('utf-8', errors='replace')
                    json_module.loads(decoded)
                    # It's valid JSON, show as JSON instead
                    from PyQt6.QtWidgets import QTextEdit

                    viewer = QTextEdit()
                    viewer.setReadOnly(True)
                    # Pretty print the JSON
                    import json as json_module

                    parsed = json_module.loads(decoded)
                    pretty_json = json_module.dumps(parsed, indent=2)
                    viewer.setPlainText(pretty_json)
                    layout.addWidget(viewer)
                except ValueError, UnicodeDecodeError:
                    # Not JSON, treat as font file
                    from ..cache.font_viewer import FontViewerWidget

                    font_viewer = FontViewerWidget(data)
                    layout.addWidget(font_viewer)
            except Exception as exc:
                layout.addWidget(QLabel(f'Font/JSON preview error: {exc}'))

        else:
            layout.addWidget(QLabel(f'No preview available for this file type'))

        container.setLayout(layout)
        return container

    def _load_data(self, mode: str) -> bytes | None:
        """Load file bytes for preview. mode='mod' or 'original'."""
        from ..modifications.manager import MOD_ORIGINALS_DIR, normalise_target_path

        if not self._manager.roblox_dirs:
            return None
        roblox_dir = self._manager.roblox_dirs[0]
        try:
            target_path = normalise_target_path(self._target_path)
        except ValueError:
            return None

        if mode == 'original':
            # Try stash first
            stash = MOD_ORIGINALS_DIR / roblox_dir.name / target_path
            if stash.is_file():
                return stash.read_bytes()
            original = read_current_platform_original_asset(self._target_path)
            if original is not None:
                return original
            mod_active = any(
                e.get('target_path') == self._target_path for e in self._manager.entries
            )
            if mod_active:
                return None
            dst = roblox_dir / target_path
            return dst.read_bytes() if dst.is_file() else None

        # mode == 'mod' — read the current (modified) file from Roblox dir
        dst = roblox_dir / target_path
        if dst.is_file():
            return dst.read_bytes()
        return read_current_platform_original_asset(self._target_path)

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Export Original File',
            Path(self._target_path).name,
        )
        if path:
            data = self._load_data('original')
            if data:
                export_path = Path(path)
                export_path.write_bytes(data)
                self._show_export_complete_message(
                    'Export Complete',
                    f'File exported to:\n{export_path}',
                    [export_path],
                )

    def _on_export_converted(self):
        if not self._mod_converted_bytes:
            return
        stem = Path(self._target_path).stem
        default_name = stem + self._mod_converted_ext
        path, _ = QFileDialog.getSaveFileName(
            self,
            'Export Converted File',
            default_name,
        )
        if path:
            export_path = Path(path)
            export_path.write_bytes(self._mod_converted_bytes)
            self._show_export_complete_message(
                'Export Complete',
                f'File exported to:\n{export_path}',
                [export_path],
            )

    def _show_export_complete_message(self, title: str, message: str, exported_paths):
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(title)
        msg.setText(message)
        open_button = msg.addButton('Open in Explorer', QMessageBox.ButtonRole.ActionRole)
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()

        if msg.clickedButton() == open_button:
            try:
                import subprocess

                paths = [Path(path) for path in exported_paths if path]
                if len(paths) == 1 and paths[0].is_file():
                    subprocess.Popen(
                        ['explorer.exe', '/select,', str(paths[0].resolve())],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                    )
                elif paths:
                    target = paths[0] if paths[0].is_dir() else paths[0].parent
                    open_folder(target)
            except Exception as exc:
                log_buffer.log('Export', f'Could not open exported file location: {exc}')


# ═══════════════════════════════════════════════════════════════════
# Fast Flags section widgets
# ═══════════════════════════════════════════════════════════════════


class CustomFFlagWarningDialog(QDialog):
    """One-time, intentionally slow confirmation for bannable custom flags."""

    CONFIRM_DELAY_SECONDS = 15

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Are you sure?')
        self.setModal(True)
        self.setMinimumWidth(520)
        self._seconds_remaining = self.CONFIRM_DELAY_SECONDS

        layout = QVBoxLayout(self)
        title = QLabel('<b>Custom FastFlags can get your Roblox account banned.</b>')
        title.setStyleSheet('color: #d9534f; font-size: 15px;')
        layout.addWidget(title)

        message = QLabel(
            'Roblox only permits a small allowlist of local FastFlags. This feature bypasses '
            'that restriction by modifying Roblox\'s remote ClientSettings response. Fleasion '
            'cannot determine whether a flag is safe, and the Fleasion contributors accept no '
            'liability for account moderation, data loss, crashes, or other consequences.\n\n'
            'Only continue if you understand the risk and accept full responsibility.'
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._confirm_button = self._buttons.addButton(
            '', QDialogButtonBox.ButtonRole.AcceptRole
        )
        self._confirm_button.setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_confirm_text()
        self._timer.start()

    def _update_confirm_text(self):
        if self._seconds_remaining > 0:
            self._confirm_button.setText(f'I accept the risk ({self._seconds_remaining}s)')
        else:
            self._confirm_button.setText('I accept the risk — enable custom FastFlags')

    def _tick(self):
        self._seconds_remaining = max(0, self._seconds_remaining - 1)
        self._update_confirm_text()
        if self._seconds_remaining == 0:
            self._timer.stop()
            self._confirm_button.setEnabled(True)


class FastFlagProfilesDialog(QDialog):
    """Manage named, on-disk FastFlag profiles without hiding the current editor."""

    def __init__(self, flags: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle('Custom FastFlag Profiles')
        self.setMinimumWidth(560)
        self._flags = dict(flags)
        self._profiles = FastFlagProfileManager()
        self.loaded_flags: dict[str, str] | None = None
        self._setup_ui()
        self._refresh_profiles()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        description = QLabel(
            'Save the current custom FastFlags as a reusable JSON profile. Loading can replace '
            'the editor or merge the profile into it.'
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        save_row = QHBoxLayout()
        self._name = QLineEdit()
        self._name.setPlaceholderText('Profile name')
        save_row.addWidget(self._name)
        save_button = QPushButton('Save Current')
        save_button.clicked.connect(self._save_profile)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)

        self._profile_list = QListWidget()
        self._profile_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._profile_list.itemDoubleClicked.connect(lambda _item: self._load_profile())
        layout.addWidget(self._profile_list)

        self._replace_flags = QCheckBox('Replace current flags when loading')
        self._replace_flags.setChecked(True)
        self._replace_flags.setToolTip('Turn this off to merge the profile into the current flags.')
        layout.addWidget(self._replace_flags)

        actions = QHBoxLayout()
        self._load_button = QPushButton('Load')
        self._load_button.clicked.connect(self._load_profile)
        actions.addWidget(self._load_button)
        self._update_button = QPushButton('Update from Current')
        self._update_button.clicked.connect(self._update_profile)
        actions.addWidget(self._update_button)
        self._copy_button = QPushButton('Copy JSON')
        self._copy_button.clicked.connect(self._copy_profile)
        actions.addWidget(self._copy_button)
        self._rename_button = QPushButton('Rename…')
        self._rename_button.clicked.connect(self._rename_profile)
        actions.addWidget(self._rename_button)
        self._delete_button = QPushButton('Delete')
        self._delete_button.clicked.connect(self._delete_profile)
        actions.addWidget(self._delete_button)
        layout.addLayout(actions)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._set_actions_enabled(False)

    def _selected_name(self) -> str | None:
        item = self._profile_list.currentItem()
        return item.text() if item is not None else None

    def _set_actions_enabled(self, enabled: bool):
        for button in (
            self._load_button,
            self._update_button,
            self._copy_button,
            self._rename_button,
            self._delete_button,
        ):
            button.setEnabled(enabled)

    def _on_selection_changed(self):
        name = self._selected_name()
        self._set_actions_enabled(name is not None)
        if name:
            self._name.setText(name)

    def _refresh_profiles(self, selected: str | None = None):
        selected = selected or self._selected_name()
        self._profile_list.clear()
        for name in self._profiles.list_profiles():
            self._profile_list.addItem(name)
        if selected:
            matches = self._profile_list.findItems(selected, Qt.MatchFlag.MatchExactly)
            if matches:
                self._profile_list.setCurrentItem(matches[0])
        self._set_actions_enabled(self._selected_name() is not None)

    def _show_error(self, action: str, exc: Exception):
        QMessageBox.warning(self, f'Could Not {action} Profile', str(exc))

    def _save_profile(self):
        try:
            name = self._profiles.save(self._name.text(), self._flags)
        except (OSError, ValueError) as exc:
            self._show_error('Save', exc)
            return
        self._refresh_profiles(name)

    def _load_profile(self):
        name = self._selected_name()
        if not name:
            return
        try:
            flags = self._profiles.load(name)
        except (OSError, ValueError) as exc:
            self._show_error('Load', exc)
            return
        self.loaded_flags = flags if self._replace_flags.isChecked() else {**self._flags, **flags}
        self.accept()

    def _update_profile(self):
        name = self._selected_name()
        if not name:
            return
        try:
            self._profiles.save(name, self._flags)
        except (OSError, ValueError) as exc:
            self._show_error('Update', exc)

    def _copy_profile(self):
        name = self._selected_name()
        if not name:
            return
        try:
            QApplication.clipboard().setText(json.dumps(self._profiles.load(name), indent=2))
        except (OSError, ValueError) as exc:
            self._show_error('Copy', exc)

    def _rename_profile(self):
        old_name = self._selected_name()
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(self, 'Rename FastFlag Profile', 'New name:', text=old_name)
        if not ok:
            return
        try:
            name = self._profiles.rename(old_name, new_name)
        except (OSError, ValueError) as exc:
            self._show_error('Rename', exc)
            return
        self._refresh_profiles(name)

    def _delete_profile(self):
        name = self._selected_name()
        if not name:
            return
        if QMessageBox.question(self, 'Delete FastFlag Profile', f'Delete “{name}”?') != QMessageBox.StandardButton.Yes:
            return
        try:
            self._profiles.delete(name)
        except (OSError, ValueError) as exc:
            self._show_error('Delete', exc)
            return
        self._refresh_profiles()


class FFlagBrowserDialog(QDialog):
    """Browse live Roblox FastFlags and client FastVariables known to the tracker."""

    _SETTINGS_URL = 'https://clientsettingscdn.roblox.com/v2/settings/application/PCDesktopClient'
    _SETTINGS_APPLICATIONS = (
        'PCDesktopClient',
        'MacDesktopClient',
        'PlayStationClient',
        'XboxClient',
        'iOSApp',
        'UWPApp',
        'AndroidApp',
        'PCStudioApp',
        'MacStudioApp',
        'PCStudioBootstrapper',
        'MacStudioBootstrapper',
        'PCClientBootstrapper',
        'MacClientBootstrapper',
    )
    _SETTINGS_BUCKETS = ('', '/bucket/zcanary', '/bucket/zintegration')
    _TRACKER_VARIABLES_URL = (
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/'
        'roblox/FVariables.txt'
    )
    _HISTORICAL_TRACKER_VARIABLES_URL = (
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/'
        '03a46e5f35e7aa5d85310189b477caee20b20761/FVariables.txt'
    )
    _BYPASS_CUSTOM_FFLAGS_HEADER = {'X-Fleasion-Bypass-Custom-FFlags': '1'}
    _UNPUBLISHED_VALUE = 'No value'
    _CACHE_PATH = APP_CACHE_DIR / 'fflag_browser.json'
    _CACHE_TTL_SECONDS = 60 * 60
    _CACHE_VERSION = 1
    _FAMILIES = (
        'DFFlag',
        'DFInt',
        'DFLog',
        'DFString',
        'DFFloat',
        'FFlag',
        'FInt',
        'FLog',
        'FString',
        'FFloat',
    )

    flags_loaded = pyqtSignal(object)
    load_failed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Browse Roblox FastFlags')
        self.setMinimumSize(820, 580)
        # ``None`` means the variable is known to exist in the client, but
        # Roblox has not published a current value through ClientSettings.
        self._flags: dict[str, str | None] = {}
        self._cache_loaded = False
        self.selected_flags: dict[str, str] = {}
        self._setup_ui()
        self.flags_loaded.connect(self._apply_flags)
        self.load_failed.connect(self._show_load_error)
        self._refresh()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        description = QLabel(
            'Browse FastFlags found across Roblox ClientSettings releases and public client '
            'tracker lists. Entries without a current PC value are added blank.'
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText('Search FastFlag names or current values…')
        self._search.textChanged.connect(self._filter_rows)
        controls.addWidget(self._search, 1)

        self._family_filter = QComboBox()
        self._family_filter.setMinimumWidth(165)
        self._family_filter.currentIndexChanged.connect(self._filter_rows)
        controls.addWidget(self._family_filter)

        self._refresh_button = QPushButton('Refresh')
        self._refresh_button.clicked.connect(lambda: self._refresh(force=True))
        controls.addWidget(self._refresh_button)
        layout.addLayout(controls)

        self._count = QLabel('Retrieving FastFlags…')
        self._count.setStyleSheet('color: #999;')
        layout.addWidget(self._count)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(['Name', 'Current Roblox Value'])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.itemSelectionChanged.connect(self._update_selection_button)
        layout.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._add_button = buttons.addButton('Add Selected', QDialogButtonBox.ButtonRole.AcceptRole)
        self._add_button.setEnabled(False)
        self._add_button.clicked.connect(self._add_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @classmethod
    def _family_for(cls, name: str) -> str:
        for family in cls._FAMILIES:
            if name.startswith(family):
                return family
        return 'Other'

    @staticmethod
    def _extract_flags(payload: object) -> dict[str, str]:
        """Validate the public ClientSettings response without accepting arbitrary JSON."""
        if not isinstance(payload, dict):
            raise ValueError('Roblox returned an invalid FastFlag response.')
        settings = payload.get('applicationSettings')
        if not isinstance(settings, dict):
            raise ValueError('Roblox returned no application FastFlags.')

        flags: dict[str, str] = {}
        for raw_name, raw_value in settings.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_value, str | int | float | bool):
                continue
            flags[name] = (
                'True' if raw_value is True else 'False' if raw_value is False else str(raw_value)
            )
        if not flags:
            raise ValueError('Roblox returned no usable FastFlags.')
        return flags

    @classmethod
    def _extract_tracker_flags(cls, payload: bytes) -> dict[str, None]:
        """Extract known FastVariable names from the public Client Tracker list."""
        try:
            lines = payload.decode('utf-8').splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError('The FastVariable tracker returned invalid text.') from exc

        flags: dict[str, None] = {}
        for line in lines:
            # The tracker currently writes entries as ``[C++] FFlagExample``.
            _, marker, raw_name = line.partition('] ')
            if not marker:
                continue
            name = raw_name.strip()
            if name.startswith(cls._FAMILIES):
                flags[name] = None
        if not flags:
            raise ValueError('The FastVariable tracker returned no usable FastFlags.')
        return flags

    @classmethod
    def _settings_urls(cls) -> tuple[str, ...]:
        """Return the live, canary, and integration endpoints used by the tracker."""
        base_url = 'https://clientsettingscdn.roblox.com/v2/settings/application/'
        urls = [cls._SETTINGS_URL]
        urls.extend(
            f'{base_url}{application}{bucket}'
            for bucket in cls._SETTINGS_BUCKETS
            for application in cls._SETTINGS_APPLICATIONS
            if f'{base_url}{application}{bucket}' != cls._SETTINGS_URL
        )
        return tuple(urls)

    @classmethod
    def _read_cache(cls, *, now: float | None = None) -> dict[str, str | None] | None:
        """Return the recent merged result, ignoring malformed or expired cache files."""
        try:
            cached = json.loads(cls._CACHE_PATH.read_text(encoding='utf-8'))
            if cached.get('version') != cls._CACHE_VERSION:
                return None
            fetched_at = float(cached['fetched_at'])
            age = (time.time() if now is None else now) - fetched_at
            raw_flags = cached['flags']
            if not 0 <= age < cls._CACHE_TTL_SECONDS or not isinstance(raw_flags, dict):
                return None
        except (OSError, ValueError, TypeError, AttributeError, KeyError, json.JSONDecodeError):
            return None

        flags: dict[str, str | None] = {}
        for raw_name, value in raw_flags.items():
            name = raw_name.strip() if isinstance(raw_name, str) else ''
            if name.startswith(cls._FAMILIES) and (value is None or isinstance(value, str)):
                flags[name] = value
        return flags or None

    @classmethod
    def _write_cache(cls, flags: dict[str, str | None], *, now: float | None = None):
        """Atomically persist a successfully resolved union for the next hour."""
        temporary_path = cls._CACHE_PATH.with_name(f'.{cls._CACHE_PATH.name}.tmp')
        try:
            cls._CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(
                    {
                        'version': cls._CACHE_VERSION,
                        'fetched_at': time.time() if now is None else now,
                        'flags': flags,
                    },
                    separators=(',', ':'),
                ),
                encoding='utf-8',
            )
            temporary_path.replace(cls._CACHE_PATH)
        except OSError:
            # The browser remains useful if its optional cache cannot be written.
            pass
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _refresh(self, force: bool = False):
        if self._refresh_button.isEnabled() is False:
            return
        if not force:
            cached_flags = self._read_cache()
            if cached_flags is not None:
                self._cache_loaded = True
                self.flags_loaded.emit(cached_flags)
                return

        self._cache_loaded = False
        self._refresh_button.setEnabled(False)
        self._count.setText('Retrieving FastFlags…')
        self._count.setStyleSheet('color: #999;')
        threading.Thread(target=self._fetch_flags, daemon=True).start()

    def _fetch_flags(self):
        try:
            # Tell Fleasion's ClientSettings interceptor to pass this browser
            # request through. Active custom flags must not look like values
            # published by Roblox.
            flags: dict[str, str | None] = {}
            settings_urls = self._settings_urls()
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {
                    executor.submit(
                        http_get,
                        url,
                        20,
                        self._BYPASS_CUSTOM_FFLAGS_HEADER,
                    ): url
                    for url in settings_urls
                }
                for future in as_completed(futures):
                    url = futures[future]
                    try:
                        settings = self._extract_flags(json.loads(future.result()))
                    except (OSError, ValueError, json.JSONDecodeError):
                        # Some platform/channel combinations are intentionally
                        # unavailable. The remaining published endpoints are
                        # still useful to the browser.
                        continue
                    if url == self._SETTINGS_URL:
                        flags.update(settings)
                    else:
                        flags.update({name: None for name in settings if name not in flags})

            for tracker_url in (
                self._TRACKER_VARIABLES_URL,
                self._HISTORICAL_TRACKER_VARIABLES_URL,
            ):
                try:
                    tracker_flags = self._extract_tracker_flags(http_get(tracker_url, timeout=20))
                except (OSError, ValueError):
                    # A tracker snapshot should add names when available, but a
                    # temporary GitHub failure must not hide published values.
                    continue
                flags.update({name: value for name, value in tracker_flags.items() if name not in flags})

            if not flags:
                raise ValueError('No configured FastFlag source returned usable data.')
            self._write_cache(flags)
            self.flags_loaded.emit(flags)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.load_failed.emit(f'Could not retrieve Roblox FastFlags: {exc}')

    def _apply_flags(self, flags: dict[str, str | None]):
        self._flags = dict(sorted(flags.items(), key=lambda item: item[0].casefold()))
        current_family = self._family_filter.currentData()
        family_counts: dict[str, int] = {}
        for name in self._flags:
            family = self._family_for(name)
            family_counts[family] = family_counts.get(family, 0) + 1

        self._family_filter.blockSignals(True)
        self._family_filter.clear()
        self._family_filter.addItem('All FastFlags', '')
        for family in sorted(family_counts):
            self._family_filter.addItem(f'{family} ({family_counts[family]:,})', family)
        previous_index = self._family_filter.findData(current_family)
        self._family_filter.setCurrentIndex(max(0, previous_index))
        self._family_filter.blockSignals(False)
        self._refresh_button.setEnabled(True)
        self._populate_table()
        self._filter_rows()

    def _show_load_error(self, message: str):
        self._refresh_button.setEnabled(True)
        self._count.setText(message)
        self._count.setStyleSheet('color: #ef8f8f;')

    def _filter_rows(self, *_args):
        query = self._search.text().strip().casefold()
        family = self._family_filter.currentData() or ''
        visible_count = 0
        self._table.setUpdatesEnabled(False)
        try:
            for row, (name, value) in enumerate(self._flags.items()):
                display_value = value if value is not None else self._UNPUBLISHED_VALUE
                matches = (not family or self._family_for(name) == family) and (
                    not query or query in name.casefold() or query in display_value.casefold()
                )
                self._table.setRowHidden(row, not matches)
                visible_count += matches
        finally:
            self._table.setUpdatesEnabled(True)

        if self._flags:
            no_pc_value_count = sum(value is None for value in self._flags.values())
            source_detail = (
                f' • {no_pc_value_count:,} without a current PC value'
                if no_pc_value_count
                else ' retrieved from Roblox'
            )
            cache_detail = ' • cached' if self._cache_loaded else ''
            self._count.setText(
                f'Showing {visible_count:,} FastFlags • {len(self._flags):,}'
                f'{source_detail}{cache_detail}'
            )
            self._count.setStyleSheet('color: #999;')
        self._update_selection_button()

    def _populate_table(self):
        """Populate once per download; search and filters only hide existing rows."""
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(self._flags))
            for row, (name, value) in enumerate(self._flags.items()):
                name_item = QTableWidgetItem(name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                name_item.setToolTip(name)
                self._table.setItem(row, 0, name_item)
                value_item = QTableWidgetItem(
                    value if value is not None else self._UNPUBLISHED_VALUE
                )
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, 1, value_item)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)

    def _selected_names(self) -> list[str]:
        return [
            self._table.item(row, 0).text()
            for row in sorted({index.row() for index in self._table.selectedIndexes()})
            if self._table.item(row, 0) is not None
        ]

    def _update_selection_button(self):
        selected_count = len(self._selected_names())
        self._add_button.setText(
            f'Add Selected ({selected_count})' if selected_count else 'Add Selected'
        )
        self._add_button.setEnabled(selected_count > 0)

    def _add_selected(self):
        self.selected_flags = {
            name: value if value is not None else ''
            for name in self._selected_names()
            for value in (self._flags.get(name),)
            if name in self._flags
        }
        if self.selected_flags:
            self.accept()


class WindowsHotkeyCaptureDialog(QDialog):
    """Capture one physical Windows key, including a bare modifier key."""

    _MODIFIER_KEYS = {
        int(Qt.Key.Key_Control),
        int(Qt.Key.Key_Shift),
        int(Qt.Key.Key_Alt),
        int(Qt.Key.Key_Meta),
        int(Qt.Key.Key_AltGr),
    }

    def __init__(self, flag_name: str, parent=None):
        super().__init__(parent)
        self.binding: dict[str, int | bool] | None = None
        self.clear_requested = False
        self._pending_modifier: dict[str, int | bool] | None = None
        self._pending_modifier_key: int | None = None
        self.setWindowTitle('Set FastFlag Keybind')
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        label = QLabel(
            f'Press the global keybind for <b>{flag_name}</b>.<br>'
            'Single keys, modifier keys, and key combinations are supported. '
            'Pressing Escape binds Escape; use Cancel to leave without changing it.'
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self._preview = QLabel('Waiting for a key combination…')
        self._preview.setStyleSheet('color: #999; padding: 10px 0;')
        layout.addWidget(self._preview)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        clear_button = buttons.addButton('Clear Keybind', QDialogButtonBox.ButtonRole.DestructiveRole)
        clear_button.clicked.connect(self._clear)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _enum_value(value) -> int:
        """PyQt6 flag enums expose `.value`; they are not reliably int-convertible."""
        return int(getattr(value, 'value', value))

    @classmethod
    def _modifier_mask(cls, modifiers) -> int:
        from .windows_hotkeys import MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN

        qt_modifiers = cls._enum_value(modifiers)
        result = 0
        if qt_modifiers & 0x02000000:
            result |= MOD_SHIFT
        if qt_modifiers & 0x04000000:
            result |= MOD_CTRL
        if qt_modifiers & 0x08000000:
            result |= MOD_ALT
        if qt_modifiers & 0x10000000:
            result |= MOD_WIN
        return result

    @staticmethod
    def _event_binding(event, modifiers: int) -> dict[str, int | bool] | None:
        from .windows_hotkeys import modifier_mask_for_virtual_key

        scan_code = int(event.nativeScanCode())
        virtual_key = int(event.nativeVirtualKey())
        if not 0 < scan_code <= 0xFF:
            return None
        extended = virtual_key in {
            0xA3, 0xA5, 0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22,
            0x25, 0x26, 0x27, 0x28, 0x5B, 0x5C,
        }
        return {
            'scan_code': scan_code,
            'extended': extended,
            'modifiers': modifiers & ~modifier_mask_for_virtual_key(virtual_key),
        }

    def _clear(self):
        self.clear_requested = True
        self.accept()

    @staticmethod
    def _modifier_preview(modifiers: int) -> str:
        from .windows_hotkeys import MOD_ALT, MOD_CTRL, MOD_SHIFT, MOD_WIN

        labels = [
            label
            for flag, label in (
                (MOD_WIN, 'Win'),
                (MOD_CTRL, 'Ctrl'),
                (MOD_ALT, 'Alt'),
                (MOD_SHIFT, 'Shift'),
            )
            if modifiers & flag
        ]
        return '+'.join([*labels, '…']) if labels else 'Waiting for a key combination…'

    def keyPressEvent(self, event):
        key = int(event.key())
        if event.isAutoRepeat():
            return
        modifiers = self._modifier_mask(event.modifiers())
        binding = self._event_binding(event, modifiers)
        if binding is None:
            self._preview.setText('That key could not be read as a Windows scan code.')
            return
        if key in self._MODIFIER_KEYS:
            from .windows_hotkeys import modifier_mask_for_virtual_key

            self._pending_modifier = binding
            self._pending_modifier_key = key
            self._preview.setText(
                self._modifier_preview(
                    modifiers | modifier_mask_for_virtual_key(int(event.nativeVirtualKey()))
                )
            )
            return
        self.binding = binding
        self.accept()

    def keyReleaseEvent(self, event):
        if (
            event.isAutoRepeat()
            or self._pending_modifier is None
            or int(event.key()) != self._pending_modifier_key
        ):
            return
        self.binding = self._pending_modifier
        self.accept()


class CustomFFlagEditor(QWidget):
    """Fishstrap-style name/value editor backed by Fleasion's proxy settings."""

    _BOOLEAN_FLAG_PREFIXES = ('FFlag', 'DFFlag')

    def __init__(self, config_manager=None, proxy_master=None, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._windows_keybinds = sys.platform == 'win32'
        self._hotkey_service = None
        if self._windows_keybinds:
            from .windows_hotkeys import WindowsHotkeyService

            self._hotkey_service = WindowsHotkeyService(self)
            self._hotkey_service.activated.connect(self._toggle_flag_from_hotkey)
        self._loading = False
        self._sort_column: int | None = 0
        self._sort_ascending = True
        self._setup_ui()
        self._load_flags()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(7)

        heading = QLabel('<b>Custom FastFlags (LIVE EDITING)</b>')
        layout.addWidget(heading)

        warning = QLabel(
            '⚠ Non-Roblox-allowed FastFlags are bannable. Use this feature entirely at your '
            'own risk. Fleasion and its contributors accept no liability.'
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            'color: #ef8f8f; background: rgba(180, 45, 45, 0.16); '
            'border: 1px solid rgba(210, 70, 70, 0.55); padding: 7px;'
        )
        layout.addWidget(warning)

        if sys.platform.startswith('linux'):
            sober_delay_warning = QLabel(
                '⚠ Linux / Sober limitation: Due to Sober security, custom FastFlags are first '
                'applied about 2 minutes after Sober starts. Live Dynamic FastFlag editing becomes active '
                '2 minutes after that (4 minutes total). This delay happens every '
                'time Sober is opened, including after a quick close/reopen.'
            )
            sober_delay_warning.setWordWrap(True)
            sober_delay_warning.setStyleSheet(
                'color: #ffcc66; background: rgba(190, 145, 30, 0.15); '
                'border: 1px solid rgba(220, 175, 55, 0.55); padding: 7px;'
            )
            layout.addWidget(sober_delay_warning)

        self._enable_toggle = QCheckBox('Enable custom FastFlags')
        self._enable_toggle.setChecked(
            bool(self._config and getattr(self._config, 'custom_fflags_enabled', False))
        )
        self._enable_toggle.toggled.connect(self._on_enabled_toggled)
        self._enable_toggle.setEnabled(self._config is not None and self._proxy_master is not None)
        layout.addWidget(self._enable_toggle)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        if self._windows_keybinds:
            hotkey_help = QLabel(
                'Double-click a Keybind cell to assign a global Windows key. Single keys, '
                'modifier-only keys, and combinations all work while Roblox is focused.'
            )
            hotkey_help.setWordWrap(True)
            hotkey_help.setStyleSheet('color: #999;')
            layout.addWidget(hotkey_help)

        self._search = QLineEdit()
        self._search.setPlaceholderText('Search custom FastFlags…')
        self._search.textChanged.connect(self._filter_rows)
        layout.addWidget(self._search)

        column_count = 4 if self._windows_keybinds else 2
        self._table = QTableWidget(0, column_count)
        self._table.setHorizontalHeaderLabels(
            ['Name', 'Value', 'Status', 'Keybind']
            if self._windows_keybinds
            else ['Name', 'Value']
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(0, Qt.SortOrder.AscendingOrder)
        header.sectionClicked.connect(self._sort_rows)
        self._table.setColumnWidth(0, 300)
        self._table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setMinimumHeight(180)
        self._table.setItemDelegateForColumn(1, FastFlagValueDelegate(self._table))
        self._table.cellChanged.connect(self._on_cell_changed)
        if self._windows_keybinds:
            self._table.cellDoubleClicked.connect(self._edit_keybind)
        if self._windows_keybinds:
            self._table.setColumnWidth(2, 85)
            self._table.setColumnWidth(3, 160)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        browse_button = QPushButton('Browse FastFlags…')
        browse_button.setToolTip('Browse and add FastFlags currently published by Roblox.')
        browse_button.clicked.connect(self._browse_fflags)
        buttons.addWidget(browse_button)

        add_button = QPushButton('Add New…')
        add_button.clicked.connect(self._add_flag)
        buttons.addWidget(add_button)

        import_button = QPushButton('Import JSON…')
        import_menu = QMenu(import_button)
        import_menu.addAction('From Text…', self._import_json)
        import_menu.addAction('From File…', self._import_file)
        import_button.setMenu(import_menu)
        buttons.addWidget(import_button)

        export_button = QPushButton('Export JSON…')
        export_menu = QMenu(export_button)
        export_menu.addAction('Copy to Clipboard', self._copy_json)
        export_menu.addAction('Export as File…', self._export_json)
        export_button.setMenu(export_menu)
        buttons.addWidget(export_button)

        profiles_button = QPushButton('Profiles…')
        profiles_button.clicked.connect(self._show_profiles)
        buttons.addWidget(profiles_button)

        delete_button = QPushButton('Delete Selected')
        delete_button.clicked.connect(self._delete_selected)
        buttons.addWidget(delete_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        if self._config is None or self._proxy_master is None:
            self._status.setText('The Fleasion proxy must be available to enable custom FastFlags.')
        else:
            self._update_status()

    def _load_flags(self, sync_hotkeys: bool = True):
        flags = dict(getattr(self._config, 'custom_fflags', {}) or {}) if self._config else {}
        self._replace_table_rows(sorted(flags.items(), key=lambda item: item[0].lower()))
        self._filter_rows(self._search.text())
        self._update_status()
        if sync_hotkeys:
            self._sync_hotkeys()

    def _replace_table_rows(self, rows: list[tuple[str, str]]):
        """Bulk-load rows without constructing a widget for every boolean value."""
        self._loading = True
        self._table.setUpdatesEnabled(False)
        self._table.blockSignals(True)
        try:
            self._table.setRowCount(len(rows))
            for row, (name, value) in enumerate(rows):
                name_item = QTableWidgetItem(name)
                name_item.setToolTip(name)
                self._table.setItem(row, 0, name_item)
                self._set_value_editor(row, name, str(value))
                if self._windows_keybinds:
                    status_item = QTableWidgetItem('Enabled')
                    status_item.setFlags(
                        (status_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        & ~Qt.ItemFlag.ItemIsEditable
                    )
                    status_item.setCheckState(
                        Qt.CheckState.Unchecked
                        if name in self._disabled_flag_names()
                        else Qt.CheckState.Checked
                    )
                    self._table.setItem(row, 2, status_item)
                    keybind_item = QTableWidgetItem(self._keybind_text(self._keybinds().get(name)))
                    keybind_item.setFlags(keybind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    keybind_item.setToolTip('Double-click to assign or clear this global Windows keybind.')
                    self._table.setItem(row, 3, keybind_item)
        finally:
            self._table.blockSignals(False)
            self._table.setUpdatesEnabled(True)
            self._loading = False

    def _flags_from_table(self) -> dict[str, str]:
        flags: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            name = name_item.text().strip() if name_item else ''
            if name:
                flags[name] = self._value_from_row(row)
        return flags

    @classmethod
    def _is_boolean_flag(cls, name: str) -> bool:
        return name.startswith(cls._BOOLEAN_FLAG_PREFIXES)

    def _value_from_row(self, row: int) -> str:
        value_item = self._table.item(row, 1)
        return value_item.text() if value_item else ''

    def _set_value_editor(self, row: int, name: str, value: str):
        """Store a lightweight value item; the boolean selector is created only when editing."""
        was_loading = self._loading
        self._loading = True
        try:
            value_item = self._table.item(row, 1)
            normalized_value = (
                'True' if str(value).strip().lower() == 'true' else 'False'
                if self._is_boolean_flag(name)
                else str(value)
            )
            if value_item is None:
                self._table.setItem(row, 1, QTableWidgetItem(normalized_value))
            else:
                value_item.setText(normalized_value)
        finally:
            self._loading = was_loading

    def _disabled_flag_names(self) -> set[str]:
        return set(getattr(self._config, 'custom_fflag_disabled', []) or [])

    def _keybinds(self) -> dict[str, dict[str, int | bool]]:
        bindings = getattr(self._config, 'custom_fflag_keybinds', {}) or {}
        return bindings if isinstance(bindings, dict) else {}

    @staticmethod
    def _keybind_text(binding) -> str:
        from .windows_hotkeys import binding_text

        return binding_text(binding)

    def _flag_is_enabled(self, row: int) -> bool:
        item = self._table.item(row, 2)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _save_hotkey_settings(self):
        if not self._windows_keybinds or self._config is None or self._loading:
            return
        names = set(self._flags_from_table())
        disabled = {
            self._table.item(row, 0).text()
            for row in range(self._table.rowCount())
            if self._table.item(row, 0) is not None and not self._flag_is_enabled(row)
        }
        bindings = {name: spec for name, spec in self._keybinds().items() if name in names}
        self._config.custom_fflag_disabled = sorted(disabled)
        self._config.custom_fflag_keybinds = bindings
        self._sync_hotkeys()
        self._refresh_proxy_hosts()
        self._update_status()

    def _prune_hotkey_settings(self, names: set[str]):
        if not self._windows_keybinds or self._config is None:
            return
        disabled = self._disabled_flag_names() & names
        bindings = {name: spec for name, spec in self._keybinds().items() if name in names}
        self._config.custom_fflag_disabled = sorted(disabled)
        self._config.custom_fflag_keybinds = bindings
        self._sync_hotkeys()

    def _sync_hotkeys(self):
        if self._hotkey_service is not None:
            feature_enabled = bool(
                self._config and getattr(self._config, 'custom_fflags_enabled', False)
            )
            self._hotkey_service.set_bindings(self._keybinds() if feature_enabled else {})

    def _edit_keybind(self, row: int, column: int):
        if column != 3:
            return
        name_item = self._table.item(row, 0)
        name = name_item.text() if name_item else ''
        if not name:
            return
        dialog = WindowsHotkeyCaptureDialog(name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        bindings = self._keybinds()
        if dialog.clear_requested:
            bindings.pop(name, None)
            self._config.custom_fflag_keybinds = bindings
            self._load_flags()
            return
        if dialog.binding is None:
            return
        bindings[name] = dialog.binding
        self._config.custom_fflag_keybinds = bindings
        self._load_flags()

    def _toggle_flag_from_hotkey(self, name: str):
        if (
            self._config is None
            or not getattr(self._config, 'custom_fflags_enabled', False)
            or name not in self._flags_from_table()
        ):
            return
        disabled = self._disabled_flag_names()
        is_enabled = name in disabled
        if is_enabled:
            disabled.remove(name)
        else:
            disabled.add(name)
        self._config.custom_fflag_disabled = sorted(disabled)
        self._refresh_proxy_hosts()
        log_buffer.log(
            'CustomFFlags', f'Windows keybind turned {name} {"on" if is_enabled else "off"}',
        )
        self._load_flags(sync_hotkeys=False)

    def closeEvent(self, event):
        if self._hotkey_service is not None:
            self._hotkey_service.stop()
        super().closeEvent(event)

    def _on_cell_changed(self, row: int, column: int):
        if self._loading:
            return
        if self._windows_keybinds and column == 2:
            self._save_hotkey_settings()
            return
        if column == 0:
            name_item = self._table.item(row, 0)
            if name_item is not None:
                name_item.setToolTip(name_item.text())
            self._set_value_editor(
                row,
                name_item.text() if name_item else '',
                self._value_from_row(row),
            )
        self._save_table()

    def _save_table(self, *_args):
        if self._loading or self._config is None:
            return
        self._config.custom_fflags = self._flags_from_table()
        self._prune_hotkey_settings(set(self._config.custom_fflags))
        self._refresh_proxy_hosts()
        self._update_status()
        self._filter_rows(self._search.text())

    def _update_status(self):
        if not hasattr(self, '_status'):
            return
        count = len(self._flags_from_table()) if hasattr(self, '_table') else 0
        active_count = (
            sum(self._flag_is_enabled(row) for row in range(self._table.rowCount()))
            if self._windows_keybinds and hasattr(self, '_table')
            else count
        )
        enabled = bool(self._config and getattr(self._config, 'custom_fflags_enabled', False))
        if enabled:
            self._status.setText(
                f'Active — {active_count} of {count} saved custom FastFlag(s) will override '
                'Roblox ClientSettings.'
            )
            self._status.setStyleSheet('color: #67c587;')
        else:
            self._status.setText(
                f'Inactive — {count} custom FastFlag(s) saved. Re-enable to restore all overrides.'
            )
            self._status.setStyleSheet('color: #999;')

    def _refresh_proxy_hosts(self):
        if self._proxy_master is None:
            return
        try:
            self._proxy_master.refresh_custom_fflag_interception()
        except Exception as exc:
            log_buffer.log('CustomFFlags', f'Could not refresh proxy interception: {exc}')

    def _on_enabled_toggled(self, checked: bool):
        if self._config is None or self._proxy_master is None:
            return

        if checked and not self._config.custom_fflags_warning_accepted:
            warning = CustomFFlagWarningDialog(self)
            if warning.exec() != QDialog.DialogCode.Accepted:
                self._enable_toggle.blockSignals(True)
                self._enable_toggle.setChecked(False)
                self._enable_toggle.blockSignals(False)
                self._update_status()
                return
            self._config.custom_fflags_warning_accepted = True

        self._config.custom_fflags_enabled = checked
        self._sync_hotkeys()
        self._refresh_proxy_hosts()
        self._update_status()

    def _add_flag(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('Add Custom FastFlag')
        dialog.setMinimumWidth(620)
        form = QFormLayout(dialog)
        name_edit = QLineEdit()
        name_edit.setMinimumWidth(500)
        value_edit = QLineEdit()
        value_edit.setMinimumWidth(500)
        value_combo = CompactBooleanComboBox()
        value_combo.addItems(['True', 'False'])
        value_stack = QStackedWidget()
        value_stack.addWidget(value_edit)
        value_stack.addWidget(value_combo)
        form.addRow('Name', name_edit)
        form.addRow('Value', value_stack)

        def update_add_value_editor(name: str):
            value_stack.setCurrentWidget(
                value_combo if self._is_boolean_flag(name.strip()) else value_edit
            )

        name_edit.textChanged.connect(update_add_value_editor)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        name_edit.setFocus()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, 'Invalid FastFlag', 'FastFlag name cannot be empty.')
            return
        flags = self._flags_from_table()
        flags[name] = (
            value_combo.currentText()
            if self._is_boolean_flag(name)
            else value_edit.text()
        )
        self._set_flags(flags)

    def _browse_fflags(self):
        dialog = FFlagBrowserDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_flags:
            return
        flags = self._flags_from_table()
        flags.update(dialog.selected_flags)
        self._set_flags(flags)

    def _set_flags(self, flags: dict):
        if self._config is None:
            return
        self._config.custom_fflags = flags
        self._prune_hotkey_settings(set(self._config.custom_fflags))
        self._refresh_proxy_hosts()
        self._load_flags()

    def _import_mapping(self, payload):
        from ..proxy.addons.custom_fflags import normalize_custom_fflags

        if not isinstance(payload, dict):
            raise ValueError('The JSON root must be an object of FastFlag name/value pairs.')
        normalized = normalize_custom_fflags(payload)
        if len(normalized) != len(payload):
            raise ValueError('Every FastFlag value must be a string, number, or boolean.')
        merged = self._flags_from_table()
        merged.update(normalized)
        self._set_flags(merged)

    def _import_json(self):
        text, ok = QInputDialog.getMultiLineText(
            self,
            'Import Custom FastFlags',
            'Paste a JSON object:',
            '{\n  "DFIntTaskSchedulerTargetFps": "20"\n}',
        )
        if not ok:
            return
        try:
            self._import_mapping(json.loads(text))
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, 'Invalid FastFlag JSON', str(exc))

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Import Custom FastFlags', '', 'JSON Files (*.json);;All Files (*)'
        )
        if not path:
            return
        try:
            self._import_mapping(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(self, 'Could Not Import FastFlags', str(exc))

    def _export_json(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export Custom FastFlags', 'ClientAppSettings.json', 'JSON Files (*.json)'
        )
        if not path:
            return
        try:
            Path(path).write_text(self._json_text(), encoding='utf-8')
        except OSError as exc:
            QMessageBox.warning(self, 'Could Not Export FastFlags', str(exc))

    def _json_text(self) -> str:
        return json.dumps(self._flags_from_table(), indent=2, ensure_ascii=False)

    def _copy_json(self):
        QApplication.clipboard().setText(self._json_text())

    def _show_profiles(self):
        dialog = FastFlagProfilesDialog(self._flags_from_table(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.loaded_flags is not None:
            self._set_flags(dialog.loaded_flags)

    def _sort_rows(self, column: int):
        if self._sort_column == column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True

        rows = [
            (
                self._table.item(row, 0).text() if self._table.item(row, 0) else '',
                self._value_from_row(row),
            )
            for row in range(self._table.rowCount())
        ]
        def sort_value(entry: tuple[str, str]) -> str:
            if column == 0:
                return entry[0]
            if column == 1:
                return entry[1]
            if column == 2:
                return 'disabled' if entry[0] in self._disabled_flag_names() else 'enabled'
            return self._keybind_text(self._keybinds().get(entry[0]))

        rows.sort(
            key=lambda entry: (sort_value(entry).casefold(), entry[0].casefold(), entry[1].casefold()),
            reverse=not self._sort_ascending,
        )

        self._replace_table_rows(rows)

        self._table.horizontalHeader().setSortIndicator(
            column,
            Qt.SortOrder.AscendingOrder
            if self._sort_ascending
            else Qt.SortOrder.DescendingOrder,
        )
        self._filter_rows(self._search.text())

    def _delete_selected(self):
        rows = sorted({index.row() for index in self._table.selectedIndexes()}, reverse=True)
        if not rows:
            return
        self._replace_table_rows(
            [
                (self._table.item(row, 0).text(), self._value_from_row(row))
                for row in range(self._table.rowCount())
                if row not in rows
            ]
        )
        self._save_table()

    def _filter_rows(self, text: str):
        query = str(text or '').strip().lower()
        visible_count = 0
        for row in range(self._table.rowCount()):
            name = self._table.item(row, 0)
            name_text = name.text() if name else ''
            value_text = self._value_from_row(row)
            keybind_text = (
                self._table.item(row, 3).text()
                if self._windows_keybinds and self._table.item(row, 3)
                else ''
            )
            matches = (
                not query
                or query in name_text.lower()
                or query in value_text.lower()
                or query in keybind_text.lower()
            )
            self._table.setRowHidden(row, not matches)
            visible_count += matches
        self._resize_table_to_contents(visible_count)

    def _resize_table_to_contents(self, visible_count: int):
        """Let the outer modifications-page scroll area handle page overflow."""
        header_height = self._table.horizontalHeader().height()
        row_height = self._table.verticalHeader().defaultSectionSize() * visible_count
        content_height = header_height + row_height + (self._table.frameWidth() * 2)
        self._table.setFixedHeight(max(180, content_height))


class FFlagSection(QWidget):
    """The complete Fast Flags section content with all controls."""

    def __init__(
        self,
        manager: ModificationManager,
        roblox_monitor=None,
        config_manager=None,
        proxy_master=None,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = manager
        self._roblox_monitor = roblox_monitor
        self._config_manager = config_manager
        self._proxy_master = proxy_master

        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._write_flags)

        self._framerate_debounce_timer = QTimer()
        self._framerate_debounce_timer.setSingleShot(True)
        self._framerate_debounce_timer.setInterval(500)
        self._framerate_debounce_timer.timeout.connect(self._write_framerate_cap)

        self._setup_ui()
        self._load_from_manager()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Warning
        warn = QLabel(
            '\u26a0 Fast Flags are written to ClientSettings/ClientAppSettings.json '
            'in every detected Roblox directory.'
        )
        warn.setWordWrap(True)
        warn.setStyleSheet('color: #c90; padding: 4px;')
        layout.addWidget(warn)

        warn2 = QLabel(
            'If you are using a bootstrapper, you must disable its fflag management,'
            ' else it will overwrite Fleasion.'
        )
        warn2.setWordWrap(True)
        warn2.setStyleSheet('color: #c90; padding: 4px;')
        layout.addWidget(warn2)

        grid = QGridLayout()
        grid.setSpacing(8)
        row = 0

        # Rendering Mode
        grid.addWidget(QLabel('Rendering Mode'), row, 0)
        self._rendering_mode = DropdownComboBox()
        self._rendering_mode.addItems(['Default', 'D3D11', 'Vulkan', 'OpenGL'])
        self._rendering_mode.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._rendering_mode, row, 1)
        row += 1

        # MSAA
        grid.addWidget(QLabel('MSAA Level'), row, 0)
        self._msaa = DropdownComboBox()
        self._msaa.addItems(['Default', '1x (Lowest)', '2x', '4x (Highest)'])
        self._msaa.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._msaa, row, 1)
        row += 1

        # Fix Display Scaling
        self._dpi_scale = QCheckBox('Fix Display Scaling')
        self._dpi_scale.toggled.connect(self._schedule_write)
        grid.addWidget(self._dpi_scale, row, 0, 1, 2)
        row += 1

        # Alt+Enter Fullscreen
        self._alt_enter = QCheckBox('Alt+Enter Fullscreen')
        self._alt_enter.toggled.connect(self._schedule_write)
        grid.addWidget(self._alt_enter, row, 0, 1, 2)
        row += 1

        # Texture Quality
        grid.addWidget(QLabel('Texture Quality'), row, 0)
        self._texture_quality = DropdownComboBox()
        self._texture_quality.addItems(
            ['Default', 'Level 0 (Lowest)', 'Level 1', 'Level 2', 'Level 3 (Highest)']
        )
        self._texture_quality.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._texture_quality, row, 1)
        row += 1

        # Mesh LOD
        self._mesh_lod_enabled = QCheckBox('Mesh LOD Override')
        self._mesh_lod_enabled.toggled.connect(self._on_mesh_lod_toggle)
        grid.addWidget(self._mesh_lod_enabled, row, 0)
        lod_row = QHBoxLayout()
        lod_row.addWidget(QLabel('Default'))
        self._mesh_lod_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._mesh_lod_slider.setRange(
            0, 4
        )  # 0=Default(no flag), 1=Level0, 2=Level1, 3=Level2, 4=Level3
        self._mesh_lod_slider.setValue(4)
        self._mesh_lod_slider.setEnabled(False)
        self._mesh_lod_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._mesh_lod_slider.setTickInterval(1)
        self._mesh_lod_slider.valueChanged.connect(self._schedule_write)
        self._mesh_lod_value = QLabel('Level 3')
        self._mesh_lod_slider.valueChanged.connect(
            lambda v: self._mesh_lod_value.setText('Default' if v == 0 else f'Level {v - 1}')
        )
        lod_row.addWidget(self._mesh_lod_slider)
        lod_row.addWidget(self._mesh_lod_value)
        lod_container = QWidget()
        lod_container.setLayout(lod_row)
        grid.addWidget(lod_container, row, 1)
        row += 1

        # FRM Quality Override
        self._frm_enabled = QCheckBox('FRM Quality Override')
        self._frm_enabled.toggled.connect(self._on_frm_toggle)
        grid.addWidget(self._frm_enabled, row, 0)
        frm_row = QHBoxLayout()
        frm_row.addWidget(QLabel('Default'))
        self._frm_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._frm_slider.setRange(0, 21)  # 0=Default(no flag), 1-21=Quality levels
        self._frm_slider.setValue(21)
        self._frm_slider.setEnabled(False)
        self._frm_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._frm_slider.setTickInterval(1)
        self._frm_slider.valueChanged.connect(self._schedule_write)
        frm_row.addWidget(self._frm_slider)
        self._frm_value = QLabel('Quality 21')
        self._frm_slider.valueChanged.connect(
            lambda v: self._frm_value.setText('Default' if v == 0 else f'Quality {v}')
        )
        frm_row.addWidget(self._frm_value)
        frm_container = QWidget()
        frm_container.setLayout(frm_row)
        grid.addWidget(frm_container, row, 1)
        row += 1

        # Grey Sky
        self._grey_sky = QCheckBox('Grey Sky (Debug)')
        self._grey_sky.toggled.connect(self._schedule_write)
        grid.addWidget(self._grey_sky, row, 0, 1, 2)
        row += 1

        # Pause Voxelizer
        self._pause_vox = QCheckBox('Pause Voxelizer')
        self._pause_vox.toggled.connect(self._schedule_write)
        grid.addWidget(self._pause_vox, row, 0, 1, 2)
        row += 1

        # Grass spinners
        for label_text, attr_name in [
            ('Grass Distance Max', '_grass_max'),
            ('Grass Distance Min', '_grass_min'),
            ('Grass Motion Factor', '_grass_motion'),
        ]:
            grid.addWidget(QLabel(label_text), row, 0)
            spin = NoWheelSpinBox()
            spin.setRange(0, 100000)
            spin.setSpecialValueText('Default')
            spin.valueChanged.connect(self._schedule_write)
            setattr(self, attr_name, spin)
            grid.addWidget(spin, row, 1)
            row += 1

        # Roblox Framerate Cap (Global Settings) - NOT disabled when FFlagsare off
        framerate_label = QLabel('Framerate Cap (FPS) (GlobalBasicSettings, NOT an fflag)')
        self._framerate_cap_label = framerate_label  # Store for enable/disable
        grid.addWidget(framerate_label, row, 0)
        self._framerate_cap = NoWheelSpinBox()
        self._framerate_cap.setRange(0, 999999999)
        self._framerate_cap.setSpecialValueText('Default')
        self._framerate_cap.valueChanged.connect(self._on_framerate_changed)
        grid.addWidget(self._framerate_cap, row, 1)
        row += 1

        self._preset_container = QWidget()
        self._preset_container.setLayout(grid)
        layout.addWidget(self._preset_container)

        # Keep the allowlisted preset reset with the preset controls, above the
        # separate custom FastFlags editor.
        self._reset_btn = QPushButton('\u21ba Reset All Allowlisted FastFlags')
        self._reset_btn.clicked.connect(self._on_reset_all)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._reset_btn)
        layout.addLayout(btn_row)

        self._custom_fflag_editor = CustomFFlagEditor(
            self._config_manager, self._proxy_master, self
        )
        layout.addWidget(self._custom_fflag_editor)

        self.setLayout(layout)

    def set_presets_enabled(self, enabled: bool):
        """Enable the allowlisted/local controls without disabling the proxy editor."""
        self._preset_container.setEnabled(enabled)
        self._reset_btn.setEnabled(enabled)

    def _on_mesh_lod_toggle(self, checked):
        self._mesh_lod_slider.setEnabled(checked)
        self._schedule_write()

    def _on_frm_toggle(self, checked):
        self._frm_slider.setEnabled(checked)
        self._schedule_write()

    def _on_framerate_changed(self, *_args):
        """Schedule a write of the framerate cap setting."""
        self._framerate_debounce_timer.start()

    def _write_framerate_cap(self):
        """Write the framerate cap to GlobalBasicSettings_13.xml only if FFlagsare enabled."""
        value = self._framerate_cap.value()
        self._manager.framerate_cap = value

        # Only write if FFlags are enabled
        if not self._manager.fast_flags_enabled:
            return

        # Check if Roblox Player is running
        is_roblox_running = False
        if self._roblox_monitor:
            is_roblox_running = self._roblox_monitor.is_player_running()

        if is_roblox_running:
            # Queue the modification instead of writing immediately
            self._manager.pending_modifications_queue.enqueue_framerate_cap(value)
        else:
            if value:
                run_in_thread(self._manager.sync_saved_global_settings)()
            else:
                run_in_thread(self._manager.reset_framerate_cap)()

    def _schedule_write(self, *_args):
        self._debounce_timer.start()

    def _gather_settings(self) -> dict:
        # Convert display values to stored numeric values
        msaa_text = self._msaa.currentText()
        if msaa_text == 'Default':
            msaa_save = 'Default'
        else:
            # "1x (Lowest)" -> "1", "4x (Highest)" -> "4", "2x" -> "2"
            msaa_save = msaa_text.replace('x', '').split(' ')[0]

        tex_text = self._texture_quality.currentText()
        if tex_text == 'Default':
            tex_save = 'Default'
        else:
            # "Level 0 (Lowest)" -> "0", "Level 3 (Highest)" -> "3", "Level 1" -> "1"
            tex_save = tex_text.replace('Level ', '').split(' ')[0]

        return {
            'rendering_mode': self._rendering_mode.currentText(),
            'msaa': msaa_save,
            'disable_dpi_scale': self._dpi_scale.isChecked(),
            'alt_enter_fullscreen': self._alt_enter.isChecked(),
            'texture_quality': tex_save,
            'mesh_lod_enabled': self._mesh_lod_enabled.isChecked(),
            'mesh_lod': self._mesh_lod_slider.value(),
            'frm_quality_enabled': self._frm_enabled.isChecked(),
            'frm_quality': self._frm_slider.value(),
            'grey_sky': self._grey_sky.isChecked(),
            'pause_voxelizer': self._pause_vox.isChecked(),
            'grass_max': self._grass_max.value() or None,
            'grass_min': self._grass_min.value() or None,
            'grass_motion': self._grass_motion.value() or None,
        }

    def _write_flags(self):
        settings = self._gather_settings()

        # Check if Roblox Player is running
        is_roblox_running = False
        if self._roblox_monitor:
            is_roblox_running = self._roblox_monitor.is_player_running()

        if is_roblox_running:
            # Queue the modification instead of writing immediately
            self._manager.pending_modifications_queue.enqueue_fast_flags(settings)
        else:
            # Write immediately
            run_in_thread(self._manager.write_fast_flags)(settings)

    def _load_from_manager(self):
        """Populate controls from the persisted fast-flag settings."""
        s = self._manager.fast_flags

        # Block signals while bulk-setting
        widgets = [
            self._rendering_mode,
            self._msaa,
            self._dpi_scale,
            self._alt_enter,
            self._texture_quality,
            self._mesh_lod_enabled,
            self._mesh_lod_slider,
            self._frm_enabled,
            self._frm_slider,
            self._grey_sky,
            self._pause_vox,
            self._grass_max,
            self._grass_min,
            self._grass_motion,
            self._framerate_cap,
        ]
        for w in widgets:
            w.blockSignals(True)

        idx = self._rendering_mode.findText(s.get('rendering_mode', 'Default'))
        if idx >= 0:
            self._rendering_mode.setCurrentIndex(idx)

        # MSAA: stored as "1", "2", "4", display as "1x (Lowest)", "2x", "4x (Highest)"
        msaa_val = s.get('msaa', 'Default')
        if msaa_val != 'Default' and msaa_val is not None:
            if msaa_val == '1':
                msaa_display = '1x (Lowest)'
            elif msaa_val == '4':
                msaa_display = '4x (Highest)'
            else:
                msaa_display = f'{msaa_val}x'
        else:
            msaa_display = 'Default'
        idx = self._msaa.findText(msaa_display)
        if idx >= 0:
            self._msaa.setCurrentIndex(idx)

        self._dpi_scale.setChecked(s.get('disable_dpi_scale', False))
        self._alt_enter.setChecked(s.get('alt_enter_fullscreen', False))

        # Texture Quality: stored as "0", "1", "2", "3", display as "Level 0 (Lowest)", "Level 1", etc.
        tex_val = s.get('texture_quality', 'Default')
        if tex_val != 'Default' and tex_val is not None:
            if tex_val == '0':
                tex_display = 'Level 0 (Lowest)'
            elif tex_val == '3':
                tex_display = 'Level 3 (Highest)'
            else:
                tex_display = f'Level {tex_val}'
        else:
            tex_display = 'Default'
        idx = self._texture_quality.findText(tex_display)
        if idx >= 0:
            self._texture_quality.setCurrentIndex(idx)

        self._mesh_lod_enabled.setChecked(s.get('mesh_lod_enabled', False))
        mesh_lod_val = s.get('mesh_lod', 4)
        self._mesh_lod_slider.setValue(mesh_lod_val)
        self._mesh_lod_slider.setEnabled(s.get('mesh_lod_enabled', False))
        self._mesh_lod_value.setText(
            'Default' if mesh_lod_val == 0 else f'Level {mesh_lod_val - 1}'
        )

        self._frm_enabled.setChecked(s.get('frm_quality_enabled', False))
        frm_val = s.get('frm_quality', 21)
        self._frm_slider.setValue(frm_val)
        self._frm_slider.setEnabled(s.get('frm_quality_enabled', False))
        self._frm_value.setText('Default' if frm_val == 0 else f'Quality {frm_val}')

        self._grey_sky.setChecked(s.get('grey_sky', False))
        self._pause_vox.setChecked(s.get('pause_voxelizer', False))

        self._grass_max.setValue(s.get('grass_max') or 0)
        self._grass_min.setValue(s.get('grass_min') or 0)
        self._grass_motion.setValue(s.get('grass_motion') or 0)

        self._framerate_cap.setValue(self._manager.framerate_cap)

        for w in widgets:
            w.blockSignals(False)

    def _on_reset_all(self):
        """Reset all fast-flag controls to default and restore files."""
        self._rendering_mode.setCurrentIndex(0)
        self._msaa.setCurrentIndex(0)
        self._dpi_scale.setChecked(False)
        self._alt_enter.setChecked(False)
        self._texture_quality.setCurrentIndex(0)
        self._mesh_lod_enabled.setChecked(False)
        self._mesh_lod_slider.setValue(4)  # Default to Level 3 (rightmost)
        self._mesh_lod_value.setText('Level 3')
        self._frm_enabled.setChecked(False)
        self._frm_slider.setValue(21)  # Default to Quality 21 (rightmost)
        self._frm_value.setText('Quality 21')
        self._grey_sky.setChecked(False)
        self._pause_vox.setChecked(False)
        self._grass_max.setValue(0)
        self._grass_min.setValue(0)
        self._grass_motion.setValue(0)
        self._framerate_cap.setValue(0)
        self._manager.framerate_cap = 0
        self._manager.reset_framerate_cap()

        self._manager.fast_flags_enabled = False


# ═══════════════════════════════════════════════════════════════════
# ModificationsTab — the top-level tab widget
# ═══════════════════════════════════════════════════════════════════


class ModificationsTab(QWidget):
    """The entire Modifications tab, added to the dashboard's QTabWidget."""

    def __init__(
        self,
        mod_manager: ModificationManager,
        roblox_monitor=None,
        config_manager=None,
        proxy_master=None,
        parent=None,
    ):
        super().__init__(parent)
        self._manager = mod_manager
        self._roblox_monitor = roblox_monitor
        self._config_manager = config_manager
        self._proxy_master = proxy_master
        self._row_widgets: dict[str, ModRowWidget] = {}  # target_path -> widget
        self._custom_rows: list[ModRowWidget] = []

        self._setup_ui()
        self._update_status_bar()

        # Connect for live status bar updates
        mod_manager.apply_finished.connect(lambda _: self._update_status_bar())
        mod_manager.restore_finished.connect(self._update_status_bar)

        # Connect for Roblox player status changes
        if self._roblox_monitor:
            self._roblox_monitor.player_status_changed.connect(
                self._on_roblox_player_status_changed
            )

    def _setup_ui(self):
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        # Explicitly paint with the AlternateBase palette colour (the same
        # grey used by QTreeWidget / QGroupBox content in the Replacer and
        # Scraper tabs).  Without this, Fusion paints through to Window (#202020).
        container.setObjectName('_FleasionModContainer')
        self._mod_container = container
        self._container_layout = QVBoxLayout()
        self._container_layout.setSpacing(10)
        self._container_layout.setContentsMargins(10, 10, 10, 10)

        # ── Fast Flags ───────────────────────────────────────────
        self._fflag_toggle = QCheckBox('Enable allowlisted FastFlag presets')
        self._fflag_toggle.setChecked(self._manager.fast_flags_enabled)
        self._fflag_toggle.toggled.connect(self._on_fflag_toggle)

        fflag_section = CollapsibleSection(
            'Fast Flags \u26a0',
            expanded=False,
            header_widgets=[self._fflag_toggle],
        )
        self._fflag_widget = FFlagSection(
            self._manager,
            self._roblox_monitor,
            self._config_manager,
            self._proxy_master,
        )
        self._fflag_widget.set_presets_enabled(self._manager.fast_flags_enabled)
        fflag_section.add_widget(self._fflag_widget)

        self._container_layout.addWidget(fflag_section)

        # ── Default Skyboxes ─────────────────────────────────────
        sky_section = CollapsibleSection('Default Roblox Skyboxes', expanded=True)

        # "Apply to All Sky Faces" button
        apply_all_btn = QPushButton('Apply to All Sky Faces\u2026')
        apply_all_btn.setFixedWidth(180)
        apply_all_btn.clicked.connect(self._on_apply_all_sky)
        sky_section.add_widget(apply_all_btn)

        for name, path in SKYBOX_FACES:
            row = ModRowWidget(self._manager, name, path, file_filter=IMAGE_FILTER)
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        # Indoor sub-label
        indoor_label = QLabel('<i>Indoor Skybox</i>')
        indoor_label.setContentsMargins(0, 8, 0, 0)
        sky_section.add_widget(indoor_label)

        for name, path in INDOOR_FACES:
            row = ModRowWidget(self._manager, name, path, file_filter=IMAGE_FILTER)
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sky_section)

        # ── Textures ─────────────────────────────────────────────
        tex_section = CollapsibleSection('Textures', expanded=True)
        for name, path, filt in TEXTURES:
            row = ModRowWidget(self._manager, name, path, file_filter=filt)
            tex_section.add_widget(row)
            self._row_widgets[path] = row
        self._container_layout.addWidget(tex_section)

        # ── R6 Default Avatar Meshes ─────────────────────────────
        self._mesh_section = CollapsibleSection('R6 Default Avatar Meshes', expanded=True)
        if sys.platform.startswith('linux'):
            sober_mesh_warning = QLabel(
                '<b>Linux / Sober limitation:</b> R6 default avatar mesh replacements '
                'do not work in Sober. Sober developers have stated that Sober\'s '
                'asset_overlay does not respect R6 mesh replacements because of '
                'concerns around inappropriate meshes and cheats.'
            )
            sober_mesh_warning.setWordWrap(True)
            sober_mesh_warning.setContentsMargins(8, 4, 8, 8)
            sober_mesh_warning.setStyleSheet('color: #ffcc66;')
            self._mesh_section.add_widget(sober_mesh_warning)
        for name, path in AVATAR_MESHES:
            row = ModRowWidget(self._manager, name, path, file_filter=MESH_FILTER)
            self._mesh_section.add_widget(row)
            self._row_widgets[path] = row

        # Add Head Variant button
        add_head_btn = QPushButton('+ Add Head Variant')
        add_head_btn.setFixedWidth(150)
        add_head_btn.clicked.connect(self._on_add_head_variant)
        self._head_variant_layout = self._mesh_section.content_layout
        self._mesh_section.add_widget(add_head_btn)

        self._container_layout.addWidget(self._mesh_section)

        # ── Sounds ───────────────────────────────────────────────
        sounds_section = CollapsibleSection('Sounds', expanded=True)
        for name, path, bundled in SOUNDS:
            row = ModRowWidget(
                self._manager,
                name,
                path,
                file_filter=SOUND_FILTER,
                mute_bundled=bundled,
            )
            sounds_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sounds_section)

        # ── Custom Font ──────────────────────────────────────────
        font_section = CollapsibleSection('Custom Font', expanded=True)
        font_row = ModRowWidget(
            self._manager,
            'Custom Font',
            target_path_for_current_platform(r'content\fonts\CustomFont.ttf'),
            file_filter=FONT_FILTER,
            is_font=True,
        )
        font_section.add_widget(font_row)
        self._row_widgets[target_path_for_current_platform(r'content\fonts\CustomFont.ttf')] = (
            font_row
        )

        self._container_layout.addWidget(font_section)

        # Rebuild persisted head variant rows (headA–headP added in a previous session)
        _head_variant_set = set(HEAD_VARIANTS)
        for entry in self._manager.entries:
            target = entry.get('target_path', '')
            if not target or target in self._row_widgets:
                continue
            fname = Path(target.replace('\\', '/')).name
            if fname in _head_variant_set:
                name = fname.replace('.mesh', '').title()
                row = ModRowWidget(
                    self._manager, name, target, file_filter=MESH_FILTER, deletable=True
                )
                row.delete_requested.connect(partial(self._on_row_deleted, row))
                self._head_variant_layout.insertWidget(
                    self._head_variant_layout.count() - 1,
                    row,
                )
                self._row_widgets[target] = row

        # ── Custom Modifications ─────────────────────────────────
        self._custom_section = CollapsibleSection('Custom Modifications', expanded=True)

        add_custom_btn = QPushButton('+ Add Modification')
        add_custom_btn.setFixedWidth(160)
        add_custom_btn.clicked.connect(self._on_add_custom)
        self._custom_section.add_widget(add_custom_btn)

        self._custom_content_layout = self._custom_section.content_layout

        # Rebuild persisted custom entries
        for entry in self._manager.entries:
            target = entry.get('target_path', '')
            if target and target not in self._row_widgets:
                # This is likely a custom entry
                if not any(target == p for _, p in AVATAR_MESHES):
                    if not any(target == p for _, p in SKYBOX_FACES):
                        if not any(target == p for _, p in INDOOR_FACES):
                            if not any(target == p for _, p, _ in SOUNDS):
                                self._add_custom_row(
                                    entry.get('display_name', Path(target).name),
                                    target,
                                )

        self._container_layout.addWidget(self._custom_section)

        # Stretch at bottom
        self._container_layout.addStretch()

        container.setLayout(self._container_layout)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._footer_widget = footer_widget
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        self._status_label = QLabel()
        self._status_label.setStyleSheet('color: #888;')
        footer_layout.addWidget(self._status_label)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton('Clear Cache')
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)
        outer.addWidget(footer_widget)

        self.setLayout(outer)
        self._update_container_bg()

    def changeEvent(self, a0: QEvent | None):
        from PyQt6.QtCore import QEvent

        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.PaletteChange:
            self._update_container_bg()

    def _update_container_bg(self):
        """Keep the modifications container background consistent across themes.

        On the explicit Dark theme AlternateBase (64,64,64) is lighter than
        Window (32,32,32), giving a subtle card effect.  On the System theme
        with Windows dark mode the OS palette can make AlternateBase darker
        than Window, which looks wrong.  When that happens we force the same
        card colour the Dark theme uses.
        """
        pal = self.palette()
        win_light = pal.window().color().lightness()
        alt_light = pal.alternateBase().color().lightness()
        if win_light < 128 and alt_light <= win_light:
            # System dark mode: alternate-base is no lighter than window —
            # force the same card colour as the explicit dark theme.
            bg = 'background-color: rgb(64, 64, 64);'
        else:
            bg = 'background-color: palette(alternate-base);'
        self._mod_container.setStyleSheet(f'QWidget#_FleasionModContainer {{ {bg} }}')

    def _clear_roblox_cache(self):
        from .delete_cache import DeleteCacheWindow

        window = DeleteCacheWindow()
        window.show()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status_bar(self):
        applied = sum(1 for e in self._manager.entries if e.get('status') == 'applied')
        roblox_count = len(self._manager.roblox_dirs)
        noun = 'modification' if applied == 1 else 'modifications'
        status_label = getattr(self, '_status_label', None)
        if status_label is None:
            return
        try:
            status_label.setText(
                f'{applied} {noun} applied \u2022 '
                f'{format_count(roblox_count, "Roblox dir")} detected'
            )
        except RuntimeError:
            return

    # ------------------------------------------------------------------
    # Section: Avatar Meshes — Add Head Variant
    # ------------------------------------------------------------------

    def _on_add_head_variant(self):
        # Filter out already-added variants
        existing = {r._target_path for r in self._row_widgets.values()}
        available = [
            v
            for v in HEAD_VARIANTS
            if target_path_for_current_platform(rf'content\avatar\heads\{v}') not in existing
        ]
        if not available:
            QMessageBox.information(self, 'Head Variants', 'All head variants already added.')
            return

        item, ok = QInputDialog.getItem(
            self,
            'Add Head Variant',
            'Select variant:',
            available,
            0,
            False,
        )
        if ok and item:
            target = target_path_for_current_platform(rf'content\avatar\heads\{item}')
            name = item.replace('.mesh', '').title()
            row = ModRowWidget(self._manager, name, target, file_filter=MESH_FILTER, deletable=True)
            row.delete_requested.connect(partial(self._on_row_deleted, row))
            # Insert before the "Add" button (last widget)
            self._head_variant_layout.insertWidget(
                self._head_variant_layout.count() - 1,
                row,
            )
            self._row_widgets[target] = row

    # ------------------------------------------------------------------
    # Section: Skybox — Apply to All
    # ------------------------------------------------------------------

    def _on_apply_all_sky(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select file for all sky faces',
            '',
            IMAGE_FILTER,
        )
        if not path:
            # Try asset ID instead
            text, ok = QInputDialog.getText(
                self,
                'Asset ID for All Sky Faces',
                'Enter an Asset ID (or cancel):',
            )
            if ok and text.strip() and text.strip().isdigit():
                for _, target in SKYBOX_FACES:
                    if target in self._row_widgets:
                        self._row_widgets[target].apply_source_external('asset_id', text.strip())
            return

        for _, target in SKYBOX_FACES:
            if target in self._row_widgets:
                self._row_widgets[target].apply_source_external('local_file', path)

    # ------------------------------------------------------------------
    # Section: Custom Modifications
    # ------------------------------------------------------------------

    def _on_add_custom(self):
        dlg = _CustomModDialog(self._manager, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.display_name
            target = dlg.target_path
            raw_source = dlg.raw_source

            row = self._add_custom_row(name, target)
            if raw_source:
                # Route through the row's own detection pipeline so that
                # 'remove', CDN URLs, asset IDs etc. all work correctly.
                row._set_source_text_silent(raw_source)
                row._apply_from_text()

    def _add_custom_row(self, name: str, target_path: str) -> ModRowWidget:
        row = ModRowWidget(self._manager, name, target_path, deletable=True)
        row.delete_requested.connect(partial(self._on_row_deleted, row))
        # Insert before the "Add" button (first widget in custom section)
        self._custom_content_layout.insertWidget(
            max(0, self._custom_content_layout.count() - 1),
            row,
        )
        self._row_widgets[target_path] = row
        self._custom_rows.append(row)
        return row

    def _on_row_deleted(self, row: ModRowWidget, _entry_id: str):
        target = row._target_path
        if target in self._row_widgets:
            del self._row_widgets[target]
        if row in self._custom_rows:
            self._custom_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._update_status_bar()

    # ------------------------------------------------------------------
    # Fast Flags toggle
    # ------------------------------------------------------------------

    def _on_fflag_toggle(self, checked: bool):
        self._manager.fast_flags_enabled = checked
        self._fflag_widget.set_presets_enabled(checked)
        if checked:
            # Immediately write current settings
            self._fflag_widget._schedule_write()
            self._fflag_widget._write_framerate_cap()

    def _on_roblox_player_status_changed(self, is_running: bool):
        """Apply all queued modifications when Roblox Player exits."""
        if not is_running:
            # Roblox has exited, apply any pending modifications
            self._manager.apply_pending_modifications()


# ═══════════════════════════════════════════════════════════════════
# Custom Modification Dialog
# ═══════════════════════════════════════════════════════════════════


def _relative_target_path_for_resource_file(
    path: str | Path, roblox_dirs: list[Path]
) -> str | None:
    """Return a safe relative Roblox resource path for a selected file, if possible."""
    try:
        selected = Path(path).expanduser()
        selected_resolved = selected.resolve(strict=True)
    except OSError:
        return None

    for raw_root in roblox_dirs:
        try:
            root = Path(raw_root).expanduser().resolve(strict=True)
            rel = selected_resolved.relative_to(root)
            normalized = normalise_target_path(rel.as_posix())
        except OSError, ValueError:
            continue
        return normalized.as_posix()
    return None


class _CustomModDialog(QDialog):
    """Dialog for adding a custom modification entry."""

    def __init__(self, manager: ModificationManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self.display_name = ''
        self.target_path = ''
        self.raw_source = ''

        self.setWindowTitle('Add Custom Modification')
        self.resize(500, 200)

        layout = QVBoxLayout()

        # Display name
        row1 = QHBoxLayout()
        row1.addWidget(QLabel('Display name:'))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText('e.g. Custom Skybox')
        row1.addWidget(self._name_edit)
        layout.addLayout(row1)

        # Target path
        row2 = QHBoxLayout()
        row2.addWidget(QLabel('Target path:'))
        self._target_edit = FileDropLineEdit()
        self._target_edit.setPlaceholderText(r'content\sounds\oof.ogg')
        self._target_edit.fileDropped.connect(self._on_target_file_dropped)
        row2.addWidget(self._target_edit)
        self._browse_roblox_btn = QPushButton('Browse Roblox Dir\u2026')
        self._browse_roblox_btn.clicked.connect(self._browse_roblox)
        row2.addWidget(self._browse_roblox_btn)
        layout.addLayout(row2)

        # Source
        row3 = QHBoxLayout()
        row3.addWidget(QLabel('Source:'))
        self._source_edit = FileDropLineEdit()
        self._source_edit.setPlaceholderText(
            'ID, URL (http://...), path (C:\\...), or "remove" to remove'
        )
        row3.addWidget(self._source_edit)
        browse_btn = QPushButton('Browse\u2026')
        browse_btn.setAutoDefault(False)
        browse_btn.clicked.connect(self._browse_source)
        row3.addWidget(browse_btn)
        layout.addLayout(row3)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton('Add')
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _warn_target_outside_roblox_dirs(self):
        QMessageBox.warning(
            self,
            'Invalid Target',
            'Target files must be inside a detected Roblox resource directory. '
            'Use the Source field for external replacement files.',
        )

    def _on_target_file_dropped(self, path: str):
        rel = _relative_target_path_for_resource_file(path, self._manager.roblox_dirs)
        if rel is None:
            self._target_edit.clear()
            self._warn_target_outside_roblox_dirs()
            return
        self._target_edit.setText(rel)

    def _browse_roblox(self):
        """Open file dialog starting at the first Roblox directory."""
        start = ''
        if self._manager.roblox_dirs:
            start = str(self._manager.roblox_dirs[0])
        path, _ = QFileDialog.getOpenFileName(
            self,
            'Select target file in Roblox directory',
            start,
        )
        if path:
            rel = _relative_target_path_for_resource_file(path, self._manager.roblox_dirs)
            if rel is None:
                self._warn_target_outside_roblox_dirs()
                return
            self._target_edit.setText(rel)

    def _browse_source(self):
        # Try to open the dialog in the directory/path the user may have pasted
        current_val = self._source_edit.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            try:
                p = Path(current_val)
                if p.exists():
                    # If it's a directory, start there; if it's a file, start in its parent
                    initial_dir = str(p) if p.is_dir() else str(p.parent)
                else:
                    # If the exact path doesn't exist but the parent does, use the parent
                    if p.parent.exists():
                        initial_dir = str(p.parent)
            except Exception:
                initial_dir = ''

        path, _ = QFileDialog.getOpenFileName(self, 'Select source file', initial_dir)
        if path:
            self._source_edit.setText(path)

    def _on_accept(self):
        name = self._name_edit.text().strip()
        target = self._target_edit.text().strip()
        if not name:
            QMessageBox.warning(self, 'Missing', 'Please enter a display name.')
            return
        if not target:
            QMessageBox.warning(self, 'Missing', 'Please enter a target path.')
            return
        try:
            target = normalise_target_path(target).as_posix()
        except ValueError as exc:
            QMessageBox.warning(self, 'Invalid Target', str(exc))
            return
        self.display_name = name
        self.target_path = target
        raw = self._source_edit.text().strip().strip('"\'')
        self.raw_source = raw
        self.accept()
