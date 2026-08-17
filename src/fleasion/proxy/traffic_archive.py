"""Bounded, credential-safe persistence for proxy traffic snapshots."""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import parse_qsl, urlencode

from ..utils.paths import PROXY_TRAFFIC_FILE

if TYPE_CHECKING:
    from ..config.manager import ConfigManager

log = logging.getLogger(__name__)

ARCHIVE_VERSION: Final = 1
MAX_ARCHIVE_ENTRIES: Final = 1_000
MAX_ARCHIVE_FILE_BYTES: Final = 8 * 1024 * 1024
MAX_ARCHIVED_MESSAGE_BYTES: Final = 256 * 1024
_MAX_HOST_CHARS: Final = 512
_MAX_PATH_CHARS: Final = 8_192
_MAX_TEXT_FIELD_CHARS: Final = 2_048
_BINARY_OMITTED: Final = b'<binary payload omitted from preserved traffic>'
_REDACTED: Final = '<redacted>'
_PERSISTED_FIELDS: Final = (
    'time',
    'host',
    'port',
    'method',
    'path',
    'intercepted',
    'status',
    'size',
    'ms',
    'was_intercepted',
    'dropped_request',
    'dropped_response',
)
_SENSITIVE_NAME_PARTS: Final = (
    'accesscode',
    'apikey',
    'authentication',
    'authorization',
    'authcookie',
    'authticket',
    'cookie',
    'credential',
    'csrf',
    'linkcode',
    'password',
    'privatekey',
    'privateserverlinkcode',
    'proxyauthorization',
    'roblosecurity',
    'secret',
    'sessioncookie',
    'setcookie',
    'ticket',
    'token',
    'xapikey',
)
_QUERY_SECRET_PATTERN: Final = re.compile(
    r'(?i)([?&](?:access[_-]?code|api[_-]?key|auth(?:orization)?|credential|csrf|'
    r'key|link[_-]?code|password|private[_-]?server[_-]?link[_-]?code|secret|'
    r'ticket|token)=)([^&#\s]*)'
)
_ROBLOSECURITY_PATTERN: Final = re.compile(
    r'(?i)(\.ROBLOSECURITY\s*[=:]\s*)([^\s;&,"\']+)'
)
_BEARER_PATTERN: Final = re.compile(r'(?i)(\bBearer\s+)([^\s,;]+)')
_BASIC_PATTERN: Final = re.compile(r'(?i)(\bBasic\s+)([A-Za-z0-9+/=_-]+)')
_URL_USERINFO_PATTERN: Final = re.compile(r'(?i)(https?://)[^/@\s]+@')
_SENSITIVE_LINE_PATTERN: Final = re.compile(
    r'(?im)^(\s*[\w-]*(?:authorization|cookie|credential|csrf|password|secret|ticket|token|'
    r'api-key)[\w-]*\s*:)[^\r\n]*'
)
_QUOTED_SECRET_PATTERN: Final = re.compile(
    r'(?i)(["\'][^"\']{0,80}(?:access[_-]?code|api[_-]?key|auth(?:entication|orization)?|'
    r'cookie|credential|csrf|password|private[_-]?key|private[_-]?server[_-]?link[_-]?code|'
    r'secret|ticket|token)[^"\']{0,80}["\']\s*[:=]\s*["\'])([^"\r\n]{0,8192})(["\'])'
)


def _is_sensitive_name(name: str) -> bool:
    normalized = ''.join(character for character in name.casefold() if character.isalnum())
    return any(part in normalized for part in _SENSITIVE_NAME_PARTS)


def _bounded_text(value: Any, limit: int) -> str:
    text = str(value or '').replace('\x00', '')
    return text[:limit]


def _sanitize_host(value: Any) -> str:
    host = _redact_inline_secrets(_bounded_text(value, _MAX_HOST_CHARS))
    if '@' in host and '://' not in host:
        return f'{_REDACTED}@{host.rsplit("@", 1)[-1]}'
    return host


def _redact_inline_secrets(text: str) -> str:
    redacted = _SENSITIVE_LINE_PATTERN.sub(
        lambda match: f'{match.group(1)} {_REDACTED}', text
    )
    redacted = _URL_USERINFO_PATTERN.sub(
        lambda match: f'{match.group(1)}{_REDACTED}@', redacted
    )
    redacted = _QUERY_SECRET_PATTERN.sub(
        lambda match: f'{match.group(1)}{_REDACTED}', redacted
    )
    lowered = redacted.casefold()
    if any(part in lowered for part in _SENSITIVE_NAME_PARTS):
        redacted = _QUOTED_SECRET_PATTERN.sub(
            lambda match: f'{match.group(1)}{_REDACTED}{match.group(3)}',
            redacted,
        )
    redacted = _ROBLOSECURITY_PATTERN.sub(
        lambda match: f'{match.group(1)}{_REDACTED}', redacted
    )
    redacted = _BEARER_PATTERN.sub(lambda match: f'{match.group(1)}{_REDACTED}', redacted)
    return _BASIC_PATTERN.sub(lambda match: f'{match.group(1)}{_REDACTED}', redacted)


