"""Modifications tab — combined Fishstrap Mods + FastFlags panel."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, override

from PySide6.QtCore import (
    QAbstractItemModel,
    QEasingCurve,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPropertyAnimation,
    QRectF,
    QSignalBlocker,
    QSize,
    Qt,
    QTimer,
    Signal,
    SignalInstance,
)
from PySide6.QtGui import (
    QCloseEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPixmap,
    QWheelEvent,
)
from PySide6.QtWidgets import (
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
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QStylePainter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fleasion.cache.tools.ktx_to_png import convert as ktx_to_png, strip_prefixed_ktx
from fleasion.localization import tr, tr_count, translation_values
from fleasion.modifications.fflag_profiles import FastFlagProfileManager
from fleasion.modifications.manager import normalise_target_path, target_path_for_roblox_dir
from fleasion.modifications.platform_targets import (
    read_current_platform_original_asset,
    target_path_for_current_platform,
)
from fleasion.modifications.stash_paths import resource_stash_dir
from fleasion.utils import APP_CACHE_DIR, log_buffer, open_folder
from fleasion.utils.http import http_get
from fleasion.utils.json_types import JsonValue, require_json_value
from fleasion.utils.threading import run_in_thread

from .file_drop import FileDropLineEdit, local_file_path_example
from .theme import ThemeManager

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import ClassVar, TypeIs

    from fleasion.app import RobloxExitMonitor
    from fleasion.config.manager import ConfigManager
    from fleasion.gui.linux_hotkeys import LinuxCustomFFlagHotkeyController, LinuxHotkeyService
    from fleasion.gui.windows_hotkeys import (
        WindowsCustomFFlagHotkeyController,
        WindowsHotkeyService,
    )
    from fleasion.proxy.master import ProxyMaster


def _lazy_attr(module_name: str, attr_name: str) -> object:
    return getattr(import_module(module_name), attr_name)


class _ObjViewerLoadable(Protocol):
    def load_obj(self, obj_text: str) -> None: ...


class _NewModificationEntry(TypedDict, total=False):
    display_name: str
    target_path: str
    source_type: str | None
    source_value: str | None
    status: str
    error_message: str | None
    converted_cache_path: str | None
    _is_font: bool


class _ModificationEntry(_NewModificationEntry):
    id: str


class _FastFlagSettings(TypedDict, total=False):
    rendering_mode: str
    msaa: str
    disable_dpi_scale: bool
    alt_enter_fullscreen: bool
    texture_quality: str
    mesh_lod_enabled: bool
    mesh_lod: int
    frm_quality_enabled: bool
    frm_quality: int
    grey_sky: bool
    pause_voxelizer: bool
    grass_max: int | None
    grass_min: int | None
    grass_motion: int | None


class _FastFlagProfileManagerLike(Protocol):
    def list_profiles(self) -> list[str]: ...
    def save(self, name: str, flags: dict[str, object]) -> str: ...
    def load(self, name: str) -> dict[str, str]: ...
    def delete(self, name: str) -> None: ...
    def rename(self, old_name: str, new_name: str) -> str: ...


class _PendingModificationsQueueLike(Protocol):
    def enqueue_fast_flags(self, settings: _FastFlagSettings) -> None: ...
    def enqueue_framerate_cap(self, value: int) -> None: ...


class _ModificationManagerLike(Protocol):
    entry_status_changed: SignalInstance
    apply_finished: SignalInstance
    restore_finished: SignalInstance
    entries: list[_ModificationEntry]
    fast_flags: _FastFlagSettings
    fast_flags_enabled: bool
    framerate_cap: int
    pending_modifications_queue: _PendingModificationsQueueLike

    @property
    def roblox_dirs(self) -> list[Path]: ...

    def add_entry(self, entry: _NewModificationEntry) -> str: ...
    def update_entry(self, entry_id: str, **kwargs: str | None) -> bool: ...
    def remove_entry(self, entry_id: str) -> bool: ...
    def clear_entry(self, entry_id: str) -> bool: ...
    def restore_orphaned_stash(self, target_path: str) -> bool: ...
    def sync_saved_global_settings(self) -> None: ...
    def reset_framerate_cap(self) -> None: ...
    def write_fast_flags(self, settings: _FastFlagSettings) -> None: ...
    def apply_pending_modifications(self) -> None: ...


type _HotkeyBinding = dict[str, int | bool | str]
type _HotkeyBindings = dict[str, _HotkeyBinding]
type _HotkeyController = WindowsCustomFFlagHotkeyController | LinuxCustomFFlagHotkeyController
type _HotkeyService = WindowsHotkeyService | LinuxHotkeyService


_FFLAG_ROW_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20
_FFLAG_CANONICAL_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 21
_FFLAG_ROW_FLAG = 'flag'
_FFLAG_ROW_FOLDER = 'folder'


def _style_or_none(widget: QWidget) -> QStyle | None:
    return widget.style()


def _current_list_item(widget: QListWidget) -> QListWidgetItem | None:
    return widget.currentItem()


def _standard_button_or_none(
    button_box: QDialogButtonBox, button: QDialogButtonBox.StandardButton
) -> QPushButton | None:
    return button_box.button(button)


def _is_object_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _is_object_collection(
    value: object,
) -> TypeIs[list[object] | tuple[object, ...] | set[object]]:
    return isinstance(value, list | tuple | set)


def _is_hotkey_bindings(value: object) -> TypeIs[_HotkeyBindings]:
    return isinstance(value, dict)


def _linux_hotkey_service(service: _HotkeyService) -> LinuxHotkeyService:
    if TYPE_CHECKING:
        assert isinstance(service, LinuxHotkeyService)
    return service


def _required_config(config: ConfigManager | None) -> ConfigManager:
    if TYPE_CHECKING:
        assert config is not None
    return config


def _object_flags(flags: dict[str, str]) -> dict[str, object]:
    return cast('dict[str, object]', flags)


# Built-in entry definition
AVATAR_MESHES = [
    (
        'modifications.builtin.avatar.left_arm',
        target_path_for_current_platform(r'content\avatar\meshes\leftarm.mesh'),
    ),
    (
        'modifications.builtin.avatar.left_leg',
        target_path_for_current_platform(r'content\avatar\meshes\leftleg.mesh'),
    ),
    (
        'modifications.builtin.avatar.right_arm',
        target_path_for_current_platform(r'content\avatar\meshes\rightarm.mesh'),
    ),
    (
        'modifications.builtin.avatar.right_leg',
        target_path_for_current_platform(r'content\avatar\meshes\rightleg.mesh'),
    ),
    (
        'modifications.builtin.avatar.torso',
        target_path_for_current_platform(r'content\avatar\meshes\torso.mesh'),
    ),
    (
        'modifications.builtin.avatar.head',
        target_path_for_current_platform(r'content\avatar\heads\head.mesh'),
    ),
]

HEAD_VARIANTS = [f'head{chr(c)}.mesh' for c in range(ord('A'), ord('P') + 1)]

SKYBOX_FACES = [
    (
        'modifications.builtin.sky.back',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_bk.tex'),
    ),
    (
        'modifications.builtin.sky.down',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_dn.tex'),
    ),
    (
        'modifications.builtin.sky.front',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_ft.tex'),
    ),
    (
        'modifications.builtin.sky.left',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_lf.tex'),
    ),
    (
        'modifications.builtin.sky.right',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_rt.tex'),
    ),
    (
        'modifications.builtin.sky.up',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\sky512_up.tex'),
    ),
]

INDOOR_FACES = [
    (
        'modifications.builtin.indoor.back',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_bk.tex'),
    ),
    (
        'modifications.builtin.indoor.down',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_dn.tex'),
    ),
    (
        'modifications.builtin.indoor.front',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_ft.tex'),
    ),
    (
        'modifications.builtin.indoor.left',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_lf.tex'),
    ),
    (
        'modifications.builtin.indoor.right',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_rt.tex'),
    ),
    (
        'modifications.builtin.indoor.up',
        target_path_for_current_platform(r'PlatformContent\pc\textures\sky\indoor512_up.tex'),
    ),
]

SOUNDS = [
    (
        'modifications.builtin.sound.footsteps_plastic',
        target_path_for_current_platform(r'content\sounds\action_footsteps_plastic.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.falling',
        target_path_for_current_platform(r'content\sounds\action_falling.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'modifications.builtin.sound.get_up',
        target_path_for_current_platform(r'content\sounds\action_get_up.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.jump',
        target_path_for_current_platform(r'content\sounds\action_jump.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.jump_land',
        target_path_for_current_platform(r'content\sounds\action_jump_land.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.swim',
        target_path_for_current_platform(r'content\sounds\action_swim.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.explosion',
        target_path_for_current_platform(r'content\sounds\impact_explosion_03.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.water_impact',
        target_path_for_current_platform(r'content\sounds\impact_water.mp3'),
        'bundled:empty.mp3',
    ),
    (
        'modifications.builtin.sound.oof',
        target_path_for_current_platform(r'content\sounds\oof.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'modifications.builtin.sound.ouch',
        target_path_for_current_platform(r'content\sounds\ouch.ogg'),
        'bundled:empty.ogg',
    ),
    (
        'modifications.builtin.sound.volume_slider',
        target_path_for_current_platform(r'content\sounds\volume_slider.ogg'),
        'bundled:empty.ogg',
    ),
]

if sys.platform.startswith('linux'):
    SOUNDS[:] = [
        sound
        for sound in SOUNDS
        if sound[1].replace('\\', '/').strip('/') != 'content/sounds/ouch.ogg'
    ]

# File-type filter strings for QFileDialog
MESH_FILTER = 'modifications.filter.mesh_files'
IMAGE_FILTER = 'modifications.filter.image_files'
# DDS textures — Roblox accepts a .png renamed to .dds as a drop-in replacement
DDS_FILTER = 'modifications.filter.dds_image_files'
# JPG textures (moon/sun) — Roblox also accepts a .png renamed to .jpg
JPG_FILTER = 'modifications.filter.jpg_image_files'
SOUND_FILTER = 'modifications.filter.audio_files'
FONT_FILTER = 'modifications.filter.font_files'

TEXTURES = [
    # (display_name_id, target_path, file_filter_id)
    (
        'modifications.builtin.texture.hq_studs_diffuse',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\diffuse.dds'),
        DDS_FILTER,
    ),
    (
        'modifications.builtin.texture.hq_studs_normal',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\normal.dds'),
        DDS_FILTER,
    ),
    (
        'modifications.builtin.texture.hq_studs_detail',
        target_path_for_current_platform(r'PlatformContent\pc\textures\plastic\normaldetail.dds'),
        DDS_FILTER,
    ),
    (
        'modifications.builtin.texture.low_quality_studs',
        target_path_for_current_platform(r'PlatformContent\pc\textures\studs.dds'),
        DDS_FILTER,
    ),
    (
        'modifications.builtin.texture.shiftlock_cursor',
        target_path_for_current_platform(r'content\textures\MouseLockedCursor.png'),
        IMAGE_FILTER,
    ),
    (
        'modifications.builtin.texture.cursor_pointing',
        target_path_for_current_platform(r'content\textures\Cursors\KeyboardMouse\ArrowCursor.png'),
        IMAGE_FILTER,
    ),
    (
        'modifications.builtin.texture.cursor_arrow',
        target_path_for_current_platform(
            r'content\textures\Cursors\KeyboardMouse\ArrowFarCursor.png'
        ),
        IMAGE_FILTER,
    ),
    (
        'modifications.builtin.texture.cursor_ibeam',
        target_path_for_current_platform(r'content\textures\Cursors\KeyboardMouse\IBeamCursor.png'),
        IMAGE_FILTER,
    ),
    (
        'modifications.builtin.texture.moon',
        target_path_for_current_platform(r'content\sky\moon.jpg'),
        JPG_FILTER,
    ),
    (
        'modifications.builtin.texture.sun',
        target_path_for_current_platform(r'content\sky\sun.jpg'),
        JPG_FILTER,
    ),
]


def _builtin_label(identifier: str) -> str:
    labels = {
        'modifications.builtin.avatar.left_arm': tr('modifications.builtin.avatar.left_arm'),
        'modifications.builtin.avatar.left_leg': tr('modifications.builtin.avatar.left_leg'),
        'modifications.builtin.avatar.right_arm': tr('modifications.builtin.avatar.right_arm'),
        'modifications.builtin.avatar.right_leg': tr('modifications.builtin.avatar.right_leg'),
        'modifications.builtin.avatar.torso': tr('modifications.builtin.avatar.torso'),
        'modifications.builtin.avatar.head': tr('modifications.builtin.avatar.head'),
        'modifications.builtin.sky.back': tr('modifications.builtin.sky.back'),
        'modifications.builtin.sky.down': tr('modifications.builtin.sky.down'),
        'modifications.builtin.sky.front': tr('modifications.builtin.sky.front'),
        'modifications.builtin.sky.left': tr('modifications.builtin.sky.left'),
        'modifications.builtin.sky.right': tr('modifications.builtin.sky.right'),
        'modifications.builtin.sky.up': tr('modifications.builtin.sky.up'),
        'modifications.builtin.indoor.back': tr('modifications.builtin.indoor.back'),
        'modifications.builtin.indoor.down': tr('modifications.builtin.indoor.down'),
        'modifications.builtin.indoor.front': tr('modifications.builtin.indoor.front'),
        'modifications.builtin.indoor.left': tr('modifications.builtin.indoor.left'),
        'modifications.builtin.indoor.right': tr('modifications.builtin.indoor.right'),
        'modifications.builtin.indoor.up': tr('modifications.builtin.indoor.up'),
        'modifications.builtin.sound.footsteps_plastic': tr(
            'modifications.builtin.sound.footsteps_plastic'
        ),
        'modifications.builtin.sound.falling': tr('modifications.builtin.sound.falling'),
        'modifications.builtin.sound.get_up': tr('modifications.builtin.sound.get_up'),
        'modifications.builtin.sound.jump': tr('modifications.builtin.sound.jump'),
        'modifications.builtin.sound.jump_land': tr('modifications.builtin.sound.jump_land'),
        'modifications.builtin.sound.swim': tr('modifications.builtin.sound.swim'),
        'modifications.builtin.sound.explosion': tr('modifications.builtin.sound.explosion'),
        'modifications.builtin.sound.water_impact': tr('modifications.builtin.sound.water_impact'),
        'modifications.builtin.sound.oof': tr('modifications.builtin.sound.oof'),
        'modifications.builtin.sound.ouch': tr('modifications.builtin.sound.ouch'),
        'modifications.builtin.sound.volume_slider': tr(
            'modifications.builtin.sound.volume_slider'
        ),
        'modifications.builtin.texture.hq_studs_diffuse': tr(
            'modifications.builtin.texture.hq_studs_diffuse'
        ),
        'modifications.builtin.texture.hq_studs_normal': tr(
            'modifications.builtin.texture.hq_studs_normal'
        ),
        'modifications.builtin.texture.hq_studs_detail': tr(
            'modifications.builtin.texture.hq_studs_detail'
        ),
        'modifications.builtin.texture.low_quality_studs': tr(
            'modifications.builtin.texture.low_quality_studs'
        ),
        'modifications.builtin.texture.shiftlock_cursor': tr(
            'modifications.builtin.texture.shiftlock_cursor'
        ),
        'modifications.builtin.texture.cursor_pointing': tr(
            'modifications.builtin.texture.cursor_pointing'
        ),
        'modifications.builtin.texture.cursor_arrow': tr(
            'modifications.builtin.texture.cursor_arrow'
        ),
        'modifications.builtin.texture.cursor_ibeam': tr(
            'modifications.builtin.texture.cursor_ibeam'
        ),
        'modifications.builtin.texture.moon': tr('modifications.builtin.texture.moon'),
        'modifications.builtin.texture.sun': tr('modifications.builtin.texture.sun'),
    }
    return labels[identifier]


def _file_filter_text(identifier: str) -> str:
    filters = {
        'modifications.filter.all_files': tr('modifications.filter.all_files'),
        'modifications.filter.mesh_files': tr('modifications.filter.mesh_files'),
        'modifications.filter.image_files': tr('modifications.filter.image_files'),
        'modifications.filter.dds_image_files': tr('modifications.filter.dds_image_files'),
        'modifications.filter.jpg_image_files': tr('modifications.filter.jpg_image_files'),
        'modifications.filter.audio_files': tr('modifications.filter.audio_files'),
        'modifications.filter.font_files': tr('modifications.filter.font_files'),
    }
    return filters[identifier]


def _ensure_text_width(widget: QWidget, minimum_width: int = 0) -> None:
    """Fit translated text without allowing layouts to inflate the control."""
    widget.setFixedWidth(max(minimum_width, widget.sizeHint().width()))


def _ensure_placeholder_width(line_edit: QLineEdit, minimum_width: int = 0) -> None:
    """Give translated line-edit placeholders enough room to be displayed."""
    placeholder_width = line_edit.fontMetrics().horizontalAdvance(line_edit.placeholderText()) + 12
    line_edit.setMinimumWidth(max(minimum_width, placeholder_width))


# Status badge styling
_STATUS_STYLES = {
    'not_set': 'color: #888; font-style: italic;',
    'pending': 'color: #4a9eda;',
    'applied': 'font-style: normal;',
    'orphaned_stash': 'color: #c90; font-weight: bold;',
}


# _RichTextButton — QPushButton-like label that renders HTML/rich text


class _RichTextButton(QPushButton):
    """QPushButton that draws a label and a larger suffix character, each independently
    vertically centred so mixed font sizes don't shift each other's position."""

    def __init__(
        self,
        label: str,
        *,
        suffix: str = '',
        suffix_size_offset: int = 0,
        y_offset: int = 0,
        suffix_x_offset: int = 0,
        suffix_pixel_size: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._label = label
        self._suffix = suffix
        self._suffix_size_offset = suffix_size_offset
        self._y_offset = y_offset
        self._suffix_x_offset = suffix_x_offset
        self._suffix_pixel_size = suffix_pixel_size
        # Give Qt the real base-font text so its native sizeHint includes only
        # the style's actual button padding/minimum width, not a dummy glyph.
        super().setText(self._label + (f' {self._suffix}' if self._suffix else ''))

    @override
    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        if not self._suffix:
            return hint

        base_font = self.font()
        base_metrics = QFontMetrics(base_font)
        base_suffix_width = base_metrics.horizontalAdvance(self._suffix)

        suffix_font = QFont(base_font)
        if self._suffix_pixel_size:
            suffix_font.setPixelSize(self._suffix_pixel_size)
        elif self._suffix_size_offset:
            point_size = suffix_font.pointSize()
            if point_size < 0:
                point_size = 9
            suffix_font.setPointSize(point_size + self._suffix_size_offset)

        painted_suffix_width = QFontMetrics(suffix_font).horizontalAdvance(self._suffix)
        extra_width = max(0, painted_suffix_width - base_suffix_width + self._suffix_x_offset)
        hint.setWidth(hint.width() + extra_width)
        return hint

    @override
    def paintEvent(self, a0: QPaintEvent) -> None:
        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        opt.text = ''
        painter = QPainter(self)
        st = _style_or_none(self)
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


# CollapsibleSection


class CollapsibleSection(QWidget):
    """A section with a clickable header that collapses/expands its content."""

    _EXPANDED_ARROW = '\u25bc'
    _COLLAPSED_ARROW = '\u25b6'
    _DEFAULT_ARROW_STYLE = 'border: none;'
    _WINDOWS_COLLAPSED_ARROW_SIZE = 19
    WINDOWS_COLLAPSED_ARROW_SIZE = _WINDOWS_COLLAPSED_ARROW_SIZE
    _WINDOWS_EXPANDED_ARROW_STYLE = 'font-size: 11px; border: none;'
    _WINDOWS_COLLAPSED_ARROW_STYLE = f'font-size: {_WINDOWS_COLLAPSED_ARROW_SIZE}px; border: none;'

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        expanded: bool = True,
        header_widgets: list[QWidget] | None = None,
    ) -> None:
        super().__init__(parent)

        self._expanded = expanded
        self._animation: QPropertyAnimation | None = None

        # Header row
        # Keep the header in its own fixed-height widget.  During a collapse,
        # the content's maximumHeight changes before the parent layout has
        # necessarily applied the section's new sizeHint.  A bare QHBoxLayout
        # can absorb that transient spare height and visibly push the header
        # separator downward for a paint frame.  A fixed-height wrapper keeps
        # the header geometry stable while the content viewport clips closed.
        self._header_widget = QWidget()
        self._header_widget.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        header_layout = QHBoxLayout(self._header_widget)
        header_layout.setContentsMargins(4, 4, 4, 4)

        self._arrow = QPushButton()
        self._arrow.setFixedSize(22, 22)
        self._arrow.setFlat(True)
        self._set_arrow_state(expanded)
        self._arrow.clicked.connect(self.toggle)
        header_layout.addWidget(self._arrow)

        self._title_label = QLabel(tr('ui.gui.modifications_tab.b_value_b', value0=title))
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
        main.addWidget(self._header_widget)
        main.addWidget(sep)
        main.addWidget(self._content)
        # Absorb any transient excess section height below the content.  This
        # is important during collapse because the child maximumHeight can be
        # updated one event-loop turn before the parent geometry catches up.
        main.addStretch()
        self.setLayout(main)

    @override
    def paintEvent(self, a0: QPaintEvent) -> None:
        """Draw a rounded-rect card that adapts to dark and light themes."""
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

    def add_widget(self, widget: QWidget) -> None:
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

    def _set_arrow_state(self, expanded: bool) -> None:
        self._arrow.setText(self._EXPANDED_ARROW if expanded else self._COLLAPSED_ARROW)
        self._arrow.setStyleSheet(self._arrow_style(expanded))

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._set_arrow_state(self._expanded)

        # A collapse animates the clipping height of _content.  If its layout
        # remains active, Qt re-lays out (and squashes) every child on every
        # animation frame; complex sections such as Fast Flags then visibly
        # bounce/reflow instead of simply sliding closed.  Freeze the existing
        # child geometries while collapsing so _content behaves as a viewport.
        # Re-enable the layout once the content is fully clipped away.
        self._content_layout.setEnabled(True)

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
            self._content_layout.setEnabled(False)
            self._animation.setStartValue(actual)
            self._animation.setEndValue(0)
            self._animation.finished.connect(self._finish_collapse)

        self._animation.start()

    def _finish_collapse(self) -> None:
        self._content_layout.setEnabled(True)
        self._content_layout.activate()


# NoWheelSpinBox — QSpinBox that ignores mouse wheel events


class NoWheelSpinBox(QSpinBox):
    """QSpinBox that ignores wheel events to prevent accidental value changes."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()


