"""Subplace Joiner tab - browse and join subplaces of any Roblox experience."""

from __future__ import annotations

import json
import secrets
import sys
import threading
import time
import traceback
import uuid
from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, cast, override
from urllib.parse import quote, urlparse

import requests
from dateutil import parser as _dateutil_parser
from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QImage, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr, tr_count
from fleasion.utils.logging import log_buffer
from fleasion.utils.paths import CONFIG_DIR, PROXY_CA_DIR
from fleasion.utils.roblox_auth import (
    get_roblosecurity as _get_roblosecurity,
    wait_for_roblosecurity as _wait_for_roblosecurity,
)
from fleasion.utils.windows import launch_as_standard_user

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from PySide6.QtCore import QEvent, QPoint
    from PySide6.QtGui import QAction, QEnterEvent, QMouseEvent, QResizeEvent, QShowEvent
    from PySide6.QtWidgets import QLayoutItem


type _MainCallback = Callable[[], object]
type _PlaceNameCallback = Callable[[str], object]
type _PlaceIdCallback = Callable[[str], object]
type _ButtonCallback = Callable[[bool], object]
type _CardItem = (
    tuple[str, str | None, str | None]
    | tuple[str, str | None, str | None, int | str | None]
    | tuple[str, str | None, str | None, int | str | None, int | str | None]
)
type _CardUpdate = tuple[str, str | None, str | None]


class _ServerInfo(TypedDict, total=False):
    id: str
    playing: int
    maxPlayers: int
    ping: int | None


class _RawPlaceRecord(TypedDict, total=False):
    id: int | str
    name: str
    display_name: str
    created: str | None
    updated: str | None
    is_root: bool


class _PlaceRecord(TypedDict):
    id: int | str
    display_name: str
    created: str | None
    updated: str | None
    is_root: bool
    name: NotRequired[str]


class _ThumbnailEntry(TypedDict, total=False):
    targetId: int | str
    imageUrl: str


class _UniverseInfo(TypedDict, total=False):
    universeId: int


class _GameInfo(TypedDict, total=False):
    rootPlaceId: int | str
    name: str


class _RandoTab(Protocol):
    _account_switched: bool

    def is_multi_instance_enabled(self) -> bool: ...

    def close_singleton_event(self) -> None: ...


class _ConfigManager(Protocol):
    proxy_mode: str
    proxy_features_enabled: bool


class _ProxyMaster(Protocol):
    def roblox_env_proxy_url(self) -> str: ...

    def hosts_intercepts_host(self, host: str) -> bool: ...


class _FlowHeaders(Protocol):
    def get(self, key: str, default: str = '') -> str: ...


class _FlowRequest(Protocol):
    pretty_url: str
    headers: _FlowHeaders
    content: bytes
    raw_content: bytes
    url: str


class _FlowResponse(Protocol):
    def json(self) -> dict[str, object]: ...


class _ProxyFlow(Protocol):
    request: _FlowRequest
    response: _FlowResponse | None


_DEFAULT_THUMB_URL = (
    'https://static.wikia.nocookie.net/roblox/images/5/54/Default_Thumbnail_1_updated.png'
    '/revision/latest/scale-to-width-down/1000?cb=20250523160858'
)
_SETTINGS_FILE = 'subplace_joiner_settings.json'
_LEGACY_SETTINGS_FILE = 'settings.json'
_default_thumb_bytes_cache: list[bytes] = []  # single-element list so it's mutable


def _get_default_thumb_bytes() -> bytes | None:
    """Return cached bytes for the default thumbnail, fetching once on first call."""
    if _default_thumb_bytes_cache:
        return _default_thumb_bytes_cache[0]
    try:
        resp = requests.get(_DEFAULT_THUMB_URL, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return None
    _default_thumb_bytes_cache.append(resp.content)
    return resp.content


def _primary_settings_path() -> Path:
    return CONFIG_DIR / _SETTINGS_FILE


def _legacy_settings_path() -> Path:
    return CONFIG_DIR / 'subplace' / _LEGACY_SETTINGS_FILE


# Shared rate-limit tracker for the public servers endpoint.
class _ServerRateLimitState:
    def __init__(self) -> None:
        self.until = 0.0
        self.lock = threading.Lock()

    def current(self) -> float:
        with self.lock:
            return self.until

    def extend(self, delay: float) -> float:
        with self.lock:
            self.until = max(self.until, time.time() + delay)
            return self.until

    def clear(self) -> None:
        with self.lock:
            self.until = 0.0

    def changed_or_elapsed(self, expected: float) -> bool:
        with self.lock:
            return self.until != expected or self.until <= time.time()


_server_rate_limit = _ServerRateLimitState()


# Helpers


def _humanize_elapsed(seconds: float) -> str:
    minutes = int(seconds / 60)
    hours = int(minutes / 60)
    days = int(hours / 24)
    months = int(days / 30)
    years = int(days / 365)
    if seconds < 60:
        return tr('subplace.time.just_now')
    if minutes < 60:
        return tr(
            'subplace.time.minute_ago' if minutes == 1 else 'subplace.time.minutes_ago',
            count=minutes,
        )
    if hours < 24:
        return tr(
            'subplace.time.hour_ago' if hours == 1 else 'subplace.time.hours_ago',
            count=hours,
        )
    if days < 30:
        return tr(
            'subplace.time.day_ago' if days == 1 else 'subplace.time.days_ago',
            count=days,
        )
    if months < 12:
        return tr(
            'subplace.time.month_ago' if months == 1 else 'subplace.time.months_ago',
            count=months,
        )
    return tr(
        'subplace.time.year_ago' if years == 1 else 'subplace.time.years_ago',
        count=years,
    )


def _humanize_time(iso_str: str | None) -> str:
    if not iso_str:
        return tr('subplace.time.unknown')
    try:
        seconds = (datetime.now(UTC) - _dateutil_parser.isoparse(iso_str)).total_seconds()
    except TypeError, ValueError, OverflowError:
        return iso_str
    return _humanize_elapsed(seconds)


def _run_contained[T](
    fn: Callable[[], T],
    on_error: Callable[[Exception], object],
) -> T | None:
    """Run a Qt/thread boundary callback and report unexpected failures."""
    try:
        return fn()
    except Exception as exc:  # ruff: ignore[blind-except]
        on_error(exc)
        return None


# Main-thread invoker


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn: _MainCallback) -> None:
        def _report_error(exc: Exception) -> None:
            log_buffer.log('subplace', f'invoker error: {exc}')
            traceback.print_exception(exc)

        _run_contained(fn, _report_error)


# GameCardWidget (inline, PySide6)

if TYPE_CHECKING:
    _CARD_H: int = 0
    _CARD_W: int = 0
    _THUMB_H: int = 0
    _THUMB_W: int = 0

    def _make_rounded_pixmap(pix: QPixmap, w: int, h: int, radius: int = 6) -> QPixmap: ...

    def _preprocess_thumb_bytes(
        raw: bytes, w: int, h: int, radius: int = 6
    ) -> tuple[bytes, int, int] | None: ...

    def _rando_account_switched(tab: _RandoTab) -> bool: ...

    def _get_auth_ticket_runtime(cookie: str) -> str | None: ...
else:
    _prejsons = import_module(f'{__package__}.prejsons_dialog')
    _CARD_H = _prejsons.__dict__['_CARD_H']
    _CARD_W = _prejsons.__dict__['_CARD_W']
    _THUMB_H = _prejsons.__dict__['_THUMB_H']
    _THUMB_W = _prejsons.__dict__['_THUMB_W']
    _make_rounded_pixmap = _prejsons.__dict__['_make_rounded_pixmap']
    _preprocess_thumb_bytes = _prejsons.__dict__['_preprocess_thumb_bytes']

    def _rando_account_switched(tab: _RandoTab) -> bool:
        return bool(tab.__dict__['_account_switched'])

    def _get_auth_ticket_runtime(cookie: str) -> str | None:
        module = import_module(f'{__package__}.rando_stuff_tab')
        return module.__dict__['_get_auth_ticket'](cookie)


_SUBPLACE_CARD_H = _CARD_H + 8


