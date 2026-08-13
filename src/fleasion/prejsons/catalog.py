"""Typed community preset catalog and custom import storage."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from ..utils.http import http_get
from ..utils.paths import CLOG_URL, ORIGINALS_DIR, PREJSONS_DIR, REPLACEMENTS_DIR
from .values import JsonValue

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    type FetchBytes = Callable[[str, int], bytes]

CUSTOM_DUMPS_DIR: Final = PREJSONS_DIR / 'custom_dumps'
CLOG_CACHE_FILE: Final = PREJSONS_DIR / 'CLOG.json'
MAX_JSON_BYTES: Final = 32 * 1024 * 1024
_INVALID_FILENAME: Final = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True, slots=True)
class CommunityPreset:
    """Normalized catalog entry used by both backends and QML."""

    preset_id: str
    name: str
    created: str = ''
    updated: str = ''
    credit: str = ''
    place_id: int | None = None
    originals_source: str = ''
    replacements_source: str = ''
    custom_path: Path | None = None

    @property
    def is_custom(self) -> bool:
        """Return whether the entry came from a saved custom catalog."""
        return self.custom_path is not None

    @property
    def search_text(self) -> str:
        """Return searchable metadata for the catalog model."""
        return f'{self.name} {self.credit} {self.place_id or ""}'

    def as_catalog_mapping(self) -> dict[str, object]:
        """Return the stable on-disk custom catalog representation."""
        result: dict[str, object] = {'name': self.name}
        if self.created:
            result['created'] = self.created
        if self.updated:
            result['updated'] = self.updated
        if self.credit:
            result['credit'] = self.credit
        if self.place_id is not None:
            result['placeId'] = self.place_id
        if self.originals_source:
            result['github'] = self.originals_source
        if self.replacements_source:
            result['replacement'] = self.replacements_source
        return result


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Catalog entries plus a recoverable load warning."""

    presets: tuple[CommunityPreset, ...]
    warning: str = ''


@dataclass(frozen=True, slots=True)
class CustomPresetRequest:
    """User input for importing a custom community preset definition."""

    catalog_source: str = ''
    name: str = ''
    place_id: str = ''
    originals_source: str = ''
    replacements_source: str = ''
    credit: str = ''


def safe_filename(name: str) -> str:
    """Return a portable, bounded filename stem."""
    return _INVALID_FILENAME.sub('_', name).strip(' .')[:128] or 'preset'


def _text(mapping: Mapping[str, object], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str):
            return value.strip()
    return ''


def _place_id(mapping: Mapping[str, object]) -> int | None:
    for key in ('placeId', 'place_id', 'id'):
        value = mapping.get(key)
        if value is None or isinstance(value, bool):
            continue
        if not isinstance(value, int | float | str):
            continue
        try:
            return int(value)
        except OverflowError, TypeError, ValueError:
            continue
    return None


def _identifier(origin: str, index: int, entry: CommunityPreset) -> str:
    identity = (
        f'{origin}\0{index}\0{entry.name}\0{entry.place_id}\0'
        f'{entry.originals_source}\0{entry.replacements_source}'
    )
    return f'{origin}:{hashlib.sha256(identity.encode()).hexdigest()[:20]}'


def normalize_preset_entry(
    value: object,
    *,
    fallback_name: str = '',
    origin: str = 'official',
    index: int = 0,
    custom_path: Path | None = None,
) -> CommunityPreset | None:
    """Normalize one legacy CLOG entry.

    Parameters
    ----------
    value
        Candidate mapping from a CLOG or custom dump document.
    fallback_name
        Name supplied by a mapping key when absent from the entry.
    origin
        Stable identifier namespace.
    index
        Position within the source document.
    custom_path
        Saved custom dump containing this entry.

    Returns
    -------
    CommunityPreset | None
        A normalized entry, or ``None`` when no usable name can be found.
    """
    if not isinstance(value, dict):
        return None
    mapping = cast('Mapping[str, object]', value)
    place_id = _place_id(mapping)
    name = _text(mapping, 'name', 'game') or fallback_name.strip()
    if not name and place_id is not None:
        name = f'Place {place_id}'
    if not name:
        return None
    provisional = CommunityPreset(
        preset_id='',
        name=name,
        created=_text(mapping, 'created'),
        updated=_text(mapping, 'updated'),
        credit=_text(mapping, 'credit', 'Credit', 'Owner', 'owner', 'author', 'Author'),
        place_id=place_id,
        originals_source=_text(mapping, 'github'),
        replacements_source=_text(mapping, 'replacement', 'Replacement'),
        custom_path=custom_path,
    )
    return replace(provisional, preset_id=_identifier(origin, index, provisional))


