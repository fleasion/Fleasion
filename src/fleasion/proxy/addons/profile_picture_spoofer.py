"""ProfilePictureSpoofer: swaps Roblox avatar thumbnail URLs per player id.

Roblox Player asks ``thumbnails.roblox.com`` for avatar headshots/busts and
gets back JSON entries shaped like ``{"targetId": 123, "state": "Completed",
"imageUrl": "https://tr.rbxcdn.com/..."}``.  This addon rewrites ``imageUrl``
for the player ids the user configured, so the client downloads a different
picture for that player.

Each entry is ``{"enabled": bool, "target": "<user id>" | "__self__",
"image_url": "<https url or local file path>"}``.  ``__self__`` means "the
account that is logged in".

An entry may point at a *local* image file instead of a remote URL.  Roblox
no longer trusts custom certificates, so Fleasion can't run a separate HTTPS
server for the file.  Instead the local path is registered under a
proxy-owned URL on an intercepted CDN host
(``https://fts.rbxcdn.com/__fleasion__/pfp/<token>``).  Roblox fetches that
URL through the proxy like any other CDN request, and the proxy answers it
from disk using the same local-file serving as asset replacements.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, TypedDict, cast
from urllib.parse import unquote, urlparse

import requests

from fleasion.utils import log_buffer
from fleasion.utils.json_types import JsonObject, JsonValue, as_json_object

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from fleasion.proxy.server import ProxyFlow

THUMBNAILS_HOST = 'thumbnails.roblox.com'
SELF_TARGET = '__self__'
_SELF_ALIASES = frozenset({SELF_TARGET, 'me', 'self'})
_BATCH_PATH_FRAGMENT = '/v1/batch'
# Endpoints whose every entry is an avatar picture for ``targetId``.
_AVATAR_PATH_FRAGMENTS = ('/v1/users/avatar-headshot', '/v1/users/avatar-bust')
# ``/v1/batch`` mixes assets, outfits, places etc., and ``targetId`` is only a
# user id for the avatar types, so each entry's type (from the request body)
# decides whether it is an avatar picture.
PFP_ROUTE_HOST = 'fts.rbxcdn.com'
PFP_ROUTE_PREFIX = '/__fleasion__/pfp/'
_PFP_ROUTE_LIMIT = 1024
_PFP_ROUTE_GRACE_SECONDS = 120.0
PFP_MAX_IMAGE_BYTES = 10 * 1024 * 1024
_PFP_IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.gif', '.webp'})
_LOG_SEEN_LIMIT = 30


class PfpEntry(TypedDict):
    enabled: bool
    target: str
    image_url: str


class _ConfigSource(Protocol):
    @property
    def profile_picture_entries(self) -> list[JsonObject]: ...


class _CookieJarWithSet(Protocol):
    def set(self, name: str, value: str, *, domain: str) -> object: ...


def is_remote_image_url(value: str) -> bool:
    """Whether *value* is an http(s) URL rather than a local file path."""
    return value.lower().startswith(('http://', 'https://'))


def normalize_target(raw: object) -> str:
    """Return ``__self__`` for "me"-style input, the digits for a user id, else ''."""
    text = str(raw or '').strip()
    if text.lower() in _SELF_ALIASES:
        return SELF_TARGET
    return text if text.isdigit() else ''


def normalize_entries(raw: object) -> list[PfpEntry]:
    """Validate a loaded/imported list, dropping anything that isn't a usable entry."""
    if not isinstance(raw, list):
        return []
    entries: list[PfpEntry] = []
    for item in cast('list[object]', raw):
        mapping = as_json_object(item)
        if mapping is None:
            continue
        target = normalize_target(mapping.get('target', ''))
        image_url = str(mapping.get('image_url', '') or '').strip()
        if not target or not image_url:
            continue
        entries.append(
            {
                'enabled': bool(mapping.get('enabled', True)),
                'target': target,
                'image_url': image_url,
            }
        )
    return entries


