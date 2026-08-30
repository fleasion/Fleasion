"""Shared helpers for reading/writing Roblox's .ROBLOSECURITY cookie."""

from __future__ import annotations

import base64
import contextlib
import errno
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections.abc import (
    Callable,  # ruff: ignore[typing-only-standard-library-import]
    Iterable,  # ruff: ignore[typing-only-standard-library-import]
    Mapping,  # ruff: ignore[typing-only-standard-library-import]
)
from dataclasses import dataclass
from http.cookiejar import Cookie  # ruff: ignore[typing-only-standard-library-import]
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol

from .linux_clients import SOBER_CLIENT, LinuxClientInstallation
from .logging import log_buffer
from .paths import CONFIG_DIR, CONFIG_FILE, LOCAL_APPDATA, USER_HOME
from .secure_tokens import decrypt_token, encrypt_token


class _Win32CryptLike(Protocol):
    def CryptUnprotectData(  # ruff: ignore[invalid-function-name]
        self,
        data: bytes,
        description: object,
        optional_entropy: object,
        reserved: object,
        flags: int,
    ) -> tuple[object, bytes]: ...


if TYPE_CHECKING:
    win32crypt: _Win32CryptLike | None
else:
    try:
        import win32crypt
    except Exception:  # ruff: ignore[blind-except]
        win32crypt = None


type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class _BrowserLoader(Protocol):
    def __call__(
        self,
        *,
        cookie_file: str | None = None,
        domain_name: str = '',
        key_file: str | None = None,
    ) -> Iterable[Cookie]: ...


class _BrowserCookieModule(Protocol):
    firefox: _BrowserLoader
    chrome: _BrowserLoader
    safari: _BrowserLoader
    brave: _BrowserLoader
    edge: _BrowserLoader
    chromium: _BrowserLoader
    opera: _BrowserLoader
    vivaldi: _BrowserLoader


if TYPE_CHECKING:

    def _json_object(value: object) -> JsonObject | None: ...

    def _base64_source(value: object) -> str | bytes: ...

    def _browser_cookie_module() -> _BrowserCookieModule: ...
else:

    def _json_object(value: object) -> JsonObject | None:
        return value if isinstance(value, dict) else None

    def _base64_source(value: object) -> str | bytes:
        return value

    def _browser_cookie_module() -> _BrowserCookieModule:
        import browser_cookie3  # ruff: ignore[import-outside-top-level]

        return browser_cookie3


def _set_module_state(name: str, value: object) -> None:
    globals()[name] = value


_SOBER_LOCAL_COOKIE_RELATIVE_PATH = Path('cookies')
if sys.platform == 'darwin':
    _roblox_cookies_path = USER_HOME / 'Library' / 'Roblox' / 'RobloxCookies.dat'
elif sys.platform.startswith('linux'):
    # Compatibility export for callers that only use this constant on Windows.
    # Linux discovery below resolves the selected installation through its
    # provider instead of probing this path unconditionally.
    _roblox_cookies_path = (
        SOBER_CLIENT.paths(home=USER_HOME).data_root / _SOBER_LOCAL_COOKIE_RELATIVE_PATH
    )
else:
    _roblox_cookies_path = LOCAL_APPDATA / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
ROBLOX_COOKIES_PATH = _roblox_cookies_path
_LOGGED_AUTH_FAILURES: set[str] = set()
_ROBLOX_COOKIE_RELATIVE_PATH = (
    Path('AppData') / 'Local' / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat'
)
_MACOS_COOKIE_CANDIDATES = (
    Path('Library') / 'Roblox' / 'RobloxCookies.dat',
    Path('Library') / 'Roblox' / 'LocalStorage' / 'RobloxCookies.dat',
)
_SUCCESSFUL_COOKIE_PATH: Path | None = None
_LAST_AUTH_FAILURE_DETAILS: dict[str, object] = {}
_BROWSER_COOKIE_CACHE: str | None = None
_BROWSER_COOKIE_SOURCE: str = ''
_BROWSER_AUTO_DISCOVERY_ATTEMPTED: bool = False
_BROWSER_DISCOVERY_LOCK = threading.Lock()
_BROWSER_AUTH_CACHE_FILE = CONFIG_DIR / 'browser_auth_cache.json'
_BROWSER_AUTH_CACHE_KEY_FILE = CONFIG_DIR / 'browser_auth_cache.key'
_PERSISTENT_BROWSER_AUTH_SOURCES = {
    'Chrome',
    'Brave',
    'Edge',
    'Chromium',
    'Opera',
    'Vivaldi',
}
_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT: bool = False
_LAST_BROWSER_AUTH_VALIDATION_DETAIL: str = ''
_LAST_BROWSER_AUTH_ERROR_DETAILS: dict[str, object] = {}
_MANUAL_AUTH_TOKEN_FILE = CONFIG_DIR / 'manual_auth_token.json'
_MANUAL_AUTH_TOKEN_KEY_FILE = CONFIG_DIR / 'manual_auth_token.key'
_MACOS_AUTH_BROWSER_NAMES = (
    'Chrome',
    'Safari',
    'Firefox',
    'Brave',
    'Edge',
    'Chromium',
    'Opera',
    'Vivaldi',
)
_MACOS_SAFARI_COOKIE_FILES = (
    Path('Library') / 'Cookies' / 'Cookies.binarycookies',
    Path('Library')
    / 'Containers'
    / 'com.apple.Safari'
    / 'Data'
    / 'Library'
    / 'Cookies'
    / 'Cookies.binarycookies',
)
_MACOS_FIREFOX_PROFILE_DIR = Path('Library') / 'Application Support' / 'Firefox' / 'Profiles'
_MACOS_CHROMIUM_BROWSER_DIRS = {
    'Chrome': (
        Path('Library') / 'Application Support' / 'Google' / 'Chrome',
        Path('Library') / 'Application Support' / 'Google' / 'Chrome Beta',
        Path('Library') / 'Application Support' / 'Google' / 'Chrome Dev',
        Path('Library') / 'Application Support' / 'Google' / 'Chrome Canary',
        Path('Library') / 'Application Support' / 'Google' / 'Chrome for Testing',
    ),
    'Brave': (
        Path('Library') / 'Application Support' / 'BraveSoftware' / 'Brave-Browser',
        Path('Library') / 'Application Support' / 'BraveSoftware' / 'Brave-Browser-Beta',
        Path('Library') / 'Application Support' / 'BraveSoftware' / 'Brave-Browser-Dev',
        Path('Library') / 'Application Support' / 'BraveSoftware' / 'Brave-Browser-Nightly',
    ),
    'Edge': (
        Path('Library') / 'Application Support' / 'Microsoft Edge',
        Path('Library') / 'Application Support' / 'Microsoft Edge Beta',
        Path('Library') / 'Application Support' / 'Microsoft Edge Dev',
        Path('Library') / 'Application Support' / 'Microsoft Edge Canary',
    ),
    'Chromium': (Path('Library') / 'Application Support' / 'Chromium',),
    'Opera': (
        Path('Library') / 'Application Support' / 'com.operasoftware.Opera',
        Path('Library') / 'Application Support' / 'com.operasoftware.OperaNext',
        Path('Library') / 'Application Support' / 'com.operasoftware.OperaDeveloper',
    ),
    'Vivaldi': (Path('Library') / 'Application Support' / 'Vivaldi',),
}
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


