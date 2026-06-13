"""Shared helpers for reading/writing Roblox's .ROBLOSECURITY cookie."""

import base64
import json
import os
import re
import sys
import threading
import time
from pathlib import Path

from .logging import log_buffer
from .paths import CONFIG_DIR, CONFIG_FILE, LOCAL_APPDATA, USER_HOME
from .secure_tokens import decrypt_token, encrypt_token

try:
    import win32crypt  # type: ignore
except Exception:
    win32crypt = None


if sys.platform == 'darwin':
    ROBLOX_COOKIES_PATH = USER_HOME / 'Library' / 'Roblox' / 'RobloxCookies.dat'
else:
    ROBLOX_COOKIES_PATH = LOCAL_APPDATA / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_LOGGED_AUTH_FAILURES: set[str] = set()
_ROBLOX_COOKIE_RELATIVE_PATH = Path('AppData') / 'Local' / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_MACOS_COOKIE_CANDIDATES = (
    Path('Library') / 'Roblox' / 'RobloxCookies.dat',
    Path('Library') / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat',
)
_SUCCESSFUL_COOKIE_PATH: Path | None = None
_LAST_AUTH_FAILURE_DETAILS: dict[str, object] = {}
_BROWSER_COOKIE_CACHE: str | None = None
_BROWSER_COOKIE_SOURCE = ''
_BROWSER_AUTO_DISCOVERY_ATTEMPTED = False
_BROWSER_DISCOVERY_LOCK = threading.Lock()
_LAST_BROWSER_AUTH_VALIDATION_DETAIL = ''
_MANUAL_AUTH_TOKEN_FILE = CONFIG_DIR / 'manual_auth_token.json'
_MANUAL_AUTH_TOKEN_KEY_FILE = CONFIG_DIR / 'manual_auth_token.key'
_MACOS_AUTH_BROWSER_NAMES = ('Chrome', 'Safari', 'Firefox', 'Brave', 'Edge', 'Chromium', 'Opera', 'Vivaldi')
_AUTH_READY_CONDITION = threading.Condition()
_AUTH_READY_COOKIE: str | None = None


def _log_auth_failure(key: str, message: str) -> None:
    """Log an auth problem once per process so repeated asset loads do not spam."""
    if key in _LOGGED_AUTH_FAILURES:
        return
    _LOGGED_AUTH_FAILURES.add(key)
    log_buffer.log('Auth', message)


def _extract_roblosecurity(cookie_text: str) -> str | None:
    """Extract .ROBLOSECURITY from known Roblox cookie-store text formats."""
    if not cookie_text:
        return None

    # Common Netscape-cookie rows:
    #   ... \t.ROBLOSECURITY\t<value>
    # and compact cookie-header forms:
    #   .ROBLOSECURITY=<value>; ...
    patterns = (
        r'(?:^|[\t ;])\.ROBLOSECURITY\s+([^\s;]+)',
        r'(?:^|[\t ;])\.ROBLOSECURITY=([^\s;]+)',
    )
    for pattern in patterns:
        match = re.search(pattern, cookie_text)
        if match:
            return match.group(1).strip().strip('"')
    return None


def _replace_roblosecurity(cookie_text: str, cookie: str) -> tuple[str, int]:
    """Replace .ROBLOSECURITY in known Roblox cookie-store text formats."""
    patterns = (
        r'((?:^|[\t ;])\.ROBLOSECURITY\s+)([^\s;]+)',
        r'((?:^|[\t ;])\.ROBLOSECURITY=)([^\s;]+)',
    )
    for pattern in patterns:
        new_text, count = re.subn(pattern, lambda m: m.group(1) + cookie, cookie_text, count=1)
        if count:
            return new_text, count
    return cookie_text.rstrip() + f"\n.ROBLOSECURITY\t{cookie}", 0


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _normalise_key(path: Path) -> str:
    return os.path.normcase(str(_safe_resolve(path)))


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _add_candidate(candidates: list[tuple[str, Path]], seen: set[str], source: str, path: Path) -> None:
    key = _normalise_key(path)
    if key in seen:
        return
    seen.add(key)
    candidates.append((source, path))


