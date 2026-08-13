"""Hierarchical JSON model for the community preset browser."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Final

from PySide6.QtCore import (
    QByteArray,
    QAbstractItemModel,
    QModelIndex,
    QObject,
    Property,
    Qt,
    Signal,
)

from ..prejsons import PresetValue

_TREE_ROLES: Final = (
    'nodeName',
    'nodePath',
    'valueText',
    'valueKind',
    'rowId',
    'importable',
    'childCount',
)


@dataclass(slots=True)
class _TreeNode:
    name: str
    path: str
    value_text: str
    value_kind: str
    row_id: str = ''
    importable: bool = False
    parent: _TreeNode | None = None
    children: list[_TreeNode] = field(default_factory=list)
    row: int = 0

    @property
    def search_text(self) -> str:
        return f'{self.path} {self.value_text} {self.value_kind}'.casefold()


class PresetJsonTreeModel(QAbstractItemModel):
    """Expose a JSON document as a searchable QML ``TreeView`` model."""

    countChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._roles: Final[dict[int, QByteArray]] = {
            int(Qt.ItemDataRole.UserRole) + offset: QByteArray(name.encode('utf-8'))
            for offset, name in enumerate(_TREE_ROLES, start=1)
        }
        self._role_names: Final[dict[int, str]] = {
            role: bytes(name.data()).decode('utf-8') for role, name in self._roles.items()
        }
        self._source_root = _TreeNode('', '', '', 'root')
        self._visible_root = _TreeNode('', '', '', 'root')
        self._query = ''
        self._count = 0

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return self._roles

    def columnCount(self, _parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        node = self._node(parent)
        return len(node.children)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        parent_node = self._node(parent)
        if row >= len(parent_node.children):
            return QModelIndex()
        return self.createIndex(row, column, parent_node.children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node = self._node(index)
        parent_node = node.parent
        if parent_node is None or parent_node is self._visible_root:
            return QModelIndex()
        return self.createIndex(parent_node.row, 0, parent_node)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid():
            return None
        node = self._node(index)
        if role == int(Qt.ItemDataRole.DisplayRole):
            return node.name
        role_name = self._role_names.get(role)
        if role_name == 'nodeName':
            return node.name
        if role_name == 'nodePath':
            return node.path
        if role_name == 'valueText':
            return node.value_text
        if role_name == 'valueKind':
            return node.value_kind
        if role_name == 'rowId':
            return node.row_id
        if role_name == 'importable':
            return node.importable
        if role_name == 'childCount':
            return len(node.children)
        return None

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self._count

    def set_document(self, document: object, values: tuple[PresetValue, ...]) -> None:
        values_by_path = {value.path: value for value in values}
        source_root = _TreeNode('', '', '', 'root')
        source_root.children = self._document_children(document, source_root, values_by_path)
        self._source_root = source_root
        self._rebuild_visible()

    def set_query(self, value: str) -> None:
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self._rebuild_visible()

    def clear(self) -> None:
        self._source_root = _TreeNode('', '', '', 'root')
        self._query = ''
        self._rebuild_visible()

    def _rebuild_visible(self) -> None:
        old_count = self._count
        self.beginResetModel()
        visible_root = _TreeNode('', '', '', 'root')
        if self._query:
            visible_root.children = [
                match
                for child in self._source_root.children
                if (match := self._filtered_copy(child, visible_root)) is not None
            ]
        else:
            visible_root.children = [
                self._copy_subtree(child, visible_root)
                for child in self._source_root.children
            ]
        self._assign_rows(visible_root)
        self._visible_root = visible_root
        self._count = self._descendant_count(visible_root)
        self.endResetModel()
        if self._count != old_count:
            self.countChanged.emit()

    def _filtered_copy(
        self,
        node: _TreeNode,
        parent: _TreeNode,
    ) -> _TreeNode | None:
        child_matches = [
            match
            for child in node.children
            if (match := self._filtered_copy(child, parent)) is not None
        ]
        if self._query not in node.search_text and not child_matches:
            return None
        copied = self._copy_node(node, parent)
        copied.children = child_matches
        for child in copied.children:
            child.parent = copied
        return copied

    def _copy_subtree(self, node: _TreeNode, parent: _TreeNode) -> _TreeNode:
        copied = self._copy_node(node, parent)
        copied.children = [self._copy_subtree(child, copied) for child in node.children]
        return copied

    @staticmethod
    def _copy_node(node: _TreeNode, parent: _TreeNode) -> _TreeNode:
        return _TreeNode(
            name=node.name,
            path=node.path,
            value_text=node.value_text,
            value_kind=node.value_kind,
            row_id=node.row_id,
            importable=node.importable,
            parent=parent,
        )

    def _document_children(
        self,
        document: object,
        root: _TreeNode,
        values_by_path: dict[str, PresetValue],
    ) -> list[_TreeNode]:
        if isinstance(document, dict):
            return [
                self._build_node(str(key), str(key), value, root, values_by_path)
                for key, value in document.items()
            ]
        if isinstance(document, list):
            return [
                self._build_node(f'[{index + 1}]', str(index + 1), value, root, values_by_path)
                for index, value in enumerate(document)
            ]
        return [self._build_node('Value', 'Value', document, root, values_by_path)]

    def _build_node(
        self,
        name: str,
        path: str,
        value: object,
        parent: _TreeNode,
        values_by_path: dict[str, PresetValue],
    ) -> _TreeNode:
        if isinstance(value, dict):
            node = _TreeNode(name, path, '', 'object', parent=parent)
            node.children = [
                self._build_node(
                    str(key),
                    f'{path} › {key}',
                    child,
                    node,
                    values_by_path,
                )
                for key, child in value.items()
            ]
            return node
        if isinstance(value, list):
            node = _TreeNode(name, path, '', 'array', parent=parent)
            node.children = [
                self._build_node(
                    f'[{index + 1}]',
                    f'{path} › {index + 1}',
                    child,
                    node,
                    values_by_path,
                )
                for index, child in enumerate(value)
            ]
            return node

        preset_value = values_by_path.get(path)
        return _TreeNode(
            name=name,
            path=path,
            value_text=_display_value(value),
            value_kind=preset_value.kind if preset_value is not None else _json_kind(value),
            row_id=preset_value.row_id if preset_value is not None else '',
            importable=preset_value is not None,
            parent=parent,
        )

    @staticmethod
    def _assign_rows(parent: _TreeNode) -> None:
        for row, child in enumerate(parent.children):
            child.row = row
            PresetJsonTreeModel._assign_rows(child)

    @staticmethod
    def _descendant_count(parent: _TreeNode) -> int:
        return sum(1 + PresetJsonTreeModel._descendant_count(child) for child in parent.children)

    def _node(self, index: QModelIndex) -> _TreeNode:
        if not index.isValid():
            return self._visible_root
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, _TreeNode) else self._visible_root


def _display_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _json_kind(value: object) -> str:
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'boolean'
    if isinstance(value, (int, float)):
        return 'number'
    return 'string'


__all__ = ['PresetJsonTreeModel']
