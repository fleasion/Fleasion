"""Shared helpers for reading/writing Roblox's .ROBLOSECURITY cookie."""

import base64
import json
import os
import re
import sys
import time
from pathlib import Path

from .logging import log_buffer
from .paths import CONFIG_DIR, LOCAL_APPDATA, USER_HOME

try:
    import win32crypt  # type: ignore
except Exception:
    win32crypt = None


if sys.platform == 'darwin':
    ROBLOX_COOKIES_PATH = USER_HOME / 'Library' / 'Roblox' / 'RobloxCookies.dat'
elif sys.platform.startswith('linux'):
    ROBLOX_COOKIES_PATH = USER_HOME / '.var' / 'app' / 'org.vinegarhq.Sober' / 'data' / 'sober' / 'cookies'
else:
    ROBLOX_COOKIES_PATH = LOCAL_APPDATA / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_LOGGED_AUTH_FAILURES: set[str] = set()
_ROBLOX_COOKIE_RELATIVE_PATH = Path('AppData') / 'Local' / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_MACOS_COOKIE_CANDIDATES = (
    Path('Library') / 'Roblox' / 'RobloxCookies.dat',
    Path('Library') / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat',
)
_LINUX_COOKIE_CANDIDATES = (
    Path('.var') / 'app' / 'org.vinegarhq.Sober' / 'data' / 'sober' / 'cookies',
)
_SUCCESSFUL_COOKIE_PATH: Path | None = None
_LAST_AUTH_FAILURE_DETAILS: dict[str, object] = {}
_BROWSER_COOKIE_CACHE: str | None = None
_BROWSER_COOKIE_SOURCE = ''
_BROWSER_AUTO_DISCOVERY_ATTEMPTED = False
_BROWSER_AUTH_CACHE_FILE = CONFIG_DIR / 'browser_auth_cache.json'
_BROWSER_AUTH_CACHE_KEY_FILE = CONFIG_DIR / 'browser_auth_cache.key'
_PERSISTENT_BROWSER_AUTH_SOURCES = {'Chrome', 'Brave', 'Edge', 'Chromium', 'Opera', 'Vivaldi'}
_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT = False
_LAST_BROWSER_AUTH_VALIDATION_DETAIL = ''


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

    if sys.platform.startswith('linux'):
        for relative in _LINUX_COOKIE_CANDIDATES:
            _add_candidate(candidates, seen, 'Sober', USER_HOME / relative)
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

    if sys.platform.startswith('linux') and path.name == 'cookies':
        try:
            return {}, path.read_bytes()
        except Exception as exc:
            _log_auth_failure(
                f'linux-cookie-read:{path}:{type(exc).__name__}',
                f'Failed to read Sober cookie file at {path}: {type(exc).__name__}: {exc}',
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

    if sys.platform.startswith('linux'):
        return data, enc

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


def _get_macos_browser_auth_cipher(create: bool = True):
    if sys.platform != 'darwin':
        return None
    try:
        from cryptography.fernet import Fernet
    except Exception as exc:
        _log_auth_failure(
            f'browser-auth-cache-crypto:{type(exc).__name__}',
            f'macOS browser auth cache encryption is unavailable: {type(exc).__name__}: {exc}',
        )
        return None

    try:
        key_path = _BROWSER_AUTH_CACHE_KEY_FILE
        if not key_path.exists():
            if not create:
                return None
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            flags = (
                getattr(os, 'O_WRONLY', 1)
                | getattr(os, 'O_CREAT', 64)
                | getattr(os, 'O_EXCL', 128)
            )
            fd = os.open(key_path, flags, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
        else:
            key = key_path.read_bytes().strip()
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return Fernet(key)
    except Exception as exc:
        _log_auth_failure(
            f'browser-auth-cache-key:{type(exc).__name__}:{exc}',
            f'macOS browser auth cache key failed: {type(exc).__name__}: {exc}',
        )
        return None


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
            f'browser-auth-cache-validate:{type(exc).__name__}',
            f'Could not validate cached Roblox browser login: {type(exc).__name__}: {exc}',
        )
        return None


def _delete_cached_browser_roblosecurity() -> None:
    try:
        _BROWSER_AUTH_CACHE_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _log_browser_auth_cache_state(state: str, message: str, *, block_automatic_import: bool = False) -> None:
    global _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT

    _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT = block_automatic_import
    _log_auth_failure(f'browser-auth-cache-state:{state}', f'Browser auth cache state: {message}')


def _read_cached_browser_roblosecurity(*, delete_invalid: bool = True) -> tuple[str | None, str]:
    global _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT

    if sys.platform != 'darwin':
        return None, ''
    if not _BROWSER_AUTH_CACHE_FILE.exists():
        _log_browser_auth_cache_state('no-cache', 'no encrypted browser login cache exists')
        return None, ''
    if not _BROWSER_AUTH_CACHE_KEY_FILE.exists():
        _log_browser_auth_cache_state(
            'missing-key',
            'encrypted browser login cache exists but its key file is missing; preserving cache',
            block_automatic_import=True,
        )
        return None, ''

    cipher = _get_macos_browser_auth_cipher(create=False)
    if cipher is None:
        _log_browser_auth_cache_state(
            'decrypt-failed',
            'encrypted browser login cache key could not be loaded; preserving cache',
            block_automatic_import=True,
        )
        return None, ''

    try:
        with _BROWSER_AUTH_CACHE_FILE.open('r', encoding='utf-8') as f:
            payload = json.load(f)
    except json.JSONDecodeError as exc:
        _log_auth_failure(
            f'browser-auth-cache-json:{type(exc).__name__}:{exc}',
            f'Browser auth cache state: malformed JSON; preserving cache ({type(exc).__name__}: {exc})',
        )
        _log_browser_auth_cache_state(
            'malformed-json',
            'encrypted browser login cache is malformed; preserving cache and skipping automatic browser prompt',
            block_automatic_import=True,
        )
        return None, ''
    except OSError as exc:
        _log_auth_failure(
            f'browser-auth-cache-read-io:{type(exc).__name__}:{exc}',
            f'Browser auth cache state: read failed; preserving cache ({type(exc).__name__}: {exc})',
        )
        _log_browser_auth_cache_state(
            'read-failed',
            'encrypted browser login cache could not be read; preserving cache and skipping automatic browser prompt',
            block_automatic_import=True,
        )
        return None, ''

    try:
        source = str(payload.get('source') or '')
        if source not in _PERSISTENT_BROWSER_AUTH_SOURCES:
            _log_browser_auth_cache_state(
                'validation-inconclusive',
                f'cache source {source or "(missing)"} is not eligible for automatic reuse; preserving cache',
                block_automatic_import=True,
            )
            return None, ''
        encrypted = str(payload.get('cookie') or '')
        if not encrypted:
            _log_browser_auth_cache_state(
                'validation-inconclusive',
                'encrypted browser login cache has no cookie payload; preserving cache',
                block_automatic_import=True,
            )
            return None, ''
        cookie = cipher.decrypt(encrypted.encode('ascii')).decode('utf-8').strip()
    except Exception as exc:
        _log_auth_failure(
            f'browser-auth-cache-decrypt:{type(exc).__name__}:{exc}',
            f'Browser auth cache state: decrypt failed; preserving cache ({type(exc).__name__}: {exc})',
        )
        _log_browser_auth_cache_state(
            'decrypt-failed',
            'encrypted browser login cache decrypt failed; preserving cache and skipping automatic browser prompt',
            block_automatic_import=True,
        )
        return None, ''

    validation = _validate_roblosecurity(cookie)
    if validation is False:
        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'invalid'
        _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT = False
        if delete_invalid:
            _delete_cached_browser_roblosecurity()
            log_buffer.log(
                'Auth',
                f'Browser auth cache state: validation invalid ({detail}); deleted cached Roblox browser login',
            )
        else:
            _log_browser_auth_cache_state(
                'validation-invalid',
                f'validation invalid ({detail}); preserving cache for startup or explicit import',
            )
        return None, ''
    if validation is None:
        _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT = False
        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'inconclusive'
        log_buffer.log('Auth', f'Browser auth cache state: validation inconclusive ({detail}); reusing encrypted cache from {source}')
    else:
        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'valid'
        _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT = False
        log_buffer.log('Auth', f'Browser auth cache state: cache reused from {source} ({detail})')

    _LAST_AUTH_FAILURE_DETAILS.clear()
    return cookie, source


def _write_cached_browser_roblosecurity(cookie: str, source: str) -> None:
    if sys.platform != 'darwin' or source not in _PERSISTENT_BROWSER_AUTH_SOURCES:
        return
    cipher = _get_macos_browser_auth_cipher()
    if cipher is None:
        return
    try:
        _BROWSER_AUTH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'source': source,
            'cached_at': int(time.time()),
            'cookie': cipher.encrypt(cookie.encode('utf-8')).decode('ascii'),
        }
        with _BROWSER_AUTH_CACHE_FILE.open('w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        try:
            os.chmod(_BROWSER_AUTH_CACHE_FILE, 0o600)
        except OSError:
            pass
    except Exception as exc:
        _log_auth_failure(
            f'browser-auth-cache-write:{type(exc).__name__}:{exc}',
            f'Could not cache Roblox browser login: {type(exc).__name__}: {exc}',
        )


def _browser_cookie_loaders(include_keychain: bool):
    import browser_cookie3

    loaders = [('Firefox', browser_cookie3.firefox)]
    if include_keychain:
        # Check the most common macOS browser first so its Safe Storage prompt
        # is useful instead of asking for less likely browser stores first.
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


def discover_browser_roblosecurity(include_keychain: bool = False, *, explicit_import: bool = False) -> tuple[str | None, str]:
    """Discover the Roblox cookie from local browsers without logging its value.

    Firefox discovery is prompt-free on macOS. Chrome-family browsers and
    Safari are only queried when ``include_keychain`` is True because macOS may
    ask the user to approve Safe Storage or browser-data access.
    """
    global _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE, _BROWSER_AUTO_DISCOVERY_ATTEMPTED

    if not explicit_import and _BROWSER_COOKIE_CACHE:
        return _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE
    if not explicit_import:
        cached_cookie, cached_source = _read_cached_browser_roblosecurity(delete_invalid=include_keychain)
        if cached_cookie:
            _BROWSER_COOKIE_CACHE = cached_cookie
            _BROWSER_COOKIE_SOURCE = cached_source
            return cached_cookie, cached_source
    if include_keychain and _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT and not explicit_import:
        log_buffer.log(
            'Auth',
            'Skipping automatic browser login prompt because encrypted cache recovery was inconclusive; use Import Browser Login to re-import explicitly',
        )
        return None, ''
    if not include_keychain and _BROWSER_AUTO_DISCOVERY_ATTEMPTED:
        return None, ''
    if not include_keychain:
        _BROWSER_AUTO_DISCOVERY_ATTEMPTED = True

    try:
        loaders = _browser_cookie_loaders(include_keychain)
    except Exception as exc:
        _log_auth_failure(
            f'browser-cookie-library:{type(exc).__name__}',
            f'Browser cookie discovery is unavailable: {type(exc).__name__}: {exc}',
        )
        return None, ''

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
        if source in _PERSISTENT_BROWSER_AUTH_SOURCES:
            validation = _validate_roblosecurity(cookie)
            if validation is False:
                detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'invalid'
                log_buffer.log('Auth', f'Browser login discovered from {source} failed validation ({detail}); skipping')
                continue
        _BROWSER_COOKIE_CACHE = cookie
        _BROWSER_COOKIE_SOURCE = source
        _LAST_AUTH_FAILURE_DETAILS.clear()
        log_buffer.log('Auth', f'Using domain-scoped Roblox browser login discovered from {source}')
        _write_cached_browser_roblosecurity(cookie, source)
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
            return cookie

    if sys.platform == 'darwin' or sys.platform.startswith('linux'):
        browser_cookie, browser_source = discover_browser_roblosecurity(
            include_keychain=include_keychain_browsers,
        )
        if browser_cookie:
            _LAST_AUTH_FAILURE_DETAILS = {}
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
