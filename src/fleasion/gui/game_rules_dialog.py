"""Game Rules dialog: texture filters, per-game profiles and mod packs."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

import numpy as np
from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
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
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fleasion.config.manager import resolve_local_replacement_path
from fleasion.localization import tr, verbatim
from fleasion.rules.asset_filters import (
    EFFECT_AMOUNT_RANGES,
    FILTER_EFFECTS,
    PRESETS,
    FilterEffect,
    FilterSettings,
    FilterStep,
    active_steps,
    filter_image,
    make_step,
    normalize_filter_settings,
)
from fleasion.rules.game_profiles import GameProfile, make_profile, parse_place_id
from fleasion.rules.mod_packs import (
    PACK_EXTENSION,
    ModPackError,
    PackPreview,
    export_pack,
    install_pack,
    read_pack,
)
from fleasion.utils import get_icon_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager

_PREVIEW_SIZE = QSize(240, 120)
_FILTER_MODE_GLOBAL = 'global'
_FILTER_MODE_NONE = 'none'
_FILTER_MODE_CUSTOM = 'custom'


def _sample_image() -> Image.Image:
    """A hue sweep over a gray ramp, so every effect shows up in the preview."""
    width, height = _PREVIEW_SIZE.width(), _PREVIEW_SIZE.height()
    hue = np.linspace(0, 255, width, dtype=np.float32)
    top = Image.fromarray(
        np.stack(
            [np.repeat(hue[None, :], height // 2, axis=0)]
            + [np.full((height // 2, width), 210, dtype=np.float32)] * 2,
            axis=-1,
        ).astype(np.uint8),
        'HSV',
    ).convert('RGBA')
    ramp = np.linspace(0, 255, width, dtype=np.uint8)
    bottom = Image.fromarray(np.repeat(ramp[None, :], height - height // 2, axis=0), 'L')
    sample = Image.new('RGBA', (width, height))
    sample.paste(top, (0, 0))
    sample.paste(bottom.convert('RGBA'), (0, height // 2))
    return sample


def _pixmap(image: Image.Image) -> QPixmap:
    rgba = image.convert('RGBA')
    data = rgba.tobytes()
    qimage = QImage(data, rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())


def _effect_label(effect: FilterEffect) -> str:
    labels: dict[FilterEffect, str] = {
        'tint': tr('ui.gui.game_rules.effect.tint'),
        'grayscale': tr('ui.gui.game_rules.effect.grayscale'),
        'hue_shift': tr('ui.gui.game_rules.effect.hue_shift'),
        'saturation': tr('ui.gui.game_rules.effect.saturation'),
        'pixelate': tr('ui.gui.game_rules.effect.pixelate'),
        'posterize': tr('ui.gui.game_rules.effect.posterize'),
        'invert': tr('ui.gui.game_rules.effect.invert'),
    }
    return labels[effect]


def _preset_label(preset: str) -> str:
    labels = {
        'purple_haze': tr('ui.gui.game_rules.preset.purple_haze'),
        'noir': tr('ui.gui.game_rules.preset.noir'),
        'retro_pixels': tr('ui.gui.game_rules.preset.retro_pixels'),
        'vaporwave': tr('ui.gui.game_rules.preset.vaporwave'),
        'sunset': tr('ui.gui.game_rules.preset.sunset'),
        'inverted': tr('ui.gui.game_rules.preset.inverted'),
    }
    return labels.get(preset, verbatim(preset))


def _step_label(step: FilterStep) -> str:
    label = _effect_label(step['effect'])
    if not step['enabled']:
        return tr('ui.gui.game_rules.step_off', name=verbatim(label))
    return label


class _FiltersTab(QWidget):
    """Edit a filter stack with a live before/after preview."""

    def __init__(self, settings: FilterSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings: FilterSettings = copy.deepcopy(settings)
        self._sample = _sample_image()
        self._loading = False

        layout = QVBoxLayout(self)
        self.enabled_box = QCheckBox(tr('ui.gui.game_rules.filters_enabled'))
        self.enabled_box.setChecked(self._settings['enabled'])
        self.enabled_box.toggled.connect(self._on_enabled)
        layout.addWidget(self.enabled_box)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel(tr('ui.gui.game_rules.preset')))
        self.preset_combo = QComboBox()
        for preset in PRESETS:
            self.preset_combo.addItem(_preset_label(preset), preset)
        preset_row.addWidget(self.preset_combo, 1)
        apply_preset = QPushButton(tr('ui.gui.game_rules.use_preset'))
        apply_preset.clicked.connect(self._apply_preset)
        preset_row.addWidget(apply_preset)
        layout.addLayout(preset_row)

        body = QHBoxLayout()
        steps_column = QVBoxLayout()
        self.steps_list = QListWidget()
        self.steps_list.currentRowChanged.connect(self._on_step_selected)
        steps_column.addWidget(self.steps_list, 1)
        step_buttons = QHBoxLayout()
        add_button = QPushButton(tr('ui.gui.game_rules.add_effect'))
        add_menu = QMenu(add_button)
        for effect in FILTER_EFFECTS:
            action = add_menu.addAction(_effect_label(effect))
            action.triggered.connect(lambda _checked=False, value=effect: self._add_step(value))
        add_button.setMenu(add_menu)
        step_buttons.addWidget(add_button)
        for label, handler in (
            (tr('ui.gui.game_rules.move_up'), lambda: self._move_step(-1)),
            (tr('ui.gui.game_rules.move_down'), lambda: self._move_step(1)),
            (tr('ui.gui.game_rules.remove'), self._remove_step),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            step_buttons.addWidget(button)
        steps_column.addLayout(step_buttons)
        body.addLayout(steps_column, 1)

        editor = QGroupBox(tr('ui.gui.game_rules.effect_settings'))
        form = QFormLayout(editor)
        self.step_enabled = QCheckBox(tr('ui.gui.game_rules.effect_on'))
        self.step_enabled.toggled.connect(self._on_step_changed)
        form.addRow(self.step_enabled)
        self.strength_slider = QSlider(Qt.Orientation.Horizontal)
        self.strength_slider.setRange(0, 100)
        self.strength_slider.valueChanged.connect(self._on_step_changed)
        form.addRow(tr('ui.gui.game_rules.strength'), self.strength_slider)
        self.amount_label = QLabel(tr('ui.gui.game_rules.amount'))
        self.amount_slider = QSlider(Qt.Orientation.Horizontal)
        self.amount_slider.valueChanged.connect(self._on_step_changed)
        form.addRow(self.amount_label, self.amount_slider)
        self.color_button = QPushButton(tr('ui.gui.game_rules.pick_color'))
        self.color_button.clicked.connect(self._pick_color)
        form.addRow(tr('ui.gui.game_rules.color'), self.color_button)
        body.addWidget(editor, 1)
        layout.addLayout(body, 1)

        previews = QGridLayout()
        previews.addWidget(QLabel(tr('ui.gui.game_rules.before')), 0, 0)
        previews.addWidget(QLabel(tr('ui.gui.game_rules.after')), 0, 1)
        self.before_label = QLabel()
        self.before_label.setPixmap(_pixmap(self._sample))
        self.after_label = QLabel()
        previews.addWidget(self.before_label, 1, 0)
        previews.addWidget(self.after_label, 1, 1)
        layout.addLayout(previews)

        self.materials_box = QCheckBox(tr('ui.gui.game_rules.include_materials'))
        self.materials_box.setChecked(self._settings['include_materials'])
        self.materials_box.toggled.connect(self._on_materials)
        layout.addWidget(self.materials_box)
        note = QLabel(tr('ui.gui.game_rules.filters_note'))
        note.setWordWrap(True)
        layout.addWidget(note)

        self._refresh_steps(select=0)

    def settings(self) -> FilterSettings:
        return normalize_filter_settings(self._settings)

    def _current_step(self) -> FilterStep | None:
        row = self.steps_list.currentRow()
        steps = self._settings['steps']
        return steps[row] if 0 <= row < len(steps) else None

    def _refresh_steps(self, select: int | None = None) -> None:
        row = self.steps_list.currentRow() if select is None else select
        self.steps_list.blockSignals(True)  # ruff: ignore[boolean-positional-value-in-call]
        self.steps_list.clear()
        for step in self._settings['steps']:
            self.steps_list.addItem(_step_label(step))
        self.steps_list.blockSignals(False)  # ruff: ignore[boolean-positional-value-in-call]
        self.steps_list.setCurrentRow(min(max(row, 0), len(self._settings['steps']) - 1))
        self._load_step_editor()
        self._update_preview()

    def _load_step_editor(self) -> None:
        step = self._current_step()
        self._loading = True
        try:
            for widget in (self.step_enabled, self.strength_slider, self.color_button):
                widget.setEnabled(step is not None)
            if step is None:
                self.amount_slider.setEnabled(False)
                return
            self.step_enabled.setChecked(step['enabled'])
            self.strength_slider.setValue(round(step['strength'] * 100))
            amount_range = EFFECT_AMOUNT_RANGES.get(step['effect'])
            self.amount_slider.setEnabled(amount_range is not None)
            self.amount_label.setEnabled(amount_range is not None)
            if amount_range is not None:
                low, high, _default = amount_range
                scale = 10 if step['effect'] == 'saturation' else 1
                self.amount_slider.setRange(round(low * scale), round(high * scale))
                self.amount_slider.setValue(round(step['amount'] * scale))
            self.color_button.setEnabled(step['effect'] == 'tint')
            self.color_button.setStyleSheet(
                f'background-color: {step["color"]};' if step['effect'] == 'tint' else ''
            )
        finally:
            self._loading = False

    def _on_step_selected(self, _row: int) -> None:
        self._load_step_editor()

    def _on_step_changed(self, *_args: object) -> None:
        step = self._current_step()
        if self._loading or step is None:
            return
        step['enabled'] = self.step_enabled.isChecked()
        step['strength'] = self.strength_slider.value() / 100
        if step['effect'] in EFFECT_AMOUNT_RANGES:
            scale = 10 if step['effect'] == 'saturation' else 1
            step['amount'] = self.amount_slider.value() / scale
        item = self.steps_list.currentItem()
        if item is not None:
            item.setText(_step_label(step))
        self._update_preview()

    def _on_enabled(self, checked: bool) -> None:
        self._settings['enabled'] = checked
        self._update_preview()

    def _on_materials(self, checked: bool) -> None:
        self._settings['include_materials'] = checked

    def _apply_preset(self) -> None:
        preset = cast('str', self.preset_combo.currentData())
        self._settings['steps'] = copy.deepcopy(list(PRESETS[preset]))
        self._settings['enabled'] = True
        self.enabled_box.setChecked(True)
        self._refresh_steps(select=0)

    def _add_step(self, effect: FilterEffect) -> None:
        self._settings['steps'].append(make_step(effect))
        self._refresh_steps(select=len(self._settings['steps']) - 1)

    def _remove_step(self) -> None:
        row = self.steps_list.currentRow()
        if 0 <= row < len(self._settings['steps']):
            del self._settings['steps'][row]
            self._refresh_steps(select=row)

    def _move_step(self, offset: int) -> None:
        steps = self._settings['steps']
        row = self.steps_list.currentRow()
        target = row + offset
        if 0 <= row < len(steps) and 0 <= target < len(steps):
            steps[row], steps[target] = steps[target], steps[row]
            self._refresh_steps(select=target)

    def _pick_color(self) -> None:
        step = self._current_step()
        if step is None:
            return
        color = QColorDialog.getColor(QColor(step['color']), self, tr('ui.gui.game_rules.color'))
        if color.isValid():
            step['color'] = color.name()
            self._load_step_editor()
            self._update_preview()

    def _update_preview(self) -> None:
        steps = active_steps(self._settings)
        image = filter_image(self._sample, steps) if steps else self._sample
        self.after_label.setPixmap(_pixmap(image))


class _ProfileEditor(QDialog):
    """Create or edit one game profile."""

    def __init__(
        self,
        profile: GameProfile,
        config_names: list[str],
        current_filters: FilterSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.gui.game_rules.edit_profile'))
        self._profile: GameProfile = copy.deepcopy(profile)
        self._current_filters = current_filters

        layout = QFormLayout(self)
        self.name_edit = QLineEdit(profile['name'])
        layout.addRow(tr('ui.gui.game_rules.profile_name'), self.name_edit)
        self.place_edit = QLineEdit(str(profile['place_id']))
        self.place_edit.setPlaceholderText(tr('ui.gui.game_rules.place_id_hint'))
        layout.addRow(tr('ui.gui.game_rules.place_id'), self.place_edit)

        self.configs_list = QListWidget()
        for name in config_names:
            item = QListWidgetItem(verbatim(name))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = name in profile['configs']
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            self.configs_list.addItem(item)
        layout.addRow(tr('ui.gui.game_rules.profile_configs'), self.configs_list)

        self.filter_mode = QComboBox()
        self.filter_mode.addItem(tr('ui.gui.game_rules.filters_global'), _FILTER_MODE_GLOBAL)
        self.filter_mode.addItem(tr('ui.gui.game_rules.filters_none'), _FILTER_MODE_NONE)
        self.filter_mode.addItem(tr('ui.gui.game_rules.filters_custom'), _FILTER_MODE_CUSTOM)
        filters = profile['filters']
        if filters is None:
            self.filter_mode.setCurrentIndex(0)
        elif not active_steps(filters):
            self.filter_mode.setCurrentIndex(1)
        else:
            self.filter_mode.setCurrentIndex(2)
        layout.addRow(tr('ui.gui.game_rules.profile_filters'), self.filter_mode)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def profile(self) -> GameProfile:
        return self._profile

    def _accept(self) -> None:
        place_id = parse_place_id(self.place_edit.text())
        if place_id is None:
            QMessageBox.warning(
                self, tr('ui.gui.game_rules.title'), tr('ui.gui.game_rules.invalid_place')
            )
            return
        configs: list[str] = []
        for index in range(self.configs_list.count()):
            item = self.configs_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                configs.append(cast('str', item.data(Qt.ItemDataRole.UserRole)))
        mode = cast('str', self.filter_mode.currentData())
        if mode == _FILTER_MODE_GLOBAL:
            filters: FilterSettings | None = None
        elif mode == _FILTER_MODE_NONE:
            filters = {'enabled': False, 'include_materials': False, 'steps': []}
        elif self._profile['filters'] is not None and active_steps(self._profile['filters']):
            filters = self._profile['filters']
        else:
            filters = {**copy.deepcopy(self._current_filters), 'enabled': True}
        enabled = self._profile['enabled']
        self._profile = make_profile(place_id, self.name_edit.text(), configs, filters)
        self._profile['enabled'] = enabled
        self.accept()


class _ProfilesTab(QWidget):
    """List, add and edit per-game profiles."""

    def __init__(
        self,
        config_manager: ConfigManager,
        filters_tab: _FiltersTab,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._filters_tab = filters_tab
        self._profiles: list[GameProfile] = config_manager.game_profiles

        layout = QVBoxLayout(self)
        intro = QLabel(tr('ui.gui.game_rules.profiles_intro'))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.current_label = QLabel()
        layout.addWidget(self.current_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [
                tr('ui.gui.game_rules.column_game'),
                tr('ui.gui.game_rules.column_place'),
                tr('ui.gui.game_rules.column_configs'),
                tr('ui.gui.game_rules.column_filters'),
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.doubleClicked.connect(lambda _index: self._edit_selected())
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        for label, handler in (
            (tr('ui.gui.game_rules.add_current_game'), self._add_current),
            (tr('ui.gui.game_rules.add_profile'), self._add_profile),
            (tr('ui.gui.game_rules.edit'), self._edit_selected),
            (tr('ui.gui.game_rules.remove'), self._remove_selected),
        ):
            button = QPushButton(label)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self._refresh()

    def profiles(self) -> list[GameProfile]:
        return self._profiles

    def _refresh(self) -> None:
        place_id = self._config.active_place_id
        active = self._config.active_game_profile
        if place_id is None:
            self.current_label.setText(tr('ui.gui.game_rules.no_current_game'))
        elif active is None:
            self.current_label.setText(tr('ui.gui.game_rules.current_game', place=place_id))
        else:
            self.current_label.setText(
                tr(
                    'ui.gui.game_rules.current_game_profile',
                    place=place_id,
                    name=verbatim(active['name']),
                )
            )
        self.table.blockSignals(True)  # ruff: ignore[boolean-positional-value-in-call]
        self.table.setRowCount(len(self._profiles))
        for row, profile in enumerate(self._profiles):
            name_item = QTableWidgetItem(verbatim(profile['name']))
            name_item.setFlags(name_item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            name_item.setCheckState(
                Qt.CheckState.Checked if profile['enabled'] else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, name_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(profile['place_id'])))
            configs = ', '.join(profile['configs']) or tr('ui.gui.game_rules.no_configs')
            self.table.setItem(row, 2, QTableWidgetItem(verbatim(configs)))
            filters = profile['filters']
            if filters is None:
                filter_text = tr('ui.gui.game_rules.filters_global')
            elif active_steps(filters):
                filter_text = tr('ui.gui.game_rules.filters_custom')
            else:
                filter_text = tr('ui.gui.game_rules.filters_none')
            self.table.setItem(row, 3, QTableWidgetItem(filter_text))
        self.table.blockSignals(False)  # ruff: ignore[boolean-positional-value-in-call]

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 0 and 0 <= item.row() < len(self._profiles):
            self._profiles[item.row()]['enabled'] = item.checkState() == Qt.CheckState.Checked

    def _upsert(self, profile: GameProfile, replace_row: int | None = None) -> None:
        for index, existing in enumerate(self._profiles):
            if existing['place_id'] == profile['place_id'] and index != replace_row:
                QMessageBox.warning(
                    self,
                    tr('ui.gui.game_rules.title'),
                    tr('ui.gui.game_rules.duplicate_place', place=profile['place_id']),
                )
                return
        if replace_row is None:
            self._profiles.append(profile)
        else:
            self._profiles[replace_row] = profile
        self._refresh()

    def _open_editor(self, profile: GameProfile, replace_row: int | None = None) -> None:
        editor = _ProfileEditor(
            profile, self._config.config_names, self._filters_tab.settings(), self
        )
        if editor.exec() == QDialog.DialogCode.Accepted:
            self._upsert(editor.profile(), replace_row)

    def _add_current(self) -> None:
        place_id = self._config.active_place_id
        if place_id is None:
            QMessageBox.information(
                self, tr('ui.gui.game_rules.title'), tr('ui.gui.game_rules.join_a_game_first')
            )
            return
        self._open_editor(make_profile(place_id, '', self._config.enabled_configs))

    def _add_profile(self) -> None:
        text, accepted = QInputDialog.getText(
            self, tr('ui.gui.game_rules.add_profile'), tr('ui.gui.game_rules.place_id_hint')
        )
        place_id = parse_place_id(text) if accepted else None
        if accepted and place_id is None:
            QMessageBox.warning(
                self, tr('ui.gui.game_rules.title'), tr('ui.gui.game_rules.invalid_place')
            )
        if place_id is not None:
            self._open_editor(make_profile(place_id, '', self._config.enabled_configs))

    def _selected_row(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _edit_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            self._open_editor(self._profiles[row], row)

    def _remove_selected(self) -> None:
        row = self._selected_row()
        if row is not None:
            del self._profiles[row]
            self._refresh()


class _PacksTab(QWidget):
    """Export configs as a mod pack, or preview and install one."""

    def __init__(
        self,
        config_manager: ConfigManager,
        save_pending: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._save_pending = save_pending

        layout = QVBoxLayout(self)
        export_box = QGroupBox(tr('ui.gui.game_rules.export_pack'))
        export_form = QFormLayout(export_box)
        self.configs_list = QListWidget()
        for name in config_manager.config_names:
            item = QListWidgetItem(verbatim(name))
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            enabled = name in config_manager.enabled_configs
            item.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
            self.configs_list.addItem(item)
        export_form.addRow(tr('ui.gui.game_rules.pack_configs'), self.configs_list)
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText(tr('ui.gui.game_rules.pack_name_hint'))
        export_form.addRow(tr('ui.gui.game_rules.pack_name'), self.name_edit)
        self.author_edit = QLineEdit()
        export_form.addRow(tr('ui.gui.game_rules.pack_author'), self.author_edit)
        self.description_edit = QTextEdit()
        self.description_edit.setFixedHeight(60)
        export_form.addRow(tr('ui.gui.game_rules.pack_description'), self.description_edit)
        self.include_filters = QCheckBox(tr('ui.gui.game_rules.pack_include_filters'))
        self.include_filters.setChecked(True)
        self.include_profiles = QCheckBox(tr('ui.gui.game_rules.pack_include_profiles'))
        self.include_profiles.setChecked(True)
        export_form.addRow(self.include_filters)
        export_form.addRow(self.include_profiles)
        export_button = QPushButton(tr('ui.gui.game_rules.export_button'))
        export_button.clicked.connect(self._export)
        export_form.addRow(export_button)
        layout.addWidget(export_box)

        import_box = QGroupBox(tr('ui.gui.game_rules.import_pack'))
        import_layout = QVBoxLayout(import_box)
        import_note = QLabel(tr('ui.gui.game_rules.import_note'))
        import_note.setWordWrap(True)
        import_layout.addWidget(import_note)
        import_button = QPushButton(tr('ui.gui.game_rules.import_button'))
        import_button.clicked.connect(self._import)
        import_layout.addWidget(import_button)
        layout.addWidget(import_box)
        layout.addStretch(1)

    def _checked_configs(self) -> list[str]:
        names: list[str] = []
        for index in range(self.configs_list.count()):
            item = self.configs_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                names.append(cast('str', item.data(Qt.ItemDataRole.UserRole)))
        return names

    def _export(self) -> None:
        configs = self._checked_configs()
        name = self.name_edit.text().strip() or (configs[0] if configs else '')
        if not configs:
            QMessageBox.warning(
                self, tr('ui.gui.game_rules.title'), tr('ui.gui.game_rules.pick_configs')
            )
            return
        self._save_pending()
        path, _filter = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.game_rules.export_pack'),
            str(Path.home() / f'{name}{PACK_EXTENSION}'),
            tr('ui.gui.game_rules.pack_file_filter', extension=PACK_EXTENSION),
        )
        if not path:
            return
        try:
            result = export_pack(
                self._config,
                configs,
                Path(path),
                name=name,
                author=self.author_edit.text(),
                description=self.description_edit.toPlainText(),
                include_filters=self.include_filters.isChecked(),
                include_profiles=self.include_profiles.isChecked(),
                resolve_local_path=resolve_local_replacement_path,
            )
        except (OSError, ModPackError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.game_rules.title'),
                tr('ui.gui.game_rules.export_failed', error=verbatim(str(exc))),
            )
            return
        message = tr(
            'ui.gui.game_rules.export_done',
            path=verbatim(str(result.path)),
            files=result.file_count,
        )
        if result.missing_files:
            message += '\n\n' + tr(
                'ui.gui.game_rules.export_missing',
                files=verbatim('\n'.join(result.missing_files[:10])),
            )
        QMessageBox.information(self, tr('ui.gui.game_rules.title'), message)

    def _confirm_install(self, preview: PackPreview) -> tuple[bool, bool, bool] | None:
        manifest = preview.manifest
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.gui.game_rules.import_pack'))
        layout = QVBoxLayout(dialog)
        summary = QLabel(
            tr(
                'ui.gui.game_rules.pack_summary',
                name=verbatim(manifest['name']),
                author=verbatim(manifest['author'] or '?'),
                configs=len(preview.configs),
                rules=preview.rule_count,
                files=len(preview.files),
                megabytes=f'{preview.total_bytes / (1024 * 1024):.1f}',
            )
        )
        summary.setWordWrap(True)
        summary.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(summary)
        if manifest['description']:
            description = QLabel(verbatim(manifest['description']))
            description.setWordWrap(True)
            description.setTextFormat(Qt.TextFormat.PlainText)
            layout.addWidget(description)
        enable_box = QCheckBox(tr('ui.gui.game_rules.install_enable'))
        enable_box.setChecked(True)
        layout.addWidget(enable_box)
        filters_box = QCheckBox(tr('ui.gui.game_rules.install_filters'))
        filters_box.setEnabled(manifest['filters'] is not None)
        layout.addWidget(filters_box)
        profiles_box = QCheckBox(
            tr('ui.gui.game_rules.install_profiles', count=len(manifest['game_profiles']))
        )
        profiles_box.setChecked(bool(manifest['game_profiles']))
        profiles_box.setEnabled(bool(manifest['game_profiles']))
        layout.addWidget(profiles_box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return enable_box.isChecked(), filters_box.isChecked(), profiles_box.isChecked()

    def _import(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.game_rules.import_pack'),
            str(Path.home()),
            tr('ui.gui.game_rules.pack_file_filter', extension=PACK_EXTENSION),
        )
        if not path:
            return
        try:
            preview = read_pack(Path(path))
        except ModPackError as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.game_rules.title'),
                tr('ui.gui.game_rules.import_failed', error=verbatim(str(exc))),
            )
            return
        choices = self._confirm_install(preview)
        if choices is None:
            return
        enable, filters, profiles = choices
        self._save_pending()
        try:
            result = install_pack(
                preview,
                self._config,
                enable_configs=enable,
                apply_filters=filters,
                add_profiles=profiles,
            )
        except (OSError, ModPackError) as exc:
            QMessageBox.warning(
                self,
                tr('ui.gui.game_rules.title'),
                tr('ui.gui.game_rules.import_failed', error=verbatim(str(exc))),
            )
            return
        window = self.window()
        if isinstance(window, GameRulesDialog):
            window.reload_from_config()
        QMessageBox.information(
            self,
            tr('ui.gui.game_rules.title'),
            tr(
                'ui.gui.game_rules.import_done',
                configs=verbatim(', '.join(result.configs.values())),
                profiles=len(result.profiles_added),
            ),
        )


class GameRulesDialog(QDialog):
    """Filters, game profiles and mod packs in one place."""

    _live_dialogs: ClassVar[set[GameRulesDialog]] = set()

    def __init__(self, config_manager: ConfigManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._live_dialogs.add(self)
        self.finished.connect(lambda _result: self._live_dialogs.discard(self))
        self.setWindowTitle(tr('ui.gui.game_rules.title'))
        if icon_path := get_icon_path():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(760, 640)

        self._layout = QVBoxLayout(self)
        self._tabs = QTabWidget()
        self._layout.addWidget(self._tabs, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        buttons.accepted.connect(self._save_and_close)
        buttons.rejected.connect(self.reject)
        self._layout.addWidget(buttons)
        self._build_tabs()

    def _build_tabs(self) -> None:
        current = self._tabs.currentIndex()
        self._tabs.clear()
        self.filters_tab = _FiltersTab(self._config.asset_filters)
        self.profiles_tab = _ProfilesTab(self._config, self.filters_tab)
        self.packs_tab = _PacksTab(self._config, self.save)
        self._tabs.addTab(self.filters_tab, tr('ui.gui.game_rules.tab_filters'))
        self._tabs.addTab(self.profiles_tab, tr('ui.gui.game_rules.tab_profiles'))
        self._tabs.addTab(self.packs_tab, tr('ui.gui.game_rules.tab_packs'))
        self._tabs.setCurrentIndex(max(current, 0))

    def reload_from_config(self) -> None:
        """Show settings a pack install just changed."""
        self._build_tabs()

    def save(self) -> None:
        self._config.asset_filters = self.filters_tab.settings()
        self._config.game_profiles = self.profiles_tab.profiles()

    def _save_and_close(self) -> None:
        self.save()
        self.accept()
