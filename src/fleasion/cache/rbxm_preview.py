"""RBXM/RBXMX structure preview widget."""

from __future__ import annotations

import base64
import gzip
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer
from PySide6.QtGui import QKeySequence, QMouseEvent, QResizeEvent, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
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
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..localization import tr, verbatim
from .roblox_document import classify_roblox_document

if TYPE_CHECKING:
    from .tools.solidmodel_converter.rbxm.types import (
        PropertyFormat,
        RbxDocument,
        RbxInstance,
        RbxRawChunk,
        RbxRawPropertyChunk,
    )


type NumberCaster = type[int] | type[float]


if TYPE_CHECKING:

    def _class_names() -> tuple[str, ...]: ...

    def _class_name_set() -> frozenset[str]: ...

    def _mouse_event(event: QEvent) -> QMouseEvent: ...

    def _tree_item(value: QTreeWidgetItem | None) -> QTreeWidgetItem: ...

    def _clear_current_item(tree: QTreeWidget) -> None: ...

    def _table_item(value: QTableWidgetItem | None) -> QTableWidgetItem: ...

    def _as_object_dict(value: object) -> dict[str, object] | None: ...

    def _as_object_list(value: object) -> list[object] | None: ...

    def _float_value(value: object) -> float: ...
else:
    from .roblox_class_names import ROBLOX_CLASS_NAME_SET, ROBLOX_CLASS_NAMES

    def _class_names() -> tuple[str, ...]:
        return ROBLOX_CLASS_NAMES

    def _class_name_set() -> frozenset[str]:
        return ROBLOX_CLASS_NAME_SET

    def _mouse_event(event: QEvent) -> QMouseEvent:
        return event

    def _tree_item(value: QTreeWidgetItem | None) -> QTreeWidgetItem:
        return value

    def _clear_current_item(tree: QTreeWidget) -> None:
        tree.setCurrentItem(None)

    def _table_item(value: QTableWidgetItem | None) -> QTableWidgetItem:
        return value

    def _as_object_dict(value: object) -> dict[str, object] | None:
        return value if isinstance(value, dict) else None

    def _as_object_list(value: object) -> list[object] | None:
        return value if isinstance(value, list) else None

    def _float_value(value: object) -> float:
        return float(value)


_COPY_VALUE_ROLE = Qt.ItemDataRole.UserRole
_ROW_KIND_ROLE = Qt.ItemDataRole.UserRole + 1
_PROP_OBJECT_ROLE = Qt.ItemDataRole.UserRole + 2


@dataclass
class PreviewProperty:
    name: str
    type_name: str
    value: object


@dataclass
class PreviewInstance:
    class_name: str
    referent: str
    name: str = ''
    properties: list[PreviewProperty] = field(default_factory=list[PreviewProperty])
    children: list[PreviewInstance] = field(default_factory=lambda: [])

    def label(self) -> str:
        if self.name:
            return self.name
        return self.class_name


@dataclass
class PreviewDocument:
    roots: list[PreviewInstance]
    instances: dict[str, PreviewInstance]
    metadata: dict[str, str] = field(default_factory=dict[str, str])
    shared_strings: list[bytes] = field(default_factory=list[bytes])
    raw_property_chunks: list[RbxRawPropertyChunk] = field(default_factory=lambda: [])
    raw_chunks: list[RbxRawChunk] = field(default_factory=lambda: [])


class ClassNameDialog(QDialog):
    """Searchable picker for Roblox engine class names."""

    def __init__(self, current: str = 'Folder', parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr('ui.cache.rbxm_preview.choose_classname'))
        self.resize(360, 460)
        self._selected = current if current in _class_name_set() else 'Folder'

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('ui.cache.rbxm_preview.search_classname'))
        self.search.textChanged.connect(self._populate)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget, stretch=1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._populate('')
        matches = self.list_widget.findItems(self._selected, Qt.MatchFlag.MatchExactly)
        if matches:
            self.list_widget.setCurrentItem(matches[0])
            self.list_widget.scrollToItem(matches[0])

    def _populate(self, query: str) -> None:
        query = query.strip().lower()
        current = self.selected_class_name()
        self.list_widget.clear()
        for class_name in _class_names():
            if query and query not in class_name.lower():
                continue
            self.list_widget.addItem(QListWidgetItem(class_name))
        if self.list_widget.count():
            matches = self.list_widget.findItems(current, Qt.MatchFlag.MatchExactly)
            self.list_widget.setCurrentItem(matches[0] if matches else self.list_widget.item(0))

    def selected_class_name(self) -> str:
        item = self.list_widget.currentItem() if hasattr(self, 'list_widget') else None
        if item is not None:
            return item.text()
        return self._selected


def is_rbx_model_data(data: bytes) -> bool:
    """Return True if bytes look like gzip-wrapped or plain RBXM/RBXMX."""
    return classify_roblox_document(data) in {'rbxm', 'rbxmx', 'rbxl'}


def _decompress_if_needed(data: bytes) -> bytes:
    if data.startswith(b'\x1f\x8b'):
        return gzip.decompress(data)
    return data


def _tag_name(elem: ET.Element) -> str:
    return elem.tag.rsplit('}', 1)[-1]


