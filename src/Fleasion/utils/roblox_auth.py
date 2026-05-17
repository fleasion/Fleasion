"""Shared helpers for reading/writing Roblox's .ROBLOSECURITY cookie."""

import base64
import json
import os
import re
from pathlib import Path

from .logging import log_buffer
from .paths import LOCAL_APPDATA

try:
    import win32crypt  # type: ignore
except Exception:
    win32crypt = None


ROBLOX_COOKIES_PATH = LOCAL_APPDATA / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_LOGGED_AUTH_FAILURES: set[str] = set()
_ROBLOX_COOKIE_RELATIVE_PATH = Path('AppData') / 'Local' / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
_SUCCESSFUL_COOKIE_PATH: Path | None = None
_LAST_AUTH_FAILURE_DETAILS: dict[str, object] = {}


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
    if win32crypt is None:
        _log_auth_failure(
            'win32crypt-unavailable',
            'Could not read Roblox auth cookie: pywin32/win32crypt is unavailable',
        )
        return None
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


def get_roblosecurity(path: Path | None = None) -> str | None:
    """Return the .ROBLOSECURITY cookie value from a Roblox cookie store.

    Uses Windows DPAPI (win32crypt) to decrypt the stored cookie data.
    When no explicit path is supplied, tries the configured LocalAppData path
    first, then likely current-user paths, then exact cookie locations under
    C:\\Users.
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

    _LAST_AUTH_FAILURE_DETAILS = {
        'local_appdata': str(LOCAL_APPDATA),
        'default_cookie_path': str(ROBLOX_COOKIES_PATH),
        'userprofile': os.environ.get('USERPROFILE') or '',
        'username': os.environ.get('USERNAME') or '',
        'home': str(Path.home()),
        'attempted_paths': attempted,
        'existing_paths': existing,
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
