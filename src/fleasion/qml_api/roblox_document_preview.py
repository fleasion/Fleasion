"""Editable Roblox document tree and property bridge for QML cache previews."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from PySide6.QtCore import (
    QByteArray,
    QAbstractItemModel,
    QModelIndex,
    QObject,
    Property,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtQml import QmlElement

from ..cache.roblox_document import (
    classify_roblox_document,
    load_roblox_document,
    serialize_roblox_document,
)
from ..cache.tools.solidmodel_converter.rbxm.types import (
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxProperty,
)
from ..localization import tr
from .models import DictListModel

QML_IMPORT_NAME = 'Fleasion'
QML_IMPORT_MAJOR_VERSION = 1
QML_IMPORT_MINOR_VERSION = 0

_TREE_ROLES: Final = (
    'displayName',
    'className',
    'referent',
    'childCount',
    'propertyCount',
)
_PROPERTY_ROLES: Final = ('name', 'typeName', 'valueText', 'editable')
_CLASS_NAME_PATTERN: Final = re.compile(r'^[A-Za-z][A-Za-z0-9_]*$')
_READ_ONLY_FORMATS: Final = {
    PropertyFormat.SHARED_STRING,
    PropertyFormat.BYTECODE,
    PropertyFormat.NUMBER_SEQUENCE,
    PropertyFormat.COLOR_SEQUENCE,
    PropertyFormat.UNKNOWN,
}
_INTEGER_FORMATS: Final = {
    PropertyFormat.INT,
    PropertyFormat.INT64,
    PropertyFormat.ENUM,
    PropertyFormat.BRICK_COLOR,
    PropertyFormat.FACES,
    PropertyFormat.AXES,
    PropertyFormat.SECURITY_CAPABILITIES,
}
_FLOAT_FORMATS: Final = {PropertyFormat.FLOAT, PropertyFormat.DOUBLE}


@dataclass(slots=True)
class _DocumentNode:
    instance: RbxInstance
    parent: _DocumentNode | None = None
    children: list[_DocumentNode] = field(default_factory=list)
    row: int = 0


class RobloxInstanceTreeModel(QAbstractItemModel):
    """Expose a filtered ``RbxDocument`` hierarchy to a QML ``TreeView``."""

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
        self._document: RbxDocument | None = None
        self._roots: list[_DocumentNode] = []
        self._nodes_by_referent: dict[int, list[_DocumentNode]] = {}
        self._query = ''
        self._count = 0

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return self._roles

    def columnCount(self, _parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 1

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        node = self._node(parent)
        return len(self._roots if node is None else node.children)

    def index(
        self,
        row: int,
        column: int,
        parent: QModelIndex = QModelIndex(),
    ) -> QModelIndex:
        if column != 0 or row < 0:
            return QModelIndex()
        parent_node = self._node(parent)
        children = self._roots if parent_node is None else parent_node.children
        if row >= len(children):
            return QModelIndex()
        return self.createIndex(row, column, children[row])

    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        node = self._node(index)
        if node is None or node.parent is None:
            return QModelIndex()
        return self.createIndex(node.parent.row, 0, node.parent)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        node = self._node(index)
        if node is None:
            return None
        instance = node.instance
        name = _instance_name(instance)
        if role == int(Qt.ItemDataRole.DisplayRole):
            return name
        role_name = self._role_names.get(role)
        if role_name == 'displayName':
            return name
        if role_name == 'className':
            return instance.class_name
        if role_name == 'referent':
            return str(instance.referent)
        if role_name == 'childCount':
            return len(node.children)
        if role_name == 'propertyCount':
            return len(instance.properties)
        return None

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return self._count

    def set_document(self, document: RbxDocument | None) -> None:
        self._document = document
        self._rebuild()

    def set_query(self, value: str) -> None:
        normalized = value.strip().casefold()
        if normalized == self._query:
            return
        self._query = normalized
        self._rebuild()

    def notify_instance_changed(self, referent: int) -> None:
        roles = list(self._roles)
        for node in self._nodes_by_referent.get(referent, []):
            index = self.createIndex(node.row, 0, node)
            self.dataChanged.emit(index, index, roles)

    def _rebuild(self) -> None:
        old_count = self._count
        self.beginResetModel()
        self._roots = []
        self._nodes_by_referent = {}
        document = self._document
        if document is not None:
            for instance in document.roots:
                node = self._build_node(instance, None, set())
                if node is not None:
                    node.row = len(self._roots)
                    self._roots.append(node)
        self._count = sum(_node_count(root) for root in self._roots)
        self.endResetModel()
        if old_count != self._count:
            self.countChanged.emit()

    def _build_node(
        self,
        instance: RbxInstance,
        parent: _DocumentNode | None,
        ancestors: set[int],
    ) -> _DocumentNode | None:
        if instance.referent in ancestors:
            return None
        next_ancestors = {*ancestors, instance.referent}
        node = _DocumentNode(instance, parent)
        for child in instance.children:
            child_node = self._build_node(child, node, next_ancestors)
            if child_node is not None:
                child_node.row = len(node.children)
                node.children.append(child_node)
        if self._query and self._query not in _instance_search_text(instance) and not node.children:
            return None
        self._nodes_by_referent.setdefault(instance.referent, []).append(node)
        return node

    @staticmethod
    def _node(index: QModelIndex) -> _DocumentNode | None:
        if not index.isValid():
            return None
        pointer = index.internalPointer()
        return pointer if isinstance(pointer, _DocumentNode) else None


@dataclass(slots=True)
class _Edit:
    kind: str
    referent: int
    before: Any
    after: Any
    property_name: str = ''
    existed_before: bool = True


@dataclass(slots=True)
class _DocumentSession:
    source_data: bytes
    source_digest: str
    source_kind: str
    label: str
    document: RbxDocument
    edits: list[_Edit] = field(default_factory=list)


@QmlElement
class RobloxDocumentPreviewApi(QObject):
    """Own editable document sessions and explicit modified exports."""

    changed = Signal()
    queryChanged = Signal()
    selectionChanged = Signal()
    errorOccurred = Signal(str)
    notificationRequested = Signal(str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._tree = RobloxInstanceTreeModel(self)
        self._properties = DictListModel(_PROPERTY_ROLES, parent=self)
        self._sessions: dict[str, _DocumentSession] = {}
        self._current_key = ''
        self._selected_referent: int | None = None
        self._query = ''
        self._error_text = ''
        self._export_directory: Path | None = None

    @Property(QObject, constant=True)
    def treeModel(self) -> QObject:  # noqa: N802
        return self._tree

    @Property(QObject, constant=True)
    def propertiesModel(self) -> QObject:  # noqa: N802
        return self._properties

    @Property(bool, notify=changed)
    def loaded(self) -> bool:
        return self._session is not None

    @Property(str, notify=changed)
    def documentKind(self) -> str:  # noqa: N802
        return self._session.source_kind.upper() if self._session is not None else ''

    @Property(str, notify=changed)
    def summaryText(self) -> str:  # noqa: N802
        session = self._session
        if session is None:
            return ''
        root_count = len(session.document.roots)
        instance_count = len(session.document.instances)
        return tr('qml.dynamic.document.summary', roots=root_count, instances=instance_count)

    @Property(list, notify=changed)
    def exportFormats(self) -> list[str]:  # noqa: N802
        return self._export_formats()

    @Property(bool, notify=changed)
    def modified(self) -> bool:
        return bool(self._session is not None and self._session.edits)

    @Property(bool, notify=changed)
    def canUndo(self) -> bool:  # noqa: N802
        return bool(self._session is not None and self._session.edits)

    @Property(str, notify=queryChanged)
    def query(self) -> str:  # pyright: ignore[reportRedeclaration]
        return self._query

    @query.setter  # pyright: ignore[reportRedeclaration]
    def query(self, value: str) -> None:  # pyright: ignore[reportRedeclaration]
        normalized = value.strip()
        if normalized == self._query:
            return
        self._query = normalized
        self._tree.set_query(normalized)
        self.queryChanged.emit()

    @Property(bool, notify=selectionChanged)
    def hasSelection(self) -> bool:  # noqa: N802
        return self._selected_instance is not None

    @Property(str, notify=selectionChanged)
    def selectedReferent(self) -> str:  # noqa: N802
        return str(self._selected_referent) if self._selected_referent is not None else ''

    @Property(str, notify=selectionChanged)
    def selectedName(self) -> str:  # noqa: N802
        instance = self._selected_instance
        return _instance_name(instance) if instance is not None else ''

    @Property(str, notify=selectionChanged)
    def selectedClassName(self) -> str:  # noqa: N802
        instance = self._selected_instance
        return instance.class_name if instance is not None else ''

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    def set_export_directory(self, value: object) -> None:
        self._export_directory = value if isinstance(value, Path) else None

    def load_bytes(self, data: bytes, asset_key: str, label: str = '') -> bool:
        """Load or restore one cached document editing session."""
        kind = classify_roblox_document(data)
        if kind is None:
            self._set_error(tr('qml.dynamic.document.not_roblox_document'))
            return False
        digest = hashlib.sha256(data).hexdigest()
        previous = self._sessions.get(asset_key)
        if previous is not None and previous.source_digest == digest and previous.edits:
            session = previous
        else:
            try:
                document = load_roblox_document(data)
            except Exception as exc:
                self._set_error(tr('qml.dynamic.document.parse_failed', error=exc))
                return False
            session = _DocumentSession(data, digest, kind, label, document)
            self._sessions[asset_key] = session

        self._release_unmodified_current(asset_key)
        self._current_key = asset_key
        self._query = ''
        self._error_text = ''
        self._tree.set_query('')
        self._tree.set_document(session.document)
        self._selected_referent = (
            session.document.roots[0].referent if session.document.roots else None
        )
        self._refresh_properties()
        self.queryChanged.emit()
        self.selectionChanged.emit()
        self.changed.emit()
        return True

    @Slot()
    def detach(self) -> None:
        self._release_unmodified_current('')
        self._current_key = ''
        self._selected_referent = None
        self._tree.set_document(None)
        self._properties.replace_items([])
        self.selectionChanged.emit()
        self.changed.emit()

    @Slot()
    def reset(self) -> None:
        self._sessions.clear()
        self.detach()

    @Slot(str)
    def selectInstance(self, referent: str) -> None:  # noqa: N802
        session = self._session
        try:
            value = int(referent)
        except ValueError:
            value = -1
        if session is None or value not in session.document.instances:
            return
        if value == self._selected_referent:
            return
        self._selected_referent = value
        self._refresh_properties()
        self.selectionChanged.emit()

    @Slot(str, result=bool)
    def renameSelected(self, value: str) -> bool:  # noqa: N802
        instance = self._selected_instance
        if instance is None:
            return False
        if '\x00' in value or len(value) > 512:
            self._set_error(tr('qml.dynamic.document.instance_name_invalid'))
            return False
        prop = instance.properties.get('Name')
        before = copy.deepcopy(prop.value) if prop is not None else None
        if prop is not None and before == value:
            return True
        instance.properties['Name'] = RbxProperty('Name', PropertyFormat.STRING, value)
        self._record_edit(
            _Edit('property', instance.referent, before, value, 'Name', prop is not None)
        )
        self._tree.notify_instance_changed(instance.referent)
        self._refresh_properties()
        self.selectionChanged.emit()
        return True

    @Slot(str, str, result=bool)
    def addProperty(self, name: str, type_name: str) -> bool:  # noqa: N802
        instance = self._selected_instance
        normalized_name = name.strip()
        normalized_type = type_name.strip().upper()
        if instance is None:
            return False
        if (
            not normalized_name
            or '\x00' in normalized_name
            or len(normalized_name) > 128
            or normalized_name in instance.properties
        ):
            self._set_error(tr('qml.dynamic.document.property_name_invalid'))
            return False
        aliases = {
            'STRING': PropertyFormat.STRING,
            'BOOL': PropertyFormat.BOOL,
            'INT': PropertyFormat.INT,
            'FLOAT': PropertyFormat.FLOAT,
            'DOUBLE': PropertyFormat.DOUBLE,
            'CONTENT': PropertyFormat.CONTENT,
            'VECTOR3': PropertyFormat.VECTOR3,
        }
        fmt = aliases.get(normalized_type)
        if fmt is None:
            self._set_error(tr('qml.dynamic.document.property_type_invalid'))
            return False
        value = _default_property_value(fmt)
        instance.properties[normalized_name] = RbxProperty(normalized_name, fmt, value)
        self._record_edit(
            _Edit('property', instance.referent, None, copy.deepcopy(value), normalized_name, False)
        )
        self._tree.notify_instance_changed(instance.referent)
        self._refresh_properties()
        return True

    @Slot(int, result=bool)
    def removeProperty(self, row: int) -> bool:  # noqa: N802
        instance = self._selected_instance
        item = self._properties.get(row)
        if instance is None or not item:
            return False
        name = str(item.get('name') or '')
        prop = instance.properties.get(name)
        if prop is None:
            return False
        before = copy.deepcopy(prop)
        instance.properties.pop(name)
        self._record_edit(_Edit('remove_property', instance.referent, before, None, name, True))
        self._tree.notify_instance_changed(instance.referent)
        self._refresh_properties()
        if name == 'Name':
            self.selectionChanged.emit()
        return True

    @Slot(str, result=bool)
    def setSelectedClassName(self, value: str) -> bool:  # noqa: N802
        instance = self._selected_instance
        normalized = value.strip()
        if instance is None:
            return False
        if not _CLASS_NAME_PATTERN.fullmatch(normalized) or len(normalized) > 128:
            self._set_error(tr('qml.dynamic.document.class_name_invalid'))
            return False
        if normalized == instance.class_name:
            return True
        before = instance.class_name
        instance.class_name = normalized
        self._record_edit(_Edit('class', instance.referent, before, normalized))
        self._tree.notify_instance_changed(instance.referent)
        self.selectionChanged.emit()
        return True

    @Slot(int, str, result=bool)
    def updateProperty(self, row: int, text: str) -> bool:  # noqa: N802
        instance = self._selected_instance
        item = self._properties.get(row)
        if instance is None or not item:
            return False
        name = str(item.get('name') or '')
        prop = instance.properties.get(name)
        if prop is None or not _property_is_editable(prop):
            self._set_error(
                tr(
                    'qml.dynamic.document.property_read_only',
                    property_name=name or tr('qml.dynamic.document.this_property'),
                )
            )
            return False
        try:
            replacement = _parse_property_value(text, prop, self._session)
        except ValueError as exc:
            self._set_error(str(exc))
            return False
        if replacement == prop.value:
            return True
        before = copy.deepcopy(prop.value)
        prop.value = replacement
        self._record_edit(
            _Edit('property', instance.referent, before, copy.deepcopy(replacement), name)
        )
        self._properties.update_item(row, {'valueText': _display_property_value(replacement)})
        if name == 'Name':
            self._tree.notify_instance_changed(instance.referent)
            self.selectionChanged.emit()
        return True

    @Slot(result=bool)
    def undo(self) -> bool:
        session = self._session
        if session is None or not session.edits:
            return False
        edit = session.edits.pop()
        instance = session.document.instances.get(edit.referent)
        if instance is None:
            return False
        if edit.kind == 'class':
            instance.class_name = str(edit.before)
        elif edit.kind == 'remove_property' and isinstance(edit.before, RbxProperty):
            instance.properties[edit.property_name] = copy.deepcopy(edit.before)
        elif edit.existed_before:
            prop = instance.properties.get(edit.property_name)
            if prop is not None:
                prop.value = copy.deepcopy(edit.before)
        else:
            instance.properties.pop(edit.property_name, None)
        self._tree.notify_instance_changed(instance.referent)
        self._refresh_properties()
        self._error_text = ''
        self.selectionChanged.emit()
        self.changed.emit()
        return True

    @Slot(result=bool)
    def revert(self) -> bool:
        session = self._session
        if session is None or not session.edits:
            return False
        selected = self._selected_referent
        try:
            session.document = load_roblox_document(session.source_data)
        except Exception as exc:
            self._set_error(tr('qml.dynamic.document.restore_failed', error=exc))
            return False
        session.edits.clear()
        self._tree.set_document(session.document)
        self._selected_referent = (
            selected
            if selected is not None and selected in session.document.instances
            else session.document.roots[0].referent
            if session.document.roots
            else None
        )
        self._refresh_properties()
        self.selectionChanged.emit()
        self.changed.emit()
        return True

    @Slot(str, result=str)
    def suggestedExportUrl(self, format_name: str) -> str:  # noqa: N802
        session = self._session
        normalized = format_name.casefold().lstrip('.')
        if session is None or normalized not in self._export_formats():
            return ''
        directory = self._export_directory or Path.cwd()
        stem = _safe_file_stem(session.label or self._current_key or 'roblox_document')
        return QUrl.fromLocalFile(str(directory / f'{stem}_modified.{normalized}')).toString()

    @Slot(str, str, result=bool)
    def exportDocument(self, format_name: str, destination_value: str) -> bool:  # noqa: N802
        session = self._session
        normalized = format_name.casefold().lstrip('.')
        if session is None:
            self._set_error(tr('qml.dynamic.document.load_before_export'))
            return False
        if normalized not in self._export_formats():
            self._set_error(tr('qml.dynamic.document.format_invalid', format=normalized.upper()))
            return False
        try:
            destination = _local_path(destination_value)
            if not destination.suffix:
                destination = destination.with_suffix(f'.{normalized}')
            data, _suffix = serialize_roblox_document(session.document, normalized)
            _atomic_write(destination, data)
        except (OSError, ValueError) as exc:
            self._set_error(tr('qml.dynamic.document.export_failed', error=exc))
            return False
        self._error_text = ''
        self.changed.emit()
        self.notificationRequested.emit(
            tr('qml.dynamic.document.exported_title'),
            str(destination),
            'success',
        )
        return True

    @Slot()
    def clearError(self) -> None:  # noqa: N802
        if not self._error_text:
            return
        self._error_text = ''
        self.changed.emit()

    @property
    def _session(self) -> _DocumentSession | None:
        return self._sessions.get(self._current_key)

    @property
    def _selected_instance(self) -> RbxInstance | None:
        session = self._session
        if session is None or self._selected_referent is None:
            return None
        return session.document.instances.get(self._selected_referent)

    def _refresh_properties(self) -> None:
        instance = self._selected_instance
        if instance is None:
            self._properties.replace_items([])
            return
        self._properties.replace_items(
            {
                'name': prop.name,
                'typeName': prop.fmt.name,
                'valueText': _display_property_value(prop.value),
                'editable': _property_is_editable(prop),
            }
            for prop in sorted(
                instance.properties.values(), key=lambda value: value.name.casefold()
            )
        )

    def _export_formats(self) -> list[str]:
        session = self._session
        if session is None:
            return []
        return ['rbxl'] if session.source_kind == 'rbxl' else ['rbxm', 'rbxmx']

    def _record_edit(self, edit: _Edit) -> None:
        session = self._session
        if session is None:
            return
        session.edits.append(edit)
        self._error_text = ''
        self.changed.emit()

    def _release_unmodified_current(self, next_key: str) -> None:
        if self._current_key and self._current_key != next_key:
            session = self._sessions.get(self._current_key)
            if session is not None and not session.edits:
                self._sessions.pop(self._current_key, None)

    def _set_error(self, message: str) -> None:
        self._error_text = message
        self.changed.emit()
        self.errorOccurred.emit(message)


def _node_count(node: _DocumentNode) -> int:
    return 1 + sum(_node_count(child) for child in node.children)


def _instance_name(instance: RbxInstance) -> str:
    prop = instance.properties.get('Name')
    return (
        prop.value
        if prop is not None and isinstance(prop.value, str) and prop.value
        else instance.class_name
    )


def _instance_search_text(instance: RbxInstance) -> str:
    values = ' '.join(
        f'{prop.name} {_display_property_value(prop.value)}'
        for prop in instance.properties.values()
    )
    return (
        f'{_instance_name(instance)} {instance.class_name} {instance.referent} {values}'.casefold()
    )


def _property_is_editable(prop: RbxProperty) -> bool:
    return not isinstance(prop.value, bytes) and prop.fmt not in _READ_ONLY_FORMATS


def _display_property_value(value: Any) -> str:
    if isinstance(value, bytes):
        return f'<{len(value)} bytes — read only>'
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(', ', ': '), default=str)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    return str(value)


def _parse_property_value(
    text: str,
    prop: RbxProperty,
    session: _DocumentSession | None,
) -> Any:
    normalized = text.strip()
    if prop.fmt == PropertyFormat.BOOL:
        folded = normalized.casefold()
        if folded in {'true', '1', 'yes', 'on'}:
            return True
        if folded in {'false', '0', 'no', 'off'}:
            return False
        raise ValueError(f'{prop.name} expects true or false.')
    if prop.fmt in _INTEGER_FORMATS:
        try:
            return int(normalized, 0)
        except ValueError as exc:
            raise ValueError(f'{prop.name} expects a whole number.') from exc
    if prop.fmt in _FLOAT_FORMATS:
        try:
            return float(normalized)
        except ValueError as exc:
            raise ValueError(f'{prop.name} expects a number.') from exc
    if prop.fmt == PropertyFormat.REF:
        if normalized.casefold() in {'', 'none', 'null', '-1'}:
            return None
        try:
            referent = int(normalized)
        except ValueError as exc:
            raise ValueError(f'{prop.name} expects an instance referent.') from exc
        if session is not None and referent not in session.document.instances:
            raise ValueError(f'No instance with referent {referent} exists in this document.')
        return referent
    if prop.fmt == PropertyFormat.CONTENT and not normalized.startswith(('{', '[')):
        return {'SourceType': 'Uri', 'Uri': text} if text else None
    if prop.fmt == PropertyFormat.OPTIONAL_CFRAME and normalized.casefold() in {
        '',
        'none',
        'null',
    }:
        return None
    if isinstance(prop.value, (dict, list, tuple)) or prop.value is None:
        try:
            parsed = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f'{prop.name} expects valid JSON matching its displayed shape.'
            ) from exc
        if isinstance(prop.value, dict) and not isinstance(parsed, dict):
            raise ValueError(f'{prop.name} expects a JSON object.')
        if isinstance(prop.value, list) and not isinstance(parsed, list):
            raise ValueError(f'{prop.name} expects a JSON array.')
        if isinstance(prop.value, tuple):
            if not isinstance(parsed, list):
                raise ValueError(f'{prop.name} expects a JSON array.')
            return tuple(parsed)
        return parsed
    if '\x00' in text:
        raise ValueError(f'{prop.name} cannot contain NUL.')
    return text


def _default_property_value(fmt: PropertyFormat) -> Any:
    if fmt == PropertyFormat.BOOL:
        return False
    if fmt == PropertyFormat.INT:
        return 0
    if fmt in {PropertyFormat.FLOAT, PropertyFormat.DOUBLE}:
        return 0.0
    if fmt == PropertyFormat.CONTENT:
        return None
    if fmt == PropertyFormat.VECTOR3:
        return {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
    return ''


def _safe_file_stem(value: str) -> str:
    cleaned = re.sub(r'[^A-Za-z0-9._-]+', '_', value.strip()).strip('._')
    return cleaned[:100] or 'roblox_document'


def _local_path(value: str) -> Path:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError('Choose an export destination.')
    url = QUrl(cleaned)
    if url.isLocalFile():
        return Path(url.toLocalFile())
    if url.scheme() and not (len(url.scheme()) == 1 and cleaned[1:2] == ':'):
        raise ValueError('Roblox document export supports local files only.')
    return Path(cleaned).expanduser()


def _atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = ['RobloxDocumentPreviewApi', 'RobloxInstanceTreeModel']
