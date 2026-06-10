"""Replacer config window."""

import json
from copy import deepcopy
from pathlib import Path
import sys
import time
from typing import Union
from urllib.error import URLError

from PyQt6.QtCore import Qt, QByteArray, QSize, pyqtSignal
from PyQt6.QtGui import QBrush, QPen, QPixmap, QPainter, QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QStyleOptionMenuItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ..utils import APP_NAME, CONFIGS_FOLDER, PREJSONS_DIR, format_count, get_icon_path, log_buffer, open_folder
from ..utils.http import http_head_status
from .file_drop import FileDropLineEdit
from .json_viewer import JsonTreeViewer
from .proxy_gate import ProxyGate


_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_KIND = Qt.ItemDataRole.UserRole.value + 1
_ROLE_SORT_BASE = Qt.ItemDataRole.UserRole.value + 16
_KIND_PROFILE = 'profile'
_KIND_GROUP = 'group'
_MIXED_STATUS = '—'
_DRAG_GROUP_COLORS = ('#2d6cdf', '#2f9e44', '#f08c00', '#ae3ec9', '#0ca678')
_GROUP_ICON = '🗀'
_TREE_INDENT_PX = 9
_GROUP_ROW_HEIGHT_PX = 24
_GROUP_CONTENT_INDENT_SPACES = 5
_PROFILE_NAME_COLUMN = 1
_GROUP_GUIDE_GUTTER_PX = 2
_GROUP_GUIDE_STEP_PX = 15
_CONFIG_MENU_ROW_HEIGHT_PX = 28
_CONFIG_MENU_SCREEN_MARGIN_PX = 12
_CONFIG_MENU_OPEN_RELEASE_GRACE_SEC = 0.25


class UndoManager:
    """Undo history manager."""

    def __init__(self, max_history: int = 50):
        self.history: list[list] = []
        self.future: list[list] = []
        self.max_history = max_history

    def save_state(self, rules: list):
        """Save a state to history."""
        self.history.append(deepcopy(rules))
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.future.clear()

    def undo(self) -> list | None:
        """Undo to previous state."""
        if len(self.history) > 1:
            self.future.append(self.history.pop())
            return deepcopy(self.history[-1])
        if len(self.history) == 1:
            return deepcopy(self.history[0])
        return None

    def redo(self) -> list | None:
        """Redo a previously undone state."""
        if self.future:
            state = self.future.pop()
            self.history.append(state)
            return deepcopy(state)
        return None

    def clear(self):
        """Clear history."""
        self.history.clear()
        self.future.clear()


class ReplacerTreeItem(QTreeWidgetItem):
    """Tree item with per-column sort keys for profile and group rows."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        left = self.data(column, _ROLE_SORT_BASE)
        right = other.data(column, _ROLE_SORT_BASE)
        if left is not None and right is not None:
            return left < right
        return self.text(column).lower() < other.text(column).lower()


class ReplacerRulesTree(QTreeWidget):
    """Constrained tree drag/drop for moving profiles and groups."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self._owner = owner

    def startDrag(self, supported_actions):  # noqa: N802
        if not self._owner._selected_movable_paths():
            return
        self._owner._set_drag_hint_active(True)
        try:
            super().startDrag(supported_actions)
        finally:
            self._owner._set_drag_hint_active(False)

    def dragEnterEvent(self, event):  # noqa: N802
        if self._owner._selected_movable_paths():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):  # noqa: N802
        target = self.itemAt(event.position().toPoint())
        if self._owner._is_valid_item_drop(target, self.dropIndicatorPosition()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):  # noqa: N802
        # Keep target highlights visible while the drag cursor is outside the
        # window; startDrag/dropEvent clear them when the drag actually ends.
        super().dragLeaveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        target = self.itemAt(event.position().toPoint())
        if self._owner._move_selected_items_to_drop(target, self.dropIndicatorPosition()):
            event.acceptProposedAction()
        else:
            event.ignore()
        self._owner._set_drag_hint_active(False)

    def paintEvent(self, event):  # noqa: N802
        super().paintEvent(event)
        self._owner._paint_group_guides(self.viewport())


class _ConfigMenuRow(QWidget):
    """Full-width row painted with the native QMenu item style."""

    activated = pyqtSignal(str)
    toggled = pyqtSignal(str, bool)

    def __init__(
        self,
        name: str,
        parent_menu,
        *,
        checkable: bool = False,
        checked: bool = False,
        icon: QIcon | None = None,
    ):
        super().__init__()
        self._name = name
        self._parent_menu = parent_menu
        self._checkable = checkable
        self._checked = checked
        self._icon = icon or QIcon()
        self._pressed = False
        self._hovered = False
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setFixedHeight(_CONFIG_MENU_ROW_HEIGHT_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def sizeHint(self):  # noqa: N802
        option = self._style_option()
        return self.style().sizeFromContents(
            QStyle.ContentsType.CT_MenuItem,
            option,
            QSize(0, _CONFIG_MENU_ROW_HEIGHT_PX),
            self,
        )

    def isChecked(self) -> bool:  # noqa: N802
        return self._checked

    def setChecked(self, checked: bool):  # noqa: N802
        if self._checked == checked:
            return
        self._checked = checked
        self.update()

    def click(self):
        self._activate()

    def _style_option(self) -> QStyleOptionMenuItem:
        option = QStyleOptionMenuItem()
        option.initFrom(self)
        option.rect = self.rect()
        option.menuItemType = QStyleOptionMenuItem.MenuItemType.Normal
        option.checkType = (
            QStyleOptionMenuItem.CheckType.NonExclusive
            if self._checkable
            else QStyleOptionMenuItem.CheckType.NotCheckable
        )
        option.checked = self._checked
        option.menuHasCheckableItems = self._checkable
        option.text = self._name
        option.icon = self._icon
        option.maxIconWidth = self._icon.actualSize(QSize(16, 16)).width() if not self._icon.isNull() else 0
        option.reservedShortcutWidth = 0
        if self._hovered:
            option.state |= QStyle.StateFlag.State_Selected
        else:
            option.state &= ~QStyle.StateFlag.State_Selected
        return option

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        self.style().drawControl(QStyle.ControlElement.CE_MenuItem, self._style_option(), painter, self)

    def enterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                self._activate()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _activate(self):
        if self._parent_menu._should_ignore_opening_release():
            return
        if self._checkable:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._name, self._checked)
        else:
            self.activated.emit(self._name)


