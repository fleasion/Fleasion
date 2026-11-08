"""Item-free Qt model for unusually large custom FastFlag collections."""

from __future__ import annotations

from typing import override

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QStyle

from fleasion.localization import tr, tr_count

ROW_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 20
CANONICAL_NAME_ROLE = int(Qt.ItemDataRole.UserRole) + 21


class VirtualFastFlagModel(QAbstractTableModel):
    """Keep all flags in Python, materializing Qt data only for requested cells."""

    value_changed = Signal(str, str)
    name_changed = Signal(str, str)
    status_changed = Signal(str, str, bool)
    invalid_name = Signal(str)

    def __init__(self, parent: QAbstractTableModel | None = None) -> None:
        super().__init__(parent)
        self._flags: dict[str, str] = {}
        self.entries: list[tuple[str, str, str | None]] = []
        self._matches: list[int] | None = None
        self._grouped: set[str] = set()
        self._folder_counts: dict[str, int] = {}
        self.disabled_flags: set[str] = set()
        self.disabled_folders: set[str] = set()
        self.bindings: dict[str, str] = {}
        self.folder_bindings: dict[str, str] = {}
        self.hotkeys_supported = False
        self._font = QFont()
        self._font.setBold(True)
        self._folder_icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)

    def replace(  # ruff: ignore[too-many-positional-arguments]
        self,
        flags: dict[str, str],
        entries: list[tuple[str, str, str | None]],
        folders: dict[str, list[str]],
        disabled_flags: set[str],
        disabled_folders: set[str],
        bindings: dict[str, str],
        folder_bindings: dict[str, str],
        *,
        hotkeys_supported: bool,
    ) -> None:
        self.beginResetModel()
        self._flags = flags
        self.entries = entries
        self._matches = None
        self._grouped = {name for members in folders.values() for name in members if name in flags}
        self._folder_counts = {
            folder: sum(name in flags for name in members) for folder, members in folders.items()
        }
        self.disabled_flags = disabled_flags
        self.disabled_folders = disabled_folders
        self.bindings = bindings
        self.folder_bindings = folder_bindings
        self.hotkeys_supported = hotkeys_supported
        self.endResetModel()

    def filter(self, query: str) -> None:
        query = query.strip().casefold()
        self.beginResetModel()
        if not query:
            self._matches = None
        else:
            self._matches = [
                index
                for index, (kind, name, value) in enumerate(self.entries)
                if query in name.casefold()
                or (value is not None and query in value.casefold())
                or query
                in (
                    self.folder_bindings.get(name, '')
                    if kind == 'folder'
                    else self.bindings.get(name, '')
                ).casefold()
            ]
        self.endResetModel()

    def entry_at(self, row: int) -> tuple[str, str, str | None]:
        return self.entries[self._matches[row] if self._matches is not None else row]

    def _entry_index(self, row: int) -> int:
        return self._matches[row] if self._matches is not None else row

    @override
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # ruff: ignore[function-call-in-default-argument]
        if parent.isValid():
            return 0
        return len(self._matches) if self._matches is not None else len(self.entries)

    @override
    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # ruff: ignore[function-call-in-default-argument]
        return 4 if self.hotkeys_supported else 2

    @override
    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if orientation != Qt.Orientation.Horizontal or role != Qt.ItemDataRole.DisplayRole:
            return None
        headers = (
            tr('modifications.table.name'),
            tr('modifications.table.value'),
            tr('modifications.table.status'),
            tr('modifications.table.keybind'),
        )
        return headers[section] if 0 <= section < len(headers) else None

    @override
    def data(  # ruff: ignore[too-many-return-statements]
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        if not index.isValid() or not 0 <= index.row() < self.rowCount():
            return None
        kind, name, value = self.entry_at(index.row())
        column = index.column()
        folder = kind == 'folder'
        if role == ROW_KIND_ROLE:
            return kind
        if role == CANONICAL_NAME_ROLE:
            return name
        if role == Qt.ItemDataRole.FontRole and folder and column == 0:
            return self._font
        if role == Qt.ItemDataRole.DecorationRole and folder and column == 0:
            return self._folder_icon
        if role == Qt.ItemDataRole.ToolTipRole:
            return name if column == 0 else None
        if role == Qt.ItemDataRole.CheckStateRole and column == 2:
            disabled = name in (self.disabled_folders if folder else self.disabled_flags)
            return Qt.CheckState.Unchecked if disabled else Qt.CheckState.Checked
        if role == Qt.ItemDataRole.UserRole and column == 1 and not folder:
            return value
        if role not in {Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole}:
            return None
        if column == 0:
            return name if folder or name not in self._grouped else f'    {name}'
        if column == 1:
            if folder:
                count = self._folder_counts.get(name, 0)
                return tr_count(
                    count,
                    'ui.gui.modifications_tab.fastflag_folder_count_one',
                    'ui.gui.modifications_tab.fastflag_folder_count_other',
                )
            if role == Qt.ItemDataRole.DisplayRole and name.startswith(('FFlag', 'DFFlag')):
                return (
                    tr('ui.gui.modifications_tab.true')
                    if value == 'True'
                    else tr('ui.gui.modifications_tab.false')
                )
            return value
        if column == 2:
            return tr('ui.gui.modifications_tab.enabled')
        if column == 3:
            return (self.folder_bindings if folder else self.bindings).get(name, '')
        return None

    @override
    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        kind, _name, _value = self.entry_at(index.row())
        result = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if kind != 'folder' and index.column() in {0, 1}:
            result |= Qt.ItemFlag.ItemIsEditable
        if index.column() == 2:
            result |= Qt.ItemFlag.ItemIsUserCheckable
        return result

    @override
    def setData(  # ruff: ignore[too-many-return-statements]
        self,
        index: QModelIndex | QPersistentModelIndex,
        value: object,
        role: int = Qt.ItemDataRole.EditRole,
    ) -> bool:
        if not index.isValid():
            return False
        entry_index = self._entry_index(index.row())
        kind, name, previous = self.entries[entry_index]
        column = index.column()
        if column == 2 and role == Qt.ItemDataRole.CheckStateRole:
            disabled_set = self.disabled_folders if kind == 'folder' else self.disabled_flags
            enabled = value in {Qt.CheckState.Checked, Qt.CheckState.Checked.value}
            if enabled:
                disabled_set.discard(name)
            else:
                disabled_set.add(name)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.status_changed.emit(kind, name, enabled)
            return True
        if kind == 'folder':
            return False
        if column == 0 and role in {Qt.ItemDataRole.EditRole, Qt.ItemDataRole.DisplayRole}:
            new_name = str(value).strip()
            if not new_name or (new_name != name and new_name in self._flags):
                self.invalid_name.emit(new_name)
                return False
            if new_name != name:
                self._flags[new_name] = self._flags.pop(name)
                if name in self._grouped:
                    self._grouped.remove(name)
                    self._grouped.add(new_name)
                self.entries[entry_index] = (kind, new_name, previous)
                self.dataChanged.emit(index, index)
                self.name_changed.emit(name, new_name)
            return True
        if column == 1:
            if role == Qt.ItemDataRole.DisplayRole and name.startswith(('FFlag', 'DFFlag')):
                return True
            if role not in {
                Qt.ItemDataRole.EditRole,
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.UserRole,
            }:
                return False
            updated = str(value)
            if name.startswith(('FFlag', 'DFFlag')):
                updated = 'True' if updated.strip().lower() == 'true' else 'False'
            if updated != previous:
                self._flags[name] = updated
                self.entries[entry_index] = (kind, name, updated)
                self.dataChanged.emit(index, index)
                self.value_changed.emit(name, updated)
            return True
        return False
