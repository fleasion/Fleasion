"""RBXM/RBXMX structure preview widget."""

from __future__ import annotations

import base64
import gzip
import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


_COPY_VALUE_ROLE = Qt.ItemDataRole.UserRole


@dataclass
class PreviewProperty:
    name: str
    type_name: str
    value: Any


@dataclass
class PreviewInstance:
    class_name: str
    referent: str
    name: str = ''
    properties: list[PreviewProperty] = field(default_factory=list)
    children: list['PreviewInstance'] = field(default_factory=list)

    def label(self) -> str:
        if self.name:
            return self.name
        return self.class_name


@dataclass
class PreviewDocument:
    roots: list[PreviewInstance]
    instances: dict[str, PreviewInstance]
    metadata: dict[str, str] = field(default_factory=dict)
    shared_strings: list[bytes] = field(default_factory=list)


def is_rbx_model_data(data: bytes) -> bool:
    """Return True if bytes look like gzip-wrapped or plain RBXM/RBXMX."""
    try:
        data = _decompress_if_needed(data)
    except Exception:
        return False
    if data.startswith(b'<roblox!'):
        return True
    return data.lstrip().startswith(b'<roblox')


def _decompress_if_needed(data: bytes) -> bytes:
    if data.startswith(b'\x1f\x8b'):
        return gzip.decompress(data)
    return data


def _tag_name(elem: ET.Element) -> str:
    return elem.tag.rsplit('}', 1)[-1]


