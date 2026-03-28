"""Modifications tab — combined Fishstrap Mods + FastFlags panel."""

from __future__ import annotations

import os
from functools import partial
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..modifications.manager import ModificationManager
from ..utils import log_buffer
from ..utils.threading import run_in_thread

# ---------------------------------------------------------------------------
# Built-in entry definitions
# ---------------------------------------------------------------------------

AVATAR_MESHES = [
    ('Left Arm',  r'content\avatar\meshes\leftarm.mesh'),
    ('Left Leg',  r'content\avatar\meshes\leftleg.mesh'),
    ('Right Arm', r'content\avatar\meshes\rightarm.mesh'),
    ('Right Leg', r'content\avatar\meshes\rightleg.mesh'),
    ('Torso',     r'content\avatar\meshes\torso.mesh'),
    ('Head',      r'content\avatar\heads\head.mesh'),
]

HEAD_VARIANTS = [f'head{chr(c)}.mesh' for c in range(ord('A'), ord('P') + 1)]

SKYBOX_FACES = [
    ('Sky \u2014 Back',  r'PlatformContent\pc\textures\sky\sky512_bk.tex'),
    ('Sky \u2014 Down',  r'PlatformContent\pc\textures\sky\sky512_dn.tex'),
    ('Sky \u2014 Front', r'PlatformContent\pc\textures\sky\sky512_ft.tex'),
    ('Sky \u2014 Left',  r'PlatformContent\pc\textures\sky\sky512_lf.tex'),
    ('Sky \u2014 Right', r'PlatformContent\pc\textures\sky\sky512_rt.tex'),
    ('Sky \u2014 Up',    r'PlatformContent\pc\textures\sky\sky512_up.tex'),
]

INDOOR_FACES = [
    ('Indoor \u2014 Back',  r'PlatformContent\pc\textures\sky\indoor512_bk.tex'),
    ('Indoor \u2014 Down',  r'PlatformContent\pc\textures\sky\indoor512_dn.tex'),
    ('Indoor \u2014 Front', r'PlatformContent\pc\textures\sky\indoor512_ft.tex'),
    ('Indoor \u2014 Left',  r'PlatformContent\pc\textures\sky\indoor512_lf.tex'),
    ('Indoor \u2014 Right', r'PlatformContent\pc\textures\sky\indoor512_rt.tex'),
    ('Indoor \u2014 Up',    r'PlatformContent\pc\textures\sky\indoor512_up.tex'),
]

SOUNDS = [
    ('Footsteps (Plastic)', r'content\sounds\action_footsteps_plastic.mp3', 'bundled:empty.mp3'),
    ('Falling',             r'content\sounds\action_falling.ogg',           'bundled:empty.ogg'),
    ('Get Up',              r'content\sounds\action_get_up.mp3',            'bundled:empty.mp3'),
    ('Jump',                r'content\sounds\action_jump.mp3',              'bundled:empty.mp3'),
    ('Jump Land',           r'content\sounds\action_jump_land.mp3',         'bundled:empty.mp3'),
    ('Swim',                r'content\sounds\action_swim.mp3',              'bundled:empty.mp3'),
    ('Explosion',           r'content\sounds\impact_explosion_03.mp3',      'bundled:empty.mp3'),
    ('Water Impact',        r'content\sounds\impact_water.mp3',             'bundled:empty.mp3'),
    ('Oof',                 r'content\sounds\oof.ogg',                      'bundled:empty.ogg'),
    ('Ouch',                r'content\sounds\ouch.ogg',                     'bundled:empty.ogg'),
    ('Volume Slider',       r'content\sounds\volume_slider.ogg',            'bundled:empty.ogg'),
]

# File-type filter strings for QFileDialog
MESH_FILTER = 'Mesh Files (*.mesh *.obj);;All Files (*)'
IMAGE_FILTER = 'Image Files (*.png *.jpg *.jpeg *.tex);;All Files (*)'
SOUND_FILTER = 'Audio Files (*.mp3 *.ogg *.wav);;All Files (*)'
FONT_FILTER = 'Font Files (*.ttf *.otf *.ttc);;All Files (*)'

# Status badge styling
_STATUS_STYLES = {
    'not_set':        'color: #888; font-style: italic;',
    'pending':        'color: #4a9eda;',
    'applied':        'font-style: normal;',
    'orphaned_stash': 'color: #c90; font-weight: bold;',
}

