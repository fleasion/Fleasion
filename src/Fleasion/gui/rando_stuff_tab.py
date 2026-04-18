"""Rando Stuff tab - miscellaneous Roblox utilities (multi-instance, asset download, rejoin)."""

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import os
import random
import re
import uuid
import threading
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote

import requests as _requests

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QCheckBox,
    QRadioButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import win32crypt  # type: ignore
except Exception:
    win32crypt = None

from ..utils.paths import CONFIG_DIR
from ..utils.logging import log_buffer
from ..utils.windows import launch_as_standard_user, resolve_roblox_player_exe_for_launch

ACCOUNTS_FILE = CONFIG_DIR / 'accounts.json'


# Helpers


def _encrypt_cookie(cookie: str) -> str:
    """Encrypt a cookie string with DPAPI and return a base64 string for storage."""
    raw = cookie.encode("utf-8")
    if win32crypt:
        enc = win32crypt.CryptProtectData(raw, None, None, None, None, 0)
        return base64.b64encode(enc).decode("ascii")
    # Fallback: plain base64 (no encryption available)
    return base64.b64encode(raw).decode("ascii")


def _decrypt_cookie(enc_b64: str) -> str | None:
    """Decrypt a stored cookie string. Returns plain cookie or None on failure."""
    try:
        enc = base64.b64decode(enc_b64)
        if win32crypt:
            return win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1].decode("utf-8")
        return enc.decode("utf-8")
    except Exception:
        return None


def _load_accounts() -> list[dict]:
    """Load accounts list from disk."""
    try:
        if ACCOUNTS_FILE.exists():
            return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return []


def _save_accounts(accounts: list[dict]):
    """Persist accounts list to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding="utf-8")



def _get_auth_ticket(cookie: str) -> str | None:
    """Fetch a Roblox authentication ticket using the user's cookie."""
    url = "https://auth.roblox.com/v1/authentication-ticket"
    headers = {
        "Cookie": f".ROBLOSECURITY={cookie}",
        "Referer": "https://www.roblox.com",
        "Content-Type": "application/json",
    }
    try:
        # First request — Roblox returns 403 with X-CSRF-TOKEN on POST endpoints
        resp = _requests.post(url, headers=headers, json={}, timeout=10)
        if resp.status_code == 403 and "x-csrf-token" in resp.headers:
            headers["X-CSRF-TOKEN"] = resp.headers["x-csrf-token"]
            resp = _requests.post(url, headers=headers, json={}, timeout=10)
        if resp.status_code == 200:
            return resp.headers.get("rbx-authentication-ticket")
    except Exception:
        pass
    return None


def _get_access_code(place_id: str, link_code: str, cookie: str) -> str | None:
    """Resolve a privateServerLinkCode to the UUID accessCode.

    Tries the games API first, then falls back to parsing the game page HTML
    (the approach used by Roblox Account Manager).
    """
    sess = _requests.Session()
    sess.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
    sess.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })

    # games.roblox.com API (fastest path)
    for url in (
        f"https://games.roblox.com/v1/private-servers?serverLinkCode={link_code}",
        f"https://games.roblox.com/v1/private-servers/{link_code}",
    ):
        try:
            resp = sess.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("accessCode") or data.get("vipServerAccessCode")
                if code:
                    return code
        except Exception:
            pass

    # Fall back to parsing game page HTML
    try:
        resp = sess.get(
            f"https://www.roblox.com/games/{place_id}",
            params={"privateServerLinkCode": link_code},
            headers={"Referer": "https://www.roblox.com/games/4924922222/Brookhaven-RP"},
            timeout=15,
        )
        for pat in (
            r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*'([\w-]+)'",
            r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*\"([\w-]+)\"",
            r'"accessCode"\s*:\s*"([\w-]{36})"',
        ):
            m = re.search(pat, resp.text)
            if m:
                return m.group(1)
    except Exception:
        pass

    return None



def _parse_game_link(link: str) -> tuple[str | None, str | None]:
    """Parse any Roblox game URL and return (place_id, link_code_or_None).

    Accepts:
    - Plain numeric placeId, e.g. "1818"
    - Full game URL, e.g. https://www.roblox.com/games/1818/Classic-Crossroads
    - Private server URL with privateServerLinkCode query param
    """
    if not link:
        return None, None
    # Plain numeric placeId
    if link.isdigit():
        return link, None
    try:
        parsed = urlparse(link)
        parts = [p for p in parsed.path.split('/') if p]
        place_id = None
        if 'games' in parts:
            idx = parts.index('games')
            if idx + 1 < len(parts) and parts[idx + 1].isdigit():
                place_id = parts[idx + 1]
        link_code = parse_qs(parsed.query).get('privateServerLinkCode', [None])[0]
        if place_id:
            return place_id, link_code
    except Exception:
        pass
    return None, None


def _is_share_link(link: str) -> bool:
    """Return True if link is a roblox.com/share?code=...&type=Server link."""
    if not link:
        return False
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        return (
            "roblox.com" in parsed.netloc
            and parsed.path.rstrip('/') == "/share"
            and "code" in qs
        )
    except Exception:
        return False


def _resolve_share_link(link: str, cookie: str = "") -> tuple[str, str]:
    """Resolve a roblox.com/share link via the sharelinks API."""
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        link_id = (qs.get("code") or [None])[0]
        link_type = (qs.get("type") or ["Server"])[0]
        if not link_id:
            return "", ""

        sess = _requests.Session()
        if cookie:
            sess.cookies.set(".ROBLOSECURITY", cookie, domain=".roblox.com")
        sess.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.roblox.com/",
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
        })

        body = {"linkId": link_id, "linkType": link_type}
        resp = sess.post(
            "https://apis.roblox.com/sharelinks/v1/resolve-link",
            json=body,
            timeout=10,
        )
        # Roblox returns 403 with X-CSRF-TOKEN on first POST — retry with token
        if resp.status_code == 403 and "x-csrf-token" in resp.headers:
            sess.headers["X-CSRF-TOKEN"] = resp.headers["x-csrf-token"]
            resp = sess.post(
                "https://apis.roblox.com/sharelinks/v1/resolve-link",
                json=body,
                timeout=10,
            )
        if resp.status_code != 200:
            return "", ""

        data = resp.json()

        def _extract(d: dict) -> tuple[str, str]:
            pid = str(d.get("placeId") or d.get("rootPlaceId") or "")
            lc = d.get("privateServerLinkCode") or d.get("linkCode") or d.get("accessCode") or ""
            return pid, lc

        place_id, link_code = _extract(data)
        for key in ("privateServerInviteData", "privateServerData", "gameDetails", "serverData"):
            nested = data.get(key)
            if isinstance(nested, dict):
                p, l = _extract(nested)
                place_id = place_id or p
                link_code = link_code or l

        if place_id and link_code:
            return place_id, link_code
    except Exception:
        pass
    return "", ""


def _find_roblox_exe() -> str | None:
    """Return best Roblox executable path using shared resolver fallbacks."""
    exe_path = resolve_roblox_player_exe_for_launch()
    return str(exe_path) if exe_path is not None else None


# Add / Change Cookie dialog