def _parse_plaintext_roblosecurity(payload: bytes) -> str | None:
    """Extract a token from a backend-owned plaintext cookie payload."""
    for encoding in ('latin-1', 'utf-8'):
        try:
            cookie = _extract_roblosecurity(payload.decode(encoding, errors='ignore'))
        except Exception:  # ruff: ignore[blind-except, try-except-continue]
            continue
        if cookie:
            return cookie
    return None


class LinuxAuthWriteError(RuntimeError):
    """A safe, user-displayable failure while switching a Linux local account."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_LINUX_AUTH_WRITE_LOCK = threading.Lock()
_SOBER_CONFIG_RELATIVE_PATH = Path('config') / 'sober' / 'config.json'
_SOBER_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _strip_json_comments(text: str) -> str:  # ruff: ignore[complex-structure]
    """Remove // and /* */ comments without touching quoted JSON strings."""
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            output.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            output.append(char)
            index += 1
            continue
        if char == '/' and index + 1 < length:
            next_char = text[index + 1]
            if next_char == '/':
                index += 2
                while index < length and text[index] not in '\r\n':
                    index += 1
                continue
            if next_char == '*':
                index += 2
                while index + 1 < length and text[index : index + 2] != '*/':
                    if text[index] in '\r\n':
                        output.append(text[index])
                    index += 1
                index = min(length, index + 2)
                continue
        output.append(char)
        index += 1
    return ''.join(output)


def _sober_use_libsecret(config_path: Path) -> bool:
    """Return Sober's secure-cookie setting, failing closed on ambiguous config."""
    if not _path_exists(config_path):
        return False
    try:
        payload: object = json.loads(_strip_json_comments(config_path.read_text(encoding='utf-8')))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        msg = 'sober_config_unreadable'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion couldn't safely read Sober's configuration, so it did not switch "
            'the local account.',
        ) from exc
    payload_map = _json_object(payload)
    if payload_map is None:
        msg = 'sober_config_invalid'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion couldn't safely understand Sober's configuration, so it did not "
            'switch the local account.',
        )
    value = payload_map.get('use_libsecret', False)
    if not isinstance(value, bool):
        msg = 'sober_config_invalid'
        raise LinuxAuthWriteError(
            msg,
            "Sober's use_libsecret setting has an unexpected value, so Fleasion did not "
            'switch the local account.',
        )
    return value


def _linux_auth_os_error(exc: OSError) -> LinuxAuthWriteError:
    if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
        return LinuxAuthWriteError(
            'cookie_store_permission_denied',
            "Sober's local cookie store is read-only or Fleasion does not have permission "
            'to replace it.',
        )
    if exc.errno in {errno.ENOSPC, getattr(errno, 'EDQUOT', -1)}:
        return LinuxAuthWriteError(
            'cookie_store_full',
            "Sober's local cookie store could not be updated because the filesystem has "
            'no free space available.',
        )
    return LinuxAuthWriteError(
        'cookie_store_write_failed',
        f"Sober's local cookie store could not be updated ({type(exc).__name__}).",
    )


def _validate_linux_cookie_file(path: Path) -> os.stat_result:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        msg = 'cookie_store_not_initialized'
        raise LinuxAuthWriteError(
            msg,
            'Sober is installed, but its local cookie store does not exist yet. '
            'Launch Sober and sign in once first.',
        ) from exc
    except OSError as exc:
        raise _linux_auth_os_error(exc) from exc

    if stat.S_ISLNK(metadata.st_mode):
        msg = 'cookie_store_unsafe_path'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion refused to switch accounts because Sober's cookie store is a symbolic link.",
        )
    if not stat.S_ISREG(metadata.st_mode):
        msg = 'cookie_store_unsafe_path'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion refused to switch accounts because Sober's cookie store is not a regular file.",  # ruff: ignore[line-too-long]
        )
    if metadata.st_nlink != 1:
        msg = 'cookie_store_unsafe_path'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion refused to switch accounts because Sober's cookie store has multiple hard links.",  # ruff: ignore[line-too-long]
        )
    getuid = getattr(os, 'getuid', None)
    if getuid is not None and metadata.st_uid != getuid():
        msg = 'cookie_store_wrong_owner'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion refused to switch accounts because Sober's cookie store is owned by another user.",  # ruff: ignore[line-too-long]
        )
    if metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        msg = 'cookie_store_insecure_permissions'
        raise LinuxAuthWriteError(
            msg,
            "Fleasion refused to place a Roblox session token in Sober's cookie store because "
            'the file is accessible by other users.',
        )
    return metadata


def _rewrite_sober_cookie_header(cookie_text: str, cookie: str) -> str:  # ruff: ignore[complex-structure]
    """Validate and rewrite Sober's known semicolon-separated cookie-header format."""
    if not cookie or re.search(r'[\s;\x00]', cookie):
        msg = 'invalid_cookie_value'
        raise LinuxAuthWriteError(
            msg,
            'The selected account has an invalid Roblox session token and was not switched.',
        )
    if '\x00' in cookie_text:
        msg = 'cookie_store_unknown_format'
        raise LinuxAuthWriteError(
            msg,
            "Sober's cookie store has an unknown format. Fleasion did not modify it.",
        )

    trailing_newline = cookie_text[len(cookie_text.rstrip('\r\n')) :]
    body = cookie_text.rstrip('\r\n')
    if not body or '\n' in body or '\r' in body:
        msg = 'cookie_store_unknown_format'
        raise LinuxAuthWriteError(
            msg,
            "Sober's cookie store has an unknown format. Fleasion did not modify it.",
        )

    parsed: list[tuple[str, str]] = []
    for raw_segment in body.split(';'):
        segment = raw_segment.strip()
        if not segment:
            continue
        if '=' not in segment:
            msg = 'cookie_store_unknown_format'
            raise LinuxAuthWriteError(
                msg,
                "Sober's cookie store has an unknown format. Fleasion did not modify it.",
            )
        name, value = segment.split('=', 1)
        name = name.strip()
        value = value.strip()
        if not _SOBER_COOKIE_NAME_RE.fullmatch(name):
            msg = 'cookie_store_unknown_format'
            raise LinuxAuthWriteError(
                msg,
                "Sober's cookie store has an unknown format. Fleasion did not modify it.",
            )
        parsed.append((name, value))

    auth_indices = [
        index for index, (name, _value) in enumerate(parsed) if name == '.ROBLOSECURITY'
    ]
    if not auth_indices:
        msg = 'cookie_store_missing_auth_cookie'
        raise LinuxAuthWriteError(
            msg,
            "Sober's plaintext cookie store does not contain .ROBLOSECURITY. Fleasion did not "
            'add a session token to an unexpected file; sign in through Sober once first.',
        )

    first_auth_index = auth_indices[0]
    rewritten: list[str] = []
    auth_written = False
    for index, (name, value) in enumerate(parsed):
        if name == '.ROBLOSECURITY':
            if index == first_auth_index and not auth_written:
                rewritten.append(f'.ROBLOSECURITY={cookie}')
                auth_written = True
            continue
        rewritten.append(f'{name}={value}')
    return '; '.join(rewritten) + trailing_newline