def clean_local_path(raw: str) -> str:
    """Undo the common ways a pasted local path arrives unusable: matching
    surrounding quotes (e.g. Windows Explorer's "Copy as path"), a
    `file://` prefix, and incidental whitespace.
    """
    cleaned = raw.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {'"', "'"}:
        cleaned = cleaned[1:-1].strip()
    if cleaned.lower().startswith('file://'):
        parsed = urlparse(cleaned)
        cleaned = unquote(parsed.path)
        # A leading slash before a Windows drive letter (file:///C:/...)
        # needs stripping or Path() will treat it as a rooted posix path.
        if len(cleaned) >= 3 and cleaned[0] == '/' and cleaned[2] == ':':
            cleaned = cleaned[1:]
    return cleaned


_clean_local_path = clean_local_path

_routes: OrderedDict[str, tuple[str, float]] = OrderedDict()
_routes_lock = threading.Lock()


def is_pfp_route(host: str, path: str) -> bool:
    """Whether *host*/*path* is a proxy-owned local profile picture URL."""
    return host.lower() == PFP_ROUTE_HOST and path.split('?', 1)[0].startswith(PFP_ROUTE_PREFIX)


def register_local_image(path: Path) -> str:
    """Expose *path* under a proxy-owned CDN URL and return that URL."""
    resolved = path.resolve()
    info = resolved.stat()
    if (
        not resolved.is_file()
        or info.st_size > PFP_MAX_IMAGE_BYTES
        or resolved.suffix.lower() not in _PFP_IMAGE_EXTENSIONS
    ):
        msg = 'Profile picture must be a supported image file no larger than 10 MiB'
        raise ValueError(msg)
    mtime = info.st_mtime_ns
    # mtime is part of the token so editing the file yields a new URL and the
    # client can't keep showing a stale cached copy.
    token = hashlib.sha256(f'{resolved}|{mtime}'.encode()).hexdigest()[:32]
    route = f'{PFP_ROUTE_PREFIX}{token}{resolved.suffix.lower()}'
    with _routes_lock:
        now = time.monotonic()
        if route not in _routes:
            # Only evict routes old enough for Roblox to have fetched them
            # Unused imports must not break URLs already given to the client
            while len(_routes) >= _PFP_ROUTE_LIMIT:
                oldest, (_path, touched) = next(iter(_routes.items()))
                if now - touched < _PFP_ROUTE_GRACE_SECONDS:
                    msg = 'Profile picture CDN route capacity is temporarily full'
                    raise ValueError(msg)
                del _routes[oldest]
        _routes[route] = str(resolved), now
        _routes.move_to_end(route)
    return f'https://{PFP_ROUTE_HOST}{route}'


def lookup_local_image(host: str, path: str) -> str | None:
    """Resolve a proxy-owned profile picture URL back to its file on disk."""
    if not is_pfp_route(host, path):
        return None
    with _routes_lock:
        route = path.split('?', 1)[0]
        found = _routes.get(route)
        if found is not None:
            _routes[route] = found[0], time.monotonic()
            _routes.move_to_end(route)
    if found is None:
        return None
    image_path = found[0]
    try:
        info = Path(image_path).stat()
    except OSError:
        return None
    return image_path if info.st_size <= PFP_MAX_IMAGE_BYTES else None


def clear_local_images() -> None:
    with _routes_lock:
        _routes.clear()


def _selected_account_user_id(cookie: str) -> str | None:
    """Resolve an explicitly selected Account Manager credential to its user ID."""
    with requests.Session() as session:
        session.trust_env = False
        session.proxies = {}
        # Requests exposes a partially untyped cookie setter in some stub versions
        cast('_CookieJarWithSet', session.cookies).set(
            '.ROBLOSECURITY', cookie, domain='.roblox.com'
        )
        response = session.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
        if response.status_code != 200:
            return None
        payload = as_json_object(response.json())
        if payload is None:
            return None
        value = payload.get('id')
        if isinstance(value, bool) or not isinstance(value, int | str):
            return None
        text = str(value)
        return text if text.isascii() and text.isdecimal() and int(text) > 0 else None