# ═══════════════════════════════════════════════════════════════════
# _RichTextButton — QPushButton-like label that renders HTML/rich text
# ═══════════════════════════════════════════════════════════════════

class _RichTextButton(QPushButton):
    """QPushButton that draws a label and a larger suffix character, each independently
    vertically centred so mixed font sizes don't shift each other's position."""

    def __init__(self, label: str, suffix: str = '', suffix_size_offset: int = 0,
                 y_offset: int = 0, parent=None):
        super().__init__(parent)
        self._label = label
        self._suffix = suffix
        self._suffix_size_offset = suffix_size_offset
        self._y_offset = y_offset
        # Non-empty text so Qt includes normal button padding in sizeHint.
        super().setText('\u200b')

    def paintEvent(self, a0):
        from PyQt6.QtWidgets import QStyleOptionButton, QStyle
        from PyQt6.QtGui import QPainter, QFontMetrics, QFont, QPalette

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

        if self._suffix and self._suffix_size_offset:
            large_font = QFont(base_font)
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
            baseline_label = int(center_y + (fm_base.ascent() - fm_base.descent()) / 2) + self._y_offset
            baseline_arrow = int(center_y + (fm_large.ascent() - fm_large.descent()) / 2) + self._y_offset

            painter.setFont(base_font)
            painter.drawText(start_x, baseline_label, label_text)
            painter.setFont(large_font)
            painter.drawText(start_x + label_w, baseline_arrow, self._suffix)
        else:
            full_text = self._label + self._suffix
            fm = QFontMetrics(base_font)
            w = fm.horizontalAdvance(full_text)
            baseline = int(center_y + (fm.ascent() - fm.descent()) / 2) + self._y_offset
            painter.setFont(base_font)
            painter.drawText(int(cr.x() + (cr.width() - w) / 2), baseline, full_text)

        painter.end()


# ═══════════════════════════════════════════════════════════════════
# CollapsibleSection
# ═══════════════════════════════════════════════════════════════════

class CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands its content."""

    def __init__(self, title: str, parent=None, expanded: bool = True,
                 header_widgets: list[QWidget] | None = None):
        super().__init__(parent)

        self._expanded = expanded
        self._animation: QPropertyAnimation | None = None

        # --- Header row ---
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(4, 4, 4, 4)

        self._arrow = QPushButton('\u25BC' if expanded else '\u25B6')
        self._arrow.setFixedSize(22, 22)
        self._arrow.setFlat(True)
        self._arrow.setStyleSheet(
            'font-size: 11px; border: none;' if expanded else 'font-size: 19px; border: none;'
        )
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
        from PyQt6.QtGui import QPainter, QColor, QPainterPath
        from PyQt6.QtCore import QRectF
        is_dark = self.palette().window().color().lightness() < 128
        bg     = QColor('#272727') if is_dark else QColor('#f0f0f0')
        border = QColor('#3a3a3a') if is_dark else QColor('#d0d0d0')
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 8.0, 8.0)
        painter.fillPath(path, bg)
        painter.setPen(border)
        painter.drawPath(path)
        painter.end()

    @property
    def content_layout(self) -> QVBoxLayout:
        return self._content_layout

    def add_widget(self, widget: QWidget):
        self._content_layout.addWidget(widget)

    def toggle(self):
        self._expanded = not self._expanded
        if self._expanded:
            self._arrow.setText('\u25BC')
            self._arrow.setStyleSheet('font-size: 11px; border: none;')
        else:
            self._arrow.setText('\u25B6')
            self._arrow.setStyleSheet('font-size: 19px; border: none;')

        self._animation = QPropertyAnimation(self._content, b'maximumHeight')
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutQuad)

        if self._expanded:
            self._animation.setStartValue(self._content.maximumHeight())
            self._animation.setEndValue(self._content.sizeHint().height())
            self._animation.finished.connect(
                lambda: self._content.setMaximumHeight(16777215)
            )
        else:
            # Capture the real rendered height so the animation starts from
            # the actual visible size rather than QWIDGETSIZE_MAX.
            actual = self._content.height()
            self._content.setMaximumHeight(actual)
            self._animation.setStartValue(actual)
            self._animation.setEndValue(0)

        self._animation.start()


# ═══════════════════════════════════════════════════════════════════
# DropdownComboBox — QComboBox with ▼ indicator instead of OS arrow
# ═══════════════════════════════════════════════════════════════════

class DropdownComboBox(QComboBox):
    """QComboBox that paints ▼ as the dropdown indicator."""

    def paintEvent(self, e):
        from PyQt6.QtWidgets import QStylePainter, QStyleOptionComboBox, QStyle

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
            QStyle.ComplexControl.CC_ComboBox, opt,
            QStyle.SubControl.SC_ComboBoxArrow, self,
        )
        painter.fillRect(arrow_rect.adjusted(1, 1, -1, -1), self.palette().button())
        painter.setPen(self.palette().buttonText().color())
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignCenter, '\u25BC')


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
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText('ID, URL (http://...), path (C:\\...), or "remove" to remove')
        self._source_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
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
        self._preview_btn = _RichTextButton('Preview', '\u25b6', suffix_size_offset=5)
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
        from ..modifications.manager import MOD_ORIGINALS_DIR
        roblox_dirs = self._manager.roblox_dirs
        if not roblox_dirs:
            return
        stash = MOD_ORIGINALS_DIR / roblox_dirs[0].name / self._target_path
        if stash.is_file():
            self._update_status('orphaned_stash')
            self._status_label.setToolTip(
                'A stash of the original file was found on disk but Fleasion has '
                'no active record for this modification. This can happen if you '
                'manually replaced the file, or if Fleasion closed unexpectedly. '
                'Click \u21BA to restore the original file.'
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
            'not_set':        'Not Set',
            'pending':        'Applying...',
            'applied':        'Applied',
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
            self._manager.update_entry(self._entry_id,
                                       source_type=source_type,
                                       source_value=source_value)
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
            self._manager.clear_entry(self._entry_id)
            # clear_entry deletes the entry from JSON; drop our reference
            # so _apply_source correctly calls add_entry next time.
            self._entry_id = None
        else:
            # Orphaned stash with no JSON entry at all — restore directly.
            self._manager.restore_orphaned_stash(self._target_path)
        self._set_source_text_silent('')
        self._update_status('not_set')

    def _on_delete(self):
        if self._entry_id:
            self._manager.remove_entry(self._entry_id)
        self.delete_requested.emit(self._entry_id or '')

    def _on_preview(self):
        dlg = ModPreviewDialog(
            self._manager, self._target_path, self._display_name, self,
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
                if src_type in ('local_file', 'asset_id', 'bundled'):
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
            return 'asset_id', text[len('rbxassetid://'):]
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
            self, 'Select replacement file', initial_dir, self._file_filter,
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

    def __init__(self, manager: ModificationManager, target_path: str,
                 display_name: str, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._target_path = target_path
        self.setWindowTitle(f'Preview \u2014 {display_name}')
        self.resize(500, 400)

        layout = QVBoxLayout()
        tabs = QTabWidget()

        # Modification tab
        mod_widget = self._build_preview_widget('mod')
        tabs.addTab(mod_widget, 'Modification')

        # Original tab
        orig_widget = self._build_preview_widget('original')
        tabs.addTab(orig_widget, 'Original')

        layout.addWidget(tabs)

        # Export button
        export_btn = QPushButton('Export\u2026')
        export_btn.clicked.connect(self._on_export)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
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
            layout.addWidget(QLabel('No data available'))
            container.setLayout(layout)
            return container

        lower = self._target_path.lower()

        # Image / Texture
        if lower.endswith(('.tex', '.png', '.jpg', '.jpeg')):
            label = QLabel()
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            display_bytes = data
            if lower.endswith('.tex'):
                from ..modifications.dds_to_png import tex_to_png_bytes
                converted = tex_to_png_bytes(data)
                if converted:
                    display_bytes = converted
                else:
                    layout.addWidget(QLabel('Could not decode .tex file'))
                    container.setLayout(layout)
                    return container

            from PyQt6.QtGui import QPixmap
            pixmap = QPixmap()
            pixmap.loadFromData(display_bytes)
            if not pixmap.isNull():
                scaled = pixmap.scaled(460, 350,
                                       Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation)
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

        else:
            layout.addWidget(QLabel(f'No preview available for this file type'))

        container.setLayout(layout)
        return container

    def _load_data(self, mode: str) -> bytes | None:
        """Load file bytes for preview. mode='mod' or 'original'."""
        from ..modifications.manager import MOD_ORIGINALS_DIR
        if not self._manager.roblox_dirs:
            return None
        roblox_dir = self._manager.roblox_dirs[0]

        if mode == 'original':
            # Try stash first
            stash = MOD_ORIGINALS_DIR / roblox_dir.name / self._target_path
            if stash.is_file():
                return stash.read_bytes()
            # Fall back to current file (unmodified)
            dst = roblox_dir / self._target_path
            return dst.read_bytes() if dst.is_file() else None

        # mode == 'mod' — read the current (modified) file from Roblox dir
        dst = roblox_dir / self._target_path
        return dst.read_bytes() if dst.is_file() else None

    def _on_export(self):
        path, _ = QFileDialog.getSaveFileName(
            self, 'Export File',
            Path(self._target_path).name,
        )
        if path:
            data = self._load_data('mod')
            if data:
                Path(path).write_bytes(data)


# ═══════════════════════════════════════════════════════════════════
# Fast Flags section widgets
# ═══════════════════════════════════════════════════════════════════

class FFlagSection(QWidget):
    """The complete Fast Flags section content with all controls."""

    def __init__(self, manager: ModificationManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._write_flags)

        self._setup_ui()
        self._load_from_manager()

    def _setup_ui(self):
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Warning
        warn = QLabel(
            '\u26A0 Fast Flags are written to ClientSettings/ClientAppSettings.json '
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
        self._msaa.addItems(['Default', '1', '2', '4'])
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
        self._texture_quality.addItems(['Default', '0', '1', '2', '3'])
        self._texture_quality.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._texture_quality, row, 1)
        row += 1

        # Mesh LOD
        self._mesh_lod_enabled = QCheckBox('Mesh LOD Override')
        self._mesh_lod_enabled.toggled.connect(self._on_mesh_lod_toggle)
        grid.addWidget(self._mesh_lod_enabled, row, 0)
        lod_row = QHBoxLayout()
        self._mesh_lod_slider = QSlider(Qt.Orientation.Horizontal)
        self._mesh_lod_slider.setRange(0, 3)
        self._mesh_lod_slider.setValue(3)
        self._mesh_lod_slider.setEnabled(False)
        self._mesh_lod_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._mesh_lod_slider.setTickInterval(1)
        self._mesh_lod_slider.valueChanged.connect(self._schedule_write)
        self._mesh_lod_value = QLabel('3')
        self._mesh_lod_slider.valueChanged.connect(
            lambda v: self._mesh_lod_value.setText(str(v))
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
        self._frm_spin = QSpinBox()
        self._frm_spin.setRange(1, 21)
        self._frm_spin.setValue(21)
        self._frm_spin.setEnabled(False)
        self._frm_spin.valueChanged.connect(self._schedule_write)
        grid.addWidget(self._frm_spin, row, 1)
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
            spin = QSpinBox()
            spin.setRange(0, 100000)
            spin.setSpecialValueText('Default')
            spin.valueChanged.connect(self._schedule_write)
            setattr(self, attr_name, spin)
            grid.addWidget(spin, row, 1)
            row += 1

        layout.addLayout(grid)

        # Reset button
        reset_btn = QPushButton('\u21BA Reset All Fast Flags')
        reset_btn.clicked.connect(self._on_reset_all)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(reset_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _on_mesh_lod_toggle(self, checked):
        self._mesh_lod_slider.setEnabled(checked)
        self._schedule_write()

    def _on_frm_toggle(self, checked):
        self._frm_spin.setEnabled(checked)
        self._schedule_write()

    def _schedule_write(self, *_args):
        self._debounce_timer.start()

    def _gather_settings(self) -> dict:
        return {
            'rendering_mode': self._rendering_mode.currentText(),
            'msaa': self._msaa.currentText(),
            'disable_dpi_scale': self._dpi_scale.isChecked(),
            'alt_enter_fullscreen': self._alt_enter.isChecked(),
            'texture_quality': self._texture_quality.currentText(),
            'mesh_lod_enabled': self._mesh_lod_enabled.isChecked(),
            'mesh_lod': self._mesh_lod_slider.value(),
            'frm_quality_enabled': self._frm_enabled.isChecked(),
            'frm_quality': self._frm_spin.value(),
            'grey_sky': self._grey_sky.isChecked(),
            'pause_voxelizer': self._pause_vox.isChecked(),
            'grass_max': self._grass_max.value() or None,
            'grass_min': self._grass_min.value() or None,
            'grass_motion': self._grass_motion.value() or None,
        }

    def _write_flags(self):
        settings = self._gather_settings()
        run_in_thread(self._manager.write_fast_flags)(settings)

    def _load_from_manager(self):
        """Populate controls from the persisted fast-flag settings."""
        s = self._manager.fast_flags
        if not s:
            return

        # Block signals while bulk-setting
        widgets = [
            self._rendering_mode, self._msaa, self._dpi_scale,
            self._alt_enter, self._texture_quality, self._mesh_lod_enabled,
            self._mesh_lod_slider, self._frm_enabled, self._frm_spin,
            self._grey_sky, self._pause_vox, self._grass_max,
            self._grass_min, self._grass_motion,
        ]
        for w in widgets:
            w.blockSignals(True)

        idx = self._rendering_mode.findText(s.get('rendering_mode', 'Default'))
        if idx >= 0:
            self._rendering_mode.setCurrentIndex(idx)

        idx = self._msaa.findText(str(s.get('msaa', 'Default')))
        if idx >= 0:
            self._msaa.setCurrentIndex(idx)

        self._dpi_scale.setChecked(s.get('disable_dpi_scale', False))
        self._alt_enter.setChecked(s.get('alt_enter_fullscreen', False))

        idx = self._texture_quality.findText(str(s.get('texture_quality', 'Default')))
        if idx >= 0:
            self._texture_quality.setCurrentIndex(idx)

        self._mesh_lod_enabled.setChecked(s.get('mesh_lod_enabled', False))
        self._mesh_lod_slider.setValue(s.get('mesh_lod', 3))
        self._mesh_lod_slider.setEnabled(s.get('mesh_lod_enabled', False))

        self._frm_enabled.setChecked(s.get('frm_quality_enabled', False))
        self._frm_spin.setValue(s.get('frm_quality', 21))
        self._frm_spin.setEnabled(s.get('frm_quality_enabled', False))

        self._grey_sky.setChecked(s.get('grey_sky', False))
        self._pause_vox.setChecked(s.get('pause_voxelizer', False))

        self._grass_max.setValue(s.get('grass_max') or 0)
        self._grass_min.setValue(s.get('grass_min') or 0)
        self._grass_motion.setValue(s.get('grass_motion') or 0)

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
        self._mesh_lod_slider.setValue(3)
        self._frm_enabled.setChecked(False)
        self._frm_spin.setValue(21)
        self._grey_sky.setChecked(False)
        self._pause_vox.setChecked(False)
        self._grass_max.setValue(0)
        self._grass_min.setValue(0)
        self._grass_motion.setValue(0)

        self._manager.fast_flags_enabled = False
        try:
            self._manager.fflag_manager.restore()
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════
# ModificationsTab — the top-level tab widget
# ═══════════════════════════════════════════════════════════════════

class ModificationsTab(QWidget):
    """The entire Modifications tab, added to the dashboard's QTabWidget."""

    def __init__(self, mod_manager: ModificationManager, parent=None):
        super().__init__(parent)
        self._manager = mod_manager
        self._row_widgets: dict[str, ModRowWidget] = {}  # target_path -> widget
        self._custom_rows: list[ModRowWidget] = []

        self._setup_ui()
        self._update_status_bar()

        # Connect for live status bar updates
        mod_manager.apply_finished.connect(lambda _: self._update_status_bar())
        mod_manager.restore_finished.connect(self._update_status_bar)

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
        container.setStyleSheet(
            'QWidget#_FleasionModContainer { background-color: palette(alternate-base); }'
        )
        self._container_layout = QVBoxLayout()
        self._container_layout.setSpacing(10)
        self._container_layout.setContentsMargins(10, 10, 10, 10)

        # ── R6 Default Avatar Meshes ─────────────────────────────
        self._mesh_section = CollapsibleSection('R6 Default Avatar Meshes', expanded=True)
        for name, path in AVATAR_MESHES:
            row = ModRowWidget(self._manager, name, path,
                               file_filter=MESH_FILTER)
            self._mesh_section.add_widget(row)
            self._row_widgets[path] = row

        # Add Head Variant button
        add_head_btn = QPushButton('+ Add Head Variant')
        add_head_btn.setFixedWidth(150)
        add_head_btn.clicked.connect(self._on_add_head_variant)
        self._head_variant_layout = self._mesh_section.content_layout
        self._mesh_section.add_widget(add_head_btn)

        self._container_layout.addWidget(self._mesh_section)

        # ── Skybox ───────────────────────────────────────────────
        sky_section = CollapsibleSection('Skybox', expanded=True)

        # "Apply to All Sky Faces" button
        apply_all_btn = QPushButton('Apply to All Sky Faces\u2026')
        apply_all_btn.setFixedWidth(180)
        apply_all_btn.clicked.connect(self._on_apply_all_sky)
        sky_section.add_widget(apply_all_btn)

        for name, path in SKYBOX_FACES:
            row = ModRowWidget(self._manager, name, path,
                               file_filter=IMAGE_FILTER)
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        # Indoor sub-label
        indoor_label = QLabel('<i>Indoor Skybox</i>')
        indoor_label.setContentsMargins(0, 8, 0, 0)
        sky_section.add_widget(indoor_label)

        for name, path in INDOOR_FACES:
            row = ModRowWidget(self._manager, name, path,
                               file_filter=IMAGE_FILTER)
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sky_section)

        # ── Sounds ───────────────────────────────────────────────
        sounds_section = CollapsibleSection('Sounds', expanded=True)
        for name, path, bundled in SOUNDS:
            row = ModRowWidget(self._manager, name, path,
                               file_filter=SOUND_FILTER,
                               mute_bundled=bundled)
            sounds_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sounds_section)

        # ── Custom Font ──────────────────────────────────────────
        font_section = CollapsibleSection('Custom Font', expanded=False)
        font_row = ModRowWidget(
            self._manager, 'Custom Font',
            r'content\fonts\CustomFont.ttf',
            file_filter=FONT_FILTER,
            is_font=True,
        )
        font_section.add_widget(font_row)
        self._row_widgets[r'content\fonts\CustomFont.ttf'] = font_row

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
                row = ModRowWidget(self._manager, name, target,
                                   file_filter=MESH_FILTER, deletable=True)
                row.delete_requested.connect(partial(self._on_row_deleted, row))
                self._head_variant_layout.insertWidget(
                    self._head_variant_layout.count() - 1, row,
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

        # ── Fast Flags ───────────────────────────────────────────
        self._fflag_toggle = QCheckBox('Enable Fast Flags')
        self._fflag_toggle.setChecked(self._manager.fast_flags_enabled)
        self._fflag_toggle.toggled.connect(self._on_fflag_toggle)

        fflag_section = CollapsibleSection(
            'Fast Flags \u26A0', expanded=False,
            header_widgets=[self._fflag_toggle],
        )
        self._fflag_widget = FFlagSection(self._manager)
        self._fflag_widget.setEnabled(self._manager.fast_flags_enabled)
        fflag_section.add_widget(self._fflag_widget)

        self._container_layout.addWidget(fflag_section)

        # Stretch at bottom
        self._container_layout.addStretch()

        container.setLayout(self._container_layout)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        # ── Status bar ───────────────────────────────────────────
        self._status_label = QLabel()
        self._status_label.setContentsMargins(8, 4, 8, 4)
        self._status_label.setStyleSheet('color: #888;')
        outer.addWidget(self._status_label)

        self.setLayout(outer)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _update_status_bar(self):
        applied = sum(1 for e in self._manager.entries if e.get('status') == 'applied')
        roblox_count = len(self._manager.roblox_dirs)
        noun = 'modification' if applied == 1 else 'modifications'
        self._status_label.setText(
            f'{applied} {noun} applied \u2022 '
            f'{roblox_count} Roblox dir(s) detected'
        )

    # ------------------------------------------------------------------
    # Section: Avatar Meshes — Add Head Variant
    # ------------------------------------------------------------------

    def _on_add_head_variant(self):
        # Filter out already-added variants
        existing = {r._target_path for r in self._row_widgets.values()}
        available = [
            v for v in HEAD_VARIANTS
            if rf'content\avatar\heads\{v}' not in existing
        ]
        if not available:
            QMessageBox.information(self, 'Head Variants', 'All head variants already added.')
            return

        item, ok = QInputDialog.getItem(
            self, 'Add Head Variant', 'Select variant:', available, 0, False,
        )
        if ok and item:
            target = rf'content\avatar\heads\{item}'
            name = item.replace('.mesh', '').title()
            row = ModRowWidget(self._manager, name, target,
                               file_filter=MESH_FILTER, deletable=True)
            row.delete_requested.connect(partial(self._on_row_deleted, row))
            # Insert before the "Add" button (last widget)
            self._head_variant_layout.insertWidget(
                self._head_variant_layout.count() - 1, row,
            )
            self._row_widgets[target] = row

    # ------------------------------------------------------------------
    # Section: Skybox — Apply to All
    # ------------------------------------------------------------------

    def _on_apply_all_sky(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select file for all sky faces', '', IMAGE_FILTER,
        )
        if not path:
            # Try asset ID instead
            text, ok = QInputDialog.getText(
                self, 'Asset ID for All Sky Faces',
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
            source_type = dlg.source_type
            source_value = dlg.source_value

            row = self._add_custom_row(name, target)
            if source_type and source_value:
                row.apply_source_external(source_type, source_value)

    def _add_custom_row(self, name: str, target_path: str) -> ModRowWidget:
        row = ModRowWidget(self._manager, name, target_path,
                           deletable=True)
        row.delete_requested.connect(partial(self._on_row_deleted, row))
        # Insert before the "Add" button (first widget in custom section)
        self._custom_content_layout.insertWidget(
            max(0, self._custom_content_layout.count() - 1), row,
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
        self._fflag_widget.setEnabled(checked)
        if checked:
            # Immediately write current settings
            self._fflag_widget._schedule_write()


# ═══════════════════════════════════════════════════════════════════
# Custom Modification Dialog
# ═══════════════════════════════════════════════════════════════════

class _CustomModDialog(QDialog):
    """Dialog for adding a custom modification entry."""

    def __init__(self, manager: ModificationManager, parent=None):
        super().__init__(parent)
        self._manager = manager
        self.display_name = ''
        self.target_path = ''
        self.source_type = ''
        self.source_value = ''

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
        self._target_edit = QLineEdit()
        self._target_edit.setPlaceholderText(r'content\sounds\oof.ogg')
        row2.addWidget(self._target_edit)
        self._browse_roblox_btn = QPushButton('Browse Roblox Dir\u2026')
        self._browse_roblox_btn.clicked.connect(self._browse_roblox)
        row2.addWidget(self._browse_roblox_btn)
        layout.addLayout(row2)

        # Source
        row3 = QHBoxLayout()
        row3.addWidget(QLabel('Source:'))
        self._source_edit = QLineEdit()
        self._source_edit.setPlaceholderText('Asset ID, rbxassetid://, or local file path (optional)')
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

    def _browse_roblox(self):
        """Open file dialog starting at the first Roblox directory."""
        start = ''
        if self._manager.roblox_dirs:
            start = str(self._manager.roblox_dirs[0])
        path, _ = QFileDialog.getOpenFileName(
            self, 'Select target file in Roblox directory', start,
        )
        if path and self._manager.roblox_dirs:
            # Make relative to the Roblox dir
            for rdir in self._manager.roblox_dirs:
                try:
                    rel = Path(path).relative_to(rdir)
                    self._target_edit.setText(str(rel))
                    return
                except ValueError:
                    continue
            self._target_edit.setText(path)

    def _browse_source(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Select source file')
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
        self.display_name = name
        self.target_path = target
        raw = self._source_edit.text().strip().strip('"\'') 
        if raw:
            if raw.isdigit():
                self.source_type = 'asset_id'
                self.source_value = raw
            elif raw.lower().startswith('rbxassetid://'):
                self.source_type = 'asset_id'
                self.source_value = raw[len('rbxassetid://'):]
            else:
                self.source_type = 'local_file'
                self.source_value = raw
        self.accept()