def _iter_user_profile_cookie_candidates() -> list[tuple[str, Path]]:
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()

    _add_candidate(candidates, seen, 'LOCALAPPDATA', ROBLOX_COOKIES_PATH)

    if sys.platform == 'darwin':
        for relative in _MACOS_COOKIE_CANDIDATES:
            _add_candidate(candidates, seen, 'macOS-home', USER_HOME / relative)
        return candidates

    userprofile = os.environ.get('USERPROFILE')
    if userprofile:
        _add_candidate(candidates, seen, 'USERPROFILE', Path(userprofile) / _ROBLOX_COOKIE_RELATIVE_PATH)

    home = Path.home()
    if home:
        _add_candidate(candidates, seen, 'Path.home', home / _ROBLOX_COOKIE_RELATIVE_PATH)

    system_drive = (os.environ.get('SystemDrive') or 'C:').strip().rstrip('\\/')
    if re.fullmatch(r'[A-Za-z]:', system_drive):
        users_root = Path(f'{system_drive}/') / 'Users'
    else:
        users_root = Path(system_drive) / 'Users'
    try:
        with os.scandir(users_root) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                _add_candidate(candidates, seen, 'all-users', Path(entry.path) / _ROBLOX_COOKIE_RELATIVE_PATH)
    except OSError as exc:
        _log_auth_failure(
            f'user-scan:{users_root}:{type(exc).__name__}',
            f'Could not scan Windows user profiles for RobloxCookies.dat: {type(exc).__name__}: {exc}',
        )

    return candidates


