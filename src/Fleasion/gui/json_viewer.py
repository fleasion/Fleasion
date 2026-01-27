"""JSON tree viewer widget."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QMessageBox,
)

from ..utils import get_icon_path


class JsonTreeViewer(QDialog):
    """JSON tree viewer dialog."""

    def __init__(
        self, parent, data, filename: str, on_import_ids, on_import_replacement
    ):
        super().__init__(parent)
        self.setWindowTitle(f'JSON - {filename}')
        self.resize(700, 500)

        # Set window flags to allow minimize/maximize
        self.setWindowFlags(
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )

        self.data = data
        self.on_import_ids = on_import_ids
        self.on_import_replacement = on_import_replacement
        self.node_values = {}
        self.node_is_leaf = {}
        self._setup_ui()
        self._populate_tree()
        self._set_icon()

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI."""
        layout = QVBoxLayout()
        layout.setContentsMargins(10, 10, 10, 10)

        # Search debounce timer
        self._search_debounce = QTimer()
        self._search_debounce.setSingleShot(True)
        self._search_debounce.timeout.connect(self._do_search)

        # Search bar
        search_layout = QHBoxLayout()
        search_layout.addWidget(QLabel('Search:'))
        self.search_input = QLineEdit()
        self.search_input.textChanged.connect(self._on_search_text_changed)
        search_layout.addWidget(self.search_input)

        clear_btn = QPushButton('Clear')
        clear_btn.clicked.connect(lambda: self.search_input.clear())
        search_layout.addWidget(clear_btn)

        expand_btn = QPushButton('Expand All')
        expand_btn.clicked.connect(self._expand_all)
        search_layout.addWidget(expand_btn)

        collapse_btn = QPushButton('Collapse All')
        collapse_btn.clicked.connect(self._collapse_all)
        search_layout.addWidget(collapse_btn)

        layout.addLayout(search_layout)

        # Tree widget
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_change)
        layout.addWidget(self.tree)

        # Selection label
        self.selection_label = QLabel('Selected: 0 values')
        layout.addWidget(self.selection_label)

        # Import buttons
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(QLabel('Import selected as:'))

        ids_btn = QPushButton('IDs to Replace')
        ids_btn.clicked.connect(self._import_as_replace_ids)
        btn_layout.addWidget(ids_btn)

        repl_btn = QPushButton('Replacement ID')
        repl_btn.clicked.connect(self._import_as_replacement)
        btn_layout.addWidget(repl_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.setLayout(layout)

    def _add_node(self, parent_item, key: str, value) -> QTreeWidgetItem:
        """Add a node to the tree."""
        if isinstance(value, (dict, list)):
            items = value.items() if isinstance(value, dict) else enumerate(value)
            fmt = '{...}' if isinstance(value, dict) else '[...]'
            display = f'{key}: {fmt}' if key else fmt
            item = QTreeWidgetItem(parent_item, [display])
            item.setExpanded(False)
            self.node_is_leaf[id(item)] = False
            for k, v in items:
                self._add_node(item, f'[{k}]' if isinstance(value, list) else k, v)
        else:
            val_str = (
                'null'
                if value is None
                else str(value).lower()
                if isinstance(value, bool)
                else f'"{value}"'
                if isinstance(value, str)
                else str(value)
            )
            display = f'{key}: {val_str}' if key else val_str
            item = QTreeWidgetItem(parent_item, [display])
            self.node_values[id(item)] = value
            self.node_is_leaf[id(item)] = True
        return item

    def _populate_tree(self):
        """Populate the tree with data."""
        self.tree.clear()
        if isinstance(self.data, (dict, list)):
            items = (
                self.data.items() if isinstance(self.data, dict) else enumerate(self.data)
            )
            for k, v in items:
                self._add_node(
                    self.tree, f'[{k}]' if isinstance(self.data, list) else k, v
                )
        else:
            self._add_node(self.tree, '', self.data)

    def _get_all_leaf_descendants(self, item: QTreeWidgetItem) -> list[QTreeWidgetItem]:
        """Get all leaf descendants of an item."""
        if self.node_is_leaf.get(id(item)):
            return [item]
        leaves = []
        for i in range(item.childCount()):
            leaves.extend(self._get_all_leaf_descendants(item.child(i)))
        return leaves

    def _get_numeric_values(self) -> list[int]:
        """Get numeric values from selected items."""
        leaves = []
        leaf_ids = set()  # Track IDs to avoid duplicates

        for item in self.tree.selectedItems():
            if self.node_is_leaf.get(id(item)):
                if id(item) not in leaf_ids:
                    leaves.append(item)
                    leaf_ids.add(id(item))
            else:
                for descendant in self._get_all_leaf_descendants(item):
                    if id(descendant) not in leaf_ids:
                        leaves.append(descendant)
                        leaf_ids.add(id(descendant))

        values = []
        for item in leaves:
            val = self.node_values.get(id(item))
            if not isinstance(val, bool):
                try:
                    values.append(int(val))
                except (ValueError, TypeError):
                    pass
        return values

    def _on_selection_change(self):
        """Handle selection change."""
        vals = self._get_numeric_values()
        self.selection_label.setText(f'Selected: {len(vals)} numeric value(s)')

    def _on_search_text_changed(self):
        """Handle search text change with debounce."""
        self._search_debounce.stop()
        self._search_debounce.start(250)  # 250ms debounce

    def _do_search(self):
        """Execute the actual search after debounce."""
        query = self.search_input.text().lower().strip()
        if not query:
            return

        # Disable updates during search
        self.tree.setUpdatesEnabled(False)

        try:
            # Clear selection
            self.tree.clearSelection()

            # Find matches
            matches = []

            def search_item(item):
                if query in item.text(0).lower():
                    matches.append(item)
                    # Expand parents
                    parent = item.parent()
                    while parent:
                        parent.setExpanded(True)
                        parent = parent.parent()
                for i in range(item.childCount()):
                    search_item(item.child(i))

            for i in range(self.tree.topLevelItemCount()):
                search_item(self.tree.topLevelItem(i))

            if matches:
                for item in matches:
                    item.setSelected(True)
                self.tree.scrollToItem(matches[0])
        finally:
            self.tree.setUpdatesEnabled(True)

    def _expand_all(self):
        """Expand all items."""
        self.tree.expandAll()

    def _collapse_all(self):
        """Collapse all items."""
        self.tree.collapseAll()

    def _import_as_replace_ids(self):
        """Import selected values as IDs to replace."""
        vals = self._get_numeric_values()
        if vals:
            self.on_import_ids(vals)
            self.accept()
        else:
            QMessageBox.information(self, 'Info', 'No numeric values selected')

    def _import_as_replacement(self):
        """Import selected value as replacement ID."""
        vals = self._get_numeric_values()
        if not vals:
            QMessageBox.information(self, 'Info', 'No numeric values selected')
            return
        if len(vals) > 1:
            reply = QMessageBox.question(
                self,
                'Multiple Values',
                f'Only the first value ({vals[0]}) will be used. Continue?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self.on_import_replacement(vals[0])
        self.accept()
