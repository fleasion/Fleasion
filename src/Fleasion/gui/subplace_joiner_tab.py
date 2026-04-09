"""Subplace Joiner tab - browse and join subplaces of any Roblox experience."""

import json
import os
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from dateutil import parser as _dateutil_parser
from PyQt6.QtCore import Qt, QTimer, QObject, pyqtSignal
from PyQt6.QtGui import QPalette, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..utils.paths import CONFIG_DIR
from ..utils.logging import log_buffer
from ..utils.roblox_auth import get_roblosecurity as _get_roblosecurity


# Global rate-limit tracker for the public servers endpoint.
# When a 429 is received, _servers_rl_until is set to now+60s so every
# subsequent dialog that opens knows to wait out the remaining cooldown.
_servers_rl_until: float = 0.0
_servers_rl_lock = threading.Lock()


# Helpers

def _humanize_time(iso_str: str) -> str:
    if not iso_str:
        return "Unknown"
    try:
        dt = _dateutil_parser.isoparse(iso_str)
        now = datetime.now(timezone.utc)
        diff = now - dt
        seconds = diff.total_seconds()
        minutes = seconds / 60
        hours = minutes / 60
        days = hours / 24
        months = days / 30
        years = days / 365
        if seconds < 60:
            return "just now"
        elif minutes < 60:
            return f"{int(minutes)} minute{'s' if minutes >= 2 else ''} ago"
        elif hours < 24:
            return f"{int(hours)} hour{'s' if hours >= 2 else ''} ago"
        elif days < 30:
            return f"{int(days)} day{'s' if days >= 2 else ''} ago"
        elif months < 12:
            return f"{int(months)} month{'s' if months >= 2 else ''} ago"
        else:
            return f"{int(years)} year{'s' if years >= 2 else ''} ago"
    except Exception:
        return iso_str


# Main-thread invoker

class _Invoker(QObject):
    call = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn):
        try:
            fn()
        except Exception as exc:
            import traceback
            log_buffer.log("subplace", f"invoker error: {exc}")
            traceback.print_exc()


# GameCardWidget (inline, PyQt6)

from .prejsons_dialog import _make_rounded_pixmap, _CARD_W, _CARD_H, _THUMB_W, _THUMB_H


class _JobIdEdit(QLineEdit):
    """QLineEdit with placeholder text for an optional JobId."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setPlaceholderText("JobId: (Optional)")
        self.setFixedHeight(20)
        self.setStyleSheet("font-size: 9pt;")

    def get_job_id(self) -> str:
        return self.text().strip()

    def set_job_id(self, job_id: str):
        self.setText(job_id)


class SubplaceGameCard(QFrame):
    """Game card matching the PreJsons visual design, with subplace-joiner buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.place_id = None
        self.is_root = False
        self.created_iso = None
        self.updated_iso = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(_CARD_W)
        self.setFixedHeight(_CARD_H)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._setup_ui()

    def _setup_ui(self):
        from PyQt6.QtGui import QFont
        layout = QVBoxLayout()
        layout.setContentsMargins(7, 7, 7, 7)
        layout.setSpacing(4)

        self.thumb_label = QLabel("Loading…")
        self.thumb_label.setFixedHeight(_THUMB_H)
        self.thumb_label.setMinimumWidth(_THUMB_W)
        self.thumb_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setScaledContents(True)
        self.thumb_label.setStyleSheet(
            "background: palette(alternate-base); border-radius: 4px; color: palette(placeholder-text); font-size: 8pt;"
        )
        layout.addWidget(self.thumb_label)

        self.name_label = QLabel("Unknown")
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(38)
        f = QFont()
        f.setBold(True)
        self.name_label.setFont(f)
        layout.addWidget(self.name_label)

        self.created_label = QLabel("")
        self.created_label.setStyleSheet("color: palette(placeholder-text); font-size: 8pt;")
        layout.addWidget(self.created_label)

        self.updated_label = QLabel("")
        self.updated_label.setStyleSheet("color: palette(placeholder-text); font-size: 8pt;")
        layout.addWidget(self.updated_label)

        layout.addStretch()

        self.job_id_edit = _JobIdEdit()
        layout.addWidget(self.job_id_edit)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)

        self.join_btn = QPushButton("Join")
        self.join_btn.setFixedHeight(22)
        btn_row.addWidget(self.join_btn)

        self.open_btn = QPushButton("Browser")
        self.open_btn.setFixedHeight(22)
        btn_row.addWidget(self.open_btn)

        self.fetch_jobs_btn = QPushButton("JobIds")
        self.fetch_jobs_btn.setFixedHeight(22)
        btn_row.addWidget(self.fetch_jobs_btn)

        layout.addLayout(btn_row)
        self.setLayout(layout)

    def set_data(self, name: str, created: str = "", updated: str = ""):
        self.name_label.setText(name)
        if created:
            self.created_label.setText("Created: " + created)
        if updated:
            self.updated_label.setText("Updated: " + updated)

    def set_thumbnail(self, pix: QPixmap):
        if not pix or pix.isNull():
            return
        try:
            baked = _make_rounded_pixmap(pix, _THUMB_W, _THUMB_H, radius=6)
        except Exception:
            baked = pix
        self.thumb_label.setPixmap(baked)
        self.thumb_label.setText("")
        self.thumb_label.setStyleSheet("background: transparent;")

    def on_join(self, fn):
        self.join_btn.clicked.connect(fn)

    def on_open(self, fn):
        self.open_btn.clicked.connect(fn)

    def on_fetch_jobs(self, fn):
        self.fetch_jobs_btn.clicked.connect(fn)

    def enterEvent(self, event):
        self.setStyleSheet("SubplaceGameCard { background: rgba(255,255,255,0.06); }")
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.setStyleSheet("")
        super().leaveEvent(event)