def normalize_catalog(
    document: object,
    *,
    origin: str = 'official',
    custom_path: Path | None = None,
) -> list[CommunityPreset]:
    """Convert CLOG and custom dump shapes into normalized entries."""
    if not isinstance(document, dict):
        return []
    root = cast('Mapping[str, object]', document)
    games = root.get('games', root.get('Games'))
    if games is None and ('name' in root or _place_id(root) is not None):
        games = [root]

    candidates: list[tuple[str, object]] = []
    if isinstance(games, dict):
        candidates.extend((str(name), value) for name, value in games.items())
    elif isinstance(games, list):
        candidates.extend(('', value) for value in games)

    normalized: list[CommunityPreset] = []
    for index, (fallback_name, value) in enumerate(candidates):
        entry = normalize_preset_entry(
            value,
            fallback_name=fallback_name,
            origin=origin,
            index=index,
            custom_path=custom_path,
        )
        if entry is not None:
            normalized.append(entry)
    return normalized


def parse_json_document(raw: bytes, *, source: str = 'JSON') -> JsonValue:
    """Parse a bounded JSON document with a useful validation error."""
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError(f'{source} is larger than the 32 MiB safety limit.')
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f'{source} is not valid JSON.') from exc
    if not isinstance(value, dict | list):
        raise ValueError(f'{source} must contain a JSON object or array.')
    return cast('JsonValue', value)


def _local_path(source: str) -> Path | None:
    parsed = urlparse(source)
    if parsed.scheme in {'http', 'https'}:
        return None
    if parsed.scheme == 'file':
        path_text = url2pathname(unquote(parsed.path))
        if parsed.netloc and parsed.netloc not in {'', 'localhost'}:
            path_text = f'//{parsed.netloc}{path_text}'
        return Path(path_text)
    if not parsed.scheme or (len(parsed.scheme) == 1 and source[1:2] == ':'):
        return Path(source).expanduser()
    raise ValueError('Only HTTP, HTTPS, and local JSON sources are supported.')


def read_source_bytes(
    source: str,
    *,
    fetch: FetchBytes | None = None,
    timeout: int = 15,
) -> bytes:
    """Read an approved URL or local path and enforce the payload size limit."""
    cleaned = source.strip()
    if not cleaned:
        raise ValueError('Choose a JSON source.')
    path = _local_path(cleaned)
    if path is not None:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise ValueError('The JSON source is larger than the 32 MiB safety limit.')
        raw = path.read_bytes()
    elif fetch is None:
        raw = http_get(cleaned, timeout=timeout, max_bytes=MAX_JSON_BYTES)
    else:
        raw = fetch(cleaned, timeout)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError('The JSON source is larger than the 32 MiB safety limit.')
    return raw


def load_json_source(
    source: str,
    *,
    fetch: FetchBytes | None = None,
    timeout: int = 15,
) -> JsonValue:
    """Read and parse a JSON URL or local file."""
    return parse_json_document(
        read_source_bytes(source, fetch=fetch, timeout=timeout),
        source=source,
    )