def _redact_json_value(value: Any, key: str = '') -> Any:
    if key and _is_sensitive_name(key):
        return _REDACTED
    if isinstance(value, dict):
        return {
            str(child_key): _redact_json_value(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_json_value(child) for child in value]
    if isinstance(value, str):
        return _redact_inline_secrets(value)
    return value


def _sanitize_form_body(text: str) -> str:
    pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=False)
    if not pairs and text:
        return _redact_inline_secrets(text)
    return urlencode(
        [
            (key, _REDACTED if _is_sensitive_name(key) else _redact_inline_secrets(value))
            for key, value in pairs
        ]
    )


def _sanitize_text_body(body: bytes, content_type: str) -> bytes:
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError:
        return _BINARY_OMITTED

    lowered_type = content_type.casefold()
    if 'json' in lowered_type or text.lstrip().startswith(('{', '[')):
        try:
            parsed = json.loads(text)
            redacted = _redact_json_value(parsed)
            return json.dumps(
                redacted,
                ensure_ascii=False,
                separators=(',', ':'),
            ).encode('utf-8')
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError):
            pass
    if 'application/x-www-form-urlencoded' in lowered_type:
        return _sanitize_form_body(text).encode('utf-8')
    return _redact_inline_secrets(text).encode('utf-8')


def sanitize_http_message(value: Any) -> bytes | None:
    """Return a bounded HTTP preview with credential-bearing values redacted."""
    if not isinstance(value, bytes | bytearray | memoryview) or not value:
        return None
    raw = bytes(value[:MAX_ARCHIVED_MESSAGE_BYTES])
    separator = b'\r\n\r\n'
    line_separator = b'\r\n'
    if separator not in raw:
        separator = b'\n\n'
        line_separator = b'\n'
    has_separator = separator in raw
    if has_separator:
        header_bytes, body = raw.split(separator, 1)
    else:
        header_bytes, body = raw, b''

    first_line = header_bytes.split(line_separator, 1)[0]
    if not has_separator and not (
        first_line.startswith(b'HTTP/')
        or re.match(rb'^[A-Z]{2,16}\s+\S+(?:\s+HTTP/\d(?:\.\d)?)?$', first_line)
    ):
        return _sanitize_text_body(raw, '')[:MAX_ARCHIVED_MESSAGE_BYTES]

    header_text = header_bytes.decode('latin-1', errors='replace')
    lines = header_text.split(line_separator.decode('ascii'))
    sanitized_lines: list[str] = []
    content_type = ''
    for index, line in enumerate(lines):
        if index == 0 or ':' not in line:
            sanitized_lines.append(_redact_inline_secrets(line))
            continue
        name, raw_value = line.split(':', 1)
        if name.strip().casefold() == 'content-type':
            content_type = raw_value.strip()
        value_text = _REDACTED if _is_sensitive_name(name) else _redact_inline_secrets(raw_value)
        sanitized_lines.append(f'{name}:{value_text}')

    sanitized_headers = line_separator.join(
        line.encode('latin-1', errors='replace') for line in sanitized_lines
    )
    if not body:
        return sanitized_headers[:MAX_ARCHIVED_MESSAGE_BYTES]
    sanitized_body = _sanitize_text_body(body, content_type)
    available = max(0, MAX_ARCHIVED_MESSAGE_BYTES - len(sanitized_headers) - len(separator))
    return sanitized_headers + separator + sanitized_body[:available]