def _atomic_replace_linux_cookie_file(
    path: Path,
    payload: bytes,
    original: os.stat_result,
) -> None:
    temp_path: Path | None = None
    fd = -1
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        fd, temp_name = tempfile.mkstemp(prefix=f'.{path.name}.fleasion-', dir=str(path.parent))
        temp_path = Path(temp_name)
        with os.fdopen(fd, 'wb', closefd=True) as handle:
            fd = -1
            handle.write(payload)
            handle.flush()
            os.fchmod(handle.fileno(), stat.S_IMODE(original.st_mode))
            os.fsync(handle.fileno())

        current = path.lstat()
        original_identity = (
            original.st_dev,
            original.st_ino,
            original.st_uid,
            original.st_mode,
            original.st_nlink,
            original.st_size,
            original.st_mtime_ns,
        )
        current_identity = (
            current.st_dev,
            current.st_ino,
            current.st_uid,
            current.st_mode,
            current.st_nlink,
            current.st_size,
            current.st_mtime_ns,
        )
        if current_identity != original_identity or not stat.S_ISREG(current.st_mode):
            msg = 'cookie_store_changed_during_write'
            raise LinuxAuthWriteError(  # ruff: ignore[raise-within-try]
                msg,
                "Sober's cookie store changed while Fleasion was preparing the account switch. "
                'Nothing was replaced; try again.',
            )

        os.replace(temp_path, path)  # ruff: ignore[os-replace]
        temp_path = None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            directory_flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
            directory_fd = os.open(path.parent, directory_flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            _log_auth_failure(
                f'linux-cookie-directory-fsync:{path}:{type(exc).__name__}',
                f'Updated Sober cookie store but could not fsync its directory: {type(exc).__name__}',  # ruff: ignore[line-too-long]
            )
    except LinuxAuthWriteError:
        raise
    except OSError as exc:
        raise _linux_auth_os_error(exc) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            with contextlib.suppress(OSError):
                temp_path.unlink()


@dataclass(frozen=True, slots=True)
class LinuxLocalAuthProvider:
    """Reader/writer for one selected Linux client's local auth store."""

    client_key: str
    source_name: str
    cookie_relative_path: Path
    parse_payload: Callable[[bytes], str | None]
    config_relative_path: Path | None = None

    def cookie_path(self, installation: LinuxClientInstallation) -> Path:
        return installation.paths.data_root / self.cookie_relative_path

    def config_path(self, cookie_path: Path) -> Path | None:
        if self.config_relative_path is None:
            return None
        try:
            flatpak_root = cookie_path.parent.parent.parent
        except IndexError:
            return None
        return flatpak_root / self.config_relative_path

    def _plaintext_storage_allowed(self, path: Path) -> bool:
        config_path = self.config_path(path)
        if config_path is None:
            return True
        return not _sober_use_libsecret(config_path)

    def read_roblosecurity(self, path: Path) -> str | None:  # ruff: ignore[too-many-return-statements]
        try:
            if not self._plaintext_storage_allowed(path):
                _log_auth_failure(
                    f'linux-cookie-libsecret-enabled:{self.client_key}',
                    f'{self.source_name} use_libsecret is enabled; plaintext local auth is ignored',
                )
                return None
        except LinuxAuthWriteError as exc:
            _log_auth_failure(
                f'linux-cookie-config-read:{self.client_key}:{exc.code}',
                f'Could not safely determine {self.source_name} local auth storage mode: {exc}',
            )
            return None

        try:  # ruff: ignore[too-many-statements-in-try-clause]
            metadata = _validate_linux_cookie_file(path)
            flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
            fd = os.open(path, flags)
            with os.fdopen(fd, 'rb', closefd=True) as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    msg = 'cookie_store_changed_during_read'
                    raise LinuxAuthWriteError(  # ruff: ignore[raise-within-try]
                        msg,
                        f'{self.source_name} local cookie store changed while it was being opened.',
                    )
                payload = handle.read()
        except LinuxAuthWriteError as exc:
            _log_auth_failure(
                f'linux-cookie-read-safe:{self.client_key}:{path}:{exc.code}',
                f'Could not safely read {self.source_name} local cookie file: {exc}',
            )
            return None
        except OSError as exc:
            _log_auth_failure(
                f'linux-cookie-read:{self.client_key}:{path}:{type(exc).__name__}',
                f'Failed to read {self.source_name} local cookie file at {path}: '
                f'{type(exc).__name__}',
            )
            return None
        try:
            cookie = self.parse_payload(payload)
        except Exception as exc:  # ruff: ignore[blind-except]
            _log_auth_failure(
                f'linux-cookie-parse:{self.client_key}:{path}:{type(exc).__name__}',
                f'Failed to parse {self.source_name} local cookie file at {path}: '
                f'{type(exc).__name__}',
            )
            return None
        if cookie:
            return cookie
        _log_auth_failure(
            f'linux-cookie-not-found:{self.client_key}:{path}',
            f'{self.source_name} local cookie file at {path} does not contain .ROBLOSECURITY',
        )
        return None

    def write_roblosecurity(self, path: Path, cookie: str) -> bool:
        """Safely replace this client's plaintext .ROBLOSECURITY value."""
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            with _LINUX_AUTH_WRITE_LOCK:
                if not self._plaintext_storage_allowed(path):
                    msg = 'libsecret_enabled'
                    raise LinuxAuthWriteError(  # ruff: ignore[raise-within-try]
                        msg,
                        "Sober account switching is unavailable because Sober's use_libsecret "
                        'setting is enabled. Fleasion will not write your Roblox session token '
                        'to the plaintext cookie file.',
                    )

                metadata = _validate_linux_cookie_file(path)
                flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                try:
                    fd = os.open(path, flags)
                except OSError as exc:
                    raise _linux_auth_os_error(exc) from exc
                try:  # ruff: ignore[too-many-statements-in-try-clause]
                    with os.fdopen(fd, 'rb', closefd=True) as handle:
                        opened = os.fstat(handle.fileno())
                        opened_identity = (
                            opened.st_dev,
                            opened.st_ino,
                            opened.st_uid,
                            opened.st_mode,
                            opened.st_nlink,
                        )
                        metadata_identity = (
                            metadata.st_dev,
                            metadata.st_ino,
                            metadata.st_uid,
                            metadata.st_mode,
                            metadata.st_nlink,
                        )
                        if opened_identity != metadata_identity:
                            msg = 'cookie_store_changed_during_read'
                            raise LinuxAuthWriteError(  # ruff: ignore[raise-within-try]
                                msg,
                                "Sober's cookie store changed while Fleasion was opening it. "
                                'Nothing was modified; try again.',
                            )
                        payload = handle.read()
                except LinuxAuthWriteError:
                    raise
                except OSError as exc:
                    raise _linux_auth_os_error(exc) from exc

                try:
                    cookie_text = payload.decode('utf-8')
                except UnicodeDecodeError as exc:
                    msg = 'cookie_store_unknown_format'
                    raise LinuxAuthWriteError(
                        msg,
                        "Sober's cookie store has an unknown format. Fleasion did not modify it.",
                    ) from exc

                updated_text = _rewrite_sober_cookie_header(cookie_text, cookie)
                _atomic_replace_linux_cookie_file(path, updated_text.encode('utf-8'), opened)
            return True  # ruff: ignore[try-consider-else]
        except LinuxAuthWriteError as exc:
            _log_auth_failure(
                f'linux-cookie-write:{self.client_key}:{path}:{exc.code}',
                f'Could not update {self.source_name} local auth cookie: {exc}',
            )
            raise


SOBER_LOCAL_AUTH_PROVIDER = LinuxLocalAuthProvider(
    client_key=SOBER_CLIENT.key,
    source_name=SOBER_CLIENT.display_name,
    cookie_relative_path=_SOBER_LOCAL_COOKIE_RELATIVE_PATH,
    parse_payload=_parse_plaintext_roblosecurity,
    config_relative_path=_SOBER_CONFIG_RELATIVE_PATH,
)
LINUX_LOCAL_AUTH_PROVIDERS_BY_KEY: Mapping[str, LinuxLocalAuthProvider] = MappingProxyType(
    {SOBER_LOCAL_AUTH_PROVIDER.client_key: SOBER_LOCAL_AUTH_PROVIDER}
)


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
    return cookie_text.rstrip() + f'\n.ROBLOSECURITY\t{cookie}', 0


def _safe_resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def _path_is_read_only(path: Path) -> bool:
    try:
        return path.exists() and not bool(path.stat().st_mode & stat.S_IWRITE)
    except OSError:
        return False


def _clear_read_only(path: Path) -> None:
    try:
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IWRITE)
    except OSError:
        pass


