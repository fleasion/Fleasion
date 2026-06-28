"""Asset-type filter popup shared by UI surfaces that should avoid OpenGL imports."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from .cache_manager import CacheManager


class CategoryFilterPopup(QMenu):
    filters_changed = pyqtSignal(set)

    def __init__(self, parent=None, active_filters=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QMenu { background-color: palette(window); border: 1px solid palette(mid); border-radius: 4px; color: palette(window-text); }
            QWidget#FilterContainer { background-color: palette(window); }
            QCheckBox { padding: 1px; color: palette(window-text); font-size: 12px; }
            QCheckBox::indicator { width: 14px; height: 14px; }
        """)

        self.active_filters = set(active_filters) if active_filters else set()
        self._updating = False

        self.container = QWidget()
        self.container.setObjectName("FilterContainer")
        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(10, 10, 10, 10)

        grid = QGridLayout()
        grid.setHorizontalSpacing(4)
        grid.setVerticalSpacing(4)

        self.categories = {
            '3D Models': [4, 10, 39, 40, 32, 17, 79, 75],
            'Images/Textures': [1, 13, 63, 21, 22, 18],
            'Audio/Video': [3, 62, 33],
            'Animations': [24, ('R6Animation', 'R6 Animation'), ('R15Animation', 'R15 Animation'), ('NonPlayerAnimation', 'Non-player Animation'), 48, 49, 50, 51, 52, 53, 54, 55, 56, 61, 78],
            'Avatar Parts': [16, 25, 26, 27, 28, 29, 30, 31],
            'Clothing': [2, 11, 12, 8, 19],
            'Accessories': [41, 42, 43, 44, 45, 46, 47, 57, 58, 64, 65, 66, 67, 68, 69, 70, 71, 72, 76, 77],
            'Scripts/Data': [5, 6, 7, 37, 38, 80, 59, 74, 73, 35, 34, 9, 'Json'],
        }

        self.checkboxes = {}
        self.category_checkboxes = {}

        col = 0
        row = 0
        fm = QFontMetrics(self.font())

        for cat_name, type_ids in self.categories.items():
            cat_frame = QFrame()
            cat_frame.setObjectName("CategoryCard")
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

            cat_cb = QCheckBox(cat_name)
            cat_cb.setStyleSheet("font-weight: bold; color: #55aaff;")
            cat_cb.setTristate(True)
            self.category_checkboxes[cat_name] = cat_cb
            vbox.addWidget(cat_cb)

            line = QFrame()
            line.setFrameShape(QFrame.Shape.HLine)
            line.setFrameShadow(QFrame.Shadow.Sunken)
            line.setStyleSheet("background-color: palette(mid); margin-bottom: 2px; margin-top: 2px;")
            vbox.addWidget(line)

            cat_types = []
            for tid in type_ids:
                if isinstance(tid, tuple):
                    tid, name = tid
                elif isinstance(tid, str):
                    name = tid
                elif tid in CacheManager.ASSET_TYPES:
                    name = CacheManager.ASSET_TYPES[tid]
                else:
                    continue

                elided = fm.elidedText(name, Qt.TextElideMode.ElideRight, 130)
                cb = QCheckBox(elided)
                if elided != name:
                    cb.setToolTip(name)
                cb.setChecked(tid in self.active_filters)
                self.checkboxes[tid] = cb
                vbox.addWidget(cb)
                cat_types.append(tid)

            cat_cb.clicked.connect(lambda checked, t=cat_types, c=cat_name: self._on_category_clicked(t, c))
            for tid in cat_types:
                cb = self.checkboxes[tid]
                cb.clicked.connect(lambda checked, t=tid, c=cat_name: self._on_type_clicked(t, c, checked))

            self._update_category_state(cat_name)
            vbox.addStretch()
            grid.addWidget(cat_frame, row, col)
            col += 1
            if col >= 4:
                col = 0
                row += 1

        layout.addLayout(grid)

        btn_layout = QHBoxLayout()
        clear_btn = QPushButton("Clear Filters")
        clear_btn.setStyleSheet("padding: 5px 15px; border: 1px solid palette(mid); border-radius: 3px;")
        clear_btn.clicked.connect(self._clear_all)
        btn_layout.addWidget(clear_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("FilterScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setWidget(self.container)

        self._natural_content_size = self.container.sizeHint()
        self._set_popup_content_size(self._natural_content_size.height())

        action = QWidgetAction(self)
        action.setDefaultWidget(self.scroll_area)
        self.addAction(action)

    def _set_popup_content_size(self, max_height):
        natural = self._natural_content_size
        height = min(natural.height(), max(220, max_height))
        width = natural.width()
        if natural.height() > height:
            width += self.scroll_area.verticalScrollBar().sizeHint().width()
        self.scroll_area.setFixedSize(width, height)

    def constrain_to_available_geometry(self, available_geometry, anchor_y=None):
        """Bound the popup to the visible screen area and enable vertical scroll."""
        if available_geometry is None:
            return

        if anchor_y is None:
            available_height = available_geometry.height()
        else:
            space_below = available_geometry.bottom() - anchor_y
            space_above = anchor_y - available_geometry.top()
            available_height = max(space_below, space_above)

        self._set_popup_content_size(max(220, available_height - 12))
        self.adjustSize()

    def set_active_filters(self, active_filters):
        """Update the popup checks without rebuilding the widget."""
        self.active_filters = set(active_filters) if active_filters else set()
        self._updating = True
        for tid, cb in self.checkboxes.items():
            cb.blockSignals(True)
            cb.setChecked(tid in self.active_filters)
            cb.blockSignals(False)
        for cat_name in self.categories:
            self._update_category_state(cat_name)
        self._updating = False

    def mouseReleaseEvent(self, a0):
        if a0 is None:
            return
        action = self.actionAt(a0.pos())
        if isinstance(action, QWidgetAction) and action.defaultWidget() == self.scroll_area:
            return
        super().mouseReleaseEvent(a0)

    def _on_category_clicked(self, type_ids, cat_name):
        if self._updating:
            return
        self._updating = True

        checked_count = sum(1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked())
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)
        new_state = checked_count < total_count

        for tid in type_ids:
            if tid in self.checkboxes:
                cb = self.checkboxes[tid]
                cb.blockSignals(True)
                cb.setChecked(new_state)
                cb.blockSignals(False)
                if new_state:
                    self.active_filters.add(tid)
                else:
                    self.active_filters.discard(tid)

        self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)

    def _on_type_clicked(self, tid, cat_name, checked):
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

    def _update_category_state(self, cat_name):
        cat_cb = self.category_checkboxes[cat_name]
        type_ids = self.categories[cat_name]
        checked_count = sum(1 for tid in type_ids if tid in self.checkboxes and self.checkboxes[tid].isChecked())
        total_count = sum(1 for tid in type_ids if tid in self.checkboxes)

        cat_cb.blockSignals(True)
        if checked_count == 0:
            cat_cb.setCheckState(Qt.CheckState.Unchecked)
        elif checked_count == total_count and total_count > 0:
            cat_cb.setCheckState(Qt.CheckState.Checked)
        else:
            cat_cb.setCheckState(Qt.CheckState.PartiallyChecked)
        cat_cb.blockSignals(False)

    def _clear_all(self):
        if self._updating:
            return
        self._updating = True
        self.active_filters.clear()
        for cb in self.checkboxes.values():
            cb.blockSignals(True)
            cb.setChecked(False)
            cb.blockSignals(False)
        for cat_name in self.categories:
            self._update_category_state(cat_name)
        self._updating = False
        self.filters_changed.emit(self.active_filters)