def _finite_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _optional_number(value: Any) -> int | float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def sanitize_traffic_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Copy one traffic entry into the safe, serializable archive schema."""
    status = entry.get('status')
    safe_status: int | str | None
    if isinstance(status, int) and not isinstance(status, bool):
        safe_status = status
    elif status is None:
        safe_status = None
    else:
        safe_status = _bounded_text(status, _MAX_TEXT_FIELD_CHARS)
    return {
        'time': _finite_number(entry.get('time')),
        'host': _sanitize_host(entry.get('host')),
        'port': max(0, min(65_535, int(_finite_number(entry.get('port'), 443)))),
        'method': _bounded_text(entry.get('method'), 32),
        'path': _redact_inline_secrets(_bounded_text(entry.get('path'), _MAX_PATH_CHARS)),
        'intercepted': bool(entry.get('intercepted')),
        'status': safe_status,
        'size': max(0, int(_finite_number(entry.get('size')))),
        'ms': _optional_number(entry.get('ms')),
        'was_intercepted': bool(entry.get('was_intercepted')),
        'dropped_request': bool(entry.get('dropped_request')),
        'dropped_response': bool(entry.get('dropped_response')),
        'request_raw': sanitize_http_message(entry.get('request_raw')),
        'response_raw': sanitize_http_message(entry.get('response_raw')),
        'pending_stage': None,
        'archived': True,
    }


def _entry_to_stored(entry: dict[str, Any]) -> dict[str, Any]:
    safe = sanitize_traffic_entry(entry)
    stored = {field: safe[field] for field in _PERSISTED_FIELDS}
    archive_id = _valid_archive_id(entry.get('id'))
    stored['archive_id'] = archive_id
    for raw_key in ('request_raw', 'response_raw'):
        raw = safe[raw_key]
        stored[raw_key] = base64.b64encode(raw).decode('ascii') if raw else None
    return stored


def _decode_stored_message(value: Any) -> bytes | None:
    if value is None or value == '':
        return None
    if not isinstance(value, str) or len(value) > MAX_ARCHIVED_MESSAGE_BYTES * 2:
        return None
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        return None
    return sanitize_http_message(decoded[:MAX_ARCHIVED_MESSAGE_BYTES])


def _decode_trusted_message(value: Any) -> bytes | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return base64.b64decode(value, validate=True)[:MAX_ARCHIVED_MESSAGE_BYTES]
    except (TypeError, ValueError):
        return None


def _stored_to_entry(
    stored: dict[str, Any],
    *,
    trusted: bool = False,
) -> dict[str, Any]:
    if trusted:
        entry = {field: stored.get(field) for field in _PERSISTED_FIELDS}
        entry['request_raw'] = _decode_trusted_message(stored.get('request_raw'))
        entry['response_raw'] = _decode_trusted_message(stored.get('response_raw'))
        entry['pending_stage'] = None
        entry['archived'] = True
        archive_id = _valid_archive_id(stored.get('archive_id'))
        if archive_id is not None:
            entry['id'] = archive_id
        return entry
    candidate = {field: stored.get(field) for field in _PERSISTED_FIELDS}
    candidate['request_raw'] = _decode_stored_message(stored.get('request_raw'))
    candidate['response_raw'] = _decode_stored_message(stored.get('response_raw'))
    entry = sanitize_traffic_entry(candidate)
    archive_id = _valid_archive_id(stored.get('archive_id'))
    if archive_id is not None:
        entry['id'] = archive_id
    return entry


def _valid_archive_id(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and -(2**63) <= value < 0:
        return value
    return None


def _entries_with_stable_ids(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reserved = {
        archive_id
        for entry in entries
        if (archive_id := _valid_archive_id(entry.get('id'))) is not None
    }
    used: set[int] = set()
    next_id = -1
    result: list[dict[str, Any]] = []
    for entry in entries:
        archive_id = _valid_archive_id(entry.get('id'))
        if archive_id is None or archive_id in used:
            while next_id in reserved or next_id in used:
                next_id -= 1
            archive_id = next_id
            next_id -= 1
        archived = dict(entry)
        archived['id'] = archive_id
        archived['pending_stage'] = None
        archived['archived'] = True
        result.append(archived)
        used.add(archive_id)
    return result


def _serialized_archive(entries: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
    stable_entries = _entries_with_stable_ids(entries[-MAX_ARCHIVE_ENTRIES:])
    selected_reversed: list[dict[str, Any]] = []
    used_bytes = 64
    for entry in reversed(stable_entries):
        stored = _entry_to_stored(entry)
        encoded = json.dumps(stored, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        if selected_reversed and used_bytes + len(encoded) + 1 > MAX_ARCHIVE_FILE_BYTES:
            break
        if len(encoded) + 64 > MAX_ARCHIVE_FILE_BYTES:
            continue
        selected_reversed.append(stored)
        used_bytes += len(encoded) + 1
    selected = list(reversed(selected_reversed))
    payload = json.dumps(
        {'version': ARCHIVE_VERSION, 'entries': selected},
        ensure_ascii=False,
        separators=(',', ':'),
    ).encode('utf-8')
    while selected and len(payload) > MAX_ARCHIVE_FILE_BYTES:
        selected.pop(0)
        payload = json.dumps(
            {'version': ARCHIVE_VERSION, 'entries': selected},
            ensure_ascii=False,
            separators=(',', ':'),
        ).encode('utf-8')
    return payload, selected


class ProxyTrafficArchive:
    """Persist sanitized traffic while keeping archived IDs separate from live IDs."""

    def __init__(
        self,
        config_manager: ConfigManager | None,
        path: Path | None = None,
    ) -> None:
        self._config = config_manager
        configured_path = (
            getattr(config_manager, 'proxy_traffic_archive_path', PROXY_TRAFFIC_FILE)
            if config_manager is not None
            else PROXY_TRAFFIC_FILE
        )
        self._path = Path(path if path is not None else configured_path)
        self._lock = threading.RLock()
        self._archive: list[dict[str, Any]] = []
        self._live_archive_ids: dict[tuple[int, float], int] = {}
        if self.enabled:
            self._archive = self._assign_ids(self._load())
            self._write(self._archive)
        elif self._config is not None:
            self.clear()

    @property
    def enabled(self) -> bool:
        return bool(
            self._config is not None
            and getattr(self._config, 'proxy_traffic_preserve', False)
        )

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._archive)

    def entries(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(entry) for entry in self._archive]

    def combined(self, live_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            archived = [dict(entry) for entry in self._archive]
        return [*archived, *(dict(entry) for entry in live_entries)]

    def set_enabled(self, enabled: bool, live_entries: list[dict[str, Any]]) -> None:
        if self._config is None:
            return
        setattr(self._config, 'proxy_traffic_preserve', bool(enabled))
        if enabled:
            self.save_snapshot(live_entries)
        else:
            self.clear()

    def save_snapshot(self, live_entries: list[dict[str, Any]]) -> bool:
        with self._lock:
            if not self.enabled:
                return False
            tagged_live = self._tag_live_entries(live_entries)
            return self._write([*self._archive, *tagged_live])

    def checkpoint(self, live_entries: list[dict[str, Any]]) -> bool:
        if not live_entries:
            return False
        with self._lock:
            if not self.enabled:
                return False
            tagged_live = self._tag_live_entries(live_entries)
            payload, selected = _serialized_archive([*self._archive, *tagged_live])
            self._archive = self._assign_ids(
                [
                    _stored_to_entry(stored, trusted=True)
                    for stored in selected
                    if isinstance(stored, dict)
                ]
            )
            self._live_archive_ids = {}
            return self._write_payload(payload)

    def clear(self) -> None:
        with self._lock:
            self._archive = []
            self._live_archive_ids = {}
            try:
                self._path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning('Could not clear preserved proxy traffic: %s', exc)

    @staticmethod
    def _assign_ids(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return _entries_with_stable_ids(entries)

    def _tag_live_entries(
        self,
        live_entries: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        used_ids = {
            archive_id
            for entry in self._archive
            if (archive_id := _valid_archive_id(entry.get('id'))) is not None
        }
        used_ids.update(self._live_archive_ids.values())
        next_id = -1
        result: list[dict[str, Any]] = []
        for entry in live_entries:
            request_id = entry.get('id')
            if not isinstance(request_id, int) or isinstance(request_id, bool) or request_id < 0:
                result.append(dict(entry))
                continue
            identity = (request_id, _finite_number(entry.get('time')))
            archive_id = self._live_archive_ids.get(identity)
            if archive_id is None:
                while next_id in used_ids:
                    next_id -= 1
                archive_id = next_id
                next_id -= 1
                self._live_archive_ids[identity] = archive_id
                used_ids.add(archive_id)
            tagged = dict(entry)
            tagged['id'] = archive_id
            tagged['pending_stage'] = None
            tagged['archived'] = True
            result.append(tagged)
        return result

    def _load(self) -> list[dict[str, Any]]:
        try:
            if self._path.stat().st_size > MAX_ARCHIVE_FILE_BYTES:
                raise ValueError('archive exceeds the size limit')
            payload: Any = json.loads(self._path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (
            OSError,
            RecursionError,
            UnicodeError,
            ValueError,
            TypeError,
            json.JSONDecodeError,
        ) as exc:
            log.warning('Could not restore preserved proxy traffic: %s', exc)
            return []
        if not isinstance(payload, dict):
            return []
        version = payload.get('version', ARCHIVE_VERSION)
        entries = payload.get('entries')
        if version != ARCHIVE_VERSION or not isinstance(entries, list):
            return []
        restored = [
            _stored_to_entry(stored)
            for stored in entries[-MAX_ARCHIVE_ENTRIES:]
            if isinstance(stored, dict)
        ]
        _payload, bounded = _serialized_archive(restored)
        return [_stored_to_entry(stored) for stored in bounded]

    def _write(self, entries: list[dict[str, Any]]) -> bool:
        payload, _selected = _serialized_archive(entries)
        return self._write_payload(payload)

    def _write_payload(self, payload: bytes) -> bool:
        temporary = self._path.with_name(f'{self._path.name}.tmp')
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(payload)
            temporary.chmod(0o600)
            os.replace(temporary, self._path)
            self._path.chmod(0o600)
        except OSError as exc:
            log.warning('Could not preserve proxy traffic: %s', exc)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        return True