def _restore_read_only(path: Path) -> None:
    try:
        if path.exists():
            path.chmod(path.stat().st_mode & ~stat.S_IWRITE)
    except OSError:
        pass


def _normalise_key(path: Path) -> str:
    return os.path.normcase(str(_safe_resolve(path)))


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _selected_linux_client_installation() -> LinuxClientInstallation | None:
    """Resolve only the configured Linux client; discovery reads no auth data."""
    from .platform_linux import (  # ruff: ignore[import-outside-top-level]
        get_selected_linux_client_installation,
    )

    return get_selected_linux_client_installation()


def _selected_linux_local_auth_candidate() -> tuple[LinuxLocalAuthProvider, Path] | None:
    """Return the selected client's opt-in local auth provider and path."""
    try:
        installation = _selected_linux_client_installation()
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'linux-auth-provider-selection:{type(exc).__name__}',
            'Could not resolve the selected Linux Roblox client auth provider: '
            f'{type(exc).__name__}',
        )
        return None
    if installation is None:
        return None
    provider = LINUX_LOCAL_AUTH_PROVIDERS_BY_KEY.get(installation.key)
    if provider is None:
        return None
    return provider, provider.cookie_path(installation)


def _add_candidate(
    candidates: list[tuple[str, Path]], seen: set[str], source: str, path: Path
) -> None:
    key = _normalise_key(path)
    if key in seen:
        return
    seen.add(key)
    candidates.append((source, path))


def _iter_user_profile_cookie_candidates() -> list[tuple[str, Path]]:  # ruff: ignore[complex-structure]
    candidates: list[tuple[str, Path]] = []
    seen: set[str] = set()

    # Linux local stores have backend-specific formats and privacy semantics.
    # They are resolved through the selected backend provider in
    # ``get_roblosecurity`` and must not enter the generic path scanner.
    if sys.platform.startswith('linux'):
        return candidates

    _add_candidate(candidates, seen, 'LOCALAPPDATA', ROBLOX_COOKIES_PATH)

    if sys.platform == 'darwin':
        for relative in _MACOS_COOKIE_CANDIDATES:
            _add_candidate(candidates, seen, 'macOS-home', USER_HOME / relative)
        return candidates

    userprofile = os.environ.get('USERPROFILE')
    if userprofile:
        _add_candidate(
            candidates,
            seen,
            'USERPROFILE',
            Path(userprofile) / _ROBLOX_COOKIE_RELATIVE_PATH,
        )

    home = Path.home()
    if home:
        _add_candidate(candidates, seen, 'Path.home', home / _ROBLOX_COOKIE_RELATIVE_PATH)

    system_drive = (os.environ.get('SystemDrive') or 'C:').strip().rstrip('\\/')  # ruff: ignore[uncapitalized-environment-variables]
    if re.fullmatch(r'[A-Za-z]:', system_drive):
        users_root = Path(f'{system_drive}/') / 'Users'
    else:
        users_root = Path(system_drive) / 'Users'
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with os.scandir(users_root) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                _add_candidate(
                    candidates,
                    seen,
                    'all-users',
                    Path(entry.path) / _ROBLOX_COOKIE_RELATIVE_PATH,
                )
    except OSError as exc:
        _log_auth_failure(
            f'user-scan:{users_root}:{type(exc).__name__}',
            f'Could not scan Windows user profiles for RobloxCookies.dat: {type(exc).__name__}: {exc}',  # ruff: ignore[line-too-long]
        )

    return candidates


def _read_cookie_payload(path: Path) -> tuple[JsonObject, bytes] | None:  # ruff: ignore[too-many-return-statements]
    if not _path_exists(path):
        _log_auth_failure(
            f'missing:{path}',
            f'RobloxCookies.dat not found at {path}',
        )
        return None

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        with path.open('r', encoding='utf-8') as f:
            data_value: object = json.load(f)
            data = _json_object(data_value)
            if data is None:
                msg = 'RobloxCookies.dat root must be an object'
                raise ValueError(msg)  # ruff: ignore[raise-within-try]
    except Exception as exc:  # ruff: ignore[blind-except]
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
        enc = base64.b64decode(_base64_source(cookies_data))
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'base64:{path}:{type(exc).__name__}',
            f'Failed to decode RobloxCookies.dat CookiesData at {path}: {type(exc).__name__}: {exc}',  # ruff: ignore[line-too-long]
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
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'dpapi:{path}:{type(exc).__name__}:{exc}',
            f'Failed to decrypt RobloxCookies.dat at {path}: {type(exc).__name__}: {exc}',
        )
        return None

    return data, dec