class _ScrollableConfigMenu(QMenu):
    """Config picker popup that scrolls when the config list exceeds the screen."""

    item_selected = pyqtSignal(str)
    item_toggled = pyqtSignal(str, bool)

    def __init__(self, parent=None, *, checkable: bool = False):
        super().__init__(parent)
        self._checkable = checkable
        self._minimum_width = 0
        self._natural_content_size = QSize(0, 0)
        self._opening_release_deadline = 0.0
        self.item_widgets: dict[str, QWidget] = {}

        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName('ConfigMenuScrollArea')
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.installEventFilter(self)
        self.scroll_area.viewport().installEventFilter(self)

        action = QWidgetAction(self)
        action.setDefaultWidget(self.scroll_area)
        self.addAction(action)
        self.aboutToShow.connect(self._guard_opening_mouse_release)

    def set_entries(self, entries: list[dict], *, minimum_width: int = 0):
        """Replace the displayed config rows."""
        old_container = self.scroll_area.takeWidget()
        if old_container is not None:
            old_container.deleteLater()

        self._minimum_width = max(0, minimum_width)
        self.item_widgets.clear()

        container = QWidget()
        container.setObjectName('ConfigMenuContainer')
        container.installEventFilter(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        for entry in entries:
            name = str(entry.get('name', ''))
            row = _ConfigMenuRow(
                name,
                self,
                checkable=self._checkable,
                checked=bool(entry.get('checked', False)),
                icon=entry.get('icon', QIcon()),
            )
            row.activated.connect(self._select_item)
            row.toggled.connect(self.item_toggled)
            row.installEventFilter(self)
            layout.addWidget(row)
            self.item_widgets[name] = row

        if not entries:
            row = QLabel('No configs')
            row.setStyleSheet('padding: 5px 8px; color: palette(placeholder-text);')
            row.setFixedHeight(_CONFIG_MENU_ROW_HEIGHT_PX)
            row.installEventFilter(self)
            layout.addWidget(row)

        self.scroll_area.setWidget(container)
        container.adjustSize()
        self._natural_content_size = container.sizeHint()
        self._set_popup_content_size(self._natural_content_size.height())

    def constrain_to_button(self, button: QPushButton):
        """Bound the popup to the screen containing the owning button."""
        if button is None:
            return
        anchor = button.mapToGlobal(button.rect().bottomLeft())
        screen = button.screen()
        if screen is None:
            app = QApplication.instance()
            if app is not None:
                screen = app.screenAt(anchor)
        if screen is None:
            return
        self.constrain_to_available_geometry(screen.availableGeometry(), anchor.y())

    def constrain_to_available_geometry(self, available_geometry, anchor_y=None):
        """Limit height to visible screen space; the scroll bar appears as needed."""
        if available_geometry is None:
            return

        if anchor_y is None:
            available_height = available_geometry.height()
        else:
            space_below = available_geometry.bottom() - anchor_y
            space_above = anchor_y - available_geometry.top()
            available_height = max(space_below, space_above)

        max_height = max(1, available_height - _CONFIG_MENU_SCREEN_MARGIN_PX)
        max_width = max(1, available_geometry.width() - _CONFIG_MENU_SCREEN_MARGIN_PX)
        self._set_popup_content_size(max_height, max_width=max_width)
        self.adjustSize()

    def _set_popup_content_size(self, max_height: int, *, max_width: int | None = None):
        natural = self._natural_content_size
        if natural.height() <= 0:
            return

        height = min(natural.height(), max(1, max_height))
        scrollbar_width = self.scroll_area.verticalScrollBar().sizeHint().width()
        needs_scrollbar = natural.height() > height
        width = max(natural.width(), self._minimum_width)
        if needs_scrollbar:
            width += scrollbar_width
        if max_width is not None:
            width = min(width, max_width)

        viewport_width = max(1, width - (scrollbar_width if needs_scrollbar else 0))
        widget = self.scroll_area.widget()
        if widget is not None:
            widget.setMinimumWidth(viewport_width)
        self.scroll_area.setFixedSize(max(1, width), height)

    def _select_item(self, name: str):
        self.item_selected.emit(name)
        self.hide()

    def _guard_opening_mouse_release(self):
        self._opening_release_deadline = time.monotonic() + _CONFIG_MENU_OPEN_RELEASE_GRACE_SEC

    def _should_ignore_opening_release(self) -> bool:
        if not self._opening_release_deadline:
            return False
        if time.monotonic() <= self._opening_release_deadline:
            self._opening_release_deadline = 0.0
            return True
        self._opening_release_deadline = 0.0
        return False

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent

        if event.type() == QEvent.Type.MouseButtonPress:
            self._opening_release_deadline = 0.0
        elif event.type() == QEvent.Type.MouseButtonRelease and self._should_ignore_opening_release():
            return True
        return super().eventFilter(obj, event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if event is None:
            return
        if self._should_ignore_opening_release():
            return
        action = self.actionAt(event.pos())
        if isinstance(action, QWidgetAction) and action.defaultWidget() == self.scroll_area:
            return
        super().mouseReleaseEvent(event)


class ReplacerConfigWindow(QDialog):
    """Replacer configuration window with tabs."""

    def __init__(self, config_manager, proxy_master=None, mod_manager=None, roblox_monitor=None, system_tray=None):
        super().__init__()
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.roblox_monitor = roblox_monitor
        self._system_tray = system_tray
        self.undo_manager = UndoManager()
        self.undo_manager.save_state(self.config_manager.replacement_rules)
        self.config_enabled_vars = {}
        self._asset_types_popup_last_closed = 0.0
        self._dialog_asset_types_popup_last_closed = 0.0
        self._prejsons_dialog: QDialog | None = None
        self._proxy_gates: list[ProxyGate] = []

        self.setWindowTitle(f'{APP_NAME} - Dashboard')
        self.resize(900, 750)
        self.setMinimumSize(800, 650)
        if sys.platform == 'darwin':
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)

        # Set window flags to allow minimize/maximize
        flags = (
            Qt.WindowType.Window |
            Qt.WindowType.WindowMinimizeButtonHint |
            Qt.WindowType.WindowMaximizeButtonHint |
            Qt.WindowType.WindowCloseButtonHint
        )
        # Apply always on top if enabled
        if self.config_manager.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

        self._setup_ui()
        self._set_icon()
        self._refresh_tree()

        # Restore geometry
        geometry_hex = self.config_manager.window_geometry
        if geometry_hex:
            self.restoreGeometry(QByteArray.fromHex(geometry_hex.encode('utf-8')))

    def closeEvent(self, event):
        """Save window geometry on close."""
        self.config_manager.window_geometry = self.saveGeometry().toHex().data().decode('utf-8')
        self._unregister_module_interceptors()
        if (
            self._system_tray is not None
            and self._system_tray.config_manager.close_to_tray
            and not self._system_tray._exiting
        ):
            try:
                self._system_tray.notify_dashboard_closed()
            except Exception:
                pass
        super().closeEvent(event)

    def _unregister_module_interceptors(self):
        if self.proxy_master is None:
            return
        for module in getattr(self, '_registered_module_interceptors', ()):
            try:
                self.proxy_master.unregister_module_interceptor(module)
            except Exception as exc:
                log_buffer.log('Proxy', f'Failed to unregister dashboard interceptor: {exc}')
        self._registered_module_interceptors = []

    def _set_icon(self):
        """Set window icon."""
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self):
        """Setup the UI with tabs."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create Replacer tab
        replacer_tab = self._proxy_required(self._create_replacer_tab())
        self.tab_widget.addTab(replacer_tab, 'Replacer')

        # Create Cache tab if proxy_master is available
        if self.proxy_master and hasattr(self.proxy_master, 'cache_manager'):
            cache_tab = self._proxy_required(self._create_cache_tab())
            self.tab_widget.addTab(cache_tab, 'Scraper')

        # Create Modifications tab
        if self._mod_manager is not None:
            from .modifications_tab import ModificationsTab
            modifications_tab = ModificationsTab(self._mod_manager, self.roblox_monitor)
            self.tab_widget.addTab(modifications_tab, 'Modifications')

        # Create Rando Stuff tab
        from .rando_stuff_tab import RandoStuffTab
        self._rando_stuff_tab = RandoStuffTab(
            config_manager=self.config_manager,
            proxy_master=self.proxy_master,
        )
        self._registered_module_interceptors = []

        # Create Subplace Joiner tab
        from .subplace_joiner_tab import SubplaceJoinerTab
        self._subplace_tab = SubplaceJoinerTab(rando_tab=self._rando_stuff_tab)
        self._rando_stuff_tab.selected_account_changed.connect(self._subplace_tab.set_selected_account)
        self.tab_widget.addTab(self._proxy_required(self._subplace_tab), 'Subplace Joiner')
        if self.proxy_master is not None:
            self.proxy_master.register_module_interceptor(self._subplace_tab)
            self._registered_module_interceptors.append(self._subplace_tab)

        self.tab_widget.addTab(self._rando_stuff_tab, 'Miscellaneous')
        if self.proxy_master is not None:
            self.proxy_master.register_module_interceptor(self._rando_stuff_tab)
            self._registered_module_interceptors.append(self._rando_stuff_tab)

        # Create Settings tab
        from .settings_tab import SettingsTab
        self._settings_tab = SettingsTab(self.config_manager, system_tray=self._system_tray)
        self.tab_widget.addTab(self._settings_tab, 'Settings')

        main_layout.addWidget(self.tab_widget)

        self.setLayout(main_layout)
        self.set_proxy_features_enabled(self.config_manager.proxy_features_enabled)

        # Setup keyboard shortcuts
        from PyQt6.QtGui import QKeySequence, QShortcut

        undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        undo_shortcut.activated.connect(self._do_undo)

        delete_shortcut = QShortcut(QKeySequence('Delete'), self)
        delete_shortcut.activated.connect(self._delete_selected)

        redo_shortcut = QShortcut(QKeySequence('Ctrl+Y'), self)
        redo_shortcut.activated.connect(self._do_redo)

        escape_shortcut = QShortcut(QKeySequence('Escape'), self)
        escape_shortcut.activated.connect(self.close)

    def _proxy_required(self, widget: QWidget) -> ProxyGate:
        gate = ProxyGate(widget)
        self._proxy_gates.append(gate)
        return gate

    def set_proxy_features_enabled(self, enabled: bool):
        for gate in self._proxy_gates:
            gate.set_proxy_enabled(enabled)
        if hasattr(self, '_rando_stuff_tab') and hasattr(self._rando_stuff_tab, 'set_proxy_features_enabled'):
            self._rando_stuff_tab.set_proxy_features_enabled(enabled)

    def _create_replacer_tab(self):
        """Create the replacer configuration tab."""
        replacer_widget = QWidget()
        replacer_layout = QVBoxLayout()
        replacer_layout.setContentsMargins(0, 0, 0, 0)

        # Config selector section
        self._create_config_section(replacer_layout)

        # Rules tree section
        self._create_tree_section(replacer_layout)

        # Edit section
        self._create_edit_section(replacer_layout)

        # Footer
        self._create_footer(replacer_layout)

        replacer_widget.setLayout(replacer_layout)
        return replacer_widget

    def _create_cache_tab(self):
        """Create the cache viewer tab."""
        from ..cache import CacheViewerTab

        cache_scraper = getattr(self.proxy_master, 'cache_scraper', None)
        tab = CacheViewerTab(
            self.proxy_master.cache_manager,
            cache_scraper,
            self,
            config_manager=self.config_manager
        )
        # Store direct reference so Send-to-Replacer can find the entry fields
        # regardless of how Qt re-parents the widget when added to QTabWidget.
        tab._replacer_window_ref = self
        self._cache_viewer_tab = tab
        return tab

    def _create_config_section(self, parent_layout):
        """Create the configuration selector section."""
        config_group = QGroupBox('Configuration')
        config_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        config_layout = QVBoxLayout()

        # Row 1: Configuration controls
        row1 = QHBoxLayout()
        editing_label = QLabel('Editing:')
        editing_label.setFixedWidth(50)
        row1.addWidget(editing_label)

        # Use button with menu (same style as enabled configs)
        # Prepend a single space plus a tiny hair-space to give a subtle gap
        self.config_menu_btn = QPushButton(' \u200A' + self.config_manager.last_config)
        self.config_menu = _ScrollableConfigMenu(self.config_menu_btn)
        self.config_menu.aboutToShow.connect(self._rebuild_editing_menu)
        self.config_menu.item_selected.connect(self._on_config_select)
        self.config_menu_btn.setMenu(self.config_menu)
        self.config_menu_btn.pressed.connect(self._rebuild_editing_menu)
        row1.addWidget(self.config_menu_btn)

        self._rebuild_editing_menu()

        row1.addSpacing(12)

        enabled_label = QLabel('Enabled:')
        enabled_label.setFixedWidth(50)
        row1.addWidget(enabled_label)

        self.enabled_menu_btn = QPushButton('Select...')
        self.enabled_menu = _ScrollableConfigMenu(self.enabled_menu_btn, checkable=True)
        self.enabled_menu.aboutToShow.connect(self._rebuild_enabled_menu)
        self.enabled_menu.item_toggled.connect(self._on_config_toggle)
        self.enabled_menu_btn.setMenu(self.enabled_menu)
        self.enabled_menu_btn.pressed.connect(self._rebuild_enabled_menu)
        row1.addWidget(self.enabled_menu_btn)

        self._rebuild_enabled_menu()

        row1.addSpacing(8)

        separator = QLabel('|')
        separator.setStyleSheet('padding-bottom: 6px;')
        row1.addWidget(separator)

        row1.addSpacing(8)

        for text, action in [
            ('New', 'new'),
            ('Duplicate', 'dup'),
            ('Rename', 'rename'),
            ('Delete', 'delete'),
        ]:
            btn = QPushButton(text)
            btn.clicked.connect(lambda checked, a=action: self._config_action(a))
            row1.addWidget(btn)

        # Removed: No Textures checkbox

        row1.addStretch()
        config_layout.addLayout(row1)

        config_group.setLayout(config_layout)
        parent_layout.addWidget(config_group)

    def _create_tree_section(self, parent_layout):
        """Create the rules tree section."""
        # Label
        label_layout = QHBoxLayout()
        title_label = QLabel('Replacement Profiles:')
        title_label.setStyleSheet('font-weight: bold; padding-left: 5px;')
        label_layout.addWidget(title_label)

        label_layout.addStretch()
        parent_layout.addLayout(label_layout)

        # Tree
        self.tree = ReplacerRulesTree(self)
        self.tree.setHeaderLabels(['Status', 'Profile Name', 'Mode', 'Asset IDs', 'Replacement'])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemExpanded.connect(lambda item: self._set_group_expanded(item, True))
        self.tree.itemCollapsed.connect(lambda item: self._set_group_expanded(item, False))
        self.tree.itemSelectionChanged.connect(self.tree.viewport().update)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setDropIndicatorShown(True)
        self.tree.setSortingEnabled(True)
        self.tree.setIndentation(_TREE_INDENT_PX)

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        parent_layout.addWidget(self.tree)

    def _create_edit_section(self, parent_layout):
        """Create the add/edit profile section."""
        edit_group = QGroupBox('Add/Edit Profile')
        edit_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        edit_layout = QVBoxLayout()
        edit_layout.setSpacing(4)

        # Profile name
        name_layout = QHBoxLayout()
        name_layout.setSpacing(5)
        label0 = QLabel('Profile Name:')
        label0.setFixedWidth(85)
        name_layout.addWidget(label0)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText('Optional profile name')
        name_layout.addWidget(self.name_entry)
        edit_layout.addLayout(name_layout)

        # Asset IDs
        ids_layout = QHBoxLayout()
        ids_layout.setSpacing(5)
        label = QLabel('Asset IDs:')
        label.setFixedWidth(85)
        ids_layout.addWidget(label)
        self.replace_entry = QLineEdit()
        self.replace_entry.setPlaceholderText('IDs or AssetTypes separated by commas, spaces, or semicolons')
        ids_layout.addWidget(self.replace_entry)
        
        # Add Asset Types filter button
        self.asset_types_btn = QPushButton('Asset Types')
        self.asset_types_btn.setFixedWidth(80)
        self.asset_types_btn.clicked.connect(self._show_asset_types_popup)
        from ..cache.cache_viewer import CategoryFilterPopup
        self.asset_types_popup = CategoryFilterPopup(parent=self)
        self.asset_types_popup.filters_changed.connect(self._on_asset_types_changed)
        self.asset_types_popup.aboutToHide.connect(self._mark_asset_types_popup_closed)
        ids_layout.addWidget(self.asset_types_btn)
        
        edit_layout.addLayout(ids_layout)

        # Replacement field (auto-detects mode)
        replace_layout = QHBoxLayout()
        replace_layout.setSpacing(5)
        label2 = QLabel('Replace With:')
        label2.setFixedWidth(85)
        replace_layout.addWidget(label2)
        self.replacement_entry = FileDropLineEdit()
        self.replacement_entry.setPlaceholderText('ID, URL (http://...), path (C:\\...), or empty to remove')
        replace_layout.addWidget(self.replacement_entry)
        browse_btn = QPushButton('Browse...')
        browse_btn.clicked.connect(self._browse_local_file)
        browse_btn.setFixedWidth(80)
        replace_layout.addWidget(browse_btn)
        edit_layout.addLayout(replace_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        for text, callback in [
            ('Add New', self._add_rule),
            ('Load Selected', self._load_selected),
            ('Update Selected', self._update_selected),
        ]:
            btn = QPushButton(text)
            btn.setMinimumWidth(130)
            btn.clicked.connect(callback)
            btn_layout.addWidget(btn)
        btn_layout.addStretch()
        import_btn = QPushButton('Scraped games...')
        import_btn.setMinimumWidth(130)
        import_btn.clicked.connect(self._open_prejsons_browser)
        btn_layout.addWidget(import_btn)
        edit_layout.addLayout(btn_layout)

        edit_group.setLayout(edit_layout)
        # Prevent edit group from expanding vertically
        edit_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        parent_layout.addWidget(edit_group)

    def _create_footer(self, parent_layout):
            """Create the footer section with buttons snapped to the right."""
            footer_widget = QWidget()
            footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            footer_layout = QHBoxLayout(footer_widget)
            footer_layout.setContentsMargins(8, 4, 8, 4)

            path_label = QLabel(f'Configs: {CONFIGS_FOLDER}')
            path_label.setStyleSheet('color: gray; font-size: 8pt; padding-left: 5px;')
            footer_layout.addWidget(path_label)

            footer_layout.addStretch()

            help_btn = QPushButton('?')
            help_btn.setMaximumWidth(25)
            help_btn.setToolTip('View keybinds')
            help_btn.clicked.connect(self._show_keybinds_help)
            footer_layout.addWidget(help_btn)

            clear_cache_btn = QPushButton('Clear Cache')
            clear_cache_btn.clicked.connect(self._clear_roblox_cache)
            footer_layout.addWidget(clear_cache_btn)

            configs_btn = QPushButton('Open Configs')
            configs_btn.clicked.connect(lambda: open_folder(CONFIGS_FOLDER))
            footer_layout.addWidget(configs_btn)

            undo_btn = QPushButton('Undo (Ctrl+Z)')
            undo_btn.clicked.connect(self._do_undo)
            footer_layout.addWidget(undo_btn)

            parent_layout.addWidget(footer_widget)

    def _clear_roblox_cache(self):
        from .delete_cache import DeleteCacheWindow
        window = DeleteCacheWindow()
        window.show()

    def _show_keybinds_help(self):
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle('Keybinds')
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(
            '<b>All keybinds.</b><br>'
            '- Ctrl+Z — Undo last change<br>'
            '- Ctrl+Y — Redo last change<br>'
            '- Ctrl+A — Select all rows<br>'
            '- Delete — Delete selected rows<br>'
            '<br>'
            '<b>Tips.</b><br>'
            '- Right-click a profile to delete, enable, or disable it'
        )
        msg.exec()

    def _open_prejsons_browser(self):
        """Open the PreJsons browser dialog."""
        from .prejsons_dialog import PreJsonsDialog

        try:
            if self._prejsons_dialog is not None:
                self._prejsons_dialog.show()
                self._prejsons_dialog.raise_()
                self._prejsons_dialog.activateWindow()
                return
        except RuntimeError:
            self._prejsons_dialog = None

        dialog = PreJsonsDialog(self)
        dialog.destroyed.connect(lambda *_: setattr(self, '_prejsons_dialog', None))
        self._prejsons_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _rebuild_enabled_menu(self, *, sync_from_disk: bool = True):
        """Rebuild the enabled configs menu."""
        if sync_from_disk:
            self._sync_config_state_from_disk(update_enabled_menu=False)

        self.config_enabled_vars.clear()

        # Clean up enabled configs that no longer exist on disk
        current_configs = self.config_manager.config_names
        enabled = self.config_manager.enabled_configs
        for name in enabled[:]:  # Copy list to allow modification
            if name not in current_configs:
                self.config_manager.set_config_enabled(name, False)

        self.enabled_menu.set_entries(
            [
                {
                    'name': name,
                    'checked': self.config_manager.is_config_enabled(name),
                }
                for name in current_configs
            ],
            minimum_width=self.enabled_menu_btn.width() if hasattr(self, 'enabled_menu_btn') else 0,
        )
        self.config_enabled_vars.update(self.enabled_menu.item_widgets)

        self._update_enabled_menu_text()
        self.enabled_menu.constrain_to_button(self.enabled_menu_btn)

    def _update_enabled_menu_text(self):
        """Update the enabled menu button text."""
        enabled = self.config_manager.enabled_configs
        if not enabled:
            self.enabled_menu_btn.setText('No Configs Enabled')
        elif len(enabled) == 1:
            self.enabled_menu_btn.setText(enabled[0])
        else:
            self.enabled_menu_btn.setText(f'{len(enabled)} configs enabled')
        # Keep the Editing button styled to reflect whether the currently
        # selected editing profile is enabled or not.
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _on_config_toggle(self, name: str, checked: bool):
        """Handle config toggle."""
        self.config_manager.set_config_enabled(name, checked)
        self._update_enabled_menu_text()
        status = 'Enabled' if checked else 'Disabled'
        log_buffer.log('Config', f'{status}: {name}')
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    @staticmethod
    def _is_group(entry: dict) -> bool:
        return isinstance(entry, dict) and entry.get('type') == _KIND_GROUP

    @staticmethod
    def _is_profile(entry: dict) -> bool:
        return isinstance(entry, dict) and entry.get('type') != _KIND_GROUP

    def _iter_profiles(self, entries: list):
        for entry in entries:
            if self._is_group(entry):
                yield from self._iter_profiles(entry.get('children', []))
            elif self._is_profile(entry):
                yield entry

    def _config_has_groups(self, entries: list | None = None) -> bool:
        if entries is None:
            entries = self.config_manager.replacement_rules
        for entry in entries:
            if self._is_group(entry):
                return True
            if isinstance(entry, dict) and self._config_has_groups(entry.get('children', [])):
                return True
        return False

    def _entry_at_path(self, entries: list, path: tuple[int, ...]) -> dict | None:
        current_entries = entries
        entry = None
        for index in path:
            if index < 0 or index >= len(current_entries):
                return None
            entry = current_entries[index]
            current_entries = entry.get('children', []) if self._is_group(entry) else []
        return entry

    def _entries_at_parent_path(self, entries: list, parent_path: tuple[int, ...]) -> list | None:
        if not parent_path:
            return entries
        parent = self._entry_at_path(entries, parent_path)
        if not self._is_group(parent):
            return None
        return parent.setdefault('children', [])

    def _set_entry_at_path(self, entries: list, path: tuple[int, ...], entry: dict) -> bool:
        parent_entries = self._entries_at_parent_path(entries, path[:-1])
        if parent_entries is None or not path or path[-1] >= len(parent_entries):
            return False
        parent_entries[path[-1]] = entry
        return True

    def _remove_paths(self, entries: list, paths: set[tuple[int, ...]], prefix: tuple[int, ...] = ()) -> list:
        kept = []
        for index, entry in enumerate(entries):
            path = prefix + (index,)
            if path in paths:
                continue
            if self._is_group(entry):
                entry = deepcopy(entry)
                entry['children'] = self._remove_paths(entry.get('children', []), paths, path)
            kept.append(entry)
        return kept

    def _prune_descendant_paths(self, paths: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
        result: list[tuple[int, ...]] = []
        for path in sorted(paths, key=lambda p: (len(p), p)):
            if not any(len(path) > len(parent) and path[:len(parent)] == parent for parent in result):
                result.append(path)
        return result

    def _profile_count(self) -> int:
        return sum(1 for _ in self._iter_profiles(self.config_manager.replacement_rules))

    def _group_summary(self, group: dict) -> tuple[int, int, str, int]:
        profiles = list(self._iter_profiles(group.get('children', [])))
        profile_count = len(profiles)
        id_count = sum(len(profile.get('replace_ids', [])) for profile in profiles)
        enabled_count = sum(1 for profile in profiles if profile.get('enabled', True))
        if profile_count == 0 or 0 < enabled_count < profile_count:
            status = _MIXED_STATUS
        elif enabled_count == profile_count:
            status = 'On'
        else:
            status = 'Off'
        sort_enabled = 1 if profile_count > 0 and enabled_count == profile_count else 0
        return profile_count, id_count, status, sort_enabled

    def _profile_display(self, rule: dict, fallback_index: int, path: tuple[int, ...]) -> tuple[list[str], list]:
        name = rule.get('name', f'Profile {fallback_index + 1}')
        enabled = rule.get('enabled', True)
        mode = rule.get('mode', 'id')
        if 'remove' in rule and 'mode' not in rule:
            mode = 'remove' if rule.get('remove') else 'id'

        if mode == 'id':
            with_id = rule.get('with_id')
            if with_id is not None:
                action = 'ID'
                replace_with = str(with_id)
            else:
                action = 'Remove'
                replace_with = '-'
        elif mode == 'cdn':
            action = 'CDN'
            cdn_url = rule.get('cdn_url', '')
            replace_with = cdn_url[:40] + '...' if len(cdn_url) > 40 else cdn_url
        elif mode == 'local':
            action = 'Local'
            local_path = rule.get('local_path', '')
            replace_with = Path(local_path).name if local_path else ''
        elif mode == 'remove':
            action = 'Remove'
            replace_with = '-'
        else:
            action = mode.upper()
            replace_with = '-'

        id_count = len(rule.get('replace_ids', []))
        values = ['On' if enabled else 'Off', self._entry_display_name(name, path), action, format_count(id_count, 'ID'), replace_with]
        sort_values = [1 if enabled else 0, name.lower(), action.lower(), id_count, replace_with.lower()]
        return values, sort_values

    def _make_tree_item(self, entry: dict, path: tuple[int, ...]) -> ReplacerTreeItem:
        if self._is_group(entry):
            profile_count, id_count, status, sort_enabled = self._group_summary(entry)
            name = entry.get('name', 'Group')
            item = ReplacerTreeItem([
                status,
                self._group_display_name(name, path),
                'Group',
                format_count(id_count, 'ID'),
                format_count(profile_count, 'profile'),
            ])
            item.setData(0, _ROLE_KIND, _KIND_GROUP)
            sort_values = [sort_enabled, name.lower(), 'group', id_count, profile_count]
            flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            font = item.font(1)
            font.setBold(True)
            item.setFont(1, font)
            for column in range(5):
                item.setSizeHint(column, QSize(0, _GROUP_ROW_HEIGHT_PX))
            for child_index, child in enumerate(entry.get('children', [])):
                item.addChild(self._make_tree_item(child, path + (child_index,)))
        else:
            values, sort_values = self._profile_display(entry, path[-1] if path else 0, path)
            item = ReplacerTreeItem(values)
            item.setData(0, _ROLE_KIND, _KIND_PROFILE)
            flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled
            flags &= ~Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)

        item.setData(0, _ROLE_PATH, path)
        for column, sort_value in enumerate(sort_values):
            item.setData(column, _ROLE_SORT_BASE, sort_value)
        return item

    def _restore_expanded_states(self):
        def walk(item: QTreeWidgetItem):
            path = item.data(0, _ROLE_PATH)
            if item.data(0, _ROLE_KIND) == _KIND_GROUP and isinstance(path, tuple):
                group = self._entry_at_path(self.config_manager.replacement_rules, path)
                item.setExpanded(bool(group.get('expanded', True)) if self._is_group(group) else True)
            for child_index in range(item.childCount()):
                walk(item.child(child_index))

        for top_index in range(self.tree.topLevelItemCount()):
            walk(self.tree.topLevelItem(top_index))

    def _set_group_expanded(self, item: QTreeWidgetItem, expanded: bool):
        if getattr(self, '_refreshing_tree', False) or item.data(0, _ROLE_KIND) != _KIND_GROUP:
            return
        path = item.data(0, _ROLE_PATH)
        if not isinstance(path, tuple):
            return
        rules = deepcopy(self.config_manager.replacement_rules)
        group = self._entry_at_path(rules, path)
        if not self._is_group(group) or group.get('expanded') == expanded:
            return
        group['expanded'] = expanded
        self.config_manager.replacement_rules = rules

    def _refresh_tree(self):
        """Refresh the tree view."""
        sort_column = self.tree.sortColumn()
        sort_order = self.tree.header().sortIndicatorOrder()

        self._refreshing_tree = True
        try:
            self.tree.setSortingEnabled(False)
            self.tree.clear()
            for index, entry in enumerate(self.config_manager.replacement_rules):
                self.tree.addTopLevelItem(self._make_tree_item(entry, (index,)))
            self._restore_expanded_states()
            self.tree.setSortingEnabled(True)
            self.tree.sortItems(sort_column, sort_order)
        finally:
            self._refreshing_tree = False
        self._tree_config_name = self.config_manager.last_config
        has_groups = self._config_has_groups()
        self.tree.setDragEnabled(has_groups)
        self.tree.setAcceptDrops(has_groups)
        self.tree.viewport().setAcceptDrops(has_groups)

    def _refresh_combo(self):
        """Refresh config controls from the current files on disk."""
        self._sync_config_state_from_disk()

    def _sync_config_state_from_disk(self, *, update_enabled_menu: bool = True) -> bool:
        """Refresh config settings from disk and update dependent UI."""
        previous_config = self.config_manager.settings.get('last_config', 'Default')
        previous_tree_config = getattr(self, '_tree_config_name', previous_config)
        changed = self.config_manager.reconcile_configs()
        current_config = self.config_manager.last_config
        selected_config_changed = previous_config != current_config
        tree_config_changed = previous_tree_config != current_config

        self.config_menu_btn.setText(' \u200A' + current_config)
        if update_enabled_menu and hasattr(self, 'enabled_menu'):
            self._rebuild_enabled_menu(sync_from_disk=False)
        if (changed or selected_config_changed or tree_config_changed) and hasattr(self, 'tree'):
            self.undo_manager.clear()
            self.undo_manager.save_state(self.config_manager.replacement_rules)
            self._refresh_tree()
            if selected_config_changed or tree_config_changed:
                self.tree.clearSelection()
                if hasattr(self, 'name_entry'):
                    self._clear_entries()
        try:
            self._update_editing_button_style()
        except Exception:
            pass
        return changed or selected_config_changed or tree_config_changed

    def _rebuild_editing_menu(self):
        """Rebuild the editing config menu."""
        self._sync_config_state_from_disk()
        current_configs = self.config_manager.config_names

        entries = []
        for name in current_configs:
            # Add a small subtle red dot icon for profiles that are not enabled.
            try:
                if not self.config_manager.is_config_enabled(name):
                    icon = self._make_status_icon('#cc5555')
                else:
                    # Mark enabled profiles with a subtle green dot
                    icon = self._make_status_icon('#55cc66')
            except Exception:
                # If querying config state fails, leave icon empty
                icon = QIcon()
            entries.append({'name': name, 'icon': icon})

        self.config_menu.set_entries(
            entries,
            minimum_width=self.config_menu_btn.width() if hasattr(self, 'config_menu_btn') else 0,
        )
        # Ensure the Editing button reflects the enabled state after rebuild
        try:
            self._update_editing_button_style()
        except Exception:
            pass
        self.config_menu.constrain_to_button(self.config_menu_btn)

    def _on_config_select(self, name: str):
        """Handle config selection from menu."""
        self._sync_config_state_from_disk()
        if name not in self.config_manager.config_names:
            return
        if name != self.config_manager.last_config:
            self.config_manager.last_config = name
            # Keep a single space plus a hair-space between icon and text
            self.config_menu_btn.setText(' \u200A' + name)
            self.undo_manager.clear()
            self.undo_manager.save_state(self.config_manager.replacement_rules)
            self._refresh_tree()

        """Handle strip textures change."""
        try:
            self._update_editing_button_style()
        except Exception:
            pass

    def _update_editing_button_style(self):
        """Color the Editing button text red if the currently edited profile
        is not enabled in the Enabled: menu.
        """
        # Guard if UI not yet created
        if not hasattr(self, 'config_menu_btn') or not hasattr(self, 'config_manager'):
            return

        name = self.config_manager.last_config
        try:
            enabled = self.config_manager.is_config_enabled(name)
        except Exception:
            enabled = False

        # Set the same small colored dot icon on the parent dropdown button
        try:
            color = '#55cc66' if enabled else '#cc5555'
            self.config_menu_btn.setIcon(self._make_status_icon(color))
        except Exception:
            try:
                self.config_menu_btn.setIcon(QIcon())
            except Exception:
                pass
        # Ensure button text color isn't used for state; the dot represents state now.
        self.config_menu_btn.setStyleSheet('')

    def _make_status_icon(self, color: str = '#cc5555', size: int = 12) -> QIcon:
        """Create a small circular QIcon of given color for menu actions.

        This uses native Qt QIcon/QPixmap drawing and avoids custom widget
        widgets so menu entries remain simple QActions.
        """
        try:
            pix = QPixmap(size, size)
            pix.fill(QColor(0, 0, 0, 0))
            p = QPainter(pix)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setBrush(QColor(color))
            p.setPen(QColor(0, 0, 0, 0))
            margin = 2
            p.drawEllipse(margin, margin, size - margin * 2, size - margin * 2)
            p.end()
            return QIcon(pix)
        except Exception:
            return QIcon()

    def _browse_local_file(self):
        """Open file browser for local file selection."""
        current_val = self.replacement_entry.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            path = Path(current_val)
            if path.parent.exists():
                initial_dir = str(path)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            'Select Local File',
            initial_dir,
            'All Files (*.*)',
        )
        if file_path:
            self.replacement_entry.setText(file_path)

    def _config_action(self, action: str):
        """Handle config management actions."""
        current = self.config_manager.last_config

        if action == 'new':
            name, ok = QInputDialog.getText(self, 'New Config', 'Name:')
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif not self.config_manager.create_config(name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'dup':
            name, ok = QInputDialog.getText(
                self, 'Duplicate', f"Copy of '{current}':"
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif not self.config_manager.duplicate_config(current, name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'rename':
            name, ok = QInputDialog.getText(
                self, 'Rename', 'New name:', text=current
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(self, 'Invalid Name',
                        'Config names cannot contain: \\ / : * ? " < > |')
                elif name != current and not self.config_manager.rename_config(current, name):
                    QMessageBox.warning(self, 'Invalid Name', f"A config named '{name}' already exists.")
                elif name != current:
                    self._refresh_combo()

        elif action == 'delete':
            if len(self.config_manager.config_names) <= 1:
                QMessageBox.critical(self, 'Error', 'Cannot delete last config')
            else:
                reply = QMessageBox.question(
                    self,
                    'Delete',
                    f"Delete '{current}'?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.config_manager.delete_config(current)
                    self.undo_manager.clear()
                    self.undo_manager.save_state(self.config_manager.replacement_rules)
                    self._refresh_combo()
                    self._refresh_tree()

    def _save_with_undo(self, rules: list):
        """Save rules with undo tracking."""
        self.undo_manager.save_state(rules)
        self.config_manager.replacement_rules = rules

    def _do_undo(self):
        """Perform undo."""
        if prev := self.undo_manager.undo():
            self.config_manager.replacement_rules = prev
            self._refresh_tree()
            log_buffer.log('Config', 'Undo performed')

    def _do_redo(self):
        """Perform redo."""
        if next_state := self.undo_manager.redo():
            self.config_manager.replacement_rules = next_state
            self._refresh_tree()
            log_buffer.log('Config', 'Redo performed')

    def _show_context_menu(self, pos):
        """Show context menu for tree item."""
        item = self.tree.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)

        selected_items = self.tree.selectedItems()
        selected_profile_paths = self._selected_profile_paths()

        menu = QMenu(self)

        # Multi-select operations (available when multiple items selected)
        if len(selected_items) > 1:
            if selected_profile_paths:
                menu.addAction('Enable Selected', self._enable_selected)
                menu.addAction('Disable Selected', self._disable_selected)
                if len(selected_profile_paths) == len(selected_items) and self._paths_share_parent(selected_profile_paths):
                    menu.addSeparator()
                    menu.addAction('Create Group', self._create_group_from_selected)
            menu.addSeparator()
            menu.addAction('Delete Selected', self._delete_selected)
        else:
            # Single item operations
            path = item.data(0, _ROLE_PATH)
            if not isinstance(path, tuple):
                return
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_group(entry):
                menu.addAction('Enable Group', lambda: self._set_group_profiles_enabled(path, True))
                menu.addAction('Disable Group', lambda: self._set_group_profiles_enabled(path, False))
                menu.addSeparator()
                menu.addAction('Rename Group', lambda: self._rename_group(path))
                menu.addSeparator()
                menu.addAction('Delete Group', lambda: self._delete_selected())
            elif self._is_profile(entry):
                enabled = entry.get('enabled', True)
                text = 'Disable Profile' if enabled else 'Enable Profile'
                menu.addAction(text, lambda: self._toggle_profile(path))
                menu.addAction('Rename Profile', lambda: self._rename_profile(path))
                menu.addAction('Edit Asset IDs', lambda: self._edit_asset_ids(path))
                menu.addAction('Edit Replacement', lambda: self._edit_replacement(path))
                menu.addSeparator()
                menu.addAction('Create Group', self._create_group_from_selected)
                menu.addSeparator()
                menu.addAction('Delete Profile', lambda: self._delete_selected())

        if menu.actions():
            menu.exec(self.tree.mapToGlobal(pos))

    def _selected_entry_paths(self) -> list[tuple[int, ...]]:
        paths: list[tuple[int, ...]] = []
        for item in self.tree.selectedItems():
            path = item.data(0, _ROLE_PATH)
            if isinstance(path, tuple):
                paths.append(path)
        return paths

    def _selected_profile_paths(self) -> list[tuple[int, ...]]:
        profile_paths = []
        for path in self._selected_entry_paths():
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_profile(entry):
                profile_paths.append(path)
        return sorted(profile_paths)

    def _selected_movable_paths(self) -> list[tuple[int, ...]]:
        paths = []
        for path in self._selected_entry_paths():
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_profile(entry) or self._is_group(entry):
                paths.append(path)
        return self._prune_descendant_paths(paths)

    @staticmethod
    def _paths_share_parent(paths: list[tuple[int, ...]]) -> bool:
        return bool(paths) and len({path[:-1] for path in paths}) == 1

    def _toggle_profile(self, path: tuple[int, ...]):
        """Toggle profile enabled state."""
        rules = deepcopy(self.config_manager.replacement_rules)
        rule = self._entry_at_path(rules, path)
        if self._is_profile(rule):
            rule['enabled'] = not rule.get('enabled', True)
            self._save_with_undo(rules)
            self._refresh_tree()

    def _rename_profile(self, path: tuple[int, ...]):
        """Rename a profile."""
        rules = self.config_manager.replacement_rules
        rule = self._entry_at_path(rules, path)
        if not self._is_profile(rule):
            return
        old_name = rule.get('name', f'Profile {path[-1] + 1}')
        name, ok = QInputDialog.getText(self, 'Rename', 'New name:', text=old_name)
        if ok and name and name.strip():
            rules_copy = deepcopy(rules)
            rule_copy = self._entry_at_path(rules_copy, path)
            if not self._is_profile(rule_copy):
                return
            rule_copy['name'] = name.strip()
            self._save_with_undo(rules_copy)
            self._refresh_tree()

    def _rename_group(self, path: tuple[int, ...]):
        """Rename a group."""
        rules = self.config_manager.replacement_rules
        group = self._entry_at_path(rules, path)
        if not self._is_group(group):
            return
        old_name = group.get('name', 'Group')
        name, ok = QInputDialog.getText(self, 'Rename', 'New name:', text=old_name)
        if ok and name and name.strip():
            rules_copy = deepcopy(rules)
            group_copy = self._entry_at_path(rules_copy, path)
            if not self._is_group(group_copy):
                return
            group_copy['name'] = name.strip()
            self._save_with_undo(rules_copy)
            self._refresh_tree()

    def _set_group_profiles_enabled(self, path: tuple[int, ...], enabled: bool):
        """Set every descendant profile in a group to the same enabled state."""
        rules = deepcopy(self.config_manager.replacement_rules)
        group = self._entry_at_path(rules, path)
        if not self._is_group(group):
            return

        changed = 0
        for profile in self._iter_profiles(group.get('children', [])):
            if profile.get('enabled', True) != enabled:
                profile['enabled'] = enabled
                changed += 1

        if changed:
            self._save_with_undo(rules)
            self._refresh_tree()
            action = 'Enabled' if enabled else 'Disabled'
            log_buffer.log('Config', f'{action} {format_count(changed, "profile")} in group: {group.get("name", "Group")}')

    def _create_group_from_selected(self):
        """Create a group from the currently selected profile rows."""
        paths = self._selected_profile_paths()
        if not paths:
            return
        if len(paths) != len(self.tree.selectedItems()):
            QMessageBox.information(self, 'Create Group', 'Select only profiles to create a group.')
            return
        if not self._paths_share_parent(paths):
            QMessageBox.information(self, 'Create Group', 'Select profiles in the same group or config level.')
            return

        name, ok = QInputDialog.getText(self, 'Rename', 'New name:', text='New Group')
        if not ok or not name or not name.strip():
            return

        rules = deepcopy(self.config_manager.replacement_rules)
        parent_path = paths[0][:-1]
        parent_entries = self._entries_at_parent_path(rules, parent_path)
        if parent_entries is None:
            return

        selected_indices = sorted(path[-1] for path in paths)
        children = [deepcopy(parent_entries[index]) for index in selected_indices if index < len(parent_entries)]
        if not children:
            return

        for index in reversed(selected_indices):
            if index < len(parent_entries):
                parent_entries.pop(index)
        insert_at = selected_indices[0]
        parent_entries.insert(insert_at, {
            'type': _KIND_GROUP,
            'name': name.strip(),
            'expanded': True,
            'children': children,
        })

        self._save_with_undo(rules)
        self._refresh_tree()
        log_buffer.log('Config', f"Created group: {name.strip()} ({format_count(children, 'profile')})")

    def _edit_asset_ids(self, path: tuple[int, ...]):
        """Edit asset IDs for a profile."""
        rules = self.config_manager.replacement_rules
        rule = self._entry_at_path(rules, path)
        if not self._is_profile(rule):
            return

        name = rule.get('name', f'Profile {path[-1] + 1}')
        ids = rule.get('replace_ids', [])

        dialog = QDialog(self)
        dialog.setWindowTitle(f'Asset IDs - {name}')
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(f'Profile: {name}')
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        count_label = QLabel(f'Total: {format_count(ids, "asset ID")}')
        layout.addWidget(count_label)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText('\n'.join(str(i) for i in ids))
        layout.addWidget(text_edit)

        def save_ids():
            content = text_edit.toPlainText().strip()
            # Use robust ID parser to avoid deleting valid string-based asset types
            new_ids = self._parse_ids(content.replace('\n', ','))
            rules_copy = deepcopy(self.config_manager.replacement_rules)
            rule_copy = self._entry_at_path(rules_copy, path)
            if not self._is_profile(rule_copy):
                return
            rule_copy['replace_ids'] = new_ids
            self._save_with_undo(rules_copy)
            self._refresh_tree()
            count_label.setText(f'Total: {format_count(new_ids, "asset ID")}')

        def copy_all():
            from PyQt6.QtWidgets import QApplication

            QApplication.clipboard().setText(', '.join(str(i) for i in ids))

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton('Copy All')
        copy_btn.clicked.connect(copy_all)
        btn_layout.addWidget(copy_btn)

        save_btn = QPushButton('Save and Close')
        save_btn.clicked.connect(lambda: (save_ids(), dialog.accept()))
        btn_layout.addWidget(save_btn)
        
        # Add Asset Types menu to Edit Asset IDs
        types_btn = QPushButton('Asset Types')
        
        def show_dialog_types_popup():
            def on_filters_changed(filters):
                from ..cache.cache_manager import CacheManager

                curr_content = text_edit.toPlainText().strip()
                curr_ids = self._parse_ids(curr_content.replace('\n', ','))
                new_ids = []
                for item in curr_ids:
                    if isinstance(item, int):
                        new_ids.append(item)
                    elif isinstance(item, str):
                        is_mapped = False
                        if item in _VIRTUAL_ANIM_TYPES:
                            is_mapped = True
                        else:
                            for tid, name in CacheManager.ASSET_TYPES.items():
                                if name.lower() == item.lower():
                                    is_mapped = True
                                    break
                        if not is_mapped:
                            new_ids.append(item)
                for tid in filters:
                    if tid in _VIRTUAL_ANIM_TYPES:
                        new_ids.append(tid)
                    elif tid in CacheManager.ASSET_TYPES:
                        new_ids.append(CacheManager.ASSET_TYPES[tid])

                if new_ids:
                    text_edit.setPlainText('\n'.join(str(i) for i in new_ids))
                else:
                    text_edit.setPlainText('')

            import time as _time
            from ..cache.cache_viewer import CategoryFilterPopup
            from ..cache.cache_manager import CacheManager
            from PyQt6.QtCore import QPoint
            from PyQt6.QtWidgets import QApplication

            if _time.monotonic() - self._dialog_asset_types_popup_last_closed < 0.25:
                return

            _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

            content = text_edit.toPlainText().strip()
            current_ids = self._parse_ids(content.replace('\n', ','))

            active_filters = set()
            for item in current_ids:
                if isinstance(item, str):
                    if item in _VIRTUAL_ANIM_TYPES:
                        active_filters.add(item)
                        continue
                    for tid, name in CacheManager.ASSET_TYPES.items():
                        if name.lower() == item.lower():
                            active_filters.add(tid)
                            break

            popup = getattr(self, '_dialog_asset_types_popup', None)
            if popup is not None:
                try:
                    if popup.isVisible():
                        popup.close()
                        self._dialog_asset_types_popup_last_closed = _time.monotonic()
                        return
                except RuntimeError:
                    popup = None

            if popup is None:
                popup = CategoryFilterPopup(parent=dialog, active_filters=active_filters)
                popup.filters_changed.connect(on_filters_changed)
                popup.aboutToHide.connect(self._mark_dialog_asset_types_popup_closed)
                self._dialog_asset_types_popup = popup
            else:
                popup.set_active_filters(active_filters)

            # Line up TOP LEFT of our popup menu with TOP LEFT of the Asset Types button
            pos = types_btn.mapToGlobal(types_btn.rect().topLeft())
            screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
            if screen is not None:
                popup.constrain_to_available_geometry(screen.availableGeometry(), pos.y())
            popup.popup(pos)

        types_btn.clicked.connect(show_dialog_types_popup)
        btn_layout.addWidget(types_btn)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        dialog.show()

    def _edit_replacement(self, path: tuple[int, ...]):
        """Edit replacement value for a profile."""
        rules = self.config_manager.replacement_rules
        rule = self._entry_at_path(rules, path)
        if not self._is_profile(rule):
            return

        mode = rule.get('mode', 'id')

        # Get current value based on mode
        if mode == 'cdn':
            old_value = rule.get('cdn_url', '')
        elif mode == 'local':
            old_value = rule.get('local_path', '')
        else:
            old_value = str(rule.get('with_id', '')) if rule.get('with_id') is not None else ''

        dialog = QDialog(self)
        dialog.setWindowTitle('Edit Replacement')
        dialog.resize(400, 100)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon

            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()
        label = QLabel('Replacement (ID, URL, file path, or empty to remove):')
        layout.addWidget(label)

        line_edit = FileDropLineEdit()
        line_edit.setText(old_value)
        layout.addWidget(line_edit)

        btn_layout = QHBoxLayout()

        browse_btn = QPushButton('Browse...')
        browse_btn.setFixedWidth(80)
        browse_btn.setAutoDefault(False)
        def _on_browse():
            current_val = line_edit.text().strip(' \t"\'')
            initial_dir = ''
            if current_val:
                path = Path(current_val)
                if path.parent.exists():
                    initial_dir = str(path)

            path, _ = QFileDialog.getOpenFileName(dialog, 'Select Local File', initial_dir, 'All Files (*.*)')
            if path:
                line_edit.setText(path)
                dialog.accept()
        browse_btn.clicked.connect(_on_browse)
        btn_layout.addWidget(browse_btn)

        btn_layout.addStretch()

        ok_btn = QPushButton('OK')
        ok_btn.setFixedWidth(80)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton('Cancel')
        cancel_btn.setFixedWidth(80)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)

        if not dialog.exec():
            return

        new_value = line_edit.text().strip(' \t"\'')
        new_mode, extra = self._detect_mode(new_value)

        if '_raw' in extra:
            QMessageBox.critical(self, 'Error', f"Invalid replacement: '{extra['_raw']}'")
            return

        if new_mode == 'local' and 'local_path' in extra:
            if not Path(extra['local_path']).exists():
                QMessageBox.critical(self, 'Error', f"File not found: {extra['local_path']}")
                return

        rules_copy = deepcopy(rules)
        rule_copy = self._entry_at_path(rules_copy, path)
        if not self._is_profile(rule_copy):
            return
        # Clear old mode fields
        rule_copy.pop('with_id', None)
        rule_copy.pop('cdn_url', None)
        rule_copy.pop('local_path', None)
        # Set new mode and value
        rule_copy['mode'] = new_mode
        rule_copy.update(extra)
        self._save_with_undo(rules_copy)
        self._refresh_tree()

    def _parse_ids(self, text: str) -> list[Union[int, str]]:
        """Parse IDs from text."""
        ids: list[Union[int, str]] = []
        # Replace common separators with comma
        text = text.replace(';', ',').replace(' ', ',')
        for part in text.split(','):
            part = part.strip()
            if not part:
                continue
            # "parentId:mapIndex" or "TexturePack:N" — keep as-is
            if ':' in part:
                left, right = part.split(':', 1)
                if right.isdigit() and (left.isdigit() or left == 'TexturePack'):
                    ids.append(part)
                    continue
            try:
                ids.append(int(part))
            except ValueError:
                ids.append(part)
        return ids
        
    def _show_asset_types_popup(self):
        """Show the asset types popup menu."""
        from ..cache.cache_manager import CacheManager
        from PyQt6.QtCore import QPoint
        from PyQt6.QtWidgets import QApplication

        if time.monotonic() - self._asset_types_popup_last_closed < 0.25:
            return

        popup = self.asset_types_popup
        if popup.isVisible():
            popup.close()
            self._asset_types_popup_last_closed = time.monotonic()
            return

        _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        # Parse current text to update active filters
        current_text = self.replace_entry.text()
        current_ids = self._parse_ids(current_text)

        active_filters = set()
        for item in current_ids:
            if isinstance(item, str):
                if item in _VIRTUAL_ANIM_TYPES:
                    active_filters.add(item)
                    continue
                # Reverse lookup the asset type integer from name
                for tid, name in CacheManager.ASSET_TYPES.items():
                    if name.lower() == item.lower():
                        active_filters.add(tid)
                        break

        # Reuse the existing popup instance and update its current selection.
        popup = self.asset_types_popup
        popup.set_active_filters(active_filters)

        global_top_right = self.asset_types_btn.mapToGlobal(self.asset_types_btn.rect().topRight())
        screen = QApplication.screenAt(global_top_right) or QApplication.primaryScreen()
        if screen is None:
            popup.popup(global_top_right)
            return
        avail = screen.availableGeometry()
        popup.constrain_to_available_geometry(avail, global_top_right.y())
        popup_size = popup.sizeHint()

        # Ideal: bottom-right of popup aligns with top-right of button
        ideal_x = global_top_right.x() - popup_size.width()
        ideal_y = global_top_right.y() - popup_size.height()

        # Clamp to the screen the button is on so it never teleports to another monitor
        x = max(avail.left(), min(ideal_x, avail.right() - popup_size.width()))
        y = max(avail.top(), min(ideal_y, avail.bottom() - popup_size.height()))

        popup.popup(QPoint(x, y))

    def _mark_asset_types_popup_closed(self):
        """Remember that the Asset Types popup just closed to debounce reopen clicks."""
        self._asset_types_popup_last_closed = time.monotonic()

    def _mark_dialog_asset_types_popup_closed(self):
        """Remember that the edit dialog Asset Types popup just closed."""
        self._dialog_asset_types_popup_last_closed = time.monotonic()
        
    def _on_asset_types_changed(self, filters):
        """Handle asset types selection change."""
        from ..cache.cache_manager import CacheManager

        _VIRTUAL_ANIM_TYPES = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        current_text = self.replace_entry.text().strip()
        current_ids = self._parse_ids(current_text)

        new_ids = []
        for item in current_ids:
            if isinstance(item, int):
                new_ids.append(item)
            elif isinstance(item, str):
                is_mapped = False
                for tid, name in CacheManager.ASSET_TYPES.items():
                    if name.lower() == item.lower():
                        is_mapped = True
                        break
                # Also treat virtual anim type strings as mapped (so they get removed/re-added cleanly)
                if item in _VIRTUAL_ANIM_TYPES:
                    is_mapped = True
                if not is_mapped:
                    new_ids.append(item)

        # Add the string representations of the selected filters
        for tid in filters:
            if tid in _VIRTUAL_ANIM_TYPES:
                new_ids.append(tid)
            elif tid in CacheManager.ASSET_TYPES:
                new_ids.append(CacheManager.ASSET_TYPES[tid])

        if new_ids:
            self.replace_entry.setText(', '.join(str(i) for i in new_ids).strip(', '))
        else:
            self.replace_entry.setText('')

    def _clear_entries(self):
        """Clear input fields."""
        self.name_entry.clear()
        self.replace_entry.clear()
        self.replacement_entry.clear()

    def _detect_mode(self, value: str) -> tuple[str, dict]:
        """Auto-detect mode from replacement value.

        Returns tuple of (mode, extra_fields).
        """
        value = value.strip()

        if not value:
            # Empty = remove
            return 'id', {}

        if value.startswith(('http://', 'https://')):
            # URL = CDN mode
            return 'cdn', {'cdn_url': value}

        # Check if it's a file path (contains path separators or drive letter)
        if '\\' in value or '/' in value or (len(value) > 2 and value[1] == ':'):
            return 'local', {'local_path': value}

        # Try to parse as integer (asset ID)
        try:
            return 'id', {'with_id': int(value)}
        except ValueError:
            pass

        # Could be a relative file path without separators
        if Path(value).exists():
            return 'local', {'local_path': str(Path(value).resolve())}

        # Default to treating as potential asset ID (will fail validation)
        return 'id', {'_raw': value}

    def _get_rule_from_entries(self) -> dict | None:
        """Get rule from input fields."""
        ids = self._parse_ids(self.replace_entry.text())
        if not ids:
            QMessageBox.critical(self, 'Error', 'Enter at least one asset ID')
            return None

        replacement = self.replacement_entry.text().strip()
        mode, extra = self._detect_mode(replacement)

        rule = {
            'name': self.name_entry.text().strip()
            or f'Profile {self._profile_count() + 1}',
            'replace_ids': ids,
            'mode': mode,
            'enabled': True,
        }

        if mode == 'id':
            if 'with_id' in extra:
                rule['with_id'] = extra['with_id']
            elif '_raw' in extra:
                # Failed to parse as ID
                QMessageBox.critical(self, 'Error', f"Invalid replacement: '{extra['_raw']}'\nMust be an asset ID, URL, or file path")
                return None
            # Empty = remove (no with_id)
        elif mode == 'cdn':
            cdn_url = extra['cdn_url']
            # Validate URL is accessible
            try:
                status = http_head_status(cdn_url, timeout=5, headers={'User-Agent': 'Mozilla/5.0'})
                if status >= 400:
                    QMessageBox.warning(
                        self, 'Warning',
                        f'CDN URL returned status {status}. Adding anyway.'
                    )
            except URLError as e:
                reply = QMessageBox.question(
                    self, 'URL Check Failed',
                    f'Could not verify CDN URL:\n{e}\n\nAdd anyway?',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return None
            except Exception:
                pass  # Ignore other errors, allow adding
            rule['cdn_url'] = cdn_url
        elif mode == 'local':
            local_path = extra['local_path']
            if not Path(local_path).exists():
                QMessageBox.critical(self, 'Error', f'File not found: {local_path}')
                return None
            rule['local_path'] = local_path

        return rule

    def _add_rule(self):
        """Add a new rule."""
        if rule := self._get_rule_from_entries():
            rules = deepcopy(self.config_manager.replacement_rules)
            rules.append(rule)
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()
            mode = rule.get('mode', 'id').upper()
            log_buffer.log('Config', f"Added profile: {rule['name']} ({mode})")

    def _load_selected(self):
        """Load selected rule into input fields."""
        items = self.tree.selectedItems()
        if not items:
            return

        path = items[0].data(0, _ROLE_PATH)
        rule = self._entry_at_path(self.config_manager.replacement_rules, path) if isinstance(path, tuple) else None
        if not self._is_profile(rule):
            return

        self._clear_entries()
        self.name_entry.setText(rule.get('name', ''))
        self.replace_entry.setText(', '.join(str(x) for x in rule.get('replace_ids', [])))

        # Determine mode and set replacement field
        mode = rule.get('mode', 'id')
        # Legacy support
        if 'remove' in rule and 'mode' not in rule:
            if rule.get('remove'):
                # For legacy remove, leave replacement empty
                return
            mode = 'id'

        if mode == 'id':
            if (with_id := rule.get('with_id')) is not None:
                self.replacement_entry.setText(str(with_id))
        elif mode == 'cdn':
            self.replacement_entry.setText(rule.get('cdn_url', ''))
        elif mode == 'local':
            self.replacement_entry.setText(rule.get('local_path', ''))

    def _update_selected(self):
        """Update selected rule."""
        items = self.tree.selectedItems()
        if not items:
            return

        if rule := self._get_rule_from_entries():
            path = items[0].data(0, _ROLE_PATH)
            if not isinstance(path, tuple):
                return
            rules = deepcopy(self.config_manager.replacement_rules)
            current_rule = self._entry_at_path(rules, path)
            if not self._is_profile(current_rule):
                return
            rule['enabled'] = current_rule.get('enabled', True)
            self._set_entry_at_path(rules, path, rule)
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()

    def _delete_selected(self):
        """Delete selected profiles or groups."""
        paths = self._prune_descendant_paths(self._selected_entry_paths())
        if not paths:
            return

        current_rules = self.config_manager.replacement_rules
        has_group = any(self._is_group(self._entry_at_path(current_rules, path)) for path in paths)
        if has_group:
            reply = QMessageBox.question(
                self,
                'Delete Group',
                'Delete selected groups and all nested contents?',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        deleted_names = []
        for path in paths:
            entry = self._entry_at_path(current_rules, path)
            if isinstance(entry, dict):
                deleted_names.append(entry.get('name', 'Group' if self._is_group(entry) else f'Profile {path[-1] + 1}'))

        rules = self._remove_paths(deepcopy(current_rules), set(paths))

        if deleted_names:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f"Deleted {format_count(deleted_names, 'item')}: {', '.join(deleted_names)}")

    def _enable_selected(self):
        """Enable selected rules."""
        paths = self._selected_profile_paths()
        if not paths:
            return

        rules = deepcopy(self.config_manager.replacement_rules)
        enabled_count = 0
        for path in paths:
            rule = self._entry_at_path(rules, path)
            if self._is_profile(rule) and not rule.get('enabled', True):
                rule['enabled'] = True
                enabled_count += 1

        if enabled_count > 0:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f'Enabled {format_count(enabled_count, "profile")}')

    def _disable_selected(self):
        """Disable selected rules."""
        paths = self._selected_profile_paths()
        if not paths:
            return

        rules = deepcopy(self.config_manager.replacement_rules)
        disabled_count = 0
        for path in paths:
            rule = self._entry_at_path(rules, path)
            if self._is_profile(rule) and rule.get('enabled', True):
                rule['enabled'] = False
                disabled_count += 1

        if disabled_count > 0:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log('Config', f'Disabled {format_count(disabled_count, "profile")}')

    def _iter_tree_items(self):
        def walk(item: QTreeWidgetItem):
            yield item
            for child_index in range(item.childCount()):
                yield from walk(item.child(child_index))

        for top_index in range(self.tree.topLevelItemCount()):
            yield from walk(self.tree.topLevelItem(top_index))

    @staticmethod
    def _is_descendant_path(path: tuple[int, ...], parent: tuple[int, ...]) -> bool:
        return len(path) > len(parent) and path[:len(parent)] == parent

    def _group_depth(self, path: tuple[int, ...]) -> int:
        depth = 0
        for index in range(1, len(path) + 1):
            entry = self._entry_at_path(self.config_manager.replacement_rules, path[:index])
            if self._is_group(entry):
                depth += 1
        return depth

    def _entry_display_name(self, name: str, path: tuple[int, ...]) -> str:
        indent = ' ' * (_GROUP_CONTENT_INDENT_SPACES * max(0, self._group_depth(path[:-1])))
        return f'{indent}{name}'

    def _group_display_name(self, name: str, path: tuple[int, ...]) -> str:
        return self._entry_display_name(f'{_GROUP_ICON} {name}', path)

    def _group_guide_x(self, group_path: tuple[int, ...]) -> int:
        name_left = self.tree.columnViewportPosition(_PROFILE_NAME_COLUMN)
        depth_offset = max(0, self._group_depth(group_path) - 1) * _GROUP_GUIDE_STEP_PX
        return name_left + _GROUP_GUIDE_GUTTER_PX + depth_offset + 1

    def _paint_group_guides(self, viewport):
        if not hasattr(self, 'tree') or not self._config_has_groups():
            return

        palette = self.tree.palette()
        is_dark = palette.window().color().lightness() < 128
        guide_color = QColor('#5f6368' if is_dark else '#c4c7c5')
        selected_color = guide_color.lighter(175 if is_dark else 115)

        painter = QPainter(viewport)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        selected_group_paths: set[tuple[int, ...]] = set()
        for path in self._selected_entry_paths():
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_group(entry):
                selected_group_paths.add(path)
                continue

            parent_path = path[:-1]
            if not parent_path:
                continue

            parent = self._entry_at_path(self.config_manager.replacement_rules, parent_path)
            if self._is_group(parent):
                selected_group_paths.add(parent_path)

        guide_pen = QPen(guide_color)
        guide_pen.setWidth(1)
        guide_pen.setCosmetic(True)
        selected_pen = QPen(selected_color)
        selected_pen.setWidth(1)
        selected_pen.setCosmetic(True)

        visible_spans: dict[tuple[int, ...], tuple[int, int]] = {}

        for item in self._iter_tree_items():
            rect = self.tree.visualItemRect(item)
            if not rect.isValid() or rect.bottom() < 0 or rect.top() > viewport.height():
                continue

            item_path = item.data(0, _ROLE_PATH)
            if not isinstance(item_path, tuple):
                continue

            max_depth = len(item_path)
            item_entry = self._entry_at_path(self.config_manager.replacement_rules, item_path)
            if self._is_group(item_entry):
                max_depth -= 1

            for depth in range(1, max_depth + 1):
                ancestor_path = item_path[:depth]
                ancestor = self._entry_at_path(self.config_manager.replacement_rules, ancestor_path)
                if not self._is_group(ancestor):
                    continue

                span = visible_spans.get(ancestor_path)
                if span is None:
                    visible_spans[ancestor_path] = (rect.top(), rect.bottom())
                else:
                    visible_spans[ancestor_path] = (min(span[0], rect.top()), max(span[1], rect.bottom()))

        for group_path, (top, bottom) in visible_spans.items():
            painter.setPen(selected_pen if group_path in selected_group_paths else guide_pen)
            x = self._group_guide_x(group_path)
            painter.drawLine(x, top, x, bottom)

        painter.end()

    def _set_drag_hint_active(self, active: bool):
        """Highlight valid group/root drop targets while dragging profiles."""
        if not hasattr(self, 'tree'):
            return
        for item in self._iter_tree_items():
            for column in range(self.tree.columnCount()):
                item.setBackground(column, QBrush())

        highlight = self.palette().highlight().color()
        if active:
            for group_item in self._iter_tree_items():
                group_path = group_item.data(0, _ROLE_PATH)
                if group_item.data(0, _ROLE_KIND) != _KIND_GROUP or not isinstance(group_path, tuple):
                    continue
                color = QColor(_DRAG_GROUP_COLORS[(self._group_depth(group_path) - 1) % len(_DRAG_GROUP_COLORS)])
                color.setAlpha(58)
                brush = QBrush(color)
                for item in self._iter_tree_items():
                    item_path = item.data(0, _ROLE_PATH)
                    if isinstance(item_path, tuple) and (item_path == group_path or self._is_descendant_path(item_path, group_path)):
                        for column in range(self.tree.columnCount()):
                            item.setBackground(column, brush)

        if active:
            self.tree.setStyleSheet(f'QTreeWidget {{ border: 1px solid {highlight.name()}; }}')
        else:
            self.tree.setStyleSheet('')

    def _drop_plan(self, target: QTreeWidgetItem | None, drop_position):
        selected_paths = self._selected_movable_paths()
        if not selected_paths or not self._config_has_groups():
            return None

        on_viewport = QAbstractItemView.DropIndicatorPosition.OnViewport
        on_item = QAbstractItemView.DropIndicatorPosition.OnItem
        above_item = QAbstractItemView.DropIndicatorPosition.AboveItem
        below_item = QAbstractItemView.DropIndicatorPosition.BelowItem

        if target is None or drop_position == on_viewport:
            return ('insert', (), None)

        target_path = target.data(0, _ROLE_PATH)
        if not isinstance(target_path, tuple) or target_path in selected_paths:
            return None
        if any(self._is_descendant_path(target_path, path) for path in selected_paths):
            return None

        if drop_position == on_item:
            if target.data(0, _ROLE_KIND) == _KIND_GROUP:
                return ('insert', target_path, None)
            return ('insert', target_path[:-1], None)

        if drop_position in (above_item, below_item):
            insert_at = target_path[-1] + (1 if drop_position == below_item else 0)
            return ('insert', target_path[:-1], insert_at)

        return None

    def _is_valid_item_drop(self, target: QTreeWidgetItem | None, drop_position) -> bool:
        return self._drop_plan(target, drop_position) is not None

    @staticmethod
    def _adjust_path_after_removals(path: tuple[int, ...], removed_paths: list[tuple[int, ...]]) -> tuple[int, ...]:
        adjusted = []
        for depth, index in enumerate(path):
            parent = path[:depth]
            removed_before = sum(
                1
                for removed in removed_paths
                if len(removed) == depth + 1 and removed[:depth] == parent and removed[depth] < index
            )
            adjusted.append(index - removed_before)
        return tuple(adjusted)

    def _move_selected_items_to_drop(self, target: QTreeWidgetItem | None, drop_position) -> bool:
        plan = self._drop_plan(target, drop_position)
        if plan is None:
            return False

        selected_paths = self._selected_movable_paths()
        current_rules = self.config_manager.replacement_rules
        moving_entries = [
            deepcopy(self._entry_at_path(current_rules, path))
            for path in selected_paths
            if isinstance(self._entry_at_path(current_rules, path), dict)
        ]
        if not moving_entries:
            return False

        kind = plan[0]
        if kind != 'insert':
            return False

        target_parent_path = plan[1]
        insert_at = plan[2]
        rules = self._remove_paths(deepcopy(current_rules), set(selected_paths))
        adjusted_parent_path = self._adjust_path_after_removals(target_parent_path, selected_paths)
        target_entries = self._entries_at_parent_path(rules, adjusted_parent_path)
        if target_entries is None:
            return False

        if insert_at is not None:
            adjusted_insert = insert_at - sum(
                1
                for path in selected_paths
                if len(path) == len(target_parent_path) + 1
                and path[:len(target_parent_path)] == target_parent_path
                and path[-1] < insert_at
            )
            adjusted_insert = max(0, min(adjusted_insert, len(target_entries)))
            for offset, entry in enumerate(moving_entries):
                target_entries.insert(adjusted_insert + offset, entry)
        else:
            target_entries.extend(moving_entries)

        self._save_with_undo(rules)
        self._refresh_tree()
        log_buffer.log('Config', f'Moved {format_count(moving_entries, "item")}')
        return True
