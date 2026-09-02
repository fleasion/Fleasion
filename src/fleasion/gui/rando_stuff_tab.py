"""Rando Stuff tab - miscellaneous Roblox utilities (multi-instance, asset download, rejoin)."""

from __future__ import annotations

import ctypes
import importlib
import json
import re
import secrets
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast, override
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests as _requests
from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
from PySide6.QtCore import QEvent, QObject, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fleasion.localization import tr, tr_count
from fleasion.utils import get_icon_path, windows as _platform_windows
from fleasion.utils.json_types import require_object_dict
from fleasion.utils.logging import log_buffer
from fleasion.utils.paths import CONFIG_DIR
from fleasion.utils.plural import format_count
from fleasion.utils.roblox_auth import (
    ROBLOX_COOKIES_PATH,
    LinuxAuthWriteError,
    discover_browser_roblosecurity,
    get_roblosecurity,
    set_roblosecurity,
)
from fleasion.utils.secure_tokens import decrypt_token, encrypt_token

from .modifications_tab import CollapsibleSection
from .proxy_gate import ProxyGate

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager
    from fleasion.proxy.master import ProxyMaster
    from fleasion.proxy.server import ProxyFlow


class Account(TypedDict):
    username: str
    cookie: str


type UsernameSpooferState = dict[str, object]


def _preserve_str(value: object) -> str:
    if TYPE_CHECKING:
        assert isinstance(value, str)
    return value


class _WinFunction(Protocol):
    restype: object

    def __call__(self, *args: object) -> int | None: ...


class _Kernel32(Protocol):
    CreateToolhelp32Snapshot: _WinFunction
    Process32FirstW: _WinFunction
    Process32NextW: _WinFunction
    CloseHandle: _WinFunction
    OpenEventW: _WinFunction
    OpenProcess: _WinFunction
    DuplicateHandle: _WinFunction


class _KernelBase(Protocol):
    CompareObjectHandles: _WinFunction


class _Ntdll(Protocol):
    NtQueryInformationProcess: _WinFunction


ACCOUNTS_FILE = CONFIG_DIR / 'accounts.json'
ACCOUNTS_KEY_FILE = CONFIG_DIR / 'accounts.key'
IS_WINDOWS = sys.platform == 'win32'
IS_MACOS = sys.platform == 'darwin'
IS_LINUX = sys.platform.startswith('linux')

launch_as_standard_user = cast(
    'Callable[[str | Path], bool]',
    _platform_windows.launch_as_standard_user,
)
resolve_roblox_player_exe_for_launch = cast(
    'Callable[[], Path | None]',
    _platform_windows.resolve_roblox_player_exe_for_launch,
)


# Helpers


def _set_signals_blocked(obj: QObject, *, blocked: bool) -> None:
    obj.blockSignals(blocked)


def _import_attr(module_name: str, attr_name: str) -> object:
    return vars(importlib.import_module(module_name))[attr_name]


def _delete_cache_window_type() -> type[QWidget]:
    return cast('type[QWidget]', _import_attr('fleasion.gui.delete_cache', 'DeleteCacheWindow'))


def _encrypt_cookie(cookie: str) -> str:
    """Encrypt a cookie string for storage."""
    return encrypt_token(cookie, ACCOUNTS_KEY_FILE)


def _decrypt_cookie(enc_b64: str) -> str | None:
    """Decrypt a stored cookie string. Returns plain cookie or None on failure."""
    return decrypt_token(enc_b64, ACCOUNTS_KEY_FILE)


def _load_accounts() -> list[Account]:
    """Load accounts list from disk."""
    try:
        if ACCOUNTS_FILE.exists():
            return cast('list[Account]', json.loads(ACCOUNTS_FILE.read_text(encoding='utf-8')))
    except OSError, UnicodeError, json.JSONDecodeError:
        pass
    return []


def _save_accounts(accounts: list[Account]) -> None:
    """Persist accounts list to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_FILE.write_text(json.dumps(accounts, indent=2), encoding='utf-8')


def _authenticated_user_response(cookie: str) -> _requests.Response:
    session = _requests.Session()
    session.trust_env = False
    session.proxies = {}
    try:
        session.cookies.set('.ROBLOSECURITY', cookie)
    except TypeError, ValueError:
        session.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
    return session.get('https://users.roblox.com/v1/users/authenticated', timeout=10)


def _json_object(raw: bytes | str) -> dict[str, object]:
    return require_object_dict(json.loads(raw))


def _response_json_object(response: _requests.Response) -> dict[str, object]:
    return require_object_dict(response.json())


def _run_contained_action(
    action: Callable[[], object],
    on_error: Callable[[Exception], None],
) -> bool:
    """Run an event/thread/OS-boundary callback without letting it escape."""
    try:
        action()
    except Exception as exc:  # ruff: ignore[blind-except]
        on_error(exc)
        return False
    return True


def _run_proxy_action(
    category: str,
    error_prefix: str,
    action: Callable[[], None],
    *,
    on_error: Callable[[], None] | None = None,
) -> None:
    def _handle_error(exc: Exception) -> None:
        log_buffer.log(category, f'{error_prefix}: {exc}')
        if on_error is not None:
            on_error()

    _run_contained_action(action, _handle_error)


def _get_auth_ticket(cookie: str) -> str | None:
    """Fetch a Roblox authentication ticket using the user's cookie."""
    url = 'https://auth.roblox.com/v1/authentication-ticket'
    headers = {
        'Cookie': f'.ROBLOSECURITY={cookie}',
        'Referer': 'https://www.roblox.com',
        'Content-Type': 'application/json',
    }
    try:
        # First request — Roblox returns 403 with X-CSRF-TOKEN on POST endpoints
        resp = _requests.post(url, headers=headers, json={}, timeout=10)
        if resp.status_code == 403 and 'x-csrf-token' in resp.headers:
            headers['X-CSRF-TOKEN'] = resp.headers['x-csrf-token']
            resp = _requests.post(url, headers=headers, json={}, timeout=10)
        if resp.status_code == 200:
            return resp.headers.get('rbx-authentication-ticket')
    except _requests.RequestException:
        pass
    return None