def _get_roblosecurity_from_path(cookie_path: Path) -> str | None:
    try:  # ruff: ignore[too-many-statements-in-try-clause]
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
            except Exception:  # ruff: ignore[blind-except, try-except-continue]
                continue
        _log_auth_failure(
            f'not-found:{cookie_path}',
            f'Decrypted RobloxCookies.dat at {cookie_path}, but .ROBLOSECURITY was not found',
        )
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'unexpected-read:{cookie_path}:{type(exc).__name__}:{exc}',
            f'Unexpected error while reading Roblox auth cookie at {cookie_path}: {type(exc).__name__}: {exc}',  # ruff: ignore[line-too-long]
        )
    return None


def get_auth_failure_details() -> dict[str, object]:
    """Return diagnostics for the most recent default cookie lookup failure."""
    return dict(_LAST_AUTH_FAILURE_DETAILS)


def _mark_auth_cookie_available(cookie: str) -> None:
    global _AUTH_READY_COOKIE  # ruff: ignore[global-variable-not-assigned]
    if not cookie:
        return
    with _AUTH_READY_CONDITION:
        _set_module_state('_AUTH_READY_COOKIE', cookie)
        _AUTH_READY_CONDITION.notify_all()


def notify_auth_source_changed() -> None:
    """Wake auth waiters after the user changes browser/manual-token settings."""
    with _AUTH_READY_CONDITION:
        _AUTH_READY_CONDITION.notify_all()


def wait_for_roblosecurity(
    *, include_keychain_browsers: bool = True, retry_interval: float = 2.0
) -> str | None:
    """Wait until a usable Roblox token is available."""
    if sys.platform != 'darwin':
        return get_roblosecurity(include_keychain_browsers=include_keychain_browsers)

    while True:
        cookie = get_roblosecurity(include_keychain_browsers=include_keychain_browsers)
        if cookie:
            return cookie
        with _AUTH_READY_CONDITION:
            _AUTH_READY_CONDITION.wait(timeout=max(0.25, retry_interval))


def _get_macos_browser_auth_cipher(create: bool = True):  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument, missing-return-type-private-function]
    if sys.platform != 'darwin':
        return None
    try:
        from cryptography.fernet import Fernet  # ruff: ignore[import-outside-top-level]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'browser-auth-cache-crypto:{type(exc).__name__}',
            f'macOS browser auth cache encryption is unavailable: {type(exc).__name__}: {exc}',
        )
        return None

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        key_path = _BROWSER_AUTH_CACHE_KEY_FILE
        if not key_path.exists():
            if not create:
                return None
            key_path.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            flags = (
                getattr(os, 'O_WRONLY', 1) | getattr(os, 'O_CREAT', 64) | getattr(os, 'O_EXCL', 128)
            )
            fd = os.open(key_path, flags, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
        else:
            key = key_path.read_bytes().strip()
        with contextlib.suppress(OSError):
            os.chmod(key_path, 0o600)  # ruff: ignore[os-chmod]
        return Fernet(key)
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'browser-auth-cache-key:{type(exc).__name__}:{exc}',
            f'macOS browser auth cache key failed: {type(exc).__name__}: {exc}',
        )
        return None


def _validate_roblosecurity(cookie: str) -> bool | None:
    """Return True/False for validation, or None when validation is inconclusive."""
    global _LAST_BROWSER_AUTH_VALIDATION_DETAIL  # ruff: ignore[global-variable-not-assigned]

    if not cookie:
        _set_module_state('_LAST_BROWSER_AUTH_VALIDATION_DETAIL', 'empty-cookie')
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        import requests  # ruff: ignore[import-outside-top-level]

        sess = requests.Session()
        sess.trust_env = False
        sess.proxies = {}
        try:
            sess.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
        except Exception:  # ruff: ignore[blind-except]
            sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
        resp = sess.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
        _set_module_state('_LAST_BROWSER_AUTH_VALIDATION_DETAIL', f'HTTP {resp.status_code}')
        if resp.status_code == 200:  # ruff: ignore[magic-value-comparison]
            return True
        if resp.status_code in (401, 403):  # ruff: ignore[literal-membership]
            return False
        return None  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
        _set_module_state('_LAST_BROWSER_AUTH_VALIDATION_DETAIL', f'{type(exc).__name__}: {exc}')
        _log_auth_failure(
            f'browser-auth-cache-validate:{type(exc).__name__}',
            f'Could not validate cached Roblox browser login: {type(exc).__name__}: {exc}',
        )
        return None


def validate_roblosecurity_for_import(cookie: str) -> tuple[bool, str]:
    """Validate a user-selected or manually imported Roblox token before saving it."""
    cleaned = (cookie or '').strip()
    if not cleaned:
        return False, 'empty token'
    validation = _validate_roblosecurity(cleaned)
    detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'unknown validation result'
    if validation is True:
        return True, detail
    if validation is False:
        return False, detail
    return False, f'could not confirm token validity ({detail})'


def get_last_browser_auth_error_details() -> dict[str, object]:
    return dict(_LAST_BROWSER_AUTH_ERROR_DETAILS)


def _set_browser_auth_error_details(
    source: str, exc: Exception, *, cookie_file: Path | str | None = None
) -> None:
    global _LAST_BROWSER_AUTH_ERROR_DETAILS  # ruff: ignore[global-variable-not-assigned]

    permission_error = isinstance(exc, PermissionError)
    blocked_file = str(cookie_file or getattr(exc, 'filename', '') or '')
    browser_error_details: dict[str, object] = {
        'source': source,
        'error_type': type(exc).__name__,
        'error': str(exc),
        'cookie_file': blocked_file,
        'full_disk_access_required': bool(source == 'Safari' and permission_error),
    }
    _set_module_state('_LAST_BROWSER_AUTH_ERROR_DETAILS', browser_error_details)


def _get_configured_macos_auth_source() -> str:
    if sys.platform != 'darwin':
        return ''
    try:
        with CONFIG_FILE.open('r', encoding='utf-8') as f:
            settings = json.load(f)
    except Exception:  # ruff: ignore[blind-except]
        return ''
    source = str(settings.get('macos_auth_source') or '')
    valid = {'', 'manual', *_MACOS_AUTH_BROWSER_NAMES}
    return source if source in valid else ''


