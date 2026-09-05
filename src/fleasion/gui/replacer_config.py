"""Replacer config window."""

from __future__ import annotations

import importlib
import re
import sys
import time
from copy import deepcopy
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NotRequired, Protocol, TypedDict, cast, override
from urllib.error import URLError

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QCloseEvent,
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QEnterEvent,
    QFontMetrics,
    QIcon,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPalette,
    QPen,
    QPixmap,
    QResizeEvent,
    QScreen,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QStyledItemDelegate,
    QStyleOptionMenuItem,
    QStyleOptionViewItem,
    QTabWidget,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from fleasion.cache.cache_viewer import CacheViewerTab
from fleasion.config.manager import (
    local_replacement_path_for_storage,
    resolve_local_replacement_path,
)
from fleasion.localization import tr, tr_count
from fleasion.utils import format_count, get_icon_path, log_buffer, open_folder
from fleasion.utils.gui_work import GuiWork
from fleasion.utils.http import http_head_status

from .file_drop import FileDropLineEdit, local_file_path_example
from .modifications_tab import ModificationsTab
from .proxy_gate import ProxyGate
from .proxy_tab import ProxyTrafficTab
from .rando_stuff_tab import RandoStuffTab
from .settings_tab import SettingsTab
from .subplace_joiner_tab import SubplaceJoinerTab

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator
    from types import TracebackType
    from typing import TypeGuard

    from fleasion.app.roblox_monitor import RobloxExitMonitor
    from fleasion.cache.cache_viewer import CacheScraperSource, CacheViewerConfig as _CacheConfig
    from fleasion.config.manager import ConfigManager
    from fleasion.gui.modifications_tab import CustomFFlagHotkeyController, ModificationSource
    from fleasion.gui.proxy_tab import (
        ProxyTrafficConfig as _TrafficConfig,
        ProxyTrafficSource as _TrafficProxy,
    )
    from fleasion.gui.subplace_joiner_tab import SubplaceJoinerConfig as _JoinerConfig
    from fleasion.modifications.manager import ModificationManager
    from fleasion.proxy.master import ProxyMaster


def _qt_optional[T](value: T) -> T | None:
    """Preserve Qt binding values whose stubs omit valid ``None`` states."""
    return value


def _set_render_hint(
    painter: QPainter,
    hint: QPainter.RenderHint,
    *,
    enabled: bool,
) -> None:
    painter.setRenderHint(hint, enabled)


def _set_widget_attribute(
    widget: QWidget,
    attribute: Qt.WidgetAttribute,
    *,
    enabled: bool,
) -> None:
    widget.setAttribute(attribute, enabled)


def _set_tree_item_bool_data(
    item: QTreeWidgetItem,
    column: int,
    role: int,
    *,
    enabled: bool,
) -> None:
    item.setData(column, role, enabled)


class _BestEffortExceptionGuard:
    """Contain non-fatal UI/cleanup errors while preserving selected exceptions."""

    def __init__(
        self,
        *,
        propagate: tuple[type[Exception], ...] = (),
        log_category: str | None = None,
        log_prefix: str | None = None,
    ) -> None:
        self._propagate = propagate
        self._log_category = log_category
        self._log_prefix = log_prefix

    def __enter__(self) -> None:
        return None

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> bool:
        if exc is None or not isinstance(exc, Exception) or isinstance(exc, self._propagate):
            return False
        if self._log_category is not None and self._log_prefix is not None:
            log_buffer.log(self._log_category, f'{self._log_prefix}: {exc}')
        return True


class _ProfileRule(TypedDict, total=False):
    type: Literal['profile']
    name: str
    enabled: bool
    replace_ids: list[int | str]
    mode: str
    remove: bool
    with_id: int
    cdn_url: str
    local_path: str


class _GroupRule(TypedDict):
    type: Literal['group']
    name: str
    children: list[_RuleEntry]
    expanded: NotRequired[bool]


type _RuleEntry = _ProfileRule | _GroupRule
type _RuleList = list[_RuleEntry]


class _ModeFields(TypedDict, total=False):
    with_id: int
    cdn_url: str
    local_path: str
    _raw: str


class _ConfigSettings(TypedDict, total=False):
    last_config: str


class _TrayConfigLike(Protocol):
    close_to_tray: bool


class _SystemTrayLike(Protocol):
    config_manager: _TrayConfigLike
    _exiting: bool

    def notify_dashboard_closed(self) -> None: ...


class _ConfigManagerLike(Protocol):
    replacement_rules: _RuleList
    always_on_top: bool
    window_geometry: str
    proxy_features_enabled: bool
    proxy_mode: str
    last_config: str
    configs_folder: Path
    config_names: list[str]
    enabled_configs: list[str]
    settings: _ConfigSettings

    def set_config_enabled(self, name: str, enabled: bool) -> None: ...
    def reconcile_configs(self, save: bool = True) -> bool: ...
    def is_config_enabled(self, name: str) -> bool: ...
    def is_valid_config_name(self, name: str) -> bool: ...
    def create_config(self, name: str) -> bool: ...
    def delete_config(self, name: str) -> bool: ...
    def rename_config(self, old_name: str, new_name: str) -> bool: ...
    def duplicate_config(self, name: str, new_name: str) -> bool: ...


class _ConfigMenuEntry(TypedDict):
    name: str
    checked: NotRequired[bool]
    icon: NotRequired[QIcon]


type _SortKey = tuple[int, int | float | str]
type _DropPlan = tuple[str, tuple[int, ...], int | None]


if TYPE_CHECKING:

    def _real_config(value: _ConfigManagerLike) -> ConfigManager: ...

    def _optional_screen(value: QScreen) -> QScreen | None: ...

    def _screen_at(point: QPoint) -> QScreen | None: ...

    def _tree_item(value: QTreeWidgetItem | None) -> QTreeWidgetItem: ...

    def _item_path(value: object) -> tuple[int, ...] | None: ...

    def _group_ancestors(value: object) -> tuple[tuple[int, ...], ...]: ...

    def _decode_qbytearray_data(value: object) -> str: ...

    def _required_profile(value: _RuleEntry) -> _ProfileRule: ...

    def _required_entry(value: _RuleEntry | None) -> _RuleEntry: ...

    def _required_mode_str(fields: _ModeFields, key: Literal['cdn_url', 'local_path']) -> str: ...

    def _required_profile_name(rule: _ProfileRule) -> str: ...

    def _tray_exiting(tray: _SystemTrayLike) -> bool: ...

    def _set_replacer_window_ref(tab: CacheViewerTab, window: ReplacerConfigWindow) -> None: ...

    def _register_interceptor(proxy: ProxyMaster, module: object) -> None: ...

    def _optional_widget(value: QWidget) -> QWidget | None: ...

    def _children_if_present(entry: _RuleEntry) -> _RuleList: ...

    def _depth_map(value: object) -> dict[tuple[int, ...], int]: ...
else:

    def _real_config(value: _ConfigManagerLike) -> ConfigManager:
        return value

    def _optional_screen(value: QScreen) -> QScreen | None:
        return value

    def _screen_at(point: QPoint) -> QScreen | None:
        return QApplication.screenAt(point)

    def _tree_item(value: QTreeWidgetItem | None) -> QTreeWidgetItem:
        return value

    def _item_path(value: object) -> tuple[int, ...] | None:
        return value if isinstance(value, tuple) else None

    def _group_ancestors(value: object) -> tuple[tuple[int, ...], ...]:
        return value or ()

    def _decode_qbytearray_data(value: object) -> str:
        return value.decode('utf-8')

    def _required_profile(value: _RuleEntry) -> _ProfileRule:
        return value

    def _required_entry(value: _RuleEntry | None) -> _RuleEntry:
        return value

    def _required_mode_str(fields: _ModeFields, key: Literal['cdn_url', 'local_path']) -> str:
        return fields[key]

    _required_profile_name = itemgetter('name')

    def _tray_exiting(tray: _SystemTrayLike) -> bool:
        return bool(vars(tray)['_exiting'])

    def _set_replacer_window_ref(tab: CacheViewerTab, window: ReplacerConfigWindow) -> None:
        vars(tab)['_replacer_window_ref'] = window

    def _register_interceptor(proxy: ProxyMaster, module: object) -> None:
        proxy.register_module_interceptor(module)

    def _optional_widget(value: QWidget) -> QWidget | None:
        return value

    def _children_if_present(entry: _RuleEntry) -> _RuleList:
        return entry.get('children', [])

    def _depth_map(value: object) -> dict[tuple[int, ...], int]:
        return value


_ROLE_PATH = Qt.ItemDataRole.UserRole
_ROLE_KIND = Qt.ItemDataRole.UserRole.value + 1
_ROLE_SORT_BASE = Qt.ItemDataRole.UserRole.value + 16
_ROLE_DRAW_GROUP_ICON = Qt.ItemDataRole.UserRole.value + 32
_ROLE_GROUP_ICON_INDENT = Qt.ItemDataRole.UserRole.value + 33
_ROLE_GROUP_ANCESTORS = Qt.ItemDataRole.UserRole.value + 34
_ROLE_GROUP_DEPTH = Qt.ItemDataRole.UserRole.value + 35
_KIND_PROFILE = 'profile'
_KIND_GROUP = 'group'
_MIXED_STATUS = '—'
_DRAG_GROUP_COLORS: tuple[str, ...] = ('#2d6cdf', '#2f9e44', '#f08c00', '#ae3ec9', '#0ca678')
_TREE_INDENT_PX = 9
_GROUP_ROW_HEIGHT_PX = 24
_GROUP_CONTENT_INDENT_SPACES = 5
_PROFILE_NAME_COLUMN = 1
_GROUP_FOLDER_ICON_WIDTH_PX = 13
_GROUP_FOLDER_ICON_HEIGHT_PX = 10
_GROUP_FOLDER_ICON_GAP_PX = 4
_GROUP_GUIDE_GUTTER_PX = 2
_GROUP_GUIDE_STEP_PX = 15
_CONFIG_MENU_ROW_HEIGHT_PX = 28
_CONFIG_MENU_SCREEN_MARGIN_PX = 12
_CONFIG_MENU_OPEN_RELEASE_GRACE_SEC = 0.25
_CONFIG_MENU_BUTTON_POPUP_EXTRA_WIDTH_PX = 24
_ID_SPLIT_RE = re.compile(r'[,\s;]+')


def _ensure_text_width(widget: QWidget, minimum_width: int = 0) -> None:
    """Keep translated widget text from being clipped by legacy fixed widths."""
    widget.setMinimumWidth(max(minimum_width, widget.sizeHint().width()))


def _replacement_path_tooltip(*, empty_removes: bool = True) -> str:
    lines = [
        tr('replacer.path_tooltip.config_files'),
        tr('replacer.path_tooltip.example'),
        tr('replacer.path_tooltip.nesting'),
        tr('replacer.path_tooltip.normal_path', path=local_file_path_example()),
    ]
    if empty_removes:
        lines.append(tr('replacer.path_tooltip.empty_removes'))
    return '\n'.join(lines)