# JobId Dialog

class JobIdDialog(QDialog):
    """Fetches and displays public server jobIds for a given placeId."""

    _results_ready = pyqtSignal(list, object)   # servers, next_cursor (object so None is allowed)
    _error_ready = pyqtSignal(str)
    _status_update = pyqtSignal(str)

    _PAGE_LIMIT = 25
    _SORT_OPTIONS = [
        ("Players ↑ (fewest first)", "playing_asc"),
        ("Players ↓ (most first)", "playing_desc"),
        ("Ping ↑ (lowest first)", "ping_asc"),
        ("Ping ↓ (highest first)", "ping_desc"),
    ]

    def __init__(self, place_id, on_select=None, parent=None, cached_servers=None, on_cache_update=None):
        super().__init__(parent)
        self._place_id = place_id
        self._on_select = on_select
        self._on_cache_update = on_cache_update
        self._cursor = None
        self._loading = False
        self._all_servers = list(cached_servers) if cached_servers else []

        self._results_ready.connect(self._apply_results)
        self._error_ready.connect(self._apply_error)

        self.setWindowTitle(f"JobIds — Place {place_id}")
        self.resize(520, 440)

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(QLabel("Sort by:"))
        self._sort_combo = QComboBox()
        for label, _ in self._SORT_OPTIONS:
            self._sort_combo.addItem(label)
        self._sort_combo.currentIndexChanged.connect(self._on_sort_changed)
        top.addWidget(self._sort_combo)
        top.addStretch(1)
        layout.addLayout(top)

        self._status_label = QLabel("Fetching servers...")
        layout.addWidget(self._status_label)

        self._status_update.connect(self._status_label.setText)

        self._list = QListWidget()
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self._list, 1)

        bottom = QHBoxLayout()
        self._load_more_btn = QPushButton("Load more")
        self._load_more_btn.clicked.connect(self._fetch_page)
        bottom.addWidget(self._load_more_btn)
        bottom.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        bottom.addWidget(close_btn)
        layout.addLayout(bottom)

        if self._all_servers:
            self._apply_results([], None)  # render cached servers immediately
        self._fetch_page()

    def _current_sort(self):
        return self._SORT_OPTIONS[self._sort_combo.currentIndex()][1]

    def _on_sort_changed(self):
        sort = self._current_sort()
        if sort in ("ping_asc", "ping_desc"):
            # Just re-sort existing data
            self._list.clear()
            for s in self._sorted_servers(self._all_servers):
                job_id = s.get("id", "")
                playing = s.get("playing", "?")
                max_players = s.get("maxPlayers", "?")
                ping = s.get("ping")
                ping_str = f"  {ping}ms" if ping is not None else ""
                item = QListWidgetItem(f"{job_id}  ({playing}/{max_players} players){ping_str}")
                item.setData(Qt.ItemDataRole.UserRole, job_id)
                self._list.addItem(item)
            return
        # For player sorts: fetch new batch with the new sort order and add to existing
        self._cursor = None
        self._fetch_page()

    def _fetch_page(self):
        if self._loading:
            return
        self._loading = True
        self._status_label.setText("Fetching servers...")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        global _servers_rl_until
        RL_WAIT = 60  # seconds to wait after a 429

        try:
            sort = self._current_sort()
            sort_order = "Asc" if sort == "playing_asc" else "Desc"
            url = (
                f"https://games.roblox.com/v1/games/{self._place_id}/servers/Public"
                f"?limit={self._PAGE_LIMIT}&sortOrder={sort_order}&excludeFullGames=false"
            )
            if self._cursor:
                url += f"&cursor={self._cursor}"

            for attempt in range(2):  # initial + one retry after 429
                # Respect the global rate-limit window before making a request.
                # Re-check every second so if another dialog's request succeeds and
                # clears the rate limit, this countdown stops early.
                with _servers_rl_lock:
                    wait_until = _servers_rl_until
                remaining = wait_until - time.time()
                if remaining > 0:
                    for sec in range(int(remaining) + 1, 0, -1):
                        with _servers_rl_lock:
                            if _servers_rl_until != wait_until or _servers_rl_until <= time.time():
                                break  # cleared by a successful request elsewhere
                        self._status_update.emit(f"Rate limited — retrying in {sec}s…")
                        time.sleep(1)
                        if time.time() >= wait_until:
                            break
                    self._status_update.emit("Retrying…")

                resp = requests.get(url, timeout=15, proxies={}, verify=False)
                if resp.status_code == 429:
                    with _servers_rl_lock:
                        _servers_rl_until = max(_servers_rl_until, time.time() + RL_WAIT)
                    if attempt == 0:
                        wait_until = _servers_rl_until
                        for sec in range(RL_WAIT, 0, -1):
                            with _servers_rl_lock:
                                if _servers_rl_until != wait_until or _servers_rl_until <= time.time():
                                    break
                            self._status_update.emit(f"Rate limited — retrying in {sec}s…")
                            time.sleep(1)
                            if time.time() >= wait_until:
                                break
                        self._status_update.emit("Retrying…")
                        continue
                    else:
                        self._error_ready.emit("429 Too Many Requests: Slow down!")
                        return
                resp.raise_for_status()
                # Successful response — clear the global rate-limit state
                with _servers_rl_lock:
                    _servers_rl_until = 0.0
                data = resp.json()
                servers = data.get("data", [])
                next_cursor = data.get("nextPageCursor")
                self._results_ready.emit(servers, next_cursor)
                return
        except Exception as exc:
            self._error_ready.emit(str(exc))

    def _sorted_servers(self, servers):
        sort = self._current_sort()
        if sort == "playing_asc":
            return sorted(servers, key=lambda s: s.get("playing", 0))
        elif sort == "playing_desc":
            return sorted(servers, key=lambda s: s.get("playing", 0), reverse=True)
        elif sort == "ping_asc":
            return sorted(servers, key=lambda s: s.get("ping", 9999))
        elif sort == "ping_desc":
            return sorted(servers, key=lambda s: s.get("ping", 0), reverse=True)
        return servers

    def _apply_results(self, servers, next_cursor):
        self._cursor = next_cursor
        self._loading = False
        existing_ids = {s.get("id") for s in self._all_servers}
        self._all_servers.extend(s for s in servers if s.get("id") not in existing_ids)
        if self._on_cache_update:
            self._on_cache_update(list(self._all_servers))

        self._list.clear()
        for s in self._sorted_servers(self._all_servers):
            job_id = s.get("id", "")
            playing = s.get("playing", "?")
            max_players = s.get("maxPlayers", "?")
            ping = s.get("ping")
            ping_str = f"  {ping}ms" if ping is not None else ""
            item = QListWidgetItem(f"{job_id}  ({playing}/{max_players} players){ping_str}")
            item.setData(Qt.ItemDataRole.UserRole, job_id)
            self._list.addItem(item)

        total = self._list.count()
        if next_cursor:
            self._status_label.setText(f"{total} servers loaded — more available")
        else:
            self._status_label.setText(f"{total} server(s) found")

    def _apply_error(self, err):
        self._loading = False
        is_ratelimit = "429" in str(err)
        err_msg = "Ratelimited: Slow down!" if is_ratelimit else f"Error: {str(err)[:80]}…"
        if self._all_servers:
            self._list.clear()
            for s in self._sorted_servers(self._all_servers):
                job_id = s.get("id", "")
                playing = s.get("playing", "?")
                max_players = s.get("maxPlayers", "?")
                ping = s.get("ping")
                ping_str = f"  {ping}ms" if ping is not None else ""
                item = QListWidgetItem(f"{job_id}  ({playing}/{max_players} players){ping_str}")
                item.setData(Qt.ItemDataRole.UserRole, job_id)
                self._list.addItem(item)
            self._status_label.setText(err_msg)
        else:
            self._status_label.setText(err_msg)

    def _on_item_double_clicked(self, item):
        job_id = item.data(Qt.ItemDataRole.UserRole)
        if self._on_select and job_id:
            self._on_select(job_id)
            self.close()