class RbxmPreviewWidget(QWidget):
    """Preview Roblox model structure and properties."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.document: PreviewDocument | None = None
        self._item_to_instance: dict[int, PreviewInstance] = {}
        self._matches: list[QTreeWidgetItem] = []
        self._match_index = -1
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(QLabel('Search:'))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Search instances and properties...')
        self.search_input.textChanged.connect(self._on_search_changed)
        toolbar.addWidget(self.search_input)

        self.prev_btn = QPushButton('^')
        self.prev_btn.setFixedWidth(30)
        self.prev_btn.setToolTip('Previous match')
        self.prev_btn.clicked.connect(self._prev_match)
        toolbar.addWidget(self.prev_btn)

        self.next_btn = QPushButton('v')
        self.next_btn.setFixedWidth(30)
        self.next_btn.setToolTip('Next match')
        self.next_btn.clicked.connect(self._next_match)
        toolbar.addWidget(self.next_btn)

        expand_btn = QPushButton('Expand All')
        expand_btn.clicked.connect(self.tree_expand_all)
        toolbar.addWidget(expand_btn)

        collapse_btn = QPushButton('Collapse All')
        collapse_btn.clicked.connect(self.tree_collapse_all)
        toolbar.addWidget(collapse_btn)

        self.copy_btn = QPushButton('Copy Value')
        self.copy_btn.clicked.connect(self._copy_selected_value)
        toolbar.addWidget(self.copy_btn)

        toolbar.addStretch()
        self.summary_label = QLabel('')
        self.summary_label.setStyleSheet('color: #888; font-size: 11px;')
        toolbar.addWidget(self.summary_label)
        layout.addLayout(toolbar)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(10)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)
        splitter.addWidget(self.tree)

        self.properties_table = QTableWidget()
        self.properties_table.setColumnCount(3)
        self.properties_table.setHorizontalHeaderLabels(['Property', 'Value', 'Type'])
        self.properties_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.properties_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.properties_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.properties_table.setWordWrap(False)
        self.properties_table.verticalHeader().hide()
        self.copy_shortcut = QShortcut(QKeySequence(QKeySequence.StandardKey.Copy), self.properties_table)
        self.copy_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.copy_shortcut.activated.connect(self._copy_selected_value)
        self.properties_table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.properties_table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionHidden(2, True)
        splitter.addWidget(self.properties_table)

        splitter.setSizes([220, 500])
        layout.addWidget(splitter, stretch=1)
        self._update_type_column_visibility()

    def load_bytes(self, data: bytes, asset_label: str = '') -> None:
        """Load RBXM/RBXMX bytes into the preview."""
        data = _decompress_if_needed(data)
        if data.startswith(b'<roblox!'):
            doc = self._load_binary(data)
        elif data.lstrip().startswith(b'<roblox'):
            doc = self._load_xml(data)
        else:
            raise ValueError('Data is not an RBXM/RBXMX document')

        self.document = doc
        self._populate_tree(asset_label)
        self._update_type_column_visibility()

    def clear(self) -> None:
        self.document = None
        self._item_to_instance.clear()
        self._matches.clear()
        self._match_index = -1
        self.tree.clear()
        self.properties_table.setRowCount(0)
        self.search_input.clear()
        self.summary_label.clear()

    def tree_expand_all(self):
        self.tree.expandAll()

    def tree_collapse_all(self):
        self.tree.collapseAll()

    def _load_binary(self, data: bytes) -> PreviewDocument:
        from .tools.solidmodel_converter.rbxm.deserializer import RbxmDeserializer

        raw_doc = RbxmDeserializer().deserialize(data)
        instances: dict[str, PreviewInstance] = {}

        def convert(inst) -> PreviewInstance:
            ref = str(inst.referent)
            existing = instances.get(ref)
            if existing is not None:
                return existing
            props: list[PreviewProperty] = []
            name = ''
            for prop_name, prop in inst.properties.items():
                type_name = getattr(prop.fmt, 'name', str(prop.fmt))
                props.append(PreviewProperty(prop_name, type_name, prop.value))
                if prop_name == 'Name' and isinstance(prop.value, str):
                    name = prop.value
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
            value: Any
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
                value = { _tag_name(child): (child.text or '').strip() for child in elem }
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
        return PreviewDocument(roots=roots, instances=instances, metadata=metadata, shared_strings=shared_strings)

    def _populate_tree(self, asset_label: str) -> None:
        self.tree.clear()
        self.properties_table.setRowCount(0)
        self._item_to_instance.clear()
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
            f'{root_count} roots, {instance_count} instances, {shared_count} shared strings{suffix}'
        )

        if self.tree.topLevelItemCount():
            first = self.tree.topLevelItem(0)
            self.tree.setCurrentItem(first)
            first.setExpanded(True)
        self._on_search_changed(self.search_input.text())

    def _add_instance_item(self, parent: QTreeWidgetItem | None, inst: PreviewInstance) -> QTreeWidgetItem:
        item = QTreeWidgetItem([inst.label()])
        if inst.referent:
            tip = f'{inst.class_name} | referent {inst.referent}'
            if inst.name:
                tip = f'{inst.name}\n{tip}'
            item.setToolTip(0, tip)
        self._item_to_instance[id(item)] = inst
        if parent is None:
            self.tree.addTopLevelItem(item)
        else:
            parent.addChild(item)
        for child in inst.children:
            self._add_instance_item(item, child)
        return item

    def _on_tree_selection_changed(self, current: QTreeWidgetItem | None, _previous: QTreeWidgetItem | None):
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
        for row, prop in enumerate(rows):
            value_text, tooltip = self._format_value(prop.value, prop.type_name)
            copy_text = self._copy_text_for_value(prop.value, prop.type_name)
            prop_item = QTableWidgetItem(prop.name)
            value_item = QTableWidgetItem(value_text)
            type_item = QTableWidgetItem(prop.type_name)
            value_item.setData(_COPY_VALUE_ROLE, copy_text)
            for item in (prop_item, value_item, type_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if tooltip:
                value_item.setToolTip(tooltip)
            self.properties_table.setItem(row, 0, prop_item)
            self.properties_table.setItem(row, 1, value_item)
            self.properties_table.setItem(row, 2, type_item)

    def _format_value(self, value: Any, type_name: str) -> tuple[str, str]:
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

        if isinstance(value, dict):
            if type_name == 'Ref':
                target = self._resolve_ref(value)
                if target:
                    return target, target
            compact_vector = _format_vector_like(value)
            if compact_vector:
                return compact_vector, compact_vector
            parts = [f'{k}={_compact(str(v), 60)}' for k, v in value.items()]
            text = ', '.join(parts)
            return text, text

        if isinstance(value, list):
            text = '[' + ', '.join(_format_scalar(v) for v in value[:8])
            if len(value) > 8:
                text += f', ... +{len(value) - 8}'
            text += ']'
            return text, str(value)

        if isinstance(value, (bool, int, float)) or type_name.lower() in {'bool', 'int', 'int64', 'float', 'double'}:
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

    def _copy_text_for_value(self, value: Any, type_name: str) -> str:
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

        if isinstance(value, dict):
            if type_name == 'Ref':
                target = self._resolve_ref(value)
                if target:
                    return target
            compact_vector = _format_vector_like(value)
            if compact_vector:
                return compact_vector
            return ', '.join(f'{k}={self._copy_text_for_value(v, "")}' for k, v in value.items())

        if isinstance(value, list):
            return '[' + ', '.join(self._copy_text_for_value(v, '') for v in value) + ']'

        if isinstance(value, (bool, int, float)) or type_name.lower() in {'bool', 'int', 'int64', 'float', 'double'}:
            return _format_scalar(value)

        text = '' if value is None else str(value)
        if type_name == 'Ref':
            target = self._resolve_ref(text)
            if target:
                return target
        return html.unescape(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_type_column_visibility()

    def _update_type_column_visibility(self):
        enough_space = self.width() >= 760
        self.properties_table.horizontalHeader().setSectionHidden(2, not enough_space)

    def _resolve_ref(self, value: Any) -> str:
        if self.document is None:
            return ''
        ref = ''
        if isinstance(value, dict):
            ref = str(value.get('Ref') or value.get('referent') or value.get('id') or '')
        else:
            ref = str(value or '')
        if not ref or ref in ('None', '-1', 'null'):
            return ref
        target = self.document.instances.get(ref)
        if target is None:
            return ref
        return f'{ref} -> {target.label()}'

    def _on_search_changed(self, text: str):
        query = text.strip().lower()
        self._matches = []
        self._match_index = -1

        def walk(item: QTreeWidgetItem):
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
                walk(item.child(i))

        for i in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(i))

        if query and self._matches:
            self._match_index = 0
            self._select_match()
        self._update_match_label()

    def _select_match(self):
        if not self._matches or self._match_index < 0:
            return
        item = self._matches[self._match_index]
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.tree.setCurrentItem(item)
        self.tree.scrollToItem(item)

    def _next_match(self):
        if not self._matches:
            return
        self._match_index = (self._match_index + 1) % len(self._matches)
        self._select_match()
        self._update_match_label()

    def _prev_match(self):
        if not self._matches:
            return
        self._match_index = (self._match_index - 1) % len(self._matches)
        self._select_match()
        self._update_match_label()

    def _update_match_label(self):
        if self.search_input.text().strip():
            if self._matches:
                self.summary_label.setText(f'Match {self._match_index + 1}/{len(self._matches)}')
            else:
                self.summary_label.setText('No matches')
        elif self.document is not None:
            self.summary_label.setText(
                f'{len(self.document.roots)} roots, {len(self.document.instances)} instances, '
                f'{len(self.document.shared_strings)} shared strings'
            )

    def _copy_selected_value(self):
        row = self.properties_table.currentRow()
        if row < 0:
            return
        value_item = self.properties_table.item(row, 1)
        if value_item is not None:
            copy_text = value_item.data(_COPY_VALUE_ROLE)
            QApplication.clipboard().setText(str(copy_text if copy_text is not None else value_item.text()))


def _compact(text: str, limit: int) -> str:
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + '...'


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return 'True' if value else 'False'
    if isinstance(value, str) and value.lower() in {'true', 'false'}:
        return 'True' if value.lower() == 'true' else 'False'
    if isinstance(value, int):
        return str(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return _compact(str(value), 80)
    if abs(number) < 1e-8:
        number = 0.0
    if number.is_integer():
        return str(int(number))
    return f'{number:.6g}'


def _format_vector_like(value: dict) -> str:
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
