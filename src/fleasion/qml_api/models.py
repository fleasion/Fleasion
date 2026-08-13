"""Small, typed models shared by QML-facing application bridges."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from PySide6.QtCore import (
    QByteArray,
    QAbstractListModel,
    QModelIndex,
    QObject,
    Property,
    Qt,
    Signal,
    Slot,
)


class DictListModel(QAbstractListModel):
    """Expose a sequence of dictionaries through named QML model roles."""

    countChanged = Signal()

    def __init__(
        self,
        roles: Sequence[str],
        items: Iterable[Mapping[str, Any]] = (),
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._role_names: Final[tuple[str, ...]] = tuple(roles)
        self._roles: Final[dict[int, QByteArray]] = {
            int(Qt.ItemDataRole.UserRole) + offset: QByteArray(name.encode('utf-8'))
            for offset, name in enumerate(self._role_names, start=1)
        }
        self._role_ids: Final[dict[str, int]] = {
            name: role for role, name in zip(self._roles, self._role_names, strict=True)
        }
        self._items: list[dict[str, Any]] = [dict(item) for item in items]

    def roleNames(self) -> dict[int, QByteArray]:  # noqa: N802
        return self._roles

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._items)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._items):
            return None
        role_name = self._roles.get(role)
        if role_name is None:
            return None
        return self._items[index.row()].get(bytes(role_name.data()).decode('utf-8'))

    def replace_items(self, items: Iterable[Mapping[str, Any]]) -> None:
        replacement = [dict(item) for item in items]
        old_count = len(self._items)
        self.beginResetModel()
        self._items = replacement
        self.endResetModel()
        if old_count != len(replacement):
            self.countChanged.emit()

    def append_item(self, item: Mapping[str, Any]) -> None:
        row = len(self._items)
        self.beginInsertRows(QModelIndex(), row, row)
        self._items.append(dict(item))
        self.endInsertRows()
        self.countChanged.emit()

    def append_items(self, items: Iterable[Mapping[str, Any]]) -> None:
        replacement = [dict(item) for item in items]
        if not replacement:
            return
        first = len(self._items)
        last = first + len(replacement) - 1
        self.beginInsertRows(QModelIndex(), first, last)
        self._items.extend(replacement)
        self.endInsertRows()
        self.countChanged.emit()

    def update_item(self, row: int, values: Mapping[str, Any]) -> bool:
        if not 0 <= row < len(self._items):
            return False
        changed_roles = [
            self._role_ids[name]
            for name, value in values.items()
            if name in self._role_ids and self._items[row].get(name) != value
        ]
        if not changed_roles:
            return False
        self._items[row].update(values)
        model_index = self.index(row, 0)
        self.dataChanged.emit(model_index, model_index, changed_roles)
        return True

    def remove_rows(self, rows: Iterable[int]) -> None:
        for row in sorted(set(rows), reverse=True):
            if not 0 <= row < len(self._items):
                continue
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._items[row]
            self.endRemoveRows()
            self.countChanged.emit()

    def snapshot(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._items]

    @Slot(int, result=dict)
    def get(self, row: int) -> dict[str, Any]:
        if not 0 <= row < len(self._items):
            return {}
        return dict(self._items[row])

    @Slot(str, object, result=int)
    def indexOf(self, role_name: str, value: Any) -> int:  # noqa: N802
        for index, item in enumerate(self._items):
            if item.get(role_name) == value:
                return index
        return -1

    @Property(int, notify=countChanged)
    def count(self) -> int:
        return len(self._items)


class SelectionModel(QObject):
    """Keep a stable set of string keys for multi-select QML views."""

    selectionChanged = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._keys: set[str] = set()

    @Slot(str, bool)
    def setSelected(self, key: str, selected: bool) -> None:  # noqa: N802
        before = len(self._keys)
        if selected:
            self._keys.add(key)
        else:
            self._keys.discard(key)
        if before != len(self._keys):
            self.selectionChanged.emit()

    @Slot(str, result=bool)
    def contains(self, key: str) -> bool:
        return key in self._keys

    @Slot()
    def clear(self) -> None:
        if not self._keys:
            return
        self._keys.clear()
        self.selectionChanged.emit()

    @Slot(result=list)
    def values(self) -> list[str]:
        return sorted(self._keys)
