"""JSON tree viewer widget for cache files - displays JSON in a clean tree view."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict, cast, override

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QSignalBlocker, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr, tr_count

if TYPE_CHECKING:
    from fleasion.utils.json_types import JsonValue

type ItemPath = tuple[str, ...]


class _LazyArrayInfo(TypedDict):
    array_data: list[JsonValue]
    loaded: bool


def _preserve_tree_item(item: QTreeWidgetItem | None) -> QTreeWidgetItem:
    if TYPE_CHECKING:
        assert item is not None
    return item


# Custom data role to flag leaf scalar items as word-wrap eligible (not preview/summary nodes)
_WRAP_ROLE = Qt.ItemDataRole.UserRole + 1
_DUPLICATE_COMBINE_LIMIT = 250


class _WordWrapDelegate(QStyledItemDelegate):
    """Item delegate that enables word-wrapping for long text in tree rows."""

    @override
    def sizeHint(
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        base = super().sizeHint(option, index)
        # Only wrap actual leaf scalar values, not summary/preview items
        if not index.data(_WRAP_ROLE):
            return base
        text = index.data(Qt.ItemDataRole.DisplayRole) or ''
        if not text:
            return base
        tree = cast('QTreeWidget | None', self.parent())
        # Use a slightly smaller available width so wrapping happens earlier
        col_w = tree.columnWidth(0) if tree is not None else 0
        # Subtract a larger margin to account for expander/gutter and padding
        w = col_w - 70 if col_w > 70 else (option.rect.width() - 70)
        if w <= 0:
            return base
        fm = option.fontMetrics
        bounding = fm.boundingRect(0, 0, w, 0, int(Qt.TextFlag.TextWordWrap), text)
        return QSize(base.width(), max(bounding.height() + 8, base.height()))


class CacheJsonViewer(QWidget):
    """A JSON tree viewer widget for displaying JSON data in cache preview pane.

    Smart display for large JSONs with filtering and intelligent previews.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.data: JsonValue = None
        self.node_values: dict[int, JsonValue] = {}
        self.node_is_leaf: dict[int, bool] = {}

        # Search state
        self._search_matches: list[QTreeWidgetItem] = []
        self._current_match_index = 0

        # Track lazy-loaded arrays
        self._lazy_arrays: dict[int, _LazyArrayInfo] = {}

        # Filter settings
        self._show_null_values = False

        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)

        # Single toolbar row: search | nav buttons (hidden until search) | expand/collapse | adv | match
        toolbar = QHBoxLayout()
        toolbar.setSpacing(4)
        toolbar.addWidget(QLabel(tr('ui.cache.cache_json_viewer.search')))

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            tr('ui.cache.cache_json_viewer.search_keys_and_values')
        )
        self.search_input.textChanged.connect(self._on_search_text_changed)
        toolbar.addWidget(self.search_input)

        # Navigation buttons — hidden until a search is active
        self.prev_match_btn = QPushButton(tr('ui.cache.cache_json_viewer.text'))
        self.prev_match_btn.setFixedWidth(30)
        self.prev_match_btn.setToolTip(tr('ui.cache.cache_json_viewer.previous_match'))
        self.prev_match_btn.clicked.connect(self._cycle_to_prev_match)
        self.prev_match_btn.hide()
        toolbar.addWidget(self.prev_match_btn)

        self.next_match_btn = QPushButton(tr('ui.cache.cache_json_viewer.text_2'))
        self.next_match_btn.setFixedWidth(30)
        self.next_match_btn.setToolTip(tr('ui.cache.cache_json_viewer.next_match'))
        self.next_match_btn.clicked.connect(self._cycle_to_next_match)
        self.next_match_btn.hide()
        toolbar.addWidget(self.next_match_btn)

        expand_btn = QPushButton(tr('ui.cache.cache_json_viewer.expand_all'))
        expand_btn.clicked.connect(self._expand_all)
        toolbar.addWidget(expand_btn)

        collapse_btn = QPushButton(tr('ui.cache.cache_json_viewer.collapse_all'))
        collapse_btn.clicked.connect(self._collapse_all)
        toolbar.addWidget(collapse_btn)

        self.adv_checkbox = QCheckBox(tr('ui.cache.cache_json_viewer.adv'))
        self.adv_checkbox.setToolTip(
            tr('ui.cache.cache_json_viewer.show_boilerplate_fields_entryid_context_etc')
        )
        self.adv_checkbox.stateChanged.connect(self._on_advanced_toggled)
        toolbar.addWidget(self.adv_checkbox)

        toolbar.addStretch()

        self.match_label = QLabel('')
        self.match_label.setStyleSheet('color: #888; font-size: 11px;')
        self.match_label.hide()
        toolbar.addWidget(self.match_label)

        layout.addLayout(toolbar)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemExpanded.connect(self._on_item_expanded)
        self.tree.setUniformRowHeights(False)
        self.tree.setWordWrap(True)
        self.tree.setColumnCount(1)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setItemDelegate(_WordWrapDelegate(self.tree))

        # Re-measure item heights whenever the column resizes (e.g. splitter moved)
        def _schedule_delayed_layout(_section: int, _old_size: int, _new_size: int) -> None:
            self.tree.scheduleDelayedItemsLayout()

        self.tree.header().sectionResized.connect(_schedule_delayed_layout)
        layout.addWidget(self.tree)

        self.setLayout(layout)

    def load_json(self, data: JsonValue) -> None:
        """Load and display JSON data."""
        self.data = data
        self.node_values = {}
        self.node_is_leaf = {}
        self._search_matches = []
        self._current_match_index = 0
        self._lazy_arrays = {}
        self.search_input.clear()
        self._populate_tree()

    def _populate_tree(self) -> None:
        """Populate the tree with JSON data."""
        signal_blocker = QSignalBlocker(self.tree)
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.clear()
            self.node_values = {}
            self.node_is_leaf = {}
            self._lazy_arrays = {}

            if isinstance(self.data, dict):
                if len(self.data) <= _DUPLICATE_COMBINE_LIMIT:
                    # Duplicate combining is useful for small metadata objects, but
                    # deep hashing every value in large JSON objects is expensive.
                    duplicate_map = self._get_duplicate_values_in_dict(self.data)
                    processed_keys: set[str] = set()
                    for key, value in self.data.items():
                        if key not in processed_keys:
                            keys_with_same_value = self._find_keys_with_same_value(
                                self.data, key, duplicate_map
                            )
                            processed_keys.update(keys_with_same_value)
                            combined_key = '+'.join(keys_with_same_value)
                            self._add_node(self.tree, combined_key, value)
                else:
                    for key, value in self.data.items():
                        self._add_node(self.tree, key, value)
            elif isinstance(self.data, list):
                for index, value in enumerate(self.data):
                    self._add_node(self.tree, f'[{index}]', value)
            else:
                self._add_node(self.tree, '', self.data)
        finally:
            self.tree.setUpdatesEnabled(True)
            del signal_blocker

    def _get_duplicate_values_in_dict(self, obj: dict[str, JsonValue]) -> dict[object, list[str]]:
        """Map values to list of keys that share that value. Only for hashable values."""
        value_map: dict[object, list[str]] = {}
        for k, v in obj.items():
            # Only track hashable values (strings, numbers, bools, tuples, etc.)
            try:
                # Make hashable version for comparison
                hashable_v = self._make_hashable(v)
                if hashable_v not in value_map:
                    value_map[hashable_v] = []
                value_map[hashable_v].append(k)
            except TypeError:
                # Non-hashable type (dict, list), skip
                pass
        return value_map

    def _make_hashable(self, obj: JsonValue | set[JsonValue]) -> object:
        """Convert an object to a hashable representation."""
        if isinstance(obj, dict):
            return tuple(sorted((k, self._make_hashable(v)) for k, v in obj.items()))
        if isinstance(obj, list):
            return tuple(self._make_hashable(item) for item in obj)
        if isinstance(obj, set):
            return frozenset(self._make_hashable(item) for item in obj)
        return obj

    def _find_keys_with_same_value(
        self,
        obj: dict[str, JsonValue],
        key: str,
        duplicate_map: dict[object, list[str]],
    ) -> list[str]:
        """Find all keys in obj that have the same value as 'key', return sorted list."""
        try:
            hashable_v = self._make_hashable(obj[key])
            keys = duplicate_map.get(hashable_v, [key])
            # Return sorted keys to ensure consistent ordering
            return sorted(keys)
        except TypeError:
            return [key]

    def _should_skip_node(self, key: str, value: JsonValue) -> bool:
        """Check if node should be skipped (null values and boilerplate fields when Advanced is off)."""
        # Always skip null values
        if value is None:
            return True

        # In non-Advanced mode, skip boilerplate fields
        if not self._show_null_values:
            boilerplate_keys = {'entryId', 'context', 'ingestionVersion'}
            if key in boilerplate_keys:
                return True

        return False

    @staticmethod
    def _format_preview_value(key: str, value: JsonValue) -> str:
        if isinstance(value, str):
            # Normalize whitespace; let the tree column elide at the screen edge
            preview = ' '.join(value.split())
            return f'{key}: "{preview}"'
        if isinstance(value, bool):
            return f'{key}: {str(value).lower()}'
        if isinstance(value, int | float):
            return f'{key}: {value}'
        return f'{key}: ...'

    def _get_preview_text(self, obj: JsonValue) -> str:
        """Get preview text for a dict/list (first non-null field)."""
        if isinstance(obj, dict):
            first_value = next(
                ((key, value) for key, value in obj.items() if value is not None), None
            )
            return self._format_preview_value(*first_value) if first_value is not None else ''
        if isinstance(obj, list) and obj and isinstance(obj[0], dict):
            return self._get_preview_text(obj[0])
        return ''

    def _add_node(
        self,
        parent_item: QTreeWidget | QTreeWidgetItem,
        key: str,
        value: JsonValue,
    ) -> QTreeWidgetItem | None:
        """Add a node to the tree with smart display and previews."""
        if self._should_skip_node(key, value):
            return None

        if isinstance(value, dict):
            # Dictionary: show with preview of first field
            preview = self._get_preview_text(value)
            display = f'{key}: {{{preview}}}' if key else f'{{{preview}}}'
            item = QTreeWidgetItem(parent_item, [display])
            item.setExpanded(False)
            self.node_is_leaf[id(item)] = False
            self.node_values[id(item)] = value

            # Add a dummy child so expand arrow shows
            QTreeWidgetItem(item, [tr('cache_json.loading')])

        elif isinstance(value, list):
            # Array: show count with preview if object array
            item_count = len(value)

            if item_count == 0:
                display = f'{key}: []' if key else '[]'
                item = QTreeWidgetItem(parent_item, [display])
                self.node_is_leaf[id(item)] = True
                self.node_values[id(item)] = value
            else:
                # Show preview for object arrays
                preview = self._get_preview_text(value[0]) if isinstance(value[0], dict) else ''
                preview_text = f': {{{preview}}}' if preview else ': [...]'
                count_text = tr_count(
                    item_count,
                    'cache_json.item_count.one',
                    'cache_json.item_count.other',
                )
                display = (
                    f'{key}: [{count_text}]{preview_text}'
                    if key
                    else f'[{count_text}]{preview_text}'
                )
                item = QTreeWidgetItem(parent_item, [display])
                item.setExpanded(False)
                self.node_is_leaf[id(item)] = False
                self.node_values[id(item)] = value

                # Store for lazy-loading
                self._lazy_arrays[id(item)] = {'array_data': value, 'loaded': False}

                # Add dummy child so expand arrow shows
                QTreeWidgetItem(item, [tr('cache_json.loading')])
        else:
            # Scalar value
            if isinstance(value, str):
                # Strip leading whitespace from each line for display
                stripped = '\n'.join(line.lstrip() for line in value.split('\n'))
                val_str = f'"{stripped}"'
            elif value is None:
                val_str = 'null'
            elif isinstance(value, bool):
                val_str = str(value).lower()
            else:
                val_str = str(value)

            # Add tooltip for long values (original unstripped value as reference)
            display = f'{key}: {val_str}' if key else val_str
            item = QTreeWidgetItem(parent_item, [display])
            if isinstance(value, str) and len(value) > 60:
                item.setToolTip(0, value)
            # Mark as a leaf scalar so the word-wrap delegate applies to it
            is_wrappable = True
            item.setData(0, _WRAP_ROLE, is_wrappable)
            self.node_values[id(item)] = value
            self.node_is_leaf[id(item)] = True

        return item

    def _load_lazy_array(self, item: QTreeWidgetItem, array_info: _LazyArrayInfo) -> None:
        if array_info['loaded']:
            return
        array_info['loaded'] = True
        if item.childCount() > 0:
            item.removeChild(_preserve_tree_item(item.child(0)))
        for index, value in enumerate(array_info['array_data']):
            self._add_node(item, f'[{index}]', value)

    @staticmethod
    def _remove_loading_placeholder(item: QTreeWidgetItem) -> bool:
        if item.childCount() != 1:
            return False
        first_child = _preserve_tree_item(item.child(0))
        if first_child.text(0) != tr('cache_json.loading'):
            return False
        item.removeChild(first_child)
        return True

    def _load_dict_children(self, item: QTreeWidgetItem, dict_obj: dict[str, JsonValue]) -> None:
        if not self._remove_loading_placeholder(item):
            return
        if len(dict_obj) > _DUPLICATE_COMBINE_LIMIT:
            for key, value in dict_obj.items():
                self._add_node(item, key, value)
            return

        duplicate_map = self._get_duplicate_values_in_dict(dict_obj)
        processed_keys: set[str] = set()
        for key, value in dict_obj.items():
            if key in processed_keys:
                continue
            matching_keys = self._find_keys_with_same_value(dict_obj, key, duplicate_map)
            processed_keys.update(matching_keys)
            self._add_node(item, '+'.join(matching_keys), value)

    def _on_item_expanded(self, item: QTreeWidgetItem) -> None:
        """Called when a tree item is expanded - load children for lazy-loaded arrays/dicts."""
        item_id = id(item)
        updates_enabled = self.tree.updatesEnabled()
        self.tree.setUpdatesEnabled(False)
        try:
            array_info = self._lazy_arrays.get(item_id)
            if array_info is not None:
                self._load_lazy_array(item, array_info)
                return
            value = self.node_values.get(item_id)
            if isinstance(value, dict):
                self._load_dict_children(item, value)
        finally:
            self.tree.setUpdatesEnabled(updates_enabled)

    def _expand_all(self) -> None:
        """Expand all nodes in the tree."""

        def expand_recursive(item: QTreeWidgetItem) -> None:
            # Trigger expansion signal to load lazy children
            item.setExpanded(True)
            for i in range(item.childCount()):
                expand_recursive(_preserve_tree_item(item.child(i)))

        self.tree.setUpdatesEnabled(False)
        try:
            for i in range(self.tree.topLevelItemCount()):
                expand_recursive(_preserve_tree_item(self.tree.topLevelItem(i)))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _collapse_all(self) -> None:
        """Collapse all nodes in the tree."""

        def collapse_recursive(item: QTreeWidgetItem) -> None:
            item.setExpanded(False)
            for i in range(item.childCount()):
                collapse_recursive(_preserve_tree_item(item.child(i)))

        self.tree.setUpdatesEnabled(False)
        try:
            for i in range(self.tree.topLevelItemCount()):
                collapse_recursive(_preserve_tree_item(self.tree.topLevelItem(i)))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _on_search_text_changed(self, _text: str) -> None:
        """Handle search text change."""
        # Debounce the search
        if not hasattr(self, '_search_debounce'):
            self._search_debounce = QTimer(self)
            self._search_debounce.setSingleShot(True)
            self._search_debounce.timeout.connect(self._do_search)

        self._search_debounce.stop()
        self._search_debounce.start(300)

    def _do_search(self) -> None:
        """Perform the actual search."""
        query = self.search_input.text().lower().strip()
        self._search_matches = []
        self._current_match_index = 0

        if not query:
            # Hide nav buttons and match label when search is cleared
            self.prev_match_btn.hide()
            self.next_match_btn.hide()
            self.match_label.hide()
            return

        # Search through all items
        def search_item(item: QTreeWidgetItem) -> None:
            if query in item.text(0).lower():
                self._search_matches.append(item)
            for i in range(item.childCount()):
                search_item(_preserve_tree_item(item.child(i)))

        for i in range(self.tree.topLevelItemCount()):
            search_item(_preserve_tree_item(self.tree.topLevelItem(i)))

        if self._search_matches:
            # Show nav buttons when matches found
            self.prev_match_btn.show()
            self.next_match_btn.show()
            self.match_label.show()
            self._update_match_label()
            # Highlight and auto-expand first match
            self._highlight_match(0)
        else:
            # No matches — hide nav, show count label
            self.prev_match_btn.hide()
            self.next_match_btn.hide()
            self.match_label.setText(tr('ui.cache.cache_json_viewer.no_matches'))
            self.match_label.show()

    def _cycle_to_next_match(self) -> None:
        """Cycle to next search match."""
        if self._search_matches:
            self._current_match_index = (self._current_match_index + 1) % len(self._search_matches)
            self._highlight_match(self._current_match_index)

    def _cycle_to_prev_match(self) -> None:
        """Cycle to previous search match."""
        if self._search_matches:
            self._current_match_index = (self._current_match_index - 1) % len(self._search_matches)
            self._highlight_match(self._current_match_index)

    def _highlight_match(self, index: int) -> None:
        """Highlight a match at the given index - auto-expand parent chain."""
        if 0 <= index < len(self._search_matches):
            item = self._search_matches[index]

            # Auto-expand parent chain to show the match
            parent = item.parent()
            while parent:
                parent.setExpanded(True)
                parent = parent.parent()

            # Expand the item itself if it has children
            if item.childCount() > 0:
                item.setExpanded(True)

            self.tree.scrollToItem(item, QTreeWidget.ScrollHint.PositionAtCenter)
            self.tree.setCurrentItem(item)
            self._current_match_index = index
            self._update_match_label()

    def _update_match_label(self) -> None:
        """Update the match counter label."""
        if self._search_matches:
            self.match_label.setText(
                tr(
                    'ui.cache.cache_json_viewer.match_value_of_value',
                    value0=self._current_match_index + 1,
                    value1=len(self._search_matches),
                )
            )
        else:
            self.match_label.setText(tr('ui.cache.cache_json_viewer.no_matches'))

    def _on_advanced_toggled(self, _state: int) -> None:
        """Toggle advanced mode (show/hide boilerplate fields)."""
        self._show_null_values = self.adv_checkbox.isChecked()
        # Save which nodes are expanded (by key-path), rebuild, then restore
        expanded = self._collect_expanded_paths()
        self._populate_tree()
        self._restore_expanded_paths(expanded)

    def _collect_expanded_paths(self) -> set[ItemPath]:
        """Return a set of key-path tuples for every currently expanded item."""
        paths: set[ItemPath] = set()

        def walk(item: QTreeWidgetItem, path: ItemPath) -> None:
            if item.isExpanded():
                paths.add(path)
                # Only recurse when expanded — lazy-unloaded children are dummies
                for i in range(item.childCount()):
                    child = _preserve_tree_item(item.child(i))
                    key = child.text(0).split(':')[0].strip().strip('[]')
                    walk(child, (*path, key))

        for i in range(self.tree.topLevelItemCount()):
            item = _preserve_tree_item(self.tree.topLevelItem(i))
            key = item.text(0).split(':')[0].strip().strip('[]')
            walk(item, (key,))

        return paths

    def _restore_expanded_paths(self, paths: set[ItemPath]) -> None:
        """Expand items whose key-path is in *paths*, loading lazy children as needed."""

        def walk(item: QTreeWidgetItem, path: ItemPath) -> None:
            if path in paths:
                # setExpanded(True) fires itemExpanded → _on_item_expanded → loads real children
                item.setExpanded(True)
                for i in range(item.childCount()):
                    child = _preserve_tree_item(item.child(i))
                    key = child.text(0).split(':')[0].strip().strip('[]')
                    walk(child, (*path, key))
            # If path is not in saved set, leave collapsed (children are still dummy nodes)

        for i in range(self.tree.topLevelItemCount()):
            item = _preserve_tree_item(self.tree.topLevelItem(i))
            key = item.text(0).split(':')[0].strip().strip('[]')
            walk(item, (key,))

    def _refresh_tree_filtering(self) -> None:
        """Update tree visibility based on current filter settings, preserving expansion state."""
        # Store current expansion state
        expansion_state: dict[int, bool] = {}

        def store_expansion(item: QTreeWidgetItem) -> None:
            item_id = id(item)
            expansion_state[item_id] = item.isExpanded()
            for i in range(item.childCount()):
                store_expansion(_preserve_tree_item(item.child(i)))

        for i in range(self.tree.topLevelItemCount()):
            store_expansion(_preserve_tree_item(self.tree.topLevelItem(i)))

        # Walk tree and toggle visibility based on filtering
        def apply_filtering(item: QTreeWidgetItem) -> None:
            # Get the value stored in this node
            item_id = id(item)
            if item_id in self.node_values:
                value = self.node_values[item_id]
                key = item.text(0).split(':')[0].strip('[]')

                # Check if this item should be visible
                should_skip = self._should_skip_node(key, value)
                item.setHidden(should_skip)

            for i in range(item.childCount()):
                apply_filtering(_preserve_tree_item(item.child(i)))

        for i in range(self.tree.topLevelItemCount()):
            apply_filtering(_preserve_tree_item(self.tree.topLevelItem(i)))

        # Restore expansion state for visible items
        def restore_expansion(item: QTreeWidgetItem) -> None:
            item_id = id(item)
            if item_id in expansion_state and not item.isHidden():
                item.setExpanded(expansion_state[item_id])
            for i in range(item.childCount()):
                restore_expansion(_preserve_tree_item(item.child(i)))

        for i in range(self.tree.topLevelItemCount()):
            restore_expansion(_preserve_tree_item(self.tree.topLevelItem(i)))