class CommunityPresetCatalog:
    """Load the official catalog and safely persist custom definitions."""

    def __init__(
        self,
        *,
        catalog_url: str = CLOG_URL,
        cache_file: Path = CLOG_CACHE_FILE,
        custom_dumps_dir: Path = CUSTOM_DUMPS_DIR,
        originals_dir: Path = ORIGINALS_DIR,
        replacements_dir: Path = REPLACEMENTS_DIR,
        fetch: FetchBytes | None = None,
    ) -> None:
        self.catalog_url = catalog_url
        self.cache_file = cache_file
        self.custom_dumps_dir = custom_dumps_dir
        self.originals_dir = originals_dir
        self.replacements_dir = replacements_dir
        self._fetch = fetch

    def load(self, *, refresh: bool = True) -> CatalogSnapshot:
        """Load official entries with cache fallback, then append custom entries."""
        warning = ''
        raw: bytes | None = None
        if refresh:
            try:
                raw = read_source_bytes(self.catalog_url, fetch=self._fetch)
                parse_json_document(raw, source='Community catalog')
                _atomic_write_bytes(self.cache_file, raw)
            except (OSError, RuntimeError, ValueError) as exc:
                warning = f'Could not refresh the community catalog: {exc}'
        if raw is None:
            try:
                raw = self.cache_file.read_bytes()
            except OSError as exc:
                if not warning:
                    warning = f'No cached community catalog is available: {exc}'

        official: list[CommunityPreset] = []
        if raw is not None:
            try:
                document = parse_json_document(raw, source='Community catalog')
                official = normalize_catalog(document)
            except ValueError as exc:
                warning = str(exc)

        custom, custom_warning = self._load_custom()
        warnings = [message for message in (warning, custom_warning) if message]
        return CatalogSnapshot(tuple((*official, *custom)), ' '.join(warnings))

    def import_custom(self, request: CustomPresetRequest) -> tuple[CommunityPreset, ...]:
        """Validate, materialize, and atomically save a custom preset definition."""
        candidates = self._request_entries(request)
        if not candidates:
            raise ValueError('No valid preset entries were found.')

        token = uuid.uuid4().hex
        created_files: list[Path] = []
        materialized: list[CommunityPreset] = []
        try:
            for index, entry in enumerate(candidates):
                originals = self._materialize_source(
                    entry.originals_source,
                    self.originals_dir,
                    entry.name,
                    token,
                    index,
                    'originals',
                    created_files,
                )
                replacements = self._materialize_source(
                    entry.replacements_source,
                    self.replacements_dir,
                    entry.name,
                    token,
                    index,
                    'replacements',
                    created_files,
                )
                materialized.append(
                    replace(
                        entry,
                        originals_source=originals,
                        replacements_source=replacements,
                    )
                )

            dump_path = self.custom_dumps_dir / f'{token}.json'
            dump_data = self._dump_document(materialized)
            _atomic_write_bytes(
                dump_path,
                json.dumps(dump_data, indent=2, ensure_ascii=False).encode('utf-8'),
            )
            created_files.append(dump_path)
        except Exception:
            for path in reversed(created_files):
                path.unlink(missing_ok=True)
            raise

        document = cast('JsonValue', dump_data)
        return tuple(normalize_catalog(document, origin=f'custom-{token}', custom_path=dump_path))

    def delete_custom(self, path: Path) -> bool:
        """Delete one custom dump only when it is inside the managed directory."""
        try:
            root = self.custom_dumps_dir.resolve()
            candidate = path.resolve()
            if candidate.parent != root or candidate.suffix.casefold() != '.json':
                return False
            candidate.unlink()
        except OSError:
            return False
        return True

    def load_payload(self, source: str) -> JsonValue:
        """Load one originals or replacements payload."""
        return load_json_source(source, fetch=self._fetch)

    def _load_custom(self) -> tuple[list[CommunityPreset], str]:
        entries: list[CommunityPreset] = []
        invalid_count = 0
        try:
            files = sorted(self.custom_dumps_dir.glob('*.json'))
        except OSError:
            return entries, 'Custom presets could not be scanned.'
        for path in files:
            try:
                document = parse_json_document(path.read_bytes(), source=path.name)
                entries.extend(
                    normalize_catalog(
                        document,
                        origin=f'custom-{path.stem}',
                        custom_path=path,
                    )
                )
            except OSError, ValueError:
                invalid_count += 1
        warning = (
            f'{invalid_count} custom preset file(s) could not be read.' if invalid_count else ''
        )
        return entries, warning

    def _request_entries(self, request: CustomPresetRequest) -> list[CommunityPreset]:
        if request.catalog_source.strip():
            document = load_json_source(request.catalog_source, fetch=self._fetch)
            return normalize_catalog(document, origin='pending-custom')

        place_text = request.place_id.strip()
        place_id: int | None = None
        if place_text:
            try:
                place_id = int(place_text)
            except ValueError as exc:
                raise ValueError('Place ID must be a whole number.') from exc
            if place_id <= 0:
                raise ValueError('Place ID must be positive.')
        name = request.name.strip() or (f'Place {place_id}' if place_id is not None else '')
        if not name:
            raise ValueError('Enter a preset name or place ID.')
        if not request.originals_source.strip() and not request.replacements_source.strip():
            raise ValueError('Choose an originals or replacements JSON source.')

        entry = CommunityPreset(
            preset_id='',
            name=name,
            place_id=place_id,
            credit=request.credit.strip(),
            originals_source=request.originals_source.strip(),
            replacements_source=request.replacements_source.strip(),
        )
        return [replace(entry, preset_id=_identifier('pending-custom', 0, entry))]

    def _materialize_source(
        self,
        source: str,
        destination_dir: Path,
        name: str,
        token: str,
        index: int,
        kind: str,
        created_files: list[Path],
    ) -> str:
        if not source:
            return ''
        raw = read_source_bytes(source, fetch=self._fetch)
        parse_json_document(raw, source=f'{name} {kind}')
        destination = destination_dir / (
            f'{safe_filename(name)}-{token[:10]}-{index + 1}-{kind}.json'
        )
        _atomic_write_bytes(destination, raw)
        created_files.append(destination)
        return str(destination)

    @staticmethod
    def _dump_document(entries: list[CommunityPreset]) -> dict[str, object]:
        games: dict[str, object] = {}
        for index, entry in enumerate(entries):
            key = entry.name if entry.name not in games else f'{entry.name} ({index + 1})'
            games[key] = entry.as_catalog_mapping()
        return {'games': games}


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    'CLOG_CACHE_FILE',
    'CUSTOM_DUMPS_DIR',
    'CatalogSnapshot',
    'CommunityPreset',
    'CommunityPresetCatalog',
    'CustomPresetRequest',
    'load_json_source',
    'normalize_catalog',
    'normalize_preset_entry',
    'parse_json_document',
    'read_source_bytes',
    'safe_filename',
]