class ProfilePictureSpoofer:
    """Rewrites avatar thumbnail responses for the configured player ids."""

    def __init__(self, config_manager: _ConfigSource | None = None) -> None:
        self._config = config_manager
        self._lock = threading.Lock()
        self._config_condition = threading.Condition(self._lock)
        self._config_loading = False
        self._entries: list[PfpEntry] = []
        self._current_user_id: str | None = None
        self._selection_generation = 0
        self._has_explicit_selection = False
        self._identity_listeners: list[Callable[[], None]] = []
        self._log_seen = 0
        self._config_loaded = config_manager is None
        self._entries_revision = 0

    def _load_config_entries(self) -> None:
        """Publish a completed load atomically, or accept a newer GUI update."""
        with self._config_condition:
            while self._config_loading and not self._config_loaded:
                # Release the lock so the loader or a GUI update can complete
                self._config_condition.wait()
            if self._config_loaded:
                return
            self._config_loading = True
            revision = self._entries_revision

        entries: list[PfpEntry] | None = None
        try:
            config = self._config
            entries = (
                normalize_entries(list(config.profile_picture_entries))
                if config is not None
                else []
            )
        finally:
            with self._config_condition:
                if entries is not None and self._entries_revision == revision:
                    self._entries = entries
                    self._config_loaded = True
                self._config_loading = False
                self._config_condition.notify_all()

    def is_enabled(self) -> bool:
        self._load_config_entries()
        with self._lock:
            return any(entry['enabled'] for entry in self._entries)

    def set_entries(self, entries: Iterable[object]) -> None:
        normalized = normalize_entries(list(entries))
        with self._config_condition:
            self._config_loaded = True
            self._entries_revision += 1
            self._entries = normalized
            # Readers blocked on an older disk load can now use the GUI snapshot
            self._config_condition.notify_all()

    def add_identity_listener(self, callback: Callable[[], None]) -> None:
        """Notify GUI previews when Account Manager's selected identity changes."""
        with self._lock:
            if callback not in self._identity_listeners:
                self._identity_listeners.append(callback)

    def remove_identity_listener(self, callback: Callable[[], None]) -> None:
        with self._lock:
            if callback in self._identity_listeners:
                self._identity_listeners.remove(callback)

    def _notify_identity_change(self) -> None:
        with self._lock:
            listeners = tuple(self._identity_listeners)
        for listener in listeners:
            try:
                listener()
            except RuntimeError:
                continue

    def current_user_id(self) -> str | None:
        """Return the selected Account Manager identity without network I/O."""
        with self._lock:
            return self._current_user_id

    def _self_user_id(self) -> str | None:
        return self.current_user_id()

    def set_current_user(self, user_id: str | None) -> None:
        """Update the selected account ID, as already resolved by Account Manager."""
        normalized = str(user_id) if user_id else None
        with self._lock:
            self._selection_generation += 1
            self._has_explicit_selection = True
            self._current_user_id = normalized
        self._notify_identity_change()

    def adopt_initial_account(self, user_id: str | None) -> None:
        """Adopt the Account Manager startup identity unless a user selected another."""
        if not user_id:
            return
        with self._lock:
            if self._has_explicit_selection:
                return
            self._current_user_id = str(user_id)
        self._notify_identity_change()

    def select_account_cookie(self, cookie: str) -> None:
        """Resolve the deliberate Account Manager selection away from the GUI thread."""
        with self._lock:
            self._selection_generation += 1
            generation = self._selection_generation
            self._has_explicit_selection = True
            self._current_user_id = None
        self._notify_identity_change()
        threading.Thread(
            target=self._resolve_selected_account,
            args=(cookie, generation),
            name='FleasionPfpSelectedAccount',
            daemon=True,
        ).start()

    def _resolve_selected_account(self, cookie: str, generation: int) -> None:
        """Ignore stale lookup results if the user selected another account."""
        user_id: str | None = None
        try:
            user_id = _selected_account_user_id(cookie)
        except requests.RequestException, OSError, ValueError, TypeError, RuntimeError:
            log_buffer.log('profile-picture-spoofer', 'Selected account lookup failed')
        with self._lock:
            if generation != self._selection_generation:
                return
            self._current_user_id = user_id
        self._notify_identity_change()

    def shutdown(self) -> None:
        with self._lock:
            self._selection_generation += 1
            self._current_user_id = None
        self._notify_identity_change()
        clear_local_images()

    def request(self, flow: ProxyFlow) -> None:
        del flow

    def _resolve_image_url(self, raw_image_url: str) -> str | None:
        """Turn a configured image string into something Roblox Player can GET.

        Remote URLs pass through. A local path becomes a proxy-owned CDN URL
        that the proxy answers from disk; a missing file yields ``None`` so the
        entry is skipped.
        """
        if is_remote_image_url(raw_image_url):
            return raw_image_url
        cleaned = clean_local_path(raw_image_url)
        path = Path(cleaned)
        if not path.is_file():
            log_buffer.log(
                'profile-picture-spoofer',
                f'Local profile picture file not found, skipping entry: {raw_image_url!r} '
                f'(resolved path: {path})',
            )
            return None
        try:
            return register_local_image(path)
        except (OSError, ValueError) as exc:
            log_buffer.log(
                'profile-picture-spoofer', f'Ignoring local picture: {type(exc).__name__}'
            )
            return None

    def _replacements(self, requested_ids: set[str]) -> dict[str, str]:
        """Resolve only configured avatars included in this upstream response.

        Local-file registration creates proxy-owned CDN routes. Registering all
        configured files before knowing which avatars were requested evicts
        still-advertised routes when importing large configurations.
        """
        if not requested_ids:
            return {}
        self._load_config_entries()
        with self._lock:
            entries = [entry for entry in self._entries if entry['enabled']]
        if not entries:
            return {}
        self_id = (
            self._self_user_id()
            if any(entry['target'] == SELF_TARGET for entry in entries)
            else None
        )
        replacements: dict[str, str] = {}
        for entry in entries:
            target = self_id if entry['target'] == SELF_TARGET else entry['target']
            if not target or target not in requested_ids or target in replacements:
                continue
            url = self._resolve_image_url(entry['image_url'])
            if url is not None:
                replacements[target] = url
        return replacements

    @staticmethod
    def _is_avatar_type(text: str) -> bool:
        """Whether a thumbnail type string names an avatar headshot or bust."""
        normalized = text.lower().replace('-', '').replace('_', '')
        return 'headshot' in normalized or 'bust' in normalized

    @staticmethod
    def _request_entries(flow: ProxyFlow) -> dict[str, tuple[str, str]]:
        """Map ``requestId`` -> (type, targetId) from a ``/v1/batch`` request body.

        The response only echoes ``requestId``/``targetId``; the thumbnail type
        (``AvatarHeadShot``, ``Asset``, ...) is only present in the request.
        """
        body = getattr(flow.request, 'content', b'') or b''
        try:
            parsed = cast('object', json.loads(bytes(body).decode('utf-8')))
        except UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError:
            return {}
        if not isinstance(parsed, list):
            return {}
        entries: dict[str, tuple[str, str]] = {}
        for item in cast('list[object]', parsed):
            mapping = as_json_object(item)
            if mapping is None:
                continue
            entries[str(mapping.get('requestId', ''))] = (
                str(mapping.get('type', '')),
                str(mapping.get('targetId', '')),
            )
        return entries

    @classmethod
    def _is_avatar_entry(
        cls,
        entry: dict[str, JsonValue],
        *,
        batch: bool,
        request_entries: dict[str, tuple[str, str]],
    ) -> bool:
        if not batch:
            return True
        request_id = str(entry.get('requestId', ''))
        requested = request_entries.get(request_id)
        if requested is not None and requested[0]:
            return cls._is_avatar_type(requested[0])
        # No request body to consult: fall back to a type embedded in the id.
        return cls._is_avatar_type(request_id)

    @classmethod
    def _rewrite_payload(
        cls,
        payload: dict[str, JsonValue],
        replacements: dict[str, str],
        *,
        batch: bool,
        request_entries: dict[str, tuple[str, str]] | None = None,
    ) -> int:
        data = payload.get('data')
        if not isinstance(data, list):
            return 0
        requested_by_id = request_entries or {}
        changed = 0
        for item in data:
            entry = as_json_object(item)
            if entry is None or not cls._is_avatar_entry(
                entry, batch=batch, request_entries=requested_by_id
            ):
                continue
            requested = requested_by_id.get(str(entry.get('requestId', '')))
            target = str(entry.get('targetId') or (requested[1] if requested else ''))
            new_url = replacements.get(target)
            if new_url is None:
                continue
            entry['imageUrl'] = new_url
            entry['state'] = 'Completed'
            changed += 1
        return changed

    def _describe_sample(
        self,
        payload: dict[str, JsonValue],
        request_entries: dict[str, tuple[str, str]],
    ) -> str:
        """Short, log-friendly summary of the first few thumbnail entries."""
        data = payload.get('data')
        if not isinstance(data, list):
            return 'no data list'
        parts: list[str] = []
        for item in data[:5]:
            entry = as_json_object(item)
            if entry is None:
                continue
            request_id = str(entry.get('requestId', ''))
            thumb_type = request_entries.get(request_id, ('?', ''))[0] or '?'
            url = str(entry.get('imageUrl') or '')
            # URLs can carry signed query tokens; log only the hostname
            try:
                image_host = urlparse(url).hostname or '(none)'
            except ValueError:
                image_host = '(invalid)'
            parts.append(
                f'id={entry.get("targetId")} type={thumb_type} '
                f'req={request_id[:40]!r} image_host={image_host}'
            )
        return f'{len(data)} entr{"y" if len(data) == 1 else "ies"}: ' + ' | '.join(parts)

    def response(self, flow: ProxyFlow) -> None:
        response = flow.response
        if response is None or not response.content or not self.is_enabled():
            return
        url = urlparse(flow.request.pretty_url)
        if url.hostname != THUMBNAILS_HOST or url.scheme != 'https':
            return
        path = url.path
        batch = path.startswith(_BATCH_PATH_FRAGMENT)
        if not batch and not any(path.startswith(fragment) for fragment in _AVATAR_PATH_FRAGMENTS):
            return
        try:
            payload = as_json_object(json.loads(response.content.decode('utf-8')))
        except (UnicodeDecodeError, ValueError) as exc:
            log_buffer.log('profile-picture-spoofer', f'Failed to read thumbnail response: {exc}')
            return
        if payload is None or self._spoof_payload(flow, payload, path, batch=batch) <= 0:
            return
        response.content = json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode(
            'utf-8'
        )

    def _spoof_payload(
        self,
        flow: ProxyFlow,
        payload: dict[str, JsonValue],
        path: str,
        *,
        batch: bool,
    ) -> int:
        """Rewrite *payload* in place and return how many entries changed."""
        request_entries = self._request_entries(flow) if batch else {}
        self._log_once(f'{path} response, {self._describe_sample(payload, request_entries)}')
        data = payload.get('data')
        requested_ids: set[str] = set()
        if isinstance(data, list):
            for item in data:
                entry = as_json_object(item)
                if entry is None or not self._is_avatar_entry(
                    entry, batch=batch, request_entries=request_entries
                ):
                    continue
                requested = request_entries.get(str(entry.get('requestId', '')))
                target = str(entry.get('targetId') or (requested[1] if requested else ''))
                if target:
                    requested_ids.add(target)
        replacements = self._replacements(requested_ids)
        if not replacements:
            self._log_once(f'{path}: no usable entry (check the Player ID and image)')
            return 0
        changed = self._rewrite_payload(
            payload, replacements, batch=batch, request_entries=request_entries
        )
        self._log_once(
            f'{path}: rewrote {changed} avatar thumbnail(s) for ids {sorted(replacements)}'
        )
        return changed

    def _log_once(self, message: str) -> None:
        """Log the first few thumbnail responses so a user can confirm traffic arrives."""
        if self._log_seen >= _LOG_SEEN_LIMIT:
            return
        self._log_seen += 1
        log_buffer.log('profile-picture-spoofer', message)