# Subplace Joiner Tab

class SubplaceJoinerTab(QWidget):
    """Subplace Joiner tab – search, browse, and join subplaces."""

    _WANTED_ENDPOINTS = (
        "/v1/join-game",
        "/v1/join-play-together-game",
        "/v1/join-game-instance",
    )

    def __init__(self, parent=None, rando_tab=None):
        super().__init__(parent)
        self._rando_tab = rando_tab
        self._invoker = _Invoker(self)
        self._cards: list[SubplaceGameCard] = []
        self._card_by_place_id: dict[int, SubplaceGameCard] = {}
        self.thumb_cache: dict = {}
        self._search_cancel_event = threading.Event()
        self.joining_place = False
        self._current_job_id: str = ""
        self._jobid_cache: dict[int, list] = {}  # place_id -> cached servers
        self._place_name_cache: dict[str, str] = {}  # place_id -> game name

        self.recent_ids: list[str] = []
        self.favorites: list[str] = []

        self._resize_timer = QTimer()
        self._resize_timer.setSingleShot(True)
        self._resize_timer.timeout.connect(self._on_resize_settled)
        self._last_cols = 0

        self._setup_ui()
        self._load_settings()
        self._rebuild_recent_buttons()
        self._rebuild_favorite_buttons()
        self._update_favorite_btn()

    # UI setup

    def _setup_ui(self):
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
        placeid_lbl = QLabel("PlaceID:")
        row0.addWidget(placeid_lbl, 0)
        self.PlaceID_search = QLineEdit()
        self.PlaceID_search.setPlaceholderText("Place ID to search")
        self.PlaceID_search.returnPressed.connect(self.on_search_clicked)
        self.PlaceID_search.textChanged.connect(self._update_favorite_btn)
        row0.addWidget(self.PlaceID_search, 1)
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self.on_search_clicked)
        row0.addWidget(self.search_btn, 0)
        top_layout.addLayout(row0)

        row1 = QHBoxLayout()
        row1.setSpacing(4)
        search_lbl = QLabel("Search:")
        search_lbl.setMinimumWidth(placeid_lbl.sizeHint().width())
        row1.addWidget(search_lbl, 0)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter by name or ID")
        self.search_input.textChanged.connect(self.apply_search_and_sort)
        row1.addWidget(self.search_input, 1)
        self.favorite_btn = QPushButton("Favorite")
        self.favorite_btn.clicked.connect(self.on_favorite_clicked)
        row1.addWidget(self.favorite_btn, 0)
        sort_combo = QComboBox()
        for item in ("PlaceID ↑", "PlaceID ↓", "Created ↑", "Created ↓", "Updated ↑", "Updated ↓"):
            sort_combo.addItem(item)
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

        recent_label = QLabel("Recent PlaceIDs")
        recent_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar.addWidget(recent_label)

        self._recent_scroll = QScrollArea()
        self._recent_scroll.setFixedWidth(200)
        self._recent_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._recent_scroll.setWidgetResizable(True)
        self.recent_contents = QWidget()
        self.recent_contents.setObjectName("RecentPlaceIdsContents")
        self.recent_contents.setAutoFillBackground(True)
        self.recent_contents.setBackgroundRole(QPalette.ColorRole.Base)
        self.recent_layout = QVBoxLayout(self.recent_contents)
        self.recent_layout.setContentsMargins(2, 2, 2, 2)
        self.recent_layout.setSpacing(2)
        self._recent_scroll.setWidget(self.recent_contents)
        sidebar.addWidget(self._recent_scroll, 1)

        fav_label = QLabel("Favorited PlaceIDs")
        fav_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sidebar.addWidget(fav_label)

        self._fav_scroll = QScrollArea()
        self._fav_scroll.setFixedWidth(200)
        self._fav_scroll.setWidgetResizable(True)
        self.fav_contents = QWidget()
        self.fav_contents.setObjectName("FavoritedPlaceIdsContents")
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
        self.results_scroll.setObjectName("Results")
        self.results_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results_scroll.setWidgetResizable(True)

        self.results_container = QWidget()
        self.results_container.setObjectName("resultsContainer")
        self.results_container.setAutoFillBackground(True)
        self.results_container.setBackgroundRole(QPalette.ColorRole.Base)
        self.results_grid = QGridLayout(self.results_container)
        self.results_grid.setContentsMargins(8, 8, 8, 8)
        self.results_grid.setSpacing(8)
        self.results_grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.results_scroll.setWidget(self.results_container)
        main_layout.addWidget(self.results_scroll, 1)

        root.addLayout(main_layout, 1)

    # Settings persistence

    def _settings_path(self) -> str:
        folder = CONFIG_DIR / "subplace"
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder / "settings.json")

    def _load_settings(self):
        path = self._settings_path()
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.recent_ids = [str(x) for x in data.get("recent_ids", []) if str(x).strip()]
                self.favorites = [str(x) for x in data.get("favorites", []) if str(x).strip()]
        except Exception as exc:
            log_buffer.log("subplace", f"Failed to load settings: {exc}")
            self.recent_ids = []
            self.favorites = []

    def _save_settings(self):
        path = self._settings_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"recent_ids": self.recent_ids, "favorites": self.favorites}, f, indent=2)
        except Exception as exc:
            log_buffer.log("subplace", f"Failed to save settings: {exc}")

    # Recent / Favorites sidebar

    def _clear_layout_buttons(self, layout):
        if not layout:
            return
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _fetch_place_name(self, place_id: str, callback):
        if place_id in self._place_name_cache:
            callback(self._place_name_cache[place_id])
            return
        def _worker():
            try:
                cookie = _get_roblosecurity() or ""
                r = self._get(
                    f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}",
                    timeout=10,
                    cookies={".ROBLOSECURITY": cookie} if cookie else None)
                r.raise_for_status()
                data = r.json()
                name = data[0].get("name", place_id) if data else place_id
            except Exception:
                name = place_id
            self._place_name_cache[place_id] = name
            self._on_main(lambda n=name: callback(n))
        threading.Thread(target=_worker, daemon=True).start()

    def _make_placeid_button(self, place_id: str, handler):
        btn = QPushButton(place_id)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.clicked.connect(lambda _=False, pid=place_id: handler(pid))
        def _set_name(name, b=btn):
            try:
                elided = b.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, 170)
                b.setText(elided.replace('&', '&&'))
                b.setToolTip(name)
            except RuntimeError:
                pass
        self._fetch_place_name(place_id, _set_name)
        return btn

    def _rebuild_recent_buttons(self):
        self._clear_layout_buttons(self.recent_layout)
        for pid in self.recent_ids:
            self.recent_layout.addWidget(
                self._make_placeid_button(pid, self._on_recent_clicked))
        self.recent_layout.addStretch(1)

    def _rebuild_favorite_buttons(self):
        self._clear_layout_buttons(self.fav_layout)
        for pid in self.favorites:
            self.fav_layout.addWidget(
                self._make_placeid_button(pid, self._on_favorite_clicked))
        self.fav_layout.addStretch(1)

    def _on_recent_clicked(self, place_id: str):
        self.PlaceID_search.setText(place_id)
        self.on_search_clicked()

    def _on_favorite_clicked(self, place_id: str):
        self.PlaceID_search.setText(place_id)
        self.on_search_clicked()

    def add_recent_place_id(self, place_id: str):
        place_id = (place_id or "").strip()
        if not place_id.isdigit():
            return
        if place_id in self.recent_ids:
            self.recent_ids.remove(place_id)
        self.recent_ids.insert(0, place_id)
        self._save_settings()
        self._rebuild_recent_buttons()

    def _update_favorite_btn(self):
        pid = self._extract_place_id(self.PlaceID_search.text())
        self.favorite_btn.setText("Unfavorite" if pid in self.favorites else "Favorite")

    def on_favorite_clicked(self):
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
            path = urlparse(text).path
            parts = path.strip("/").split("/")
            if "games" in parts:
                idx = parts.index("games")
                candidate = parts[idx + 1] if idx + 1 < len(parts) else ""
                if candidate.isdigit():
                    return candidate
        except Exception:
            pass
        return text

    def on_search_clicked(self):
        place_id = self._extract_place_id(self.PlaceID_search.text())
        if not place_id.isdigit():
            log_buffer.log("subplace", "Invalid Place ID")
            return
        self.PlaceID_search.setText(place_id)

        log_buffer.log("subplace", f"Searching for Place ID: {place_id}")
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

    def _search_worker(self, place_id: str, cancel_event: threading.Event):
        try:
            if cancel_event.is_set():
                return

            u = self._get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe", timeout=10)
            u.raise_for_status()
            universe_id = u.json().get("universeId")
            if not universe_id:
                raise Exception("Invalid Place ID or universe not found")

            details = self._get(f"https://games.roblox.com/v1/games?universeIds={universe_id}", timeout=10)
            details.raise_for_status()
            games_data = details.json().get("data", [])
            root_place_id = games_data[0].get("rootPlaceId") if games_data else int(place_id)

            all_places = []
            cursor = None
            seen: set = set()

            while True:
                if cancel_event.is_set():
                    return
                url = f"https://develop.roblox.com/v1/universes/{universe_id}/places?limit=100"
                if cursor:
                    url += f"&cursor={cursor}"
                r = self._get(url, timeout=10)
                r.raise_for_status()
                data = r.json()
                batch = data.get("data", [])
                if not batch:
                    break
                for p in batch:
                    pid = p.get("id")
                    if pid in seen:
                        continue
                    seen.add(pid)
                    p["display_name"] = p.get("name") or f"Place {pid}"
                    p["created"] = None
                    p["updated"] = None
                    p["is_root"] = int(pid) == int(root_place_id)
                    all_places.append(p)
                cursor = data.get("nextPageCursor")
                if not cursor:
                    break

            log_buffer.log("subplace", f"Found {len(all_places)} places")

            items = [
                (p["display_name"], p.get("created"), p.get("updated"), p["id"], root_place_id)
                for p in all_places
            ]
            self._on_main(lambda: self._add_new_cards(items))

            cookie = _get_roblosecurity() or ""

            def load_timestamps():
                updated = []
                for i, p in enumerate(all_places):
                    if cancel_event.is_set():
                        return
                    pid = p.get("id")
                    while True:
                        try:
                            resp = self._get(
                                f"https://economy.roblox.com/v2/assets/{pid}/details",
                                cookies={".ROBLOSECURITY": cookie}, timeout=10)
                            resp.raise_for_status()
                            asset_data = resp.json()
                            p["created"] = asset_data.get("Created")
                            p["updated"] = asset_data.get("Updated")
                            break
                        except requests.HTTPError as err:
                            status = getattr(err.response, "status_code", None)
                            if status in (429, 500, 502, 503, 504):
                                time.sleep(1)
                                continue
                            break
                        except Exception:
                            break
                    updated.append(p)
                    if (i + 1) % 5 == 0 or i == len(all_places) - 1:
                        pc = [(p["display_name"], p.get("created"), p.get("updated")) for p in updated.copy()]
                        self._on_main(lambda x=pc: self._update_cards(x))

            threading.Thread(target=load_timestamps, daemon=True).start()

            def load_thumbnails():
                BATCH_SIZE = 100
                pending = [p for p in all_places if p.get("id")]
                for chunk_start in range(0, len(pending), BATCH_SIZE):
                    if cancel_event.is_set():
                        return
                    chunk = pending[chunk_start:chunk_start + BATCH_SIZE]
                    place_ids = [p["id"] for p in chunk]
                    try:
                        thumb_map = self._fetch_thumb_bytes_batch(place_ids)
                    except Exception as exc:
                        log_buffer.log("subplace", f"Batch thumbnail fetch failed: {exc}")
                        continue
                    for pid_val, img_bytes in thumb_map.items():
                        if img_bytes:
                            pid_int = int(pid_val)
                            def apply_pix(pid=pid_int, data=img_bytes):
                                card = self._card_by_place_id.get(pid)
                                if card:
                                    pix = QPixmap()
                                    if pix.loadFromData(data):
                                        card.set_thumbnail(pix)
                            self._on_main(apply_pix)

            threading.Thread(target=load_thumbnails, daemon=True).start()

        except Exception as exc:
            log_buffer.log("subplace", f"Search failed: {exc}")

    def _fetch_thumb_bytes_batch(self, place_ids: list) -> dict:
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

        ids_param = ",".join(uncached)
        url = (
            f"https://thumbnails.roblox.com/v1/places/gameicons"
            f"?placeIds={ids_param}&size=512x512&format=Png"
        )

        entries = []
        for attempt in range(3):
            if attempt > 0:
                time.sleep(2 ** attempt)
            try:
                resp = self._get(url, timeout=15)
                if resp.status_code == 429:
                    log_buffer.log("subplace", f"Thumbnail batch 429 rate-limited (attempt {attempt + 1}), retrying…")
                    continue
                resp.raise_for_status()
                entries = resp.json().get("data", [])
                break
            except Exception as exc:
                log_buffer.log("subplace", f"Thumbnail batch failed (attempt {attempt + 1}): {exc}")

        # Collect image URLs to download
        to_download: dict[str, str] = {}  # sid → img_url
        for entry in entries:
            target_id = entry.get("targetId")
            img_url = entry.get("imageUrl")
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
            except Exception:
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
                except Exception as exc:
                    log_buffer.log("subplace", f"Thumbnail download failed for {sid}: {exc}")

        log_buffer.log(
            "subplace",
            f"Batch thumbs: {len(uncached)} requested, {len(entries)} returned, {len(result)} resolved",
        )
        return result

    # Cards

    def _add_new_cards(self, items):
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
            card.set_data(name=name, created=created or "", updated=updated or "")
            card.place_id = int(pid) if pid is not None else None
            card.is_root = bool(root is not None and pid is not None and int(pid) == int(root))
            card.created_iso = created
            card.updated_iso = updated

            if pid is not None:
                card.on_join(lambda _, c=card, place_id=pid, root_id=root: self._join_place(place_id, root_id, job_id=c.job_id_edit.get_job_id()))
                card.on_open(lambda _, pid_val=pid: os.startfile(
                    f"https://www.roblox.com/games/{pid_val}"))
                card.on_fetch_jobs(lambda _, pid_val=pid, c=card: self._open_job_ids(pid_val, c))

            self._cards.append(card)
            if pid is not None:
                self._card_by_place_id[int(pid)] = card
            existing_names.add(name)
            added_any = True

        if added_any:
            self.apply_search_and_sort()

    def _update_cards(self, items):
        existing_map = {card.name_label.text(): card for card in self._cards}
        for name, created, updated in items:
            card = existing_map.get(name)
            if not card:
                continue
            card.created_iso = created
            card.updated_iso = updated
            card.set_data(name, _humanize_time(created), _humanize_time(updated))
        self.apply_search_and_sort()

    def clear_results(self):
        for card in self._cards:
            self.results_grid.removeWidget(card)
            card.setParent(None)
            card.deleteLater()
        self._cards.clear()

    def apply_search_and_sort(self):
        text = (self.search_input.text() or "").strip().lower()

        for card in self._cards:
            name = (card.name_label.text() or "").lower()
            pid = getattr(card, "place_id", None)
            match = not text or (text in name) or (pid is not None and text in str(pid))
            card.setVisible(match)

        mode = (self.sort_combo.currentText() or "").strip()
        visible = [c for c in self._cards if c.isVisible()]

        def _iso_ts(iso):
            try:
                if not iso:
                    return float("-inf")
                return _dateutil_parser.isoparse(iso).timestamp()
            except Exception:
                return float("-inf")

        if "PlaceID" in mode:
            visible.sort(key=lambda c: getattr(c, "place_id", 0) or 0, reverse="↓" in mode)
        elif "Created" in mode:
            visible.sort(key=lambda c: _iso_ts(getattr(c, "created_iso", None)), reverse="↓" in mode)
        elif "Updated" in mode:
            visible.sort(key=lambda c: _iso_ts(getattr(c, "updated_iso", None)), reverse="↓" in mode)

        self._cards = visible + [c for c in self._cards if not c.isVisible()]
        self._place_cards(visible)

    def _get_cols(self) -> int:
        vp = self.results_scroll.viewport()
        available = vp.width() if vp else (self.width() - 30)
        return max(1, available // (_CARD_W + self.results_grid.spacing()))

    def _place_cards(self, visible: list):
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

    def _on_resize_settled(self):
        cols = self._get_cols()
        if cols == self._last_cols:
            return
        self._place_cards([c for c in self._cards if c.isVisible()])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._resize_timer.start(60)

    # Join

    def _open_job_ids(self, place_id, card=None):
        pid = int(place_id)
        def _on_select(job_id):
            if card is not None:
                card.job_id_edit.set_job_id(job_id)
        def _on_cache_update(servers):
            self._jobid_cache[pid] = servers
        dlg = JobIdDialog(
            place_id, on_select=_on_select, parent=self,
            cached_servers=self._jobid_cache.get(pid),
            on_cache_update=_on_cache_update,
        )
        dlg.show()

    def _join_place(self, place_id, root_place_id=None, job_id: str = ""):
        self._current_job_id = job_id
        log_buffer.log("subplace", f"Joining place ID: {place_id}" + (f" with jobId: {job_id}" if job_id else ""))
        cookie = _get_roblosecurity()
        if root_place_id and int(place_id) != int(root_place_id):
            ok = self._join_root(root_place_id, cookie)
            log_buffer.log("subplace", f"Pre-seed join {'succeeded' if ok else 'failed'} for root {root_place_id}")
        url = f"roblox://experiences/start?placeId={place_id}"
        # If multi-instance is enabled, Roblox is already running, and the account was
        # switched -+ do exactly what the Launch button does: os.startfile(exe) to open
        # a fresh Roblox instance, then fire the deeplink once it has had time to boot.
        if (self._rando_tab is not None
                and self._rando_tab.is_multi_instance_enabled()
                and self._rando_tab._account_switched):
            from ..utils.windows import is_roblox_running
            if is_roblox_running():
                exe = self._rando_tab.get_roblox_exe()
                if exe:
                    log_buffer.log("subplace", "Account switched + multi-instance on — launching new Roblox instance then joining")
                    self._rando_tab.close_singleton_event()
                    self.joining_place = True
                    def _launch_then_join(exe=exe, url=url):
                        def _count_roblox():
                            try:
                                out = subprocess.check_output(
                                    ['tasklist', '/FI', 'IMAGENAME eq RobloxPlayerBeta.exe'],
                                    text=True, creationflags=0x08000000)
                                return out.lower().count('robloxplayerbeta.exe')
                            except Exception:
                                return 0
                        before = _count_roblox()
                        os.startfile(exe)
                        for _ in range(60):  # poll up to 30 s
                            time.sleep(0.5)
                            if _count_roblox() > before:
                                break
                        time.sleep(0.5)
                        os.startfile(url)
                    threading.Thread(target=_launch_then_join, daemon=True).start()
                    return
        self.joining_place = True
        os.startfile(url)

    def _join_root(self, root_place_id: int, cookie: str | None = None) -> bool:
        try:
            if cookie is None:
                cookie = _get_roblosecurity()
            if not cookie:
                return False
            sess = self._new_session(cookie)
            payload = {
                "placeId": int(root_place_id),
                "isTeleport": True,
                "isImmersiveAdsTeleport": False,
                "gameJoinAttemptId": str(uuid.uuid4()),
            }
            r = sess.post("https://gamejoin.roblox.com/v1/join-game", json=payload, timeout=15)
            try:
                return r.status_code == 200 and r.json().get("status") == 2
            except Exception:
                return False
        except Exception as exc:
            log_buffer.log("subplace", f"Pre-seed join error: {exc}")
            return False

    def _new_session(self, cookie: str | None):
        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        sess.verify = False
        sess.headers.update({
            "User-Agent": "Roblox/WinInet",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Referer": "https://www.roblox.com/",
            "Origin": "https://www.roblox.com",
        })
        if cookie:
            sess.headers["Cookie"] = f".ROBLOSECURITY={cookie};"
        try:
            r = sess.post("https://auth.roblox.com/v2/logout", timeout=10)
            token = r.headers.get("x-csrf-token") or r.headers.get("X-CSRF-TOKEN")
            if token:
                sess.headers["X-CSRF-TOKEN"] = token
        except Exception:
            pass
        return sess

    # HTTP helpers

    def _get(self, url, timeout=10, headers=None, cookies=None):
        r = requests.get(url, timeout=timeout, proxies={},
                         cookies=cookies, headers=headers, verify=False)
        return r

    def _on_main(self, fn):
        self._invoker.call.emit(fn)

    # Proxy interceptor hooks (called by ProxyMaster on gamejoin traffic)

    def request(self, flow):
        url = flow.request.pretty_url
        parsed_url = urlparse(url)
        content_type = flow.request.headers.get("Content-Type", "").lower()

        if (self.joining_place and
                any(p == parsed_url.path for p in self._WANTED_ENDPOINTS) and
                "gamejoin.roblox.com" in url and
                "application/json" in content_type):
            try:
                body_json = json.loads(flow.request.content)
            except Exception:
                return
            if "isTeleport" not in body_json:
                body_json["isTeleport"] = True
                log_buffer.log("subplace", "Added isTeleport flag")
            job_id = self._current_job_id
            if job_id:
                body_json["gameId"] = job_id
                flow.request.url = "https://gamejoin.roblox.com/v1/join-game-instance"
                log_buffer.log("subplace", f"Redirecting to join-game-instance with jobId: {job_id}")
            new_body = json.dumps(body_json, separators=(",", ":")).encode()
            flow.request.raw_content = new_body

    def response(self, flow):
        url = flow.request.pretty_url
        parsed_url = urlparse(url)

        if self.joining_place and any(p == parsed_url.path for p in self._WANTED_ENDPOINTS):
            if flow.response is None:
                return
            try:
                data = flow.response.json()
                if data.get("status") == 2:
                    self.joining_place = False
            except Exception:
                pass