def store_manual_roblosecurity(cookie: str) -> bool:
    """Store a manually imported Roblox token encrypted for local reuse."""
    if not cookie or not cookie.strip():
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        _MANUAL_AUTH_TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'token': encrypt_token(cookie.strip(), _MANUAL_AUTH_TOKEN_KEY_FILE),
        }
        with _MANUAL_AUTH_TOKEN_FILE.open('w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        with contextlib.suppress(OSError):
            os.chmod(_MANUAL_AUTH_TOKEN_FILE, 0o600)  # ruff: ignore[os-chmod]
        _mark_auth_cookie_available(cookie.strip())
        return True  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
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
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'manual-auth-token-read:{type(exc).__name__}:{exc}',
            f'Could not read manually imported Roblox token: {type(exc).__name__}: {exc}',
        )
        return None


def _delete_cached_browser_roblosecurity() -> None:
    with contextlib.suppress(OSError):
        _BROWSER_AUTH_CACHE_FILE.unlink(missing_ok=True)


def _log_browser_auth_cache_state(
    state: str, message: str, *, block_automatic_import: bool = False
) -> None:
    global _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT  # ruff: ignore[global-variable-not-assigned]

    _set_module_state('_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT', block_automatic_import)
    _log_auth_failure(f'browser-auth-cache-state:{state}', f'Browser auth cache state: {message}')


def _read_cached_browser_roblosecurity(*, delete_invalid: bool = True) -> tuple[str | None, str]:  # ruff: ignore[complex-structure, too-many-branches, too-many-return-statements]
    global _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT  # ruff: ignore[global-variable-not-assigned]

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
            f'Browser auth cache state: malformed JSON; preserving cache ({type(exc).__name__}: {exc})',  # ruff: ignore[line-too-long]
        )
        _log_browser_auth_cache_state(
            'malformed-json',
            'encrypted browser login cache is malformed; preserving cache and skipping automatic browser prompt',  # ruff: ignore[line-too-long]
            block_automatic_import=True,
        )
        return None, ''
    except OSError as exc:
        _log_auth_failure(
            f'browser-auth-cache-read-io:{type(exc).__name__}:{exc}',
            f'Browser auth cache state: read failed; preserving cache ({type(exc).__name__}: {exc})',  # ruff: ignore[line-too-long]
        )
        _log_browser_auth_cache_state(
            'read-failed',
            'encrypted browser login cache could not be read; preserving cache and skipping automatic browser prompt',  # ruff: ignore[line-too-long]
            block_automatic_import=True,
        )
        return None, ''

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        source = str(payload.get('source') or '')
        if source not in _PERSISTENT_BROWSER_AUTH_SOURCES:
            _log_browser_auth_cache_state(
                'validation-inconclusive',
                f'cache source {source or "(missing)"} is not eligible for automatic reuse; preserving cache',  # ruff: ignore[line-too-long]
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
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'browser-auth-cache-decrypt:{type(exc).__name__}:{exc}',
            f'Browser auth cache state: decrypt failed; preserving cache ({type(exc).__name__}: {exc})',  # ruff: ignore[line-too-long]
        )
        _log_browser_auth_cache_state(
            'decrypt-failed',
            'encrypted browser login cache decrypt failed; preserving cache and skipping automatic browser prompt',  # ruff: ignore[line-too-long]
            block_automatic_import=True,
        )
        return None, ''

    validation = _validate_roblosecurity(cookie)
    if validation is False:
        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'invalid'
        _set_module_state('_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT', False)  # ruff: ignore[boolean-positional-value-in-call]
        if delete_invalid:
            _delete_cached_browser_roblosecurity()
            log_buffer.log(
                'Auth',
                f'Browser auth cache state: validation invalid ({detail}); deleted cached Roblox browser login',  # ruff: ignore[line-too-long]
            )
        else:
            _log_browser_auth_cache_state(
                'validation-invalid',
                f'validation invalid ({detail}); preserving cache for startup or explicit import',
            )
        return None, ''
    if validation is not True:
        _set_module_state('_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT', False)  # ruff: ignore[boolean-positional-value-in-call]
        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'inconclusive'
        log_buffer.log(
            'Auth',
            f'Browser auth cache state: validation inconclusive ({detail}); cached Roblox browser login was not reused',  # ruff: ignore[line-too-long]
        )
        return None, ''

    detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'valid'
    _set_module_state('_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT', False)  # ruff: ignore[boolean-positional-value-in-call]
    log_buffer.log('Auth', f'Browser auth cache state: cache reused from {source} ({detail})')

    _LAST_AUTH_FAILURE_DETAILS.clear()
    return cookie, source


def _write_cached_browser_roblosecurity(cookie: str, source: str) -> None:
    if sys.platform != 'darwin' or source not in _PERSISTENT_BROWSER_AUTH_SOURCES:
        return
    cipher = _get_macos_browser_auth_cipher()
    if cipher is None:
        return
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        _BROWSER_AUTH_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'source': source,
            'cached_at': int(time.time()),
            'cookie': cipher.encrypt(cookie.encode('utf-8')).decode('ascii'),
        }
        with _BROWSER_AUTH_CACHE_FILE.open('w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
        with contextlib.suppress(OSError):
            os.chmod(_BROWSER_AUTH_CACHE_FILE, 0o600)  # ruff: ignore[os-chmod]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'browser-auth-cache-write:{type(exc).__name__}:{exc}',
            f'Could not cache Roblox browser login: {type(exc).__name__}: {exc}',
        )


def _macos_browser_cookie_files(source: str) -> list[Path]:  # ruff: ignore[complex-structure]
    """Return explicit macOS cookie DB candidates for browser_cookie3 gaps."""
    if sys.platform != 'darwin':
        return []

    if source == 'Safari':
        return [
            USER_HOME / relative
            for relative in _MACOS_SAFARI_COOKIE_FILES
            if _path_exists(USER_HOME / relative)
        ]

    if source == 'Firefox':
        profiles_dir = USER_HOME / _MACOS_FIREFOX_PROFILE_DIR
        try:
            profile_cookie_files = sorted(profiles_dir.glob('*/cookies.sqlite'))
        except OSError:
            return []
        return [path for path in profile_cookie_files if _path_exists(path)]

    bases = _MACOS_CHROMIUM_BROWSER_DIRS.get(source)
    if not bases:
        return []

    candidates: list[Path] = []
    seen: set[str] = set()

    def _add(path: Path) -> None:
        if not _path_exists(path):
            return
        key = _normalise_key(path)
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    for relative_base in bases:
        base = USER_HOME / relative_base
        # Modern Chromium stores cookies under Network/Cookies. Older installs
        # and some channels still use the profile root Cookies DB.
        for profile in ('Default',):
            _add(base / profile / 'Network' / 'Cookies')
            _add(base / profile / 'Cookies')
        try:
            profile_dirs = sorted(base.glob('Profile *'))
        except OSError:
            profile_dirs = []
        for profile_dir in profile_dirs:
            if not profile_dir.is_dir():
                continue
            _add(profile_dir / 'Network' / 'Cookies')
            _add(profile_dir / 'Cookies')
        _add(base / 'Network' / 'Cookies')
        _add(base / 'Cookies')

    return candidates


