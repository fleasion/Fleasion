"""Shared helpers for reading/writing Roblox's .ROBLOSECURITY cookie."""

import base64
import json
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


def _read_cookie_payload(path: Path) -> tuple[dict, bytes] | None:
    if win32crypt is None:
        _log_auth_failure(
            'win32crypt-unavailable',
            'Could not read Roblox auth cookie: pywin32/win32crypt is unavailable',
        )
        return None
    if not path.exists():
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


def get_roblosecurity(path: Path | None = None) -> str | None:
    """Return the .ROBLOSECURITY cookie value from the local Roblox cookie store.

    Uses Windows DPAPI (win32crypt) to decrypt the stored cookie data.
    Returns None if the cookie is not found or cannot be decrypted.
    """
    cookie_path = Path(path) if path is not None else ROBLOX_COOKIES_PATH
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
