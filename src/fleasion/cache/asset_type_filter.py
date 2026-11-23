"""Asset-type filter popup shared by UI surfaces that should avoid OpenGL imports."""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr
from fleasion.widgets.menu_items import sync_widget_menu_surface
from fleasion.widgets.scrolling_menu import ScrollingMenu
from fleasion.widgets.text import style_text

type AssetType = int | str


_CATEGORY_TYPES: dict[str, list[AssetType]] = {
    'models_3d': [4, 10, 39, 40, 32, 17, 79, 75],
    'images_textures': [1, 13, 63, 21, 22, 18],
    'audio_video': [3, 62, 33],
    'animations': [
        24,
        'R6Animation',
        'R15Animation',
        'NonPlayerAnimation',
        48,
        49,
        50,
        51,
        52,
        53,
        54,
        55,
        56,
        61,
        78,
    ],
    'avatar_parts': [16, 25, 26, 27, 28, 29, 30, 31],
    'clothing': [2, 11, 12, 8, 19],
    'accessories': [41, 42, 43, 44, 45, 46, 47, 57, 58, 64, 65, 66, 67, 68, 69, 70, 71, 72, 76, 77],
    'scripts_data': [5, 6, 7, 37, 38, 80, 59, 74, 73, 35, 34, 9, 'Json'],
}


def _category_label(category: str) -> str:
    return {
        'models_3d': tr('asset_filter.category.models_3d'),
        'images_textures': tr('asset_filter.category.images_textures'),
        'audio_video': tr('asset_filter.category.audio_video'),
        'animations': tr('asset_filter.category.animations'),
        'avatar_parts': tr('asset_filter.category.avatar_parts'),
        'clothing': tr('asset_filter.category.clothing'),
        'accessories': tr('asset_filter.category.accessories'),
        'scripts_data': tr('asset_filter.category.scripts_data'),
    }[category]


def asset_type_display_name(type_id: AssetType) -> str:
    if type_id == 'R6Animation':
        return tr('asset_filter.type.r6_animation')
    if type_id == 'R15Animation':
        return tr('asset_filter.type.r15_animation')
    if type_id == 'NonPlayerAnimation':
        return tr('asset_filter.type.non_player_animation')
    if type_id == 'Json':
        return tr('asset_filter.type.json')
    labels: dict[AssetType, str] = {
        1: tr('asset_filter.type.1'),
        2: tr('asset_filter.type.2'),
        3: tr('asset_filter.type.3'),
        4: tr('asset_filter.type.4'),
        5: tr('asset_filter.type.5'),
        6: tr('asset_filter.type.6'),
        7: tr('asset_filter.type.7'),
        8: tr('asset_filter.type.8'),
        9: tr('asset_filter.type.9'),
        10: tr('asset_filter.type.10'),
        11: tr('asset_filter.type.11'),
        12: tr('asset_filter.type.12'),
        13: tr('asset_filter.type.13'),
        16: tr('asset_filter.type.16'),
        17: tr('asset_filter.type.17'),
        18: tr('asset_filter.type.18'),
        19: tr('asset_filter.type.19'),
        21: tr('asset_filter.type.21'),
        22: tr('asset_filter.type.22'),
        24: tr('asset_filter.type.24'),
        25: tr('asset_filter.type.25'),
        26: tr('asset_filter.type.26'),
        27: tr('asset_filter.type.27'),
        28: tr('asset_filter.type.28'),
        29: tr('asset_filter.type.29'),
        30: tr('asset_filter.type.30'),
        31: tr('asset_filter.type.31'),
        32: tr('asset_filter.type.32'),
        33: tr('asset_filter.type.33'),
        34: tr('asset_filter.type.34'),
        35: tr('asset_filter.type.35'),
        37: tr('asset_filter.type.37'),
        38: tr('asset_filter.type.38'),
        39: tr('asset_filter.type.39'),
        40: tr('asset_filter.type.40'),
        41: tr('asset_filter.type.41'),
        42: tr('asset_filter.type.42'),
        43: tr('asset_filter.type.43'),
        44: tr('asset_filter.type.44'),
        45: tr('asset_filter.type.45'),
        46: tr('asset_filter.type.46'),
        47: tr('asset_filter.type.47'),
        48: tr('asset_filter.type.48'),
        49: tr('asset_filter.type.49'),
        50: tr('asset_filter.type.50'),
        51: tr('asset_filter.type.51'),
        52: tr('asset_filter.type.52'),
        53: tr('asset_filter.type.53'),
        54: tr('asset_filter.type.54'),
        55: tr('asset_filter.type.55'),
        56: tr('asset_filter.type.56'),
        57: tr('asset_filter.type.57'),
        58: tr('asset_filter.type.58'),
        59: tr('asset_filter.type.59'),
        61: tr('asset_filter.type.61'),
        62: tr('asset_filter.type.62'),
        63: tr('asset_filter.type.63'),
        64: tr('asset_filter.type.64'),
        65: tr('asset_filter.type.65'),
        66: tr('asset_filter.type.66'),
        67: tr('asset_filter.type.67'),
        68: tr('asset_filter.type.68'),
        69: tr('asset_filter.type.69'),
        70: tr('asset_filter.type.70'),
        71: tr('asset_filter.type.71'),
        72: tr('asset_filter.type.72'),
        73: tr('asset_filter.type.73'),
        74: tr('asset_filter.type.74'),
        75: tr('asset_filter.type.75'),
        76: tr('asset_filter.type.76'),
        77: tr('asset_filter.type.77'),
        78: tr('asset_filter.type.78'),
        79: tr('asset_filter.type.79'),
        80: tr('asset_filter.type.80'),
    }
    return labels.get(type_id, str(type_id))