def _make_browser_cookie_loader(source: str, loader: _BrowserLoader) -> _BrowserLoader:
    def _load(
        *,
        cookie_file: str | None = None,
        domain_name: str = '',
        key_file: str | None = None,
    ) -> Iterable[Cookie]:
        if sys.platform != 'darwin':
            return loader(cookie_file=cookie_file, domain_name=domain_name, key_file=key_file)

        cookie_files = _macos_browser_cookie_files(source)
        if not cookie_files:
            return loader(cookie_file=cookie_file, domain_name=domain_name, key_file=key_file)

        combined: list[Cookie] = []
        loaded_any = False
        first_error: Exception | None = None
        errors: list[str] = []
        for candidate_file in cookie_files:
            try:
                jar = loader(
                    cookie_file=str(candidate_file),
                    domain_name=domain_name,
                    key_file=key_file,
                )
            except Exception as exc:  # ruff: ignore[blind-except]
                _set_browser_auth_error_details(source, exc, cookie_file=candidate_file)
                if first_error is None:
                    first_error = exc
                errors.append(f'{candidate_file}: {type(exc).__name__}: {exc}')
                continue
            loaded_any = True
            combined.extend(jar)

        if loaded_any:
            return combined

        if first_error is not None:
            if len(errors) > 1:
                log_buffer.log(
                    'Auth',
                    f'{source} browser cookie candidates failed: {"; ".join(errors[:3])}',
                )
            raise first_error
        return loader(cookie_file=cookie_file, domain_name=domain_name, key_file=key_file)

    return _load


def _browser_cookie_loaders(include_keychain: bool) -> list[tuple[str, _BrowserLoader]]:  # ruff: ignore[boolean-type-hint-positional-argument]
    browser_cookie3 = _browser_cookie_module()
    loaders: list[tuple[str, _BrowserLoader]] = [('Firefox', browser_cookie3.firefox)]
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
    return [(source, _make_browser_cookie_loader(source, loader)) for source, loader in loaders]


def _candidate_roblosecurity_values(jar: Iterable[Cookie], now: float) -> list[str]:
    candidates = [
        cookie
        for cookie in jar
        if cookie.name == '.ROBLOSECURITY'
        and cookie.value
        and 'roblox.com' in (cookie.domain or '').lower()
        and (not cookie.expires or cookie.expires > now)
    ]

    values: list[str] = []
    seen: set[str] = set()
    for cookie in sorted(candidates, key=lambda item: item.expires or 0, reverse=True):
        raw_value = cookie.value
        if raw_value is None:
            continue
        value = raw_value.strip()
        if not value or any(char.isspace() for char in value) or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def discover_browser_roblosecurity(  # ruff: ignore[complex-structure, too-many-branches, too-many-return-statements]
    include_keychain: bool = False,  # ruff: ignore[boolean-default-value-positional-argument, boolean-type-hint-positional-argument]
    *,
    explicit_import: bool = False,
    browser: str | None = None,
) -> tuple[str | None, str]:
    """Discover the Roblox cookie from local browsers without logging its value.

    Firefox discovery is prompt-free on macOS. Chrome-family browsers and
    Safari are only queried when ``include_keychain`` is True because macOS may
    ask the user to approve Safe Storage or browser-data access.
    """
    global _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE, _BROWSER_AUTO_DISCOVERY_ATTEMPTED  # ruff: ignore[global-variable-not-assigned]

    if browser is not None and browser not in _MACOS_AUTH_BROWSER_NAMES:
        return None, ''

    with _BROWSER_DISCOVERY_LOCK:
        if (
            not explicit_import
            and _BROWSER_COOKIE_CACHE
            and (not browser or browser == _BROWSER_COOKIE_SOURCE)
        ):
            return _BROWSER_COOKIE_CACHE, _BROWSER_COOKIE_SOURCE
        if not explicit_import:
            cached_cookie, cached_source = _read_cached_browser_roblosecurity(
                delete_invalid=include_keychain
            )
            if cached_cookie and (not browser or browser == cached_source):
                _set_module_state('_BROWSER_COOKIE_CACHE', cached_cookie)
                _set_module_state('_BROWSER_COOKIE_SOURCE', cached_source)
                return cached_cookie, cached_source
        if include_keychain and _BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT and not explicit_import:
            log_buffer.log(
                'Auth',
                'Skipping automatic browser login prompt because encrypted cache recovery was inconclusive; use Import Browser Login to re-import explicitly',  # ruff: ignore[line-too-long]
            )
            return None, ''
        if not explicit_import and not include_keychain and _BROWSER_AUTO_DISCOVERY_ATTEMPTED:
            return None, ''
        if not explicit_import and not include_keychain:
            _set_module_state('_BROWSER_AUTO_DISCOVERY_ATTEMPTED', True)  # ruff: ignore[boolean-positional-value-in-call]

        try:
            loaders = _browser_cookie_loaders(include_keychain)
        except Exception as exc:  # ruff: ignore[blind-except]
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
                candidates = _candidate_roblosecurity_values(jar, now)
            except Exception as exc:  # ruff: ignore[blind-except]
                _set_browser_auth_error_details(source, exc)
                _log_auth_failure(
                    f'browser-cookie:{source}:{type(exc).__name__}:{exc}',
                    f'Could not read Roblox browser login from {source}: {type(exc).__name__}: {exc}',  # ruff: ignore[line-too-long]
                )
                continue

            if not candidates:
                continue
            for cookie in candidates:
                if (
                    sys.platform == 'darwin'
                    or source in _PERSISTENT_BROWSER_AUTH_SOURCES
                    or explicit_import
                    or browser
                ):
                    validation = _validate_roblosecurity(cookie)
                    if validation is not True:
                        detail = _LAST_BROWSER_AUTH_VALIDATION_DETAIL or 'invalid'
                        log_buffer.log(
                            'Auth',
                            f'Browser login discovered from {source} was not valid ({detail}); skipping',  # ruff: ignore[line-too-long]
                        )
                        continue
                _set_module_state('_BROWSER_COOKIE_CACHE', cookie)
                _set_module_state('_BROWSER_COOKIE_SOURCE', source)
                _LAST_AUTH_FAILURE_DETAILS.clear()
                log_buffer.log(
                    'Auth',
                    f'Using domain-scoped Roblox browser login discovered from {source}',
                )
                _write_cached_browser_roblosecurity(cookie, source)
                return cookie, source

    return None, ''