class AddAccountDialog(QDialog):
    """Dialog for pasting a .ROBLOSECURITY cookie and validating it."""

    _validated = pyqtSignal(str, str)   # username, cookie
    _failed = pyqtSignal(str)           # error message

    def __init__(self, parent=None, title="Add Account"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(500)
        self.result_username: str | None = None
        self.result_cookie: str | None = None
        self._validated.connect(self._on_validated)
        self._failed.connect(self._on_failed)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Paste your .ROBLOSECURITY cookie:"))

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText(
            "_|WARNING:-DO-NOT-SHARE-THIS.--Sharing-this-will-allow-someone-to-log-in-as-you-and-to-steal-your-ROBUX-and-items.|_..."
        )
        self._input.setFixedHeight(70)
        layout.addWidget(self._input)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        self._ok_btn = QPushButton("Add")
        self._ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def set_ok_label(self, text: str):
        self._ok_btn.setText(text)

    def _on_ok(self):
        cookie = self._input.toPlainText().strip()
        if not cookie:
            self._status.setText("Please paste a cookie.")
            return
        self._ok_btn.setEnabled(False)
        self._status.setText("Validating…")
        threading.Thread(target=self._validate, args=(cookie,), daemon=True).start()

    def _validate(self, cookie: str):
        try:
            sess = _requests.Session()
            sess.trust_env = False
            sess.proxies = {}
            try:
                sess.cookies.set(".ROBLOSECURITY", cookie)
            except Exception:
                sess.headers["Cookie"] = f".ROBLOSECURITY={cookie};"
            resp = sess.get(
                "https://users.roblox.com/v1/users/authenticated",
                timeout=10,
            )
            if resp.status_code == 200:
                username = resp.json().get("name", "Unknown")
                self._validated.emit(username, cookie)
            else:
                self._failed.emit(f"Invalid cookie (HTTP {resp.status_code}).")
        except Exception as exc:
            self._failed.emit(f"Error: {exc}")

    def _on_validated(self, username: str, cookie: str):
        self.result_username = username
        self.result_cookie = cookie
        self.accept()

    def _on_failed(self, msg: str):
        self._status.setText(msg)
        self._ok_btn.setEnabled(True)


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
            log_buffer.log("randostuff", f"invoker error: {exc}")


# Tab widget

class RandoStuffTab(QWidget):
    """Rando Stuff tab – proxy interceptor + UI combined."""

    selected_account_changed = pyqtSignal(str)

    _WANTED_ENDPOINTS = (
        "/v1/join-game",
        "/v1/join-play-together-game",
        "/v1/join-game-instance",
    )

    def __init__(self, parent=None, config_manager=None):
        super().__init__(parent)
        self._config = config_manager
        self._invoker = _Invoker(self)

        self._last_place_id = None
        self._last_access_code = None
        self._last_session_id = None
        self._doing_rejoin = False
        self._awaiting_rejoin_response = False
        self._active_rejoin_attempt_id = None  # gameJoinAttemptId being redirected
        loaded_subplace_blacklist = []
        loaded_subplace_mode = 'block'
        if self._config is not None:
            loaded_subplace_blacklist = getattr(self._config, 'subplace_blacklist', [])
            loaded_subplace_mode = getattr(self._config, 'subplace_blacklist_mode', 'block')
        self._subplace_blacklisted_ids: set[str] = set(
            self._parse_numeric_id_list(','.join(str(x) for x in loaded_subplace_blacklist))
        )
        self._subplace_block_mode = 'stall' if loaded_subplace_mode == 'stall' else 'block'
        self._blocked_subplace_log_at: dict[str, float] = {}
        self._subplace_unblock_until = 0.0
        self._lock = threading.Lock()

        self._multi_stop = threading.Event()
        self._multi_thread = None
        self._account_switched = False
        self._last_switched_account: dict | None = None

        self._accounts: list[dict] = _load_accounts()
        self._game_jobs: dict = {}  # placeId -> jobId, session-only memory
        self._account_manager_job_id: str = ""
        self._account_manager_capture_place_id: str | None = None
        self._auto_filled_for_place: str | None = None

        self._setup_ui()
        if self._config is not None:
            enabled = self._config.multi_instance_launching
            self._multi_chk.blockSignals(True)
            self._multi_chk.setChecked(enabled)
            self._multi_chk.blockSignals(False)
            if enabled:
                self._on_multi_instance_toggled(True, persist=False)
        threading.Thread(target=self._check_cookies_on_boot, daemon=True).start()
        threading.Thread(target=self._resolve_current_user, daemon=True).start()

        if self._subplace_blacklisted_ids:
            count = len(self._subplace_blacklisted_ids)
            log_buffer.log('subplace', f'Loaded subplace blacklist: {count} ID(s) active')

    @staticmethod
    def _normalize_numeric_id(value) -> str | None:
        try:
            return str(int(str(value).strip()))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _parse_numeric_id_list(cls, raw_value: str) -> list[str]:
        content = raw_value.replace('\n', ',').replace(';', ',').replace(' ', ',')
        ids = []
        for part in content.split(','):
            normalized = cls._normalize_numeric_id(part)
            if normalized is not None:
                ids.append(normalized)
        return ids

    def _is_subplace_blacklisted(self, place_id) -> bool:
        normalized = self._normalize_numeric_id(place_id)
        return normalized is not None and normalized in self._subplace_blacklisted_ids

    def _drop_subplace_join(self, flow, place_id: str, attempt_id: str | None = None):
        with self._lock:
            mode = self._subplace_block_mode

        if mode == 'stall':
            payload = {
                'jobId': None,
                'status': 1,
                'joinScriptUrl': None,
                'authenticationUrl': None,
                'authenticationTicket': None,
                'message': '',
                'joinScript': None,
                'queuePosition': 0,
            }
            log_interval = 10.0
        else:
            payload = {
                'jobId': None,
                'status': 12,
                'joinScriptUrl': None,
                'authenticationUrl': None,
                'authenticationTicket': None,
                'message': 'Teleport blocked by Subplace Blacklist.',
                'joinScript': None,
                'queuePosition': 0,
            }
            log_interval = 5.0

        flow.drop_request = True
        flow.drop_status_code = 200
        flow.drop_body = json.dumps(payload, separators=(',', ':')).encode('utf-8')

        key = f'{place_id}:{attempt_id or ""}'
        now = time.time()
        last = self._blocked_subplace_log_at.get(key, 0.0)
        if now - last >= log_interval:
            self._blocked_subplace_log_at[key] = now
            if len(self._blocked_subplace_log_at) > 512:
                cutoff = now - 30.0
                self._blocked_subplace_log_at = {
                    k: ts for k, ts in self._blocked_subplace_log_at.items() if ts >= cutoff
                }
            log_buffer.log('subplace', f'Blocked join request to blacklisted subplace ID: {place_id}')

    def _set_subplace_block_mode(self, mode: str, checked: bool):
        if not checked:
            return
        with self._lock:
            self._subplace_block_mode = mode
        if self._config is not None:
            self._config.subplace_blacklist_mode = mode
        if mode == 'stall':
            log_buffer.log('subplace', 'Subplace blacklist mode: Infinitely Stall Subplace')
        else:
            log_buffer.log('subplace', 'Subplace blacklist mode: Block Subplace')

    def _is_subplace_unblock_active(self) -> bool:
        with self._lock:
            return time.time() < self._subplace_unblock_until

    def _on_subplace_unblock_for_5s(self):
        with self._lock:
            self._subplace_unblock_until = time.time() + 5.0
        log_buffer.log('subplace', 'Subplace blacklist bypass enabled for 5 seconds')

    # UI

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 0, 0)
        root.setSpacing(8)

        rejoin_group = QGroupBox("Reserved Server Rejoin")
        rjl = QVBoxLayout(rejoin_group)

        btn_row = QHBoxLayout()
        self._btn = QPushButton("Rejoin Reserved Server")
        btn_row.addWidget(self._btn)
        help_btn = QPushButton("?")
        _btn_h = self._btn.sizeHint().height()
        help_btn.setFixedSize(_btn_h, _btn_h)
        help_btn.setToolTip("What is a reserved server?")
        help_btn.clicked.connect(self._show_reserved_server_help)
        btn_row.addWidget(help_btn)
        btn_row.addStretch()
        rjl.addLayout(btn_row)

        self._lbl_place = QLabel("Last placeID = ")
        self._lbl_place.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._lbl_access = QLabel("Last accessCode = ")
        self._lbl_access.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rjl.addWidget(self._lbl_place)
        rjl.addWidget(self._lbl_access)

        self._lbl_timer = QLabel("Timer: —")
        rjl.addWidget(self._lbl_timer)

        self._rejoin_timer = QTimer(self)
        self._rejoin_timer.setInterval(1000)
        self._rejoin_timer.timeout.connect(self._tick_rejoin_timer)
        self._rejoin_timer_secs = 0

        root.addWidget(rejoin_group)

        mi_group = QGroupBox("Multi-Instance")
        mil = QVBoxLayout(mi_group)
        self._multi_chk = QCheckBox("Enable Multi-Instance launching")
        mil.addWidget(self._multi_chk)
        root.addWidget(mi_group)

        am_group = QGroupBox("Account Manager")
        aml = QVBoxLayout(am_group)

        self._account_list = QListWidget()
        self._account_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._account_list.customContextMenuRequested.connect(self._on_account_ctx_menu)
        aml.addWidget(self._account_list)

        self._selected_label = QLabel("Selected: (none)")
        self._selected_label.setStyleSheet("color: palette(placeholder-text); font-size: 9pt;")
        aml.addWidget(self._selected_label)

        self._private_server_input = QLineEdit()
        self._private_server_input.setPlaceholderText(
            'Game link, e.g. https://www.roblox.com/games/123/Name or ...?privateServerLinkCode=AbCd...'
        )
        self._private_server_input.textChanged.connect(self._on_game_link_changed)
        aml.addWidget(self._private_server_input)

        self._job_id_input = QLineEdit()
        self._job_id_input.setPlaceholderText("JobId: (Optional)")
        aml.addWidget(self._job_id_input)

        am_btns = QHBoxLayout()
        self._add_acct_btn = QPushButton("Add Account")
        self._add_acct_btn.clicked.connect(self._on_add_account)
        self._launch_acct_btn = QPushButton("Launch")
        self._launch_acct_btn.clicked.connect(self._on_launch_account)
        self._switch_acct_btn = QPushButton("Switch to selected")
        self._switch_acct_btn.clicked.connect(self._on_switch_account)
        am_btns.addWidget(self._add_acct_btn)
        am_btns.addWidget(self._launch_acct_btn)
        am_btns.addWidget(self._switch_acct_btn)
        am_btns.addStretch()
        aml.addLayout(am_btns)

        root.addWidget(am_group)

        self._populate_account_list()

        ac_group = QGroupBox("R6 ↔ R15 Animation Converter")
        acl = QVBoxLayout(ac_group)

        import_row = QHBoxLayout()
        self._ac_import_btn = QPushButton("Import .rbxmx / .rbxm…")
        self._ac_import_btn.clicked.connect(self._ac_import)
        self._ac_file_lbl = QLabel("No file loaded")
        self._ac_file_lbl.setWordWrap(True)
        import_row.addWidget(self._ac_import_btn)
        import_row.addWidget(self._ac_file_lbl, 1)
        acl.addLayout(import_row)

        self._ac_rig_lbl = QLabel("Detected rig: —")
        acl.addWidget(self._ac_rig_lbl)

        conv_row = QHBoxLayout()
        self._ac_to_r15_btn = QPushButton("Convert R6 → R15")
        self._ac_to_r15_btn.setEnabled(False)
        self._ac_to_r15_btn.clicked.connect(lambda: self._ac_convert('R15'))
        self._ac_to_r6_btn = QPushButton("Convert R15 → R6")
        self._ac_to_r6_btn.setEnabled(False)
        self._ac_to_r6_btn.clicked.connect(lambda: self._ac_convert('R6'))
        conv_row.addWidget(self._ac_to_r15_btn)
        conv_row.addWidget(self._ac_to_r6_btn)
        conv_row.addStretch()
        acl.addLayout(conv_row)

        self._ac_status_lbl = QLabel("")
        acl.addWidget(self._ac_status_lbl)

        root.addWidget(ac_group)

        subplace_blacklist_group = QGroupBox('Subplace Blacklist')
        subplace_blacklist_layout = QVBoxLayout(subplace_blacklist_group)
        subplace_blacklist_row = QHBoxLayout()
        self._subplace_blacklist_btn = QPushButton('Blacklist Subplaces...')
        self._subplace_blacklist_btn.clicked.connect(self._show_subplace_blacklist_dialog)
        subplace_blacklist_row.addWidget(self._subplace_blacklist_btn)
        self._subplace_unblock_btn = QPushButton('Unblock For 5s')
        self._subplace_unblock_btn.clicked.connect(self._on_subplace_unblock_for_5s)
        subplace_blacklist_row.addWidget(self._subplace_unblock_btn)
        subplace_blacklist_row.addStretch()
        subplace_blacklist_layout.addLayout(subplace_blacklist_row)

        self._subplace_block_radio = QRadioButton('Block Subplace')
        self._subplace_stall_radio = QRadioButton('Infinitely Stall Subplace')
        if self._subplace_block_mode == 'stall':
            self._subplace_stall_radio.setChecked(True)
        else:
            self._subplace_block_radio.setChecked(True)
        self._subplace_block_radio.toggled.connect(
            lambda checked: self._set_subplace_block_mode('block', checked)
        )
        self._subplace_stall_radio.toggled.connect(
            lambda checked: self._set_subplace_block_mode('stall', checked)
        )
        subplace_blacklist_layout.addWidget(self._subplace_block_radio)
        subplace_blacklist_layout.addWidget(self._subplace_stall_radio)
        root.addWidget(subplace_blacklist_group)

        root.addStretch()

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton('Clear Cache')
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)
        root.addWidget(footer_widget)

        # Connections
        self._btn.clicked.connect(self._on_rejoin_clicked)
        self._multi_chk.toggled.connect(self._on_multi_instance_toggled)

    def _clear_roblox_cache(self):
        from .delete_cache import DeleteCacheWindow
        window = DeleteCacheWindow()
        window.show()

    # Rejoin

    def _on_rejoin_clicked(self):
        with self._lock:
            if self._last_place_id is None or self._last_access_code is None:
                log_buffer.log("randostuff", "No reserved server logged yet — join one first.")
                return
            self._doing_rejoin = True
        log_buffer.log("randostuff", f"Rejoin triggered — placeId={self._last_place_id}")
        if not launch_as_standard_user(f"roblox://placeId={self._last_place_id}"):
            log_buffer.log("randostuff", "Failed to launch Roblox without elevation")

    def _update_labels(self, place_id, access_code):
        def _do():
            self._lbl_place.setText(f"Last placeID = {place_id}")
            self._lbl_access.setText(f"Last accessCode = {access_code.replace('&', '&&')}")
            self._rejoin_timer_secs = 300
            self._lbl_timer.setText("Timer: 5:00")
            self._rejoin_timer.start()
        self._invoker.call.emit(_do)

    def _tick_rejoin_timer(self):
        self._rejoin_timer_secs -= 1
        if self._rejoin_timer_secs <= 0:
            self._rejoin_timer.stop()
            self._lbl_timer.setText("Timer: Expired!")
        else:
            m, s = divmod(self._rejoin_timer_secs, 60)
            self._lbl_timer.setText(f"Timer: {m}:{s:02d}")

    def _show_reserved_server_help(self):
        msg = QMessageBox(self)
        msg.setWindowTitle("Reserved Server Info")
        msg.setText(
            "<b>What the hell is a reserved server???</b><br><br>"
            "A reserved server is basically a private server but that can be made at any time by the server, "
            "they are typically used in subplaces to prevent other people from joining you or from other people "
            "ending up in your servers. Take Doors for example, when you join a game in Doors you get sent a "
            "reserved server.<br><br>"
            "<b>How does this work?</b><br><br>"
            "It works by scanning APIs coming in and out of your client, it specifically looks for "
            "gamejoin.roblox.com APIs. It then keeps track of the accessCode and placeId of said server "
            "and when you click the button it deeplinks and intercepts the gamejoin.roblox.com API from "
            "the deeplink to join the reserved server.<br><br>"
            "<b>Note:</b> The access code is only valid for 5 minutes after being teleported to the reserved server by the server."
        )
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.exec()

    def _show_subplace_blacklist_dialog(self):
        from ..utils import get_icon_path

        dialog = QDialog(self)
        dialog.setWindowTitle('Blacklist Subplace...')
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            from PyQt6.QtGui import QIcon
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel('Blacklisted Subplace IDs')
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        hint = QLabel('Enter subplace IDs separated by commas, spaces, newlines, or semicolons.')
        hint.setStyleSheet('color: gray; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlaceholderText('e.g. 1818, 1234567890, 9876543210')

        if self._subplace_blacklisted_ids:
            text_edit.setPlainText(', '.join(sorted(self._subplace_blacklisted_ids, key=lambda x: int(x))))
        layout.addWidget(text_edit)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_edit = QLineEdit()
        search_edit.setPlaceholderText('Search IDs')
        search_layout.addWidget(search_edit)
        search_layout.addStretch()
        status_label = QLabel('')
        status_label.setStyleSheet('color: #888; font-size: 9pt;')
        search_layout.addWidget(status_label)
        apply_btn = QPushButton('Apply blacklist')
        search_layout.addWidget(apply_btn)
        layout.addLayout(search_layout)

        dialog.setLayout(layout)

        _last_search_query = ['']

        def _search_id():
            query = search_edit.text().strip()
            if not query:
                return
            doc = text_edit.document()
            if doc is None:
                return
            if query != _last_search_query[0]:
                _last_search_query[0] = query
                text_edit.moveCursor(text_edit.textCursor().MoveOperation.Start)
            cursor = doc.find(query, text_edit.textCursor())
            if cursor.isNull():
                cursor = doc.find(query)
            if not cursor.isNull():
                text_edit.setTextCursor(cursor)
                text_edit.ensureCursorVisible()
                status_label.setText('')
            else:
                status_label.setText(f'ID {query} not found.')
                status_label.setStyleSheet('color: #cc5555; font-size: 9pt;')

        search_edit.returnPressed.connect(_search_id)
        search_edit.textChanged.connect(lambda: status_label.setText(''))

        def _apply():
            ids = self._parse_numeric_id_list(text_edit.toPlainText().strip())
            self._subplace_blacklisted_ids = set(ids)
            if self._config is not None:
                self._config.subplace_blacklist = ids
            count = len(self._subplace_blacklisted_ids)
            status_label.setText(f'Blacklist applied: {count} ID(s).')
            status_label.setStyleSheet('color: #55cc55; font-size: 9pt;')
            if self._subplace_blacklisted_ids:
                ordered = ', '.join(sorted(self._subplace_blacklisted_ids, key=lambda x: int(x) if x.isdigit() else 0))
                log_buffer.log('subplace', f'Subplace blacklist updated: {count} ID(s) active - {ordered}')
            else:
                log_buffer.log('subplace', 'Subplace blacklist cleared')

        apply_btn.clicked.connect(_apply)

        dialog.exec()

    # Multi-instance

    def _on_multi_instance_toggled(self, checked, persist=True):
        if persist and self._config is not None:
            self._config.multi_instance_launching = checked
        if checked:
            self._multi_stop.clear()
            self._multi_thread = threading.Thread(target=self._multi_instance_loop, daemon=True)
            self._multi_thread.start()
            log_buffer.log("multiinstance", "Enabled — watching for ROBLOX_singletonEvent")
        else:
            self._multi_stop.set()
            log_buffer.log("multiinstance", "Disabled")

    def _multi_instance_loop(self):
        stripped_pids: set = set()
        while not self._multi_stop.wait(0.2):
            try:
                current_pids = self._get_roblox_pids()
                
                # Only strip singletons if there is more than 1 instance running.
                if len(current_pids) > 1:
                    for pid in current_pids - stripped_pids:
                        log_buffer.log("multiinstance", f"Multiple PIDs detected ({len(current_pids)}). Stripping singleton for PID {pid}")
                        threading.Thread(
                            target=self._close_singleton_for_pid,
                            args=(pid,),
                            daemon=True,
                        ).start()
                        stripped_pids.add(pid)
                
                # Clean up to prevent building up old PIDs
                stripped_pids.intersection_update(current_pids)

            except Exception as exc:
                log_buffer.log("multiinstance", f"Error: {exc}")

    def _get_roblox_pids(self) -> set:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.restype = wintypes.BOOL

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ('dwSize',              wintypes.DWORD),
                ('cntUsage',            wintypes.DWORD),
                ('th32ProcessID',       wintypes.DWORD),
                ('th32DefaultHeapID',   ctypes.c_size_t),
                ('th32ModuleID',        wintypes.DWORD),
                ('cntThreads',          wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase',      ctypes.c_long),
                ('dwFlags',             wintypes.DWORD),
                ('szExeFile',           ctypes.c_wchar * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snap:
            return set()
        pids = set()
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    if 'robloxplayerbeta' in pe.szExeFile.lower():
                        pids.add(pe.th32ProcessID)
                    if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(snap)
        return pids

    def _close_singleton_for_pid(self, pid: int):
        """Retry closing ROBLOX_singletonEvent in `pid` until found or process exits/stop set."""
        while not self._multi_stop.is_set():
            try:
                if self._scan_and_close_singleton(pid):
                    return
            except Exception as exc:
                log_buffer.log("multiinstance", f"Error scanning PID {pid}: {exc}")
                return
            self._multi_stop.wait(0.1)

    def _scan_and_close_singleton(self, pid: int) -> bool:
        """Scan `pid` for a ROBLOX_singletonEvent handle and close it. Returns True if closed."""
        ntdll     = ctypes.windll.ntdll
        kernel32  = ctypes.windll.kernel32
        kernelbase = ctypes.windll.kernelbase

        kernel32.OpenEventW.restype    = wintypes.HANDLE
        kernel32.OpenProcess.restype   = wintypes.HANDLE
        kernel32.DuplicateHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.restype   = wintypes.BOOL
        kernelbase.CompareObjectHandles.restype = wintypes.BOOL
        ntdll.NtQueryInformationProcess.restype = ctypes.c_ulong

        SYNCHRONIZE              = 0x00100000
        PROCESS_DUP_HANDLE       = 0x0040
        PROCESS_QUERY_INFORMATION = 0x0400
        DUPLICATE_CLOSE_SOURCE   = 0x00000001
        DUPLICATE_SAME_ACCESS    = 0x00000002
        STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
        STATUS_SUCCESS           = 0x00000000
        ProcessHandleInformation = 51

        class _ProcHandleEntry(ctypes.Structure):
            _fields_ = [
                ('HandleValue',      ctypes.c_size_t),
                ('HandleCount',      ctypes.c_size_t),
                ('PointerCount',     ctypes.c_size_t),
                ('GrantedAccess',    wintypes.ULONG),
                ('ObjectTypeIndex',  wintypes.ULONG),
                ('HandleAttributes', wintypes.ULONG),
                ('Reserved',         wintypes.ULONG),
            ]

        entry_size  = ctypes.sizeof(_ProcHandleEntry)
        header_size = ctypes.sizeof(ctypes.c_size_t) * 2
        current_proc = ctypes.c_void_p(-1)

        our_handle = kernel32.OpenEventW(SYNCHRONIZE, False, 'ROBLOX_singletonEvent')
        if not our_handle:
            return False  # event doesn't exist yet

        proc = kernel32.OpenProcess(PROCESS_DUP_HANDLE | PROCESS_QUERY_INFORMATION, False, pid)
        if not proc:
            kernel32.CloseHandle(our_handle)
            raise RuntimeError(f"OpenProcess failed for PID {pid} — process may have exited")

        found = False
        try:
            size = 4096
            while True:
                buf = (ctypes.c_ubyte * size)()
                ret_len = wintypes.ULONG(0)
                status = ntdll.NtQueryInformationProcess(
                    proc, ProcessHandleInformation, buf, size, ctypes.byref(ret_len))
                if status == STATUS_INFO_LENGTH_MISMATCH:
                    size = ret_len.value + 4096
                    continue
                break

            if status != STATUS_SUCCESS:
                return False

            buf_bytes = bytes(buf)
            num = ctypes.c_size_t.from_buffer_copy(buf_bytes[:ctypes.sizeof(ctypes.c_size_t)]).value
            offset = header_size
            for _ in range(num):
                e = _ProcHandleEntry.from_buffer_copy(buf_bytes[offset:offset + entry_size])
                offset += entry_size

                dup = wintypes.HANDLE()
                if not kernel32.DuplicateHandle(proc, wintypes.HANDLE(e.HandleValue),
                                                current_proc, ctypes.byref(dup),
                                                0, False, DUPLICATE_SAME_ACCESS):
                    continue

                is_same = kernelbase.CompareObjectHandles(our_handle, dup)
                kernel32.CloseHandle(dup)
                if not is_same:
                    continue

                dup2 = wintypes.HANDLE()
                kernel32.DuplicateHandle(proc, wintypes.HANDLE(e.HandleValue),
                                         current_proc, ctypes.byref(dup2),
                                         0, False, DUPLICATE_CLOSE_SOURCE)
                kernel32.CloseHandle(dup2)
                log_buffer.log("multiinstance", f"Closed ROBLOX_singletonEvent in PID {pid}")
                found = True
                break
        finally:
            kernel32.CloseHandle(proc)
            kernel32.CloseHandle(our_handle)

        return found

    def _close_singleton_event(self):
        """One-shot: close ROBLOX_singletonEvent in all current Roblox processes."""
        for pid in self._get_roblox_pids():
            try:
                self._scan_and_close_singleton(pid)
            except Exception as exc:
                log_buffer.log("multiinstance", f"Error in PID {pid}: {exc}")

    # Account Manager

    def _on_game_link_changed(self, text: str):
        place_id, link_code = _parse_game_link(text.strip())
        if place_id and not link_code:
            # Normal game link — auto-fill stored jobId if field is empty or was auto-filled
            stored_job = self._game_jobs.get(place_id, "")
            current = self._job_id_input.text().strip()
            if not current or self._auto_filled_for_place is not None:
                self._job_id_input.setText(stored_job)
                self._auto_filled_for_place = place_id if stored_job else None
        elif link_code:
            # Private server link — clear any auto-filled jobId
            if self._auto_filled_for_place is not None:
                self._job_id_input.clear()
                self._auto_filled_for_place = None
        else:
            if self._auto_filled_for_place is not None:
                self._job_id_input.clear()
                self._auto_filled_for_place = None

    def _set_selected_account(self, username: str):
        username = (username or '').strip()
        if not username:
            self._selected_label.setText('Selected: (none)')
            return
        self._selected_label.setText(f'Selected: {username}')
        self.selected_account_changed.emit(username)

    def _resolve_current_user(self):
        """Background thread: read the active Roblox cookie and update the selected label."""
        from ..utils.roblox_auth import get_roblosecurity as _get_roblosecurity
        cookie = _get_roblosecurity()
        if not cookie:
            return
        try:
            sess = _requests.Session()
            sess.trust_env = False
            sess.proxies = {}
            try:
                sess.cookies.set(".ROBLOSECURITY", cookie)
            except Exception:
                sess.headers["Cookie"] = f".ROBLOSECURITY={cookie};"
            resp = sess.get("https://users.roblox.com/v1/users/authenticated", timeout=10)
            if resp.status_code == 200:
                username = resp.json().get("name", "")
                if username:
                    def _update(u=username):
                        self._set_selected_account(u)
                    self._invoker.call.emit(_update)
        except Exception:
            pass

    def _check_cookies_on_boot(self):
        """Background thread: validate every stored cookie and flag expired ones in the list."""
        for idx, acc in enumerate(self._accounts):
            cookie = _decrypt_cookie(acc.get("cookie", ""))
            expired = not cookie
            if not expired:
                try:
                    sess = _requests.Session()
                    sess.trust_env = False
                    sess.proxies = {}
                    try:
                        sess.cookies.set(".ROBLOSECURITY", cookie)
                    except Exception:
                        sess.headers["Cookie"] = f".ROBLOSECURITY={cookie};"
                    resp = sess.get(
                        "https://users.roblox.com/v1/users/authenticated",
                        timeout=10,
                    )
                    expired = resp.status_code != 200
                except Exception:
                    pass  # Network error — don't mark as expired
            if expired:
                def _mark(i=idx):
                    item = self._account_list.item(i)
                    if item:
                        item.setText("Expired! Right click to update.")
                self._invoker.call.emit(_mark)

    def _populate_account_list(self):
        self._account_list.clear()
        for acc in self._accounts:
            item = QListWidgetItem(acc.get("username", "(unknown)"))
            self._account_list.addItem(item)

    def _on_add_account(self):
        dlg = AddAccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        username = dlg.result_username
        cookie = dlg.result_cookie
        if not username or not cookie:
            return
        self._accounts.append({"username": username, "cookie": _encrypt_cookie(cookie)})
        _save_accounts(self._accounts)
        self._populate_account_list()
        # Select the newly added entry
        self._account_list.setCurrentRow(len(self._accounts) - 1)

    def _on_account_ctx_menu(self, pos):
        item = self._account_list.itemAt(pos)
        if item is None:
            return
        idx = self._account_list.row(item)
        menu = QMenu(self)
        change_action = menu.addAction("Change Cookie")
        remove_action = menu.addAction("Remove")
        action = menu.exec(self._account_list.viewport().mapToGlobal(pos))
        if action == change_action:
            self._change_cookie(idx)
        elif action == remove_action:
            self._remove_account(idx)

    def _change_cookie(self, idx: int):
        dlg = AddAccountDialog(self, title="Change Cookie")
        dlg.set_ok_label("Update")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        username = dlg.result_username
        cookie = dlg.result_cookie
        if not username or not cookie:
            return
        self._accounts[idx] = {"username": username, "cookie": _encrypt_cookie(cookie)}
        _save_accounts(self._accounts)
        self._populate_account_list()
        self._account_list.setCurrentRow(idx)

    def _remove_account(self, idx: int):
        username = self._accounts[idx].get("username", "(unknown)")
        reply = QMessageBox.question(
            self,
            "Remove Account",
            f"Remove '{username}' from the list?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._accounts.pop(idx)
        _save_accounts(self._accounts)
        self._populate_account_list()

    def _on_launch_account(self):
        log_buffer.log("accounts", "Launch button clicked")
        acc = self._last_switched_account
        if acc is None:
            log_buffer.log("accounts", "Launch aborted: no switched account selected")
            QMessageBox.information(self, "No Account Switched",
                                    "Use 'Switch to selected' first to pick an account.")
            return
        cookie = _decrypt_cookie(acc.get("cookie", ""))
        if not cookie:
            log_buffer.log("accounts", "Launch aborted: failed to decrypt cookie")
            QMessageBox.warning(self, "Error", "Could not decrypt the stored cookie.")
            return
        username = acc.get("username", "(unknown)")
        link = self._private_server_input.text().strip()
        job_id = self._job_id_input.text().strip()
        log_buffer.log(
            "accounts",
            f"Launch request prepared for {username}: hasLink={'yes' if bool(link) else 'no'}, hasJobId={'yes' if bool(job_id) else 'no'}",
        )

        if _is_share_link(link):
            self._launch_acct_btn.setEnabled(False)
            self._launch_acct_btn.setText("Resolving…")
            def _resolve_thread():
                place_id, link_code = _resolve_share_link(link, cookie)
                def _done():
                    self._launch_acct_btn.setEnabled(True)
                    self._launch_acct_btn.setText("Launch")
                    if place_id and link_code:
                        resolved = f"https://www.roblox.com/games/{place_id}/game?privateServerLinkCode={link_code}"
                        self._private_server_input.setText(resolved)
                        threading.Thread(
                            target=self._launch_account_thread,
                            args=(cookie, username, resolved, job_id),
                            daemon=True,
                        ).start()
                    else:
                        QMessageBox.warning(
                            self,
                            "Unsupported Link Format",
                            "This looks like a Roblox share link:\n"
                            f"  {link}\n\n"
                            "Paste it into your browser first — it will redirect to the full "
                            "private server link (with privateServerLinkCode=…). "
                            "Copy that URL and paste it here instead.",
                        )
                self._invoker.call.emit(_done)
            threading.Thread(target=_resolve_thread, daemon=True).start()
            return

        threading.Thread(
            target=self._launch_account_thread,
            args=(cookie, username, link, job_id),
            daemon=True,
        ).start()

    def _on_switch_account(self):
        idx = self._account_list.currentRow()
        if idx < 0:
            QMessageBox.information(self, "No Selection", "Select an account first.")
            return
        acc = self._accounts[idx]
        cookie = _decrypt_cookie(acc.get("cookie", ""))
        if not cookie:
            QMessageBox.warning(self, "Error", "Could not decrypt the stored cookie.")
            return
        username = acc.get("username", "(unknown)")
        try:
            self._write_cookie_to_dat(cookie)
            self._last_switched_account = acc
            self._set_selected_account(username)
            log_buffer.log("accounts", f"Switched Roblox cookie to account: {username}")
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Failed to write cookie: {exc}")

    def _launch_account_thread(self, cookie: str, username: str, private_server_link: str = "", job_id: str = ""):
        try:
            self._write_cookie_to_dat(cookie)
        except Exception as exc:
            log_buffer.log("accounts", f"Failed to write cookie file: {exc}")

        exe = _find_roblox_exe()
        if not exe:
            log_buffer.log("accounts", "Roblox executable resolution failed before launch")
            QTimer.singleShot(0, lambda: QMessageBox.warning(
                self, "Roblox Not Found",
                "Could not locate RobloxPlayerBeta.exe. Is Roblox installed?"
            ))
            return
        log_buffer.log("accounts", f"Resolved Roblox executable: {exe}")

        place_id, link_code = _parse_game_link(private_server_link)
        log_buffer.log(
            "accounts",
            f"Launch parse result: placeId={place_id or '(none)'}, linkCode={'present' if bool(link_code) else 'missing'}, jobId={'present' if bool(job_id) else 'missing'}",
        )
        launch_ok = False
        if place_id and link_code:
            # Private server launch
            ticket = _get_auth_ticket(cookie)
            if ticket:
                access_code = _get_access_code(place_id, link_code, cookie) or link_code
                tracker_id = random.randint(10_000_000_000, 99_999_999_999)
                place_launcher_url = (
                    f"https://www.roblox.com/Game/PlaceLauncher.ashx"
                    f"?request=RequestPrivateGame"
                    f"&browserTrackerId={tracker_id}"
                    f"&placeId={place_id}"
                    f"&accessCode={access_code}"
                    f"&linkCode={link_code}"
                    f"&joinAttemptId={uuid.uuid4()}"
                )
                roblox_player_uri = (
                    f"roblox-player:1+launchmode:play+gameinfo:{ticket}"
                    f"+launchtime:{int(time.time() * 1000)}"
                    f"+placelauncherurl:{quote(place_launcher_url, safe='')}"
                    f"+browsertrackerid:{tracker_id}+robloxLocale:en_us+gameLocale:en_us"
                    f"+channel:+LaunchExp:InApp"
                )
                log_buffer.log("accounts", f"Launching Roblox URI to placeId={place_id} (private server)")
                launch_ok = launch_as_standard_user(roblox_player_uri)
                if not launch_ok:
                    log_buffer.log("accounts", "Failed to launch Roblox URI without elevation")
            else:
                log_buffer.log("accounts", "Failed to get auth ticket, falling back to deeplink")
                deeplink = f"roblox://experiences/start?placeId={place_id}&linkCode={link_code}"
                log_buffer.log("accounts", f"Launching Roblox executable fallback: {exe}")
                exe_started = launch_as_standard_user(exe)
                if not exe_started:
                    log_buffer.log("accounts", "Failed to launch RobloxPlayerBeta.exe without elevation")
                time.sleep(3)
                log_buffer.log("accounts", f"Launching Roblox deeplink to placeId={place_id} with linkCode")
                deeplink_started = launch_as_standard_user(deeplink)
                if not deeplink_started:
                    log_buffer.log("accounts", "Failed to launch Roblox deeplink without elevation")
                launch_ok = exe_started and deeplink_started
        elif place_id:
            # Normal game link — optionally join a specific job
            ticket = _get_auth_ticket(cookie)
            if ticket:
                tracker_id = random.randint(10_000_000_000, 99_999_999_999)
                if job_id:
                    request_type = "RequestGameJob"
                    extra = f"&gameId={job_id}"
                else:
                    request_type = "RequestGame"
                    extra = ""
                    with self._lock:
                        self._account_manager_capture_place_id = place_id
                place_launcher_url = (
                    f"https://www.roblox.com/Game/PlaceLauncher.ashx"
                    f"?request={request_type}"
                    f"&browserTrackerId={tracker_id}"
                    f"&placeId={place_id}"
                    f"{extra}"
                    f"&joinAttemptId={uuid.uuid4()}"
                )
                roblox_player_uri = (
                    f"roblox-player:1+launchmode:play+gameinfo:{ticket}"
                    f"+launchtime:{int(time.time() * 1000)}"
                    f"+placelauncherurl:{quote(place_launcher_url, safe='')}"
                    f"+browsertrackerid:{tracker_id}+robloxLocale:en_us+gameLocale:en_us"
                    f"+channel:+LaunchExp:InApp"
                )
                if job_id:
                    log_buffer.log("accounts", f"Launching Roblox URI to placeId={place_id}, gameId={job_id}")
                else:
                    log_buffer.log("accounts", f"Launching Roblox URI to placeId={place_id}")
                launch_ok = launch_as_standard_user(roblox_player_uri)
                if not launch_ok:
                    log_buffer.log("accounts", "Failed to launch Roblox URI without elevation")
            else:
                log_buffer.log("accounts", "Failed to get auth ticket, falling back to deeplink")
                if not job_id:
                    with self._lock:
                        self._account_manager_capture_place_id = place_id
                    # Proxy intercept will handle jobId capture; set pending job ID to empty
                    with self._lock:
                        self._account_manager_job_id = ""
                else:
                    with self._lock:
                        self._account_manager_job_id = job_id
                deeplink = f"roblox://experiences/start?placeId={place_id}"
                log_buffer.log("accounts", f"Launching Roblox executable fallback: {exe}")
                exe_started = launch_as_standard_user(exe)
                if not exe_started:
                    log_buffer.log("accounts", "Failed to launch RobloxPlayerBeta.exe without elevation")
                time.sleep(3)
                log_buffer.log("accounts", f"Launching Roblox deeplink to placeId={place_id}")
                deeplink_started = launch_as_standard_user(deeplink)
                if not deeplink_started:
                    log_buffer.log("accounts", "Failed to launch Roblox deeplink without elevation")
                launch_ok = exe_started and deeplink_started
        else:
            log_buffer.log("accounts", f"Launching Roblox executable: {exe}")
            launch_ok = launch_as_standard_user(exe)
            if not launch_ok:
                log_buffer.log("accounts", "Failed to launch RobloxPlayerBeta.exe without elevation")
        if launch_ok:
            log_buffer.log("accounts", f"Launched Roblox for account: {username}")
        else:
            log_buffer.log("accounts", f"Launch failed for account: {username}")

    def _write_cookie_to_dat(self, cookie: str):
        """Replace the .ROBLOSECURITY value in RobloxCookies.dat and re-encrypt."""
        if not win32crypt:
            log_buffer.log("accounts", "win32crypt unavailable — cannot update cookie file")
            return
        path = os.path.expandvars(r"%LocalAppData%\Roblox\LocalStorage\RobloxCookies.dat")
        if not os.path.exists(path):
            log_buffer.log("accounts", "RobloxCookies.dat not found — launch Roblox once first")
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        cookies_b64 = data.get("CookiesData", "")
        if not cookies_b64:
            return
        enc = base64.b64decode(cookies_b64)
        dec_bytes = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
        # Use latin-1 for a lossless decode/encode round-trip (preserves all byte values)
        dec_str = dec_bytes.decode("latin-1")
        # Replace the existing .ROBLOSECURITY value (use lambda to avoid regex injection)
        new_str, n = re.subn(r'(\.ROBLOSECURITY\s+)[^\s;]+', lambda m: m.group(1) + cookie, dec_str)
        if n == 0:
            # Value wasn't found - append it (unusual but safe fallback)
            new_str = dec_str.rstrip() + f"\n.ROBLOSECURITY\t{cookie}"
        new_enc = win32crypt.CryptProtectData(new_str.encode("latin-1"), None, None, None, None, 0)
        data["CookiesData"] = base64.b64encode(new_enc).decode("ascii")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self._account_switched = True

    def is_multi_instance_enabled(self) -> bool:
        """Return True if the multi-instance checkbox is checked."""
        return self._multi_chk.isChecked()

    def close_singleton_event(self):
        """Close the Roblox singleton event to allow a new instance, then clear the switched flag."""
        try:
            self._close_singleton_event()
        except Exception as exc:
            log_buffer.log("multiinstance", f"close_singleton_event error: {exc}")
        self._account_switched = False

    def get_roblox_exe(self) -> str | None:
        """Return the path to RobloxPlayerBeta.exe, or None if not found."""
        return _find_roblox_exe()

    # R6 <-> R15 Animation Converter

    def _ac_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Open Animation File', '',
            'Roblox Animation (*.rbxmx *.rbxm);;All files (*.*)',
        )
        if not path:
            return
        p = Path(path)
        try:
            data = p.read_bytes()
        except Exception as e:
            self._ac_status_lbl.setText(f'Read error: {e}')
            return

        # Detect rig from original bytes (binary parser handles .bin/.rbxm natively)
        try:
            from ..utils.anim_converter import detect_rig
            rig = detect_rig(data)
        except Exception:
            rig = 'unknown'

        # Auto-convert binary .rbxm -> .rbxmx so _ac_convert has XML to work with
        if p.suffix.lower() == '.rbxm':
            try:
                from ..utils.anim_converter import rbxm_to_rbxmx
                data = rbxm_to_rbxmx(data)
                self._ac_status_lbl.setText('Auto-converted .rbxm → .rbxmx')
            except Exception as e:
                self._ac_status_lbl.setText(f'.rbxm conversion failed: {e}')
                return
        else:
            self._ac_status_lbl.setText('')

        self._ac_xml_bytes = data
        self._ac_source_path = p

        self._ac_rig_lbl.setText(f'Detected rig: {rig}')
        self._ac_file_lbl.setText(p.name)
        self._ac_to_r6_btn.setEnabled(rig == 'R15')
        self._ac_to_r15_btn.setEnabled(rig == 'R6')

    def _ac_convert(self, target: str):
        if not hasattr(self, '_ac_xml_bytes'):
            self._ac_status_lbl.setText('No file loaded.')
            return

        try:
            import xml.etree.ElementTree as ET
            from ..utils.r15_to_r6 import (convert_keyframe_r15_to_r6,
                                            convert_keyframe_r6_to_r15, sanitize_xml)
            from ..utils.rig_data import R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS

            xml_bytes = self._ac_xml_bytes

            # If this is a CurveAnimation, convert to KeyframeSequence first
            if b'CurveAnimation' in xml_bytes:
                from ..utils.anim_converter import curve_anim_to_keyframe
                xml_bytes = curve_anim_to_keyframe(xml_bytes)

            root = ET.fromstring(sanitize_xml(xml_bytes))
            etree = ET.ElementTree(root)

            ks = root.find("Item[@class='KeyframeSequence']")
            if ks is None:
                self._ac_status_lbl.setText('No KeyframeSequence found.')
                return
            keyframes = ks.findall("Item[@class='Keyframe']")
            if not keyframes:
                self._ac_status_lbl.setText('No Keyframes found.')
                return

            if target == 'R6':
                for kf in keyframes:
                    convert_keyframe_r15_to_r6(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)
            else:
                for kf in keyframes:
                    convert_keyframe_r6_to_r15(kf, R6_PARTS, R6_JOINTS, R15_PARTS, R15_JOINTS)

            suffix = '_r6' if target == 'R6' else '_r15'
            default_name = self._ac_source_path.stem + suffix + '.rbxmx'
            default_dir = str(self._ac_source_path.parent)
            out_str, _ = QFileDialog.getSaveFileName(
                self, 'Save Converted Animation', f'{default_dir}/{default_name}',
                'Roblox Animation (*.rbxmx);;All files (*.*)',
            )
            if not out_str:
                self._ac_status_lbl.setText('Cancelled.')
                return
            out_path = Path(out_str)
            etree.write(str(out_path), encoding='utf-8', xml_declaration=True)
            self._ac_status_lbl.setText(f'Saved: {out_path.name}')
        except Exception as e:
            self._ac_status_lbl.setText(f'Error: {e}')

    # Proxy interceptor hooks

    def request(self, flow):
        url = flow.request.pretty_url
        if "gamejoin.roblox.com" not in url:
            return

        parsed = urlparse(url)

        if parsed.path == "/v1/join-reserved-game":
            try:
                body = json.loads(flow.request.content)
                place_id = body.get("placeId")
                access_code = body.get("accessCode")
                attempt_id = body.get('gameJoinAttemptId')
                normalized_place_id = self._normalize_numeric_id(place_id)
                if (
                    normalized_place_id is not None
                    and normalized_place_id in self._subplace_blacklisted_ids
                    and not self._is_subplace_unblock_active()
                ):
                    self._drop_subplace_join(flow, normalized_place_id, attempt_id=str(attempt_id) if attempt_id else None)
                    return
                session_id = flow.request.headers.get("Roblox-Session-Id", "")
                if place_id is not None and access_code is not None:
                    with self._lock:
                        self._last_place_id = place_id
                        self._last_access_code = access_code
                        self._last_session_id = session_id or None
                    has_session = bool(session_id)
                    log_buffer.log(
                        "randostuff", f"Logged reserved server — placeId={place_id}, "
                        f"sessionHeader={'present' if has_session else 'missing'}"
                    )
                    self._update_labels(place_id, access_code)
            except Exception as exc:
                log_buffer.log("randostuff", f"Failed to parse join-reserved-game body: {exc}")
            return

        if parsed.path not in self._WANTED_ENDPOINTS:
            return

        try:
            precheck_body = json.loads(flow.request.content)
            blocked_place_id = self._normalize_numeric_id(precheck_body.get('placeId'))
            if (
                blocked_place_id is not None
                and blocked_place_id in self._subplace_blacklisted_ids
                and not self._is_subplace_unblock_active()
            ):
                precheck_attempt_id = precheck_body.get('gameJoinAttemptId')
                self._drop_subplace_join(
                    flow,
                    blocked_place_id,
                    attempt_id=str(precheck_attempt_id) if precheck_attempt_id else None,
                )
                return
        except Exception:
            pass

        # Account manager: redirect join-game to join-game-instance if a jobId is pending
        if parsed.path == "/v1/join-game":
            with self._lock:
                pending_job = self._account_manager_job_id
            if pending_job:
                try:
                    body = json.loads(flow.request.content)
                    body["gameId"] = pending_job
                    flow.request.url = "https://gamejoin.roblox.com/v1/join-game-instance"
                    flow.request.raw_content = json.dumps(body).encode("utf-8")
                    with self._lock:
                        self._account_manager_job_id = ""
                    log_buffer.log("accounts", f"Redirected join-game -> join-game-instance with jobId={pending_job}")
                except Exception as exc:
                    log_buffer.log("accounts", f"Failed to intercept join-game for jobId: {exc}")
                return

        try:
            req_body = json.loads(flow.request.content)
            attempt_id = req_body.get("gameJoinAttemptId")
        except Exception:
            req_body = {}
            attempt_id = None

        with self._lock:
            doing = self._doing_rejoin
            active_id = self._active_rejoin_attempt_id
            place_id = self._last_place_id
            access_code = self._last_access_code
            session_id = self._last_session_id

            # First interception: consume the flag, record the attempt ID
            if doing:
                self._doing_rejoin = False
                self._active_rejoin_attempt_id = attempt_id
                active_id = attempt_id
            # Follow-up polls: only intercept if attempt ID matches
            elif active_id is None or attempt_id != active_id:
                return

        if place_id is None or access_code is None:
            log_buffer.log("randostuff", "Rejoin flag set but no reserved server stored — aborting.")
            with self._lock:
                self._active_rejoin_attempt_id = None
            return

        normalized_place_id = self._normalize_numeric_id(place_id)
        if (
            normalized_place_id is not None
            and normalized_place_id in self._subplace_blacklisted_ids
            and not self._is_subplace_unblock_active()
        ):
            self._drop_subplace_join(flow, normalized_place_id, attempt_id=str(attempt_id) if attempt_id else None)
            with self._lock:
                self._active_rejoin_attempt_id = None
                self._awaiting_rejoin_response = False
            return

        new_payload = {
            "placeId": place_id,
            "accessCode": access_code,
            "isTeleport": True,
            "isImmersiveAdsTeleport": False,
        }

        flow.request.url = "https://gamejoin.roblox.com/v1/join-reserved-game"
        flow.request.raw_content = json.dumps(new_payload).encode("utf-8")
        if session_id:
            flow.request.headers["Roblox-Session-Id"] = session_id

        log_buffer.log("randostuff", "Rejoin request -> POST gamejoin.roblox.com/v1/join-reserved-game")
        with self._lock:
            self._awaiting_rejoin_response = True

    def response(self, flow):
        if "gamejoin.roblox.com" not in flow.request.pretty_url:
            return

        req_path = urlparse(flow.request.pretty_url).path

        # Capture jobId from a normal game join initiated by the account manager
        with self._lock:
            capture_place_id = self._account_manager_capture_place_id
        if capture_place_id:
            if req_path in ("/v1/join-game", "/v1/join-game-instance"):
                try:
                    resp_json = json.loads(flow.response.content)
                    job_id = resp_json.get("jobId") or resp_json.get("gameId")
                    if job_id:
                        self._game_jobs[capture_place_id] = job_id
                        place_id_snap = capture_place_id
                        def _update_ui(jid=job_id, pid=place_id_snap):
                            if not self._job_id_input.text().strip():
                                self._job_id_input.setText(jid)
                                self._auto_filled_for_place = pid
                        self._invoker.call.emit(_update_ui)
                        log_buffer.log("accounts", f"Captured jobId={job_id} for placeId={capture_place_id}")
                except Exception as exc:
                    log_buffer.log("accounts", f"Failed to capture jobId from response: {exc}")
                with self._lock:
                    self._account_manager_capture_place_id = None

        with self._lock:
            waiting = self._awaiting_rejoin_response
            if waiting:
                self._awaiting_rejoin_response = False

        if not waiting:
            return

        resp = flow.response
        if resp is None:
            log_buffer.log("randostuff", "Rejoin response: (none)")
            return

        try:
            body_text = resp.content.decode('utf-8', errors='replace')
            resp_json = json.loads(body_text)
            join_ready = bool(resp_json.get("joinScriptUrl"))
            log_buffer.log(
                "randostuff",
                f"Rejoin response status: http={resp.status_code}, status={resp_json.get('status')}, "
                f"joinScriptUrl={'yes' if join_ready else 'no'}",
            )
            # status 2 = join script ready; clear the active attempt so no more redirects
            if resp_json.get("status") == 2 or join_ready:
                with self._lock:
                    self._active_rejoin_attempt_id = None
                log_buffer.log("randostuff", "Reserved server join ready — stopping redirect.")
            elif resp.status_code >= 400:
                with self._lock:
                    self._active_rejoin_attempt_id = None
                log_buffer.log("randostuff", "Reserved server join error — stopping redirect.")
        except Exception as exc:
            log_buffer.log("randostuff", f"Could not parse rejoin response JSON: {exc}")
            with self._lock:
                self._active_rejoin_attempt_id = None