class _ElidedLabel(QLabel):
    """Label that keeps long dynamic text readable without widening the window."""

    def __init__(self, text: str = '', parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = text
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self._refresh_text()

    def set_full_text(self, text: str) -> None:
        self._full_text = text
        self.setToolTip(text)
        self._refresh_text()

    def _refresh_text(self) -> None:
        available_width = max(0, self.contentsRect().width() - 4)
        display_text = self.fontMetrics().elidedText(
            self._full_text,
            Qt.TextElideMode.ElideMiddle,
            available_width,
        )
        if self.text() != display_text:
            super().setText(display_text)
        self.setToolTip(self._full_text)

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh_text()


class UndoManager:
    """Undo history manager."""

    def __init__(self, max_history: int = 50) -> None:
        self.history: list[_RuleList] = []
        self.future: list[_RuleList] = []
        self.max_history = max_history

    def save_state(self, rules: _RuleList, *, copy_state: bool = True) -> None:
        """Save a state to history."""
        self.history.append(deepcopy(rules) if copy_state else rules)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        self.future.clear()

    def undo(self) -> _RuleList | None:
        """Undo to previous state."""
        if len(self.history) > 1:
            self.future.append(self.history.pop())
            return deepcopy(self.history[-1])
        if len(self.history) == 1:
            return deepcopy(self.history[0])
        return None

    def redo(self) -> _RuleList | None:
        """Redo a previously undone state."""
        if self.future:
            state = self.future.pop()
            self.history.append(state)
            return deepcopy(state)
        return None

    def clear(self) -> None:
        """Clear history."""
        self.history.clear()
        self.future.clear()


class ReplacerTreeItem(QTreeWidgetItem):
    """Tree item with per-column sort keys for profile and group rows."""

    @staticmethod
    def _sort_key(value: object) -> _SortKey | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return (0, int(value))
        if isinstance(value, (int, float)):
            return (0, value)
        if isinstance(value, str):
            return (1, value.casefold())
        return (2, str(value).casefold())

    @override
    def __lt__(self, other: QTreeWidgetItem) -> bool:
        tree = _qt_optional(self.treeWidget())
        column = tree.sortColumn() if tree is not None else 0
        left = self._sort_key(self.data(column, _ROLE_SORT_BASE))
        right = self._sort_key(other.data(column, _ROLE_SORT_BASE))
        if left is not None and right is not None:
            return left < right
        return self.text(column).lower() < other.text(column).lower()


class _ProfileNameDelegate(QStyledItemDelegate):
    """Draw group folder icons without relying on platform emoji fonts."""

    @override
    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex | QPersistentModelIndex,
    ) -> None:
        if not index.data(_ROLE_DRAW_GROUP_ICON):
            super().paint(painter, option, index)
            return

        item_option = QStyleOptionViewItem(option)
        self.initStyleOption(item_option, index)
        label = item_option.text
        item_option.text = ''
        item_option.icon = QIcon()
        widget = _qt_optional(item_option.widget)
        style = widget.style() if widget is not None else QApplication.style()

        painter.save()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, item_option, painter, widget)

        content_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, item_option, widget
        )
        content_x = content_rect.x() + (index.data(_ROLE_GROUP_ICON_INDENT) or 0)
        icon_y = content_rect.y() + max(
            0, (content_rect.height() - _GROUP_FOLDER_ICON_HEIGHT_PX) // 2
        )
        self._draw_folder_icon(
            painter,
            QRect(
                content_x,
                icon_y,
                _GROUP_FOLDER_ICON_WIDTH_PX,
                _GROUP_FOLDER_ICON_HEIGHT_PX,
            ),
            item_option,
        )

        text_rect = QRect(content_rect)
        text_rect.setX(content_x + _GROUP_FOLDER_ICON_WIDTH_PX + _GROUP_FOLDER_ICON_GAP_PX)
        painter.setFont(item_option.font)
        painter.setPen(
            item_option.palette.color(
                QPalette.ColorGroup.Active
                if item_option.state & QStyle.StateFlag.State_Enabled
                else QPalette.ColorGroup.Disabled,
                QPalette.ColorRole.HighlightedText
                if item_option.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text,
            )
        )
        painter.drawText(text_rect, item_option.displayAlignment, label)
        painter.restore()

    @staticmethod
    def _draw_folder_icon(painter: QPainter, rect: QRect, option: QStyleOptionViewItem) -> None:
        color = option.palette.color(
            QPalette.ColorRole.HighlightedText
            if option.state & QStyle.StateFlag.State_Selected
            else QPalette.ColorRole.Text
        )
        _set_render_hint(painter, QPainter.RenderHint.Antialiasing, enabled=False)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(color, 1))
        tab_width = max(4, rect.width() // 2)
        tab_height = 3
        painter.drawLine(rect.left() + 1, rect.top() + tab_height, rect.left() + 1, rect.top() + 1)
        painter.drawLine(rect.left() + 1, rect.top() + 1, rect.left() + tab_width, rect.top() + 1)
        painter.drawLine(
            rect.left() + tab_width,
            rect.top() + 1,
            rect.left() + tab_width + 2,
            rect.top() + tab_height,
        )
        painter.drawLine(
            rect.left() + tab_width + 2,
            rect.top() + tab_height,
            rect.right() - 1,
            rect.top() + tab_height,
        )
        painter.drawRect(
            rect.left(),
            rect.top() + tab_height,
            rect.width() - 1,
            rect.height() - tab_height - 1,
        )


class ReplacerRulesTree(QTreeWidget):
    """Constrained tree drag/drop for moving profiles and groups."""

    def __init__(self, owner: ReplacerConfigWindow, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._owner = owner

    @override
    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        if not self._owner.selected_movable_paths():
            return
        self._owner.set_drag_hint_active(active=True)
        try:
            super().startDrag(supported_actions)
        finally:
            self._owner.set_drag_hint_active(active=False)

    @override
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._owner.selected_movable_paths():
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        target = self.itemAt(event.position().toPoint())
        if self._owner.is_valid_item_drop(target, self.dropIndicatorPosition()):
            event.acceptProposedAction()
        else:
            event.ignore()

    @override
    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        # Keep target highlights visible while the drag cursor is outside the
        # window; startDrag/dropEvent clear them when the drag actually ends.
        super().dragLeaveEvent(event)

    @override
    def dropEvent(self, event: QDropEvent) -> None:
        target = self.itemAt(event.position().toPoint())
        if self._owner.move_selected_items_to_drop(target, self.dropIndicatorPosition()):
            event.acceptProposedAction()
        else:
            event.ignore()
        self._owner.set_drag_hint_active(active=False)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        self._owner.paint_group_guides(self.viewport())


class _ConfigMenuRow(QWidget):
    """Full-width row painted with the native QMenu item style."""

    activated = Signal(str)
    toggled = Signal(str, bool)

    def __init__(
        self,
        name: str,
        parent_menu: _ScrollableConfigMenu,
        *,
        checkable: bool = False,
        checked: bool = False,
        icon: QIcon | None = None,
    ) -> None:
        # QWidget construction may call the Python sizeHint override before
        # QWidget.__init__ returns, so every field used by sizeHint/style
        # calculation must exist first.
        self._name = name
        self._parent_menu = parent_menu
        self._checkable = checkable
        self._checked = checked
        self._icon = icon or QIcon()
        self._pressed = False
        self._hovered = False
        super().__init__()
        self.setMouseTracking(True)
        _set_widget_attribute(self, Qt.WidgetAttribute.WA_Hover, enabled=True)
        self.setFixedHeight(_CONFIG_MENU_ROW_HEIGHT_PX)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    @override
    def sizeHint(self) -> QSize:
        option = self._style_option()
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(self._name)
        icon_width = option.maxIconWidth + 12 if option.maxIconWidth else 0
        check_width = 28 if self._checkable else 0
        return self.style().sizeFromContents(
            QStyle.ContentsType.CT_MenuItem,
            option,
            QSize(text_width + icon_width + check_width + 36, _CONFIG_MENU_ROW_HEIGHT_PX),
            self,
        )

    def isChecked(self) -> bool:  # ruff: ignore[invalid-function-name]
        return self._checked

    def setChecked(self, checked: bool) -> None:  # ruff: ignore[invalid-function-name]
        if self._checked == checked:
            return
        self._checked = checked
        self.update()

    def click(self) -> None:
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
        option.maxIconWidth = (
            self._icon.actualSize(QSize(16, 16)).width() if not self._icon.isNull() else 0
        )
        option.reservedShortcutWidth = 0
        if self._hovered:
            option.state |= QStyle.StateFlag.State_Selected
        else:
            option.state &= ~QStyle.StateFlag.State_Selected
        return option

    @override
    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        self.style().drawControl(
            QStyle.ControlElement.CE_MenuItem, self._style_option(), painter, self
        )

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            event.accept()
            return
        super().mousePressEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            if self.rect().contains(event.position().toPoint()):
                self._activate()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _activate(self) -> None:
        if self._parent_menu.should_ignore_opening_release():
            return
        if self._checkable:
            self._checked = not self._checked
            self.update()
            self.toggled.emit(self._name, self._checked)
        else:
            self.activated.emit(self._name)


class _ScrollableConfigMenu(QMenu):
    """Config picker popup that scrolls when the config list exceeds the screen."""

    item_selected = Signal(str)
    item_toggled = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None, *, checkable: bool = False) -> None:
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

        self._content_action = QWidgetAction(self)
        self._content_action.setDefaultWidget(self.scroll_area)
        self.addAction(self._content_action)
        self.aboutToShow.connect(self._guard_opening_mouse_release)

    @override
    def sizeHint(self) -> QSize:
        """Use the embedded scroll area's size without rebuilding the action."""
        if self._natural_content_size.isValid() and self.scroll_area.size().isValid():
            return self.scroll_area.size()
        return super().sizeHint()

    def set_entries(self, entries: list[_ConfigMenuEntry], *, minimum_width: int = 0) -> None:
        """Replace the displayed config rows."""
        old_container = _optional_widget(self.scroll_area.takeWidget())
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
            row = QLabel(tr('ui.gui.replacer_config.no_configs'))
            row.setStyleSheet('padding: 5px 8px; color: palette(placeholder-text);')
            row.setFixedHeight(_CONFIG_MENU_ROW_HEIGHT_PX)
            row.installEventFilter(self)
            layout.addWidget(row)

        self.scroll_area.setWidget(container)
        container.adjustSize()
        self._natural_content_size = container.sizeHint()
        self._set_popup_content_size(self._natural_content_size.height())
        self._reset_action_geometry()

    def constrain_to_button(self, button: QPushButton | None) -> None:
        """Bound the popup to the screen containing the owning button."""
        if button is None:
            return
        anchor = button.mapToGlobal(button.rect().bottomLeft())
        screen = _optional_screen(button.screen())
        if screen is None:
            screen = _screen_at(anchor)
        if screen is None:
            return
        self.constrain_to_available_geometry(screen.availableGeometry(), anchor.y())

    def constrain_to_available_geometry(
        self, available_geometry: QRect | None, anchor_y: int | None = None
    ) -> None:
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
        self._reset_action_geometry()

    def _set_popup_content_size(self, max_height: int, *, max_width: int | None = None) -> None:
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
            widget.setFixedWidth(viewport_width)
        self.scroll_area.setFixedSize(max(1, width), height)
        self.scroll_area.updateGeometry()
        self.updateGeometry()

    def _reset_action_geometry(self) -> None:
        """Invalidate QMenu/QWidgetAction cached geometry after row count changes."""
        self.adjustSize()
        self.resize(self.sizeHint())
        self.updateGeometry()
        self.update()

    def _select_item(self, name: str) -> None:
        self.item_selected.emit(name)
        self.hide()

    def _guard_opening_mouse_release(self) -> None:
        self._opening_release_deadline = time.monotonic() + _CONFIG_MENU_OPEN_RELEASE_GRACE_SEC

    def should_ignore_opening_release(self) -> bool:
        return self._should_ignore_opening_release()

    def _should_ignore_opening_release(self) -> bool:
        if not self._opening_release_deadline:
            return False
        if time.monotonic() <= self._opening_release_deadline:
            self._opening_release_deadline = 0.0
            return True
        self._opening_release_deadline = 0.0
        return False

    @override
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.MouseButtonPress:
            self._opening_release_deadline = 0.0
        elif (
            event.type() == QEvent.Type.MouseButtonRelease and self.should_ignore_opening_release()
        ):
            return True
        return super().eventFilter(obj, event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:
        if event is None:
            return
        if self.should_ignore_opening_release():
            return
        action = self.actionAt(event.pos())
        if isinstance(action, QWidgetAction) and action.defaultWidget() == self.scroll_area:
            return
        super().mouseReleaseEvent(event)


class ReplacerConfigWindow(QDialog):
    """Replacer configuration window with tabs."""

    first_painted = Signal()

    def __init__(  # ruff: ignore[too-many-positional-arguments]
        self,
        config_manager: _ConfigManagerLike,
        proxy_master: ProxyMaster | None = None,
        mod_manager: ModificationManager | None = None,
        roblox_monitor: RobloxExitMonitor | None = None,
        system_tray: _SystemTrayLike | None = None,
        hotkey_controller: object | None = None,
    ) -> None:
        super().__init__()
        self.config_manager = config_manager
        self.proxy_master = proxy_master
        self._mod_manager = mod_manager
        self.roblox_monitor = roblox_monitor
        self._system_tray = system_tray
        self._hotkey_controller = hotkey_controller
        self.undo_manager = UndoManager()
        self.undo_manager.save_state(self.config_manager.replacement_rules, copy_state=False)
        self.config_enabled_vars: dict[str, QWidget] = {}
        self._asset_types_popup_last_closed = 0.0
        self._dialog_asset_types_popup_last_closed = 0.0
        self._dialog_asset_types_popup: QMenu | None = None
        self._prejsons_dialog: QDialog | None = None
        self._preload = GuiWork(self)
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._preload.cancel)
        self._first_paint = False
        self._closed = False
        self._cache_viewer_tab: CacheViewerTab | None = None
        self._settings_tab: SettingsTab | None = None
        self._rando_stuff_tab: RandoStuffTab | None = None
        self._subplace_tab: SubplaceJoinerTab | None = None
        self._registered_module_interceptors: list[object] = []
        self._tab_pages: dict[str, QWidget] = {}
        self._tab_indices: dict[str, int] = {}
        self._proxy_gates: list[ProxyGate] = []
        self._env_proxy_gates: list[ProxyGate] = []

        self.setWindowTitle(tr('ui.gui.replacer_config.dashboard'))
        self.resize(900, 750)
        self.setMinimumSize(800, 650)
        if sys.platform == 'darwin':
            _set_widget_attribute(
                self,
                Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow,
                enabled=True,
            )

        # Set window flags to allow minimize/maximize
        flags = (
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
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

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not self._first_paint and not self._closed:
            self._first_paint = True
            self.first_painted.emit()
            self._preload.start(self._preload_tabs())

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        """Save window geometry on close."""
        self._closed = True
        self._preload.cancel()
        if self._cache_viewer_tab is not None:
            self._cache_viewer_tab.shutdown()
        self.config_manager.window_geometry = _decode_qbytearray_data(
            self.saveGeometry().toHex().data()
        )
        self._unregister_module_interceptors()
        if (
            self._system_tray is not None
            and self._system_tray.config_manager.close_to_tray
            and not _tray_exiting(self._system_tray)
        ):
            with _BestEffortExceptionGuard():
                self._system_tray.notify_dashboard_closed()
        super().closeEvent(event)

    def _unregister_module_interceptors(self) -> None:
        if self.proxy_master is None:
            return
        for module in getattr(self, '_registered_module_interceptors', ()):
            with _BestEffortExceptionGuard(
                log_category='Proxy',
                log_prefix='Failed to unregister dashboard interceptor',
            ):
                self.proxy_master.unregister_module_interceptor(module)
        self._registered_module_interceptors = []

    def _set_icon(self) -> None:
        """Set window icon."""
        if icon_path := get_icon_path():
            self.setWindowIcon(QIcon(str(icon_path)))

    def _setup_ui(self) -> None:
        """Setup the UI with tabs."""
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        # Create tab widget
        self.tab_widget = QTabWidget()

        # Create Replacer tab
        replacer_tab = self._create_replacer_tab()
        self.tab_widget.addTab(replacer_tab, tr('ui.gui.replacer_config.replacer'))

        # Reserve the final tab order without constructing hidden pages
        names: list[tuple[str, str]] = []
        if self.proxy_master is not None:
            names.append(('scraper', tr('ui.gui.replacer_config.scraper')))
        if self._mod_manager is not None:
            names.append(('modifications', tr('ui.gui.replacer_config.modifications')))
        names.extend(
            (
                ('subplace_joiner', tr('ui.gui.replacer_config.subplace_joiner')),
                ('miscellaneous', tr('ui.gui.replacer_config.miscellaneous')),
                ('proxy', tr('ui.gui.replacer_config.proxy')),
                ('settings', tr('ui.gui.replacer_config.settings')),
            )
        )
        for name, title in names:
            page = QWidget(self.tab_widget)
            QVBoxLayout(page).setContentsMargins(0, 0, 0, 0)
            self._tab_pages[name] = page
            wrapper = page
            if name == 'subplace_joiner':
                wrapper = self._proxy_required(page)
            elif name == 'proxy':
                wrapper = self._env_proxy_required(page)
            index = self.tab_widget.addTab(wrapper, title)
            self._tab_indices[name] = index
            self.tab_widget.setTabEnabled(index, False)  # ruff: ignore[boolean-positional-value-in-call]

        main_layout.addWidget(self.tab_widget)

        self.setLayout(main_layout)
        self.set_proxy_features_enabled(self.config_manager.proxy_features_enabled)

        # Setup keyboard shortcuts
        undo_shortcut = QShortcut(QKeySequence('Ctrl+Z'), self)
        undo_shortcut.activated.connect(self._do_undo)

        delete_shortcut = QShortcut(QKeySequence('Delete'), self)
        delete_shortcut.activated.connect(self._delete_selected)

        redo_shortcut = QShortcut(QKeySequence('Ctrl+Y'), self)
        redo_shortcut.activated.connect(self._do_redo)

        escape_shortcut = QShortcut(QKeySequence('Escape'), self)
        escape_shortcut.activated.connect(self.close)

    def _install_tab(self, name: str, widget: QWidget, *, enabled: bool = True) -> None:
        page = self._tab_pages[name]
        layout = cast('QVBoxLayout', page.layout())
        layout.addWidget(widget)
        self.tab_widget.setTabEnabled(self._tab_indices[name], enabled)
        self.set_proxy_features_enabled(self.config_manager.proxy_features_enabled)

    def _preload_tabs(self) -> Generator[None]:
        config = _real_config(self.config_manager)
        # The account selector feeds the joiner, so construct it first
        rando_tab = RandoStuffTab(
            parent=self._tab_pages['miscellaneous'],
            config_manager=config,
            proxy_master=self.proxy_master,
            defer_setup=True,
        )
        yield from rando_tab.build_ui()
        self._rando_stuff_tab = rando_tab
        self._install_tab('miscellaneous', self._rando_stuff_tab)
        if self.proxy_master is not None:
            _register_interceptor(self.proxy_master, self._rando_stuff_tab)
            self._registered_module_interceptors.append(self._rando_stuff_tab)
        yield
        subplace_tab = SubplaceJoinerTab(
            rando_tab=self._rando_stuff_tab,
            config_manager=cast('_JoinerConfig', config),
            parent=self._tab_pages['subplace_joiner'],
            defer_setup=True,
            proxy_master=self.proxy_master,
        )
        yield from subplace_tab.build_ui()
        self._subplace_tab = subplace_tab
        self._rando_stuff_tab.selected_account_changed.connect(
            self._subplace_tab.set_selected_account
        )
        self._install_tab('subplace_joiner', self._subplace_tab)
        if self.proxy_master is not None:
            _register_interceptor(self.proxy_master, self._subplace_tab)
            self._registered_module_interceptors.append(self._subplace_tab)
        yield
        self._proxy_traffic_tab = ProxyTrafficTab(
            config_manager=cast('_TrafficConfig', config),
            parent=self._tab_pages['proxy'],
            proxy_master=cast('_TrafficProxy | None', self.proxy_master),
        )
        self._install_tab('proxy', self._proxy_traffic_tab)
        yield
        settings_tab = SettingsTab(
            config,
            system_tray=self._system_tray,
            parent=self._tab_pages['settings'],
            defer_setup=True,
        )
        yield from settings_tab.build_ui()
        self._settings_tab = settings_tab
        self._install_tab('settings', self._settings_tab)
        yield
        if self.proxy_master is not None:
            cache_tab = self._create_cache_tab()
            yield from cache_tab.build_ui()
            self._cache_viewer_tab = cache_tab
            cache_tab.initial_population_finished.connect(self._enable_scraper_tab)
            self._install_tab('scraper', cache_tab, enabled=cache_tab.initial_population_ready)
            yield
        if self._mod_manager is not None:
            tab = ModificationsTab(
                cast('ModificationSource', self._mod_manager),
                self.roblox_monitor,
                config_manager=config,
                proxy_master=self.proxy_master,
                hotkey_controller=cast(
                    'CustomFFlagHotkeyController | None', self._hotkey_controller
                ),
                parent=self._tab_pages['modifications'],
                defer_setup=True,
            )
            yield from tab.build_ui()
            self._install_tab('modifications', tab)

    def _proxy_required(self, widget: QWidget) -> ProxyGate:
        gate = ProxyGate(widget, parent=self.tab_widget)
        self._proxy_gates.append(gate)
        return gate

    def _env_proxy_required(self, widget: QWidget) -> ProxyGate:
        gate = ProxyGate(
            widget,
            message=tr('replacer.proxy_gate.env_mode_required'),
            parent=self.tab_widget,
        )
        self._env_proxy_gates.append(gate)
        return gate

    def _env_proxy_effective_enabled(self) -> bool:
        return bool(
            self.config_manager.proxy_features_enabled and self.config_manager.proxy_mode == 'env'
        )

    def refresh_env_proxy_gate(self) -> None:
        enabled = self._env_proxy_effective_enabled()
        for gate in self._env_proxy_gates:
            gate.set_proxy_enabled(enabled)

    def refresh_settings_tab(self) -> None:
        if self._settings_tab is not None:
            self._settings_tab.refresh_from_config()

    def apply_cache_viewer_display_setting(
        self,
        setting: Literal['show_names', 'show_creator_id'],
        *,
        enabled: bool,
    ) -> None:
        tab = getattr(self, '_cache_viewer_tab', None)
        if tab is None:
            return
        if setting == 'show_names':
            tab.set_show_names(enabled)
        else:
            tab.set_show_creator_id(enabled)

    def set_cache_scraper_enabled(self, *, enabled: bool) -> None:
        if self._settings_tab is not None:
            self._settings_tab.set_cache_scraper_enabled(enabled)
        if self._cache_viewer_tab is not None:
            self._cache_viewer_tab.set_cache_scraper_enabled(enabled)

    def set_proxy_features_enabled(self, enabled: bool) -> None:
        for gate in self._proxy_gates:
            gate.set_proxy_enabled(enabled)
        if self._cache_viewer_tab is not None:
            self._cache_viewer_tab.set_proxy_features_enabled(enabled)
        if self._rando_stuff_tab is not None:
            self._rando_stuff_tab.set_proxy_features_enabled(enabled)
        self.refresh_env_proxy_gate()

    def _create_replacer_tab(self) -> QWidget:
        """Create the replacer configuration tab."""
        replacer_widget = QWidget()
        replacer_layout = QVBoxLayout()
        replacer_layout.setContentsMargins(0, 0, 0, 0)

        top_section_widget = QWidget()
        top_section_layout = QVBoxLayout(top_section_widget)
        top_section_layout.setContentsMargins(0, 0, 0, 0)
        top_section_layout.setSpacing(6)

        # Config selector section
        self._create_config_section(top_section_layout)

        # Rules tree section
        self._create_tree_section(top_section_layout)

        self._replacer_top_proxy_gate = self._proxy_required(top_section_widget)
        replacer_layout.addWidget(self._replacer_top_proxy_gate)

        # Edit section
        self._create_edit_section(replacer_layout)

        # Footer
        self._create_footer(replacer_layout)

        replacer_widget.setLayout(replacer_layout)
        return replacer_widget

    def _enable_scraper_tab(self) -> None:
        enabled = True
        self.tab_widget.setTabEnabled(self._tab_indices['scraper'], enabled)

    def _create_cache_tab(self) -> CacheViewerTab:
        """Create the cache viewer tab."""
        if self.proxy_master is None:
            msg = 'cache tab requires proxy master'
            raise RuntimeError(msg)
        tab = CacheViewerTab(
            self.proxy_master.cache_manager,
            cast('CacheScraperSource', self.proxy_master.cache_scraper),
            self._tab_pages['scraper'],
            config_manager=cast('_CacheConfig', self.config_manager),
            defer_setup=True,
        )
        # Store direct reference so Send-to-Replacer can find the entry fields
        # regardless of how Qt re-parents the widget when added to QTabWidget.
        _set_replacer_window_ref(tab, self)
        return tab

    def _create_config_section(self, parent_layout: QVBoxLayout) -> None:
        """Create the configuration selector section."""
        config_group = QGroupBox(tr('ui.gui.replacer_config.configuration'))
        config_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        config_layout = QVBoxLayout()

        # Row 1: Configuration controls
        row1 = QHBoxLayout()
        editing_label = QLabel(tr('ui.gui.replacer_config.editing'))
        _ensure_text_width(editing_label, 50)
        row1.addWidget(editing_label)

        # Use button with menu (same style as enabled configs)
        # Prepend a single space plus a tiny hair-space to give a subtle gap
        self.config_menu_btn = QPushButton(
            tr('ui.gui.replacer_config.value', value0=self.config_manager.last_config)
        )
        self.config_menu = _ScrollableConfigMenu(self.config_menu_btn)
        self.config_menu.aboutToShow.connect(self._rebuild_editing_menu)
        self.config_menu.item_selected.connect(self._on_config_select)
        self.config_menu_btn.setMenu(self.config_menu)
        row1.addWidget(self.config_menu_btn)

        self._rebuild_editing_menu()

        row1.addSpacing(12)

        enabled_label = QLabel(tr('ui.gui.replacer_config.enabled'))
        _ensure_text_width(enabled_label, 54)
        row1.addWidget(enabled_label)

        self.enabled_menu_btn = QPushButton(tr('ui.gui.replacer_config.select'))
        self.enabled_menu = _ScrollableConfigMenu(self.enabled_menu_btn, checkable=True)
        self.enabled_menu.aboutToShow.connect(self._rebuild_enabled_menu)
        self.enabled_menu.item_toggled.connect(self._on_config_toggle)
        self.enabled_menu_btn.setMenu(self.enabled_menu)
        row1.addWidget(self.enabled_menu_btn)

        self._rebuild_enabled_menu()

        row1.addSpacing(8)

        separator = QLabel(tr('ui.gui.replacer_config.text'))
        separator.setStyleSheet('padding-bottom: 6px;')
        row1.addWidget(separator)

        row1.addSpacing(8)

        for text, action in [
            (tr('replacer.config.new'), 'new'),
            (tr('replacer.config.duplicate'), 'dup'),
            (tr('replacer.config.rename'), 'rename'),
            (tr('replacer.config.delete'), 'delete'),
        ]:
            btn = QPushButton(text)

            def run_config_action(_checked: bool = False, action_name: str = action) -> None:
                self._config_action(action_name)

            btn.clicked.connect(run_config_action)
            row1.addWidget(btn)

        # Removed: No Textures checkbox

        row1.addStretch()
        config_layout.addLayout(row1)

        config_group.setLayout(config_layout)
        parent_layout.addWidget(config_group)

    def _create_tree_section(self, parent_layout: QVBoxLayout) -> None:
        """Create the rules tree section."""
        # Label
        label_layout = QHBoxLayout()
        title_label = QLabel(tr('ui.gui.replacer_config.replacement_profiles'))
        title_label.setStyleSheet('font-weight: bold; padding-left: 5px;')
        label_layout.addWidget(title_label)

        label_layout.addStretch()
        parent_layout.addLayout(label_layout)

        # Tree
        self.tree = ReplacerRulesTree(self)
        self.tree.setHeaderLabels(
            [
                tr('ui.gui.replacer_config.status'),
                tr('ui.gui.replacer_config.profile_name'),
                tr('ui.gui.replacer_config.mode'),
                tr('ui.gui.replacer_config.asset_ids'),
                tr('ui.gui.replacer_config.replacement'),
            ]
        )
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)

        def expand_group(item: QTreeWidgetItem) -> None:
            self._set_group_expanded(item, expanded=True)

        def collapse_group(item: QTreeWidgetItem) -> None:
            self._set_group_expanded(item, expanded=False)

        self.tree.itemExpanded.connect(expand_group)
        self.tree.itemCollapsed.connect(collapse_group)
        self.tree.itemSelectionChanged.connect(self.tree.viewport().update)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setDropIndicatorShown(True)
        self.tree.setSortingEnabled(True)
        self.tree.setIndentation(_TREE_INDENT_PX)
        self.tree.setItemDelegateForColumn(_PROFILE_NAME_COLUMN, _ProfileNameDelegate(self.tree))

        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)

        parent_layout.addWidget(self.tree)

    def _create_edit_section(self, parent_layout: QVBoxLayout) -> None:
        """Create the add/edit profile section."""
        edit_group = QGroupBox(tr('ui.gui.replacer_config.add_edit_profile'))
        edit_group.setStyleSheet('QGroupBox::title { padding-left: 5px; }')
        edit_layout = QVBoxLayout()
        edit_layout.setSpacing(4)

        field_labels = [
            QLabel(tr('ui.gui.replacer_config.profile_name_2')),
            QLabel(tr('ui.gui.replacer_config.asset_ids_2')),
            QLabel(tr('ui.gui.replacer_config.replace_with')),
        ]
        field_label_width = max(85, *(label.sizeHint().width() for label in field_labels))
        for field_label in field_labels:
            field_label.setFixedWidth(field_label_width)
        name_label, asset_ids_label, replacement_label = field_labels

        # Profile name
        name_layout = QHBoxLayout()
        name_layout.setSpacing(5)
        name_layout.addWidget(name_label)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText(tr('ui.gui.replacer_config.optional_profile_name'))
        name_layout.addWidget(self.name_entry)
        edit_layout.addLayout(name_layout)

        # Asset IDs
        ids_layout = QHBoxLayout()
        ids_layout.setSpacing(5)
        ids_layout.addWidget(asset_ids_label)
        self.replace_entry = QLineEdit()
        self.replace_entry.setPlaceholderText(
            tr('ui.gui.replacer_config.ids_or_assettypes_separated_by_commas_spaces')
        )
        ids_layout.addWidget(self.replace_entry)

        # Add Asset Types filter button
        self.asset_types_btn = QPushButton(tr('ui.gui.replacer_config.asset_types'))
        _ensure_text_width(self.asset_types_btn, 80)
        self.asset_types_btn.clicked.connect(self._show_asset_types_popup)
        asset_type_filter = importlib.import_module('fleasion.cache.asset_type_filter')
        self.asset_types_popup = asset_type_filter.CategoryFilterPopup(parent=self)
        self.asset_types_popup.filters_changed.connect(self._on_asset_types_changed)
        self.asset_types_popup.aboutToHide.connect(self._mark_asset_types_popup_closed)
        ids_layout.addWidget(self.asset_types_btn)

        edit_layout.addLayout(ids_layout)

        # Replacement field (auto-detects mode)
        replace_layout = QHBoxLayout()
        replace_layout.setSpacing(5)
        replace_layout.addWidget(replacement_label)
        self.replacement_entry = FileDropLineEdit()
        self.replacement_entry.setPlaceholderText(
            tr('ui.gui.replacer_config.id_url_file_path_or_exampleobj_example')
        )
        self.replacement_entry.setToolTip(_replacement_path_tooltip())
        self.replacement_entry.fileDropped.connect(self._store_dropped_replacement_path)
        replacement_label.setToolTip(_replacement_path_tooltip())
        replace_layout.addWidget(self.replacement_entry)
        browse_btn = QPushButton(tr('ui.gui.replacer_config.browse'))
        browse_btn.clicked.connect(self._browse_local_file)
        _ensure_text_width(browse_btn, 80)
        replace_layout.addWidget(browse_btn)
        edit_layout.addLayout(replace_layout)

        # Buttons
        btn_layout = QHBoxLayout()
        for text, callback in [
            (tr('replacer.rules.add_new'), self._add_rule),
            (tr('replacer.rules.load_selected'), self._load_selected),
            (tr('replacer.rules.update_selected'), self._update_selected),
        ]:
            btn = QPushButton(text)
            _ensure_text_width(btn, 130)
            btn.clicked.connect(callback)
            btn_layout.addWidget(btn)
        btn_layout.addStretch()
        import_btn = QPushButton(tr('ui.gui.replacer_config.scraped_games'))
        _ensure_text_width(import_btn, 130)
        import_btn.clicked.connect(self._open_prejsons_browser)
        btn_layout.addWidget(import_btn)
        edit_layout.addLayout(btn_layout)

        edit_group.setLayout(edit_layout)
        # Prevent edit group from expanding vertically
        edit_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        parent_layout.addWidget(edit_group)

    def _create_footer(self, parent_layout: QVBoxLayout) -> None:
        """Create the footer section with buttons snapped to the right."""
        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)

        self._configs_path_label = _ElidedLabel(
            tr('ui.gui.replacer_config.configs_value', value0=self.config_manager.configs_folder)
        )
        self._configs_path_label.setStyleSheet('color: gray; font-size: 8pt; padding-left: 5px;')
        footer_layout.addWidget(self._configs_path_label)

        footer_layout.addStretch()

        help_btn = QPushButton(tr('ui.gui.replacer_config.text_2'))
        help_btn.setMaximumWidth(25)
        help_btn.setToolTip(tr('ui.gui.replacer_config.view_keybinds'))
        help_btn.clicked.connect(self._show_keybinds_help)
        footer_layout.addWidget(help_btn)

        clear_cache_btn = QPushButton(tr('ui.gui.replacer_config.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)

        configs_btn = QPushButton(tr('ui.gui.replacer_config.open_configs'))
        configs_btn.clicked.connect(self._open_configs_folder)
        footer_layout.addWidget(configs_btn)

        undo_btn = QPushButton(tr('ui.gui.replacer_config.undo_ctrl_z'))
        undo_btn.clicked.connect(self._do_undo)
        footer_layout.addWidget(undo_btn)

        parent_layout.addWidget(footer_widget)

    def _open_configs_folder(self) -> None:
        """Open the same config folder used by this window's ConfigManager."""
        configs_folder = self.config_manager.configs_folder
        self._configs_path_label.set_full_text(
            tr('ui.gui.replacer_config.configs_value', value0=configs_folder)
        )
        open_folder(configs_folder)

    def _clear_roblox_cache(self) -> None:
        delete_cache = importlib.import_module('.delete_cache', __package__)
        window = delete_cache.DeleteCacheWindow()
        window.show()

    def _show_keybinds_help(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(tr('ui.gui.replacer_config.keybinds'))
        msg.setTextFormat(Qt.TextFormat.RichText)
        msg.setText(tr('ui.gui.replacer_config.b_all_keybinds_b_br_ctrl_z'))
        msg.exec()

    def _open_prejsons_browser(self) -> None:
        """Open the PreJsons browser dialog."""
        prejsons_dialog = importlib.import_module('.prejsons_dialog', __package__)

        try:
            if self._prejsons_dialog is not None:
                self._prejsons_dialog.show()
                self._prejsons_dialog.raise_()
                self._prejsons_dialog.activateWindow()
                return
        except RuntimeError:
            self._prejsons_dialog = None

        dialog = prejsons_dialog.PreJsonsDialog(self)

        def clear_prejson_dialog(*_args: object) -> None:
            self._prejsons_dialog = None

        dialog.destroyed.connect(clear_prejson_dialog)
        self._prejsons_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _rebuild_enabled_menu(self, *, sync_from_disk: bool = True) -> None:
        """Rebuild the enabled configs menu."""
        if sync_from_disk:
            self._sync_config_state_from_disk(update_enabled_menu=False)

        self.config_enabled_vars.clear()

        # Clean up enabled configs that no longer exist on disk
        current_configs = self.config_manager.config_names
        enabled = self.config_manager.enabled_configs
        enabled_set = set(enabled)
        for name in enabled[:]:  # Copy list to allow modification
            if name not in current_configs:
                self.config_manager.set_config_enabled(name, enabled=False)

        self.enabled_menu.set_entries(
            [
                {
                    'name': name,
                    'checked': name in enabled_set,
                }
                for name in current_configs
            ],
            minimum_width=(
                self.enabled_menu_btn.width() + _CONFIG_MENU_BUTTON_POPUP_EXTRA_WIDTH_PX
                if hasattr(self, 'enabled_menu_btn')
                else 0
            ),
        )
        self.config_enabled_vars.update(self.enabled_menu.item_widgets)

        self._update_enabled_menu_text()
        self.enabled_menu.constrain_to_button(self.enabled_menu_btn)

    def _update_enabled_menu_text(self) -> None:
        """Update the enabled menu button text."""
        enabled = self.config_manager.enabled_configs
        if not enabled:
            self.enabled_menu_btn.setText(tr('ui.gui.replacer_config.no_configs_enabled'))
        elif len(enabled) == 1:
            self.enabled_menu_btn.setText(enabled[0])
        else:
            self.enabled_menu_btn.setText(
                tr('ui.gui.replacer_config.value_configs_enabled', value0=len(enabled))
            )
        # Keep the Editing button styled to reflect whether the currently
        # selected editing profile is enabled or not.
        self._update_editing_button_style()

    def _on_config_toggle(self, name: str, checked: bool) -> None:
        """Handle config toggle."""
        self.config_manager.set_config_enabled(name, checked)
        self._update_enabled_menu_text()
        status = 'Enabled' if checked else 'Disabled'
        log_buffer.log('Config', f'{status}: {name}')
        self._update_editing_button_style()

    @staticmethod
    def _is_group(entry: _RuleEntry | None) -> TypeGuard[_GroupRule]:
        return isinstance(entry, dict) and entry.get('type') == _KIND_GROUP

    @staticmethod
    def _is_profile(entry: _RuleEntry | None) -> TypeGuard[_ProfileRule]:
        return isinstance(entry, dict) and entry.get('type') != _KIND_GROUP

    def _iter_profiles(self, entries: _RuleList) -> Iterator[_ProfileRule]:
        for entry in entries:
            if self._is_group(entry):
                yield from self._iter_profiles(entry.get('children', []))
            elif self._is_profile(entry):
                yield entry

    def _config_has_groups(self, entries: _RuleList | None = None) -> bool:
        if entries is None:
            entries = self.config_manager.replacement_rules
        for entry in entries:
            if self._is_group(entry):
                return True
            if self._config_has_groups(_children_if_present(entry)):
                return True
        return False

    def _entry_at_path(self, entries: _RuleList, path: tuple[int, ...]) -> _RuleEntry | None:
        current_entries = entries
        entry = None
        for index in path:
            if index < 0 or index >= len(current_entries):
                return None
            entry = current_entries[index]
            current_entries = entry.get('children', []) if self._is_group(entry) else []
        return entry

    def _entries_at_parent_path(
        self, entries: _RuleList, parent_path: tuple[int, ...]
    ) -> _RuleList | None:
        if not parent_path:
            return entries
        parent = self._entry_at_path(entries, parent_path)
        if not self._is_group(parent):
            return None
        return parent.setdefault('children', [])

    def _set_entry_at_path(
        self, entries: _RuleList, path: tuple[int, ...], entry: _RuleEntry
    ) -> bool:
        parent_entries = self._entries_at_parent_path(entries, path[:-1])
        if parent_entries is None or not path or path[-1] >= len(parent_entries):
            return False
        parent_entries[path[-1]] = entry
        return True

    def _remove_paths(
        self, entries: _RuleList, paths: set[tuple[int, ...]], prefix: tuple[int, ...] = ()
    ) -> _RuleList:
        kept: _RuleList = []
        for index, entry in enumerate(entries):
            path = (*prefix, index)
            if path in paths:
                continue
            kept_entry = deepcopy(entry) if self._is_group(entry) else entry
            if self._is_group(kept_entry):
                kept_entry['children'] = self._remove_paths(
                    kept_entry.get('children', []), paths, path
                )
            kept.append(kept_entry)
        return kept

    def _prune_descendant_paths(self, paths: list[tuple[int, ...]]) -> list[tuple[int, ...]]:
        result: list[tuple[int, ...]] = []
        for path in sorted(paths, key=lambda p: (len(p), p)):
            if not any(
                len(path) > len(parent) and path[: len(parent)] == parent for parent in result
            ):
                result.append(path)
        return result

    def _profile_count(self) -> int:
        return sum(1 for _ in self._iter_profiles(self.config_manager.replacement_rules))

    def _group_summary(self, group: _GroupRule) -> tuple[int, int, str, int]:
        profile_count, id_count, enabled_count = self._summarize_entries(group.get('children', []))
        if profile_count == 0 or 0 < enabled_count < profile_count:
            status = _MIXED_STATUS
        elif enabled_count == profile_count:
            status = 'On'
        else:
            status = 'Off'
        sort_enabled = 1 if profile_count > 0 and enabled_count == profile_count else 0
        return profile_count, id_count, status, sort_enabled

    def _summarize_entries(self, entries: _RuleList) -> tuple[int, int, int]:
        profile_count = 0
        id_count = 0
        enabled_count = 0
        for entry in entries:
            if self._is_group(entry):
                child_profiles, child_ids, child_enabled = self._summarize_entries(
                    entry.get('children', [])
                )
                profile_count += child_profiles
                id_count += child_ids
                enabled_count += child_enabled
            elif self._is_profile(entry):
                profile_count += 1
                id_count += len(entry.get('replace_ids', []))
                if entry.get('enabled', True):
                    enabled_count += 1
        return profile_count, id_count, enabled_count

    @staticmethod
    def _status_from_summary(profile_count: int, enabled_count: int) -> tuple[str, int]:
        if profile_count == 0 or 0 < enabled_count < profile_count:
            return _MIXED_STATUS, 0
        if enabled_count == profile_count:
            return tr('replacer.status.on'), 1 if profile_count > 0 else 0
        return tr('replacer.status.off'), 0

    def _profile_display(
        self,
        rule: _ProfileRule,
        fallback_index: int,
        path: tuple[int, ...],
        group_depth: int | None = None,
    ) -> tuple[list[str], list[int | str]]:
        name = rule.get('name') or tr('replacer.profile.default_name', index=fallback_index + 1)
        enabled = rule.get('enabled', True)
        mode = rule.get('mode', 'id')
        if 'remove' in rule and 'mode' not in rule:
            mode = 'remove' if rule.get('remove') else 'id'

        if mode == 'id':
            with_id = rule.get('with_id')
            if with_id is not None:
                action = tr('replacer.action.id')
                replace_with = str(with_id)
            else:
                action = tr('replacer.action.remove')
                replace_with = '-'
        elif mode == 'cdn':
            action = tr('replacer.action.cdn')
            cdn_url = rule.get('cdn_url', '')
            replace_with = cdn_url[:40] + '...' if len(cdn_url) > 40 else cdn_url
        elif mode == 'local':
            action = tr('replacer.action.local')
            local_path = rule.get('local_path', '')
            replace_with = Path(local_path).name if local_path else ''
        elif mode == 'remove':
            action = tr('replacer.action.remove')
            replace_with = '-'
        else:
            action = mode.upper()
            replace_with = '-'

        id_count = len(rule.get('replace_ids', []))
        if group_depth is None:
            group_depth = self._group_depth(path[:-1])
        values = [
            tr('replacer.status.on') if enabled else tr('replacer.status.off'),
            self._entry_display_name(name, group_depth),
            action,
            tr_count(id_count, 'count.id.one', 'count.id.other'),
            replace_with,
        ]
        sort_values = [
            1 if enabled else 0,
            name.lower(),
            mode,
            id_count,
            replace_with.lower(),
        ]
        return values, sort_values

    def _make_tree_item(self, entry: _RuleEntry, path: tuple[int, ...]) -> ReplacerTreeItem:
        item, _summary = self._make_tree_item_with_summary(entry, path)
        return item

    def _make_tree_item_with_summary(
        self,
        entry: _RuleEntry,
        path: tuple[int, ...],
        group_depth: int = 0,
        group_ancestors: tuple[tuple[int, ...], ...] = (),
    ) -> tuple[ReplacerTreeItem, tuple[int, int, int]]:
        if self._is_group(entry):
            child_items: list[ReplacerTreeItem] = []
            profile_count = 0
            id_count = 0
            enabled_count = 0
            current_group_depth = group_depth + 1
            child_ancestors = (*group_ancestors, path)
            if hasattr(self, '_group_depth_by_path'):
                self._group_depth_by_path[path] = current_group_depth
            for child_index, child in enumerate(entry.get('children', [])):
                child_item, child_summary = self._make_tree_item_with_summary(
                    child,
                    (*path, child_index),
                    current_group_depth,
                    child_ancestors,
                )
                child_items.append(child_item)
                child_profiles, child_ids, child_enabled = child_summary
                profile_count += child_profiles
                id_count += child_ids
                enabled_count += child_enabled
            status, sort_enabled = self._status_from_summary(profile_count, enabled_count)
            name = entry.get('name') or tr('replacer.group.label')
            item = ReplacerTreeItem(
                [
                    status,
                    self._group_display_name(name, path),
                    tr('replacer.group.label'),
                    tr_count(id_count, 'count.id.one', 'count.id.other'),
                    tr_count(
                        profile_count,
                        'count.profile.one',
                        'count.profile.other',
                    ),
                ]
            )
            item.setData(0, _ROLE_KIND, _KIND_GROUP)
            _set_tree_item_bool_data(
                item,
                _PROFILE_NAME_COLUMN,
                _ROLE_DRAW_GROUP_ICON,
                enabled=True,
            )
            item.setData(
                _PROFILE_NAME_COLUMN,
                _ROLE_GROUP_ICON_INDENT,
                _GROUP_GUIDE_STEP_PX * max(0, current_group_depth - 1),
            )
            sort_values = [sort_enabled, name.lower(), 'group', id_count, profile_count]
            flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            font = item.font(1)
            font.setBold(True)
            item.setFont(1, font)
            for column in range(5):
                item.setSizeHint(column, QSize(0, _GROUP_ROW_HEIGHT_PX))
            for child_item in child_items:
                item.addChild(child_item)
            summary = (profile_count, id_count, enabled_count)
        else:
            profile = _required_profile(entry)
            values, sort_values = self._profile_display(
                profile, path[-1] if path else 0, path, group_depth
            )
            item = ReplacerTreeItem(values)
            item.setData(0, _ROLE_KIND, _KIND_PROFILE)
            flags = item.flags() | Qt.ItemFlag.ItemIsDragEnabled
            flags &= ~Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            summary = (
                1,
                len(profile.get('replace_ids', [])),
                1 if profile.get('enabled', True) else 0,
            )

        item.setData(0, _ROLE_PATH, path)
        item.setData(0, _ROLE_GROUP_ANCESTORS, group_ancestors)
        item.setData(
            0,
            _ROLE_GROUP_DEPTH,
            group_depth + 1 if self._is_group(entry) else group_depth,
        )
        for column, sort_value in enumerate(sort_values):
            item.setData(column, _ROLE_SORT_BASE, sort_value)
        return item, summary

    def _restore_expanded_states(self, rules: _RuleList) -> None:
        def walk(item: QTreeWidgetItem) -> None:
            path = _item_path(item.data(0, _ROLE_PATH))
            if item.data(0, _ROLE_KIND) == _KIND_GROUP and path is not None:
                group = self._entry_at_path(rules, path)
                item.setExpanded(
                    bool(group.get('expanded', True)) if self._is_group(group) else True
                )
            for child_index in range(item.childCount()):
                walk(_tree_item(item.child(child_index)))

        for top_index in range(self.tree.topLevelItemCount()):
            walk(_tree_item(self.tree.topLevelItem(top_index)))

    def _set_group_expanded(self, item: QTreeWidgetItem, expanded: bool) -> None:
        if getattr(self, '_refreshing_tree', False) or item.data(0, _ROLE_KIND) != _KIND_GROUP:
            return
        path = _item_path(item.data(0, _ROLE_PATH))
        if path is None:
            return
        rules = deepcopy(self.config_manager.replacement_rules)
        group = self._entry_at_path(rules, path)
        if not self._is_group(group) or group.get('expanded') == expanded:
            return
        group['expanded'] = expanded
        self.config_manager.replacement_rules = rules

    def _refresh_tree(self) -> None:
        """Refresh the tree view."""
        sort_column = self.tree.sortColumn()
        sort_order = self.tree.header().sortIndicatorOrder()
        rules = self.config_manager.replacement_rules

        self._refreshing_tree = True
        try:
            self.tree.setSortingEnabled(False)
            self.tree.clear()
            self._group_depth_by_path: dict[tuple[int, ...], int] = {}
            has_groups = False
            for index, entry in enumerate(rules):
                item, _summary = self._make_tree_item_with_summary(entry, (index,))
                self.tree.addTopLevelItem(item)
                if self._is_group(entry):
                    has_groups = True
            self._restore_expanded_states(rules)
            self.tree.setSortingEnabled(True)
            self.tree.sortItems(sort_column, sort_order)
        finally:
            self._refreshing_tree = False
        self._tree_config_name = self.config_manager.last_config
        has_groups = has_groups or self._config_has_groups(rules)
        self.tree.setDragEnabled(has_groups)
        self.tree.setAcceptDrops(has_groups)
        self.tree.viewport().setAcceptDrops(has_groups)

    def _refresh_combo(self) -> None:
        """Refresh config controls from the current files on disk."""
        self._sync_config_state_from_disk()

    def refresh_configs_from_disk(self) -> None:
        """Refresh config controls after an external config-folder change."""
        self._sync_config_state_from_disk()

    def _sync_config_state_from_disk(self, *, update_enabled_menu: bool = True) -> bool:
        """Refresh config settings from disk and update dependent UI."""
        previous_config = self.config_manager.settings.get('last_config', 'Default')
        previous_tree_config = getattr(self, '_tree_config_name', previous_config)
        changed = self.config_manager.reconcile_configs()
        current_config = self.config_manager.last_config
        selected_config_changed = previous_config != current_config
        tree_config_changed = previous_tree_config != current_config

        self.config_menu_btn.setText(tr('ui.gui.replacer_config.value', value0=current_config))
        if update_enabled_menu and hasattr(self, 'enabled_menu'):
            self._rebuild_enabled_menu(sync_from_disk=False)
        if (changed or selected_config_changed or tree_config_changed) and hasattr(self, 'tree'):
            self.undo_manager.clear()
            self.undo_manager.save_state(self.config_manager.replacement_rules, copy_state=False)
            self._refresh_tree()
            if selected_config_changed or tree_config_changed:
                self.tree.clearSelection()
                if hasattr(self, 'name_entry'):
                    self._clear_entries()
        self._update_editing_button_style()
        return changed or selected_config_changed or tree_config_changed

    def _rebuild_editing_menu(self) -> None:
        """Rebuild the editing config menu."""
        self._sync_config_state_from_disk()
        current_configs = self.config_manager.config_names
        enabled = set(self.config_manager.enabled_configs)

        entries: list[_ConfigMenuEntry] = []
        for name in current_configs:
            # Add a small subtle red dot icon for profiles that are not enabled.
            if name not in enabled:
                icon = self._make_status_icon('#cc5555')
            else:
                # Mark enabled profiles with a subtle green dot
                icon = self._make_status_icon('#55cc66')
            entries.append({'name': name, 'icon': icon})

        self.config_menu.set_entries(
            entries,
            minimum_width=(
                self.config_menu_btn.width() + _CONFIG_MENU_BUTTON_POPUP_EXTRA_WIDTH_PX
                if hasattr(self, 'config_menu_btn')
                else 0
            ),
        )
        # Ensure the Editing button reflects the enabled state after rebuild
        self._update_editing_button_style()
        self.config_menu.constrain_to_button(self.config_menu_btn)

    def _on_config_select(self, name: str) -> None:
        """Handle config selection from menu."""
        self._sync_config_state_from_disk()
        if name not in self.config_manager.config_names:
            return
        if name != self.config_manager.last_config:
            self.config_manager.last_config = name
            # Keep a single space plus a hair-space between icon and text
            self.config_menu_btn.setText(tr('ui.gui.replacer_config.value', value0=name))
            self.undo_manager.clear()
            self.undo_manager.save_state(self.config_manager.replacement_rules, copy_state=False)
            self._refresh_tree()

        """Handle strip textures change."""
        self._update_editing_button_style()

    def _update_editing_button_style(self) -> None:
        """Color the Editing button text red if the currently edited profile
        is not enabled in the Enabled: menu.
        """
        # Guard if UI not yet created
        if not hasattr(self, 'config_menu_btn') or not hasattr(self, 'config_manager'):
            return

        name = self.config_manager.last_config
        enabled = self.config_manager.is_config_enabled(name)

        # Set the same small colored dot icon on the parent dropdown button
        color = '#55cc66' if enabled else '#cc5555'
        self.config_menu_btn.setIcon(self._make_status_icon(color))
        # Ensure button text color isn't used for state; the dot represents state now.
        self.config_menu_btn.setStyleSheet('')

    def _make_status_icon(self, color: str = '#cc5555', size: int = 12) -> QIcon:
        """Create a small circular QIcon of given color for menu actions.

        This uses native Qt QIcon/QPixmap drawing and avoids custom widget
        widgets so menu entries remain simple QActions.
        """
        pix = QPixmap(size, size)
        pix.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color))
        painter.setPen(QColor(0, 0, 0, 0))
        margin = 2
        painter.drawEllipse(margin, margin, size - margin * 2, size - margin * 2)
        painter.end()
        return QIcon(pix)

    def _browse_local_file(self) -> None:
        """Open file browser for local file selection."""
        current_val = self.replacement_entry.text().strip(' \t"\'')
        initial_dir = ''
        if current_val:
            path = resolve_local_replacement_path(current_val)
            if path.parent.exists():
                initial_dir = str(path.parent)

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.replacer_config.select_local_file'),
            initial_dir,
            tr('ui.gui.replacer_config.all_files'),
        )
        if file_path:
            self.replacement_entry.setText(local_replacement_path_for_storage(file_path))

    def _store_dropped_replacement_path(self, file_path: str) -> None:
        """Make dropped Configs assets portable while preserving external paths."""
        self.replacement_entry.setText(local_replacement_path_for_storage(file_path))

    def _config_action(self, action: str) -> None:
        """Handle config management actions."""
        current = self.config_manager.last_config

        if action == 'new':
            name, ok = QInputDialog.getText(
                self, tr('ui.gui.replacer_config.new_config'), tr('ui.gui.replacer_config.name')
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr('ui.gui.replacer_config.config_names_cannot_contain'),
                    )
                elif not self.config_manager.create_config(name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr(
                            'ui.gui.replacer_config.a_config_named_value_already_exists',
                            value0=name,
                        ),
                    )
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(
                        self.config_manager.replacement_rules, copy_state=False
                    )
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'dup':
            name, ok = QInputDialog.getText(
                self,
                tr('ui.gui.replacer_config.duplicate'),
                tr('ui.gui.replacer_config.copy_of_value', value0=current),
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr('ui.gui.replacer_config.config_names_cannot_contain'),
                    )
                elif not self.config_manager.duplicate_config(current, name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr(
                            'ui.gui.replacer_config.a_config_named_value_already_exists',
                            value0=name,
                        ),
                    )
                else:
                    self.config_manager.last_config = name
                    self.undo_manager.clear()
                    self.undo_manager.save_state(
                        self.config_manager.replacement_rules, copy_state=False
                    )
                    self._refresh_combo()
                    self._refresh_tree()

        elif action == 'rename':
            name, ok = QInputDialog.getText(
                self,
                tr('ui.gui.replacer_config.rename'),
                tr('ui.gui.replacer_config.new_name'),
                text=current,
            )
            if ok and name:
                name = name.strip()
                if not self.config_manager.is_valid_config_name(name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr('ui.gui.replacer_config.config_names_cannot_contain'),
                    )
                elif name != current and not self.config_manager.rename_config(current, name):
                    QMessageBox.warning(
                        self,
                        tr('ui.gui.replacer_config.invalid_name'),
                        tr(
                            'ui.gui.replacer_config.a_config_named_value_already_exists',
                            value0=name,
                        ),
                    )
                elif name != current:
                    self._refresh_combo()

        elif action == 'delete':
            if len(self.config_manager.config_names) <= 1:
                QMessageBox.critical(
                    self,
                    tr('ui.gui.replacer_config.error'),
                    tr('ui.gui.replacer_config.cannot_delete_last_config'),
                )
            else:
                reply = QMessageBox.question(
                    self,
                    tr('ui.gui.replacer_config.delete'),
                    tr('ui.gui.replacer_config.delete_value', value0=current),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    self.config_manager.delete_config(current)
                    self.undo_manager.clear()
                    self.undo_manager.save_state(
                        self.config_manager.replacement_rules, copy_state=False
                    )
                    self._refresh_combo()
                    self._refresh_tree()

    def _save_with_undo(self, rules: _RuleList) -> None:
        """Save rules with undo tracking."""
        self.undo_manager.save_state(rules, copy_state=False)
        self.config_manager.replacement_rules = rules

    def _do_undo(self) -> None:
        """Perform undo."""
        if prev := self.undo_manager.undo():
            self.config_manager.replacement_rules = prev
            self._refresh_tree()
            log_buffer.log('Config', 'Undo performed')

    def _do_redo(self) -> None:
        """Perform redo."""
        if next_state := self.undo_manager.redo():
            self.config_manager.replacement_rules = next_state
            self._refresh_tree()
            log_buffer.log('Config', 'Redo performed')

    def _show_context_menu(self, pos: QPoint) -> None:
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
                menu.addAction(tr('ui.gui.replacer_config.enable_selected'), self._enable_selected)
                menu.addAction(
                    tr('ui.gui.replacer_config.disable_selected'), self._disable_selected
                )
                if len(selected_profile_paths) == len(selected_items) and self._paths_share_parent(
                    selected_profile_paths
                ):
                    menu.addSeparator()
                    menu.addAction(
                        tr('ui.gui.replacer_config.create_group'), self._create_group_from_selected
                    )
            menu.addSeparator()
            menu.addAction(tr('ui.gui.replacer_config.delete_selected'), self._delete_selected)
        else:
            # Single item operations
            path = _item_path(item.data(0, _ROLE_PATH))
            if path is None:
                return
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_group(entry):
                menu.addAction(
                    tr('ui.gui.replacer_config.enable_group'),
                    lambda: self._set_group_profiles_enabled(path, enabled=True),
                )
                menu.addAction(
                    tr('ui.gui.replacer_config.disable_group'),
                    lambda: self._set_group_profiles_enabled(path, enabled=False),
                )
                menu.addSeparator()
                menu.addAction(
                    tr('ui.gui.replacer_config.rename_group'), lambda: self._rename_group(path)
                )
                menu.addSeparator()
                menu.addAction(
                    tr('ui.gui.replacer_config.delete_group'),
                    self._delete_selected,
                )
            elif self._is_profile(entry):
                enabled = entry.get('enabled', True)
                text = tr('replacer.profile.disable') if enabled else tr('replacer.profile.enable')
                menu.addAction(text, lambda: self._toggle_profile(path))
                menu.addAction(
                    tr('ui.gui.replacer_config.rename_profile'), lambda: self._rename_profile(path)
                )
                menu.addAction(
                    tr('ui.gui.replacer_config.edit_asset_ids'), lambda: self._edit_asset_ids(path)
                )
                menu.addAction(
                    tr('ui.gui.replacer_config.edit_replacement'),
                    lambda: self._edit_replacement(path),
                )
                menu.addSeparator()
                menu.addAction(
                    tr('ui.gui.replacer_config.create_group'), self._create_group_from_selected
                )
                menu.addSeparator()
                menu.addAction(
                    tr('ui.gui.replacer_config.delete_profile'),
                    self._delete_selected,
                )

        if menu.actions():
            menu.exec(self.tree.mapToGlobal(pos))

    def _selected_entry_paths(self) -> list[tuple[int, ...]]:
        paths: list[tuple[int, ...]] = []
        for item in self.tree.selectedItems():
            path = _item_path(item.data(0, _ROLE_PATH))
            if path is not None:
                paths.append(path)
        return paths

    def _selected_profile_paths(self) -> list[tuple[int, ...]]:
        profile_paths: list[tuple[int, ...]] = []
        for path in self._selected_entry_paths():
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_profile(entry):
                profile_paths.append(path)
        return sorted(profile_paths)

    def selected_movable_paths(self) -> list[tuple[int, ...]]:
        return self._selected_movable_paths()

    def _selected_movable_paths(self) -> list[tuple[int, ...]]:
        paths: list[tuple[int, ...]] = []
        for path in self._selected_entry_paths():
            entry = self._entry_at_path(self.config_manager.replacement_rules, path)
            if self._is_profile(entry) or self._is_group(entry):
                paths.append(path)
        return self._prune_descendant_paths(paths)

    @staticmethod
    def _paths_share_parent(paths: list[tuple[int, ...]]) -> bool:
        return bool(paths) and len({path[:-1] for path in paths}) == 1

    def _toggle_profile(self, path: tuple[int, ...]) -> None:
        """Toggle profile enabled state."""
        rules = deepcopy(self.config_manager.replacement_rules)
        rule = self._entry_at_path(rules, path)
        if self._is_profile(rule):
            rule['enabled'] = not rule.get('enabled', True)
            self._save_with_undo(rules)
            self._refresh_tree()

    def _rename_profile(self, path: tuple[int, ...]) -> None:
        """Rename a profile."""
        rules = self.config_manager.replacement_rules
        rule = self._entry_at_path(rules, path)
        if not self._is_profile(rule):
            return
        old_name = rule.get('name', f'Profile {path[-1] + 1}')
        name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.replacer_config.rename'),
            tr('ui.gui.replacer_config.new_name'),
            text=old_name,
        )
        if ok and name and name.strip():
            rules_copy = deepcopy(rules)
            rule_copy = self._entry_at_path(rules_copy, path)
            if not self._is_profile(rule_copy):
                return
            rule_copy['name'] = name.strip()
            self._save_with_undo(rules_copy)
            self._refresh_tree()

    def _rename_group(self, path: tuple[int, ...]) -> None:
        """Rename a group."""
        rules = self.config_manager.replacement_rules
        group = self._entry_at_path(rules, path)
        if not self._is_group(group):
            return
        old_name = group.get('name', 'Group')
        name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.replacer_config.rename'),
            tr('ui.gui.replacer_config.new_name'),
            text=old_name,
        )
        if ok and name and name.strip():
            rules_copy = deepcopy(rules)
            group_copy = self._entry_at_path(rules_copy, path)
            if not self._is_group(group_copy):
                return
            group_copy['name'] = name.strip()
            self._save_with_undo(rules_copy)
            self._refresh_tree()

    def _set_group_profiles_enabled(self, path: tuple[int, ...], enabled: bool) -> None:
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
            log_buffer.log(
                'Config',
                f'{action} {format_count(changed, "profile")} in group: {group.get("name", "Group")}',
            )

    def _create_group_from_selected(self) -> None:
        """Create a group from the currently selected profile rows."""
        paths = self._selected_profile_paths()
        if not paths:
            return
        if len(paths) != len(self.tree.selectedItems()):
            QMessageBox.information(
                self,
                tr('ui.gui.replacer_config.create_group'),
                tr('ui.gui.replacer_config.select_only_profiles_to_create_a_group'),
            )
            return
        if not self._paths_share_parent(paths):
            QMessageBox.information(
                self,
                tr('ui.gui.replacer_config.create_group'),
                tr('ui.gui.replacer_config.select_profiles_in_the_same_group_or'),
            )
            return

        name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.replacer_config.rename'),
            tr('ui.gui.replacer_config.new_name'),
            text=tr('replacer.group.new_default_name'),
        )
        if not ok or not name or not name.strip():
            return

        rules = deepcopy(self.config_manager.replacement_rules)
        parent_path = paths[0][:-1]
        parent_entries = self._entries_at_parent_path(rules, parent_path)
        if parent_entries is None:
            return

        selected_indices = sorted(path[-1] for path in paths)
        children = [
            deepcopy(parent_entries[index])
            for index in selected_indices
            if index < len(parent_entries)
        ]
        if not children:
            return

        for index in reversed(selected_indices):
            if index < len(parent_entries):
                parent_entries.pop(index)
        insert_at = selected_indices[0]
        parent_entries.insert(
            insert_at,
            {
                'type': _KIND_GROUP,
                'name': name.strip(),
                'expanded': True,
                'children': children,
            },
        )

        self._save_with_undo(rules)
        self._refresh_tree()
        log_buffer.log(
            'Config',
            f'Created group: {name.strip()} ({format_count(children, "profile")})',
        )

    def _edit_asset_ids(self, path: tuple[int, ...]) -> None:
        """Edit asset IDs for a profile."""
        rules = self.config_manager.replacement_rules
        rule = self._entry_at_path(rules, path)
        if not self._is_profile(rule):
            return

        name = rule.get('name') or tr('replacer.profile.default_name', index=path[-1] + 1)
        ids = rule.get('replace_ids', [])

        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.gui.replacer_config.asset_ids_value', value0=name))
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(tr('ui.gui.replacer_config.profile_value', value0=name))
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        count_label = QLabel(
            tr(
                'ui.gui.replacer_config.total_value',
                value0=tr_count(ids, 'count.asset_id.one', 'count.asset_id.other'),
            )
        )
        layout.addWidget(count_label)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlainText('\n'.join(str(i) for i in ids))
        layout.addWidget(text_edit)

        def save_ids() -> None:
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
            count_label.setText(
                tr(
                    'ui.gui.replacer_config.total_value',
                    value0=tr_count(
                        new_ids,
                        'count.asset_id.one',
                        'count.asset_id.other',
                    ),
                )
            )

        def copy_all() -> None:
            QApplication.clipboard().setText(', '.join(str(i) for i in ids))

        btn_layout = QHBoxLayout()
        copy_btn = QPushButton(tr('ui.gui.replacer_config.copy_all'))
        copy_btn.clicked.connect(copy_all)
        btn_layout.addWidget(copy_btn)

        save_btn = QPushButton(tr('ui.gui.replacer_config.save_and_close'))
        save_btn.clicked.connect(lambda: (save_ids(), dialog.accept()))
        btn_layout.addWidget(save_btn)

        # Add Asset Types menu to Edit Asset IDs
        types_btn = QPushButton(tr('ui.gui.replacer_config.asset_types'))

        def show_dialog_types_popup() -> None:
            def on_filters_changed(filters: set[int | str]) -> None:
                cache_manager_module = importlib.import_module('fleasion.cache.cache_manager')
                curr_content = text_edit.toPlainText().strip()
                curr_ids = self._parse_ids(curr_content.replace('\n', ','))
                new_ids: list[int | str] = []
                for item in curr_ids:
                    if isinstance(item, int):
                        new_ids.append(item)
                    else:
                        is_mapped = False
                        if item in virtual_anim_types:
                            is_mapped = True
                        else:
                            for name in cache_manager_module.CacheManager.ASSET_TYPES.values():
                                if name.lower() == item.lower():
                                    is_mapped = True
                                    break
                        if not is_mapped:
                            new_ids.append(item)
                for tid in filters:
                    if tid in virtual_anim_types:
                        new_ids.append(tid)
                    elif tid in cache_manager_module.CacheManager.ASSET_TYPES:
                        new_ids.append(cache_manager_module.CacheManager.ASSET_TYPES[tid])

                if new_ids:
                    text_edit.setPlainText('\n'.join(str(i) for i in new_ids))
                else:
                    text_edit.setPlainText('')

            asset_type_filter = importlib.import_module('fleasion.cache.asset_type_filter')
            cache_manager_module = importlib.import_module('fleasion.cache.cache_manager')

            if time.monotonic() - self._dialog_asset_types_popup_last_closed < 0.25:
                return

            virtual_anim_types = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

            content = text_edit.toPlainText().strip()
            current_ids = self._parse_ids(content.replace('\n', ','))

            active_filters: set[int | str] = set()
            for item in current_ids:
                if isinstance(item, str):
                    if item in virtual_anim_types:
                        active_filters.add(item)
                        continue
                    for tid, name in cache_manager_module.CacheManager.ASSET_TYPES.items():
                        if name.lower() == item.lower():
                            active_filters.add(tid)
                            break

            popup = getattr(self, '_dialog_asset_types_popup', None)
            if popup is not None:
                try:
                    if popup.isVisible():
                        popup.close()
                        self._dialog_asset_types_popup_last_closed = time.monotonic()
                        return
                except RuntimeError:
                    popup = None

            if popup is None:
                popup = asset_type_filter.CategoryFilterPopup(
                    parent=dialog, active_filters=active_filters
                )
                popup.filters_changed.connect(on_filters_changed)
                popup.aboutToHide.connect(self._mark_dialog_asset_types_popup_closed)
                self._dialog_asset_types_popup = popup
            else:
                popup.set_active_filters(active_filters)

            # Line up TOP LEFT of our popup menu with TOP LEFT of the Asset Types button
            pos = types_btn.mapToGlobal(types_btn.rect().topLeft())
            screen = _screen_at(pos) or _optional_screen(QApplication.primaryScreen())
            if screen is not None:
                popup.constrain_to_available_geometry(screen.availableGeometry(), pos.y())
            popup.popup(pos)

        types_btn.clicked.connect(show_dialog_types_popup)
        btn_layout.addWidget(types_btn)

        cancel_btn = QPushButton(tr('ui.gui.replacer_config.cancel'))
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)
        dialog.show()

    def _edit_replacement(self, path: tuple[int, ...]) -> None:
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
        dialog.setWindowTitle(tr('ui.gui.replacer_config.edit_replacement'))
        dialog.resize(400, 100)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()
        label = QLabel(tr('ui.gui.replacer_config.replacement_id_url_file_path_or_empty'))
        layout.addWidget(label)

        line_edit = FileDropLineEdit()
        line_edit.setText(old_value)
        line_edit.setToolTip(_replacement_path_tooltip())

        def _store_dropped_path(file_path: str) -> None:
            line_edit.setText(local_replacement_path_for_storage(file_path))

        line_edit.fileDropped.connect(_store_dropped_path)
        layout.addWidget(line_edit)

        btn_layout = QHBoxLayout()

        browse_btn = QPushButton(tr('ui.gui.replacer_config.browse'))
        _ensure_text_width(browse_btn, 80)
        browse_btn.setAutoDefault(False)

        def _on_browse() -> None:
            current_val = line_edit.text().strip(' \t"\'')
            initial_dir = ''
            if current_val:
                path = resolve_local_replacement_path(current_val)
                if path.parent.exists():
                    initial_dir = str(path.parent)

            path, _ = QFileDialog.getOpenFileName(
                dialog,
                tr('ui.gui.replacer_config.select_local_file'),
                initial_dir,
                tr('ui.gui.replacer_config.all_files'),
            )
            if path:
                line_edit.setText(local_replacement_path_for_storage(path))
                dialog.accept()

        browse_btn.clicked.connect(_on_browse)
        btn_layout.addWidget(browse_btn)

        btn_layout.addStretch()

        ok_btn = QPushButton(tr('ui.gui.replacer_config.ok'))
        _ensure_text_width(ok_btn, 80)
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(dialog.accept)
        btn_layout.addWidget(ok_btn)

        cancel_btn = QPushButton(tr('ui.gui.replacer_config.cancel'))
        _ensure_text_width(cancel_btn, 80)
        cancel_btn.clicked.connect(dialog.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)
        dialog.setLayout(layout)

        if not dialog.exec():
            return

        new_value = line_edit.text().strip(' \t"\'')
        new_mode, extra = self._detect_mode(new_value)

        if '_raw' in extra:
            QMessageBox.critical(
                self,
                tr('ui.gui.replacer_config.error'),
                tr('ui.gui.replacer_config.invalid_replacement_value', value0=extra['_raw']),
            )
            return

        if (
            new_mode == 'local'
            and 'local_path' in extra
            and not resolve_local_replacement_path(extra['local_path']).is_file()
        ):
            QMessageBox.critical(
                self,
                tr('ui.gui.replacer_config.error'),
                tr('ui.gui.replacer_config.file_not_found_value', value0=extra['local_path']),
            )
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
        if 'with_id' in extra:
            rule_copy['with_id'] = extra['with_id']
        if 'cdn_url' in extra:
            rule_copy['cdn_url'] = extra['cdn_url']
        if 'local_path' in extra:
            rule_copy['local_path'] = extra['local_path']
        self._save_with_undo(rules_copy)
        self._refresh_tree()

    def _parse_ids(self, text: str) -> list[int | str]:
        """Parse IDs from text."""
        ids: list[int | str] = []
        for raw_part in _ID_SPLIT_RE.split(text):
            part = raw_part.strip()
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

    def _show_asset_types_popup(self) -> None:
        """Show the asset types popup menu."""
        cache_manager_module = importlib.import_module('fleasion.cache.cache_manager')
        if time.monotonic() - self._asset_types_popup_last_closed < 0.25:
            return

        popup = self.asset_types_popup
        if popup.isVisible():
            popup.close()
            self._asset_types_popup_last_closed = time.monotonic()
            return

        virtual_anim_types = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        # Parse current text to update active filters
        current_text = self.replace_entry.text()
        current_ids = self._parse_ids(current_text)

        active_filters: set[int | str] = set()
        for item in current_ids:
            if isinstance(item, str):
                if item in virtual_anim_types:
                    active_filters.add(item)
                    continue
                # Reverse lookup the asset type integer from name
                for tid, name in cache_manager_module.CacheManager.ASSET_TYPES.items():
                    if name.lower() == item.lower():
                        active_filters.add(tid)
                        break

        # Reuse the existing popup instance and update its current selection.
        popup = self.asset_types_popup
        popup.set_active_filters(active_filters)

        global_top_right = self.asset_types_btn.mapToGlobal(self.asset_types_btn.rect().topRight())
        screen = _screen_at(global_top_right) or _optional_screen(QApplication.primaryScreen())
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

    def _mark_asset_types_popup_closed(self) -> None:
        """Remember that the Asset Types popup just closed to debounce reopen clicks."""
        self._asset_types_popup_last_closed = time.monotonic()

    def _mark_dialog_asset_types_popup_closed(self) -> None:
        """Remember that the edit dialog Asset Types popup just closed."""
        self._dialog_asset_types_popup_last_closed = time.monotonic()

    def _on_asset_types_changed(self, filters: set[int | str]) -> None:
        """Handle asset types selection change."""
        cache_manager_module = importlib.import_module('fleasion.cache.cache_manager')
        virtual_anim_types = {'R6Animation', 'R15Animation', 'NonPlayerAnimation'}

        current_text = self.replace_entry.text().strip()
        current_ids = self._parse_ids(current_text)

        new_ids: list[int | str] = []
        for item in current_ids:
            if isinstance(item, int):
                new_ids.append(item)
            else:
                is_mapped = False
                for name in cache_manager_module.CacheManager.ASSET_TYPES.values():
                    if name.lower() == item.lower():
                        is_mapped = True
                        break
                # Also treat virtual anim type strings as mapped (so they get removed/re-added cleanly)
                if item in virtual_anim_types:
                    is_mapped = True
                if not is_mapped:
                    new_ids.append(item)

        # Add the string representations of the selected filters
        for tid in filters:
            if tid in virtual_anim_types:
                new_ids.append(tid)
            elif tid in cache_manager_module.CacheManager.ASSET_TYPES:
                new_ids.append(cache_manager_module.CacheManager.ASSET_TYPES[tid])

        if new_ids:
            self.replace_entry.setText(', '.join(str(i) for i in new_ids).strip(', '))
        else:
            self.replace_entry.setText('')

    def _clear_entries(self) -> None:
        """Clear input fields."""
        self.name_entry.clear()
        self.replace_entry.clear()
        self.replacement_entry.clear()

    def _detect_mode(self, value: str) -> tuple[str, _ModeFields]:
        """Auto-detect mode from replacement value.

        Returns tuple of (mode, extra_fields).
        """
        value = value.strip().strip('"\'')

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

    def _get_rule_from_entries(self) -> _ProfileRule | None:
        """Get rule from input fields."""
        ids = self._parse_ids(self.replace_entry.text())
        if not ids:
            QMessageBox.critical(
                self,
                tr('ui.gui.replacer_config.error'),
                tr('ui.gui.replacer_config.enter_at_least_one_asset_id'),
            )
            return None

        replacement = self.replacement_entry.text().strip()
        mode, extra = self._detect_mode(replacement)

        rule: _ProfileRule = {
            'name': self.name_entry.text().strip() or f'Profile {self._profile_count() + 1}',
            'replace_ids': ids,
            'mode': mode,
            'enabled': True,
        }

        if mode == 'id':
            if 'with_id' in extra:
                rule['with_id'] = extra['with_id']
            elif '_raw' in extra:
                # Failed to parse as ID
                QMessageBox.critical(
                    self,
                    tr('ui.gui.replacer_config.error'),
                    tr(
                        'ui.gui.replacer_config.invalid_replacement_value_must_be_an_asset',
                        value0=extra['_raw'],
                    ),
                )
                return None
            # Empty = remove (no with_id)
        elif mode == 'cdn':
            cdn_url = _required_mode_str(extra, 'cdn_url')
            # Validate URL is accessible. Unexpected validation/UI errors remain non-fatal.
            try:
                with _BestEffortExceptionGuard(propagate=(URLError,)):
                    status = http_head_status(
                        cdn_url,
                        timeout=5,
                        headers={'User-Agent': 'Mozilla/5.0'},
                    )
                    if status >= 400:
                        QMessageBox.warning(
                            self,
                            tr('ui.gui.replacer_config.warning'),
                            tr(
                                'ui.gui.replacer_config.cdn_url_returned_status_value_adding_anyway',
                                value0=status,
                            ),
                        )
            except URLError as e:
                reply = QMessageBox.question(
                    self,
                    tr('ui.gui.replacer_config.url_check_failed'),
                    tr('ui.gui.replacer_config.could_not_verify_cdn_url_value_add', value0=e),
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return None
            rule['cdn_url'] = cdn_url
        elif mode == 'local':
            local_path = _required_mode_str(extra, 'local_path')
            if not resolve_local_replacement_path(local_path).is_file():
                QMessageBox.critical(
                    self,
                    tr('ui.gui.replacer_config.error'),
                    tr('ui.gui.replacer_config.file_not_found_value', value0=local_path),
                )
                return None
            rule['local_path'] = local_path

        return rule

    def _add_rule(self) -> None:
        """Add a new rule."""
        if rule := self._get_rule_from_entries():
            rules = deepcopy(self.config_manager.replacement_rules)
            rules.append(rule)
            self._save_with_undo(rules)
            self._refresh_tree()
            self._clear_entries()
            mode = rule.get('mode', 'id').upper()
            log_buffer.log('Config', f'Added profile: {_required_profile_name(rule)} ({mode})')

    def _load_selected(self) -> None:
        """Load selected rule into input fields."""
        items = self.tree.selectedItems()
        if not items:
            return

        path = _item_path(items[0].data(0, _ROLE_PATH))
        rule = (
            self._entry_at_path(self.config_manager.replacement_rules, path)
            if path is not None
            else None
        )
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

    def _update_selected(self) -> None:
        """Update selected rule."""
        items = self.tree.selectedItems()
        if not items:
            return

        if rule := self._get_rule_from_entries():
            path = _item_path(items[0].data(0, _ROLE_PATH))
            if path is None:
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

    def _delete_selected(self) -> None:
        """Delete selected profiles or groups."""
        paths = self._prune_descendant_paths(self._selected_entry_paths())
        if not paths:
            return

        current_rules = self.config_manager.replacement_rules
        has_group = any(self._is_group(self._entry_at_path(current_rules, path)) for path in paths)
        if has_group:
            reply = QMessageBox.question(
                self,
                tr('ui.gui.replacer_config.delete_group'),
                tr('ui.gui.replacer_config.delete_selected_groups_and_all_nested_contents'),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        deleted_names: list[str] = []
        for path in paths:
            entry = self._entry_at_path(current_rules, path)
            if isinstance(entry, dict):
                deleted_names.append(
                    entry.get(
                        'name',
                        'Group' if self._is_group(entry) else f'Profile {path[-1] + 1}',
                    )
                )

        rules = self._remove_paths(deepcopy(current_rules), set(paths))

        if deleted_names:
            self._save_with_undo(rules)
            self._refresh_tree()
            log_buffer.log(
                'Config',
                f'Deleted {format_count(deleted_names, "item")}: {", ".join(deleted_names)}',
            )

    def _enable_selected(self) -> None:
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

    def _disable_selected(self) -> None:
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

    def _iter_tree_items(self) -> Iterator[QTreeWidgetItem]:
        def walk(item: QTreeWidgetItem) -> Iterator[QTreeWidgetItem]:
            yield item
            for child_index in range(item.childCount()):
                yield from walk(_tree_item(item.child(child_index)))

        for top_index in range(self.tree.topLevelItemCount()):
            yield from walk(_tree_item(self.tree.topLevelItem(top_index)))

    def _item_for_path(self, path: tuple[int, ...]) -> QTreeWidgetItem | None:
        for item in self._iter_tree_items():
            if item.data(0, _ROLE_PATH) == path:
                return item
        return None

    @staticmethod
    def _is_descendant_path(path: tuple[int, ...], parent: tuple[int, ...]) -> bool:
        return len(path) > len(parent) and path[: len(parent)] == parent

    def _group_depth(self, path: tuple[int, ...], rules: _RuleList | None = None) -> int:
        cached_depth = getattr(self, '_group_depth_by_path', {}).get(path)
        if cached_depth is not None:
            return cached_depth
        if rules is None:
            rules = self.config_manager.replacement_rules
        depth = 0
        for index in range(1, len(path) + 1):
            entry = self._entry_at_path(rules, path[:index])
            if self._is_group(entry):
                depth += 1
        return depth

    def _entry_display_name(self, name: str, group_depth: int | tuple[int, ...]) -> str:
        if isinstance(group_depth, tuple):
            group_depth = self._group_depth(group_depth[:-1])
        indent = ' ' * (_GROUP_CONTENT_INDENT_SPACES * max(0, group_depth))
        return f'{indent}{name}'

    def _group_display_name(self, name: str, _path: tuple[int, ...]) -> str:
        return name

    def _group_guide_x(self, group_path: tuple[int, ...]) -> int:
        name_left = self.tree.columnViewportPosition(_PROFILE_NAME_COLUMN)
        group_depth = getattr(self, '_group_depth_by_path', {}).get(group_path)
        if group_depth is None:
            group_depth = self._group_depth(group_path)
        depth_offset = max(0, group_depth - 1) * _GROUP_GUIDE_STEP_PX
        return name_left + _GROUP_GUIDE_GUTTER_PX + depth_offset + 1

    def paint_group_guides(self, viewport: QWidget) -> None:
        self._paint_group_guides(viewport)

    def _paint_group_guides(self, viewport: QWidget) -> None:
        if not hasattr(self, 'tree'):
            return
        if not getattr(self, '_group_depth_by_path', None) and not self._config_has_groups():
            return

        palette = self.tree.palette()
        is_dark = palette.window().color().lightness() < 128
        guide_color = QColor('#5f6368' if is_dark else '#c4c7c5')
        selected_color = guide_color.lighter(175 if is_dark else 115)

        painter = QPainter(viewport)
        _set_render_hint(painter, QPainter.RenderHint.Antialiasing, enabled=False)

        selected_group_paths: set[tuple[int, ...]] = set()
        for path in self._selected_entry_paths():
            item = self._item_for_path(path)
            if item is not None and item.data(0, _ROLE_KIND) == _KIND_GROUP:
                selected_group_paths.add(path)
                continue
            ancestors = item.data(0, _ROLE_GROUP_ANCESTORS) if item is not None else ()
            if ancestors:
                selected_group_paths.add(ancestors[-1])

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

            ancestors = item.data(0, _ROLE_GROUP_ANCESTORS) or ()
            for ancestor_path in ancestors:
                span = visible_spans.get(ancestor_path)
                if span is None:
                    visible_spans[ancestor_path] = (rect.top(), rect.bottom())
                else:
                    visible_spans[ancestor_path] = (
                        min(span[0], rect.top()),
                        max(span[1], rect.bottom()),
                    )

        for group_path, (top, bottom) in visible_spans.items():
            painter.setPen(selected_pen if group_path in selected_group_paths else guide_pen)
            x = self._group_guide_x(group_path)
            painter.drawLine(x, top, x, bottom)

        painter.end()

    def set_drag_hint_active(self, active: bool) -> None:
        self._set_drag_hint_active(active)

    def _set_drag_hint_active(self, active: bool) -> None:
        """Highlight valid group/root drop targets while dragging profiles."""
        if not hasattr(self, 'tree'):
            return
        items = list(self._iter_tree_items())
        for item in items:
            for column in range(self.tree.columnCount()):
                item.setBackground(column, QBrush())

        highlight = self.palette().highlight().color()
        if active:
            brushes: dict[tuple[int, ...], QBrush] = {}
            for item in items:
                path = _item_path(item.data(0, _ROLE_PATH))
                if path is None:
                    continue
                if item.data(0, _ROLE_KIND) == _KIND_GROUP:
                    group_path = path
                else:
                    ancestors = _group_ancestors(item.data(0, _ROLE_GROUP_ANCESTORS))
                    if not ancestors:
                        continue
                    group_path = ancestors[-1]
                brush = brushes.get(group_path)
                if brush is None:
                    depth = _depth_map(getattr(self, '_group_depth_by_path', {})).get(
                        group_path, self._group_depth(group_path)
                    )
                    color = QColor(_DRAG_GROUP_COLORS[(depth - 1) % len(_DRAG_GROUP_COLORS)])
                    color.setAlpha(58)
                    brush = QBrush(color)
                    brushes[group_path] = brush
                for column in range(self.tree.columnCount()):
                    item.setBackground(column, brush)

        if active:
            self.tree.setStyleSheet(f'QTreeWidget {{ border: 1px solid {highlight.name()}; }}')
        else:
            self.tree.setStyleSheet('')

    def _drop_plan(
        self,
        target: QTreeWidgetItem | None,
        drop_position: QAbstractItemView.DropIndicatorPosition,
    ) -> _DropPlan | None:
        selected_paths = self.selected_movable_paths()
        if not selected_paths or not self._config_has_groups():
            return None

        on_viewport = QAbstractItemView.DropIndicatorPosition.OnViewport
        on_item = QAbstractItemView.DropIndicatorPosition.OnItem
        above_item = QAbstractItemView.DropIndicatorPosition.AboveItem
        below_item = QAbstractItemView.DropIndicatorPosition.BelowItem
        plan: _DropPlan | None = None

        if target is None or drop_position == on_viewport:
            plan = ('insert', (), None)
        else:
            target_path = _item_path(target.data(0, _ROLE_PATH))
            valid_target = (
                target_path is not None
                and target_path not in selected_paths
                and not any(self._is_descendant_path(target_path, path) for path in selected_paths)
            )
            if valid_target and target_path is not None:
                if drop_position == on_item:
                    parent_path = (
                        target_path
                        if target.data(0, _ROLE_KIND) == _KIND_GROUP
                        else target_path[:-1]
                    )
                    plan = ('insert', parent_path, None)
                elif drop_position in {above_item, below_item}:
                    insert_at = target_path[-1] + (1 if drop_position == below_item else 0)
                    plan = ('insert', target_path[:-1], insert_at)

        return plan

    def is_valid_item_drop(
        self,
        target: QTreeWidgetItem | None,
        drop_position: QAbstractItemView.DropIndicatorPosition,
    ) -> bool:
        return self._is_valid_item_drop(target, drop_position)

    def _is_valid_item_drop(
        self,
        target: QTreeWidgetItem | None,
        drop_position: QAbstractItemView.DropIndicatorPosition,
    ) -> bool:
        return self._drop_plan(target, drop_position) is not None

    @staticmethod
    def _adjust_path_after_removals(
        path: tuple[int, ...], removed_paths: list[tuple[int, ...]]
    ) -> tuple[int, ...]:
        adjusted: list[int] = []
        for depth, index in enumerate(path):
            parent = path[:depth]
            removed_before = sum(
                1
                for removed in removed_paths
                if len(removed) == depth + 1
                and removed[:depth] == parent
                and removed[depth] < index
            )
            adjusted.append(index - removed_before)
        return tuple(adjusted)

    def move_selected_items_to_drop(
        self,
        target: QTreeWidgetItem | None,
        drop_position: QAbstractItemView.DropIndicatorPosition,
    ) -> bool:
        return self._move_selected_items_to_drop(target, drop_position)

    def _move_selected_items_to_drop(
        self,
        target: QTreeWidgetItem | None,
        drop_position: QAbstractItemView.DropIndicatorPosition,
    ) -> bool:
        plan = self._drop_plan(target, drop_position)
        if plan is None:
            return False

        selected_paths = self.selected_movable_paths()
        current_rules = self.config_manager.replacement_rules
        moving_entries: _RuleList = []
        for path in selected_paths:
            entry = self._entry_at_path(current_rules, path)
            if entry is not None:
                moving_entries.append(deepcopy(_required_entry(entry)))
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
                and path[: len(target_parent_path)] == target_parent_path
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