def get_roblosecurity(  # ruff: ignore[complex-structure, too-many-branches, too-many-return-statements, too-many-statements]
    path: Path | None = None, *, include_keychain_browsers: bool = False
) -> str | None:
    """Return the .ROBLOSECURITY cookie value from a Roblox cookie store.

    On Windows, uses DPAPI (win32crypt) to decrypt the stored cookie data. On
    macOS, tries known Roblox cookie-file locations if Roblox creates them; a
    normal macOS install may only expose app-local account metadata, not the
    browser-style .ROBLOSECURITY cookie. Set ``include_keychain_browsers`` for
    an explicit user-facing macOS browser permission request.
    """
    global _SUCCESSFUL_COOKIE_PATH, _LAST_AUTH_FAILURE_DETAILS  # ruff: ignore[global-variable-not-assigned]

    if path is not None:
        if sys.platform.startswith('linux'):
            candidate = _selected_linux_local_auth_candidate()
            if candidate is not None and Path(path) == candidate[1]:
                return candidate[0].read_roblosecurity(candidate[1])
        return _get_roblosecurity_from_path(Path(path))

    attempted: list[str] = []
    existing: list[str] = []

    if sys.platform.startswith('linux'):
        candidate = _selected_linux_local_auth_candidate()
        if candidate is not None:
            provider, cookie_path = candidate
            attempted.append(str(cookie_path))
            if _path_exists(cookie_path):
                existing.append(str(cookie_path))
            cookie = provider.read_roblosecurity(cookie_path)
            if cookie:
                _set_module_state('_SUCCESSFUL_COOKIE_PATH', cookie_path)
                _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
                _mark_auth_cookie_available(cookie)
                return cookie

    if _SUCCESSFUL_COOKIE_PATH is not None and not sys.platform.startswith('linux'):
        attempted.append(str(_SUCCESSFUL_COOKIE_PATH))
        cookie = _get_roblosecurity_from_path(_SUCCESSFUL_COOKIE_PATH)
        if cookie:
            _mark_auth_cookie_available(cookie)
            return cookie
        _set_module_state('_SUCCESSFUL_COOKIE_PATH', None)

    for source, cookie_path in _iter_user_profile_cookie_candidates():
        attempted.append(str(cookie_path))
        if source == 'all-users' and not _path_exists(cookie_path):
            continue
        if _path_exists(cookie_path):
            existing.append(str(cookie_path))

        cookie = _get_roblosecurity_from_path(cookie_path)
        if cookie:
            _set_module_state('_SUCCESSFUL_COOKIE_PATH', cookie_path)
            if cookie_path != ROBLOX_COOKIES_PATH:
                _log_auth_failure(
                    f'fallback-success:{cookie_path}',
                    f'Using Roblox auth cookie discovered from {source}: {cookie_path}',
                )
            _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
            _mark_auth_cookie_available(cookie)
            return cookie

    if sys.platform == 'darwin':
        auth_source = _get_configured_macos_auth_source()
        if auth_source == 'manual':
            manual_cookie = get_manual_roblosecurity()
            browser_source = 'manual'
            if manual_cookie:
                valid, detail = validate_roblosecurity_for_import(manual_cookie)
                if valid:
                    _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
                    _mark_auth_cookie_available(manual_cookie)
                    return manual_cookie
                log_buffer.log(
                    'Auth',
                    f'Manual Roblox token was not valid ({detail}); not using it',
                )
        elif auth_source:
            browser_cookie, browser_source = discover_browser_roblosecurity(
                include_keychain=include_keychain_browsers,
                browser=auth_source,
            )
            if browser_cookie:
                _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
                _mark_auth_cookie_available(browser_cookie)
                return browser_cookie
        else:
            browser_cookie, browser_source = discover_browser_roblosecurity(
                include_keychain=False,
            )
            if browser_cookie:
                _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
                _mark_auth_cookie_available(browser_cookie)
                return browser_cookie
    elif sys.platform.startswith('linux'):
        browser_cookie, browser_source = discover_browser_roblosecurity(
            include_keychain=include_keychain_browsers,
        )
        if browser_cookie:
            _set_module_state('_LAST_AUTH_FAILURE_DETAILS', {})
            _mark_auth_cookie_available(browser_cookie)
            return browser_cookie
    else:
        browser_source = ''

    failure_details: dict[str, object] = {
        'local_appdata': str(LOCAL_APPDATA),
        'default_cookie_path': str(ROBLOX_COOKIES_PATH),
        'userprofile': os.environ.get('USERPROFILE') or '',
        'username': os.environ.get('USERNAME') or '',
        'home': str(USER_HOME),
        'attempted_paths': attempted,
        'existing_paths': existing,
        'browser_source': browser_source,
    }
    _set_module_state('_LAST_AUTH_FAILURE_DETAILS', failure_details)
    _log_auth_failure(
        'all-cookie-candidates-failed',
        (
            'Could not find a usable Roblox auth cookie after checking '
            f'{len(attempted)} candidate path(s); {len(existing)} RobloxCookies.dat file(s) existed'
        ),
    )
    return None


def set_roblosecurity(cookie: str, path: Path | None = None) -> bool:
    """Replace the active client's local .ROBLOSECURITY value."""
    if sys.platform.startswith('linux'):
        candidate = _selected_linux_local_auth_candidate()
        if candidate is None:
            error = LinuxAuthWriteError(
                'linux_client_not_installed',
                'Sober is not installed as a Flatpak, or Flatpak could not confirm the installation. '  # ruff: ignore[line-too-long]
                'Fleasion did not change any Sober account data.',
            )
            _log_auth_failure(
                'linux-cookie-write-provider-missing',
                str(error),
            )
            raise error
        provider, selected_path = candidate
        cookie_path = Path(path) if path is not None else selected_path
        return provider.write_roblosecurity(cookie_path, cookie)

    cookie_path = Path(path) if path is not None else ROBLOX_COOKIES_PATH
    if sys.platform != 'win32' or win32crypt is None:
        _log_auth_failure(
            f'write-unsupported:{cookie_path}',
            f'Cannot update Roblox auth storage at {cookie_path} on this platform',
        )
        return False
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        payload = _read_cookie_payload(cookie_path)
        if payload is None:
            return False

        data, dec = payload
        cookie_text = dec.decode('latin-1')
        new_text, _count = _replace_roblosecurity(cookie_text, cookie)
        new_enc = win32crypt.CryptProtectData(new_text.encode('latin-1'), None, None, None, None, 0)
        data['CookiesData'] = base64.b64encode(new_enc).decode('ascii')
        restore_read_only = _path_is_read_only(cookie_path)
        _clear_read_only(cookie_path)
        try:
            with cookie_path.open('w', encoding='utf-8') as f:
                json.dump(data, f)
        finally:
            if restore_read_only:
                _restore_read_only(cookie_path)
        return True  # ruff: ignore[try-consider-else]
    except Exception as exc:  # ruff: ignore[blind-except]
        _log_auth_failure(
            f'write:{cookie_path}:{type(exc).__name__}:{exc}',
            f'Failed to write Roblox auth cookie at {cookie_path}: {type(exc).__name__}: {exc}',
        )
        return False