class NoWheelSlider(QSlider):
    """QSlider that ignores wheel events to prevent accidental value changes."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()


# DropdownComboBox — QComboBox with ▼ indicator instead of OS arrow


class DropdownComboBox(QComboBox):
    """QComboBox that paints ▼ as the dropdown indicator and ignores wheel events."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        """Ignore wheel events to prevent accidental value changes."""
        e.ignore()

    @override
    def paintEvent(self, e: QPaintEvent) -> None:
        style = _style_or_none(self)
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

        # Replace the native arrow without painting a separate button-colored block.
        # Redraw an adjacent slice of the edit field over the arrow interior so the
        # background remains exactly continuous for the active Qt style/theme.
        arrow_rect = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            opt,
            QStyle.SubControl.SC_ComboBoxArrow,
            self,
        )
        painter.save()
        painter.setClipRect(arrow_rect.adjusted(0, 1, -1, -1))
        background_opt = QStyleOptionComboBox(opt)
        background_opt.rect = opt.rect.translated(arrow_rect.width(), 0)
        painter.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, background_opt)
        painter.restore()

        painter.setPen(self.palette().buttonText().color())
        f = painter.font()
        f.setPointSize(8)
        painter.setFont(f)
        painter.drawText(arrow_rect, Qt.AlignmentFlag.AlignCenter, '\u25bc')


class CompactBooleanComboBox(QComboBox):
    """A borderless True/False selector that blends into a table cell."""

    @override
    def wheelEvent(self, e: QWheelEvent) -> None:
        e.ignore()

    @override
    def paintEvent(self, e: QPaintEvent) -> None:
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

    @override
    def createEditor(
        self,
        parent: QWidget,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> QWidget:
        name_index = index.sibling(index.row(), 0)
        name = str(
            name_index.data(_FFLAG_CANONICAL_NAME_ROLE)
            or name_index.data(Qt.ItemDataRole.DisplayRole)
            or ''
        ).strip()
        if name.startswith(self._BOOLEAN_FLAG_PREFIXES):
            editor = CompactBooleanComboBox(parent)
            editor.addItem(tr('ui.gui.modifications_tab.true'), 'True')
            editor.addItem(tr('ui.gui.modifications_tab.false'), 'False')
            editor.activated.connect(partial(self._commit_and_close_boolean_editor, editor))
            return editor
        return super().createEditor(parent, option, index)

    def _commit_and_close_boolean_editor(self, editor: QComboBox, _selected_index: int) -> None:
        """Finish the table edit as soon as a boolean is picked from its popup."""
        self.commitData.emit(editor)
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    @override
    def setEditorData(self, editor: QWidget, index: QModelIndex | QPersistentModelIndex) -> None:
        if isinstance(editor, QComboBox):
            stored = index.data(Qt.ItemDataRole.UserRole)
            if stored is None:
                stored = index.data(Qt.ItemDataRole.DisplayRole)
            value = 'True' if str(stored or '').strip().lower() == 'true' else 'False'
            editor.setCurrentIndex(max(0, editor.findData(value)))
            # A table double-click creates the combo editor but does not pass
            # the click on to the combo itself.  Defer opening the popup until
            # the editor has been installed and shown by the view, otherwise
            # the first double-click only produces the collapsed editor.
            QTimer.singleShot(0, editor.showPopup)
            return
        super().setEditorData(editor, index)

    @override
    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        if isinstance(editor, QComboBox):
            value = str(editor.currentData())
            display = (
                tr('ui.gui.modifications_tab.true')
                if value == 'True'
                else tr('ui.gui.modifications_tab.false')
            )
            model.setData(index, display, Qt.ItemDataRole.DisplayRole)
            model.setData(index, value, Qt.ItemDataRole.UserRole)
            return
        super().setModelData(editor, model, index)


# ModRowWidget — the reusable row for each modifiable file


class ModRowWidget(QWidget):
    """A single row representing one modification entry."""

    delete_requested = Signal(str)  # entry_id

    def __init__(
        self,
        manager: _ModificationManagerLike,
        display_name: str,
        target_path: str,
        *,
        file_filter: str = 'modifications.filter.all_files',
        deletable: bool = False,
        mute_bundled: str | None = None,
        is_font: bool = False,
        parent: QWidget | None = None,
    ) -> None:
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

    def _setup_ui(self) -> None:
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        # Display name
        self._name_label = QLabel(self._display_name)
        _ensure_text_width(self._name_label, 160)
        layout.addWidget(self._name_label)

        # Status badge — keep the compact English baseline but allow longer translations.
        self._status_label = QLabel(tr('ui.gui.modifications_tab.not_set'))
        self._status_label.setStyleSheet(_STATUS_STYLES['not_set'])
        _ensure_text_width(self._status_label, 72)
        layout.addWidget(self._status_label)

        # Source text field (expands to fill remaining row space)
        self._source_edit = FileDropLineEdit()
        self._source_edit.setPlaceholderText(
            tr(
                'ui.gui.modifications_tab.id_url_path_value_or_remove',
                value0=local_file_path_example(),
                value1=tr('replacer.action.remove').casefold(),
            )
        )
        self._source_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _ensure_placeholder_width(self._source_edit)
        layout.addWidget(self._source_edit)

        # Debounce timer: apply 1 s after the user stops typing
        self._debounce = QTimer()
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(1000)
        self._debounce.timeout.connect(self._apply_from_text)
        self._source_edit.textChanged.connect(self._restart_debounce)
        self._source_edit.editingFinished.connect(self._on_editing_finished)

        # Pending-visibility timer: show 'Applying...' only if apply takes > 500 ms
        self._pending_timer = QTimer()
        self._pending_timer.setSingleShot(True)
        self._pending_timer.setInterval(500)
        self._pending_timer.timeout.connect(lambda: self._update_status('pending'))

        # Reset button
        self._reset_btn = _RichTextButton('\u21ba', y_offset=-1)
        self._reset_btn.setToolTip(tr('ui.gui.modifications_tab.reset_to_original'))
        self._reset_btn.setFixedWidth(28)
        self._reset_btn.setVisible(False)
        self._reset_btn.clicked.connect(self._on_reset)
        layout.addWidget(self._reset_btn)

        # Browse button — to the right of reset (collapses next to textbox when reset hidden)
        self._browse_btn = QPushButton(tr('ui.gui.modifications_tab.browse'))
        _ensure_text_width(self._browse_btn, 65)
        self._browse_btn.clicked.connect(self._on_browse)
        layout.addWidget(self._browse_btn)

        # Preview button
        preview_arrow_size = (
            CollapsibleSection.WINDOWS_COLLAPSED_ARROW_SIZE if os.name == 'nt' else None
        )
        self._preview_btn = _RichTextButton(
            tr('modifications.preview'),
            suffix='\u25b6',
            suffix_x_offset=3,
            suffix_pixel_size=preview_arrow_size,
        )
        _ensure_text_width(self._preview_btn, 82)
        self._preview_btn.clicked.connect(self._on_preview)
        layout.addWidget(self._preview_btn)

        # Delete button (custom rows only)
        if self._deletable:
            self._del_btn = _RichTextButton('\u2715', y_offset=-1)
            self._del_btn.setFixedWidth(28)
            self._del_btn.setToolTip(tr('ui.gui.modifications_tab.remove_modification'))
            self._del_btn.clicked.connect(self._on_delete)
            layout.addWidget(self._del_btn)

        self.setLayout(layout)

    # Sync with manager

    def _sync_from_manager(self) -> None:
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

    def _check_for_orphaned_stash(self) -> None:
        """Show a warning if a stash file exists but Fleasion has no active record."""
        mod_originals_dir = cast(
            'Path', _lazy_attr('fleasion.modifications.manager', 'MOD_ORIGINALS_DIR')
        )

        roblox_dirs = self._manager.roblox_dirs
        if not roblox_dirs:
            return
        try:
            target_path = target_path_for_roblox_dir(self._target_path, roblox_dirs[0])
        except ValueError:
            return
        stash = resource_stash_dir(mod_originals_dir, roblox_dirs[0]) / target_path
        if stash.is_file():
            self._update_status('orphaned_stash')
            self._status_label.setToolTip(
                tr('ui.gui.modifications_tab.a_stash_of_the_original_file_was')
            )

    def _on_status_changed(self, entry_id: str, status: str, error_msg: str) -> None:
        if entry_id == self._entry_id:
            self._update_status(status, error_msg)

    def _update_status(self, status: str, error_msg: str | None = '') -> None:
        # Final status: stop the pending-visibility timer
        if status != 'pending':
            self._pending_timer.stop()

        # 'error' shows same label/style as 'not_set'; red textbox is the indicator
        display_status = 'not_set' if status == 'error' else status

        labels = {
            'not_set': tr('modifications.status.not_set'),
            'pending': tr('modifications.status.applying'),
            'applied': tr('modifications.status.applied'),
            'orphaned_stash': tr('modifications.status.external_modified'),
        }
        self._status_label.setText(labels.get(display_status, display_status))
        self._status_label.setStyleSheet(_STATUS_STYLES.get(display_status, ''))
        _ensure_text_width(self._status_label, 72)

        if status == 'error':
            self._show_source_error(error_msg or tr('modifications.error.failed_to_apply'))
        elif status in {'applied', 'not_set'}:
            self._clear_source_error()

        if status != 'orphaned_stash':
            self._status_label.setToolTip('')

        self._reset_btn.setVisible(status in {'applied', 'error', 'orphaned_stash'})

    # Actions

    def _apply_source(self, source_type: str, source_value: str) -> None:
        entry_data: _NewModificationEntry = {
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

    def _on_edit(self) -> None:
        # Kept as a no-op stub — inline textbox replaced the Edit dialog
        pass

    def _on_reset(self) -> None:
        self._debounce.stop()
        if self._entry_id:
            if not self._manager.clear_entry(self._entry_id):
                return
            # clear_entry deletes the entry from JSON; drop our reference
            # so _apply_source correctly calls add_entry next time.
            self._entry_id = None
        # Orphaned stash with no JSON entry at all — restore directly
        elif not self._manager.restore_orphaned_stash(self._target_path):
            return
        self._set_source_text_silent('')
        self._update_status('not_set')

    def _on_delete(self) -> None:
        if self._entry_id and not self._manager.remove_entry(self._entry_id):
            return
        self.delete_requested.emit(self._entry_id or '')

    def _on_preview(self) -> None:
        dlg = ModPreviewDialog(
            self._manager,
            self._target_path,
            self._display_name,
            self,
        )
        dlg.exec()

    # External helpers

    @property
    def target_path(self) -> str:
        return self._target_path

    def apply_raw_source(self, raw_source: str) -> None:
        self._set_source_text_silent(raw_source)
        self._apply_from_text()

    def apply_source_external(self, source_type: str, source_value: str) -> None:
        """Called externally (e.g. by 'Apply to All Sky Faces')."""
        self._apply_source(source_type, source_value)
        display = source_value if source_type in {'local_file', 'asset_id', 'bundled'} else ''
        self._set_source_text_silent(display)

    # Inline source editing

    def _get_source_display_text(self) -> str:
        """Return the textbox display string for the current entry's source."""
        for entry in self._manager.entries:
            if entry.get('target_path') == self._target_path:
                src_type = entry.get('source_type')
                src_val = entry.get('source_value') or ''
                if src_type == 'bundled':
                    # Reverse-map any remove-class bundled value back to the active language.
                    if src_val == self._resolve_bundled_empty() or src_val == 'bundled:zero':
                        return tr('replacer.action.remove').casefold()
                    return src_val
                if src_type in {'local_file', 'asset_id', 'cdn_url'}:
                    return src_val
                return ''
        return ''

    def _set_source_text_silent(self, text: str) -> None:
        """Set textbox text without triggering the apply debounce."""
        self._debounce.stop()
        with QSignalBlocker(self._source_edit):
            self._source_edit.setText(text)
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
    _BUNDLED_EMPTY_BY_EXT: ClassVar[dict[str, str]] = {
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
        lowered = text.lower()
        # Any registered translation of the remove action is accepted, so newly
        # added languages work automatically without changing this validator.
        remove_tokens = {
            value.strip().casefold() for value in translation_values('replacer.action.remove')
        }
        if text.casefold() in remove_tokens or lowered == 'bundled:empty':
            source = ('bundled', self._resolve_bundled_empty())
        elif text.isdigit():
            source = ('asset_id', text)
        elif lowered.startswith('rbxassetid://'):
            source = ('asset_id', text[len('rbxassetid://') :])
        elif lowered.startswith('bundled:'):
            source = ('bundled', text)
        elif lowered.startswith(('http://', 'https://')):
            source = ('cdn_url', text)
        else:
            source = ('local_file', text)
        return source

    def _apply_from_text(self) -> None:
        """Apply (or clear) the modification from the current textbox value."""
        self._debounce.stop()
        text = self._source_edit.text().strip().strip('"\'')

        if not text:
            self._clear_source_error()
            # Empty box = user wants to clear the modification
            self._on_reset()
            return

        src_type, src_value = self._detect_source_from_text(text)

        if src_type == 'local_file' and not Path(src_value).is_file():
            # Show red border but still apply — the manager will fail and
            # the status indicator will show 'error', matching asset-ID behaviour
            self._show_source_error(tr('modifications.error.file_not_found', path=src_value))
        else:
            self._clear_source_error()

        self._apply_source(src_type, src_value)

    def _restart_debounce(self, _text: str) -> None:
        self._debounce.start()

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
            tr('ui.gui.modifications_tab.select_replacement_file'),
            initial_dir,
            _file_filter_text(self._file_filter),
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


# ModPreviewDialog


class ModPreviewDialog(QDialog):
    """Preview dialog showing Modification vs Original side-by-side tabs."""

    def __init__(
        self,
        manager: _ModificationManagerLike,
        target_path: str,
        display_name: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._target_path = target_path
        self._mod_converted_bytes: bytes | None = None
        self._mod_converted_ext: str = ''
        self._orig_unavailable: bool = False
        self.setWindowTitle(tr('ui.gui.modifications_tab.preview_value', value0=display_name))
        self.resize(500, 400)

        layout = QVBoxLayout()
        tabs = QTabWidget()

        # Modification tab — build first so _mod_converted_bytes is populated
        mod_widget = self._build_preview_widget('mod')
        tabs.addTab(mod_widget, tr('ui.gui.modifications_tab.modification'))

        # Original tab
        orig_widget = self._build_preview_widget('original')
        tabs.addTab(orig_widget, tr('ui.gui.modifications_tab.original'))

        layout.addWidget(tabs)

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        export_conv_btn = QPushButton(tr('ui.gui.modifications_tab.export_converted'))
        export_conv_btn.setEnabled(self._mod_converted_bytes is not None)
        export_conv_btn.clicked.connect(self._on_export_converted)
        btn_row.addWidget(export_conv_btn)
        export_btn = QPushButton(tr('ui.gui.modifications_tab.export_original'))
        export_btn.setEnabled(not self._orig_unavailable)
        export_btn.clicked.connect(self._on_export)
        btn_row.addWidget(export_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _build_mesh_preview(self, data: bytes, mode: str) -> QWidget:
        convert_mesh = cast(
            'Callable[[bytes], str | None]',
            _lazy_attr('fleasion.cache.mesh_processing', 'convert'),
        )
        obj_text = convert_mesh(data)
        if not obj_text:
            return QLabel(tr('ui.gui.modifications_tab.could_not_convert_mesh_for_preview'))
        if mode == 'mod':
            self._mod_converted_bytes = obj_text.encode()
            self._mod_converted_ext = '.obj'
        viewer_type = cast(
            'Callable[[], QWidget]',
            _lazy_attr('fleasion.cache.obj_viewer', 'ObjViewerPanel'),
        )
        viewer = viewer_type()
        cast('_ObjViewerLoadable', viewer).load_obj(obj_text)
        return viewer

    def _build_audio_preview(self, data: bytes) -> QWidget:
        suffix = Path(self._target_path).suffix
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        player_type = cast(
            'Callable[[str], QWidget]',
            _lazy_attr('fleasion.cache.audio_player', 'AudioPlayerWidget'),
        )
        return player_type(tmp_path)

    def _build_font_preview(self, data: bytes) -> QWidget:
        decoded = data.decode('utf-8', errors='replace')
        try:
            parsed = json.loads(decoded)
        except ValueError:
            font_viewer_type = cast(
                'Callable[[bytes], QWidget]',
                _lazy_attr('fleasion.cache.font_viewer', 'FontViewerWidget'),
            )
            return font_viewer_type(data)
        viewer = QTextEdit()
        viewer.setReadOnly(True)
        viewer.setPlainText(json.dumps(parsed, indent=2))
        return viewer

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
                    tr('ui.gui.modifications_tab.preview_of_roblox_original_fonts_are_not')
                )
                lbl.setWordWrap(True)
                layout.addWidget(lbl)
                self._orig_unavailable = True
            else:
                layout.addWidget(QLabel(tr('ui.gui.modifications_tab.no_data_available')))
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
                    layout.addWidget(
                        QLabel(tr('ui.gui.modifications_tab.could_not_decode_ktx_texture_file'))
                    )
                    container.setLayout(layout)
                    return container
            elif lower.endswith(('.tex', '.dds')):
                # The replacement may be a plain image (PNG/JPEG) even though
                # the target path ends in .tex/.dds — detect by magic bytes first.
                is_raw_image = (
                    data[:8] == b'\x89PNG\r\n\x1a\n'  # PNG
                    or data[:2] == b'\xff\xd8'  # JPEG
                    or data[:6] in {b'GIF87a', b'GIF89a'}
                )
                if not is_raw_image:
                    tex_to_png_bytes = cast(
                        'Callable[[bytes], bytes | None]',
                        _lazy_attr('fleasion.modifications.dds_to_png', 'tex_to_png_bytes'),
                    )
                    converted = tex_to_png_bytes(data)
                    if converted:
                        display_bytes = converted
                        if mode == 'mod':
                            self._mod_converted_bytes = converted
                            self._mod_converted_ext = '.png'
                    else:
                        layout.addWidget(
                            QLabel(tr('ui.gui.modifications_tab.could_not_decode_tex_dds_file'))
                        )
                        container.setLayout(layout)
                        return container

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
                label.setText(tr('ui.gui.modifications_tab.could_not_render_image'))
            layout.addWidget(label)

        # Mesh
        elif lower.endswith('.mesh'):
            try:
                layout.addWidget(self._build_mesh_preview(data, mode))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError, IndexError) as exc:
                layout.addWidget(
                    QLabel(tr('ui.gui.modifications_tab.mesh_preview_error_value', value0=exc))
                )

        # Audio
        elif lower.endswith(('.mp3', '.ogg', '.wav')):
            try:
                layout.addWidget(self._build_audio_preview(data))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                layout.addWidget(
                    QLabel(tr('ui.gui.modifications_tab.audio_preview_error_value', value0=exc))
                )

        # Fonts
        elif lower.endswith(('.ttf', '.otf', '.ttc')):
            try:
                layout.addWidget(self._build_font_preview(data))
            except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
                layout.addWidget(
                    QLabel(tr('ui.gui.modifications_tab.font_json_preview_error_value', value0=exc))
                )

        else:
            layout.addWidget(
                QLabel(tr('ui.gui.modifications_tab.no_preview_available_for_this_file_type'))
            )

        container.setLayout(layout)
        return container

    def _load_data(self, mode: str) -> bytes | None:
        """Load file bytes for preview. mode='mod' or 'original'."""
        mod_originals_dir = cast(
            'Path', _lazy_attr('fleasion.modifications.manager', 'MOD_ORIGINALS_DIR')
        )
        if not self._manager.roblox_dirs:
            return None
        roblox_dir = self._manager.roblox_dirs[0]
        try:
            target_path = target_path_for_roblox_dir(self._target_path, roblox_dir)
        except ValueError:
            return None

        destination = roblox_dir / target_path
        if mode != 'original':
            return (
                destination.read_bytes()
                if destination.is_file()
                else read_current_platform_original_asset(self._target_path, roblox_dir)
            )

        stash = resource_stash_dir(mod_originals_dir, roblox_dir) / target_path
        if stash.is_file():
            result = stash.read_bytes()
        else:
            result = read_current_platform_original_asset(self._target_path, roblox_dir)
            if result is None:
                mod_active = any(
                    entry.get('target_path') == self._target_path for entry in self._manager.entries
                )
                if not mod_active and destination.is_file():
                    result = destination.read_bytes()
        return result

    def _on_export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.modifications_tab.export_original_file'),
            Path(self._target_path).name,
        )
        if path:
            data = self._load_data('original')
            if data:
                export_path = Path(path)
                export_path.write_bytes(data)
                self._show_export_complete_message(
                    tr('modifications.export.complete_title'),
                    tr('modifications.export.file_exported_to', path=export_path),
                    [export_path],
                )

    def _on_export_converted(self) -> None:
        if not self._mod_converted_bytes:
            return
        stem = Path(self._target_path).stem
        default_name = stem + self._mod_converted_ext
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.modifications_tab.export_converted_file'),
            default_name,
        )
        if path:
            export_path = Path(path)
            export_path.write_bytes(self._mod_converted_bytes)
            self._show_export_complete_message(
                tr('modifications.export.complete_title'),
                tr('modifications.export.file_exported_to', path=export_path),
                [export_path],
            )

    @staticmethod
    def _open_export_location(paths: list[Path]) -> None:
        if len(paths) == 1 and paths[0].is_file():
            explorer = (
                Path(os.environ.get('WINDIR', r'C:\Windows')) / 'explorer.exe'
                if sys.platform == 'win32'
                else Path('explorer.exe')
            )
            subprocess.Popen(
                [str(explorer), '/select,', str(paths[0].resolve())],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        elif paths:
            target = paths[0] if paths[0].is_dir() else paths[0].parent
            open_folder(target)

    def _show_export_complete_message(
        self, title: str, message: str, exported_paths: list[Path]
    ) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setWindowTitle(title)
        msg.setText(message)
        open_button = msg.addButton(
            tr('ui.gui.modifications_tab.open_in_explorer'), QMessageBox.ButtonRole.ActionRole
        )
        msg.addButton(QMessageBox.StandardButton.Ok)
        msg.exec()

        if msg.clickedButton() == open_button:
            try:
                self._open_export_location(exported_paths)
            except (OSError, ValueError) as exc:
                log_buffer.log('Export', f'Could not open exported file location: {exc}')


# Fast Flags section widgets


class CustomFFlagWarningDialog(QDialog):
    """One-time, intentionally slow confirmation for bannable custom flags."""

    CONFIRM_DELAY_SECONDS = 15

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.modifications_tab.are_you_sure'))
        self.setModal(True)
        self.setMinimumWidth(520)
        self._seconds_remaining = self.CONFIRM_DELAY_SECONDS

        layout = QVBoxLayout(self)
        title = QLabel(tr('ui.gui.modifications_tab.b_custom_fastflags_can_get_your_roblox'))
        title.setStyleSheet('color: #d9534f; font-size: 15px;')
        layout.addWidget(title)

        message = QLabel(tr('ui.gui.modifications_tab.roblox_only_permits_a_small_allowlist_of'))
        message.setWordWrap(True)
        layout.addWidget(message)

        self._buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._confirm_button = self._buttons.addButton('', QDialogButtonBox.ButtonRole.AcceptRole)
        self._confirm_button.setEnabled(False)
        self._buttons.accepted.connect(self.accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._update_confirm_text()
        self._timer.start()

    def _update_confirm_text(self) -> None:
        if self._seconds_remaining > 0:
            self._confirm_button.setText(
                tr(
                    'ui.gui.modifications_tab.i_accept_the_risk_value_s',
                    value0=self._seconds_remaining,
                )
            )
        else:
            self._confirm_button.setText(
                tr('ui.gui.modifications_tab.i_accept_the_risk_enable_custom_fastflags')
            )

    def _tick(self) -> None:
        self._seconds_remaining = max(0, self._seconds_remaining - 1)
        self._update_confirm_text()
        if self._seconds_remaining == 0:
            self._timer.stop()
            self._confirm_button.setEnabled(True)


class FastFlagProfilesDialog(QDialog):
    """Manage named, on-disk FastFlag profiles without hiding the current editor."""

    def __init__(self, flags: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.modifications_tab.custom_fastflag_profiles'))
        self.setMinimumWidth(560)
        self._flags = dict(flags)
        self._profiles: _FastFlagProfileManagerLike = FastFlagProfileManager()
        self.loaded_flags: dict[str, str] | None = None
        self._setup_ui()
        self._refresh_profiles()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        description = QLabel(tr('ui.gui.modifications_tab.save_the_current_custom_fastflags_as_a'))
        description.setWordWrap(True)
        layout.addWidget(description)

        save_row = QHBoxLayout()
        self._name = QLineEdit()
        self._name.setPlaceholderText(tr('ui.gui.modifications_tab.profile_name'))
        save_row.addWidget(self._name)
        save_button = QPushButton(tr('ui.gui.modifications_tab.save_current'))
        save_button.clicked.connect(self._save_profile)
        save_row.addWidget(save_button)
        layout.addLayout(save_row)

        self._profile_list = QListWidget()
        self._profile_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._profile_list.itemDoubleClicked.connect(self._on_profile_double_clicked)
        layout.addWidget(self._profile_list)

        self._replace_flags = QCheckBox(
            tr('ui.gui.modifications_tab.replace_current_flags_when_loading')
        )
        self._replace_flags.setChecked(True)
        self._replace_flags.setToolTip(
            tr('ui.gui.modifications_tab.turn_this_off_to_merge_the_profile')
        )
        layout.addWidget(self._replace_flags)

        actions = QHBoxLayout()
        self._load_button = QPushButton(tr('ui.gui.modifications_tab.load'))
        self._load_button.clicked.connect(self._load_profile)
        actions.addWidget(self._load_button)
        self._update_button = QPushButton(tr('ui.gui.modifications_tab.update_from_current'))
        self._update_button.clicked.connect(self._update_profile)
        actions.addWidget(self._update_button)
        self._copy_button = QPushButton(tr('ui.gui.modifications_tab.copy_json'))
        self._copy_button.clicked.connect(self._copy_profile)
        actions.addWidget(self._copy_button)
        self._rename_button = QPushButton(tr('ui.gui.modifications_tab.rename'))
        self._rename_button.clicked.connect(self._rename_profile)
        actions.addWidget(self._rename_button)
        self._delete_button = QPushButton(tr('ui.gui.modifications_tab.delete'))
        self._delete_button.clicked.connect(self._delete_profile)
        actions.addWidget(self._delete_button)
        layout.addLayout(actions)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._set_actions_enabled(enabled=False)

    def _on_profile_double_clicked(self, _item: QListWidgetItem) -> None:
        self._load_profile()

    def _selected_name(self) -> str | None:
        item = _current_list_item(self._profile_list)
        return item.text() if item is not None else None

    def _set_actions_enabled(self, enabled: bool) -> None:
        for button in (
            self._load_button,
            self._update_button,
            self._copy_button,
            self._rename_button,
            self._delete_button,
        ):
            button.setEnabled(enabled)

    def _on_selection_changed(self) -> None:
        name = self._selected_name()
        self._set_actions_enabled(name is not None)
        if name:
            self._name.setText(name)

    def _refresh_profiles(self, selected: str | None = None) -> None:
        selected = selected or self._selected_name()
        self._profile_list.clear()
        for name in self._profiles.list_profiles():
            self._profile_list.addItem(name)
        if selected:
            matches = self._profile_list.findItems(selected, Qt.MatchFlag.MatchExactly)
            if matches:
                self._profile_list.setCurrentItem(matches[0])
        self._set_actions_enabled(self._selected_name() is not None)

    def _show_error(self, action: str, exc: Exception) -> None:
        QMessageBox.warning(
            self,
            tr('ui.gui.modifications_tab.could_not_value_profile', value0=action),
            str(exc),
        )

    def _save_profile(self) -> None:
        try:
            name = self._profiles.save(self._name.text(), _object_flags(self._flags))
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_save'), exc)
            return
        self._refresh_profiles(name)

    def _load_profile(self) -> None:
        name = self._selected_name()
        if not name:
            return
        try:
            flags = self._profiles.load(name)
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_load'), exc)
            return
        self.loaded_flags = flags if self._replace_flags.isChecked() else {**self._flags, **flags}
        self.accept()

    def _update_profile(self) -> None:
        name = self._selected_name()
        if not name:
            return
        try:
            self._profiles.save(name, _object_flags(self._flags))
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_update'), exc)

    def _copy_profile(self) -> None:
        name = self._selected_name()
        if not name:
            return
        try:
            QApplication.clipboard().setText(json.dumps(self._profiles.load(name), indent=2))
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_copy'), exc)

    def _rename_profile(self) -> None:
        old_name = self._selected_name()
        if not old_name:
            return
        new_name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.modifications_tab.rename_fastflag_profile'),
            tr('ui.gui.modifications_tab.new_name'),
            text=old_name,
        )
        if not ok:
            return
        try:
            name = self._profiles.rename(old_name, new_name)
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_rename'), exc)
            return
        self._refresh_profiles(name)

    def _delete_profile(self) -> None:
        name = self._selected_name()
        if not name:
            return
        if (
            QMessageBox.question(
                self,
                tr('ui.gui.modifications_tab.delete_fastflag_profile'),
                tr('ui.gui.modifications_tab.delete_value', value0=name),
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._profiles.delete(name)
        except (OSError, ValueError) as exc:
            self._show_error(tr('ui.gui.modifications_tab.profile_action_delete'), exc)
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
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/roblox/FVariables.txt'
    )
    _HISTORICAL_TRACKER_VARIABLES_URL = (
        'https://raw.githubusercontent.com/MaximumADHD/Roblox-Client-Tracker/'
        '03a46e5f35e7aa5d85310189b477caee20b20761/FVariables.txt'
    )
    _BYPASS_CUSTOM_FFLAGS_HEADER: ClassVar[dict[str, str]] = {
        'X-Fleasion-Bypass-Custom-FFlags': '1'
    }
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

    flags_loaded = Signal(object)
    load_failed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.modifications_tab.browse_roblox_fastflags'))
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

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        description = QLabel(
            tr(
                'ui.gui.modifications_tab.browse_fastflags_found_across_roblox_clientsettings_releases'
            )
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        controls = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(
            tr('ui.gui.modifications_tab.search_fastflag_names_or_current_values')
        )
        self._search.textChanged.connect(self._filter_rows)
        controls.addWidget(self._search, 1)

        self._family_filter = QComboBox()
        self._family_filter.setMinimumWidth(165)
        self._family_filter.currentIndexChanged.connect(self._filter_rows)
        controls.addWidget(self._family_filter)

        self._refresh_button = QPushButton(tr('ui.gui.modifications_tab.refresh'))
        self._refresh_button.clicked.connect(lambda: self._refresh(force=True))
        controls.addWidget(self._refresh_button)
        layout.addLayout(controls)

        self._count = QLabel(tr('ui.gui.modifications_tab.retrieving_fastflags'))
        self._count.setStyleSheet('color: #999;')
        layout.addWidget(self._count)

        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(
            [
                tr('ui.gui.modifications_tab.name'),
                tr('ui.gui.modifications_tab.current_roblox_value'),
            ]
        )
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.itemSelectionChanged.connect(self._update_selection_button)
        layout.addWidget(self._table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self._add_button = buttons.addButton(
            tr('ui.gui.modifications_tab.add_selected'), QDialogButtonBox.ButtonRole.AcceptRole
        )
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
    def _display_value(value: str | None) -> str:
        """Keep the canonical unpublished sentinel as None and translate only presentation."""
        return value if value is not None else tr('modifications.fastflags.no_value')

    @staticmethod
    def _extract_flags(payload: JsonValue) -> dict[str, str]:
        """Validate the public ClientSettings response without accepting arbitrary JSON."""
        if type(payload) is not dict:
            msg = 'Roblox returned an invalid FastFlag response.'
            raise ValueError(msg)
        settings = payload.get('applicationSettings')
        if type(settings) is not dict:
            msg = 'Roblox returned no application FastFlags.'
            raise ValueError(msg)

        flags: dict[str, str] = {}
        for raw_name, raw_value in settings.items():
            name = str(raw_name).strip()
            if not name or not isinstance(raw_value, str | int | float | bool):
                continue
            flags[name] = (
                'True' if raw_value is True else 'False' if raw_value is False else str(raw_value)
            )
        if not flags:
            msg = 'Roblox returned no usable FastFlags.'
            raise ValueError(msg)
        return flags

    @classmethod
    def _extract_tracker_flags(cls, payload: bytes) -> dict[str, None]:
        """Extract known FastVariable names from the public Client Tracker list."""
        try:
            lines = payload.decode('utf-8').splitlines()
        except UnicodeDecodeError as exc:
            msg = 'The FastVariable tracker returned invalid text.'
            raise ValueError(msg) from exc

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
            msg = 'The FastVariable tracker returned no usable FastFlags.'
            raise ValueError(msg)
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
            cached = require_json_value(json.loads(cls._CACHE_PATH.read_text(encoding='utf-8')))
        except OSError, ValueError, json.JSONDecodeError:
            return None
        if not isinstance(cached, dict) or cached.get('version') != cls._CACHE_VERSION:
            return None
        raw_fetched_at = cached.get('fetched_at')
        if not isinstance(raw_fetched_at, str | int | float):
            return None
        try:
            fetched_at = float(raw_fetched_at)
        except TypeError, ValueError, OverflowError:
            return None
        age = (time.time() if now is None else now) - fetched_at
        raw_flags = cached.get('flags')
        if not 0 <= age < cls._CACHE_TTL_SECONDS or not isinstance(raw_flags, dict):
            return None

        flags: dict[str, str | None] = {}
        for raw_name, value in raw_flags.items():
            name = raw_name.strip()
            if name.startswith(cls._FAMILIES) and (value is None or isinstance(value, str)):
                flags[name] = value
        return flags or None

    @classmethod
    def _write_cache(cls, flags: dict[str, str | None], *, now: float | None = None) -> None:
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
            with contextlib.suppress(OSError):
                temporary_path.unlink(missing_ok=True)

    def _refresh(self, force: bool = False) -> None:
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
        self._count.setText(tr('ui.gui.modifications_tab.retrieving_fastflags'))
        self._count.setStyleSheet('color: #999;')
        threading.Thread(target=self._fetch_flags, daemon=True).start()

    def _collect_flags(self) -> dict[str, str | None]:
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
                except OSError, TypeError, ValueError, json.JSONDecodeError:
                    # Some platform/channel combinations are intentionally unavailable.
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
            except OSError, ValueError:
                # A tracker snapshot is optional; published values remain useful.
                continue
            flags.update(
                {name: value for name, value in tracker_flags.items() if name not in flags}
            )
        if not flags:
            msg = 'No configured FastFlag source returned usable data.'
            raise ValueError(msg)
        return flags

    def _fetch_flags(self) -> None:
        try:
            flags = self._collect_flags()
            self._write_cache(flags)
            self.flags_loaded.emit(flags)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self.load_failed.emit(tr('modifications.fastflags.load_failed', error=exc))

    def _apply_flags(self, flags: dict[str, str | None]) -> None:
        self._flags = dict(sorted(flags.items(), key=lambda item: item[0].casefold()))
        current_family = self._family_filter.currentData()
        family_counts: dict[str, int] = {}
        for name in self._flags:
            family = self._family_for(name)
            family_counts[family] = family_counts.get(family, 0) + 1

        with QSignalBlocker(self._family_filter):
            self._family_filter.clear()
            self._family_filter.addItem(tr('ui.gui.modifications_tab.all_fastflags'), '')
            for family in sorted(family_counts):
                self._family_filter.addItem(
                    tr(
                        'ui.gui.modifications_tab.value_value',
                        value0=family,
                        value1=family_counts[family],
                    ),
                    family,
                )
            previous_index = self._family_filter.findData(current_family)
            self._family_filter.setCurrentIndex(max(0, previous_index))
        self._refresh_button.setEnabled(True)
        self._populate_table()
        self._filter_rows()

    def _show_load_error(self, message: str) -> None:
        self._refresh_button.setEnabled(True)
        self._count.setText(message)
        self._count.setStyleSheet('color: #ef8f8f;')

    def _filter_rows(self, *_args: object) -> None:
        query = self._search.text().strip().casefold()
        family = self._family_filter.currentData() or ''
        visible_count = 0
        self._table.setUpdatesEnabled(False)
        try:
            for row, (name, value) in enumerate(self._flags.items()):
                display_value = self._display_value(value)
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
                tr(
                    'modifications.fastflags.no_current_pc_values',
                    count=no_pc_value_count,
                )
                if no_pc_value_count
                else tr('modifications.fastflags.retrieved_from_roblox')
            )
            cache_detail = tr('modifications.fastflags.cached_suffix') if self._cache_loaded else ''
            self._count.setText(
                tr(
                    'ui.gui.modifications_tab.showing_value_fastflags_value_value_value',
                    value0=visible_count,
                    value1=len(self._flags),
                    value2=source_detail,
                    value3=cache_detail,
                )
            )
            self._count.setStyleSheet('color: #999;')
        self._update_selection_button()

    def _populate_table(self) -> None:
        """Populate once per download; search and filters only hide existing rows."""
        self._table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(self._table)
        try:
            self._table.setRowCount(len(self._flags))
            for row, (name, value) in enumerate(self._flags.items()):
                name_item = QTableWidgetItem(name)
                name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                name_item.setToolTip(name)
                self._table.setItem(row, 0, name_item)
                value_item = QTableWidgetItem(self._display_value(value))
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, 1, value_item)
        finally:
            del blocker
            self._table.setUpdatesEnabled(True)

    def _selected_names(self) -> list[str]:
        names: list[str] = []
        for row in sorted({index.row() for index in self._table.selectedIndexes()}):
            item = self._table.item(row, 0)
            if item is not None:
                names.append(item.text())
        return names

    def _update_selection_button(self) -> None:
        selected_count = len(self._selected_names())
        self._add_button.setText(
            tr('modifications.add_selected_count', count=selected_count)
            if selected_count
            else tr('ui.gui.modifications_tab.add_selected')
        )
        self._add_button.setEnabled(selected_count > 0)

    def _add_selected(self) -> None:
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

    _MODIFIER_KEYS: ClassVar[set[int]] = {
        int(Qt.Key.Key_Control),
        int(Qt.Key.Key_Shift),
        int(Qt.Key.Key_Alt),
        int(Qt.Key.Key_Meta),
        int(Qt.Key.Key_AltGr),
    }

    def __init__(self, flag_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.binding: _HotkeyBinding | None = None
        self.clear_requested = False
        self._pending_modifier: _HotkeyBinding | None = None
        self._pending_modifier_key: int | None = None
        self._suppress_mouse_capture = False
        self.setWindowTitle(tr('ui.gui.modifications_tab.set_fastflag_keybind'))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        label = QLabel(
            tr('ui.gui.modifications_tab.press_the_global_keybind_for_b_value', value0=flag_name)
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self._preview = QLabel(tr('ui.gui.modifications_tab.waiting_for_a_key_combination'))
        self._preview.setStyleSheet('color: #999; padding: 10px 0;')
        layout.addWidget(self._preview)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        clear_button = buttons.addButton(
            tr('ui.gui.modifications_tab.clear_keybind'),
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        clear_button.clicked.connect(self._clear)
        buttons.rejected.connect(self.reject)
        clear_button.installEventFilter(self)
        cancel_button = _standard_button_or_none(buttons, QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.installEventFilter(self)
        layout.addWidget(buttons)

    @staticmethod
    def _enum_value(value: Qt.KeyboardModifier) -> int:
        """PySide6 flag enums expose `.value`; they are not reliably int-convertible."""
        return value.value

    @classmethod
    def _modifier_mask(cls, modifiers: Qt.KeyboardModifier) -> int:
        mod_alt = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_ALT'))
        mod_ctrl = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_CTRL'))
        mod_shift = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_SHIFT'))
        mod_win = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_WIN'))

        qt_modifiers = cls._enum_value(modifiers)
        result = 0
        if qt_modifiers & 0x02000000:
            result |= mod_shift
        if qt_modifiers & 0x04000000:
            result |= mod_ctrl
        if qt_modifiers & 0x08000000:
            result |= mod_alt
        if qt_modifiers & 0x10000000:
            result |= mod_win
        return result

    @staticmethod
    def _event_binding(event: QKeyEvent, modifiers: int) -> _HotkeyBinding | None:
        modifier_mask_for_virtual_key = cast(
            'Callable[[int], int]',
            _lazy_attr('fleasion.gui.windows_hotkeys', 'modifier_mask_for_virtual_key'),
        )

        scan_code = int(event.nativeScanCode())
        virtual_key = int(event.nativeVirtualKey())
        if not 0 < scan_code <= 0xFF:
            return None
        extended = virtual_key in {
            0xA3,
            0xA5,
            0x2D,
            0x2E,
            0x24,
            0x23,
            0x21,
            0x22,
            0x25,
            0x26,
            0x27,
            0x28,
            0x5B,
            0x5C,
        }
        return {
            'scan_code': scan_code,
            'extended': extended,
            'modifiers': modifiers & ~modifier_mask_for_virtual_key(virtual_key),
        }

    def _clear(self) -> None:
        self.clear_requested = True
        self.accept()

    @staticmethod
    def _modifier_preview(modifiers: int) -> str:
        mod_alt = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_ALT'))
        mod_ctrl = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_CTRL'))
        mod_shift = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_SHIFT'))
        mod_win = cast('int', _lazy_attr('fleasion.gui.windows_hotkeys', 'MOD_WIN'))

        labels = [
            label
            for flag, label in (
                (mod_win, tr('modifications.hotkey.modifier.win')),
                (mod_ctrl, tr('modifications.hotkey.modifier.ctrl')),
                (mod_alt, tr('modifications.hotkey.modifier.alt')),
                (mod_shift, tr('modifications.hotkey.modifier.shift')),
            )
            if modifiers & flag
        ]
        return (
            '+'.join([*labels, '…'])
            if labels
            else tr('modifications.hotkey.waiting_for_combination')
        )

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = int(event.key())
        if event.isAutoRepeat():
            return
        modifiers = self._modifier_mask(event.modifiers())
        binding = self._event_binding(event, modifiers)
        if binding is None:
            self._preview.setText(tr('ui.gui.modifications_tab.that_key_could_not_be_read_as'))
            return
        if key in self._MODIFIER_KEYS:
            modifier_mask_for_virtual_key = cast(
                'Callable[[int], int]',
                _lazy_attr('fleasion.gui.windows_hotkeys', 'modifier_mask_for_virtual_key'),
            )

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

    @override
    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if (
            event.isAutoRepeat()
            or self._pending_modifier is None
            or int(event.key()) != self._pending_modifier_key
        ):
            return
        self.binding = self._pending_modifier
        self.accept()

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._suppress_mouse_capture:
            return super().mousePressEvent(event)
        button_map = {
            Qt.MouseButton.LeftButton: 1,
            Qt.MouseButton.RightButton: 2,
            Qt.MouseButton.MiddleButton: 4,
            Qt.MouseButton.BackButton: 5,
            Qt.MouseButton.ForwardButton: 6,
        }
        if virtual_key := button_map.get(event.button()):
            self.binding = {
                'platform': 'windows',
                'kind': 'mouse_button',
                'scan_code': virtual_key,
                'extended': False,
                'modifiers': self._modifier_mask(event.modifiers()),
            }
            self.accept()
            return None
        return super().mousePressEvent(event)

    @override
    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta:
            self.binding = {
                'platform': 'windows',
                'kind': 'mouse_wheel',
                'direction': 'up' if delta > 0 else 'down',
                'modifiers': self._modifier_mask(event.modifiers()),
            }
            self.accept()
            return
        super().wheelEvent(event)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._suppress_mouse_capture = True
        return super().eventFilter(watched, event)


class LinuxHotkeyCaptureDialog(QDialog):
    """Capture a physical evdev key from Fleasion's passive Linux reader."""

    def __init__(
        self,
        flag_name: str,
        hotkey_service: LinuxHotkeyService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.binding: _HotkeyBinding | None = None
        self.clear_requested = False
        self._service = hotkey_service
        self._pending_modifier: int | None = None
        self._suppress_mouse_capture = False
        self.setWindowTitle(tr('ui.gui.modifications_tab.set_fastflag_keybind'))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        label = QLabel(
            tr('ui.gui.modifications_tab.press_the_global_keybind_for_b_value_2', value0=flag_name)
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        self._preview = QLabel(tr('ui.gui.modifications_tab.waiting_for_a_key_combination'))
        self._preview.setStyleSheet('color: #999; padding: 10px 0;')
        layout.addWidget(self._preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        clear_button = buttons.addButton(
            tr('ui.gui.modifications_tab.clear_keybind'),
            QDialogButtonBox.ButtonRole.DestructiveRole,
        )
        clear_button.clicked.connect(self._clear)
        buttons.rejected.connect(self.reject)
        clear_button.installEventFilter(self)
        cancel_button = _standard_button_or_none(buttons, QDialogButtonBox.StandardButton.Cancel)
        if cancel_button is not None:
            cancel_button.installEventFilter(self)
        layout.addWidget(buttons)
        self._service.key_pressed.connect(self._key_pressed)
        self._service.key_released.connect(self._key_released)
        self._service.wheel_scrolled.connect(self._wheel_scrolled)

    def _clear(self) -> None:
        self.clear_requested = True
        self.accept()

    def _key_pressed(self, code: int, modifiers: int) -> None:
        modifier_mask_for_evdev_code = cast(
            'Callable[[int], int]',
            _lazy_attr('fleasion.gui.linux_hotkeys', 'modifier_mask_for_evdev_code'),
        )

        # Raw evdev sees the dialog controls too. Only Clear and Cancel arm
        # this suppression; every other mouse button remains bindable. Delay
        # button capture a moment so Qt has time to deliver a Clear/Cancel
        # click first; if it closes this dialog, the queued capture is ignored.
        if code >= 0x100:
            QTimer.singleShot(25, partial(self._capture_mouse_button, code, modifiers))
            return
        own_modifier = modifier_mask_for_evdev_code(code)
        binding = {
            'platform': 'linux_evdev',
            'scan_code': code,
            'modifiers': modifiers & ~own_modifier,
        }
        if own_modifier:
            self._pending_modifier = code
            self._preview.setText(
                tr('ui.gui.modifications_tab.modifier_captured_release_it_to_bind_it')
            )
            return
        self.binding = binding
        self.accept()

    def _capture_mouse_button(self, code: int, modifiers: int) -> None:
        if self._suppress_mouse_capture or not self.isVisible():
            return
        self.binding = {
            'platform': 'linux_evdev',
            'kind': 'mouse_button',
            'scan_code': code,
            'modifiers': modifiers,
        }
        self.accept()

    def _wheel_scrolled(self, code: int, modifiers: int) -> None:
        self.binding = {
            'platform': 'linux_evdev',
            'kind': 'mouse_wheel',
            'direction': 'up' if code == 256 else 'down',
            'modifiers': modifiers,
        }
        self.accept()

    def _key_released(self, code: int) -> None:
        if code != self._pending_modifier:
            return
        self.binding = {'platform': 'linux_evdev', 'scan_code': code, 'modifiers': 0}
        self.accept()

    @override
    def done(self, result: int) -> None:
        try:
            self._service.key_pressed.disconnect(self._key_pressed)
            self._service.key_released.disconnect(self._key_released)
            self._service.wheel_scrolled.disconnect(self._wheel_scrolled)
        except TypeError:
            pass
        super().done(result)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._suppress_mouse_capture = True
        return super().eventFilter(watched, event)


class CustomFFlagEditor(QWidget):
    """Fishstrap-style name/value editor backed by Fleasion's proxy settings."""

    _BOOLEAN_FLAG_PREFIXES = ('FFlag', 'DFFlag')

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        proxy_master: ProxyMaster | None = None,
        parent: QWidget | None = None,
        hotkey_controller: _HotkeyController | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._windows_keybinds = sys.platform == 'win32'
        self._linux_keybinds = sys.platform.startswith('linux')
        self._hotkeys_supported = self._windows_keybinds or self._linux_keybinds
        self._hotkey_service: _HotkeyService | None = None
        self._linux_hotkey_service: LinuxHotkeyService | None = None
        self._hotkey_controller: _HotkeyController | None = None
        self._owns_hotkey_controller = False
        if self._windows_keybinds:
            controller_type = cast(
                'type[WindowsCustomFFlagHotkeyController]',
                _lazy_attr('fleasion.gui.windows_hotkeys', 'WindowsCustomFFlagHotkeyController'),
            )

            self._hotkey_controller = hotkey_controller
            if self._hotkey_controller is None:
                self._hotkey_controller = controller_type(config_manager, proxy_master, self)
                self._owns_hotkey_controller = True
            self._hotkey_service = self._hotkey_controller.service
            self._hotkey_controller.toggled.connect(self._on_hotkey_toggled)
        elif self._linux_keybinds:
            controller_type = cast(
                'type[LinuxCustomFFlagHotkeyController]',
                _lazy_attr('fleasion.gui.linux_hotkeys', 'LinuxCustomFFlagHotkeyController'),
            )

            self._hotkey_controller = hotkey_controller
            if self._hotkey_controller is None:
                self._hotkey_controller = controller_type(config_manager, proxy_master, self)
                self._owns_hotkey_controller = True
            self._hotkey_service = self._hotkey_controller.service
            self._linux_hotkey_service = _linux_hotkey_service(self._hotkey_controller.service)
            self._hotkey_controller.toggled.connect(self._on_hotkey_toggled)
        self._loading = False
        self._sort_column: int | None = 0
        self._sort_ascending = True
        self._setup_ui()
        self._load_flags()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(7)

        heading = QLabel(tr('ui.gui.modifications_tab.b_custom_fastflags_live_editing_b'))
        layout.addWidget(heading)

        warning = QLabel(
            tr('ui.gui.modifications_tab.non_roblox_allowed_fastflags_are_bannable_use')
        )
        warning.setWordWrap(True)
        warning.setStyleSheet(
            'color: #ef8f8f; background: rgba(180, 45, 45, 0.16); '
            'border: 1px solid rgba(210, 70, 70, 0.55); padding: 7px;'
        )
        layout.addWidget(warning)

        if sys.platform.startswith('linux'):
            sober_delay_warning = QLabel(
                tr('ui.gui.modifications_tab.linux_sober_limitation_due_to_sober_security')
            )
            sober_delay_warning.setWordWrap(True)
            sober_delay_warning.setStyleSheet(
                'color: #ffcc66; background: rgba(190, 145, 30, 0.15); '
                'border: 1px solid rgba(220, 175, 55, 0.55); padding: 7px;'
            )
            layout.addWidget(sober_delay_warning)

        self._enable_toggle = QCheckBox(tr('ui.gui.modifications_tab.enable_custom_fastflags'))
        self._enable_toggle.setChecked(
            bool(self._config and getattr(self._config, 'custom_fflags_enabled', False))
        )
        self._enable_toggle.toggled.connect(self._on_enabled_toggled)
        self._enable_toggle.setEnabled(self._config is not None and self._proxy_master is not None)
        layout.addWidget(self._enable_toggle)

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        if self._hotkeys_supported:
            hotkey_help = QLabel(
                tr('ui.gui.modifications_tab.double_click_a_keybind_cell_to_assign')
                if self._windows_keybinds
                else tr('ui.gui.modifications_tab.double_click_a_keybind_cell_to_assign_2')
            )
            hotkey_help.setWordWrap(True)
            hotkey_help.setStyleSheet('color: #999;')
            layout.addWidget(hotkey_help)

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr('ui.gui.modifications_tab.search_custom_fastflags'))
        self._search.textChanged.connect(self._filter_rows)
        layout.addWidget(self._search)

        column_count = 4 if self._hotkeys_supported else 2
        self._table = QTableWidget(0, column_count)
        base_headers = [
            tr('modifications.table.name'),
            tr('modifications.table.value'),
        ]
        if self._hotkeys_supported:
            base_headers.extend(
                [
                    tr('modifications.table.status'),
                    tr('modifications.table.keybind'),
                ]
            )
        self._table.setHorizontalHeaderLabels(base_headers)
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
        self._table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.setMinimumHeight(180)
        self._table.setItemDelegateForColumn(1, FastFlagValueDelegate(self._table))
        self._table.clicked.connect(self._edit_value_cell)
        self._table.cellChanged.connect(self._on_cell_changed)
        if self._hotkeys_supported:
            self._table.cellDoubleClicked.connect(self._edit_keybind)
        if self._hotkeys_supported:
            self._table.setColumnWidth(2, 85)
            self._table.setColumnWidth(3, 160)
        layout.addWidget(self._table)

        buttons = QHBoxLayout()
        browse_button = QPushButton(tr('ui.gui.modifications_tab.browse_fastflags'))
        browse_button.setToolTip(
            tr('ui.gui.modifications_tab.browse_and_add_fastflags_currently_published_by')
        )
        browse_button.clicked.connect(self._browse_fflags)
        buttons.addWidget(browse_button)

        add_button = QPushButton(tr('ui.gui.modifications_tab.add_new'))
        add_button.clicked.connect(self._add_flag)
        buttons.addWidget(add_button)

        folder_button = QPushButton(tr('ui.gui.modifications_tab.fastflag_folders'))
        folder_menu = QMenu(folder_button)
        folder_menu.addAction(
            tr('ui.gui.modifications_tab.create_fastflag_folder_from_selected'),
            self._create_folder_from_selected,
        )
        folder_menu.addAction(
            tr('ui.gui.modifications_tab.move_selected_fastflags_to_folder'),
            self._move_selected_to_folder,
        )
        folder_menu.addAction(
            tr('ui.gui.modifications_tab.remove_selected_fastflags_from_folder'),
            self._remove_selected_from_folders,
        )
        folder_button.setMenu(folder_menu)
        buttons.addWidget(folder_button)

        import_button = QPushButton(tr('ui.gui.modifications_tab.import_json'))
        import_menu = QMenu(import_button)
        import_menu.addAction(tr('ui.gui.modifications_tab.from_text'), self._import_json)
        import_menu.addAction(tr('ui.gui.modifications_tab.from_file'), self._import_file)
        import_button.setMenu(import_menu)
        buttons.addWidget(import_button)

        export_button = QPushButton(tr('ui.gui.modifications_tab.export_json'))
        export_menu = QMenu(export_button)
        export_menu.addAction(tr('ui.gui.modifications_tab.copy_to_clipboard'), self._copy_json)
        export_menu.addAction(tr('ui.gui.modifications_tab.export_as_file'), self._export_json)
        export_button.setMenu(export_menu)
        buttons.addWidget(export_button)

        profiles_button = QPushButton(tr('ui.gui.modifications_tab.profiles'))
        profiles_button.clicked.connect(self._show_profiles)
        buttons.addWidget(profiles_button)

        delete_button = QPushButton(tr('ui.gui.modifications_tab.delete_selected'))
        delete_button.clicked.connect(self._delete_selected)
        buttons.addWidget(delete_button)
        buttons.addStretch()
        layout.addLayout(buttons)

        if self._config is None or self._proxy_master is None:
            self._status.setText(
                tr('ui.gui.modifications_tab.the_fleasion_proxy_must_be_available_to')
            )
        else:
            self._update_status()

    def _load_flags(self, sync_hotkeys: bool = True) -> None:
        flags = dict(getattr(self._config, 'custom_fflags', {}) or {}) if self._config else {}
        self._replace_table_rows(sorted(flags.items(), key=lambda item: item[0].lower()))
        self._filter_rows(self._search.text())
        self._update_status()
        if sync_hotkeys:
            self._sync_hotkeys()

    def _replace_table_rows(self, rows: list[tuple[str, str]]) -> None:
        """Bulk-load flag and folder rows without persistent cell widgets."""
        self._loading = True
        self._table.setUpdatesEnabled(False)
        blocker = QSignalBlocker(self._table)
        try:
            values = dict(rows)
            order = {name: index for index, (name, _value) in enumerate(rows)}
            folders = self._folders()
            grouped_names = {
                name for members in folders.values() for name in members if name in values
            }
            rendered: list[tuple[str, str, str | None]] = []
            for folder_name in sorted(folders, key=str.casefold):
                members = [name for name in folders[folder_name] if name in values]
                rendered.append((_FFLAG_ROW_FOLDER, folder_name, None))
                rendered.extend(
                    (_FFLAG_ROW_FLAG, name, values[name])
                    for name in sorted(members, key=lambda name: order.get(name, 0))
                )
            rendered.extend(
                (_FFLAG_ROW_FLAG, name, value) for name, value in rows if name not in grouped_names
            )

            self._table.setRowCount(len(rendered))
            disabled_flags = self._disabled_flag_names()
            disabled_folders = self._disabled_folder_names()
            flag_bindings = self._keybinds()
            folder_bindings = self._folder_keybinds()
            for row, (kind, name, value) in enumerate(rendered):
                display_name = (
                    name
                    if kind == _FFLAG_ROW_FOLDER
                    else f'    {name}'
                    if name in grouped_names
                    else name
                )
                name_item = QTableWidgetItem(display_name)
                name_item.setData(_FFLAG_ROW_KIND_ROLE, kind)
                name_item.setData(_FFLAG_CANONICAL_NAME_ROLE, name)
                name_item.setToolTip(name)
                if kind == _FFLAG_ROW_FOLDER:
                    name_item.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon))
                    font = name_item.font()
                    font.setBold(True)
                    name_item.setFont(font)
                    name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._table.setItem(row, 0, name_item)

                if kind == _FFLAG_ROW_FOLDER:
                    member_count = sum(1 for member in folders.get(name, []) if member in values)
                    value_item = QTableWidgetItem(
                        tr_count(
                            member_count,
                            'ui.gui.modifications_tab.fastflag_folder_count_one',
                            'ui.gui.modifications_tab.fastflag_folder_count_other',
                        )
                    )
                    value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    self._table.setItem(row, 1, value_item)
                else:
                    self._set_value_editor(row, name, str(value or ''))

                if self._hotkeys_supported:
                    status_item = QTableWidgetItem(tr('ui.gui.modifications_tab.enabled'))
                    status_item.setFlags(
                        (status_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        & ~Qt.ItemFlag.ItemIsEditable
                    )
                    disabled = name in (
                        disabled_folders if kind == _FFLAG_ROW_FOLDER else disabled_flags
                    )
                    status_item.setCheckState(
                        Qt.CheckState.Unchecked if disabled else Qt.CheckState.Checked
                    )
                    self._table.setItem(row, 2, status_item)
                    bindings = folder_bindings if kind == _FFLAG_ROW_FOLDER else flag_bindings
                    keybind_item = QTableWidgetItem(self._keybind_text(bindings.get(name)))
                    keybind_item.setFlags(keybind_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    keybind_item.setToolTip(
                        tr('ui.gui.modifications_tab.double_click_to_assign_or_clear_this')
                    )
                    self._table.setItem(row, 3, keybind_item)
        finally:
            del blocker
            self._table.setUpdatesEnabled(True)
            self._loading = False

    def _row_kind(self, row: int) -> str:
        item = self._table.item(row, 0)
        value = item.data(_FFLAG_ROW_KIND_ROLE) if item is not None else None
        return value if value in {_FFLAG_ROW_FLAG, _FFLAG_ROW_FOLDER} else _FFLAG_ROW_FLAG

    def _row_name(self, row: int) -> str:
        item = self._table.item(row, 0)
        if item is None:
            return ''
        canonical = item.data(_FFLAG_CANONICAL_NAME_ROLE)
        return str(canonical).strip() if canonical is not None else item.text().strip()

    def _flags_from_table(self) -> dict[str, str]:
        flags: dict[str, str] = {}
        for row in range(self._table.rowCount()):
            if self._row_kind(row) != _FFLAG_ROW_FLAG:
                continue
            name = self._row_name(row)
            if name:
                flags[name] = self._value_from_row(row)
        return flags

    @classmethod
    def _is_boolean_flag(cls, name: str) -> bool:
        return name.startswith(cls._BOOLEAN_FLAG_PREFIXES)

    def _value_from_row(self, row: int) -> str:
        value_item = self._table.item(row, 1)
        if value_item is None:
            return ''
        stored = value_item.data(Qt.ItemDataRole.UserRole)
        return str(stored) if stored is not None else value_item.text()

    def _set_value_editor(self, row: int, name: str, value: str) -> None:
        """Store a lightweight value item; the boolean selector is created only when editing."""
        was_loading = self._loading
        self._loading = True
        try:
            value_item = self._table.item(row, 1)
            normalized_value = (
                'True'
                if str(value).strip().lower() == 'true'
                else 'False'
                if self._is_boolean_flag(name)
                else str(value)
            )
            display_value = (
                tr('ui.gui.modifications_tab.true')
                if normalized_value == 'True' and self._is_boolean_flag(name)
                else tr('ui.gui.modifications_tab.false')
                if normalized_value == 'False' and self._is_boolean_flag(name)
                else normalized_value
            )
            if value_item is None:
                value_item = QTableWidgetItem(display_value)
                self._table.setItem(row, 1, value_item)
            else:
                value_item.setText(display_value)
            value_item.setData(Qt.ItemDataRole.UserRole, normalized_value)
        finally:
            self._loading = was_loading

    def _disabled_flag_names(self) -> set[str]:
        return set(getattr(self._config, 'custom_fflag_disabled', []) or [])

    def _folders(self) -> dict[str, list[str]]:
        raw = cast('object', getattr(self._config, 'custom_fflag_folders', {}))
        if not _is_object_dict(raw):
            return {}
        folders: dict[str, list[str]] = {}
        for raw_folder, raw_names in raw.items():
            if not isinstance(raw_folder, str) or not _is_object_collection(raw_names):
                continue
            folders[raw_folder] = [str(name) for name in raw_names]
        return folders

    def _disabled_folder_names(self) -> set[str]:
        raw = cast('object', getattr(self._config, 'custom_fflag_disabled_folders', []))
        if not _is_object_collection(raw):
            return set()
        return {str(name) for name in raw}

    def _folder_keybinds(self) -> _HotkeyBindings:
        raw = cast('object', getattr(self._config, 'custom_fflag_folder_keybinds', {}))
        return raw if _is_hotkey_bindings(raw) else {}

    def _keybinds(self) -> _HotkeyBindings:
        empty_bindings: _HotkeyBindings = {}
        bindings: object = (
            getattr(self._config, 'custom_fflag_keybinds', empty_bindings) or empty_bindings
        )
        return bindings if _is_hotkey_bindings(bindings) else empty_bindings

    @staticmethod
    def _keybind_text(binding: _HotkeyBinding | None) -> str:
        binding_text: Callable[[_HotkeyBinding | None], str]
        module_name = (
            'fleasion.gui.linux_hotkeys'
            if sys.platform.startswith('linux')
            else 'fleasion.gui.windows_hotkeys'
        )
        binding_text = cast(
            'Callable[[_HotkeyBinding | None], str]',
            _lazy_attr(module_name, 'binding_text'),
        )
        return binding_text(binding)

    def _flag_is_enabled(self, row: int) -> bool:
        item = self._table.item(row, 2)
        return item is not None and item.checkState() == Qt.CheckState.Checked

    def _save_hotkey_settings(self) -> None:
        if not self._hotkeys_supported or self._config is None or self._loading:
            return
        disabled: set[str] = set()
        disabled_folders: set[str] = set()
        for row in range(self._table.rowCount()):
            if self._flag_is_enabled(row):
                continue
            name = self._row_name(row)
            if self._row_kind(row) == _FFLAG_ROW_FOLDER:
                disabled_folders.add(name)
            else:
                disabled.add(name)
        disabled_names = sorted(disabled)
        disabled_folder_names = sorted(disabled_folders)
        if disabled_names != sorted(self._disabled_flag_names()):
            self._config.custom_fflag_disabled = disabled_names
        if disabled_folder_names != sorted(self._disabled_folder_names()):
            self._config.custom_fflag_disabled_folders = disabled_folder_names
        # Status toggles do not change interception routes or hotkey bindings.
        # Runtime flag reads pick up the saved state directly.
        self._update_status()

    def _prune_hotkey_settings(self, names: set[str], *, sync_hotkeys: bool = True) -> None:
        if self._config is None:
            return
        current_folders = self._folders()
        folders = {
            folder: [name for name in members if name in names]
            for folder, members in current_folders.items()
        }
        if folders != current_folders:
            self._config.custom_fflag_folders = folders
        if not self._hotkeys_supported:
            return
        folder_names = set(folders)
        disabled = sorted(self._disabled_flag_names() & names)
        current_disabled = sorted(self._disabled_flag_names())
        if disabled != current_disabled:
            self._config.custom_fflag_disabled = disabled
        disabled_folders = sorted(self._disabled_folder_names() & folder_names)
        current_disabled_folders = sorted(self._disabled_folder_names())
        if disabled_folders != current_disabled_folders:
            self._config.custom_fflag_disabled_folders = disabled_folders
        bindings = {name: spec for name, spec in self._keybinds().items() if name in names}
        if bindings != self._keybinds():
            self._config.custom_fflag_keybinds = bindings
        folder_bindings = {
            name: spec for name, spec in self._folder_keybinds().items() if name in folder_names
        }
        if folder_bindings != self._folder_keybinds():
            self._config.custom_fflag_folder_keybinds = folder_bindings
        if sync_hotkeys:
            self._sync_hotkeys()

    def _sync_hotkeys(self) -> None:
        if self._hotkey_controller is not None:
            self._hotkey_controller.sync()
        elif self._hotkey_service is not None:
            feature_enabled = bool(
                self._config and getattr(self._config, 'custom_fflags_enabled', False)
            )
            bindings = dict(self._keybinds())
            bindings.update(
                {f'folder:{name}': spec for name, spec in self._folder_keybinds().items()}
            )
            self._hotkey_service.set_bindings(bindings if feature_enabled else {})

    def _begin_linux_hotkey_capture(self) -> LinuxHotkeyService | None:
        """Open evdev only when a user first tries to set a Linux keybind."""
        service = self._linux_hotkey_service
        if service is not None and service.begin_capture():
            return service

        first_attempt = bool(
            self._config is not None
            and not getattr(self._config, 'linux_fflag_keybind_setup_prompted', False)
        )
        if self._config is not None and first_attempt:
            self._config.linux_fflag_keybind_setup_prompted = True

        detail = getattr(
            self._hotkey_service,
            'last_error',
            tr('modifications.hotkey.unknown_input_device_error'),
        )
        if not first_attempt:
            self._status.setText(
                tr('ui.gui.modifications_tab.linux_global_keybinds_need_access_to_dev')
            )
            self._status.setStyleSheet('color: #ffcc66;')
            log_buffer.log('CustomFFlags', f'Linux keybind capture unavailable: {detail}')
            return None

        prompt = QMessageBox(self)
        prompt.setWindowTitle(tr('ui.gui.modifications_tab.enable_linux_fastflag_keybinds'))
        prompt.setIcon(QMessageBox.Icon.Information)
        prompt.setText(tr('ui.gui.modifications_tab.linux_global_keybinds_need_permission_to_read'))
        prompt.setInformativeText(
            tr(
                'ui.gui.modifications_tab.fleasion_uses_passive_dev_input_event_reads',
                value0=detail,
            )
        )
        setup_button = prompt.addButton(
            tr('ui.gui.modifications_tab.set_up_permissions'), QMessageBox.ButtonRole.AcceptRole
        )
        prompt.addButton(tr('ui.gui.modifications_tab.not_now'), QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(setup_button)
        prompt.exec()
        if prompt.clickedButton() != setup_button:
            return None
        try:
            launch_permission_setup = cast(
                'Callable[[], None]',
                _lazy_attr('fleasion.gui.linux_hotkeys', 'launch_permission_setup'),
            )
            launch_permission_setup()
        except OSError as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.modifications_tab.linux_keybind_setup_failed'),
                tr(
                    'ui.gui.modifications_tab.fleasion_could_not_start_the_polkit_permission',
                    value0=exc,
                ),
            )
            return None
        QMessageBox.information(
            self,
            tr('ui.gui.modifications_tab.linux_keybind_setup_started'),
            tr('ui.gui.modifications_tab.complete_the_administrator_prompt_then_log_out'),
        )
        return None

    def _edit_keybind(self, row: int, column: int) -> None:
        if column != 3:
            return
        name = self._row_name(row)
        if not name:
            return
        is_folder = self._row_kind(row) == _FFLAG_ROW_FOLDER
        if self._linux_keybinds:
            service = self._begin_linux_hotkey_capture()
            if service is None:
                return
            dialog = LinuxHotkeyCaptureDialog(name, service, self)
        else:
            dialog = WindowsHotkeyCaptureDialog(name, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        bindings = self._folder_keybinds() if is_folder else self._keybinds()
        config = _required_config(self._config)
        if dialog.clear_requested:
            bindings.pop(name, None)
            if is_folder:
                config.custom_fflag_folder_keybinds = bindings
            else:
                config.custom_fflag_keybinds = bindings
            self._load_flags()
            return
        if dialog.binding is None:
            return
        bindings[name] = dialog.binding
        if is_folder:
            config.custom_fflag_folder_keybinds = bindings
        else:
            config.custom_fflag_keybinds = bindings
        self._load_flags()

    def _toggle_flag_from_hotkey(self, name: str) -> None:
        if self._hotkey_controller is not None:
            self._hotkey_controller.toggle_flag(name)

    def _on_hotkey_toggled(self, _name: str) -> None:
        if hasattr(self, '_table'):
            self._load_flags(sync_hotkeys=False)

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        if self._owns_hotkey_controller and self._hotkey_controller is not None:
            self._hotkey_controller.stop()
        super().closeEvent(event)

    def _edit_value_cell(self, index: QModelIndex) -> None:
        """Open boolean value selectors explicitly instead of relying on Qt edit heuristics."""
        if (
            index.column() == 1
            and self._row_kind(index.row()) == _FFLAG_ROW_FLAG
            and self._is_boolean_flag(self._row_name(index.row()))
        ):
            self._table.edit(index)

    def _on_cell_changed(self, row: int, column: int) -> None:
        if self._loading:
            return
        if self._hotkeys_supported and column == 2:
            self._save_hotkey_settings()
            return
        if column == 0 and self._row_kind(row) == _FFLAG_ROW_FLAG:
            name_item = self._table.item(row, 0)
            if name_item is not None:
                name = name_item.text().strip()
                name_item.setData(_FFLAG_CANONICAL_NAME_ROLE, name)
                name_item.setToolTip(name)
            self._set_value_editor(
                row,
                self._row_name(row),
                self._value_from_row(row),
            )
        self._save_table()

    def _save_table(self, *_args: object) -> None:
        if self._loading or self._config is None:
            return
        self._config.custom_fflags = self._flags_from_table()
        self._prune_hotkey_settings(set(self._config.custom_fflags))
        self._update_status()
        self._filter_rows(self._search.text())

    def _update_status(self) -> None:
        if not hasattr(self, '_status'):
            return
        flags = self._flags_from_table() if hasattr(self, '_table') else {}
        count = len(flags)
        disabled = self._disabled_flag_names()
        for folder in self._disabled_folder_names():
            disabled.update(self._folders().get(folder, []))
        active_count = len(set(flags) - disabled) if self._hotkeys_supported else count
        enabled = bool(self._config and getattr(self._config, 'custom_fflags_enabled', False))
        if enabled:
            self._status.setText(
                tr(
                    'ui.gui.modifications_tab.active_value_of_value_saved_custom_fastflag',
                    value0=active_count,
                    value1=count,
                )
            )
            self._status.setStyleSheet('color: #67c587;')
        else:
            self._status.setText(
                tr(
                    'ui.gui.modifications_tab.inactive_value_custom_fastflag_s_saved_re',
                    value0=count,
                )
            )
            self._status.setStyleSheet('color: #999;')

    def _refresh_proxy_hosts(self) -> None:
        if self._proxy_master is None:
            return
        try:
            self._proxy_master.refresh_custom_fflag_interception()
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('CustomFFlags', f'Could not refresh proxy interception: {exc}')

    def _on_enabled_toggled(self, checked: bool) -> None:
        if self._config is None or self._proxy_master is None:
            return

        if checked and not self._config.custom_fflags_warning_accepted:
            warning = CustomFFlagWarningDialog(self)
            if warning.exec() != QDialog.DialogCode.Accepted:
                with QSignalBlocker(self._enable_toggle):
                    self._enable_toggle.setChecked(False)
                self._update_status()
                return
            self._config.custom_fflags_warning_accepted = True

        self._config.custom_fflags_enabled = checked
        self._sync_hotkeys()
        self._refresh_proxy_hosts()
        self._update_status()

    def _add_flag(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.gui.modifications_tab.add_custom_fastflag'))
        dialog.setMinimumWidth(620)
        form = QFormLayout(dialog)
        name_edit = QLineEdit()
        name_edit.setMinimumWidth(500)
        value_edit = QLineEdit()
        value_edit.setMinimumWidth(500)
        value_combo = CompactBooleanComboBox()
        value_combo.addItem(tr('ui.gui.modifications_tab.true'), 'True')
        value_combo.addItem(tr('ui.gui.modifications_tab.false'), 'False')
        value_stack = QStackedWidget()
        value_stack.addWidget(value_edit)
        value_stack.addWidget(value_combo)
        form.addRow(tr('modifications.fastflag.name'), name_edit)
        form.addRow(tr('modifications.fastflag.value'), value_stack)

        def update_add_value_editor(name: str) -> None:
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
            QMessageBox.warning(
                self,
                tr('ui.gui.modifications_tab.invalid_fastflag'),
                tr('ui.gui.modifications_tab.fastflag_name_cannot_be_empty'),
            )
            return
        flags = self._flags_from_table()
        flags[name] = (
            str(value_combo.currentData()) if self._is_boolean_flag(name) else value_edit.text()
        )
        self._set_flags(flags)

    def _browse_fflags(self) -> None:
        dialog = FFlagBrowserDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_flags:
            return
        flags = self._flags_from_table()
        flags.update(dialog.selected_flags)
        self._set_flags(flags)

    def _set_flags(self, flags: dict[str, str]) -> None:
        if self._config is None:
            return
        self._config.custom_fflags = flags
        self._prune_hotkey_settings(set(self._config.custom_fflags), sync_hotkeys=False)
        # Flag values are consumed directly from settings; only the master
        # enable toggle changes the interception route set.
        self._load_flags()

    def _import_mapping(self, payload: object) -> None:
        normalize_custom_fflags = cast(
            'Callable[[object], dict[str, str]]',
            _lazy_attr('fleasion.proxy.addons.custom_fflags', 'normalize_custom_fflags'),
        )

        if not _is_object_dict(payload):
            raise ValueError(tr('modifications.fastflag.import_root_must_be_object'))
        normalized = normalize_custom_fflags(payload)
        if len(normalized) != len(payload):
            raise ValueError(tr('modifications.fastflag.import_values_invalid'))
        merged = self._flags_from_table()
        merged.update(normalized)
        self._set_flags(merged)

    def _import_json(self) -> None:
        text, ok = QInputDialog.getMultiLineText(
            self,
            tr('modifications.fastflag.import_title'),
            tr('modifications.fastflag.import_prompt'),
            '{\n  "DFIntTaskSchedulerTargetFps": "20"\n}',
        )
        if not ok:
            return
        try:
            self._import_mapping(json.loads(text))
        except (json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(
                self, tr('ui.gui.modifications_tab.invalid_fastflag_json'), str(exc)
            )

    def _import_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.modifications_tab.import_custom_fastflags'),
            '',
            tr('ui.gui.modifications_tab.json_files_json_all_files'),
        )
        if not path:
            return
        try:
            self._import_mapping(json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            QMessageBox.warning(
                self, tr('ui.gui.modifications_tab.could_not_import_fastflags'), str(exc)
            )

    def _export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.modifications_tab.export_custom_fastflags'),
            'ClientAppSettings.json',
            tr('ui.gui.modifications_tab.json_files_json'),
        )
        if not path:
            return
        try:
            Path(path).write_text(self._json_text(), encoding='utf-8')
        except OSError as exc:
            QMessageBox.warning(
                self, tr('ui.gui.modifications_tab.could_not_export_fastflags'), str(exc)
            )

    def _json_text(self) -> str:
        return json.dumps(self._flags_from_table(), indent=2, ensure_ascii=False)

    def _copy_json(self) -> None:
        QApplication.clipboard().setText(self._json_text())

    def _show_profiles(self) -> None:
        dialog = FastFlagProfilesDialog(self._flags_from_table(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.loaded_flags is not None:
            self._set_flags(dialog.loaded_flags)

    def _sort_rows(self, column: int) -> None:
        if self._sort_column == column:
            self._sort_ascending = not self._sort_ascending
        else:
            self._sort_column = column
            self._sort_ascending = True

        rows = list(self._flags_from_table().items())

        def sort_value(entry: tuple[str, str]) -> str:
            if column == 0:
                return entry[0]
            if column == 1:
                return entry[1]
            if column == 2:
                return 'disabled' if entry[0] in self._disabled_flag_names() else 'enabled'
            return self._keybind_text(self._keybinds().get(entry[0]))

        rows.sort(
            key=lambda entry: (
                sort_value(entry).casefold(),
                entry[0].casefold(),
                entry[1].casefold(),
            ),
            reverse=not self._sort_ascending,
        )
        self._replace_table_rows(rows)
        self._table.horizontalHeader().setSortIndicator(
            column,
            Qt.SortOrder.AscendingOrder if self._sort_ascending else Qt.SortOrder.DescendingOrder,
        )
        self._filter_rows(self._search.text())

    def _delete_selected(self) -> None:
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        if not rows or self._config is None:
            return
        flags = self._flags_from_table()
        folders = self._folders()
        for row in rows:
            name = self._row_name(row)
            if self._row_kind(row) == _FFLAG_ROW_FOLDER:
                folders.pop(name, None)
            else:
                flags.pop(name, None)
                for members in folders.values():
                    with contextlib.suppress(ValueError):
                        members.remove(name)
        self._config.custom_fflag_folders = folders
        self._set_flags(flags)

    def _selected_flag_names(self) -> list[str]:
        return sorted(
            {
                self._row_name(index.row())
                for index in self._table.selectedIndexes()
                if self._row_kind(index.row()) == _FFLAG_ROW_FLAG and self._row_name(index.row())
            },
            key=str.casefold,
        )

    def _create_folder_from_selected(self) -> None:
        if self._config is None:
            return
        selected = self._selected_flag_names()
        if not selected:
            QMessageBox.information(
                self,
                tr('ui.gui.modifications_tab.create_fastflag_folder'),
                tr('ui.gui.modifications_tab.select_fastflags_for_folder'),
            )
            return
        name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.modifications_tab.create_fastflag_folder'),
            tr('ui.gui.modifications_tab.fastflag_folder_name'),
        )
        name = name.strip()
        if not ok or not name:
            return
        folders = self._folders()
        for members in folders.values():
            members[:] = [member for member in members if member not in selected]
        folders[name] = selected
        self._config.custom_fflag_folders = folders
        self._load_flags()

    def _move_selected_to_folder(self) -> None:
        if self._config is None:
            return
        selected = self._selected_flag_names()
        folders = self._folders()
        if not selected or not folders:
            QMessageBox.information(
                self,
                tr('ui.gui.modifications_tab.move_fastflags_to_folder'),
                tr('ui.gui.modifications_tab.select_fastflags_and_create_folder'),
            )
            return
        folder, ok = QInputDialog.getItem(
            self,
            tr('ui.gui.modifications_tab.move_fastflags_to_folder'),
            tr('ui.gui.modifications_tab.fastflag_folder'),
            sorted(folders, key=str.casefold),
            current=0,
            editable=False,
        )
        if not ok or not folder:
            return
        for members in folders.values():
            members[:] = [member for member in members if member not in selected]
        folders[str(folder)] = sorted({*folders[str(folder)], *selected}, key=str.casefold)
        self._config.custom_fflag_folders = folders
        self._load_flags()

    def _remove_selected_from_folders(self) -> None:
        if self._config is None:
            return
        selected = set(self._selected_flag_names())
        if not selected:
            return
        folders = self._folders()
        for members in folders.values():
            members[:] = [member for member in members if member not in selected]
        self._config.custom_fflag_folders = folders
        self._load_flags()

    def _filter_rows(self, text: str) -> None:
        query = str(text or '').strip().lower()
        visible_count = 0
        for row in range(self._table.rowCount()):
            name_text = self._row_name(row)
            value_text = self._value_from_row(row)
            keybind_item = self._table.item(row, 3)
            keybind_text = (
                keybind_item.text() if self._hotkeys_supported and keybind_item is not None else ''
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

    def _resize_table_to_contents(self, visible_count: int) -> None:
        """Let the outer modifications-page scroll area handle page overflow."""
        header_height = self._table.horizontalHeader().height()
        row_height = self._table.verticalHeader().defaultSectionSize() * visible_count
        content_height = header_height + row_height + (self._table.frameWidth() * 2)
        self._table.setFixedHeight(max(180, content_height))


class FFlagSection(QWidget):
    """The complete Fast Flags section content with all controls."""

    def __init__(
        self,
        manager: _ModificationManagerLike,
        *,
        roblox_monitor: RobloxExitMonitor | None = None,
        config_manager: ConfigManager | None = None,
        proxy_master: ProxyMaster | None = None,
        hotkey_controller: _HotkeyController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._roblox_monitor = roblox_monitor
        self._config_manager = config_manager
        self._proxy_master = proxy_master
        self._hotkey_controller = hotkey_controller

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

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # Warning
        warn = QLabel(
            tr(
                'ui.gui.modifications_tab.fast_flags_are_written_to_clientsettings_clientappsettings'
            )
        )
        warn.setWordWrap(True)
        warn.setStyleSheet('color: #c90; padding: 4px;')
        layout.addWidget(warn)

        warn2 = QLabel(
            tr(
                'ui.gui.modifications_tab.fleasion_synchronizes_detected_bootstrapper_launch_settings_if'
            )
        )
        warn2.setWordWrap(True)
        warn2.setStyleSheet('color: #c90; padding: 4px;')
        layout.addWidget(warn2)

        grid = QGridLayout()
        grid.setSpacing(8)
        row = 0

        # Rendering Mode
        grid.addWidget(QLabel(tr('ui.gui.modifications_tab.rendering_mode')), row, 0)
        self._rendering_mode = DropdownComboBox()
        self._rendering_mode.addItem(tr('ui.gui.modifications_tab.default'), 'Default')
        self._rendering_mode.addItem(tr('ui.gui.modifications_tab.d3d11'), 'D3D11')
        self._rendering_mode.addItem(tr('ui.gui.modifications_tab.vulkan'), 'Vulkan')
        self._rendering_mode.addItem(tr('ui.gui.modifications_tab.opengl'), 'OpenGL')
        self._rendering_mode.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._rendering_mode, row, 1)
        row += 1

        # MSAA
        grid.addWidget(QLabel(tr('ui.gui.modifications_tab.msaa_level')), row, 0)
        self._msaa = DropdownComboBox()
        self._msaa.addItem(tr('ui.gui.modifications_tab.default'), 'Default')
        self._msaa.addItem(tr('ui.gui.modifications_tab.1x_lowest'), '1')
        self._msaa.addItem(tr('ui.gui.modifications_tab.2x'), '2')
        self._msaa.addItem(tr('ui.gui.modifications_tab.4x_highest'), '4')
        self._msaa.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._msaa, row, 1)
        row += 1

        # Fix Display Scaling
        self._dpi_scale = QCheckBox(tr('ui.gui.modifications_tab.fix_display_scaling'))
        self._dpi_scale.toggled.connect(self._schedule_write)
        grid.addWidget(self._dpi_scale, row, 0, 1, 2)
        row += 1

        # Alt+Enter Fullscreen
        self._alt_enter = QCheckBox(tr('ui.gui.modifications_tab.alt_enter_fullscreen'))
        self._alt_enter.toggled.connect(self._schedule_write)
        grid.addWidget(self._alt_enter, row, 0, 1, 2)
        row += 1

        # Texture Quality
        grid.addWidget(QLabel(tr('ui.gui.modifications_tab.texture_quality')), row, 0)
        self._texture_quality = DropdownComboBox()
        self._texture_quality.addItem(tr('ui.gui.modifications_tab.default'), 'Default')
        self._texture_quality.addItem(tr('ui.gui.modifications_tab.level_0_lowest'), '0')
        self._texture_quality.addItem(tr('ui.gui.modifications_tab.level_1'), '1')
        self._texture_quality.addItem(tr('ui.gui.modifications_tab.level_2'), '2')
        self._texture_quality.addItem(tr('ui.gui.modifications_tab.level_3_highest'), '3')
        self._texture_quality.currentTextChanged.connect(self._schedule_write)
        grid.addWidget(self._texture_quality, row, 1)
        row += 1

        # Mesh LOD
        self._mesh_lod_enabled = QCheckBox(tr('ui.gui.modifications_tab.mesh_lod_override'))
        self._mesh_lod_enabled.toggled.connect(self._on_mesh_lod_toggle)
        grid.addWidget(self._mesh_lod_enabled, row, 0)
        lod_row = QHBoxLayout()
        lod_row.addWidget(QLabel(tr('ui.gui.modifications_tab.default')))
        self._mesh_lod_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._mesh_lod_slider.setRange(
            0, 4
        )  # 0=Default(no flag), 1=Level0, 2=Level1, 3=Level2, 4=Level3
        self._mesh_lod_slider.setValue(4)
        self._mesh_lod_slider.setEnabled(False)
        self._mesh_lod_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._mesh_lod_slider.setTickInterval(1)
        self._mesh_lod_slider.valueChanged.connect(self._schedule_write)
        self._mesh_lod_value = QLabel(tr('ui.gui.modifications_tab.level_3'))
        self._mesh_lod_slider.valueChanged.connect(self._update_mesh_lod_label)
        lod_row.addWidget(self._mesh_lod_slider)
        lod_row.addWidget(self._mesh_lod_value)
        lod_container = QWidget()
        lod_container.setLayout(lod_row)
        grid.addWidget(lod_container, row, 1)
        row += 1

        # FRM Quality Override
        self._frm_enabled = QCheckBox(tr('ui.gui.modifications_tab.frm_quality_override'))
        self._frm_enabled.toggled.connect(self._on_frm_toggle)
        grid.addWidget(self._frm_enabled, row, 0)
        frm_row = QHBoxLayout()
        frm_row.addWidget(QLabel(tr('ui.gui.modifications_tab.default')))
        self._frm_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self._frm_slider.setRange(0, 21)  # 0=Default(no flag), 1-21=Quality levels
        self._frm_slider.setValue(21)
        self._frm_slider.setEnabled(False)
        self._frm_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._frm_slider.setTickInterval(1)
        self._frm_slider.valueChanged.connect(self._schedule_write)
        frm_row.addWidget(self._frm_slider)
        self._frm_value = QLabel(tr('ui.gui.modifications_tab.quality_21'))
        self._frm_slider.valueChanged.connect(self._update_frm_label)
        frm_row.addWidget(self._frm_value)
        frm_container = QWidget()
        frm_container.setLayout(frm_row)
        grid.addWidget(frm_container, row, 1)
        row += 1

        # Grey Sky
        self._grey_sky = QCheckBox(tr('ui.gui.modifications_tab.grey_sky_debug'))
        self._grey_sky.toggled.connect(self._schedule_write)
        grid.addWidget(self._grey_sky, row, 0, 1, 2)
        row += 1

        # Pause Voxelizer
        self._pause_vox = QCheckBox(tr('ui.gui.modifications_tab.pause_voxelizer'))
        self._pause_vox.toggled.connect(self._schedule_write)
        grid.addWidget(self._pause_vox, row, 0, 1, 2)
        row += 1

        # Grass spinners
        self._grass_max: NoWheelSpinBox
        self._grass_min: NoWheelSpinBox
        self._grass_motion: NoWheelSpinBox
        for label_text, attr_name in [
            (tr('modifications.grass_distance_max'), '_grass_max'),
            (tr('modifications.grass_distance_min'), '_grass_min'),
            (tr('modifications.grass_motion_factor'), '_grass_motion'),
        ]:
            grid.addWidget(QLabel(label_text), row, 0)
            spin = NoWheelSpinBox()
            spin.setRange(0, 100000)
            spin.setSpecialValueText(tr('ui.gui.modifications_tab.default'))
            spin.valueChanged.connect(self._schedule_write)
            setattr(self, attr_name, spin)
            grid.addWidget(spin, row, 1)
            row += 1

        # Roblox Framerate Cap (Global Settings) - NOT disabled when FFlagsare off
        framerate_label = QLabel(
            tr('ui.gui.modifications_tab.framerate_cap_fps_globalbasicsettings_not_an_fflag')
        )
        self._framerate_cap_label = framerate_label  # Store for enable/disable
        grid.addWidget(framerate_label, row, 0)
        self._framerate_cap = NoWheelSpinBox()
        self._framerate_cap.setRange(0, 999999999)
        self._framerate_cap.setSpecialValueText(tr('ui.gui.modifications_tab.default'))
        self._framerate_cap.valueChanged.connect(self._on_framerate_changed)
        grid.addWidget(self._framerate_cap, row, 1)
        row += 1

        self._preset_container = QWidget()
        self._preset_container.setLayout(grid)
        layout.addWidget(self._preset_container)

        # Keep the allowlisted preset reset with the preset controls, above the
        # separate custom FastFlags editor.
        self._reset_btn = QPushButton(
            tr('ui.gui.modifications_tab.reset_all_allowlisted_fastflags')
        )
        self._reset_btn.clicked.connect(self._on_reset_all)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(self._reset_btn)
        layout.addLayout(btn_row)

        self._custom_fflag_editor = CustomFFlagEditor(
            self._config_manager,
            self._proxy_master,
            self,
            hotkey_controller=self._hotkey_controller,
        )
        layout.addWidget(self._custom_fflag_editor)

        self.setLayout(layout)

    def set_presets_enabled(self, enabled: bool) -> None:
        """Enable the allowlisted/local controls without disabling the proxy editor."""
        self._preset_container.setEnabled(enabled)
        self._reset_btn.setEnabled(enabled)

    def _update_mesh_lod_label(self, value: int) -> None:
        self._mesh_lod_value.setText(
            tr('ui.gui.modifications_tab.default')
            if value == 0
            else tr('modifications.level', level=value - 1)
        )

    def _update_frm_label(self, value: int) -> None:
        self._frm_value.setText(
            tr('ui.gui.modifications_tab.default')
            if value == 0
            else tr('modifications.quality', quality=value)
        )

    def _on_mesh_lod_toggle(self, checked: bool) -> None:
        self._mesh_lod_slider.setEnabled(checked)
        self._schedule_write()

    def _on_frm_toggle(self, checked: bool) -> None:
        self._frm_slider.setEnabled(checked)
        self._schedule_write()

    def _on_framerate_changed(self, *_args: object) -> None:
        """Schedule a write of the framerate cap setting."""
        self._framerate_debounce_timer.start()

    def apply_current_settings(self) -> None:
        self._schedule_write()
        self._write_framerate_cap()

    def _write_framerate_cap(self) -> None:
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
        elif value:
            run_in_thread(self._manager.sync_saved_global_settings)()
        else:
            run_in_thread(self._manager.reset_framerate_cap)()

    def _schedule_write(self, *_args: object) -> None:
        self._debounce_timer.start()

    def _gather_settings(self) -> _FastFlagSettings:
        return {
            'rendering_mode': str(self._rendering_mode.currentData() or 'Default'),
            'msaa': str(self._msaa.currentData() or 'Default'),
            'disable_dpi_scale': self._dpi_scale.isChecked(),
            'alt_enter_fullscreen': self._alt_enter.isChecked(),
            'texture_quality': str(self._texture_quality.currentData() or 'Default'),
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

    def _write_flags(self) -> None:
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

    def _load_from_manager(self) -> None:
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
        blockers = [QSignalBlocker(widget) for widget in widgets]

        idx = self._rendering_mode.findData(str(s.get('rendering_mode') or 'Default'))
        if idx >= 0:
            self._rendering_mode.setCurrentIndex(idx)

        idx = self._msaa.findData(str(s.get('msaa') or 'Default'))
        if idx >= 0:
            self._msaa.setCurrentIndex(idx)

        self._dpi_scale.setChecked(s.get('disable_dpi_scale', False))
        self._alt_enter.setChecked(s.get('alt_enter_fullscreen', False))

        idx = self._texture_quality.findData(str(s.get('texture_quality') or 'Default'))
        if idx >= 0:
            self._texture_quality.setCurrentIndex(idx)

        self._mesh_lod_enabled.setChecked(s.get('mesh_lod_enabled', False))
        mesh_lod_val = s.get('mesh_lod', 4)
        self._mesh_lod_slider.setValue(mesh_lod_val)
        self._mesh_lod_slider.setEnabled(s.get('mesh_lod_enabled', False))
        self._mesh_lod_value.setText(
            tr('ui.gui.modifications_tab.default')
            if mesh_lod_val == 0
            else tr('modifications.level', level=mesh_lod_val - 1)
        )

        self._frm_enabled.setChecked(s.get('frm_quality_enabled', False))
        frm_val = s.get('frm_quality', 21)
        self._frm_slider.setValue(frm_val)
        self._frm_slider.setEnabled(s.get('frm_quality_enabled', False))
        self._frm_value.setText(
            tr('ui.gui.modifications_tab.default')
            if frm_val == 0
            else tr('modifications.quality', quality=frm_val)
        )

        self._grey_sky.setChecked(s.get('grey_sky', False))
        self._pause_vox.setChecked(s.get('pause_voxelizer', False))

        self._grass_max.setValue(s.get('grass_max') or 0)
        self._grass_min.setValue(s.get('grass_min') or 0)
        self._grass_motion.setValue(s.get('grass_motion') or 0)

        # QSpinBox.setValue() is bound to a signed 32-bit C++ int.  Values
        # loaded from GlobalBasicSettings_13.xml (or an older config) are not
        # inherently bounded, so passing an oversized integer through PyQt can
        # raise OverflowError before QSpinBox gets a chance to clamp it.
        try:
            framerate_cap = int(self._manager.framerate_cap)
        except TypeError, ValueError, OverflowError:
            framerate_cap = 0
        framerate_cap = max(
            self._framerate_cap.minimum(),
            min(self._framerate_cap.maximum(), framerate_cap),
        )
        self._framerate_cap.setValue(framerate_cap)

        blockers.clear()

    def _on_reset_all(self) -> None:
        """Reset all fast-flag controls to default and restore files."""
        self._rendering_mode.setCurrentIndex(0)
        self._msaa.setCurrentIndex(0)
        self._dpi_scale.setChecked(False)
        self._alt_enter.setChecked(False)
        self._texture_quality.setCurrentIndex(0)
        self._mesh_lod_enabled.setChecked(False)
        self._mesh_lod_slider.setValue(4)  # Default to Level 3 (rightmost)
        self._mesh_lod_value.setText(tr('ui.gui.modifications_tab.level_3'))
        self._frm_enabled.setChecked(False)
        self._frm_slider.setValue(21)  # Default to Quality 21 (rightmost)
        self._frm_value.setText(tr('ui.gui.modifications_tab.quality_21'))
        self._grey_sky.setChecked(False)
        self._pause_vox.setChecked(False)
        self._grass_max.setValue(0)
        self._grass_min.setValue(0)
        self._grass_motion.setValue(0)
        self._framerate_cap.setValue(0)
        self._manager.framerate_cap = 0
        self._manager.reset_framerate_cap()

        self._manager.fast_flags_enabled = False


# ModificationsTab — the top-level tab widget


class ModificationsTab(QWidget):
    """The entire Modifications tab, added to the dashboard's QTabWidget."""

    def __init__(  # ruff: ignore[too-many-positional-arguments]
        self,
        mod_manager: _ModificationManagerLike,
        roblox_monitor: RobloxExitMonitor | None = None,
        config_manager: ConfigManager | None = None,
        proxy_master: ProxyMaster | None = None,
        hotkey_controller: _HotkeyController | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = mod_manager
        self._roblox_monitor = roblox_monitor
        self._config_manager = config_manager
        self._proxy_master = proxy_master
        self._hotkey_controller = hotkey_controller
        self._row_widgets: dict[str, ModRowWidget] = {}  # target_path -> widget
        self._custom_rows: list[ModRowWidget] = []

        self._setup_ui()
        self._update_status_bar()

        # Connect for live status bar updates
        mod_manager.apply_finished.connect(self._on_apply_finished)
        mod_manager.restore_finished.connect(self._update_status_bar)

        # Connect for Roblox player status changes
        if self._roblox_monitor:
            self._roblox_monitor.player_status_changed.connect(
                self._on_roblox_player_status_changed
            )

    def _setup_ui(self) -> None:
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
        self._fflag_toggle = QCheckBox(
            tr('ui.gui.modifications_tab.enable_allowlisted_fastflag_presets')
        )
        self._fflag_toggle.setChecked(self._manager.fast_flags_enabled)
        self._fflag_toggle.toggled.connect(self._on_fflag_toggle)

        fflag_section = CollapsibleSection(
            tr('modifications.section.fast_flags'),
            expanded=False,
            header_widgets=[self._fflag_toggle],
        )
        self._fflag_widget = FFlagSection(
            self._manager,
            roblox_monitor=self._roblox_monitor,
            config_manager=self._config_manager,
            proxy_master=self._proxy_master,
            hotkey_controller=self._hotkey_controller,
        )
        self._fflag_widget.set_presets_enabled(self._manager.fast_flags_enabled)
        fflag_section.add_widget(self._fflag_widget)

        self._container_layout.addWidget(fflag_section)

        # ── Default Skyboxes ─────────────────────────────────────
        sky_section = CollapsibleSection(
            tr('modifications.section.default_skyboxes'), expanded=True
        )

        # "Apply to All Sky Faces" button
        apply_all_btn = QPushButton(tr('ui.gui.modifications_tab.apply_to_all_sky_faces'))
        _ensure_text_width(apply_all_btn, 180)
        apply_all_btn.clicked.connect(self._on_apply_all_sky)
        sky_section.add_widget(apply_all_btn)

        for name, path in SKYBOX_FACES:
            row = ModRowWidget(
                self._manager,
                _builtin_label(name),
                path,
                file_filter=IMAGE_FILTER,
            )
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        # Indoor sub-label
        indoor_label = QLabel(tr('ui.gui.modifications_tab.i_indoor_skybox_i'))
        indoor_label.setContentsMargins(0, 8, 0, 0)
        sky_section.add_widget(indoor_label)

        for name, path in INDOOR_FACES:
            row = ModRowWidget(
                self._manager,
                _builtin_label(name),
                path,
                file_filter=IMAGE_FILTER,
            )
            sky_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sky_section)

        # ── Textures ─────────────────────────────────────────────
        tex_section = CollapsibleSection(tr('modifications.section.textures'), expanded=True)
        for name, path, filt in TEXTURES:
            row = ModRowWidget(self._manager, _builtin_label(name), path, file_filter=filt)
            tex_section.add_widget(row)
            self._row_widgets[path] = row
        self._container_layout.addWidget(tex_section)

        # ── R6 Default Avatar Meshes ─────────────────────────────
        self._mesh_section = CollapsibleSection(
            tr('modifications.section.r6_avatar_meshes'), expanded=True
        )
        if sys.platform.startswith('linux'):
            sober_mesh_warning = QLabel(
                tr('ui.gui.modifications_tab.b_linux_sober_limitation_b_r6_default')
            )
            sober_mesh_warning.setWordWrap(True)
            sober_mesh_warning.setContentsMargins(8, 4, 8, 8)
            sober_mesh_warning.setStyleSheet('color: #ffcc66;')
            self._mesh_section.add_widget(sober_mesh_warning)
        for name, path in AVATAR_MESHES:
            row = ModRowWidget(
                self._manager,
                _builtin_label(name),
                path,
                file_filter=MESH_FILTER,
            )
            self._mesh_section.add_widget(row)
            self._row_widgets[path] = row

        # Add Head Variant button
        add_head_btn = QPushButton(tr('ui.gui.modifications_tab.add_head_variant'))
        _ensure_text_width(add_head_btn, 150)
        add_head_btn.clicked.connect(self._on_add_head_variant)
        self._head_variant_layout = self._mesh_section.content_layout
        self._mesh_section.add_widget(add_head_btn)

        self._container_layout.addWidget(self._mesh_section)

        # ── Sounds ───────────────────────────────────────────────
        sounds_section = CollapsibleSection(tr('modifications.section.sounds'), expanded=True)
        for name, path, bundled in SOUNDS:
            row = ModRowWidget(
                self._manager,
                _builtin_label(name),
                path,
                file_filter=SOUND_FILTER,
                mute_bundled=bundled,
            )
            sounds_section.add_widget(row)
            self._row_widgets[path] = row

        self._container_layout.addWidget(sounds_section)

        # ── Custom Font ──────────────────────────────────────────
        font_section = CollapsibleSection(tr('modifications.section.custom_font'), expanded=True)
        font_row = ModRowWidget(
            self._manager,
            tr('modifications.custom_font'),
            target_path_for_current_platform(r'content\fonts\CustomFont.ttf'),
            file_filter=FONT_FILTER,
            is_font=True,
        )
        font_section.add_widget(font_row)
        self._row_widgets[target_path_for_current_platform(r'content\fonts\CustomFont.ttf')] = (
            font_row
        )

        self._container_layout.addWidget(font_section)

        # Rebuild persisted head variant rows (headA-headP added in a previous session)
        head_variant_set = set(HEAD_VARIANTS)
        for entry in self._manager.entries:
            target = entry.get('target_path', '')
            if not target or target in self._row_widgets:
                continue
            fname = Path(target.replace('\\', '/')).name
            if fname in head_variant_set:
                name = tr('modifications.head_variant', variant=fname[4:-5].upper())
                row = ModRowWidget(
                    self._manager,
                    name,
                    target,
                    file_filter=MESH_FILTER,
                    deletable=True,
                )
                row.delete_requested.connect(partial(self._on_row_deleted, row))
                self._head_variant_layout.insertWidget(
                    self._head_variant_layout.count() - 1,
                    row,
                )
                self._row_widgets[target] = row

        # ── Custom Modifications ─────────────────────────────────
        self._custom_section = CollapsibleSection(
            tr('modifications.section.custom_modifications'), expanded=True
        )

        add_custom_btn = QPushButton(tr('ui.gui.modifications_tab.add_modification'))
        _ensure_text_width(add_custom_btn, 160)
        add_custom_btn.clicked.connect(self._on_add_custom)
        self._custom_section.add_widget(add_custom_btn)

        self._custom_content_layout = self._custom_section.content_layout

        # Rebuild persisted custom entries
        for entry in self._manager.entries:
            target = entry.get('target_path', '')
            known_target = (
                any(target == path for _, path in AVATAR_MESHES)
                or any(target == path for _, path in SKYBOX_FACES)
                or any(target == path for _, path in INDOOR_FACES)
                or any(target == path for _, path, _ in SOUNDS)
            )
            if target and target not in self._row_widgets and not known_target:
                # This is likely a custom entry
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
        clear_cache_btn = QPushButton(tr('ui.gui.modifications_tab.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)
        outer.addWidget(footer_widget)

        self.setLayout(outer)
        self._update_container_bg()

    @override
    def changeEvent(self, a0: QEvent) -> None:
        super().changeEvent(a0)
        if a0.type() == QEvent.Type.PaletteChange:
            self._update_container_bg()

    def _update_container_bg(self) -> None:
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

    def _clear_roblox_cache(self) -> None:
        window_type = cast(
            'Callable[[], QWidget]',
            _lazy_attr('fleasion.gui.delete_cache', 'DeleteCacheWindow'),
        )
        window = window_type()
        window.show()

    # Status bar

    def _on_apply_finished(self, _result: object) -> None:
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        applied = sum(1 for e in self._manager.entries if e.get('status') == 'applied')
        roblox_count = len(self._manager.roblox_dirs)
        noun = tr_count(
            applied,
            'count.modification.one',
            'count.modification.other',
        )
        status_label = getattr(self, '_status_label', None)
        if status_label is None:
            return
        try:
            status_label.setText(
                tr(
                    'ui.gui.modifications_tab.value_value_applied_value_detected',
                    value0=applied,
                    value1=noun,
                    value2=tr_count(
                        roblox_count,
                        'count.roblox_dir.one',
                        'count.roblox_dir.other',
                    ),
                )
            )
        except RuntimeError:
            return

    # Section: Avatar Meshes — Add Head Variant

    def _on_add_head_variant(self) -> None:
        # Filter out already-added variants
        existing = {r.target_path for r in self._row_widgets.values()}
        available = [
            v
            for v in HEAD_VARIANTS
            if target_path_for_current_platform(rf'content\avatar\heads\{v}') not in existing
        ]
        if not available:
            QMessageBox.information(
                self,
                tr('ui.gui.modifications_tab.head_variants'),
                tr('ui.gui.modifications_tab.all_head_variants_already_added'),
            )
            return

        editable = False
        item, ok = QInputDialog.getItem(
            self,
            tr('ui.gui.modifications_tab.add_head_variant_2'),
            tr('ui.gui.modifications_tab.select_variant'),
            available,
            0,
            editable,
        )
        if ok and item:
            target = target_path_for_current_platform(rf'content\avatar\heads\{item}')
            name = item.replace('.mesh', '').title()
            row = ModRowWidget(
                self._manager,
                name,
                target,
                file_filter=MESH_FILTER,
                deletable=True,
            )
            row.delete_requested.connect(partial(self._on_row_deleted, row))
            # Insert before the "Add" button (last widget)
            self._head_variant_layout.insertWidget(
                self._head_variant_layout.count() - 1,
                row,
            )
            self._row_widgets[target] = row

    # Section: Skybox — Apply to All

    def _on_apply_all_sky(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.modifications_tab.select_file_for_all_sky_faces'),
            '',
            IMAGE_FILTER,
        )
        if not path:
            # Try asset ID instead
            text, ok = QInputDialog.getText(
                self,
                tr('ui.gui.modifications_tab.asset_id_for_all_sky_faces'),
                tr('ui.gui.modifications_tab.enter_an_asset_id_or_cancel'),
            )
            if ok and text.strip() and text.strip().isdigit():
                for _, target in SKYBOX_FACES:
                    if target in self._row_widgets:
                        self._row_widgets[target].apply_source_external('asset_id', text.strip())
            return

        for _, target in SKYBOX_FACES:
            if target in self._row_widgets:
                self._row_widgets[target].apply_source_external('local_file', path)

    # Section: Custom Modifications

    def _on_add_custom(self) -> None:
        dlg = _CustomModDialog(self._manager, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            name = dlg.display_name
            target = dlg.target_path
            raw_source = dlg.raw_source

            row = self._add_custom_row(name, target)
            if raw_source:
                # Route through the row's own detection pipeline so that
                # 'remove', CDN URLs, asset IDs etc. all work correctly.
                row.apply_raw_source(raw_source)

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

    def _on_row_deleted(self, row: ModRowWidget, _entry_id: str) -> None:
        target = row.target_path
        if target in self._row_widgets:
            del self._row_widgets[target]
        if row in self._custom_rows:
            self._custom_rows.remove(row)
        row.setParent(None)
        row.deleteLater()
        self._update_status_bar()

    # Fast Flags toggle

    def _on_fflag_toggle(self, checked: bool) -> None:
        self._manager.fast_flags_enabled = checked
        self._fflag_widget.set_presets_enabled(checked)
        if checked:
            # Immediately write current settings
            self._fflag_widget.apply_current_settings()

    def _on_roblox_player_status_changed(self, is_running: bool) -> None:
        """Apply all queued modifications when Roblox Player exits."""
        if not is_running:
            # Roblox has exited, apply any pending modifications
            self._manager.apply_pending_modifications()


# Custom Modification Dialog


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

    def __init__(self, manager: _ModificationManagerLike, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.display_name = ''
        self.target_path = ''
        self.raw_source = ''

        self.setWindowTitle(tr('ui.gui.modifications_tab.add_custom_modification'))
        self.resize(500, 200)

        layout = QVBoxLayout()

        # Display name
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(tr('ui.gui.modifications_tab.display_name')))
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(tr('ui.gui.modifications_tab.e_g_custom_skybox'))
        row1.addWidget(self._name_edit)
        layout.addLayout(row1)

        # Target path
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(tr('ui.gui.modifications_tab.target_path')))
        self._target_edit = FileDropLineEdit()
        self._target_edit.setPlaceholderText(tr('ui.gui.modifications_tab.content_sounds_oof_ogg'))
        self._target_edit.fileDropped.connect(self._on_target_file_dropped)
        row2.addWidget(self._target_edit)
        self._browse_roblox_btn = QPushButton(tr('ui.gui.modifications_tab.browse_roblox_dir'))
        self._browse_roblox_btn.clicked.connect(self._browse_roblox)
        row2.addWidget(self._browse_roblox_btn)
        layout.addLayout(row2)

        # Source
        row3 = QHBoxLayout()
        row3.addWidget(QLabel(tr('ui.gui.modifications_tab.source')))
        self._source_edit = FileDropLineEdit()
        self._source_edit.setPlaceholderText(
            tr(
                'ui.gui.modifications_tab.id_url_path_value_or_remove',
                value0=local_file_path_example(),
                value1=tr('replacer.action.remove').casefold(),
            )
        )
        row3.addWidget(self._source_edit)
        browse_btn = QPushButton(tr('ui.gui.modifications_tab.browse_2'))
        browse_btn.setAutoDefault(False)
        browse_btn.clicked.connect(self._browse_source)
        row3.addWidget(browse_btn)
        layout.addLayout(row3)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(tr('ui.gui.modifications_tab.cancel'))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        ok_btn = QPushButton(tr('ui.gui.modifications_tab.add'))
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        btn_row.addWidget(ok_btn)
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def _warn_target_outside_roblox_dirs(self) -> None:
        QMessageBox.warning(
            self,
            tr('ui.gui.modifications_tab.invalid_target'),
            tr('ui.gui.modifications_tab.target_files_must_be_inside_a_detected'),
        )

    def _on_target_file_dropped(self, path: str) -> None:
        rel = _relative_target_path_for_resource_file(path, self._manager.roblox_dirs)
        if rel is None:
            self._target_edit.clear()
            self._warn_target_outside_roblox_dirs()
            return
        self._target_edit.setText(rel)

    def _browse_roblox(self) -> None:
        """Open file dialog starting at the first Roblox directory."""
        start = ''
        if self._manager.roblox_dirs:
            start = str(self._manager.roblox_dirs[0])
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.modifications_tab.select_target_file_in_roblox_directory'),
            start,
        )
        if path:
            rel = _relative_target_path_for_resource_file(path, self._manager.roblox_dirs)
            if rel is None:
                self._warn_target_outside_roblox_dirs()
                return
            self._target_edit.setText(rel)

    def _browse_source(self) -> None:
        # Try to open the dialog in the directory/path the user may have pasted
        current_val = self._source_edit.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            try:
                p = Path(current_val)
                if p.exists():
                    # If it's a directory, start there; if it's a file, start in its parent
                    initial_dir = str(p) if p.is_dir() else str(p.parent)
                # If the exact path doesn't exist but the parent does, use the parent
                elif p.parent.exists():
                    initial_dir = str(p.parent)
            except OSError:
                initial_dir = ''

        path, _ = QFileDialog.getOpenFileName(
            self, tr('ui.gui.modifications_tab.select_source_file'), initial_dir
        )
        if path:
            self._source_edit.setText(path)

    def _on_accept(self) -> None:
        name = self._name_edit.text().strip()
        target = self._target_edit.text().strip()
        if not name:
            QMessageBox.warning(
                self,
                tr('ui.gui.modifications_tab.missing'),
                tr('ui.gui.modifications_tab.please_enter_a_display_name'),
            )
            return
        if not target:
            QMessageBox.warning(
                self,
                tr('ui.gui.modifications_tab.missing'),
                tr('ui.gui.modifications_tab.please_enter_a_target_path'),
            )
            return
        try:
            target = normalise_target_path(target).as_posix()
        except ValueError as exc:
            QMessageBox.warning(self, tr('ui.gui.modifications_tab.invalid_target'), str(exc))
            return
        self.display_name = name
        self.target_path = target
        raw = self._source_edit.text().strip().strip('"\'')
        self.raw_source = raw
        self.accept()