class _JobIdEdit(QLineEdit):
    """QLineEdit with placeholder text for an optional JobId."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText(tr('ui.gui.subplace_joiner_tab.jobid_optional'))
        self.setFixedHeight(20)
        self.setStyleSheet('font-size: 9pt;')

    def get_job_id(self) -> str:
        return self.text().strip()

    def set_job_id(self, job_id: str) -> None:
        self.setText(job_id)


class _CopyPlaceIdLabel(QLabel):
    """Small clickable label that copies its card's placeId."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(tr('subplace.copy_id'), parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(12)
        self.setStyleSheet('color: palette(placeholder-text); font-size: 7pt;')
        self.setToolTip(tr('ui.gui.subplace_joiner_tab.copy_subplace_id'))

    @override
    def mousePressEvent(self, event: QMouseEvent) -> None:
        card = self.parent()
        place_id = getattr(card, 'place_id', None)
        if place_id is not None:
            QApplication.clipboard().setText(str(place_id))
        super().mousePressEvent(event)


class SubplaceGameCard(QFrame):
    """Game card matching the PreJsons visual design, with subplace-joiner buttons."""

    def _apply_style(self, hover: bool = False) -> None:
        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        border = 'rgba(255,255,255,0.22)' if dark else 'rgba(0,0,0,0.18)'
        bg = (
            ('rgba(255,255,255,0.07)' if hover else 'rgba(255,255,255,0.04)')
            if dark
            else ('rgba(0,0,0,0.06)' if hover else 'transparent')
        )
        self.setStyleSheet(f'SubplaceGameCard {{ border: 1px solid {border}; background: {bg}; }}')

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.place_id: int | None = None
        self.is_root: bool = False
        self.created_iso: str | None = None
        self.updated_iso: str | None = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_style()
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_SUBPLACE_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)

        self.thumb_label = QLabel(tr('ui.gui.subplace_joiner_tab.loading'))
        self.thumb_label.setFixedHeight(_THUMB_H)
        self.thumb_label.setMinimumWidth(_THUMB_W)
        self.thumb_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setScaledContents(True)
        self.thumb_label.setStyleSheet(
            'background: palette(alternate-base); border-radius: 4px; color: palette(placeholder-text); font-size: 8pt;'
        )
        layout.addWidget(self.thumb_label)

        self.name_label = QLabel(tr('ui.gui.subplace_joiner_tab.unknown'))
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(42)
        f = QFont()
        f.setBold(True)
        self.name_label.setFont(f)
        layout.addWidget(self.name_label)

        self.created_label = QLabel('')
        self.created_label.setStyleSheet('color: palette(placeholder-text); font-size: 8pt;')
        layout.addWidget(self.created_label)

        self.updated_label = QLabel('')
        self.updated_label.setStyleSheet('color: palette(placeholder-text); font-size: 8pt;')
        layout.addWidget(self.updated_label)

        self.copy_id_label = _CopyPlaceIdLabel(self)
        layout.addWidget(self.copy_id_label, 0, Qt.AlignmentFlag.AlignLeft)

        layout.addStretch()

        self.job_id_edit = _JobIdEdit()
        layout.addWidget(self.job_id_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.join_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.join'))
        self.join_btn.setFixedHeight(22)
        btn_row.addWidget(self.join_btn)

        self.open_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.browser'))
        self.open_btn.setFixedHeight(22)
        btn_row.addWidget(self.open_btn)

        self.fetch_jobs_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.jobids'))
        self.fetch_jobs_btn.setFixedHeight(22)
        btn_row.addWidget(self.fetch_jobs_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def set_data(self, name: str, created: str = '', updated: str = '') -> None:
        self.name_label.setText(name)
        if created:
            self.created_label.setText(
                tr('ui.gui.subplace_joiner_tab.created_value', value0=created)
            )
        if updated:
            self.updated_label.setText(
                tr('ui.gui.subplace_joiner_tab.updated_value', value0=updated)
            )

    def set_thumbnail(self, pix: QPixmap) -> None:
        if not pix or pix.isNull():
            return
        try:
            baked = _make_rounded_pixmap(pix, _THUMB_W, _THUMB_H, radius=6)
        except OSError, RuntimeError, TypeError, ValueError:
            baked = pix
        self.thumb_label.setPixmap(baked)
        self.thumb_label.setText('')
        self.thumb_label.setStyleSheet('background: transparent;')

    def on_join(self, fn: _ButtonCallback) -> None:
        self.join_btn.clicked.connect(fn)

    def on_open(self, fn: _ButtonCallback) -> None:
        self.open_btn.clicked.connect(fn)

    def on_fetch_jobs(self, fn: _ButtonCallback) -> None:
        self.fetch_jobs_btn.clicked.connect(fn)

    @override
    def enterEvent(self, event: QEnterEvent) -> None:
        self._apply_style(hover=True)
        super().enterEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self._apply_style()
        super().leaveEvent(event)


# JobId Dialog


class JobIdDialog(QDialog):
    """Fetches and displays public server jobIds for a given placeId."""

    _results_ready = Signal(list, object)  # servers, next_cursor (object so None is allowed)
    _error_ready = Signal(str)
    _status_update = Signal(str)

    _PAGE_LIMIT = 25

    @staticmethod
    def _sort_options() -> tuple[tuple[str, str], ...]:
        return (
            (tr('subplace.jobs.sort.players_fewest'), 'playing_asc'),
            (tr('subplace.jobs.sort.players_most'), 'playing_desc'),
            (tr('subplace.jobs.sort.ping_lowest'), 'ping_asc'),
            (tr('subplace.jobs.sort.ping_highest'), 'ping_desc'),
        )

    def __init__(
        self,
        place_id: int | str,
        on_select: Callable[[str], object] | None = None,
        parent: QWidget | None = None,
        cached_servers: list[_ServerInfo] | None = None,
        on_cache_update: Callable[[list[_ServerInfo]], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._place_id = place_id
        self._on_select = on_select
        self._on_cache_update = on_cache_update
        self._cursor: str | None = None
        self._loading = False
        self._all_servers: list[_ServerInfo] = list(cached_servers) if cached_servers else []

        self._results_ready.connect(self._apply_results)
        self._error_ready.connect(self._apply_error)

        self.setWindowTitle(tr('ui.gui.subplace_joiner_tab.jobids_place_value', value0=place_id))
        self.resize(520, 440)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel(tr('ui.gui.subplace_joiner_tab.sort_by')))
        self._sort_combo = QComboBox()
        for label, value in self._sort_options():
            self._sort_combo.addItem(label, value)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self._sort_combo)
        top.addStretch(1)
        layout.addLayout(top)

        self._status_label = QLabel(tr('ui.gui.subplace_joiner_tab.fetching_servers'))
        layout.addWidget(self._status_label)

        self._status_update.connect(self._status_label.setText)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list, 1)

        bottom = QHBoxLayout()
        self._load_more_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.load_more'))
        self._load_more_btn.clicked.connect(self._fetch_page)
        bottom.addWidget(self._load_more_btn)
        bottom.addStretch(1)
        close_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.close'))
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        if self._all_servers:
            self._apply_results([], None)  # render cached servers immediately
        self._fetch_page()

    def _current_sort(self) -> str:
        return str(self._sort_combo.currentData() or 'playing_asc')

    def _on_sort_changed(self) -> None:
        sort = self._current_sort()
        if sort in {'ping_asc', 'ping_desc'}:
            # Just re-sort existing data
            self._list.clear()
            for s in self._sorted_servers(self._all_servers):
                job_id = s.get('id', '')
                playing = s.get('playing', '?')
                max_players = s.get('maxPlayers', '?')
                ping = s.get('ping')
                ping_str = tr('subplace.jobs.ping_suffix', ping=ping) if ping is not None else ''
                item = QListWidgetItem(
                    tr(
                        'ui.gui.subplace_joiner_tab.value_value_value_players_value',
                        value0=job_id,
                        value1=playing,
                        value2=max_players,
                        value3=ping_str,
                    )
                )
                item.setData(Qt.ItemDataRole.UserRole, job_id)
                self._list.addItem(item)
            return
        # For player sorts: fetch new batch with the new sort order and add to existing
        self._cursor = None
        self._fetch_page()

    def _fetch_page(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._status_label.setText(tr('ui.gui.subplace_joiner_tab.fetching_servers'))
        sort = self._current_sort()
        cursor = self._cursor
        threading.Thread(target=self._worker, args=(sort, cursor), daemon=True).start()

    def _wait_for_rate_limit(self, wait_until: float) -> None:
        remaining = wait_until - time.time()
        if remaining <= 0:
            return
        for seconds in range(int(remaining) + 1, 0, -1):
            if _server_rate_limit.changed_or_elapsed(wait_until):
                break
            self._status_update.emit(tr('subplace.jobs.rate_limited_retry', seconds=seconds))
            time.sleep(1)
            if time.time() >= wait_until:
                break
        self._status_update.emit(tr('subplace.jobs.retrying'))

    def _fetch_server_page(self, url: str) -> None:
        rate_limit_wait = 60
        for attempt in range(2):
            self._wait_for_rate_limit(_server_rate_limit.current())
            response = requests.get(url, timeout=15, proxies={})
            if response.status_code == 429:
                wait_until = _server_rate_limit.extend(rate_limit_wait)
                if attempt == 0:
                    self._wait_for_rate_limit(wait_until)
                    continue
                self._error_ready.emit(tr('subplace.jobs.too_many_requests'))
                return
            response.raise_for_status()
            _server_rate_limit.clear()
            payload: object = response.json()
            if not isinstance(payload, dict):
                msg = 'Roblox public-server response was not an object'
                raise TypeError(msg)
            data = cast('dict[str, object]', payload)
            servers = cast('list[_ServerInfo]', data.get('data', []))
            next_cursor = cast('str | None', data.get('nextPageCursor'))
            self._results_ready.emit(servers, next_cursor)
            return

    def _worker(self, sort: str, cursor: str | None) -> None:
        sort_order = 'Asc' if sort == 'playing_asc' else 'Desc'
        url = (
            f'https://games.roblox.com/v1/games/{self._place_id}/servers/Public'
            f'?limit={self._PAGE_LIMIT}&sortOrder={sort_order}&excludeFullGames=false'
        )
        if cursor:
            url += f'&cursor={cursor}'
        _run_contained(
            lambda: self._fetch_server_page(url),
            lambda exc: self._error_ready.emit(str(exc)),
        )

    def _sorted_servers(self, servers: list[_ServerInfo]) -> list[_ServerInfo]:
        sort = self._current_sort()
        if sort == 'playing_asc':
            return sorted(servers, key=lambda s: s.get('playing', 0))
        if sort == 'playing_desc':
            return sorted(servers, key=lambda s: s.get('playing', 0), reverse=True)
        if sort == 'ping_asc':
            return sorted(servers, key=lambda s: cast('int', s.get('ping', 9999)))
        if sort == 'ping_desc':
            return sorted(servers, key=lambda s: cast('int', s.get('ping', 0)), reverse=True)
        return servers

    def _apply_results(self, servers: list[_ServerInfo], next_cursor: str | None) -> None:
        self._cursor = next_cursor
        self._loading = False
        existing_ids = {s.get('id') for s in self._all_servers}
        self._all_servers.extend(s for s in servers if s.get('id') not in existing_ids)
        if self._on_cache_update:
            self._on_cache_update(list(self._all_servers))

        self._list.clear()
        for s in self._sorted_servers(self._all_servers):
            job_id = s.get('id', '')
            playing = s.get('playing', '?')
            max_players = s.get('maxPlayers', '?')
            ping = s.get('ping')
            ping_str = tr('subplace.jobs.ping_suffix', ping=ping) if ping is not None else ''
            item = QListWidgetItem(
                tr(
                    'ui.gui.subplace_joiner_tab.value_value_value_players_value',
                    value0=job_id,
                    value1=playing,
                    value2=max_players,
                    value3=ping_str,
                )
            )
            item.setData(Qt.ItemDataRole.UserRole, job_id)
            self._list.addItem(item)

        total = self._list.count()
        if next_cursor:
            self._status_label.setText(
                tr('ui.gui.subplace_joiner_tab.value_servers_loaded_more_available', value0=total)
            )
        else:
            self._status_label.setText(
                tr(
                    'ui.gui.subplace_joiner_tab.value_found',
                    value0=tr_count(total, 'count.server.one', 'count.server.other'),
                )
            )

    def _apply_error(self, err: str) -> None:
        self._loading = False
        is_ratelimit = '429' in str(err)
        err_msg = (
            tr('subplace.jobs.ratelimited')
            if is_ratelimit
            else tr('subplace.jobs.error', error=str(err)[:80])
        )
        if self._all_servers:
            self._list.clear()
            for s in self._sorted_servers(self._all_servers):
                job_id = s.get('id', '')
                playing = s.get('playing', '?')
                max_players = s.get('maxPlayers', '?')
                ping = s.get('ping')
                ping_str = tr('subplace.jobs.ping_suffix', ping=ping) if ping is not None else ''
                item = QListWidgetItem(
                    tr(
                        'ui.gui.subplace_joiner_tab.value_value_value_players_value',
                        value0=job_id,
                        value1=playing,
                        value2=max_players,
                        value3=ping_str,
                    )
                )
                item.setData(Qt.ItemDataRole.UserRole, job_id)
                self._list.addItem(item)
            self._status_label.setText(err_msg)
        else:
            self._status_label.setText(err_msg)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        job_id = item.data(Qt.ItemDataRole.UserRole)
        if self._on_select and job_id:
            self._on_select(job_id)
            self.close()


# Subplace Joiner Tab


class SubplaceJoinerTab(QWidget):
    """Subplace Joiner tab - search, browse, and join subplaces."""

    _WANTED_ENDPOINTS = (
        '/v1/join-game',
        '/v1/join-play-together-game',
        '/v1/join-game-instance',
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        rando_tab: _RandoTab | None = None,
        config_manager: _ConfigManager | None = None,
        proxy_master: _ProxyMaster | None = None,
    ) -> None:
        super().__init__(parent)
        self._rando_tab = rando_tab
        self._config_manager = config_manager
        self._proxy_master = proxy_master
        self._qt_destroyed = False
        self.destroyed.connect(self._on_qt_destroyed)
        self._invoker = _Invoker(self)
        self._cards: list[SubplaceGameCard] = []
        self._card_by_place_id: dict[int, SubplaceGameCard] = {}
        self.thumb_cache: dict[str, bytes] = {}
        self._search_cancel_event = threading.Event()
        self.joining_place = False
        self._current_job_id: str = ''
        self._jobid_cache: dict[int, list[_ServerInfo]] = {}  # place_id -> cached servers
        self._place_name_cache: dict[str, str] = {}  # place_id -> game name
        self._custom_names: dict[str, str] = {}  # place_id -> user-defined name

        self.recent_ids: list[str] = []
        self.favorites: list[str] = []

        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_cols = 0

        self._setup_ui()
        self._load_settings()
        self._rebuild_recent_buttons()
        self._rebuild_favorite_buttons()
        self._update_favorite_btn()
        threading.Thread(target=self._resolve_current_user, daemon=True).start()

    def _on_qt_destroyed(self, *_: object) -> None:
        self._qt_destroyed = True

    def _resolve_current_user(self) -> None:
        """Background thread: read the active Roblox cookie and resolve the username."""
        cookie = _wait_for_roblosecurity()
        if not cookie:
            return
        session = requests.Session()
        session.trust_env = False
        session.proxies = {}
        try:
            session.cookies.set('.ROBLOSECURITY', cookie)
        except TypeError, ValueError:
            session.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        try:
            response = session.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
            if response.status_code != 200:
                return
            payload: object = response.json()
        except requests.RequestException as exc:
            log_buffer.log('subplace', f'Failed to resolve current user: {exc}')
            return
        if not isinstance(payload, dict):
            return
        user_data = cast('dict[str, object]', payload)
        username = user_data.get('name')
        if not isinstance(username, str) or not username:
            return

        def _update(u: str = username) -> None:
            self.set_selected_account(u)

        self._on_main(_update)

    def set_selected_account(self, username: str) -> None:
        """Update the selected-account footer label from external account switches."""
        username = (username or '').strip()
        self._selected_label.setText(
            tr(
                'ui.gui.subplace_joiner_tab.selected_value',
                value0=username or tr('subplace.none'),
            )
        )
        unresolved = set(self.recent_ids) | set(self.favorites)
        for place_id in unresolved:
            if self._place_name_cache.get(place_id) == place_id:
                self._place_name_cache.pop(place_id, None)
        self._rebuild_recent_buttons()
        self._rebuild_favorite_buttons()

    # UI setup

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        top_frame = QFrame()
        top_frame.setFrameShape(QFrame.Shape.StyledPanel)
        top_frame.setFrameShadow(QFrame.Shadow.Raised)
        top_layout = QVBoxLayout(top_frame)
        top_layout.setContentsMargins(4, 4, 4, 4)
        top_layout.setSpacing(4)

        row0 = QHBoxLayout()
        row0.setSpacing(4)
        placeid_lbl = QLabel(tr('ui.gui.subplace_joiner_tab.placeid'))
        row0.addWidget(placeid_lbl, 0)
        self.PlaceID_search = QLineEdit()
        self.PlaceID_search.setPlaceholderText(tr('ui.gui.subplace_joiner_tab.place_id_to_search'))
        self.PlaceID_search.returnPressed.connect(self.on_search_clicked)
        self.PlaceID_search.textChanged.connect(self._update_favorite_btn)
        row0.addWidget(self.PlaceID_search, 1)
        self.search_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.search'))
        self.search_btn.clicked.connect(self.on_search_clicked)
        row0.addWidget(self.search_btn, 0)
        top_layout.addLayout(row0)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        search_lbl = QLabel(tr('ui.gui.subplace_joiner_tab.search_2'))
        search_lbl.setMinimumWidth(placeid_lbl.sizeHint().width())
        row1.addWidget(search_lbl, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(tr('ui.gui.subplace_joiner_tab.filter_by_name_or_id'))
        self.search_input.textChanged.connect(self.apply_search_and_sort)
        row1.addWidget(self.search_input, 1)
        self.favorite_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.favorite'))
        self.favorite_btn.clicked.connect(self.on_favorite_clicked)
        row1.addWidget(self.favorite_btn, 0)
        sort_combo = QComboBox()
        for label, value in (
            (tr('subplace.sort.place_id_asc'), 'place_id_asc'),
            (tr('subplace.sort.place_id_desc'), 'place_id_desc'),
            (tr('subplace.sort.created_asc'), 'created_asc'),
            (tr('subplace.sort.created_desc'), 'created_desc'),
            (tr('subplace.sort.updated_asc'), 'updated_asc'),
            (tr('subplace.sort.updated_desc'), 'updated_desc'),
        ):
            sort_combo.addItem(label, value)
        self.sort_combo = sort_combo
        sort_combo.currentIndexChanged.connect(self.apply_search_and_sort)
        row1.addWidget(sort_combo, 0)
        top_layout.addLayout(row1)

        root.addWidget(top_frame)

        main_layout = QHBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(4)

        # Left sidebar
        sidebar = QVBoxLayout()
        sidebar.setSpacing(4)

        recent_label = QLabel(tr('ui.gui.subplace_joiner_tab.recent_placeids'))
        recent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar.addWidget(recent_label)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setFixedWidth(200)
        self._recent_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setWidgetResizable(True)
        self.recent_contents = QWidget()
        self.recent_contents.setObjectName('RecentPlaceIdsContents')
        self.recent_contents.setAutoFillBackground(True)
        self.recent_contents.setBackgroundRole(QPalette.ColorRole.Base)
        self.recent_layout = QVBoxLayout(self.recent_contents)
        self.recent_layout.setContentsMargins(2, 2, 2, 2)
        self.recent_layout.setSpacing(2)
        self._recent_scroll.setWidget(self.recent_contents)
        sidebar.addWidget(self._recent_scroll, 1)

        fav_label = QLabel(tr('ui.gui.subplace_joiner_tab.favorited_placeids'))
        fav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar.addWidget(fav_label)

        self._fav_scroll = QScrollArea()
        self._fav_scroll.setFixedWidth(200)
        self._fav_scroll.setWidgetResizable(True)
        self.fav_contents = QWidget()
        self.fav_contents.setObjectName('FavoritedPlaceIdsContents')
        self.fav_contents.setAutoFillBackground(True)
        self.fav_contents.setBackgroundRole(QPalette.ColorRole.Base)
        self.fav_layout = QVBoxLayout(self.fav_contents)
        self.fav_layout.setContentsMargins(2, 2, 2, 2)
        self.fav_layout.setSpacing(2)
        self._fav_scroll.setWidget(self.fav_contents)
        sidebar.addWidget(self._fav_scroll, 1)

        main_layout.addLayout(sidebar)

        # Results area
        self.results_scroll = QScrollArea()
        self.results_scroll.setObjectName('Results')
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_scroll.setWidgetResizable(True)

        self.results_container = QWidget()
        self.results_container.setObjectName('resultsContainer')
        self.results_container.setAutoFillBackground(True)
        self.results_container.setBackgroundRole(QPalette.ColorRole.Base)
        self.results_grid = QGridLayout(self.results_container)
        self.results_grid.setContentsMargins(8, 8, 8, 8)
        self.results_grid.setSpacing(8)
        self.results_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.results_scroll.setWidget(self.results_container)
        main_layout.addWidget(self.results_scroll, 1)

        root.addLayout(main_layout, 1)

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        self._selected_label = QLabel(tr('ui.gui.subplace_joiner_tab.selected_none'))
        self._selected_label.setStyleSheet('color: palette(placeholder-text); font-size: 9pt;')
        footer_layout.addWidget(self._selected_label)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton(tr('ui.gui.subplace_joiner_tab.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)
        root.addWidget(footer_widget)

    def _clear_roblox_cache(self) -> None:
        window_type = cast(
            'Callable[[], QWidget]',
            import_module('fleasion.gui.delete_cache').__dict__['DeleteCacheWindow'],
        )
        window = window_type()
        window.show()

    # Settings persistence

    def _settings_path(self) -> Path:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return _primary_settings_path()

    @staticmethod
    def _read_settings_file(path: Path) -> tuple[list[str], list[str], dict[str, str]]:
        with path.open(encoding='utf-8') as file:
            payload: object = json.load(file)
        if not isinstance(payload, dict):
            msg = 'Subplace settings must contain a JSON object'
            raise TypeError(msg)
        data = cast('dict[str, object]', payload)
        raw_recent = data.get('recent_ids', [])
        raw_favorites = data.get('favorites', [])
        raw_names = data.get('custom_names', {})
        if not isinstance(raw_recent, list) or not isinstance(raw_favorites, list):
            msg = 'Subplace recent_ids and favorites must contain JSON arrays'
            raise TypeError(msg)
        if not isinstance(raw_names, dict):
            msg = 'Subplace custom_names must contain a JSON object'
            raise TypeError(msg)
        recent_values = cast('list[object]', raw_recent)
        favorite_values = cast('list[object]', raw_favorites)
        recent_ids = [str(value) for value in recent_values if str(value).strip()]
        favorites = [str(value) for value in favorite_values if str(value).strip()]
        custom_names = {
            str(key): str(value) for key, value in cast('dict[object, object]', raw_names).items()
        }
        return recent_ids, favorites, custom_names

    @classmethod
    def _find_settings(
        cls, paths: tuple[Path, Path]
    ) -> tuple[Path, list[str], list[str], dict[str, str]] | None:
        for path in paths:
            if not path.exists():
                continue
            recent_ids, favorites, custom_names = cls._read_settings_file(path)
            return path, recent_ids, favorites, custom_names
        return None

    def _load_settings(self) -> None:
        primary_path = _primary_settings_path()
        try:
            loaded = self._find_settings((primary_path, _legacy_settings_path()))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            log_buffer.log('subplace', f'Failed to load settings: {exc}')
            self.recent_ids = []
            self.favorites = []
            self._custom_names = {}
            return
        if loaded is None:
            return
        loaded_from, self.recent_ids, self.favorites, self._custom_names = loaded
        if loaded_from != primary_path:
            self._save_settings()

    def _save_settings(self) -> None:
        path = self._settings_path()
        try:
            with path.open('w', encoding='utf-8') as file:
                json.dump(
                    {
                        'recent_ids': self.recent_ids,
                        'favorites': self.favorites,
                        'custom_names': self._custom_names,
                    },
                    file,
                    indent=2,
                )
        except (OSError, TypeError, ValueError) as exc:
            log_buffer.log('subplace', f'Failed to save settings: {exc}')

    # Recent / Favorites sidebar

    def _clear_layout_buttons(self, layout: QVBoxLayout) -> None:
        if not layout:
            return
        while layout.count():
            item = cast('QLayoutItem', layout.takeAt(0))
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _fetch_place_name(self, place_id: str, callback: _PlaceNameCallback) -> None:
        if place_id in self._custom_names:
            name = self._custom_names[place_id]
            self._place_name_cache[place_id] = name
            callback(name)
            return
        if place_id in self._place_name_cache:
            callback(self._place_name_cache[place_id])
            return

        def _worker() -> None:
            def _resolve() -> str | None:
                cookie = _wait_for_roblosecurity() or ''
                return self._resolve_place_name(place_id, cookie)

            name = _run_contained(
                _resolve,
                lambda exc: log_buffer.log(
                    'subplace', f'Failed to resolve recent PlaceID {place_id}: {exc}'
                ),
            )
            if not name:
                return
            self._place_name_cache[place_id] = name
            self._on_main(lambda n=name: callback(n))

        threading.Thread(target=_worker, daemon=True).start()

    def _resolve_authenticated_place_name(
        self, place_id: str, cookie: str, errors: list[str]
    ) -> str | None:
        try:
            response = self._get(
                f'https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}',
                timeout=10,
                cookies={'.ROBLOSECURITY': cookie},
            )
            payload: object = response.json() if response.status_code == 200 else None
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'multiget {type(exc).__name__}: {exc}')
            return None
        if response.status_code != 200:
            errors.append(f'multiget status {response.status_code}')
            return None
        if not isinstance(payload, list) or not payload:
            return None
        records = cast('list[object]', payload)
        first = records[0]
        if not isinstance(first, dict):
            return None
        name = cast('dict[str, object]', first).get('name')
        return name if isinstance(name, str) and name else None

    @staticmethod
    def _universe_id_from_payload(payload: object, errors: list[str]) -> int | str | None:
        if not isinstance(payload, dict):
            errors.append('universe response was not an object')
            return None
        universe_id = cast('dict[str, object]', payload).get('universeId')
        if not isinstance(universe_id, int | str) or not str(universe_id).strip():
            errors.append('universe missing universeId')
            return None
        return universe_id

    @staticmethod
    def _game_name_from_payload(payload: object, place_id: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        games = cast('dict[str, object]', payload).get('data', [])
        if not isinstance(games, list) or not games:
            return None
        first = cast('list[object]', games)[0]
        if not isinstance(first, dict):
            return None
        name = cast('dict[str, object]', first).get('name')
        if isinstance(name, str) and name and name not in {'[TITLE UNAVAILABLE]', place_id}:
            return name
        return None

    def _resolve_public_universe_id(self, place_id: str, errors: list[str]) -> str | None:
        try:
            response = self._get(
                f'https://apis.roblox.com/universes/v1/places/{place_id}/universe',
                timeout=10,
            )
            payload: object = response.json() if response.status_code == 200 else None
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'public fallback {type(exc).__name__}: {exc}')
            return None
        if response.status_code != 200:
            errors.append(f'universe status {response.status_code}')
            return None
        if not isinstance(payload, dict):
            errors.append('universe response was not an object')
            return None
        universe_id = cast('dict[str, object]', payload).get('universeId')
        if not isinstance(universe_id, int | str) or not str(universe_id).strip():
            errors.append('universe missing universeId')
            return None
        return str(universe_id)

    @staticmethod
    def _game_name_from_details_payload(payload: object, place_id: str) -> str | None:
        if not isinstance(payload, dict):
            return None
        games = cast('dict[str, object]', payload).get('data', [])
        if not isinstance(games, list) or not games:
            return None
        first = cast('list[object]', games)[0]
        if not isinstance(first, dict):
            return None
        name = cast('dict[str, object]', first).get('name')
        if isinstance(name, str) and name and name not in {'[TITLE UNAVAILABLE]', place_id}:
            return name
        return None

    def _resolve_public_place_name(self, place_id: str, errors: list[str]) -> str | None:
        universe_id = self._resolve_public_universe_id(place_id, errors)
        if universe_id is None:
            return None
        try:
            details = self._get(
                f'https://games.roblox.com/v1/games?universeIds={universe_id}',
                timeout=10,
            )
            payload: object = details.json() if details.status_code == 200 else None
        except (requests.RequestException, ValueError) as exc:
            errors.append(f'public fallback {type(exc).__name__}: {exc}')
            return None
        if details.status_code != 200:
            errors.append(f'games status {details.status_code}')
            return None
        return self._game_name_from_details_payload(payload, place_id)

    def _resolve_place_name(self, place_id: str, cookie: str = '') -> str | None:
        errors: list[str] = []
        if cookie:
            name = self._resolve_authenticated_place_name(place_id, cookie, errors)
            if name:
                return name
        else:
            errors.append('missing cookie')

        name = self._resolve_public_place_name(place_id, errors)
        if name:
            return name
        log_buffer.log(
            'subplace',
            f'Could not resolve recent PlaceID {place_id}: {"; ".join(errors)}',
        )
        return None

    def _make_placeid_button(self, place_id: str, handler: _PlaceIdCallback) -> QPushButton:
        btn = QPushButton(place_id)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, pid=place_id: handler(pid))
        btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        def _show_context_menu(pos: QPoint, pid: str = place_id, b: QPushButton = btn) -> None:
            self._show_sidebar_context_menu(pid, b, pos)

        btn.customContextMenuRequested.connect(_show_context_menu)

        def _set_name(name: str, b: QPushButton = btn) -> None:
            try:
                elided = b.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 170)
                b.setText(elided.replace('&', '&&'))
                b.setToolTip(name)
            except RuntimeError:
                pass

        self._fetch_place_name(place_id, _set_name)
        return btn

    def _show_sidebar_context_menu(self, place_id: str, btn: QPushButton, pos: QPoint) -> None:
        menu = QMenu(self)
        rename_action = menu.addAction(tr('ui.gui.subplace_joiner_tab.rename'))
        remove_recent_action = None
        remove_fav_action = None
        if place_id in self.recent_ids:
            remove_recent_action = menu.addAction(
                tr('ui.gui.subplace_joiner_tab.remove_from_recents')
            )
        if place_id in self.favorites:
            remove_fav_action = menu.addAction(
                tr('ui.gui.subplace_joiner_tab.remove_from_favorites')
            )
        action = cast('QAction | None', menu.exec(btn.mapToGlobal(pos)))
        if action is None:
            return
        if action == rename_action:
            self._rename_sidebar_entry(place_id, btn)
        elif remove_recent_action and action == remove_recent_action:
            self.recent_ids.remove(place_id)
            self._save_settings()
            self._rebuild_recent_buttons()
        elif remove_fav_action and action == remove_fav_action:
            self.favorites.remove(place_id)
            self._save_settings()
            self._rebuild_favorite_buttons()

    def _rename_sidebar_entry(self, place_id: str, btn: QPushButton) -> None:
        current = self._custom_names.get(place_id) or self._place_name_cache.get(place_id, place_id)
        name, ok = QInputDialog.getText(
            self,
            tr('ui.gui.subplace_joiner_tab.rename'),
            tr('ui.gui.subplace_joiner_tab.name_for_value', value0=place_id),
            text=current,
        )
        if not ok or not name.strip():
            return
        name = name.strip()
        self._custom_names[place_id] = name
        self._place_name_cache[place_id] = name
        self._save_settings()
        try:
            elided = btn.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 170)
            btn.setText(elided.replace('&', '&&'))
            btn.setToolTip(name)
        except RuntimeError:
            pass

    def _rebuild_recent_buttons(self) -> None:
        self._clear_layout_buttons(self.recent_layout)
        for pid in self.recent_ids:
            self.recent_layout.addWidget(self._make_placeid_button(pid, self._on_recent_clicked))
        self.recent_layout.addStretch(1)

    def _rebuild_favorite_buttons(self) -> None:
        self._clear_layout_buttons(self.fav_layout)
        for pid in self.favorites:
            self.fav_layout.addWidget(self._make_placeid_button(pid, self._on_favorite_clicked))
        self.fav_layout.addStretch(1)

    def _on_recent_clicked(self, place_id: str) -> None:
        self.PlaceID_search.setText(place_id)
        self.on_search_clicked()

    def _on_favorite_clicked(self, place_id: str) -> None:
        self.PlaceID_search.setText(place_id)
        self.on_search_clicked()

    def add_recent_place_id(self, place_id: str) -> None:
        place_id = (place_id or '').strip()
        if not place_id.isdigit():
            return
        if place_id in self.recent_ids:
            self.recent_ids.remove(place_id)
        self.recent_ids.insert(0, place_id)
        self._save_settings()
        self._rebuild_recent_buttons()

    def _update_favorite_btn(self) -> None:
        pid = self._extract_place_id(self.PlaceID_search.text())
        self.favorite_btn.setText(
            tr('ui.gui.subplace_joiner_tab.unfavorite')
            if pid in self.favorites
            else tr('ui.gui.subplace_joiner_tab.favorite')
        )

    def on_favorite_clicked(self) -> None:
        place_id = self._extract_place_id(self.PlaceID_search.text())
        if not place_id.isdigit():
            return
        if place_id in self.favorites:
            self.favorites.remove(place_id)
        else:
            self.favorites.insert(0, place_id)
        self._save_settings()
        self._rebuild_favorite_buttons()
        self._update_favorite_btn()

    # Search

    @staticmethod
    def _extract_place_id(text: str) -> str:
        """Extract numeric place ID from a raw ID or a Roblox game URL."""
        text = text.strip()
        if text.isdigit():
            return text
        # e.g. https://www.roblox.com/games/537413528/some-name
        try:
            parts = urlparse(text).path.strip('/').split('/')
        except ValueError:
            return text
        if 'games' not in parts:
            return text
        index = parts.index('games')
        candidate = parts[index + 1] if index + 1 < len(parts) else ''
        return candidate if candidate.isdigit() else text

    def on_search_clicked(self) -> None:
        place_id = self._extract_place_id(self.PlaceID_search.text())
        if not place_id.isdigit():
            log_buffer.log('subplace', 'Invalid Place ID')
            return
        self.PlaceID_search.setText(place_id)

        log_buffer.log('subplace', f'Searching for Place ID: {place_id}')
        self.add_recent_place_id(place_id)

        self._search_cancel_event.set()
        self.clear_results()
        self._card_by_place_id.clear()
        self._search_cancel_event = threading.Event()

        threading.Thread(
            target=self._search_worker,
            args=(place_id, self._search_cancel_event),
            daemon=True,
        ).start()

    def _fetch_asset_timestamps(
        self, place_id: int | str | None, cookie: str
    ) -> tuple[str | None, str | None]:
        while True:
            try:
                response = self._get(
                    f'https://economy.roblox.com/v2/assets/{place_id}/details',
                    cookies={'.ROBLOSECURITY': cookie},
                    timeout=10,
                )
                response.raise_for_status()
                payload: object = response.json()
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status in {429, 500, 502, 503, 504}:
                    time.sleep(1)
                    continue
                return None, None
            except requests.RequestException, TypeError, ValueError:
                return None, None
            if not isinstance(payload, dict):
                return None, None
            details = cast('dict[str, object]', payload)
            created = details.get('Created')
            updated = details.get('Updated')
            return (
                created if isinstance(created, str) else None,
                updated if isinstance(updated, str) else None,
            )

    def _load_timestamps(
        self, all_places: list[_PlaceRecord], cookie: str, cancel_event: threading.Event
    ) -> None:
        updated: list[_PlaceRecord] = []
        for i, p in enumerate(all_places):
            if cancel_event.is_set():
                return
            p['created'], p['updated'] = self._fetch_asset_timestamps(p.get('id'), cookie)
            updated.append(p)
            if (i + 1) % 5 == 0 or i == len(all_places) - 1:
                pc: list[_CardUpdate] = [
                    (p['display_name'], p.get('created'), p.get('updated')) for p in updated.copy()
                ]
                self._on_main_guarded(lambda x=pc: self._update_cards(x), cancel_event)

    def _queue_thumbnail(
        self, place_id: int, image_bytes: bytes, cancel_event: threading.Event
    ) -> None:
        processed = _preprocess_thumb_bytes(image_bytes, _THUMB_W, _THUMB_H)
        if processed is None:
            return
        rgba, width, height = processed

        def apply_pix() -> None:
            card = self._card_by_place_id.get(place_id)
            if card is None:
                return
            image = QImage(rgba, width, height, QImage.Format.Format_RGBA8888)
            card.thumb_label.setPixmap(QPixmap.fromImage(image))
            card.thumb_label.setText('')
            card.thumb_label.setStyleSheet('background: transparent;')

        self._on_main_guarded(apply_pix, cancel_event)

    def _queue_default_thumbnails(
        self, place_ids: list[int | str], cancel_event: threading.Event
    ) -> None:
        fallback = _get_default_thumb_bytes()
        if fallback is None:
            return
        processed = _preprocess_thumb_bytes(fallback, _THUMB_W, _THUMB_H)
        if processed is None:
            return
        rgba, width, height = processed
        for place_id in place_ids:
            pid = int(place_id)

            def apply_fallback(pid: int = pid) -> None:
                card = self._card_by_place_id.get(pid)
                if card is None:
                    return
                image = QImage(rgba, width, height, QImage.Format.Format_RGBA8888)
                card.thumb_label.setPixmap(QPixmap.fromImage(image))
                card.thumb_label.setText('')
                card.thumb_label.setStyleSheet('background: transparent;')

            self._on_main_guarded(apply_fallback, cancel_event)

    def _load_thumbnails(
        self, all_places: list[_PlaceRecord], cancel_event: threading.Event
    ) -> None:
        batch_size = 100
        pending = [p for p in all_places if p.get('id')]
        for chunk_start in range(0, len(pending), batch_size):
            if cancel_event.is_set():
                return
            chunk = pending[chunk_start : chunk_start + batch_size]
            place_ids = [p['id'] for p in chunk]
            try:
                thumb_map = self._fetch_thumb_bytes_batch(place_ids)
            except (requests.RequestException, TypeError, ValueError) as exc:
                log_buffer.log('subplace', f'Batch thumbnail fetch failed: {exc}')
                thumb_map = {}
            for pid_val, img_bytes in thumb_map.items():
                if img_bytes:
                    self._queue_thumbnail(int(pid_val), img_bytes, cancel_event)
            missing_ids = [pid for pid in place_ids if not thumb_map.get(str(pid))]
            if missing_ids:
                self._queue_default_thumbnails(missing_ids, cancel_event)

    def _run_search(self, place_id: str, cancel_event: threading.Event) -> None:
        if cancel_event.is_set():
            return

        u = self._get(
            f'https://apis.roblox.com/universes/v1/places/{place_id}/universe',
            timeout=10,
        )
        u.raise_for_status()
        universe_data = cast('_UniverseInfo', u.json())
        universe_id = universe_data.get('universeId')
        if not universe_id:
            msg = 'Invalid Place ID or universe not found'
            raise RuntimeError(msg)

        details = self._get(
            f'https://games.roblox.com/v1/games?universeIds={universe_id}',
            timeout=10,
        )
        details.raise_for_status()
        details_data = cast('dict[str, object]', details.json())
        games_data = cast('list[_GameInfo]', details_data.get('data', []))
        root_place_id = cast(
            'int | str', games_data[0].get('rootPlaceId') if games_data else int(place_id)
        )

        all_places: list[_PlaceRecord] = []
        cursor: str | None = None
        seen: set[int | str | None] = set()

        while True:
            if cancel_event.is_set():
                return
            url = f'https://develop.roblox.com/v1/universes/{universe_id}/places?limit=100'
            if cursor:
                url += f'&cursor={cursor}'
            r = self._get(url, timeout=10)
            r.raise_for_status()
            page_data = cast('dict[str, object]', r.json())
            batch = cast('list[_RawPlaceRecord]', page_data.get('data', []))
            if not batch:
                break
            for p in batch:
                pid = p.get('id')
                if pid in seen:
                    continue
                seen.add(pid)
                p['display_name'] = p.get('name') or f'Place {pid}'
                p['created'] = None
                p['updated'] = None
                p['is_root'] = int(cast('int | str', pid)) == int(root_place_id)
                all_places.append(cast('_PlaceRecord', p))
            cursor = cast('str | None', page_data.get('nextPageCursor'))
            if not cursor:
                break

        log_buffer.log('subplace', f'Found {len(all_places)} places')

        items: list[_CardItem] = [
            (
                p['display_name'],
                p.get('created'),
                p.get('updated'),
                p['id'],
                root_place_id,
            )
            for p in all_places
        ]
        self._on_main_guarded(lambda: self._add_new_cards(items), cancel_event)

        cookie = _wait_for_roblosecurity() or ''

        threading.Thread(
            target=self._load_timestamps,
            args=(all_places, cookie, cancel_event),
            daemon=True,
        ).start()

        threading.Thread(
            target=self._load_thumbnails,
            args=(all_places, cancel_event),
            daemon=True,
        ).start()

    def _search_worker(self, place_id: str, cancel_event: threading.Event) -> None:
        _run_contained(
            lambda: self._run_search(place_id, cancel_event),
            lambda exc: log_buffer.log('subplace', f'Search failed: {exc}'),
        )

    def _fetch_thumbnail_entries(self, url: str, attempt: int) -> list[_ThumbnailEntry] | None:
        try:
            response = self._get(url, timeout=15)
        except (requests.RequestException, TypeError, ValueError) as exc:
            log_buffer.log('subplace', f'Thumbnail batch failed (attempt {attempt + 1}): {exc}')
            return None
        if response.status_code == 429:
            log_buffer.log(
                'subplace',
                f'Thumbnail batch 429 rate-limited (attempt {attempt + 1}), retrying…',
            )
            return None
        try:
            response.raise_for_status()
            payload: object = response.json()
        except (requests.RequestException, TypeError, ValueError) as exc:
            log_buffer.log('subplace', f'Thumbnail batch failed (attempt {attempt + 1}): {exc}')
            return None
        if not isinstance(payload, dict):
            return None
        raw_entries = cast('dict[str, object]', payload).get('data', [])
        if not isinstance(raw_entries, list):
            return None
        entries = cast('list[object]', raw_entries)
        return [cast('_ThumbnailEntry', entry) for entry in entries if isinstance(entry, dict)]

    def _fetch_thumb_bytes_batch(self, place_ids: list[int | str]) -> dict[str, bytes]:
        """Fetch thumbnail image bytes for a batch of place IDs.

        Uses v1/places/gameicons with comma-separated IDs — more reliable than
        v1/batch which has a known bug returning placeholder images for game icons.

        Returns {str(place_id): bytes} for all successfully fetched entries.
        Already-cached entries are returned from cache without a network call.
        Retries the metadata request on 429/5xx and retries failed image downloads.
        """
        str_ids = [str(pid) for pid in place_ids]
        uncached = [sid for sid in str_ids if sid not in self.thumb_cache]
        result = {sid: self.thumb_cache[sid] for sid in str_ids if sid in self.thumb_cache}

        if not uncached:
            return result

        ids_param = ','.join(uncached)
        url = (
            f'https://thumbnails.roblox.com/v1/places/gameicons'
            f'?placeIds={ids_param}&size=512x512&format=Png'
        )

        entries: list[_ThumbnailEntry] = []
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2**attempt)
            fetched_entries = self._fetch_thumbnail_entries(url, attempt)
            if fetched_entries is not None:
                entries = fetched_entries
                break

        # Collect image URLs to download
        to_download: dict[str, str] = {}  # sid → img_url
        for entry in entries:
            target_id = entry.get('targetId')
            img_url = entry.get('imageUrl')
            if target_id and img_url:
                to_download[str(target_id)] = img_url

        # Download image bytes; retry failures once
        failed: dict[str, str] = {}
        for sid, img_url in to_download.items():
            try:
                img_resp = self._get(img_url, timeout=10)
                img_resp.raise_for_status()
                img_bytes = img_resp.content
                self.thumb_cache[sid] = img_bytes
                result[sid] = img_bytes
            except requests.RequestException:
                failed[sid] = img_url

        if failed:
            time.sleep(1)
            for sid, img_url in failed.items():
                try:
                    img_resp = self._get(img_url, timeout=10)
                    img_resp.raise_for_status()
                    img_bytes = img_resp.content
                    self.thumb_cache[sid] = img_bytes
                    result[sid] = img_bytes
                except requests.RequestException as exc:
                    log_buffer.log('subplace', f'Thumbnail download failed for {sid}: {exc}')

        log_buffer.log(
            'subplace',
            f'Batch thumbs: {len(uncached)} requested, {len(entries)} returned, {len(result)} resolved',
        )
        return result

    # Cards

    def _add_new_cards(self, items: list[_CardItem]) -> None:
        existing_names = {c.name_label.text() for c in self._cards}
        added_any = False

        for item in items:
            if len(item) == 5:
                name, created, updated, pid, root = item
            elif len(item) == 4:
                name, created, updated, pid = item
                root = None
            else:
                name, created, updated = item
                pid = root = None

            if name in existing_names:
                continue

            card = SubplaceGameCard(self.results_container)
            card.set_data(name=name, created=created or '', updated=updated or '')
            card.place_id = int(pid) if pid is not None else None
            card.is_root = bool(root is not None and pid is not None and int(pid) == int(root))
            card.created_iso = created
            card.updated_iso = updated

            if pid is not None:
                card.on_join(
                    lambda _, c=card, place_id=pid, root_id=root: self._join_place(
                        place_id, root_id, job_id=c.job_id_edit.get_job_id()
                    )
                )
                card.on_open(
                    lambda _, pid_val=pid: QDesktopServices.openUrl(
                        QUrl(f'https://www.roblox.com/games/{pid_val}')
                    )
                )
                card.on_fetch_jobs(lambda _, pid_val=pid, c=card: self._open_job_ids(pid_val, c))
            else:
                card.join_btn.setEnabled(False)
                card.fetch_jobs_btn.setEnabled(False)
                card.job_id_edit.setEnabled(False)
                card.join_btn.setToolTip(
                    tr('ui.gui.subplace_joiner_tab.join_unavailable_placeid_is_missing_for_this')
                )
                card.fetch_jobs_btn.setToolTip(
                    tr('ui.gui.subplace_joiner_tab.jobids_unavailable_placeid_is_missing_for_this')
                )
                log_buffer.log(
                    'subplace',
                    f'Card created without placeId; join disabled (name={name})',
                )

            self._cards.append(card)
            if pid is not None:
                self._card_by_place_id[int(pid)] = card
            existing_names.add(name)
            added_any = True

        if added_any:
            self.apply_search_and_sort()

    def _update_cards(self, items: list[_CardUpdate]) -> None:
        existing_map = {card.name_label.text(): card for card in self._cards}
        for name, created, updated in items:
            card = existing_map.get(name)
            if not card:
                continue
            card.created_iso = created
            card.updated_iso = updated
            card.set_data(name, _humanize_time(created), _humanize_time(updated))
        self.apply_search_and_sort()

    def clear_results(self) -> None:
        for card in self._cards:
            self.results_grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def apply_search_and_sort(self) -> None:
        text = (self.search_input.text() or '').strip().lower()

        for card in self._cards:
            name = (card.name_label.text() or '').lower()
            pid = getattr(card, 'place_id', None)
            match = not text or (text in name) or (pid is not None and text in str(pid))
            card.setVisible(match)

        mode = str(self.sort_combo.currentData() or 'place_id_asc')
        visible = [c for c in self._cards if c.isVisible()]

        def _iso_ts(iso: str | None) -> float:
            try:
                if not iso:
                    return float('-inf')
                return _dateutil_parser.isoparse(iso).timestamp()
            except TypeError, ValueError, OverflowError:
                return float('-inf')

        if mode.startswith('place_id_'):
            visible.sort(
                key=lambda c: getattr(c, 'place_id', 0) or 0,
                reverse=mode.endswith('_desc'),
            )
        elif mode.startswith('created_'):
            visible.sort(
                key=lambda c: _iso_ts(getattr(c, 'created_iso', None)),
                reverse=mode.endswith('_desc'),
            )
        elif mode.startswith('updated_'):
            visible.sort(
                key=lambda c: _iso_ts(getattr(c, 'updated_iso', None)),
                reverse=mode.endswith('_desc'),
            )

        self._cards = visible + [c for c in self._cards if not c.isVisible()]
        self._place_cards(visible)

    def _get_cols(self) -> int:
        vp = self.results_scroll.viewport()
        available = vp.width() if vp else (self.width() - 30)
        return max(1, available // (_CARD_W + self.results_grid.spacing()))

    def _place_cards(self, visible: list[SubplaceGameCard]) -> None:
        for card in self._cards:
            self.results_grid.removeWidget(card)

        cols = self._get_cols()
        self._last_cols = cols

        for c in range(max(self.results_grid.columnCount(), cols) + 1):
            self.results_grid.setColumnStretch(c, 0)
        for c in range(cols):
            self.results_grid.setColumnStretch(c, 1)

        for i, card in enumerate(visible):
            self.results_grid.addWidget(card, i // cols, i % cols)

    def _on_resize_settled(self) -> None:
        cols = self._get_cols()
        if cols == self._last_cols:
            return
        self._place_cards([c for c in self._cards if c.isVisible()])

    @override
    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._resize_timer.start(60)

    @override
    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self._cards:
            self.apply_search_and_sort()

    # Join

    def _open_job_ids(self, place_id: int | str, card: SubplaceGameCard | None = None) -> None:
        pid = int(place_id)

        def _on_select(job_id: str) -> None:
            if card is not None:
                card.job_id_edit.set_job_id(job_id)

        def _on_cache_update(servers: list[_ServerInfo]) -> None:
            self._jobid_cache[pid] = servers

        dlg = JobIdDialog(
            place_id,
            on_select=_on_select,
            parent=self,
            cached_servers=self._jobid_cache.get(pid),
            on_cache_update=_on_cache_update,
        )
        dlg.show()

    def _join_place(
        self, place_id: int | str, root_place_id: int | str | None = None, job_id: str = ''
    ) -> None:
        self._current_job_id = job_id
        log_buffer.log(
            'subplace',
            f'Joining place ID: {place_id}' + (f' with jobId: {job_id}' if job_id else ''),
        )
        cookie = _get_roblosecurity()
        if root_place_id and int(place_id) != int(root_place_id):
            ok = self._join_root(root_place_id, cookie)
            log_buffer.log(
                'subplace',
                f'Pre-seed join {"succeeded" if ok else "failed"} for root {root_place_id}',
            )
        if (
            self._rando_tab is not None
            and self._rando_tab.is_multi_instance_enabled()
            and _rando_account_switched(self._rando_tab)
        ):
            is_roblox_running = cast(
                'Callable[[], bool]',
                import_module('fleasion.utils.windows').__dict__['is_roblox_running'],
            )

            if is_roblox_running():
                log_buffer.log(
                    'subplace',
                    'Account switched + multi-instance on — launching new Roblox instance then joining',
                )
                self._rando_tab.close_singleton_event()
                self.joining_place = True

                def _launch_with_uri(
                    place_id: int | str = place_id, cookie: str | None = cookie
                ) -> None:
                    ticket = _get_auth_ticket_runtime(cast('str', cookie))
                    if not ticket:
                        log_buffer.log(
                            'subplace',
                            'Failed to get auth ticket for multi-instance join',
                        )
                        return
                    tracker_id = 10_000_000_000 + secrets.randbelow(90_000_000_000)
                    place_launcher_url = (
                        f'https://www.roblox.com/Game/PlaceLauncher.ashx'
                        f'?request=RequestGame'
                        f'&browserTrackerId={tracker_id}'
                        f'&placeId={place_id}'
                        f'&isPlayTogetherGame=false'
                    )
                    roblox_player_uri = (
                        f'roblox-player:1+launchmode:play+gameinfo:{ticket}'
                        f'+launchtime:{int(time.time() * 1000)}'
                        f'+placelauncherurl:{quote(place_launcher_url, safe="")}'
                        f'+browsertrackerid:{tracker_id}+robloxLocale:en_us+gameLocale:en_us'
                        f'+channel:+LaunchExp:InApp'
                    )
                    log_buffer.log(
                        'subplace',
                        f'Launching Roblox URI to placeId={place_id} (multi-instance)',
                    )
                    if not self._launch_roblox_uri(roblox_player_uri):
                        log_buffer.log('subplace', 'Failed to launch Roblox URI without elevation')

                threading.Thread(target=_launch_with_uri, daemon=True).start()
                return
        self.joining_place = True
        log_buffer.log('subplace', f'Launching Roblox deeplink to placeId={place_id}')
        if not self._launch_roblox_uri(f'roblox://experiences/start?placeId={place_id}'):
            log_buffer.log('subplace', 'Failed to launch Roblox deeplink without elevation')

    def _launch_roblox_uri(self, target: str) -> bool:
        """Launch a join URI using the active platform proxy strategy."""
        if sys.platform.startswith('linux'):
            return launch_as_standard_user(target)

        if (
            sys.platform == 'darwin'
            and getattr(self._config_manager, 'proxy_mode', 'hosts') == 'env'
            and getattr(self._config_manager, 'proxy_features_enabled', False)
            and self._proxy_master is not None
        ):
            relaunch_roblox_with_proxy_env = cast(
                'Callable[[str, str], bool]',
                import_module('fleasion.utils.platform_macos').__dict__[
                    'relaunch_roblox_with_proxy_env'
                ],
            )
            return relaunch_roblox_with_proxy_env(self._proxy_master.roblox_env_proxy_url(), target)
        return launch_as_standard_user(target)

    def _post_root_join(self, root_place_id: int | str, cookie: str) -> requests.Response:
        sess = self._new_session(cookie)
        payload = {
            'placeId': int(root_place_id),
            'isTeleport': True,
            'isImmersiveAdsTeleport': False,
            'gameJoinAttemptId': str(uuid.uuid4()),
        }
        join_url = 'https://gamejoin.roblox.com/v1/join-game'
        return sess.post(
            join_url,
            json=payload,
            timeout=15,
            verify=self._request_verify(join_url),
        )

    def _join_root(self, root_place_id: int | str, cookie: str | None = None) -> bool:
        resolved_cookie = cookie if cookie is not None else _get_roblosecurity()
        if not resolved_cookie:
            return False
        try:
            response = self._post_root_join(root_place_id, resolved_cookie)
            payload: object = response.json() if response.status_code == 200 else None
        except (requests.RequestException, TypeError, ValueError) as exc:
            log_buffer.log('subplace', f'Pre-seed join error: {exc}')
            return False
        if not isinstance(payload, dict):
            return False
        return cast('dict[str, object]', payload).get('status') == 2

    def _new_session(self, cookie: str | None) -> requests.Session:
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.headers.update(
            {
                'User-Agent': 'Roblox/WinInet',
                'Content-Type': 'application/json',
                'Accept': 'application/json',
                'Referer': 'https://www.roblox.com/',
                'Origin': 'https://www.roblox.com',
            }
        )
        if cookie:
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        try:
            r = sess.post('https://auth.roblox.com/v2/logout', timeout=10)
            token = r.headers.get('x-csrf-token') or r.headers.get('X-CSRF-TOKEN')
            if token:
                sess.headers['X-CSRF-TOKEN'] = token
        except requests.RequestException:
            pass
        return sess

    # HTTP helpers

    def _request_verify(self, url: str) -> bool | str:
        host = (urlparse(url).hostname or '').strip().lower().rstrip('.')
        proxy_master = self._proxy_master
        if proxy_master is None or not proxy_master.hosts_intercepts_host(host):
            return True
        ca_cert = PROXY_CA_DIR / 'ca.crt'
        return str(ca_cert) if ca_cert.is_file() else True

    def _get(
        self,
        url: str,
        timeout: float = 10,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ) -> requests.Response:
        return requests.get(
            url,
            timeout=timeout,
            proxies={},
            cookies=cookies,
            headers=headers,
            verify=self._request_verify(url),
        )

    def _on_main(self, fn: _MainCallback) -> bool:
        if self._qt_destroyed:
            return False
        invoker = getattr(self, '_invoker', None)
        if invoker is None:
            return False
        try:
            invoker.call.emit(fn)
        except RuntimeError:
            self._qt_destroyed = True
            return False
        return True

    def _on_main_guarded(self, fn: _MainCallback, cancel_event: threading.Event) -> None:
        """Post fn to the main thread, but skip execution if cancel_event is set by then."""

        def wrapped() -> None:
            if not cancel_event.is_set():
                fn()

        self._on_main(wrapped)

    # Proxy interceptor hooks (called by ProxyMaster on gamejoin traffic)

    def request(self, flow: _ProxyFlow) -> None:
        url = flow.request.pretty_url
        parsed_url = urlparse(url)
        content_type = flow.request.headers.get('Content-Type', '').lower()

        if (
            self.joining_place
            and any(p == parsed_url.path for p in self._WANTED_ENDPOINTS)
            and 'gamejoin.roblox.com' in url
            and 'application/json' in content_type
        ):
            try:
                parsed_body = json.loads(flow.request.content)
            except json.JSONDecodeError, UnicodeDecodeError, TypeError:
                return
            if not isinstance(parsed_body, dict):
                return
            body_json = cast('dict[str, object]', parsed_body)
            if 'isTeleport' not in body_json:
                body_json['isTeleport'] = True
                log_buffer.log('subplace', 'Added isTeleport flag')
            job_id = self._current_job_id
            if job_id:
                body_json['gameId'] = job_id
                flow.request.url = 'https://gamejoin.roblox.com/v1/join-game-instance'
                log_buffer.log(
                    'subplace',
                    f'Redirecting to join-game-instance with jobId: {job_id}',
                )
            new_body = json.dumps(body_json, separators=(',', ':')).encode()
            flow.request.raw_content = new_body

    def response(self, flow: _ProxyFlow) -> None:
        url = flow.request.pretty_url
        parsed_url = urlparse(url)

        if self.joining_place and any(p == parsed_url.path for p in self._WANTED_ENDPOINTS):
            if flow.response is None:
                return
            try:
                data = flow.response.json()
            except TypeError, ValueError:
                return
            if data.get('status') == 2:
                self.joining_place = False