class CategoryFilterPopup(ScrollingMenu):
    filters_changed = Signal(set)

    def __init__(
        self,
        parent: QWidget | None = None,
        active_filters: set[AssetType] | None = None,
    ) -> None:
        super().__init__(parent, horizontal_scroll=True)
        self.scroll_area.setObjectName('FilterScrollArea')

        self.active_filters: set[AssetType] = set(active_filters) if active_filters else set()
        self._updating = False

        self.container = QWidget()
        self.container.setObjectName('FilterContainer')
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(10, 10, 10, 10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        self.categories: dict[str, list[AssetType]] = {
            key: list(type_ids) for key, type_ids in _CATEGORY_TYPES.items()
        }

        self.checkboxes: dict[AssetType, QCheckBox] = {}
        self.category_checkboxes: dict[str, QCheckBox] = {}

        col = 0
        row = 0
        fm = QFontMetrics(self.font())

        for cat_name, type_ids in self.categories.items():
            cat_frame = QFrame()
            cat_frame.setObjectName('CategoryCard')
            cat_frame.setStyleSheet("""
                QFrame#CategoryCard {
                    border: 1px solid palette(mid);
                    border-radius: 6px;
                    background-color: palette(base);
                }
            """)
            vbox = QVBoxLayout(cat_frame)
            vbox.setContentsMargins(6, 6, 6, 6)
            vbox.setSpacing(3)

            cat_cb = QCheckBox(_category_label(cat_name))
            style_text(cat_cb, tone='info', bold=True)
            cat_cb.setTristate(True)
            self.category_checkboxes[cat_name] = cat_cb
            vbox.addWidget(cat_cb)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)
            line.setStyleSheet(
                'background-color: palette(mid); margin-bottom: 2px; margin-top: 2px;'
            )
            vbox.addWidget(line)

            cat_types: list[AssetType] = []
            for tid in type_ids:
                name = asset_type_display_name(tid)
                elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, 130)
                cb = QCheckBox(elided)
                if elided != name:
                    cb.setToolTip(name)
                cb.setChecked(tid in self.active_filters)
                self.checkboxes[tid] = cb
                vbox.addWidget(cb)
                cat_types.append(tid)

            def toggle_category(
                _checked: bool = False,
                category_types: list[AssetType] = cat_types,
                category_name: str = cat_name,
            ) -> None:
                self._on_category_clicked(category_types, category_name)

            cat_cb.clicked.connect(toggle_category)
            for tid in cat_types:
                cb = self.checkboxes[tid]

                def toggle_type(
                    checked: bool = False,
                    type_id: AssetType = tid,
                    category_name: str = cat_name,
                ) -> None:
                    self._on_type_clicked(type_id, category_name, checked)

                cb.clicked.connect(toggle_type)

            self._update_category_state(cat_name)
            vbox.addStretch()
            grid.addWidget(cat_frame, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1

        layout.addLayout(grid)

        btn_layout = QHBoxLayout()
        clear_btn = QPushButton(tr('ui.cache.asset_type_filter.clear_filters'))
        clear_btn.setStyleSheet(
            'padding: 5px 15px; border: 1px solid palette(mid); border-radius: 3px;'
        )
        clear_btn.clicked.connect(self._clear_all)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self._sync_surface()
        self.aboutToShow.connect(self._sync_surface)
        self.set_content(self.container)

    def _sync_surface(self) -> None:
        sync_widget_menu_surface(self, self.container, checkbox_padding='1px')

    def set_active_filters(self, active_filters: set[AssetType] | None) -> None:
        """Update the popup checks without rebuilding the widget."""
        self.active_filters: set[AssetType] = set(active_filters) if active_filters else set()
        self._updating = True
        for tid, cb in self.checkboxes.items():
            with QSignalBlocker(cb):
                cb.setChecked(tid in self.active_filters)
        for cat_name in self.categories:
            self._update_category_state(cat_name)
        self._updating = False

    def _on_category_clicked(self, type_ids: list[AssetType], cat_name: str) -> None:
        if self._updating:
            return
        self._updating = True

        checked_count = sum(
            1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked()
        )
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)
        new_state = checked_count < total_count

        for tid in type_ids:
            if tid in self.checkboxes:
                cb = self.checkboxes[tid]
                with QSignalBlocker(cb):
                    cb.setChecked(new_state)
                if new_state:
                    self.active_filters.add(tid)
                else:
                    self.active_filters.discard(tid)

        self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)

    def _on_type_clicked(self, tid: AssetType, cat_name: str, checked: bool) -> None:
        if self._updating:
            return
        self._updating = True
        if checked:
            self.active_filters.add(tid)
        else:
            self.active_filters.discard(tid)

        self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)

    def _update_category_state(self, cat_name: str) -> None:
        cat_cb = self.category_checkboxes[cat_name]
        type_ids = self.categories[cat_name]
        checked_count = sum(
            1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked()
        )
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)

        with QSignalBlocker(cat_cb):
            if checked_count == 0:
                cat_cb.setCheckState(Qt.CheckState.Unchecked)
            elif checked_count == total_count and total_count > 0:
                cat_cb.setCheckState(Qt.CheckState.Checked)
            else:
                cat_cb.setCheckState(Qt.CheckState.PartiallyChecked)

    def _clear_all(self) -> None:
        if self._updating:
            return
        self._updating = True
        self.active_filters.clear()
        for cb in self.checkboxes.values():
            with QSignalBlocker(cb):
                cb.setChecked(False)
        for cat_name in self.categories:
            self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)