class RbxmPreviewWidget(QWidget):
    """Preview Roblox model structure and properties."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.document: PreviewDocument | None = None
        self._item_to_instance: dict[int, PreviewInstance] = {}
        self._instance_to_item: dict[int, QTreeWidgetItem] = {}
        self._matches: list[QTreeWidgetItem] = []
        self._match_index = -1
        self._dirty = False
        self._updating = False
        self._property_refresh_pending = False
        self._next_referent = 1
        self._asset_label = ''
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(QLabel(tr('ui.cache.rbxm_preview.search')))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            tr('ui.cache.rbxm_preview.search_instances_and_properties')
        )
        self.search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_input)

        self.prev_btn = QPushButton(tr('ui.cache.rbxm_preview.text'))
        self.prev_btn.setFixedWidth(30)
        self.prev_btn.setToolTip(tr('ui.cache.rbxm_preview.previous_match'))
        self.prev_btn.clicked.connect(self._prev_match)
        toolbar.addWidget(self.prev_btn)

        self.next_btn = QPushButton(tr('ui.cache.rbxm_preview.v'))
        self.next_btn.setFixedWidth(30)
        self.next_btn.setToolTip(tr('ui.cache.rbxm_preview.next_match'))
        self.next_btn.clicked.connect(self._next_match)
        toolbar.addWidget(self.next_btn)

        expand_btn = QPushButton(tr('ui.cache.rbxm_preview.expand_all'))
        expand_btn.clicked.connect(self.tree_expand_all)
        toolbar.addWidget(expand_btn)

        collapse_btn = QPushButton(tr('ui.cache.rbxm_preview.collapse_all'))
        collapse_btn.clicked.connect(self.tree_collapse_all)
        toolbar.addWidget(collapse_btn)

        self.copy_btn = QPushButton(tr('ui.cache.rbxm_preview.copy_value'))
        self.copy_btn.clicked.connect(self._copy_selected_value)
        toolbar.addWidget(self.copy_btn)

        self.modified_label = QLabel('')
        self.modified_label.setStyleSheet('color: #b58900; font-size: 11px;')
        toolbar.addWidget(self.modified_label)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left_pane = QWidget()
        left_layout = QVBoxLayout(left_pane)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.customContextMenuRequested.connect(self._show_tree_context_menu)
        self.tree.viewport().installEventFilter(self)
        left_layout.addWidget(self.tree, stretch=1)

        left_bottom = QHBoxLayout()
        self.add_child_btn = QPushButton(tr('ui.cache.rbxm_preview.text_2'))
        self.add_child_btn.setFixedWidth(34)
        self.add_child_btn.setToolTip(tr('ui.cache.rbxm_preview.add_child_to_selected_item_or_add'))
        self.add_child_btn.clicked.connect(self._add_child_from_button)
        left_bottom.addWidget(self.add_child_btn)
        left_bottom.addStretch()
        left_layout.addLayout(left_bottom)
        splitter.addWidget(left_pane)

        right_pane = QWidget()
        right_layout = QVBoxLayout(right_pane)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(4)
        self.properties_table = QTableWidget()
        self.properties_table.setColumnCount(3)
        self.properties_table.setHorizontalHeaderLabels(
            [
                tr('ui.cache.rbxm_preview.property'),
                tr('ui.cache.rbxm_preview.value'),
                tr('ui.cache.rbxm_preview.type'),
            ]
        )
        self.properties_table.setEditTriggers(
            QTableWidget.EditTrigger.DoubleClicked | QTableWidget.EditTrigger.EditKeyPressed
        )
        self.properties_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.properties_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.properties_table.setWordWrap(False)
        self.properties_table.verticalHeader().hide()
        self.properties_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.properties_table.itemChanged.connect(self._on_property_item_changed)
        self.properties_table.cellDoubleClicked.connect(self._on_property_cell_double_clicked)
        self.properties_table.customContextMenuRequested.connect(self._show_property_context_menu)
        self.copy_shortcut = QShortcut(
            QKeySequence(QKeySequence.StandardKey.Copy), self.properties_table
        )
        self.copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self._copy_selected_value)
        self.properties_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.properties_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionHidden(2, False)
        right_layout.addWidget(self.properties_table, stretch=1)

        right_bottom = QHBoxLayout()

        self.add_property_btn = QPushButton(tr('ui.cache.rbxm_preview.text_2'))
        self.add_property_btn.setFixedWidth(34)
        self.add_property_btn.setToolTip(tr('ui.cache.rbxm_preview.add_property_to_selected_item'))
        self.add_property_btn.clicked.connect(self._add_property)
        right_bottom.addWidget(self.add_property_btn)
        self.summary_label = QLabel('')
        self.summary_label.setStyleSheet('color: #888; font-size: 11px;')
        right_bottom.addWidget(self.summary_label)
        right_bottom.addStretch()

        right_layout.addLayout(right_bottom)
        splitter.addWidget(right_pane)

        splitter.setSizes([220, 500])
        layout.addWidget(splitter, stretch=1)
        self._update_type_column_visibility()

    def load_bytes(self, data: bytes, asset_label: str = '') -> None:
        """Load RBXM/RBXMX bytes into the preview."""
        data = _decompress_if_needed(data)
        self._dirty = False
        self._asset_label = asset_label
        if data.startswith(b'<roblox!'):
            doc = self._load_binary(data)
        elif data.lstrip().startswith(b'<roblox'):
            doc = self._load_xml(data)
        else:
            raise ValueError('Data is not an RBXM/RBXMX document')

        self.document = doc
        self._next_referent = self._compute_next_referent()
        self._populate_tree(asset_label)
        self._update_type_column_visibility()
        self._set_dirty(False)

    def load_document(
        self, document: PreviewDocument, asset_label: str = '', dirty: bool = True
    ) -> None:
        """Load an existing in-memory preview document."""
        self.document = document
        self._asset_label = asset_label
        self._next_referent = self._compute_next_referent()
        self._populate_tree(asset_label)
        self._update_type_column_visibility()
        self._set_dirty(dirty)

    def clear(self) -> None:
        self.document = None
        self._item_to_instance.clear()
        self._instance_to_item.clear()
        self._matches.clear()
        self._match_index = -1
        self.tree.clear()
        self.properties_table.setRowCount(0)
        self.search_input.clear()
        self.summary_label.clear()
        self.modified_label.clear()
        self._dirty = False
        self._asset_label = ''

    def tree_expand_all(self) -> None:
        self.tree.expandAll()

    def tree_collapse_all(self) -> None:
        self.tree.collapseAll()

    def eventFilter(self, source: QObject, event: QEvent) -> bool:
        if (
            source is self.tree.viewport()
            and event.type() == QEvent.Type.MouseButtonPress
            and self.tree.itemAt(_mouse_event(event).position().toPoint()) is None
        ):
            self.tree.clearSelection()
            _clear_current_item(self.tree)
            self.properties_table.setRowCount(0)
        return super().eventFilter(source, event)

    def _load_binary(self, data: bytes) -> PreviewDocument:
        from .tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer

        raw_doc = RbxmDeserializer().deserialize(data)
        instances: dict[str, PreviewInstance] = {}

        def convert(inst: RbxInstance) -> PreviewInstance:
            ref = str(inst.referent)
            existing = instances.get(ref)
            if existing is not None:
                return existing
            props: list[PreviewProperty] = []
            name = ''
            for prop_name, prop in inst.properties.items():
                type_name = prop.fmt.name
                prop_value: object = prop.value
                props.append(PreviewProperty(prop_name, type_name, prop_value))
                if prop_name == 'Name' and isinstance(prop_value, str):
                    name = prop_value
            preview = PreviewInstance(inst.class_name, ref, name, props)
            instances[ref] = preview
            preview.children = [convert(child) for child in inst.children]
            return preview

        roots = [convert(root) for root in raw_doc.roots]
        return PreviewDocument(
            roots=roots,
            instances=instances,
            metadata=dict(raw_doc.metadata.entries),
            shared_strings=list(raw_doc.shared_strings),
            raw_property_chunks=list(raw_doc.raw_property_chunks),
            raw_chunks=list(raw_doc.raw_chunks),
        )

    def _load_xml(self, data: bytes) -> PreviewDocument:
        root = ET.fromstring(data)
        shared_by_md5: dict[str, bytes] = {}
        shared_strings: list[bytes] = []
        ss_root = root.find('SharedStrings')
        if ss_root is not None:
            for shared in ss_root.findall('SharedString'):
                text = (shared.text or '').strip()
                blob = b''
                if text:
                    try:
                        blob = base64.b64decode(text)
                    except Exception:
                        blob = text.encode('utf-8', errors='replace')
                md5 = shared.get('md5') or ''
                if md5:
                    shared_by_md5[md5] = blob
                shared_strings.append(blob)

        instances: dict[str, PreviewInstance] = {}

        def parse_property(elem: ET.Element) -> PreviewProperty:
            prop_name = elem.get('name') or ''
            type_name = _tag_name(elem)
            text = elem.text or ''
            value: object
            if type_name == 'SharedString':
                value = shared_by_md5.get(text.strip(), b'')
            elif type_name in ('BinaryString', 'ProtectedString'):
                stripped = text.strip()
                if stripped:
                    try:
                        value = base64.b64decode(stripped)
                    except Exception:
                        value = stripped
                else:
                    value = b''
            elif list(elem):
                value = {_tag_name(child): (child.text or '').strip() for child in elem}
            else:
                value = text.strip()
            return PreviewProperty(prop_name, type_name, value)

        def parse_item(item: ET.Element) -> PreviewInstance:
            referent = item.get('referent') or ''
            inst = PreviewInstance(item.get('class') or 'Instance', referent)
            props_elem = item.find('Properties')
            if props_elem is not None:
                for prop_elem in props_elem:
                    prop = parse_property(prop_elem)
                    inst.properties.append(prop)
                    if prop.name == 'Name' and isinstance(prop.value, str):
                        inst.name = prop.value
            inst.children = [parse_item(child) for child in item.findall('Item')]
            if referent:
                instances[referent] = inst
            return inst

        roots = [parse_item(item) for item in root.findall('Item')]
        metadata = {
            elem.get('name') or _tag_name(elem): (elem.text or '').strip()
            for elem in root
            if _tag_name(elem) == 'Meta'
        }
        return PreviewDocument(
            roots=roots,
            instances=instances,
            metadata=metadata,
            shared_strings=shared_strings,
        )

    def _populate_tree(self, asset_label: str) -> None:
        self.tree.clear()
        self.properties_table.setRowCount(0)
        self._item_to_instance.clear()
        self._instance_to_item.clear()
        self._matches.clear()
        self._match_index = -1

        if self.document is None:
            return

        for root in self.document.roots:
            self._add_instance_item(None, root)

        instance_count = len(self.document.instances)
        root_count = len(self.document.roots)
        shared_count = len(self.document.shared_strings)
        suffix = f' | {asset_label}' if asset_label else ''
        self.summary_label.setText(
            tr(
                'ui.cache.rbxm_preview.value_roots_value_instances_value_shared_strings',
                value0=root_count,
                value1=instance_count,
                value2=shared_count,
                value3=suffix,
            )
        )

        if self.tree.topLevelItemCount():
            first = _tree_item(self.tree.topLevelItem(0))
            self.tree.setCurrentItem(first)
            first.setExpanded(True)
        self._on_search_changed(self.search_input.text())

    def _add_instance_item(
        self, parent: QTreeWidgetItem | None, inst: PreviewInstance
    ) -> QTreeWidgetItem:
        item = QTreeWidgetItem([inst.label()])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        if inst.referent:
            tip = f'{inst.class_name} | referent {inst.referent}'
            if inst.name:
                tip = f'{inst.name}\n{tip}'
            item.setToolTip(0, tip)
        self._item_to_instance[id(item)] = inst
        self._instance_to_item[id(inst)] = item
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in inst.children:
            self._add_instance_item(item, child)
        return item

    def _on_tree_selection_changed(
        self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None
    ) -> None:
        self.properties_table.setRowCount(0)
        if current is None:
            return
        inst = self._item_to_instance.get(id(current))
        if inst is None:
            return

        rows = [
            PreviewProperty('ClassName', 'Class', inst.class_name),
            PreviewProperty('Referent', 'RefId', inst.referent),
        ]
        rows.extend(inst.properties)

        self.properties_table.setRowCount(len(rows))
        self._updating = True
        for row, prop in enumerate(rows):
            value_text, tooltip = self._format_value(prop.value, prop.type_name)
            copy_text = self._copy_text_for_value(prop.value, prop.type_name)
            prop_item = QTableWidgetItem(prop.name)
            value_item = QTableWidgetItem(value_text)
            type_item = QTableWidgetItem(prop.type_name)
            prop_item.setData(_ROW_KIND_ROLE, 'synthetic' if row < 2 else 'property')
            prop_item.setData(_PROP_OBJECT_ROLE, prop if row >= 2 else None)
            value_item.setData(
                _ROW_KIND_ROLE,
                'class_name' if row == 0 else 'referent' if row == 1 else 'property',
            )
            value_item.setData(_PROP_OBJECT_ROLE, prop if row >= 2 else None)
            type_item.setData(_ROW_KIND_ROLE, 'synthetic' if row < 2 else 'property')
            type_item.setData(_PROP_OBJECT_ROLE, prop if row >= 2 else None)
            value_item.setData(_COPY_VALUE_ROLE, copy_text)
            if row < 2:
                prop_item.setFlags(prop_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                type_item.setFlags(type_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if row == 0:
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                value_item.setToolTip(
                    tr('ui.cache.rbxm_preview.double_click_to_choose_a_roblox_classname')
                )
            if row == 1:
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            elif row >= 2 and not self._value_is_editable(prop.value, prop.type_name):
                value_item.setFlags(value_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if tooltip:
                value_item.setToolTip(tooltip)
            self.properties_table.setItem(row, 0, prop_item)
            self.properties_table.setItem(row, 1, value_item)
            self.properties_table.setItem(row, 2, type_item)
        self._updating = False

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._updating or column != 0:
            return
        inst = self._item_to_instance.get(id(item))
        if inst is None:
            return
        new_name = item.text(0).strip()
        inst.name = new_name
        self._set_name_property(inst, new_name)
        self._refresh_selected_properties()
        self._set_dirty(True)

    def _on_property_item_changed(self, item: QTableWidgetItem) -> None:
        if self._updating:
            return
        inst = self._current_instance()
        if inst is None:
            return

        col = item.column()
        kind = item.data(_ROW_KIND_ROLE)

        if kind == 'class_name' and col == 1:
            class_name = item.text().strip() or verbatim('Folder')
            if class_name not in _class_name_set():
                QMessageBox.warning(
                    self,
                    tr('ui.cache.rbxm_preview.invalid_classname'),
                    tr(
                        'ui.cache.rbxm_preview.value_is_not_a_known_roblox_classname',
                        value0=class_name,
                    ),
                )
                self._refresh_selected_properties_later()
                return
            inst.class_name = class_name
            self._set_dirty(True)
            self._refresh_selected_properties_later()
            return

        if kind != 'property':
            return

        prop = item.data(_PROP_OBJECT_ROLE)
        if not isinstance(prop, PreviewProperty):
            return

        if col == 0:
            new_name = item.text().strip()
            if not new_name:
                self._refresh_selected_properties_later()
                return
            prop.name = new_name
            if new_name == 'Name':
                inst.name = str(prop.value)
                self._refresh_tree_label(inst)
            self._set_dirty(True)
        elif col == 1:
            prop.value = self._parse_edited_value(item.text(), prop.type_name, prop.value)
            if prop.name == 'Name':
                inst.name = '' if prop.value is None else str(prop.value)
                self._refresh_tree_label(inst)
            self._set_dirty(True)
            self._refresh_selected_properties_later()
            return
        elif col == 2:
            prop.type_name = item.text().strip() or 'STRING'
            prop.value = self._parse_edited_value(
                self._copy_text_for_value(prop.value, prop.type_name),
                prop.type_name,
                prop.value,
            )
            self._set_dirty(True)
        self._refresh_selected_properties_later()

    def _on_property_cell_double_clicked(self, row: int, column: int) -> None:
        if row == 0 and column == 1:
            self._change_current_class_name()

    def _change_current_class_name(self) -> None:
        inst = self._current_instance()
        if inst is None:
            return
        class_name = self._choose_class_name(inst.class_name)
        if not class_name or class_name == inst.class_name:
            return
        inst.class_name = class_name
        self._set_dirty(True)
        self._refresh_selected_properties()

    def _show_tree_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        current = self.tree.itemAt(position)
        if current is not None:
            self.tree.setCurrentItem(current)
        add_child_action = menu.addAction(tr('ui.cache.rbxm_preview.add_child'))
        rename_action = menu.addAction(tr('ui.cache.rbxm_preview.rename'))
        class_action = menu.addAction(tr('ui.cache.rbxm_preview.change_classname'))
        delete_action = menu.addAction(tr('ui.cache.rbxm_preview.delete'))
        if current is None:
            rename_action.setEnabled(False)
            class_action.setEnabled(False)
            delete_action.setEnabled(False)
        action = menu.exec(self.tree.viewport().mapToGlobal(position))
        if action == add_child_action:
            self._add_child(parent_item=current)
        elif action == rename_action and current is not None:
            self.tree.editItem(current, 0)
        elif action == class_action and current is not None:
            self._change_current_class_name()
        elif action == delete_action and current is not None:
            self._delete_instance(current)

    def _show_property_context_menu(self, position: QPoint) -> None:
        menu = QMenu(self)
        add_action = menu.addAction(tr('ui.cache.rbxm_preview.add_property'))
        copy_action = menu.addAction(tr('ui.cache.rbxm_preview.copy_value'))
        delete_action = menu.addAction(tr('ui.cache.rbxm_preview.delete'))

        row = self.properties_table.rowAt(position.y())
        if row >= 0:
            self.properties_table.selectRow(row)
        property_item = self.properties_table.item(row, 0) if row >= 0 else None
        kind = (
            _table_item(property_item).data(_ROW_KIND_ROLE) if property_item is not None else None
        )
        delete_action.setEnabled(kind == 'property')

        action = menu.exec(self.properties_table.viewport().mapToGlobal(position))
        if action == add_action:
            self._add_property()
        elif action == copy_action:
            self._copy_selected_value()
        elif action == delete_action and row >= 0:
            self._delete_property(row)

    def _add_child_from_button(self) -> None:
        self._add_child(parent_item=self.tree.currentItem())

    def _add_child(self, parent_item: QTreeWidgetItem | None) -> None:
        if self.document is None:
            return
        class_name = self._choose_class_name('Folder')
        if not class_name:
            return
        referent = self._new_referent()
        inst = PreviewInstance(
            class_name=class_name,
            referent=referent,
            name=class_name,
            properties=[
                PreviewProperty('Archivable', 'BOOL', True),
                PreviewProperty('Name', 'STRING', class_name),
            ],
        )

        parent = self._item_to_instance.get(id(parent_item)) if parent_item is not None else None
        if parent is None:
            self.document.roots.append(inst)
        else:
            parent.children.append(inst)
        self.document.instances[referent] = inst

        item = self._add_instance_item(parent_item, inst)
        item.setExpanded(True)
        self.tree.setCurrentItem(item)
        self.tree.editItem(item, 0)
        self._set_dirty(True)
        self._update_summary()

    def _choose_class_name(self, current: str = 'Folder') -> str | None:
        dialog = ClassNameDialog(current=current, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_class_name()

    def _delete_instance(self, item: QTreeWidgetItem) -> None:
        if self.document is None:
            return
        inst = self._item_to_instance.get(id(item))
        if inst is None:
            return
        if (
            QMessageBox.question(
                self,
                tr('ui.cache.rbxm_preview.delete_instance'),
                tr('ui.cache.rbxm_preview.delete_value_and_its_children', value0=inst.label()),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return

        parent_item = item.parent()
        parent_inst = (
            self._item_to_instance.get(id(parent_item)) if parent_item is not None else None
        )
        if parent_inst is None:
            self.document.roots = [root for root in self.document.roots if root is not inst]
        else:
            parent_inst.children = [child for child in parent_inst.children if child is not inst]
        self._remove_instance_refs(inst)

        if parent_item is None:
            index = self.tree.indexOfTopLevelItem(item)
            self.tree.takeTopLevelItem(index)
        else:
            parent_item.removeChild(item)
        self.properties_table.setRowCount(0)
        self._set_dirty(True)
        self._update_summary()

    def _add_property(self) -> None:
        inst = self._current_instance()
        if inst is None:
            return
        name, ok = QInputDialog.getText(
            self,
            tr('ui.cache.rbxm_preview.add_property'),
            tr('ui.cache.rbxm_preview.property_name'),
            text=tr('rbxm_preview.property.default_name'),
        )
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        default_type_label = tr('rbxm_preview.property.default_type_string')
        type_name, ok = QInputDialog.getText(
            self,
            tr('ui.cache.rbxm_preview.add_property'),
            tr('ui.cache.rbxm_preview.property_type'),
            text=default_type_label,
        )
        if not ok:
            return
        type_name = type_name.strip() or default_type_label
        if type_name == default_type_label:
            type_name = 'STRING'
        inst.properties.append(
            PreviewProperty(name, type_name, self._default_preview_value(type_name))
        )
        if name == 'Name':
            inst.name = str(inst.properties[-1].value)
            self._refresh_tree_label(inst)
        self._set_dirty(True)
        self._refresh_selected_properties()

    def _delete_property(self, row: int) -> None:
        inst = self._current_instance()
        if inst is None:
            return
        item = self.properties_table.item(row, 0)
        prop = item.data(_PROP_OBJECT_ROLE) if item is not None else None
        if not isinstance(prop, PreviewProperty):
            return
        inst.properties = [p for p in inst.properties if p is not prop]
        if prop.name == 'Name':
            inst.name = ''
            self._refresh_tree_label(inst)
        self._set_dirty(True)
        self._refresh_selected_properties()

    def _current_instance(self) -> PreviewInstance | None:
        current = self.tree.currentItem()
        if current is None:
            return None
        return self._item_to_instance.get(id(current))

    def _refresh_selected_properties(self) -> None:
        self._property_refresh_pending = False
        current = self.tree.currentItem()
        self._on_tree_selection_changed(current, current)

    def _refresh_selected_properties_later(self) -> None:
        if self._property_refresh_pending:
            return
        self._property_refresh_pending = True
        QTimer.singleShot(0, self._refresh_selected_properties)

    def _refresh_tree_label(self, inst: PreviewInstance) -> None:
        item = self._instance_to_item.get(id(inst))
        if item is None:
            return
        self._updating = True
        item.setText(0, inst.label())
        self._updating = False

    def _set_name_property(self, inst: PreviewInstance, name: str) -> None:
        for prop in inst.properties:
            if prop.name == 'Name':
                prop.value = name
                prop.type_name = prop.type_name or 'STRING'
                return
        inst.properties.append(PreviewProperty('Name', 'STRING', name))

    def _remove_instance_refs(self, inst: PreviewInstance) -> None:
        if self.document is None:
            return
        self.document.instances.pop(inst.referent, None)
        for child in inst.children:
            self._remove_instance_refs(child)

    def _new_referent(self) -> str:
        while str(self._next_referent) in (self.document.instances if self.document else {}):
            self._next_referent += 1
        referent = str(self._next_referent)
        self._next_referent += 1
        return referent

    def _compute_next_referent(self) -> int:
        if self.document is None:
            return 1
        values: list[int] = []
        for ref in self.document.instances:
            try:
                values.append(int(ref))
            except ValueError:
                continue
        return (max(values) + 1) if values else 1

    def _update_summary(self) -> None:
        if self.document is None:
            return
        suffix = f' | {self._asset_label}' if self._asset_label else ''
        self.summary_label.setText(
            tr(
                'ui.cache.rbxm_preview.value_roots_value_instances_value_shared_strings',
                value0=len(self.document.roots),
                value1=len(self.document.instances),
                value2=len(self.document.shared_strings),
                value3=suffix,
            )
        )

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.modified_label.setText(tr('ui.cache.rbxm_preview.modified') if dirty else '')

    def is_modified(self) -> bool:
        return self._dirty

    def export_rbxm_bytes(self, document: PreviewDocument | None = None) -> bytes:
        """Serialize the current edited preview as binary RBXM bytes."""
        from .tools.solidmodel_converter.rbxm.serializer import write_rbxm

        return write_rbxm(self._to_rbx_document(document))

    def export_rbxmx_bytes(self, document: PreviewDocument | None = None) -> bytes:
        """Serialize the current edited preview as RBXMX XML bytes."""
        from .tools.solidmodel_converter.rbxm.xml_writer import write_rbxmx

        return write_rbxmx(self._to_rbx_document(document))

    def _to_rbx_document(self, document: PreviewDocument | None = None) -> RbxDocument:
        source_doc = document or self.document
        if source_doc is None:
            raise ValueError('No RBXM/RBXMX document is loaded')

        from .tools.solidmodel_converter.rbxm.types import (
            RbxDocument,
            RbxInstance,
            RbxMetadata,
            RbxProperty,
        )

        ref_map: dict[str, int] = {}
        next_ref = 1

        def mapped_ref(ref: str) -> int:
            nonlocal next_ref
            if ref in ref_map:
                return ref_map[ref]
            try:
                value = int(ref)
            except ValueError:
                while next_ref in ref_map.values():
                    next_ref += 1
                value = next_ref
                next_ref += 1
            ref_map[ref] = value
            return value

        instances: dict[int, RbxInstance] = {}

        def convert(inst: PreviewInstance) -> RbxInstance:
            referent = mapped_ref(inst.referent)
            rbx_inst = RbxInstance(class_name=inst.class_name or 'Folder', referent=referent)
            instances[referent] = rbx_inst
            for prop in inst.properties:
                fmt = self._property_format_from_type_name(prop.type_name)
                if fmt is None:
                    continue
                rbx_inst.properties[prop.name] = RbxProperty(
                    name=prop.name,
                    fmt=fmt,
                    value=self._value_for_format(prop.value, fmt, mapped_ref),
                )
            rbx_inst.children = [convert(child) for child in inst.children]
            return rbx_inst

        roots = [convert(root) for root in source_doc.roots]
        return RbxDocument(
            version=0,
            type_count=0,
            object_count=len(instances),
            metadata=RbxMetadata(entries=dict(source_doc.metadata)),
            instances=instances,
            roots=roots,
            shared_strings=list(source_doc.shared_strings),
            raw_property_chunks=list(source_doc.raw_property_chunks),
            raw_chunks=list(source_doc.raw_chunks),
        )

    @staticmethod
    def _property_format_from_type_name(type_name: str) -> PropertyFormat | None:
        from .tools.solidmodel_converter.rbxm.types import (
            PROPERTY_FORMAT_TO_XML_TAG,
            PropertyFormat,
        )

        normalized = type_name.strip()
        if not normalized:
            return PropertyFormat.STRING
        upper = normalized.upper()
        if upper in PropertyFormat.__members__:
            return PropertyFormat[upper]

        tag_to_format = {tag.lower(): fmt for fmt, tag in PROPERTY_FORMAT_TO_XML_TAG.items()}
        aliases = {
            'class': None,
            'refid': None,
            'binarystring': PropertyFormat.STRING,
            'protectedstring': PropertyFormat.STRING,
            'content': PropertyFormat.CONTENT,
            'token': PropertyFormat.ENUM,
            'optionalcoordinateframe': PropertyFormat.OPTIONAL_CFRAME,
            'uniqueid': PropertyFormat.UNIQUE_ID,
            'securitycapabilities': PropertyFormat.SECURITY_CAPABILITIES,
        }
        key = normalized.lower()
        if key in aliases:
            return aliases[key]
        return tag_to_format.get(key, PropertyFormat.STRING)

    def _value_for_format(
        self,
        value: object,
        fmt: PropertyFormat,
        ref_mapper: Callable[[str], int] | None = None,
    ) -> object:
        from .tools.solidmodel_converter.rbxm.types import PropertyFormat

        if fmt in {
            PropertyFormat.INT,
            PropertyFormat.ENUM,
            PropertyFormat.BRICK_COLOR,
            PropertyFormat.SECURITY_CAPABILITIES,
        }:
            return self._safe_int(value)
        if fmt == PropertyFormat.INT64:
            return self._safe_int(value)
        if fmt in {PropertyFormat.FLOAT, PropertyFormat.DOUBLE}:
            return self._safe_float(value)
        if fmt == PropertyFormat.BOOL:
            return self._safe_bool(value)
        if fmt == PropertyFormat.REF:
            if value is None:
                return None
            value_map = _as_object_dict(value)
            if value_map is not None:
                value = value_map.get('Ref') or value_map.get('referent') or value_map.get('id')
            text = str(value or '').strip()
            if text in {'', 'None', '-1', 'null'}:
                return None
            if '->' in text:
                text = text.split('->', 1)[0].strip()
            if ref_mapper is not None:
                return ref_mapper(text)
            return self._safe_int(text)
        if fmt == PropertyFormat.UNIQUE_ID:
            value_map = _as_object_dict(value)
            if value_map is not None:
                return value_map
            if isinstance(value, bytes):
                return value
            text = str(value).strip().replace('-', '')
            if len(text) == 32:
                try:
                    xml_random = int(text[:16], 16)
                    random_bits = (xml_random >> 1) | ((xml_random & 1) << 63)
                    return {
                        'Index': int(text[24:32], 16),
                        'Time': int(text[16:24], 16),
                        'Random': random_bits,
                    }
                except ValueError:
                    pass
            return {'Index': 0, 'Time': 0, 'Random': 0}
        if fmt == PropertyFormat.CONTENT:
            value_map = _as_object_dict(value)
            if value_map is not None:
                uri = value_map.get('Uri') or value_map.get('uri') or value_map.get('url')
                if uri:
                    return {'SourceType': 'Uri', 'Uri': str(uri)}
                ref = value_map.get('Ref')
                if ref is not None:
                    return {
                        'SourceType': 'Object',
                        'Ref': ref_mapper(str(ref))
                        if ref_mapper is not None
                        else self._safe_int(ref),
                    }
                if 'null' in value_map:
                    return None
                return value_map
            if value is None:
                return value
            text = str(value)
            return {'SourceType': 'Uri', 'Uri': text} if text else None
        if fmt == PropertyFormat.UDIM:
            return self._parse_udim_value(value)
        if fmt == PropertyFormat.UDIM2:
            return self._parse_udim2_value(value)
        if fmt == PropertyFormat.RAY:
            return self._parse_ray_value(value)
        if fmt == PropertyFormat.COLOR3:
            return self._parse_vector_value(value, ('R', 'G', 'B'), float)
        if fmt == PropertyFormat.VECTOR2:
            return self._parse_vector_value(value, ('X', 'Y'), float)
        if fmt == PropertyFormat.VECTOR3:
            return self._parse_vector_value(value, ('X', 'Y', 'Z'), float)
        if fmt == PropertyFormat.VECTOR2INT16:
            return self._parse_vector_value(value, ('X', 'Y'), int)
        if fmt == PropertyFormat.VECTOR3INT16:
            return self._parse_vector_value(value, ('X', 'Y', 'Z'), int)
        if fmt in {
            PropertyFormat.CFRAME_MATRIX,
            PropertyFormat.CFRAME_QUAT,
            PropertyFormat.OPTIONAL_CFRAME,
        }:
            return self._parse_cframe_value(value)
        if fmt == PropertyFormat.NUMBER_RANGE:
            return self._parse_number_range_value(value)
        if fmt == PropertyFormat.RECT2D:
            return self._parse_rect2d_value(value)
        if fmt == PropertyFormat.PHYSICAL_PROPERTIES:
            return self._parse_physical_properties_value(value)
        if fmt == PropertyFormat.COLOR3UINT8:
            return self._parse_vector_value(value, ('R', 'G', 'B'), int)
        if fmt == PropertyFormat.FONT:
            return self._parse_font_value(value)
        return value

    def _value_is_editable(self, value: object, type_name: str) -> bool:
        from .tools.solidmodel_converter.rbxm.types import PropertyFormat

        fmt = self._property_format_from_type_name(type_name)
        if fmt is None:
            return True
        if fmt in {
            PropertyFormat.SHARED_STRING,
            PropertyFormat.BYTECODE,
            PropertyFormat.NUMBER_SEQUENCE,
            PropertyFormat.COLOR_SEQUENCE,
        }:
            return False
        if isinstance(value, bytes):
            return False
        return True

    def _parse_edited_value(self, text: str, type_name: str, old_value: object) -> object:
        if not self._value_is_editable(old_value, type_name):
            return old_value
        fmt = self._property_format_from_type_name(type_name)
        if fmt is None:
            return text
        from .tools.solidmodel_converter.rbxm.types import PropertyFormat

        if fmt in {
            PropertyFormat.CFRAME_MATRIX,
            PropertyFormat.CFRAME_QUAT,
            PropertyFormat.OPTIONAL_CFRAME,
        }:
            return self._parse_cframe_value(text, old_value)
        return self._value_for_format(text, fmt)

    @staticmethod
    def _default_preview_value(type_name: str) -> object:
        key = type_name.strip().upper()
        compact_key = key.replace('_', '')
        if key in {'BOOL'}:
            return False
        if key in {
            'INT',
            'ENUM',
            'BRICK_COLOR',
            'INT64',
            'SECURITY_CAPABILITIES',
        } or compact_key in {
            'BRICKCOLOR',
            'SECURITYCAPABILITIES',
        }:
            return 0
        if key in {'FLOAT', 'DOUBLE'}:
            return 0.0
        if key in {'UDIM'}:
            return {'S': 0.0, 'O': 0}
        if key in {'UDIM2'}:
            return {'XS': 0.0, 'XO': 0, 'YS': 0.0, 'YO': 0}
        if key in {'COLOR3'}:
            return {'R': 0.0, 'G': 0.0, 'B': 0.0}
        if key in {'VECTOR2'}:
            return {'X': 0.0, 'Y': 0.0}
        if key in {'VECTOR3'}:
            return {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
        if compact_key in {'VECTOR2INT16'}:
            return {'X': 0, 'Y': 0}
        if compact_key in {'VECTOR3INT16'}:
            return {'X': 0, 'Y': 0, 'Z': 0}
        if compact_key in {'COLOR3UINT8'}:
            return {'R': 0, 'G': 0, 'B': 0}
        if compact_key in {'NUMBERRANGE'}:
            return {'Min': 0.0, 'Max': 1.0}
        if key in {'RECT2D'}:
            return {'min': {'X': 0.0, 'Y': 0.0}, 'max': {'X': 0.0, 'Y': 0.0}}
        if compact_key in {
            'CFRAMEMATRIX',
            'CFRAMEQUAT',
            'OPTIONALCFRAME',
            'COORDINATEFRAME',
        }:
            return {
                'X': 0.0,
                'Y': 0.0,
                'Z': 0.0,
                'R00': 1.0,
                'R01': 0.0,
                'R02': 0.0,
                'R10': 0.0,
                'R11': 1.0,
                'R12': 0.0,
                'R20': 0.0,
                'R21': 0.0,
                'R22': 1.0,
            }
        if key in {'FONT'}:
            return {'Family': '', 'Weight': 400, 'Style': 0, 'CachedFaceId': ''}
        return ''

    def _parse_udim_value(self, value: object) -> dict[str, float | int]:
        value_map = _as_object_dict(value)
        if value_map is not None:
            return {
                'S': self._safe_float(value_map.get('S', 0.0)),
                'O': self._safe_int(value_map.get('O', 0)),
            }
        pairs = self._parse_key_values(str(value))
        if pairs:
            return {
                'S': self._safe_float(pairs.get('S', 0.0)),
                'O': self._safe_int(pairs.get('O', 0)),
            }
        numbers = self._parse_numbers(str(value))
        return {
            'S': numbers[0] if len(numbers) > 0 else 0.0,
            'O': int(numbers[1]) if len(numbers) > 1 else 0,
        }

    def _parse_udim2_value(self, value: object) -> dict[str, float | int]:
        value_map = _as_object_dict(value)
        pairs: dict[str, object] | dict[str, str]
        if value_map is not None:
            pairs = value_map
        else:
            pairs = self._parse_key_values(str(value))
        if pairs:
            return {
                'XS': self._safe_float(pairs.get('XS', 0.0)),
                'XO': self._safe_int(pairs.get('XO', 0)),
                'YS': self._safe_float(pairs.get('YS', 0.0)),
                'YO': self._safe_int(pairs.get('YO', 0)),
            }
        numbers = self._parse_numbers(str(value))
        return {
            'XS': numbers[0] if len(numbers) > 0 else 0.0,
            'XO': int(numbers[1]) if len(numbers) > 1 else 0,
            'YS': numbers[2] if len(numbers) > 2 else 0.0,
            'YO': int(numbers[3]) if len(numbers) > 3 else 0,
        }

    def _parse_vector_value(
        self, value: object, keys: tuple[str, ...], caster: NumberCaster
    ) -> dict[str, int | float]:
        value_map = _as_object_dict(value)
        pairs: dict[str, object] | dict[str, str]
        if value_map is not None:
            pairs = value_map
        else:
            pairs = self._parse_key_values(str(value))
        if pairs:
            return {key: self._cast_number(pairs.get(key, 0), caster) for key in keys}
        numbers = self._parse_numbers(str(value))
        return {
            key: self._cast_number(numbers[index] if index < len(numbers) else 0, caster)
            for index, key in enumerate(keys)
        }

    def _parse_ray_value(self, value: object) -> dict[str, dict[str, float]]:
        value_map = _as_object_dict(value)
        if value_map is not None:
            origin = self._parse_vector_value(value_map.get('origin', {}), ('X', 'Y', 'Z'), float)
            direction = self._parse_vector_value(
                value_map.get('direction', {}), ('X', 'Y', 'Z'), float
            )
            return {'origin': origin, 'direction': direction}
        numbers = self._parse_numbers(str(value))
        padded = numbers + [0.0] * max(0, 6 - len(numbers))
        return {
            'origin': {'X': padded[0], 'Y': padded[1], 'Z': padded[2]},
            'direction': {'X': padded[3], 'Y': padded[4], 'Z': padded[5]},
        }

    def _parse_cframe_value(
        self, value: object, old_value: object = None
    ) -> dict[str, float] | None:
        text = str(value).strip()
        if value is None or text.lower() in {'', 'none', 'null'}:
            return None

        result = {
            'X': 0.0,
            'Y': 0.0,
            'Z': 0.0,
            'R00': 1.0,
            'R01': 0.0,
            'R02': 0.0,
            'R10': 0.0,
            'R11': 1.0,
            'R12': 0.0,
            'R20': 0.0,
            'R21': 0.0,
            'R22': 1.0,
        }
        old_map = _as_object_dict(old_value)
        if old_map is not None:
            result.update({key: self._safe_float(old_map.get(key, result[key])) for key in result})
        value_map = _as_object_dict(value)
        if value_map is not None:
            result.update(
                {key: self._safe_float(value_map.get(key, result[key])) for key in result}
            )
            return result

        pairs = self._parse_key_values(text)
        if pairs:
            for key in result:
                if key in pairs:
                    result[key] = self._safe_float(pairs[key])
            return result

        numbers = self._parse_numbers(text)
        if len(numbers) >= 12:
            for key, number in zip(result, numbers[:12], strict=False):
                result[key] = number
        elif len(numbers) >= 3:
            result['X'], result['Y'], result['Z'] = numbers[:3]
        return result

    def _parse_number_range_value(self, value: object) -> dict[str, float]:
        value_map = _as_object_dict(value)
        if value_map is not None:
            return {
                'Min': self._safe_float(value_map.get('Min', 0.0)),
                'Max': self._safe_float(value_map.get('Max', 0.0)),
            }
        pairs = self._parse_key_values(str(value))
        if pairs:
            return {
                'Min': self._safe_float(pairs.get('Min', 0.0)),
                'Max': self._safe_float(pairs.get('Max', 0.0)),
            }
        numbers = self._parse_numbers(str(value))
        return {
            'Min': numbers[0] if len(numbers) > 0 else 0.0,
            'Max': numbers[1] if len(numbers) > 1 else 0.0,
        }

    def _parse_rect2d_value(self, value: object) -> dict[str, dict[str, float]]:
        value_map = _as_object_dict(value)
        if value_map is not None:
            return {
                'min': self._parse_vector_value(value_map.get('min', {}), ('X', 'Y'), float),
                'max': self._parse_vector_value(value_map.get('max', {}), ('X', 'Y'), float),
            }
        numbers = self._parse_numbers(str(value))
        padded = numbers + [0.0] * max(0, 4 - len(numbers))
        return {
            'min': {'X': padded[0], 'Y': padded[1]},
            'max': {'X': padded[2], 'Y': padded[3]},
        }

    def _parse_physical_properties_value(self, value: object) -> dict[str, bool | float] | None:
        text = str(value).strip()
        if value is None or text.lower() in {'', 'none', 'null', 'default'}:
            return None
        value_map = _as_object_dict(value)
        if value_map is not None:
            return {
                'CustomPhysics': self._safe_bool(value_map.get('CustomPhysics', True)),
                'Density': self._safe_float(value_map.get('Density', 0.0)),
                'Friction': self._safe_float(value_map.get('Friction', 0.0)),
                'Elasticity': self._safe_float(value_map.get('Elasticity', 0.0)),
                'FrictionWeight': self._safe_float(value_map.get('FrictionWeight', 0.0)),
                'ElasticityWeight': self._safe_float(value_map.get('ElasticityWeight', 0.0)),
                'AcousticAbsorption': self._safe_float(value_map.get('AcousticAbsorption', 1.0)),
            }
        pairs = self._parse_key_values(text)
        if pairs:
            return self._parse_physical_properties_value(pairs)
        numbers = self._parse_numbers(text)
        if len(numbers) < 5:
            return None
        value_dict: dict[str, bool | float] = {
            'CustomPhysics': True,
            'Density': numbers[0],
            'Friction': numbers[1],
            'Elasticity': numbers[2],
            'FrictionWeight': numbers[3],
            'ElasticityWeight': numbers[4],
        }
        if len(numbers) > 5:
            value_dict['AcousticAbsorption'] = numbers[5]
        return value_dict

    def _parse_font_value(self, value: object) -> dict[str, str | int]:
        value_map = _as_object_dict(value)
        if value_map is not None:
            return {
                'Family': str(value_map.get('Family', '')),
                'Weight': self._safe_int(value_map.get('Weight', 400)),
                'Style': self._safe_int(value_map.get('Style', 0)),
                'CachedFaceId': str(value_map.get('CachedFaceId', '')),
            }
        pairs = self._parse_key_values(str(value))
        if pairs:
            return self._parse_font_value(pairs)
        parts = [part.strip() for part in str(value).split(',')]
        return {
            'Family': parts[0] if len(parts) > 0 else '',
            'Weight': self._safe_int(parts[1] if len(parts) > 1 else 400),
            'Style': self._safe_int(parts[2] if len(parts) > 2 else 0),
            'CachedFaceId': parts[3] if len(parts) > 3 else '',
        }

    @staticmethod
    def _parse_key_values(text: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for part in re.split(r'[,;]\s*', text.strip().strip('[]{}()')):
            if '=' in part:
                key, raw_value = part.split('=', 1)
            elif ':' in part:
                key, raw_value = part.split(':', 1)
            else:
                continue
            key = key.strip().strip('"\'{}[]()')
            raw_value = raw_value.strip().strip('"\'{}[]()')
            if key:
                result[key] = raw_value
        return result

    @staticmethod
    def _parse_numbers(text: str) -> list[float]:
        return [
            float(match.group(0))
            for match in re.finditer(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', text)
        ]

    @staticmethod
    def _cast_number(value: object, caster: NumberCaster) -> int | float:
        number = _float_value(value)
        if caster is int:
            return int(round(number))
        return number

    @staticmethod
    def _safe_int(value: object) -> int:
        try:
            return int(str(value).strip(), 0)
        except TypeError, ValueError:
            return 0

    @staticmethod
    def _safe_float(value: object) -> float:
        try:
            return _float_value(value)
        except TypeError, ValueError:
            return 0.0

    @staticmethod
    def _safe_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    def _format_value(self, value: object, type_name: str) -> tuple[str, str]:
        if isinstance(value, bytes):
            size = len(value)
            if size == 0:
                return '<empty>', ''
            utf_preview = ''
            try:
                decoded = value.decode('utf-8')
                if decoded and all(ch.isprintable() or ch.isspace() for ch in decoded[:200]):
                    utf_preview = ' | text: ' + _compact(decoded, 120)
            except UnicodeDecodeError:
                pass
            b64 = base64.b64encode(value[:48]).decode('ascii')
            hex_prefix = value[:32].hex(' ')
            return f'<{size} bytes>{utf_preview} | base64: {b64}', f'hex: {hex_prefix}'

        value_map = _as_object_dict(value)
        if value_map is not None:
            if type_name == 'Ref':
                target = self._resolve_ref(value_map)
                if target:
                    return target, target
            compact_vector = _format_vector_like(value_map)
            if compact_vector:
                return compact_vector, compact_vector
            parts = [f'{k}={_compact(str(v), 60)}' for k, v in value_map.items()]
            text = ', '.join(parts)
            return text, text

        value_list = _as_object_list(value)
        if value_list is not None:
            text = '[' + ', '.join(_format_scalar(v) for v in value_list[:8])
            if len(value_list) > 8:
                text += f', ... +{len(value_list) - 8}'
            text += ']'
            return text, str(value_list)

        if isinstance(value, (bool, int, float)) or type_name.lower() in {
            'bool',
            'int',
            'int64',
            'float',
            'double',
        }:
            compact_scalar = _format_scalar(value)
            return compact_scalar, compact_scalar

        text = '' if value is None else str(value)
        if type_name == 'Ref':
            target = self._resolve_ref(text)
            if target:
                return target, target
        compact = _compact(text, 500)
        tooltip = text if len(text) > len(compact) else ''
        return compact, tooltip

    def _copy_text_for_value(self, value: object, type_name: str) -> str:
        if isinstance(value, bytes):
            if not value:
                return ''
            try:
                decoded = value.decode('utf-8')
            except UnicodeDecodeError:
                return base64.b64encode(value).decode('ascii')
            if decoded and all(ch.isprintable() or ch.isspace() for ch in decoded):
                return html.unescape(decoded)
            return base64.b64encode(value).decode('ascii')

        value_map = _as_object_dict(value)
        if value_map is not None:
            if type_name == 'Ref':
                target = self._resolve_ref(value_map)
                if target:
                    return target
            compact_vector = _format_vector_like(value_map)
            if compact_vector:
                return compact_vector
            return ', '.join(
                f'{k}={self._copy_text_for_value(v, "")}' for k, v in value_map.items()
            )

        value_list = _as_object_list(value)
        if value_list is not None:
            return '[' + ', '.join(self._copy_text_for_value(v, '') for v in value_list) + ']'

        if isinstance(value, (bool, int, float)) or type_name.lower() in {
            'bool',
            'int',
            'int64',
            'float',
            'double',
        }:
            return _format_scalar(value)

        text = '' if value is None else str(value)
        if type_name == 'Ref':
            target = self._resolve_ref(text)
            if target:
                return target
        return html.unescape(text)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_type_column_visibility()

    def _update_type_column_visibility(self) -> None:
        self.properties_table.horizontalHeader().setSectionHidden(2, False)

    def _resolve_ref(self, value: object) -> str:
        if self.document is None:
            return ''
        ref = ''
        value_map = _as_object_dict(value)
        if value_map is not None:
            ref = str(
                value_map.get('Ref') or value_map.get('referent') or value_map.get('id') or ''
            )
        else:
            ref = str(value or '')
        if not ref or ref in ('None', '-1', 'null'):
            return ref
        target = self.document.instances.get(ref)
        if target is None:
            return ref
        return f'{ref} -> {target.label()}'

    def _on_search_changed(self, text: str) -> None:
        query = text.strip().lower()
        self._matches = []
        self._match_index = -1

        def walk(item: QTreeWidgetItem) -> None:
            inst = self._item_to_instance.get(id(item))
            haystack = item.text(0)
            if inst is not None:
                for prop in inst.properties:
                    value_text, _ = self._format_value(prop.value, prop.type_name)
                    haystack += f' {prop.name} {value_text} {prop.type_name}'
            matched = bool(query and query in haystack.lower())
            font = item.font(0)
            font.setBold(matched)
            item.setFont(0, font)
            if matched:
                self._matches.append(item)
            for i in range(item.childCount()):
                walk(_tree_item(item.child(i)))

        for i in range(self.tree.topLevelItemCount()):
            walk(_tree_item(self.tree.topLevelItem(i)))

        if query and self._matches:
            self._match_index = 0
            self._select_match()
        self._update_match_label()

    def _select_match(self) -> None:
        if not self._matches or self._match_index < 0:
            return
        item = self._matches[self._match_index]
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def _next_match(self) -> None:
        if not self._matches:
            return
        self._match_index = (self._match_index + 1) % len(self._matches)
        self._select_match()
        self._update_match_label()

    def _prev_match(self) -> None:
        if not self._matches:
            return
        self._match_index = (self._match_index - 1) % len(self._matches)
        self._select_match()
        self._update_match_label()

    def _update_match_label(self) -> None:
        if self.search_input.text().strip():
            if self._matches:
                self.summary_label.setText(
                    tr(
                        'ui.cache.rbxm_preview.match_value_value',
                        value0=self._match_index + 1,
                        value1=len(self._matches),
                    )
                )
            else:
                self.summary_label.setText(tr('ui.cache.rbxm_preview.no_matches'))
        elif self.document is not None:
            self.summary_label.setText(
                tr(
                    'ui.cache.rbxm_preview.value_roots_value_instances_value_shared_strings_2',
                    value0=len(self.document.roots),
                    value1=len(self.document.instances),
                    value2=len(self.document.shared_strings),
                )
            )

    def _copy_selected_value(self) -> None:
        row = self.properties_table.currentRow()
        if row < 0:
            return
        value_item = self.properties_table.item(row, 1)
        if value_item is not None:
            copy_text = value_item.data(_COPY_VALUE_ROLE)
            QApplication.clipboard().setText(
                str(copy_text if copy_text is not None else value_item.text())
            )


def _compact(text: str, limit: int) -> str:
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + '...'


def _format_scalar(value: object) -> str:
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, str) and value.lower() in {'true', 'false'}:
        return 'True' if value.lower() == 'true' else 'False'
    if isinstance(value, int):
        return str(value)
    try:
        number = _float_value(value)
    except TypeError, ValueError:
        return _compact(str(value), 80)
    if abs(number) < 1e-8:
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return f'{number:.6g}'


def _format_vector_like(value: dict[str, object]) -> str:
    key_sets = (
        ('X', 'Y', 'Z'),
        ('x', 'y', 'z'),
        ('R', 'G', 'B'),
        ('r', 'g', 'b'),
    )
    for keys in key_sets:
        if all(k in value for k in keys):
            return ', '.join(_format_scalar(value[k]) for k in keys)
    return ''