def _read_cookie_payload(path: Path) -> tuple[dict, bytes] | None:
    if not _path_exists(path):
        _log_auth_failure(
            f'missing:{path}',
            f'RobloxCookies.dat not found at {path}',
        )
        return None

    try:
        with path.open('r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as exc:
        _log_auth_failure(
            f'json:{path}:{type(exc).__name__}',
            f'Failed to read RobloxCookies.dat at {path}: {type(exc).__name__}: {exc}',
        )
        return None

    cookies_data = data.get('CookiesData')
    if not cookies_data:
        _log_auth_failure(
            f'empty:{path}',
            f'RobloxCookies.dat at {path} does not contain CookiesData',
        )
        return None

    try:
        enc = base64.b64decode(cookies_data)
    except Exception as exc:
        _log_auth_failure(
            f'base64:{path}:{type(exc).__name__}',
            f'Failed to decode RobloxCookies.dat CookiesData at {path}: {type(exc).__name__}: {exc}',
        )
        return None

    if win32crypt is None:
        if sys.platform == 'darwin':
            _log_auth_failure(
                f'macos-cookie-unsupported:{path}',
                f'RobloxCookies.dat at {path} is not decryptable with Windows DPAPI on macOS',
            )
        else:
            _log_auth_failure(
                'win32crypt-unavailable',
                'Could not read Roblox auth cookie: pywin32/win32crypt is unavailable',
            )
        return data, enc

    try:
        dec = win32crypt.CryptUnprotectData(enc, None, None, None, 0)[1]
    except Exception as exc:
        _log_auth_failure(
            f'dpapi:{path}:{type(exc).__name__}:{exc}',
            f'Failed to decrypt RobloxCookies.dat at {path}: {type(exc).__name__}: {exc}',
        )
        return None

    return data, dec


def _get_roblosecurity_from_path(cookie_path: Path) -> str | None:
    try:
        payload = _read_cookie_payload(cookie_path)
        if payload is None:
            return None
        _data, dec = payload
        # Use latin-1 first for a lossless byte-to-text mapping; fall back just
        # in case Roblox changes the plaintext encoding.
        for encoding in ('latin-1', 'utf-8'):
            try:
                cookie = _extract_roblosecurity(dec.decode(encoding, errors='ignore'))
                if cookie:
                    return cookie
            except Exception:
                continue
        _log_auth_failure(
            f'not-found:{cookie_path}',
            f'Decrypted RobloxCookies.dat at {cookie_path}, but .ROBLOSECURITY was not found',
        )
    except Exception as exc:
        _log_auth_failure(
            f'unexpected-read:{cookie_path}:{type(exc).__name__}:{exc}',
            f'Unexpected error while reading Roblox auth cookie at {cookie_path}: {type(exc).__name__}: {exc}',
        )
    return None


def get_auth_failure_details() -> dict[str, object]:
    """Return diagnostics for the most recent default cookie lookup failure."""
    return dict(_LAST_AUTH_FAILURE_DETAILS)


def _mark_auth_cookie_available(cookie: str) -> None:
    global _AUTH_READY_COOKIE
    if not cookie:
        return
    with _AUTH_READY_CONDITION:
        _AUTH_READY_COOKIE = cookie
        _AUTH_READY_CONDITION.notify_all()


def notify_auth_source_changed() -> None:
    """Wake auth waiters after the user changes browser/manual-token settings."""
    with _AUTH_READY_CONDITION:
        _AUTH_READY_CONDITION.notify_all()


def wait_for_roblosecurity(*, include_keychain_browsers: bool = True, retry_interval: float = 2.0) -> str | None:
    """Wait until a usable Roblox token is available.

    On macOS, account-aware background jobs use this while the user is approving
    browser access. Other platforms keep the old single lookup behavior.
    """
    if sys.platform != 'darwin':
        return get_roblosecurity(include_keychain_browsers=include_keychain_browsers)

    while True:
        cookie = get_roblosecurity(include_keychain_browsers=include_keychain_browsers)
        if cookie:
            return cookie
        with _AUTH_READY_CONDITION:
            _AUTH_READY_CONDITION.wait(timeout=max(0.25, retry_interval))


def _validate_roblosecurity(cookie: str) -> bool | None:
    """Return True/False for validation, or None when validation is inconclusive."""
    global _LAST_BROWSER_AUTH_VALIDATION_DETAIL

    if not cookie:
        _LAST_BROWSER_AUTH_VALIDATION_DETAIL = 'empty-cookie'
        return False
    try:
        import requests

        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        try:
            sess.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
        except Exception:
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        resp = sess.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
        _LAST_BROWSER_AUTH_VALIDATION_DETAIL = f'HTTP {resp.status_code}'
        if resp.status_code == 200:
            return True
        if resp.status_code in (401, 403):
            return False
        return None
    except Exception as exc:
        _LAST_BROWSER_AUTH_VALIDATION_DETAIL = f'{type(exc).__name__}: {exc}'
        _log_auth_failure(
            f'browser-auth-validate:{type(exc).__name__}',
            f'Could not validate Roblox browser login: {type(exc).__name__}: {exc}',
        )
        return None


def _get_configured_macos_auth_source() -> str:
    if sys.platform != 'darwin':
        return ''
    try:
        with CONFIG_FILE.open('r', encoding='utf-8') as f:
            settings = json.load(f)
    except Exception:
        return ''
    source = str(settings.get('macos_auth_source') or '')
    valid = {'', 'manual', *_MACOS_AUTH_BROWSER_NAMES}
    return source if source in valid else ''


def store_manual_roblosecurity(cookie: str) -> bool:
    """Store a manually imported Roblox token encrypted for local reuse."""
    if not cookie or not cookie.strip():
        return False
    try:
        _MANUAL_AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'token': encrypt_token(cookie.strip(), _MANUAL_AUTH_TOKEN_KEY_FILE),
        }
        with _MANUAL_AUTH_TOKEN_FILE.open('w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        try:
            os.chmod(_MANUAL_AUTH_TOKEN_FILE, 0o600)
        except OSError:
            pass
        _mark_auth_cookie_available(cookie.strip())
        return True
    except Exception as exc:
        _log_auth_failure(
            f'manual-auth-token-write:{type(exc).__name__}:{exc}',
            f'Could not store manually imported Roblox token: {type(exc).__name__}: {exc}',
        )
        return False


def get_manual_roblosecurity() -> str | None:
    """Return the encrypted manually imported Roblox token, if present."""
    if not _MANUAL_AUTH_TOKEN_FILE.exists():
        return None
    try:
        with _MANUAL_AUTH_TOKEN_FILE.open('r', encoding='utf-8') as f:
            payload = json.load(f)
        token_payload = str(payload.get('token') or '')
        if not token_payload:
            return None
        cookie = decrypt_token(token_payload, _MANUAL_AUTH_TOKEN_KEY_FILE)
        return cookie.strip() if cookie else None
    except Exception as exc:
        _log_auth_failure(
            f'manual-auth-token-read:{type(exc).__name__}:{exc}',
            f'Could not read manually imported Roblox token: {type(exc).__name__}: {exc}',
        )
        return None


def _browser_cookie_loaders(include_keychain: bool):
    import browser_cookie3

    loaders = [('Firefox', browser_cookie3.firefox)]
    if include_keychain:
        # Keep the common browsers first for explicit imports that search every
        # supported store.
        loaders = [
            ('Chrome', browser_cookie3.chrome),
            ('Safari', browser_cookie3.safari),
            ('Brave', browser_cookie3.brave),
            ('Edge', browser_cookie3.edge),
            ('Chromium', browser_cookie3.chromium),
            ('Opera', browser_cookie3.opera),
            ('Vivaldi', browser_cookie3.vivaldi),
            *loaders,
        ]
    return loaders


def discover_browser_roblosecurity(
    include_keychain: bool = False,
    *,
    explicit_import: bool = False,
    browser: str | None = None,
) -> tuple[str | None, str]:
    """Discover the Roblox cookie from local browsers without logging its value.

    Firefox discovery is prompt-free on macOS. Chrome-family browsers and
    Safari are only queried when ``include_keychain`` is True because macOS may
    ask the user to approve browser-data access.
    """
    global _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE, _BROWSER_AUTO_DISCOVERY_ATTEMPTED

    if browser is not None and browser not in _MACOS_AUTH_BROWSER_NAMES:
        return None, ''
    with _BROWSER_DISCOVERY_LOCK:
        if not explicit_import and _BROWSER_COOKIE_CACHE and (not browser or browser == _BROWSER_COOKIE_SOURCE):
            return _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE
        if not explicit_import and not include_keychain and _BROWSER_AUTO_DISCOVERY_ATTEMPTED:
            return None, ''
        if not explicit_import and not include_keychain:
            _BROWSER_AUTO_DISCOVERY_ATTEMPTED = True

        try:
            loaders = _browser_cookie_loaders(include_keychain)
        except Exception as exc:
            _log_auth_failure(
                f'browser-cookie-library:{type(exc).__name__}',
                f'Browser cookie discovery is unavailable: {type(exc).__name__}: {exc}',
            )
            return None, ''
        if browser:
            loaders = [(source, loader) for source, loader in loaders if source == browser]

        now = time.time()
        for source, loader in loaders:
            try:
                jar = loader(domain_name='roblox.com')
                candidates = [
                    cookie
                    for cookie in jar
                    if cookie.name == '.ROBLOSECURITY'
                    and cookie.value
                    and 'roblox.com' in (cookie.domain or '').lower()
                    and (not cookie.expires or cookie.expires > now)
                ]
            except Exception as exc:
                _log_auth_failure(
                    f'browser-cookie:{source}:{type(exc).__name__}:{exc}',
                    f'Could not read Roblox browser login from {source}: {type(exc).__name__}: {exc}',
                )
                continue

            if not candidates:
                continue
            cookie = max(candidates, key=lambda item: item.expires or 0).value.strip()
            if not cookie or any(char.isspace() for char in cookie):
                continue
            if include_keychain:
                validation = _validate_roblosecurity(cookie)
                if validation is False:
                    detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'invalid'
                    log_buffer.log('Auth', f'Browser login discovered from {source} failed validation ({detail}); skipping')
                    continue
            _BROWSER_COOKIE_CACHE = cookie
            _BROWSER_COOKIE_SOURCE = source
            _LAST_AUTH_FAILURE_DETAILS.clear()
            log_buffer.log('Auth', f'Using domain-scoped Roblox browser login discovered from {source}')
            return cookie, source

    return None, ''


def get_roblosecurity(path: Path | None = None, *, include_keychain_browsers: bool = False) -> str | None:
    """Return the .ROBLOSECURITY cookie value from a Roblox cookie store.

    On Windows, uses DPAPI (win32crypt) to decrypt the stored cookie data. On
    macOS, tries known Roblox cookie-file locations if Roblox creates them; a
    normal macOS install may only expose app-local account metadata, not the
    browser-style .ROBLOSECURITY cookie. Set ``include_keychain_browsers`` for
    an explicit user-facing macOS browser permission request.
    """
    global _SUCCESSFUL_COOKIE_PATH, _LAST_AUTH_FAILURE_DETAILS

    if path is not None:
        return _get_roblosecurity_from_path(Path(path))

    attempted: list[str] = []
    existing: list[str] = []

    if _SUCCESSFUL_COOKIE_PATH is not None:
        attempted.append(str(_SUCCESSFUL_COOKIE_PATH))
        cookie = _get_roblosecurity_from_path(_SUCCESSFUL_COOKIE_PATH)
        if cookie:
            _mark_auth_cookie_available(cookie)
            return cookie
        _SUCCESSFUL_COOKIE_PATH = None

    for source, cookie_path in _iter_user_profile_cookie_candidates():
        attempted.append(str(cookie_path))
        if source == 'all-users' and not _path_exists(cookie_path):
            continue
        if _path_exists(cookie_path):
            existing.append(str(cookie_path))

        cookie = _get_roblosecurity_from_path(cookie_path)
        if cookie:
            _SUCCESSFUL_COOKIE_PATH = cookie_path
            if cookie_path != ROBLOX_COOKIES_PATH:
                _log_auth_failure(
                    f'fallback-success:{cookie_path}',
                    f'Using Roblox auth cookie discovered from {source}: {cookie_path}',
                )
            _LAST_AUTH_FAILURE_DETAILS = {}
            _mark_auth_cookie_available(cookie)
            return cookie

    if sys.platform == 'darwin':
        auth_source = _get_configured_macos_auth_source()
        if auth_source == 'manual':
            manual_cookie = get_manual_roblosecurity()
            browser_source = 'manual'
            if manual_cookie:
                _LAST_AUTH_FAILURE_DETAILS = {}
                _mark_auth_cookie_available(manual_cookie)
                return manual_cookie
        elif auth_source:
            browser_cookie, browser_source = discover_browser_roblosecurity(
                include_keychain=include_keychain_browsers,
                browser=auth_source,
            )
            if browser_cookie:
                _LAST_AUTH_FAILURE_DETAILS = {}
                _mark_auth_cookie_available(browser_cookie)
                return browser_cookie
        else:
            browser_cookie, browser_source = discover_browser_roblosecurity(
                include_keychain=False,
            )
            if browser_cookie:
                _LAST_AUTH_FAILURE_DETAILS = {}
                _mark_auth_cookie_available(browser_cookie)
                return browser_cookie
    else:
        browser_source = ''

    _LAST_AUTH_FAILURE_DETAILS = {
        'local_appdata': str(LOCAL_APPDATA),
        'default_cookie_path': str(ROBLOX_COOKIES_PATH),
        'userprofile': os.environ.get('USERPROFILE') or '',
        'username': os.environ.get('USERNAME') or '',
        'home': str(USER_HOME),
        'attempted_paths': attempted,
        'existing_paths': existing,
        'browser_source': browser_source,
    }
    _log_auth_failure(
        'all-cookie-candidates-failed',
        (
            'Could not find a usable Roblox auth cookie after checking '
            f'{len(attempted)} candidate path(s); {len(existing)} RobloxCookies.dat file(s) existed'
        ),
    )
    return None


def set_roblosecurity(cookie: str, path: Path | None = None) -> bool:
    """Replace the .ROBLOSECURITY value in RobloxCookies.dat and re-encrypt it."""
    cookie_path = Path(path) if path is not None else ROBLOX_COOKIES_PATH
    try:
        payload = _read_cookie_payload(cookie_path)
        if payload is None:
            return False

        data, dec = payload
        cookie_text = dec.decode('latin-1')
        new_text, _count = _replace_roblosecurity(cookie_text, cookie)
        if win32crypt is None:
            _log_auth_failure(
                f'write-unsupported:{cookie_path}',
                f'Cannot encrypt RobloxCookies.dat at {cookie_path} on this platform',
            )
            return False
        new_enc = win32crypt.CryptProtectData(new_text.encode('latin-1'), None, None, None, None, 0)
        data['CookiesData'] = base64.b64encode(new_enc).decode('ascii')
        with cookie_path.open('w', encoding='utf-8') as f:
            json.dump(data, f)
        return True
    except Exception as exc:
        _log_auth_failure(
            f'write:{cookie_path}:{type(exc).__name__}:{exc}',
            f'Failed to write Roblox auth cookie at {cookie_path}: {type(exc).__name__}: {exc}',
        )
        return False