def _get_access_code(place_id: str, link_code: str, cookie: str) -> str | None:
    """Resolve a privateServerLinkCode to the UUID accessCode.

    Tries the games API first, then falls back to parsing the game page HTML
    (the approach used by Roblox Account Manager).
    """
    sess = _requests.Session()
    sess.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
    sess.headers.update(
        {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    )

    # games.roblox.com API (fastest path)
    for url in (
        f'https://games.roblox.com/v1/private-servers?serverLinkCode={link_code}',
        f'https://games.roblox.com/v1/private-servers/{link_code}',
    ):
        try:
            resp = sess.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                code = data.get('accessCode') or data.get('vipServerAccessCode')
                if code:
                    return code
        except _requests.RequestException:
            pass

    # Fall back to parsing game page HTML
    try:
        resp = sess.get(
            f'https://www.roblox.com/games/{place_id}',
            params={'privateServerLinkCode': link_code},
            headers={'Referer': 'https://www.roblox.com/games/4924922222/Brookhaven-RP'},
            timeout=15,
        )
        for pat in (
            r"Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*'([\w-]+)'",
            r'Roblox\.GameLauncher\.joinPrivateGame\(\d+,\s*\"([\w-]+)\"',
            r'"accessCode"\s*:\s*"([\w-]{36})"',
        ):
            m = re.search(pat, resp.text)
            if m:
                return m.group(1)
    except _requests.RequestException:
        pass

    return None


def _preseed_root_join_response(root_place_id: str, cookie: str) -> _requests.Response:
    sess = _requests.Session()
    sess.trust_env = False
    sess.proxies = {}
    sess.verify = False
    sess.headers.update(
        {
            'User-Agent': 'Roblox/WinInet',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Referer': 'https://www.roblox.com/',
            'Origin': 'https://www.roblox.com',
            'Cookie': f'.ROBLOSECURITY={cookie};',
        }
    )
    try:
        token_resp = sess.post('https://auth.roblox.com/v2/logout', timeout=10)
        token = token_resp.headers.get('x-csrf-token') or token_resp.headers.get('X-CSRF-TOKEN')
        if token:
            sess.headers['X-CSRF-TOKEN'] = token
    except _requests.RequestException:
        pass

    payload = {
        'placeId': int(root_place_id),
        'isTeleport': True,
        'isImmersiveAdsTeleport': False,
        'gameJoinAttemptId': str(uuid.uuid4()),
    }
    return sess.post('https://gamejoin.roblox.com/v1/join-game', json=payload, timeout=15)


def _preseed_root_place_for_subplace(root_place_id: str, cookie: str) -> bool:
    """Prime Roblox's join state for a subplace launch by joining the root place."""
    if not root_place_id or not cookie:
        return False
    try:
        resp = _preseed_root_join_response(root_place_id, cookie)
        try:
            return resp.status_code == 200 and resp.json().get('status') == 2
        except _requests.RequestException:
            return False
    except (ValueError, _requests.RequestException) as exc:
        log_buffer.log('accounts', f'Subplace root pre-seed error: {exc}')
        return False


_UUID_RE = re.compile(
    r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.IGNORECASE
)


def _extract_job_id(raw: str) -> str:
    """Return just the UUID from a job ID string, stripping any prefix like 'JoinGame=JOBID '."""
    raw = raw.strip()
    m = _UUID_RE.search(raw)
    if m:
        return m.group(0)
    if re.search(r'(?:^|[;:\s])Join(?:Place|Game|PrivateGame)\s*[=:]', raw, re.IGNORECASE):
        return ''
    return raw


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
        link_code = parse_qs(parsed.query).get('privateServerLinkCode', [None])[0]
    except ValueError:
        return None, None
    parts = [part for part in parsed.path.split('/') if part]
    if 'games' in parts:
        idx = parts.index('games')
        if idx + 1 < len(parts) and parts[idx + 1].isdigit():
            return parts[idx + 1], link_code
    return None, None


def _parse_optional_place_id(raw: str) -> str | None:
    """Parse an optional place/subplace ID field, accepting a plain ID or game URL."""
    raw = (raw or '').strip()
    if not raw:
        return None
    place_id, _link_code = _parse_game_link(raw)
    return place_id


def _is_share_link(link: str) -> bool:
    """Return True if link is a roblox.com/share?code=...&type=Server link."""
    if not link:
        return False
    try:
        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        return (
            'roblox.com' in parsed.netloc and parsed.path.rstrip('/') == '/share' and 'code' in qs
        )
    except ValueError:
        return False


def _resolve_share_link_impl(link: str, cookie: str) -> tuple[str, str]:
    parsed = urlparse(link)
    qs = parse_qs(parsed.query)
    link_id = (qs.get('code') or [None])[0]
    link_type = (qs.get('type') or ['Server'])[0]
    if not link_id:
        return '', ''

    sess = _requests.Session()
    if cookie:
        sess.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
    sess.headers.update(
        {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.roblox.com/',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json;charset=UTF-8',
        }
    )

    body = {'linkId': link_id, 'linkType': link_type}
    resp = sess.post(
        'https://apis.roblox.com/sharelinks/v1/resolve-link',
        json=body,
        timeout=10,
    )
    # Roblox returns 403 with X-CSRF-TOKEN on first POST — retry with token
    if resp.status_code == 403 and 'x-csrf-token' in resp.headers:
        sess.headers['X-CSRF-TOKEN'] = resp.headers['x-csrf-token']
        resp = sess.post(
            'https://apis.roblox.com/sharelinks/v1/resolve-link',
            json=body,
            timeout=10,
        )
    if resp.status_code != 200:
        return '', ''

    data = cast('dict[str, object]', resp.json())

    def _extract(d: dict[str, object]) -> tuple[str, str]:
        pid = str(d.get('placeId') or d.get('rootPlaceId') or '')
        lc = d.get('privateServerLinkCode') or d.get('linkCode') or d.get('accessCode') or ''
        return pid, _preserve_str(lc)

    place_id, link_code = _extract(data)
    for key in (
        'privateServerInviteData',
        'privateServerData',
        'gameDetails',
        'serverData',
    ):
        nested = data.get(key)
        if isinstance(nested, dict):
            nested_place_id, nested_link_code = _extract(cast('dict[str, object]', nested))
            place_id = place_id or nested_place_id
            link_code = link_code or nested_link_code

    return (place_id, link_code) if place_id and link_code else ('', '')


def _resolve_share_link(link: str, cookie: str = '') -> tuple[str, str]:
    """Resolve a roblox.com/share link via the sharelinks API."""
    try:
        return _resolve_share_link_impl(link, cookie)
    except ValueError, _requests.RequestException:
        return '', ''


def _find_roblox_exe() -> str | None:
    """Return best Roblox executable path using shared resolver fallbacks."""
    exe_path = resolve_roblox_player_exe_for_launch()
    return str(exe_path) if exe_path is not None else None


def _linux_client_display_name() -> str:
    """Return the active registered Linux client name without hard-coding it."""
    if not IS_LINUX:
        return tr('rando.linux_roblox_client')
    try:
        display_name = cast(
            'Callable[[], str]',
            _import_attr('fleasion.utils.platform_linux', 'selected_linux_client_display_name'),
        )
        return display_name()
    except ImportError, KeyError, OSError, RuntimeError:
        return tr('rando.linux_roblox_client')


def _build_roblox_player_uri(
    ticket: str,
    *,
    launch_mode: str = 'play',
    place_launcher_url: str = '',
    tracker_id: int | None = None,
    launch_time_ms: int | None = None,
) -> str:
    """Build a roblox-player URI using an auth ticket."""
    parts = [
        'roblox-player:1',
        f'launchmode:{launch_mode}',
        f'gameinfo:{ticket}',
        f'launchtime:{launch_time_ms if launch_time_ms is not None else int(time.time() * 1000)}',
    ]
    if place_launcher_url:
        parts.append(f'placelauncherurl:{quote(place_launcher_url, safe="")}')
    if tracker_id is not None:
        parts.append(f'browsertrackerid:{tracker_id}')
    parts.extend(['robloxLocale:en_us', 'gameLocale:en_us', 'channel:', 'LaunchExp:InApp'])
    return '+'.join(parts)


def _build_place_launcher_url(
    place_id: str,
    *,
    request_type: str = 'RequestGame',
    tracker_id: int,
    job_id: str = '',
    access_code: str = '',
    link_code: str = '',
    join_attempt_id: str | None = None,
) -> str:
    params = {
        'request': request_type,
        'browserTrackerId': str(tracker_id),
        'placeId': str(place_id),
        'joinAttemptId': join_attempt_id or str(uuid.uuid4()),
    }
    if job_id:
        params['gameId'] = job_id
    if access_code:
        params['accessCode'] = access_code
    if link_code:
        params['linkCode'] = link_code
    return 'https://www.roblox.com/Game/PlaceLauncher.ashx?' + urlencode(params)


def _build_auth_ticket_app_uri(ticket: str, *, launch_time_ms: int | None = None) -> str:
    return _build_roblox_player_uri(ticket, launch_mode='app', launch_time_ms=launch_time_ms)


def _build_auth_ticket_place_uri(
    ticket: str,
    place_id: str,
    *,
    job_id: str = '',
    tracker_id: int | None = None,
    join_attempt_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    tracker = (
        tracker_id if tracker_id is not None else 10_000_000_000 + secrets.randbelow(90_000_000_000)
    )
    launcher = _build_place_launcher_url(
        place_id,
        request_type='RequestGameJob' if job_id else 'RequestGame',
        tracker_id=tracker,
        job_id=job_id,
        join_attempt_id=join_attempt_id,
    )
    return _build_roblox_player_uri(
        ticket,
        launch_mode='play',
        place_launcher_url=launcher,
        tracker_id=tracker,
        launch_time_ms=launch_time_ms,
    )


def _build_auth_ticket_private_server_uri(
    ticket: str,
    place_id: str,
    *,
    access_code: str,
    link_code: str,
    tracker_id: int | None = None,
    join_attempt_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    tracker = (
        tracker_id if tracker_id is not None else 10_000_000_000 + secrets.randbelow(90_000_000_000)
    )
    launcher = _build_place_launcher_url(
        place_id,
        request_type='RequestPrivateGame',
        tracker_id=tracker,
        access_code=access_code,
        link_code=link_code,
        join_attempt_id=join_attempt_id,
    )
    return _build_roblox_player_uri(
        ticket,
        launch_mode='play',
        place_launcher_url=launcher,
        tracker_id=tracker,
        launch_time_ms=launch_time_ms,
    )


# Add / Change Cookie dialog


class AddAccountDialog(QDialog):
    """Dialog for pasting a .ROBLOSECURITY cookie and validating it."""

    _validated = Signal(str, str)  # username, cookie
    _failed = Signal(str)  # error message

    def __init__(self, parent: QWidget | None = None, title: str | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title or tr('rando.account.add_title'))
        self.setMinimumWidth(500)
        self.result_username: str | None = None
        self.result_cookie: str | None = None
        self._validated.connect(self._on_validated)
        self._failed.connect(self._on_failed)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(tr('ui.gui.rando_stuff_tab.paste_your_roblosecurity_cookie')))

        self._input = QPlainTextEdit()
        self._input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.warning_do_not_share_this_sharing_this')
        )
        self._input.setFixedHeight(70)
        layout.addWidget(self._input)

        self._status = QLabel('')
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        btn_row = QHBoxLayout()
        self._ok_btn = QPushButton(tr('ui.gui.rando_stuff_tab.add'))
        self._ok_btn.clicked.connect(self._on_ok)
        cancel_btn = QPushButton(tr('ui.gui.rando_stuff_tab.cancel'))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def set_ok_label(self, text: str) -> None:
        self._ok_btn.setText(text)

    def _on_ok(self) -> None:
        cookie = self._input.toPlainText().strip()
        if not cookie:
            self._status.setText(tr('ui.gui.rando_stuff_tab.please_paste_a_cookie'))
            return
        self._ok_btn.setEnabled(False)
        self._status.setText(tr('ui.gui.rando_stuff_tab.validating'))
        threading.Thread(target=self._validate, args=(cookie,), daemon=True).start()

    def _validate(self, cookie: str) -> None:
        try:
            resp = _authenticated_user_response(cookie)
            payload = _response_json_object(resp) if resp.status_code == 200 else None
        except (_requests.RequestException, RuntimeError, TypeError, ValueError) as exc:
            self._failed.emit(tr('rando.account.error', error=exc))
            return
        if resp.status_code == 200 and isinstance(payload, dict):
            self._validated.emit(str(payload.get('name', 'Unknown')), cookie)
            return
        self._failed.emit(tr('rando.account.invalid_cookie_http', status_code=resp.status_code))

    def _on_validated(self, username: str, cookie: str) -> None:
        self.result_username = username
        self.result_cookie = cookie
        self.accept()

    def _on_failed(self, msg: str) -> None:
        self._status.setText(msg)
        self._ok_btn.setEnabled(True)


# Main-thread invoker


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.call.connect(self._run, Qt.ConnectionType.QueuedConnection)

    def _run(self, fn: Callable[[], object]) -> None:
        _run_contained_action(
            fn,
            lambda exc: log_buffer.log('randostuff', f'invoker error: {exc}'),
        )


# Tab widget


class RandoStuffTab(QWidget):
    """Rando Stuff tab - proxy interceptor + UI combined."""

    selected_account_changed = Signal(str)

    _WANTED_ENDPOINTS = (
        '/v1/join-game',
        '/v1/join-play-together-game',
        '/v1/join-game-instance',
    )
    _PRIVATE_GAME_ENDPOINT = '/v1/join-private-game'
    _RESERVED_GAME_ENDPOINT = '/v1/join-reserved-game'

    def __init__(
        self,
        parent: QWidget | None = None,
        config_manager: ConfigManager | None = None,
        proxy_master: ProxyMaster | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config_manager
        self._proxy_master = proxy_master
        self._qt_destroyed = False
        self.destroyed.connect(self._on_qt_destroyed)
        self._invoker = _Invoker(self)

        self._last_place_id: str | None = None
        self._last_access_code: str | None = None
        self._last_session_id: str | None = None
        self._doing_rejoin = False
        self._awaiting_rejoin_response = False
        self._active_rejoin_attempt_id: object | None = None  # gameJoinAttemptId being redirected
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
        self._multi_thread: threading.Thread | None = None
        self._account_switched = False
        self._last_switched_account: Account | None = None

        self._accounts: list[Account] = _load_accounts()
        self._game_jobs: dict[str, str] = {}  # placeId -> jobId, session-only memory
        self._account_manager_job_id: str = ''
        self._account_manager_capture_place_id: str | None = None
        self._account_manager_teleport_place_id: str | None = None
        self._auto_filled_for_place: str | None = None
        self._username_spoofer_current_user_id: str | None = None
        self._username_spoofer_current_username = ''
        self._username_spoofer_state = self._load_username_spoofer_settings()

        self._setup_ui()
        self._push_username_spoofer_runtime_state()
        if self._config is not None:
            enabled = bool(self._config.multi_instance_launching) and IS_WINDOWS
            _set_signals_blocked(self._multi_chk, blocked=True)
            self._multi_chk.setChecked(enabled)
            _set_signals_blocked(self._multi_chk, blocked=False)
            if enabled:
                self._on_multi_instance_toggled(checked=True, persist=False)
        threading.Thread(target=self._check_cookies_on_boot, daemon=True).start()
        threading.Thread(target=self._resolve_current_user, daemon=True).start()
        if self._subplace_blacklisted_ids:
            count = len(self._subplace_blacklisted_ids)
            log_buffer.log(
                'subplace',
                f'Loaded subplace blacklist: {format_count(count, "ID")} active',
            )

    def _on_qt_destroyed(self, *_: object) -> None:
        self._qt_destroyed = True

    def _on_main(self, fn: Callable[[], object]) -> bool:
        if self._qt_destroyed:
            return False
        invoker = getattr(self, '_invoker', None)
        if invoker is None:
            return False
        try:
            invoker.call.emit(fn)
        except RuntimeError as exc:
            if 'has been deleted' not in str(exc):
                log_buffer.log('randostuff', f'invoker emit error: {exc}')
            self._qt_destroyed = True
            return False
        return True

    @staticmethod
    def _normalize_numeric_id(value: object) -> str | None:
        try:
            return str(int(str(value).strip()))
        except TypeError, ValueError:
            return None

    @classmethod
    def _parse_numeric_id_list(cls, raw_value: str) -> list[str]:
        content = raw_value.replace('\n', ',').replace(';', ',').replace(' ', ',')
        ids: list[str] = []
        for part in content.split(','):
            normalized = cls._normalize_numeric_id(part)
            if normalized is not None:
                ids.append(normalized)
        return ids

    def _is_subplace_blacklisted(self, place_id: object) -> bool:
        normalized = self._normalize_numeric_id(place_id)
        return normalized is not None and normalized in self._subplace_blacklisted_ids

    def _apply_account_manager_subplace_teleport(self, body: dict[str, object]) -> bool:
        body_place_id = self._normalize_numeric_id(body.get('placeId'))
        with self._lock:
            teleport_place_id = self._account_manager_teleport_place_id
        if not teleport_place_id or body_place_id != teleport_place_id:
            return False
        if body.get('isTeleport') is not True:
            body['isTeleport'] = True
            return True
        return False

    def _clear_account_manager_subplace_teleport_if_complete(
        self, flow: ProxyFlow, req_path: str
    ) -> None:
        with self._lock:
            teleport_place_id = self._account_manager_teleport_place_id
        if not teleport_place_id or req_path not in {
            *self._WANTED_ENDPOINTS,
            self._PRIVATE_GAME_ENDPOINT,
            self._RESERVED_GAME_ENDPOINT,
        }:
            return
        try:
            body = cast('dict[str, object]', json.loads(flow.request.content))
        except UnicodeDecodeError, json.JSONDecodeError, TypeError:
            return
        if self._normalize_numeric_id(body.get('placeId')) != teleport_place_id:
            return
        resp_json: dict[str, object] = {}
        try:
            if flow.response is not None:
                resp_json = cast('dict[str, object]', json.loads(flow.response.content))
        except UnicodeDecodeError, json.JSONDecodeError, TypeError:
            resp_json = {}
        status = resp_json.get('status')
        if status in {0, 1}:
            return
        with self._lock:
            if self._account_manager_teleport_place_id == teleport_place_id:
                self._account_manager_teleport_place_id = None

    def _drop_subplace_join(
        self, flow: ProxyFlow, place_id: str, attempt_id: str | None = None
    ) -> None:
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
            log_buffer.log(
                'subplace',
                f'Blocked join request to blacklisted subplace ID: {place_id}',
            )

    def _set_subplace_block_mode(self, mode: str, checked: bool) -> None:
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

    def _on_subplace_unblock_for_5s(self) -> None:
        with self._lock:
            self._subplace_unblock_until = time.time() + 5.0
        log_buffer.log('subplace', 'Subplace blacklist bypass enabled for 5 seconds')

    @staticmethod
    def _default_username_spoofer_state() -> UsernameSpooferState:
        return {
            'save_settings': False,
            'others_name': '',
            'others_apply_ingame': False,
            'others_verified': False,
            'self_name': '',
            'self_apply_ingame': False,
            'self_verified': False,
            'self_game_creator': False,
        }

    def _load_username_spoofer_settings(self) -> UsernameSpooferState:
        state = self._default_username_spoofer_state()
        if self._config is None:
            return state
        saved = getattr(self._config, 'username_spoofer', {})
        if isinstance(saved, dict):
            state.update(cast('dict[str, object]', saved))
        if not state.get('save_settings', False):
            return self._default_username_spoofer_state()
        return state

    def _username_spoofer_state_from_widgets(self) -> UsernameSpooferState:
        return {
            'save_settings': self._username_save_chk.isChecked(),
            'others_name': self._username_others_input.text(),
            'others_apply_ingame': self._username_others_apply_chk.isChecked(),
            'others_verified': self._username_others_verified_chk.isChecked(),
            'self_name': self._username_self_input.text(),
            'self_apply_ingame': self._username_self_apply_chk.isChecked(),
            'self_verified': self._username_self_verified_chk.isChecked(),
            'self_game_creator': self._username_self_game_creator_chk.isChecked(),
        }

    def _set_username_spoofer_state(self, state: UsernameSpooferState) -> None:
        with self._lock:
            self._username_spoofer_state = {
                'save_settings': bool(state.get('save_settings', False)),
                'others_name': str(state.get('others_name', '')),
                'others_apply_ingame': bool(state.get('others_apply_ingame', False)),
                'others_verified': bool(state.get('others_verified', False)),
                'self_name': str(state.get('self_name', '')),
                'self_apply_ingame': bool(state.get('self_apply_ingame', False)),
                'self_verified': bool(state.get('self_verified', False)),
                'self_game_creator': bool(state.get('self_game_creator', False)),
            }

    def _persist_username_spoofer_state(self, state: UsernameSpooferState) -> None:
        if self._config is not None:
            self._config.username_spoofer = state

    def _push_username_spoofer_runtime_state(self) -> None:
        spoofer = getattr(self._proxy_master, 'username_spoofer', None)
        if spoofer is not None and hasattr(spoofer, 'set_runtime_state'):
            spoofer.set_runtime_state(dict(self._username_spoofer_state))
        if self._proxy_master is not None and hasattr(
            self._proxy_master, 'refresh_username_spoofer_interception'
        ):
            self._proxy_master.refresh_username_spoofer_interception()

    def _push_username_spoofer_current_user(self) -> None:
        spoofer = getattr(self._proxy_master, 'username_spoofer', None)
        if spoofer is not None and hasattr(spoofer, 'set_current_user'):
            spoofer.set_current_user(
                self._username_spoofer_current_user_id,
                self._username_spoofer_current_username,
            )

    def _on_username_spoofer_changed(self) -> None:
        state = self._username_spoofer_state_from_widgets()
        self._set_username_spoofer_state(state)
        self._push_username_spoofer_runtime_state()
        if state['save_settings']:
            self._persist_username_spoofer_state(state)

    def _on_username_spoofer_save_toggled(self, checked: bool) -> None:
        state = self._username_spoofer_state_from_widgets()
        self._set_username_spoofer_state(state)
        self._push_username_spoofer_runtime_state()
        if checked:
            self._persist_username_spoofer_state(state)
        else:
            self._persist_username_spoofer_state(self._default_username_spoofer_state())

    # UI

    def _setup_ui(self) -> None:
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        container.setObjectName('_FleasionMiscContainer')
        self._misc_container = container
        root = QVBoxLayout(container)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # Match Modifications/Settings: every utility lives in the same
        # collapsible rounded section card instead of a native QGroupBox.
        self._rejoin_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.reserved_server_rejoin'),
            expanded=True,
        )
        rejoin_body = QWidget()
        rjl = QVBoxLayout(rejoin_body)
        rjl.setContentsMargins(0, 0, 0, 0)
        rjl.setSpacing(6)

        btn_row = QHBoxLayout()
        self._btn = QPushButton(tr('ui.gui.rando_stuff_tab.rejoin_reserved_server'))
        btn_row.addWidget(self._btn)
        help_btn = QPushButton(tr('ui.gui.rando_stuff_tab.text'))
        help_btn.setMaximumWidth(self._btn.sizeHint().height())
        help_btn.setToolTip(tr('ui.gui.rando_stuff_tab.what_is_a_reserved_server'))
        help_btn.clicked.connect(self._show_reserved_server_help)
        btn_row.addWidget(help_btn)
        btn_row.addStretch()
        rjl.addLayout(btn_row)

        place_row = QHBoxLayout()
        place_lbl = QLabel(tr('ui.gui.rando_stuff_tab.placeid'))
        place_row.addWidget(place_lbl)
        self._place_id_input = QLineEdit()
        self._place_id_input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.reserved_server_placeid')
        )
        self._place_id_input.textChanged.connect(self._on_reserved_fields_changed)
        place_row.addWidget(self._place_id_input, 1)
        rjl.addLayout(place_row)

        access_row = QHBoxLayout()
        access_lbl = QLabel(tr('ui.gui.rando_stuff_tab.accesscode'))
        label_width = max(place_lbl.sizeHint().width(), access_lbl.sizeHint().width())
        place_lbl.setMinimumWidth(label_width)
        access_lbl.setMinimumWidth(label_width)
        access_row.addWidget(access_lbl)
        self._access_code_input = QLineEdit()
        self._access_code_input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.reserved_server_accesscode')
        )
        self._access_code_input.textChanged.connect(self._on_reserved_fields_changed)
        access_row.addWidget(self._access_code_input, 1)
        rjl.addLayout(access_row)

        self._lbl_timer = QLabel(tr('ui.gui.rando_stuff_tab.timer'))
        rjl.addWidget(self._lbl_timer)

        self._rejoin_timer = QTimer(self)
        self._rejoin_timer.setInterval(1000)
        self._rejoin_timer.timeout.connect(self._tick_rejoin_timer)
        self._rejoin_timer_secs = 0

        self._rejoin_section.add_widget(rejoin_body)
        self._rejoin_proxy_gate = ProxyGate(self._rejoin_section, compact=True)
        root.addWidget(self._rejoin_proxy_gate)

        self._multi_instance_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.multi_instance'),
            expanded=True,
        )
        self._multi_chk = QCheckBox(tr('ui.gui.rando_stuff_tab.enable_multi_instance_launching'))
        if not IS_WINDOWS:
            self._multi_chk.setChecked(False)
            self._multi_chk.setEnabled(False)
            self._multi_chk.setToolTip(
                tr('ui.gui.rando_stuff_tab.multi_instance_launching_depends_on_a_windows')
            )
        self._multi_instance_section.add_widget(self._multi_chk)
        root.addWidget(self._multi_instance_section)

        self._account_manager_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.account_manager'),
            expanded=True,
        )
        account_body = QWidget()
        account_layout = QHBoxLayout(account_body)
        account_layout.setContentsMargins(0, 0, 0, 0)
        account_layout.setSpacing(10)

        # Account selection stays in a narrow left column.  The controls that
        # used to sit below the list now occupy the right column, eliminating
        # the large empty list area on wide/tall windows.
        self._account_list = QListWidget()
        self._account_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._account_list.customContextMenuRequested.connect(self._on_account_ctx_menu)
        self._account_list.setMinimumWidth(180)
        self._account_list.setMaximumWidth(300)
        self._account_list.setMinimumHeight(150)
        account_layout.addWidget(self._account_list, 1)

        account_details = QWidget()
        account_details_layout = QVBoxLayout(account_details)
        account_details_layout.setContentsMargins(0, 0, 0, 0)
        account_details_layout.setSpacing(6)

        self._selected_label = QLabel(tr('ui.gui.rando_stuff_tab.selected_none'))
        self._selected_label.setStyleSheet('color: palette(placeholder-text); font-size: 9pt;')
        account_details_layout.addWidget(self._selected_label)

        self._private_server_input = QLineEdit()
        self._private_server_input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.game_link_e_g_https_www_roblox')
        )
        self._private_server_input.textChanged.connect(self._on_game_link_changed)
        account_details_layout.addWidget(self._private_server_input)

        self._subplace_id_input = QLineEdit()
        self._subplace_id_input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.subplace_id_optional')
        )
        self._subplace_id_input.textChanged.connect(self._on_subplace_id_changed)
        account_details_layout.addWidget(self._subplace_id_input)

        self._job_id_input = QLineEdit()
        self._job_id_input.setPlaceholderText(tr('ui.gui.rando_stuff_tab.jobid_optional'))
        account_details_layout.addWidget(self._job_id_input)

        am_btns = QHBoxLayout()
        self._add_acct_btn = QPushButton(tr('ui.gui.rando_stuff_tab.add_account'))
        self._add_acct_btn.clicked.connect(self._on_add_account)
        self._import_browser_btn = QPushButton(tr('ui.gui.rando_stuff_tab.import_browser_login'))
        self._import_browser_btn.clicked.connect(self._on_import_browser_account)
        self._import_browser_btn.setVisible(IS_MACOS or IS_LINUX)
        self._launch_acct_btn = QPushButton(tr('ui.gui.rando_stuff_tab.launch'))
        self._launch_acct_btn.clicked.connect(self._on_launch_account)
        self._switch_acct_btn = QPushButton(tr('ui.gui.rando_stuff_tab.switch_to_selected'))
        self._switch_acct_btn.clicked.connect(self._on_switch_account)
        am_btns.addWidget(self._add_acct_btn)
        am_btns.addWidget(self._import_browser_btn)
        am_btns.addWidget(self._launch_acct_btn)
        am_btns.addWidget(self._switch_acct_btn)
        am_btns.addStretch()
        account_details_layout.addLayout(am_btns)
        account_details_layout.addStretch()
        account_layout.addWidget(account_details, 4)

        self._account_manager_section.add_widget(account_body)
        root.addWidget(self._account_manager_section)

        self._populate_account_list()

        self._username_spoofer_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.username_spoofer_client_sided_only_you_see'),
            expanded=True,
        )
        username_body = QWidget()
        username_layout = QVBoxLayout(username_body)
        username_layout.setContentsMargins(0, 0, 0, 0)
        username_layout.setSpacing(6)

        self._username_save_chk = QCheckBox(
            tr('ui.gui.rando_stuff_tab.save_username_spoofer_settings')
        )
        self._username_save_chk.setChecked(
            bool(self._username_spoofer_state.get('save_settings', False))
        )
        username_layout.addWidget(self._username_save_chk)

        others_row = QHBoxLayout()
        others_label = QLabel(tr('ui.gui.rando_stuff_tab.everyone_else'))
        others_label.setMinimumWidth(105)
        self._username_others_input = QLineEdit()
        self._username_others_input.setPlaceholderText(
            tr('ui.gui.rando_stuff_tab.spoofed_username')
        )
        self._username_others_input.setText(
            str(self._username_spoofer_state.get('others_name', ''))
        )
        self._username_others_apply_chk = QCheckBox(tr('ui.gui.rando_stuff_tab.apply_ingame'))
        self._username_others_apply_chk.setChecked(
            bool(self._username_spoofer_state.get('others_apply_ingame', False))
        )
        self._username_others_verified_chk = QCheckBox(tr('ui.gui.rando_stuff_tab.verified'))
        self._username_others_verified_chk.setToolTip(
            tr('ui.gui.rando_stuff_tab.force_other_profiles_to_show_as_verified')
        )
        self._username_others_verified_chk.setChecked(
            bool(self._username_spoofer_state.get('others_verified', False))
        )
        others_row.addWidget(others_label)
        others_row.addWidget(self._username_others_input, 1)
        others_row.addWidget(self._username_others_apply_chk)
        others_row.addWidget(self._username_others_verified_chk)
        username_layout.addLayout(others_row)

        self_row = QHBoxLayout()
        self_label = QLabel(tr('ui.gui.rando_stuff_tab.your_username'))
        self_label.setMinimumWidth(105)
        self._username_self_input = QLineEdit()
        self._username_self_input.setPlaceholderText(tr('ui.gui.rando_stuff_tab.spoofed_username'))
        self._username_self_input.setText(str(self._username_spoofer_state.get('self_name', '')))
        self._username_self_apply_chk = QCheckBox(tr('ui.gui.rando_stuff_tab.apply_ingame'))
        self._username_self_apply_chk.setChecked(
            bool(self._username_spoofer_state.get('self_apply_ingame', False))
        )
        self._username_self_verified_chk = QCheckBox(tr('ui.gui.rando_stuff_tab.verified'))
        self._username_self_verified_chk.setToolTip(
            tr('ui.gui.rando_stuff_tab.force_your_own_profile_to_show_as')
        )
        self._username_self_verified_chk.setChecked(
            bool(self._username_spoofer_state.get('self_verified', False))
        )
        self._username_self_game_creator_chk = QCheckBox(
            tr('ui.gui.rando_stuff_tab.make_yourself_game_creator')
        )
        self._username_self_game_creator_chk.setToolTip(
            tr('ui.gui.rando_stuff_tab.force_gamejoin_creator_metadata_to_use_your')
        )
        self._username_self_game_creator_chk.setChecked(
            bool(self._username_spoofer_state.get('self_game_creator', False))
        )
        self_row.addWidget(self_label)
        self_row.addWidget(self._username_self_input, 1)
        self_row.addWidget(self._username_self_apply_chk)
        self_row.addWidget(self._username_self_verified_chk)
        username_layout.addLayout(self_row)
        username_layout.addWidget(self._username_self_game_creator_chk)

        self._username_spoofer_section.add_widget(username_body)
        self._username_spoofer_proxy_gate = ProxyGate(
            self._username_spoofer_section,
            compact=True,
        )
        root.addWidget(self._username_spoofer_proxy_gate)

        self._animation_converter_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.r6_r15_animation_converter'),
            expanded=True,
        )
        animation_body = QWidget()
        acl = QVBoxLayout(animation_body)
        acl.setContentsMargins(0, 0, 0, 0)
        acl.setSpacing(6)

        import_row = QHBoxLayout()
        self._ac_import_btn = QPushButton(tr('ui.gui.rando_stuff_tab.import_rbxmx_rbxm'))
        self._ac_import_btn.clicked.connect(self._ac_import)
        self._ac_file_lbl = QLabel(tr('ui.gui.rando_stuff_tab.no_file_loaded'))
        self._ac_file_lbl.setWordWrap(True)
        import_row.addWidget(self._ac_import_btn)
        import_row.addWidget(self._ac_file_lbl, 1)
        acl.addLayout(import_row)

        self._ac_rig_lbl = QLabel(tr('ui.gui.rando_stuff_tab.detected_rig'))
        acl.addWidget(self._ac_rig_lbl)

        conv_row = QHBoxLayout()
        self._ac_to_r15_btn = QPushButton(tr('ui.gui.rando_stuff_tab.convert_r6_r15'))
        self._ac_to_r15_btn.setEnabled(False)
        self._ac_to_r15_btn.clicked.connect(lambda: self._ac_convert('R15'))
        self._ac_to_r6_btn = QPushButton(tr('ui.gui.rando_stuff_tab.convert_r15_r6'))
        self._ac_to_r6_btn.setEnabled(False)
        self._ac_to_r6_btn.clicked.connect(lambda: self._ac_convert('R6'))
        conv_row.addWidget(self._ac_to_r15_btn)
        conv_row.addWidget(self._ac_to_r6_btn)
        conv_row.addStretch()
        acl.addLayout(conv_row)

        self._ac_status_lbl = QLabel('')
        acl.addWidget(self._ac_status_lbl)

        self._animation_converter_section.add_widget(animation_body)
        root.addWidget(self._animation_converter_section)

        self._subplace_blacklist_section = CollapsibleSection(
            tr('ui.gui.rando_stuff_tab.subplace_blacklist'),
            expanded=True,
        )
        subplace_blacklist_body = QWidget()
        subplace_blacklist_layout = QVBoxLayout(subplace_blacklist_body)
        subplace_blacklist_layout.setContentsMargins(0, 0, 0, 0)
        subplace_blacklist_layout.setSpacing(6)
        subplace_blacklist_row = QHBoxLayout()
        self._subplace_blacklist_btn = QPushButton(tr('ui.gui.rando_stuff_tab.blacklist_subplaces'))
        self._subplace_blacklist_btn.clicked.connect(self._show_subplace_blacklist_dialog)
        subplace_blacklist_row.addWidget(self._subplace_blacklist_btn)
        self._subplace_unblock_btn = QPushButton(tr('ui.gui.rando_stuff_tab.unblock_for_5s'))
        self._subplace_unblock_btn.clicked.connect(self._on_subplace_unblock_for_5s)
        subplace_blacklist_row.addWidget(self._subplace_unblock_btn)
        subplace_blacklist_row.addStretch()
        subplace_blacklist_layout.addLayout(subplace_blacklist_row)

        self._subplace_block_radio = QRadioButton(tr('ui.gui.rando_stuff_tab.block_subplace'))
        self._subplace_stall_radio = QRadioButton(
            tr('ui.gui.rando_stuff_tab.infinitely_stall_subplace')
        )
        if self._subplace_block_mode == 'stall':
            self._subplace_stall_radio.setChecked(True)
        else:
            self._subplace_block_radio.setChecked(True)
        self._subplace_block_radio.toggled.connect(
            lambda checked=False: self._set_subplace_block_mode('block', checked)
        )
        self._subplace_stall_radio.toggled.connect(
            lambda checked=False: self._set_subplace_block_mode('stall', checked)
        )
        subplace_blacklist_layout.addWidget(self._subplace_block_radio)
        subplace_blacklist_layout.addWidget(self._subplace_stall_radio)
        self._subplace_blacklist_section.add_widget(subplace_blacklist_body)
        self._subplace_blacklist_proxy_gate = ProxyGate(
            self._subplace_blacklist_section,
            compact=True,
        )
        root.addWidget(self._subplace_blacklist_proxy_gate)

        # Give all spare viewport height to the trailing spacer.  Without a
        # positive stretch factor QVBoxLayout distributes that space back
        # across Preferred collapsible cards, so collapsed sections keep
        # giant empty bodies instead of shrinking to their headers.
        root.addStretch(1)

        footer_widget = QWidget()
        footer_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        footer_layout = QHBoxLayout(footer_widget)
        footer_layout.setContentsMargins(8, 4, 8, 4)
        footer_layout.addStretch()
        clear_cache_btn = QPushButton(tr('ui.gui.rando_stuff_tab.clear_cache'))
        clear_cache_btn.clicked.connect(self._clear_roblox_cache)
        footer_layout.addWidget(clear_cache_btn)

        scroll.setWidget(container)
        outer.addWidget(scroll)
        outer.addWidget(footer_widget)

        # Connections
        self.setLayout(outer)
        self._update_container_bg()
        self._btn.clicked.connect(self._on_rejoin_clicked)
        self._multi_chk.toggled.connect(self._on_multi_instance_toggled)
        self._username_save_chk.toggled.connect(self._on_username_spoofer_save_toggled)
        self._username_others_input.textChanged.connect(
            lambda _text='': self._on_username_spoofer_changed()
        )
        self._username_others_apply_chk.toggled.connect(
            lambda _checked=False: self._on_username_spoofer_changed()
        )
        self._username_others_verified_chk.toggled.connect(
            lambda _checked=False: self._on_username_spoofer_changed()
        )
        self._username_self_input.textChanged.connect(
            lambda _text='': self._on_username_spoofer_changed()
        )
        self._username_self_apply_chk.toggled.connect(
            lambda _checked=False: self._on_username_spoofer_changed()
        )
        self._username_self_verified_chk.toggled.connect(
            lambda _checked=False: self._on_username_spoofer_changed()
        )
        self._username_self_game_creator_chk.toggled.connect(
            lambda _checked=False: self._on_username_spoofer_changed()
        )

    @override
    def changeEvent(self, a0: QEvent) -> None:
        super().changeEvent(a0)
        if a0.type() == QEvent.Type.PaletteChange:
            self._update_container_bg()

    def _update_container_bg(self) -> None:
        """Keep the Miscellaneous tab background aligned with the tab theme."""
        pal = self.palette()
        win_light = pal.window().color().lightness()
        alt_light = pal.alternateBase().color().lightness()
        if win_light < 128 and alt_light <= win_light:
            bg = 'background-color: rgb(64, 64, 64);'
        else:
            bg = 'background-color: palette(alternate-base);'
        self._misc_container.setStyleSheet(f'QWidget#_FleasionMiscContainer {{ {bg} }}')

    def set_proxy_features_enabled(self, enabled: bool) -> None:
        for gate_name in (
            '_rejoin_proxy_gate',
            '_subplace_blacklist_proxy_gate',
            '_username_spoofer_proxy_gate',
        ):
            gate = getattr(self, gate_name, None)
            if gate is not None:
                gate.set_proxy_enabled(enabled)

    def _clear_roblox_cache(self) -> None:
        window = _delete_cache_window_type()()
        window.show()

    # Rejoin

    def _on_reserved_fields_changed(self, *_: object) -> None:
        place_id = self._place_id_input.text().strip()
        access_code = self._access_code_input.text().strip()
        with self._lock:
            self._last_place_id = place_id or None
            self._last_access_code = access_code or None

    def _on_rejoin_clicked(self) -> None:
        place_id = self._place_id_input.text().strip()
        access_code = self._access_code_input.text().strip()
        with self._lock:
            if not place_id or not access_code:
                log_buffer.log(
                    'randostuff',
                    'No reserved server placeID/accessCode set yet - join one first or enter them manually.',
                )
                return
            self._last_place_id = place_id
            self._last_access_code = access_code
            self._doing_rejoin = True
        log_buffer.log('randostuff', f'Rejoin triggered - placeId={place_id}')
        if not launch_as_standard_user(f'roblox://placeId={place_id}'):
            log_buffer.log('randostuff', 'Failed to launch Roblox without elevation')

    def _update_labels(self, place_id: object, access_code: object) -> None:
        def _do() -> None:
            self._place_id_input.setText(str(place_id))
            self._access_code_input.setText(str(access_code))
            self._rejoin_timer_secs = 300
            self._lbl_timer.setText(tr('ui.gui.rando_stuff_tab.timer_5_00'))
            self._rejoin_timer.start()

        self._on_main(_do)

    def _tick_rejoin_timer(self) -> None:
        self._rejoin_timer_secs -= 1
        if self._rejoin_timer_secs <= 0:
            self._rejoin_timer.stop()
            self._lbl_timer.setText(tr('ui.gui.rando_stuff_tab.timer_expired'))
        else:
            m, s = divmod(self._rejoin_timer_secs, 60)
            self._lbl_timer.setText(
                tr('ui.gui.rando_stuff_tab.timer_value_value', value0=m, value1=s)
            )

    def _show_reserved_server_help(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle(tr('ui.gui.rando_stuff_tab.reserved_server_info'))
        msg.setText(tr('ui.gui.rando_stuff_tab.b_what_the_hell_is_a_reserved'))
        msg.setIcon(QMessageBox.Icon.NoIcon)
        msg.exec()

    def _show_subplace_blacklist_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr('ui.gui.rando_stuff_tab.blacklist_subplace'))
        dialog.resize(400, 350)
        if icon_path := get_icon_path():
            dialog.setWindowIcon(QIcon(str(icon_path)))

        layout = QVBoxLayout()

        title = QLabel(tr('ui.gui.rando_stuff_tab.blacklisted_subplace_ids'))
        title.setStyleSheet('font-weight: bold;')
        layout.addWidget(title)

        hint = QLabel(tr('ui.gui.rando_stuff_tab.enter_subplace_ids_separated_by_commas_spaces'))
        hint.setStyleSheet('color: gray; font-size: 9pt;')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        text_edit = QTextEdit()
        text_edit.setAcceptRichText(False)
        text_edit.setPlaceholderText(tr('ui.gui.rando_stuff_tab.e_g_1818_1234567890_9876543210'))

        if self._subplace_blacklisted_ids:
            text_edit.setPlainText(', '.join(sorted(self._subplace_blacklisted_ids, key=int)))
        layout.addWidget(text_edit)

        search_layout = QHBoxLayout()
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_edit = QLineEdit()
        search_edit.setPlaceholderText(tr('ui.gui.rando_stuff_tab.search_ids'))
        search_layout.addWidget(search_edit)
        search_layout.addStretch()
        status_label = QLabel('')
        status_label.setStyleSheet('color: #888; font-size: 9pt;')
        search_layout.addWidget(status_label)
        apply_btn = QPushButton(tr('ui.gui.rando_stuff_tab.apply_blacklist'))
        search_layout.addWidget(apply_btn)
        layout.addLayout(search_layout)

        dialog.setLayout(layout)

        last_search_query = ['']

        def _search_id() -> None:
            query = search_edit.text().strip()
            if not query:
                return
            doc = text_edit.document()
            if query != last_search_query[0]:
                last_search_query[0] = query
                text_edit.moveCursor(text_edit.textCursor().MoveOperation.Start)
            cursor = doc.find(query, text_edit.textCursor())
            if cursor.isNull():
                cursor = doc.find(query)
            if not cursor.isNull():
                text_edit.setTextCursor(cursor)
                text_edit.ensureCursorVisible()
                status_label.setText('')
            else:
                status_label.setText(tr('ui.gui.rando_stuff_tab.id_value_not_found', value0=query))
                status_label.setStyleSheet('color: #cc5555; font-size: 9pt;')

        search_edit.returnPressed.connect(_search_id)
        search_edit.textChanged.connect(lambda: status_label.setText(''))

        def _apply() -> None:
            ids = self._parse_numeric_id_list(text_edit.toPlainText().strip())
            self._subplace_blacklisted_ids = set(ids)
            if self._config is not None:
                self._config.subplace_blacklist = ids
            count = len(self._subplace_blacklisted_ids)
            status_label.setText(
                tr(
                    'ui.gui.rando_stuff_tab.blacklist_applied_value',
                    value0=tr_count(count, 'count.id.one', 'count.id.other'),
                )
            )
            status_label.setStyleSheet('color: #55cc55; font-size: 9pt;')
            if self._subplace_blacklisted_ids:
                ordered = ', '.join(
                    sorted(
                        self._subplace_blacklisted_ids,
                        key=lambda x: int(x) if x.isdigit() else 0,
                    )
                )
                log_buffer.log(
                    'subplace',
                    f'Subplace blacklist updated: {format_count(count, "ID")} active - {ordered}',
                )
            else:
                log_buffer.log('subplace', 'Subplace blacklist cleared')

        apply_btn.clicked.connect(_apply)

        dialog.exec()

    # Multi-instance

    def _on_multi_instance_toggled(self, checked: bool, persist: bool = True) -> None:
        if checked and not IS_WINDOWS:
            _set_signals_blocked(self._multi_chk, blocked=True)
            self._multi_chk.setChecked(False)
            _set_signals_blocked(self._multi_chk, blocked=False)
            if persist and self._config is not None:
                self._config.multi_instance_launching = False
            log_buffer.log('multiinstance', 'Multi-instance launching is only available on Windows')
            return
        if persist and self._config is not None:
            self._config.multi_instance_launching = checked
        if checked:
            self._multi_stop.clear()
            self._multi_thread = threading.Thread(target=self._multi_instance_loop, daemon=True)
            self._multi_thread.start()
            log_buffer.log('multiinstance', 'Enabled — watching for ROBLOX_singletonEvent')
        else:
            self._multi_stop.set()
            log_buffer.log('multiinstance', 'Disabled')

    def _update_multi_instance_pids(self, stripped_pids: set[int]) -> None:
        current_pids = self._get_roblox_pids()

        # Only strip singletons if there is more than 1 instance running.
        if len(current_pids) > 1:
            for pid in current_pids - stripped_pids:
                log_buffer.log(
                    'multiinstance',
                    f'Multiple PIDs detected ({len(current_pids)}). Stripping singleton for PID {pid}',
                )
                threading.Thread(
                    target=self._close_singleton_for_pid,
                    args=(pid,),
                    daemon=True,
                ).start()
                stripped_pids.add(pid)

        # Clean up to prevent building up old PIDs
        stripped_pids.intersection_update(current_pids)

    def _multi_instance_loop(self) -> None:
        stripped_pids: set[int] = set()
        while not self._multi_stop.wait(0.2):
            _run_contained_action(
                lambda: self._update_multi_instance_pids(stripped_pids),
                lambda exc: log_buffer.log('multiinstance', f'Error: {exc}'),
            )

    def _get_roblox_pids(self) -> set[int]:
        if TYPE_CHECKING:
            kernel32 = cast('_Kernel32', object())
        else:
            kernel32 = ctypes.windll.kernel32
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.restype = wintypes.BOOL

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ('dwSize', wintypes.DWORD),
                ('cntUsage', wintypes.DWORD),
                ('th32ProcessID', wintypes.DWORD),
                ('th32DefaultHeapID', ctypes.c_size_t),
                ('th32ModuleID', wintypes.DWORD),
                ('cntThreads', wintypes.DWORD),
                ('th32ParentProcessID', wintypes.DWORD),
                ('pcPriClassBase', ctypes.c_long),
                ('dwFlags', wintypes.DWORD),
                ('szExeFile', ctypes.c_wchar * 260),
            ]

        snap = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if not snap:
            return set()
        pids: set[int] = set()
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

    def _close_singleton_for_pid(self, pid: int) -> None:
        """Retry closing ROBLOX_singletonEvent in `pid` until found or process exits/stop set."""
        while not self._multi_stop.is_set():
            closed = False

            def _scan() -> None:
                nonlocal closed
                closed = self._scan_and_close_singleton(pid)

            if not _run_contained_action(
                _scan,
                lambda exc: log_buffer.log('multiinstance', f'Error scanning PID {pid}: {exc}'),
            ):
                return
            if closed:
                return
            self._multi_stop.wait(0.1)

    def _scan_and_close_singleton(self, pid: int) -> bool:
        """Scan `pid` for a ROBLOX_singletonEvent handle and close it. Returns True if closed."""
        if TYPE_CHECKING:
            ntdll = cast('_Ntdll', object())
            kernel32 = cast('_Kernel32', object())
            kernelbase = cast('_KernelBase', object())
        else:
            ntdll = ctypes.windll.ntdll
            kernel32 = ctypes.windll.kernel32
            kernelbase = ctypes.windll.kernelbase

        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.DuplicateHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernelbase.CompareObjectHandles.restype = wintypes.BOOL
        ntdll.NtQueryInformationProcess.restype = ctypes.c_ulong

        synchronize = 0x00100000
        process_dup_handle = 0x0040
        process_query_information = 0x0400
        duplicate_close_source = 0x00000001
        duplicate_same_access = 0x00000002
        status_info_length_mismatch = 0xC0000004
        status_success = 0x00000000
        process_handle_information = 51

        class _ProcHandleEntry(ctypes.Structure):
            _fields_ = [
                ('HandleValue', ctypes.c_size_t),
                ('HandleCount', ctypes.c_size_t),
                ('PointerCount', ctypes.c_size_t),
                ('GrantedAccess', wintypes.ULONG),
                ('ObjectTypeIndex', wintypes.ULONG),
                ('HandleAttributes', wintypes.ULONG),
                ('Reserved', wintypes.ULONG),
            ]

        entry_size = ctypes.sizeof(_ProcHandleEntry)
        header_size = ctypes.sizeof(ctypes.c_size_t) * 2
        current_proc = ctypes.c_void_p(-1)

        inherit_handle = False
        our_handle = kernel32.OpenEventW(synchronize, inherit_handle, 'ROBLOX_singletonEvent')
        if not our_handle:
            return False  # event doesn't exist yet

        proc = kernel32.OpenProcess(
            process_dup_handle | process_query_information,
            inherit_handle,
            pid,
        )
        if not proc:
            kernel32.CloseHandle(our_handle)
            msg = f'OpenProcess failed for PID {pid} — process may have exited'
            raise RuntimeError(msg)

        found = False
        try:
            size = 4096
            while True:
                buf = (ctypes.c_ubyte * size)()
                ret_len = wintypes.ULONG(0)
                status = ntdll.NtQueryInformationProcess(
                    proc, process_handle_information, buf, size, ctypes.byref(ret_len)
                )
                if status == status_info_length_mismatch:
                    size = ret_len.value + 4096
                    continue
                break

            if status != status_success:
                return False

            buf_bytes = bytes(buf)
            num = ctypes.c_size_t.from_buffer_copy(
                buf_bytes[: ctypes.sizeof(ctypes.c_size_t)]
            ).value
            offset = header_size
            for _ in range(num):
                e = _ProcHandleEntry.from_buffer_copy(buf_bytes[offset : offset + entry_size])
                offset += entry_size

                dup = wintypes.HANDLE()
                if not kernel32.DuplicateHandle(
                    proc,
                    wintypes.HANDLE(e.HandleValue),
                    current_proc,
                    ctypes.byref(dup),
                    0,
                    inherit_handle,
                    duplicate_same_access,
                ):
                    continue

                is_same = kernelbase.CompareObjectHandles(our_handle, dup)
                kernel32.CloseHandle(dup)
                if not is_same:
                    continue

                dup2 = wintypes.HANDLE()
                kernel32.DuplicateHandle(
                    proc,
                    wintypes.HANDLE(e.HandleValue),
                    current_proc,
                    ctypes.byref(dup2),
                    0,
                    inherit_handle,
                    duplicate_close_source,
                )
                kernel32.CloseHandle(dup2)
                log_buffer.log('multiinstance', f'Closed ROBLOX_singletonEvent in PID {pid}')
                found = True
                break
        finally:
            kernel32.CloseHandle(proc)
            kernel32.CloseHandle(our_handle)

        return found

    def _close_singleton_event(self) -> None:
        """One-shot: close ROBLOX_singletonEvent in all current Roblox processes."""
        for pid in self._get_roblox_pids():
            _run_contained_action(
                lambda pid=pid: self._scan_and_close_singleton(pid),
                lambda exc, pid=pid: log_buffer.log('multiinstance', f'Error in PID {pid}: {exc}'),
            )

    # Account Manager

    def _on_game_link_changed(self, text: str) -> None:
        if _parse_optional_place_id(self._subplace_id_input.text()):
            if self._auto_filled_for_place is not None:
                self._job_id_input.clear()
                self._auto_filled_for_place = None
            return

        place_id, link_code = _parse_game_link(text.strip())
        if place_id and not link_code:
            # Normal game link — auto-fill stored jobId if field is empty or was auto-filled
            stored_job = self._game_jobs.get(place_id, '')
            current = self._job_id_input.text().strip()
            if not current or self._auto_filled_for_place is not None:
                self._job_id_input.setText(stored_job)
                self._auto_filled_for_place = place_id if stored_job else None
        elif link_code:
            # Private server link — clear any auto-filled jobId
            if self._auto_filled_for_place is not None:
                self._job_id_input.clear()
                self._auto_filled_for_place = None
        elif self._auto_filled_for_place is not None:
            self._job_id_input.clear()
            self._auto_filled_for_place = None

    def _on_subplace_id_changed(self, text: str) -> None:
        if _parse_optional_place_id(text):
            if self._auto_filled_for_place is not None:
                self._job_id_input.clear()
                self._auto_filled_for_place = None
            return
        self._on_game_link_changed(self._private_server_input.text())

    def _set_selected_account(self, username: str) -> None:
        username = (username or '').strip()
        if not username:
            self._selected_label.setText(tr('ui.gui.rando_stuff_tab.selected_none'))
            return
        self._selected_label.setText(tr('ui.gui.rando_stuff_tab.selected_value', value0=username))
        self.selected_account_changed.emit(username)

    def _resolve_current_user(self) -> None:
        """Background thread: read the active Roblox cookie and update the selected label."""
        cookie = get_roblosecurity()
        if not cookie:
            return
        try:
            resp = _authenticated_user_response(cookie)
            data = _response_json_object(resp) if resp.status_code == 200 else None
        except _requests.RequestException, RuntimeError, TypeError, ValueError:
            return
        if not isinstance(data, dict):
            return
        username = data.get('name', '')
        user_id = data.get('id')
        with self._lock:
            self._username_spoofer_current_user_id = str(user_id) if user_id is not None else None
            self._username_spoofer_current_username = str(username or '')
        self._push_username_spoofer_current_user()
        if username:

            def _update(u: str = str(username)) -> None:
                self._set_selected_account(u)

            self._on_main(_update)

    def _check_cookies_on_boot(self) -> None:
        """Background thread: validate every stored cookie and flag expired ones in the list."""
        for idx, acc in enumerate(self._accounts):
            cookie = _decrypt_cookie(acc.get('cookie', ''))
            expired = not cookie
            if cookie:
                try:
                    resp = _authenticated_user_response(cookie)
                    expired = resp.status_code != 200
                except _requests.RequestException:
                    pass  # Network error — don't mark as expired
            if expired:

                def _mark(i: int = idx) -> None:
                    item = self._account_list.item(i)
                    if item:
                        item.setText(tr('ui.gui.rando_stuff_tab.expired_right_click_to_update'))

                self._on_main(_mark)

    def _populate_account_list(self) -> None:
        self._account_list.clear()
        for acc in self._accounts:
            item = QListWidgetItem(acc.get('username') or tr('rando.account.unknown_parenthesized'))
            self._account_list.addItem(item)

    def _on_add_account(self) -> None:
        dlg = AddAccountDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        username = dlg.result_username
        cookie = dlg.result_cookie
        if not username or not cookie:
            return
        self._accounts.append({'username': username, 'cookie': _encrypt_cookie(cookie)})
        _save_accounts(self._accounts)
        self._populate_account_list()
        # Select the newly added entry
        self._account_list.setCurrentRow(len(self._accounts) - 1)

    def _on_import_browser_account(self) -> None:
        self._import_browser_btn.setEnabled(False)
        self._import_browser_btn.setText(tr('ui.gui.rando_stuff_tab.importing'))

        def _import() -> None:
            cookie, source = discover_browser_roblosecurity(
                include_keychain=True, explicit_import=True
            )
            if not cookie:
                self._on_main(lambda: self._finish_browser_import(None, None, None))
                return
            try:
                session = _requests.Session()
                session.trust_env = False
                session.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
                response = session.get(
                    'https://users.roblox.com/v1/users/authenticated', timeout=10
                )
                username = (
                    str(response.json().get('name') or '') if response.status_code == 200 else ''
                )
            except _requests.RequestException, TypeError, ValueError:
                username = ''
            self._on_main(lambda: self._finish_browser_import(username, cookie, source))

        threading.Thread(target=_import, daemon=True, name='fleasion-browser-cookie-import').start()

    def _finish_browser_import(
        self, username: str | None, cookie: str | None, source: str | None
    ) -> None:
        self._import_browser_btn.setEnabled(True)
        self._import_browser_btn.setText(tr('ui.gui.rando_stuff_tab.import_browser_login'))
        if not username or not cookie:
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.browser_login_not_found'),
                tr('ui.gui.rando_stuff_tab.no_usable_roblox_login_was_found_in'),
            )
            return

        existing_index = next(
            (
                index
                for index, account in enumerate(self._accounts)
                if account.get('username') == username
            ),
            None,
        )
        account: Account = {'username': username, 'cookie': _encrypt_cookie(cookie)}
        if existing_index is None:
            self._accounts.append(account)
            selected_index = len(self._accounts) - 1
        else:
            self._accounts[existing_index] = account
            selected_index = existing_index
        _save_accounts(self._accounts)
        self._populate_account_list()
        self._account_list.setCurrentRow(selected_index)
        log_buffer.log('accounts', f'Imported Roblox browser login for {username} from {source}')
        QMessageBox.information(
            self,
            tr('ui.gui.rando_stuff_tab.browser_login_imported'),
            tr('ui.gui.rando_stuff_tab.imported_value_from_value', value0=username, value1=source),
        )

    def _on_account_ctx_menu(self, pos: QPoint) -> None:
        item = self._account_list.itemAt(pos)
        if item is None:
            return
        idx = self._account_list.row(item)
        menu = QMenu(self)
        change_action = menu.addAction(tr('ui.gui.rando_stuff_tab.change_cookie'))
        remove_action = menu.addAction(tr('ui.gui.rando_stuff_tab.remove'))
        action = menu.exec(self._account_list.viewport().mapToGlobal(pos))
        if action == change_action:
            self._change_cookie(idx)
        elif action == remove_action:
            self._remove_account(idx)

    def _change_cookie(self, idx: int) -> None:
        dlg = AddAccountDialog(self, title=tr('rando.account.change_cookie_title'))
        dlg.set_ok_label(tr('rando.account.update'))
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        username = dlg.result_username
        cookie = dlg.result_cookie
        if not username or not cookie:
            return
        self._accounts[idx] = {'username': username, 'cookie': _encrypt_cookie(cookie)}
        _save_accounts(self._accounts)
        self._populate_account_list()
        self._account_list.setCurrentRow(idx)

    def _remove_account(self, idx: int) -> None:
        username = self._accounts[idx].get('username') or tr('rando.account.unknown_parenthesized')
        reply = QMessageBox.question(
            self,
            tr('ui.gui.rando_stuff_tab.remove_account'),
            tr('ui.gui.rando_stuff_tab.remove_value_from_the_list', value0=username),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._accounts.pop(idx)
        _save_accounts(self._accounts)
        self._populate_account_list()

    def _on_launch_account(self) -> None:
        log_buffer.log('accounts', 'Launch button clicked')
        acc = self._last_switched_account
        if acc is None:
            log_buffer.log('accounts', 'Launch aborted: no switched account selected')
            QMessageBox.information(
                self,
                tr('ui.gui.rando_stuff_tab.no_account_switched'),
                tr('ui.gui.rando_stuff_tab.use_switch_to_selected_first_to_pick'),
            )
            return
        cookie = _decrypt_cookie(acc.get('cookie', ''))
        if not cookie:
            log_buffer.log('accounts', 'Launch aborted: failed to decrypt cookie')
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.error'),
                tr('ui.gui.rando_stuff_tab.could_not_decrypt_the_stored_cookie'),
            )
            return
        username = acc.get('username') or tr('rando.account.unknown_parenthesized')
        link = self._private_server_input.text().strip()
        subplace_raw = self._subplace_id_input.text().strip()
        subplace_id = _parse_optional_place_id(subplace_raw)
        if subplace_raw and not subplace_id:
            log_buffer.log('accounts', f'Launch aborted: invalid subplace ID: {subplace_raw}')
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.invalid_subplace_id'),
                tr('ui.gui.rando_stuff_tab.enter_a_numeric_subplace_id_or_roblox'),
            )
            return
        job_id = _extract_job_id(self._job_id_input.text())
        log_buffer.log(
            'accounts',
            f'Launch request prepared for {username}: hasLink={"yes" if bool(link) else "no"}, '
            f'hasSubplace={"yes" if bool(subplace_id) else "no"}, hasJobId={"yes" if bool(job_id) else "no"}',
        )

        if _is_share_link(link):
            self._launch_acct_btn.setEnabled(False)
            self._launch_acct_btn.setText(tr('ui.gui.rando_stuff_tab.resolving'))

            def _resolve_thread() -> None:
                place_id, link_code = _resolve_share_link(link, cookie)

                def _done() -> None:
                    self._launch_acct_btn.setEnabled(True)
                    self._launch_acct_btn.setText(tr('ui.gui.rando_stuff_tab.launch'))
                    if place_id and link_code:
                        resolved = f'https://www.roblox.com/games/{place_id}/game?privateServerLinkCode={link_code}'
                        self._private_server_input.setText(resolved)
                        threading.Thread(
                            target=self._launch_account_thread,
                            args=(
                                cookie,
                                username,
                                resolved,
                                job_id,
                                subplace_id or '',
                            ),
                            daemon=True,
                        ).start()
                    else:
                        QMessageBox.warning(
                            self,
                            tr('ui.gui.rando_stuff_tab.unsupported_link_format'),
                            tr(
                                'ui.gui.rando_stuff_tab.this_looks_like_a_roblox_share_link',
                                value0=link,
                            ),
                        )

                self._on_main(_done)

            threading.Thread(target=_resolve_thread, daemon=True).start()
            return

        threading.Thread(
            target=self._launch_account_thread,
            args=(cookie, username, link, job_id, subplace_id or ''),
            daemon=True,
        ).start()

    def _on_switch_account(self) -> None:
        idx = self._account_list.currentRow()
        if idx < 0:
            QMessageBox.information(
                self,
                tr('ui.gui.rando_stuff_tab.no_selection'),
                tr('ui.gui.rando_stuff_tab.select_an_account_first'),
            )
            return
        acc = self._accounts[idx]
        cookie = _decrypt_cookie(acc.get('cookie', ''))
        if not cookie:
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.error'),
                tr('ui.gui.rando_stuff_tab.could_not_decrypt_the_stored_cookie'),
            )
            return
        username = acc.get('username') or tr('rando.account.unknown_parenthesized')
        if IS_MACOS:
            self._last_switched_account = acc
            self._set_selected_account(username)
            log_buffer.log(
                'accounts',
                f'Selected account for Fleasion launches on macOS: {username}',
            )
            QMessageBox.information(
                self,
                tr('ui.gui.rando_stuff_tab.account_selected'),
                tr('ui.gui.rando_stuff_tab.this_account_will_be_used_for_fleasion'),
            )
            return

        def _switch() -> None:
            self._write_cookie_to_dat(cookie)
            self._last_switched_account = acc
            self._set_selected_account(username)
            platform_name = _linux_client_display_name() if IS_LINUX else 'Roblox'
            log_buffer.log(
                'accounts',
                f'Switched {platform_name} cookie to account: {username}',
            )

        def _handle_switch_error(exc: Exception) -> None:
            if isinstance(exc, LinuxAuthWriteError):
                log_buffer.log('accounts', f'Linux account switch was not performed: {exc.code}')
                QMessageBox.warning(
                    self, tr('ui.gui.rando_stuff_tab.account_switch_unavailable'), str(exc)
                )
                return
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.error'),
                tr('ui.gui.rando_stuff_tab.failed_to_write_cookie_value', value0=exc),
            )

        _run_contained_action(_switch, _handle_switch_error)

    def _show_selected_account_launch_failed(self, username: str, reason: str) -> None:
        def _warn() -> None:
            QMessageBox.warning(
                self,
                tr('ui.gui.rando_stuff_tab.selected_account_launch_failed'),
                tr(
                    'ui.gui.rando_stuff_tab.fleasion_could_not_launch_roblox_as_value',
                    value0=username,
                    value1=reason,
                ),
            )

        self._on_main(_warn)

    def _get_launch_auth_ticket(self, cookie: str, mode: str) -> str | None:
        log_buffer.log('accounts', f'Requesting auth ticket for {mode} launch')
        ticket = _get_auth_ticket(cookie)
        if ticket:
            log_buffer.log('accounts', f'Auth-ticket retrieval succeeded for {mode} launch')
        else:
            log_buffer.log('accounts', f'Auth-ticket retrieval failed for {mode} launch')
        return ticket

    def _launch_account_thread(
        self,
        cookie: str,
        username: str,
        private_server_link: str = '',
        job_id: str = '',
        subplace_id: str = '',
    ) -> None:
        if IS_WINDOWS:
            _run_contained_action(
                lambda: self._write_cookie_to_dat(cookie),
                lambda exc: log_buffer.log('accounts', f'Failed to write cookie file: {exc}'),
            )
        else:
            platform_name = 'macOS' if IS_MACOS else _linux_client_display_name()
            log_buffer.log(
                'accounts',
                f'Launching selected account on {platform_name} with an auth ticket; '
                'the local signed-in account is unchanged during launch',
            )

        exe = _find_roblox_exe()
        if not exe:
            log_buffer.log('accounts', 'Roblox executable resolution failed before launch')

            def _warn_roblox_not_found() -> None:
                QMessageBox.warning(
                    self,
                    tr('ui.gui.rando_stuff_tab.roblox_not_found'),
                    tr('ui.gui.rando_stuff_tab.could_not_locate_roblox_player_is_roblox'),
                )

            self._on_main(_warn_roblox_not_found)
            return
        log_buffer.log('accounts', f'Resolved Roblox executable: {exe}')

        place_id, link_code = _parse_game_link(private_server_link)
        launch_place_id = subplace_id or place_id
        log_buffer.log(
            'accounts',
            f'Launch parse result: placeId={place_id or "(none)"}, '
            f'subplaceId={subplace_id or "(none)"}, launchPlaceId={launch_place_id or "(none)"}, '
            f'linkCode={"present" if bool(link_code) else "missing"}, jobId={"present" if bool(job_id) else "missing"}',
        )
        root_place_id = self._normalize_numeric_id(place_id)
        normalized_launch_place_id = self._normalize_numeric_id(launch_place_id)
        is_distinct_subplace_launch = (
            bool(subplace_id)
            and root_place_id is not None
            and normalized_launch_place_id is not None
            and normalized_launch_place_id != root_place_id
        )
        with self._lock:
            self._account_manager_teleport_place_id = (
                normalized_launch_place_id if is_distinct_subplace_launch else None
            )
        if is_distinct_subplace_launch and root_place_id is not None:
            ok = _preseed_root_place_for_subplace(root_place_id, cookie)
            if not ok:
                log_buffer.log(
                    'accounts',
                    f'Subplace root pre-seed failed for root {root_place_id}',
                )
        launch_ok = False
        if place_id and link_code and launch_place_id:
            # Private server launch
            ticket = self._get_launch_auth_ticket(cookie, 'private-server')
            if ticket:
                access_code = _get_access_code(place_id, link_code, cookie) or link_code
                roblox_player_uri = _build_auth_ticket_private_server_uri(
                    ticket,
                    launch_place_id,
                    access_code=access_code,
                    link_code=link_code,
                )
                log_buffer.log(
                    'accounts',
                    f'Launching Roblox URI to placeId={launch_place_id} (private server)',
                )
                launch_ok = launch_as_standard_user(roblox_player_uri)
                if not launch_ok:
                    log_buffer.log('accounts', 'Failed to launch Roblox URI without elevation')
            else:
                if IS_MACOS:
                    self._show_selected_account_launch_failed(
                        username,
                        tr('rando.account.launch_failed.private_server_ticket'),
                    )
                    launch_ok = False
                    log_buffer.log('accounts', f'Launch failed for account: {username}')
                    return
                log_buffer.log('accounts', 'Failed to get auth ticket, falling back to deeplink')
                deeplink = (
                    f'roblox://experiences/start?placeId={launch_place_id}&linkCode={link_code}'
                )
                log_buffer.log('accounts', f'Launching Roblox executable fallback: {exe}')
                exe_started = launch_as_standard_user(exe)
                if not exe_started:
                    log_buffer.log('accounts', 'Failed to launch Roblox Player without elevation')
                time.sleep(3)
                log_buffer.log(
                    'accounts',
                    f'Launching Roblox deeplink to placeId={launch_place_id} with linkCode',
                )
                deeplink_started = launch_as_standard_user(deeplink)
                if not deeplink_started:
                    log_buffer.log('accounts', 'Failed to launch Roblox deeplink without elevation')
                launch_ok = exe_started and deeplink_started
        elif launch_place_id:
            # Normal game link — optionally join a specific job
            ticket = self._get_launch_auth_ticket(cookie, 'place' if not job_id else 'job-id')
            if ticket:
                if not job_id:
                    with self._lock:
                        self._account_manager_capture_place_id = launch_place_id
                roblox_player_uri = _build_auth_ticket_place_uri(
                    ticket,
                    launch_place_id,
                    job_id=job_id,
                )
                if job_id:
                    log_buffer.log(
                        'accounts',
                        f'Launching Roblox URI to placeId={launch_place_id}, gameId={job_id}',
                    )
                else:
                    log_buffer.log('accounts', f'Launching Roblox URI to placeId={launch_place_id}')
                launch_ok = launch_as_standard_user(roblox_player_uri)
                if not launch_ok:
                    log_buffer.log('accounts', 'Failed to launch Roblox URI without elevation')
            else:
                if IS_MACOS:
                    self._show_selected_account_launch_failed(
                        username,
                        tr('rando.account.launch_failed.place_ticket'),
                    )
                    launch_ok = False
                    log_buffer.log('accounts', f'Launch failed for account: {username}')
                    return
                log_buffer.log('accounts', 'Failed to get auth ticket, falling back to deeplink')
                if not job_id:
                    with self._lock:
                        self._account_manager_capture_place_id = launch_place_id
                    # Proxy intercept will handle jobId capture; set pending job ID to empty
                    with self._lock:
                        self._account_manager_job_id = ''
                else:
                    with self._lock:
                        self._account_manager_job_id = job_id
                deeplink = f'roblox://experiences/start?placeId={launch_place_id}'
                log_buffer.log('accounts', f'Launching Roblox executable fallback: {exe}')
                exe_started = launch_as_standard_user(exe)
                if not exe_started:
                    log_buffer.log('accounts', 'Failed to launch Roblox Player without elevation')
                time.sleep(3)
                log_buffer.log(
                    'accounts',
                    f'Launching Roblox deeplink to placeId={launch_place_id}',
                )
                deeplink_started = launch_as_standard_user(deeplink)
                if not deeplink_started:
                    log_buffer.log('accounts', 'Failed to launch Roblox deeplink without elevation')
                launch_ok = exe_started and deeplink_started
        else:
            ticket = self._get_launch_auth_ticket(cookie, 'app')
            if ticket:
                roblox_player_uri = _build_auth_ticket_app_uri(ticket)
                log_buffer.log('accounts', 'Launching Roblox app URI for selected account')
                launch_ok = launch_as_standard_user(roblox_player_uri)
                if not launch_ok:
                    log_buffer.log('accounts', 'Failed to launch Roblox app URI without elevation')
                    if IS_MACOS:
                        self._show_selected_account_launch_failed(
                            username,
                            tr('rando.account.launch_failed.macos_app_uri'),
                        )
            elif IS_MACOS:
                self._show_selected_account_launch_failed(
                    username,
                    tr('rando.account.launch_failed.app_ticket'),
                )
            else:
                log_buffer.log(
                    'accounts',
                    'Failed to get auth ticket, falling back to executable launch',
                )
                log_buffer.log('accounts', f'Launching Roblox executable: {exe}')
                launch_ok = launch_as_standard_user(exe)
                if not launch_ok:
                    log_buffer.log(
                        'accounts',
                        'Failed to launch Roblox Player without elevation',
                    )
        if launch_ok:
            log_buffer.log('accounts', f'Launched Roblox for account: {username}')
        else:
            log_buffer.log('accounts', f'Launch failed for account: {username}')

    def _write_cookie_to_dat(self, cookie: str) -> None:
        """Replace .ROBLOSECURITY in the platform client's local account store."""
        if IS_MACOS or not (IS_WINDOWS or IS_LINUX):
            msg = 'Local cookie switching is not supported on this platform'
            raise RuntimeError(msg)
        if IS_WINDOWS and not ROBLOX_COOKIES_PATH.exists():
            log_buffer.log('accounts', 'RobloxCookies.dat not found - launch Roblox once first')
            msg = 'RobloxCookies.dat was not found. Launch Roblox once first.'
            raise RuntimeError(msg)
        if not set_roblosecurity(cookie):
            if IS_LINUX:
                platform_name = _linux_client_display_name()
                log_buffer.log('accounts', f'Failed to update {platform_name} local cookie store')
                msg = 'cookie_store_write_failed'
                raise LinuxAuthWriteError(
                    msg,
                    f'Could not update the {platform_name} local cookie store.',
                )
            log_buffer.log(
                'accounts',
                f'Failed to update RobloxCookies.dat at {ROBLOX_COOKIES_PATH}',
            )
            msg = 'Could not update RobloxCookies.dat.'
            raise RuntimeError(msg)
        self._account_switched = True

    def is_multi_instance_enabled(self) -> bool:
        """Return True if the multi-instance checkbox is checked."""
        return IS_WINDOWS and self._multi_chk.isChecked()

    def close_singleton_event(self) -> None:
        """Close the Roblox singleton event to allow a new instance, then clear the switched flag."""
        if not IS_WINDOWS:
            self._account_switched = False
            return
        _run_contained_action(
            self._close_singleton_event,
            lambda exc: log_buffer.log('multiinstance', f'close_singleton_event error: {exc}'),
        )
        self._account_switched = False

    def get_roblox_exe(self) -> str | None:
        """Return the path to the platform Roblox Player executable, or None if not found."""
        return _find_roblox_exe()

    # R6 <-> R15 Animation Converter

    def _ac_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            tr('ui.gui.rando_stuff_tab.open_animation_file'),
            '',
            tr('ui.gui.rando_stuff_tab.roblox_animation_rbxmx_rbxm_all_files'),
        )
        if not path:
            return
        p = Path(path)
        try:
            data = p.read_bytes()
        except OSError as exc:
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.read_error_value', value0=exc))
            return

        # Detect rig from original bytes (binary parser handles .bin/.rbxm natively)
        try:
            detect_rig = cast(
                'Callable[[bytes], str]',
                _import_attr('fleasion.utils.anim_converter', 'detect_rig'),
            )
            rig = detect_rig(data)
        except ImportError, KeyError, RuntimeError, TypeError, ValueError:
            rig = tr('rando.rig.unknown')

        # Auto-convert binary .rbxm -> .rbxmx so _ac_convert has XML to work with
        if p.suffix.lower() == '.rbxm':
            try:
                rbxm_to_rbxmx = cast(
                    'Callable[[bytes], bytes]',
                    _import_attr('fleasion.utils.anim_converter', 'rbxm_to_rbxmx'),
                )
                data = rbxm_to_rbxmx(data)
                self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.auto_converted_rbxm_rbxmx'))
            except (ImportError, KeyError, RuntimeError, TypeError, ValueError) as exc:
                self._ac_status_lbl.setText(
                    tr('ui.gui.rando_stuff_tab.rbxm_conversion_failed_value', value0=exc)
                )
                return
        else:
            self._ac_status_lbl.setText('')

        self._ac_xml_bytes = data
        self._ac_source_path = p

        self._ac_rig_lbl.setText(tr('ui.gui.rando_stuff_tab.detected_rig_value', value0=rig))
        self._ac_file_lbl.setText(p.name)
        self._ac_to_r6_btn.setEnabled(rig == 'R15')
        self._ac_to_r15_btn.setEnabled(rig == 'R6')

    def _convert_loaded_animation(self, target: str) -> None:
        converter_module = 'fleasion.utils.r15_to_r6'
        convert_keyframe_r6_to_r15 = cast(
            'Callable[..., None]',
            _import_attr(converter_module, 'convert_keyframe_r6_to_r15'),
        )
        convert_keyframe_r15_to_r6 = cast(
            'Callable[..., None]',
            _import_attr(converter_module, 'convert_keyframe_r15_to_r6'),
        )
        sanitize_xml = cast(
            'Callable[[bytes], str]',
            _import_attr(converter_module, 'sanitize_xml'),
        )
        rig_module = 'fleasion.utils.rig_data'
        r6_joints = _import_attr(rig_module, 'R6_JOINTS')
        r6_parts = _import_attr(rig_module, 'R6_PARTS')
        r15_joints = _import_attr(rig_module, 'R15_JOINTS')
        r15_parts = _import_attr(rig_module, 'R15_PARTS')

        xml_bytes = self._ac_xml_bytes
        if b'CurveAnimation' in xml_bytes:
            curve_anim_to_keyframe = cast(
                'Callable[[bytes], bytes]',
                _import_attr('fleasion.utils.anim_converter', 'curve_anim_to_keyframe'),
            )
            xml_bytes = curve_anim_to_keyframe(xml_bytes)

        root = DefusedElementTree.fromstring(sanitize_xml(xml_bytes))
        ks = root.find("Item[@class='KeyframeSequence']")
        if ks is None:
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.no_keyframesequence_found'))
            return
        keyframes = ks.findall("Item[@class='Keyframe']")
        if not keyframes:
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.no_keyframes_found'))
            return

        if target == 'R6':
            for keyframe in keyframes:
                convert_keyframe_r15_to_r6(
                    keyframe,
                    r6_parts,
                    r6_joints,
                    r15_parts,
                    r15_joints,
                )
        else:
            for keyframe in keyframes:
                convert_keyframe_r6_to_r15(
                    keyframe,
                    r6_parts,
                    r6_joints,
                    r15_parts,
                    r15_joints,
                )

        suffix = '_r6' if target == 'R6' else '_r15'
        default_name = self._ac_source_path.stem + suffix + '.rbxmx'
        default_dir = str(self._ac_source_path.parent)
        out_str, _ = QFileDialog.getSaveFileName(
            self,
            tr('ui.gui.rando_stuff_tab.save_converted_animation'),
            f'{default_dir}/{default_name}',
            tr('ui.gui.rando_stuff_tab.roblox_animation_rbxmx_all_files'),
        )
        if not out_str:
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.cancelled'))
            return
        out_path = Path(out_str)
        out_path.write_bytes(
            DefusedElementTree.tostring(root, encoding='utf-8', xml_declaration=True)
        )
        self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.saved_value', value0=out_path.name))

    def _ac_convert(self, target: str) -> None:
        if not hasattr(self, '_ac_xml_bytes'):
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.no_file_loaded_2'))
            return

        try:
            self._convert_loaded_animation(target)
        except (
            DefusedXmlException,
            DefusedElementTree.ParseError,
            ImportError,
            KeyError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
        ) as exc:
            self._ac_status_lbl.setText(tr('ui.gui.rando_stuff_tab.error_value', value0=exc))

    # Proxy interceptor hooks

    def _handle_private_game_request(self, flow: ProxyFlow) -> None:
        body = _json_object(flow.request.content)
        if self._apply_account_manager_subplace_teleport(body):
            flow.request.raw_content = json.dumps(body, separators=(',', ':')).encode('utf-8')

    def _handle_reserved_game_request(self, flow: ProxyFlow) -> None:
        body = _json_object(flow.request.content)
        if self._apply_account_manager_subplace_teleport(body):
            flow.request.raw_content = json.dumps(body, separators=(',', ':')).encode('utf-8')
        place_id = body.get('placeId')
        access_code = body.get('accessCode')
        attempt_id = body.get('gameJoinAttemptId')
        normalized_place_id = self._normalize_numeric_id(place_id)
        if (
            normalized_place_id is not None
            and normalized_place_id in self._subplace_blacklisted_ids
            and not self._is_subplace_unblock_active()
        ):
            self._drop_subplace_join(
                flow,
                normalized_place_id,
                attempt_id=str(attempt_id) if attempt_id else None,
            )
            return
        session_id = flow.request.headers.get('Roblox-Session-Id', '')
        if place_id is None or access_code is None:
            return
        with self._lock:
            self._last_place_id = _preserve_str(place_id)
            self._last_access_code = _preserve_str(access_code)
            self._last_session_id = session_id or None
        has_session = bool(session_id)
        log_buffer.log(
            'randostuff',
            f'Logged reserved server — placeId={place_id}, '
            f'sessionHeader={"present" if has_session else "missing"}',
        )
        self._update_labels(place_id, access_code)

    def _precheck_blacklisted_join(self, flow: ProxyFlow) -> bool:
        try:
            body = _json_object(flow.request.content)
        except UnicodeDecodeError, json.JSONDecodeError, TypeError:
            return False
        blocked_place_id = self._normalize_numeric_id(body.get('placeId'))
        if (
            blocked_place_id is None
            or blocked_place_id not in self._subplace_blacklisted_ids
            or self._is_subplace_unblock_active()
        ):
            return False
        attempt_id = body.get('gameJoinAttemptId')
        self._drop_subplace_join(
            flow,
            blocked_place_id,
            attempt_id=str(attempt_id) if attempt_id else None,
        )
        return True

    def _account_request_body(self, flow: ProxyFlow) -> tuple[dict[str, object] | None, bool]:
        try:
            body = _json_object(flow.request.content)
        except UnicodeDecodeError, json.JSONDecodeError, TypeError:
            return None, False
        return body, self._apply_account_manager_subplace_teleport(body)

    def _redirect_pending_job(
        self,
        flow: ProxyFlow,
        account_body: dict[str, object] | None,
        pending_job: str,
    ) -> None:
        body = account_body if account_body is not None else _json_object(flow.request.content)
        body['gameId'] = pending_job
        flow.request.url = 'https://gamejoin.roblox.com/v1/join-game-instance'
        flow.request.raw_content = json.dumps(body, separators=(',', ':')).encode('utf-8')
        with self._lock:
            self._account_manager_job_id = ''
        log_buffer.log(
            'accounts',
            f'Redirected join-game -> join-game-instance with jobId={pending_job}',
        )

    def _handle_wanted_join_request(self, flow: ProxyFlow, req_path: str) -> None:
        if self._precheck_blacklisted_join(flow):
            return

        account_body, account_body_modified = self._account_request_body(flow)
        if req_path == '/v1/join-game':
            with self._lock:
                pending_job = self._account_manager_job_id
            if pending_job:
                _run_proxy_action(
                    'accounts',
                    'Failed to intercept join-game for jobId',
                    lambda: self._redirect_pending_job(flow, account_body, pending_job),
                )
                return

        if account_body_modified and account_body is not None:
            flow.request.raw_content = json.dumps(account_body, separators=(',', ':')).encode(
                'utf-8'
            )

        try:
            req_body = (
                account_body if account_body is not None else _json_object(flow.request.content)
            )
        except UnicodeDecodeError, json.JSONDecodeError, TypeError:
            attempt_id = None
        else:
            attempt_id = req_body.get('gameJoinAttemptId')

        with self._lock:
            doing = self._doing_rejoin
            active_id = self._active_rejoin_attempt_id
            place_id = self._last_place_id
            access_code = self._last_access_code
            session_id = self._last_session_id

            if doing:
                self._doing_rejoin = False
                self._active_rejoin_attempt_id = attempt_id
                active_id = attempt_id
            elif active_id is None or attempt_id != active_id:
                return

        if place_id is None or access_code is None:
            log_buffer.log(
                'randostuff',
                'Rejoin flag set but no reserved server stored — aborting.',
            )
            with self._lock:
                self._active_rejoin_attempt_id = None
            return

        normalized_place_id = self._normalize_numeric_id(place_id)
        if (
            normalized_place_id is not None
            and normalized_place_id in self._subplace_blacklisted_ids
            and not self._is_subplace_unblock_active()
        ):
            self._drop_subplace_join(
                flow,
                normalized_place_id,
                attempt_id=str(attempt_id) if attempt_id else None,
            )
            with self._lock:
                self._active_rejoin_attempt_id = None
                self._awaiting_rejoin_response = False
            return

        new_payload = {
            'placeId': place_id,
            'accessCode': access_code,
            'isTeleport': True,
            'isImmersiveAdsTeleport': False,
        }
        flow.request.url = 'https://gamejoin.roblox.com/v1/join-reserved-game'
        flow.request.raw_content = json.dumps(new_payload).encode('utf-8')
        if session_id:
            flow.request.headers['Roblox-Session-Id'] = session_id
        log_buffer.log(
            'randostuff',
            'Rejoin request -> POST gamejoin.roblox.com/v1/join-reserved-game',
        )
        with self._lock:
            self._awaiting_rejoin_response = True

    def request(self, flow: ProxyFlow) -> None:
        url = flow.request.pretty_url
        if 'gamejoin.roblox.com' not in url:
            return

        req_path = urlparse(url).path
        if req_path == self._PRIVATE_GAME_ENDPOINT:
            _run_proxy_action(
                'accounts',
                'Failed to parse join-private-game body',
                lambda: self._handle_private_game_request(flow),
            )
            return
        if req_path == self._RESERVED_GAME_ENDPOINT:
            _run_proxy_action(
                'randostuff',
                'Failed to parse join-reserved-game body',
                lambda: self._handle_reserved_game_request(flow),
            )
            return
        if req_path in self._WANTED_ENDPOINTS:
            self._handle_wanted_join_request(flow, req_path)

    def _capture_account_manager_job(self, flow: ProxyFlow, capture_place_id: str) -> None:
        response = flow.response
        if response is None:
            msg = 'Proxy response is unavailable while capturing account-manager job ID'
            raise RuntimeError(msg)
        resp_json = _json_object(response.content)
        job_id = _extract_job_id(
            _preserve_str(resp_json.get('jobId') or resp_json.get('gameId') or '')
        )
        if not job_id:
            return
        self._game_jobs[capture_place_id] = job_id

        def _update_ui(jid: str = job_id, pid: str = capture_place_id) -> None:
            if not self._job_id_input.text().strip():
                self._job_id_input.setText(jid)
                self._auto_filled_for_place = pid

        self._on_main(_update_ui)
        log_buffer.log(
            'accounts',
            f'Captured jobId={job_id} for placeId={capture_place_id}',
        )

    def _clear_active_rejoin_attempt(self) -> None:
        with self._lock:
            self._active_rejoin_attempt_id = None

    def _process_rejoin_response(self, flow: ProxyFlow) -> None:
        response = flow.response
        if response is None:
            log_buffer.log('randostuff', 'Rejoin response: (none)')
            return
        resp_json = _json_object(response.content.decode('utf-8', errors='replace'))
        join_ready = bool(resp_json.get('joinScriptUrl'))
        log_buffer.log(
            'randostuff',
            f'Rejoin response status: http={response.status_code}, status={resp_json.get("status")}, '
            f'joinScriptUrl={"yes" if join_ready else "no"}',
        )
        if resp_json.get('status') == 2 or join_ready:
            self._clear_active_rejoin_attempt()
            log_buffer.log('randostuff', 'Reserved server join ready — stopping redirect.')
        elif response.status_code >= 400:
            self._clear_active_rejoin_attempt()
            log_buffer.log('randostuff', 'Reserved server join error — stopping redirect.')

    def response(self, flow: ProxyFlow) -> None:
        if 'gamejoin.roblox.com' not in flow.request.pretty_url:
            return

        req_path = urlparse(flow.request.pretty_url).path
        self._clear_account_manager_subplace_teleport_if_complete(flow, req_path)

        with self._lock:
            capture_place_id = self._account_manager_capture_place_id
        if capture_place_id and req_path in {'/v1/join-game', '/v1/join-game-instance'}:
            _run_proxy_action(
                'accounts',
                'Failed to capture jobId from response',
                lambda: self._capture_account_manager_job(flow, capture_place_id),
            )
            with self._lock:
                self._account_manager_capture_place_id = None

        with self._lock:
            waiting = self._awaiting_rejoin_response
            if waiting:
                self._awaiting_rejoin_response = False
        if not waiting:
            return

        _run_proxy_action(
            'randostuff',
            'Could not parse rejoin response JSON',
            lambda: self._process_rejoin_response(flow),
            on_error=self._clear_active_rejoin_attempt,
        )
