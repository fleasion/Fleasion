"""Resolve manually entered unprefixed FastFlags without changing stored names."""

from __future__ import annotations

import importlib
import json
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, cast

from fleasion.utils.json_types import as_object_dict
from fleasion.utils.paths import APP_CACHE_DIR, CONFIG_DIR, FFLAG_CATALOG_CACHE_FILENAME

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.modifications.fflag_static import ClientBinary, StaticFlagCatalog

CLIENT_FFLAG_PREFIXES: tuple[str, ...] = (
    'DFFlag',
    'DFInt',
    'DFLog',
    'DFString',
    'DFFloat',
    'FFlag',
    'FInt',
    'FLog',
    'FString',
    'FFloat',
)
_QUALIFIED_PREFIXES = (*CLIENT_FFLAG_PREFIXES, 'SFFlag', 'SFInt', 'SFLog', 'SFString', 'SFFloat')
_BROWSER_CACHE_VERSION = 3
FFLAG_CATALOG_CACHE_TTL_SECONDS = 24 * 60 * 60
_BROWSER_CACHE_PATH = APP_CACHE_DIR / FFLAG_CATALOG_CACHE_FILENAME

_SCAN_CACHE_DIR = CONFIG_DIR / 'fflag_scans'
_SELECTION_LOCK = threading.Lock()
_SELECTION_RECHECK_SECONDS = 2.0


@dataclass(slots=True)
class _SelectionState:
    deadline: float = 0.0
    binary: ClientBinary | None = None


_SELECTION = _SelectionState()


@dataclass(frozen=True, slots=True)
class InstalledNameEvidence:
    """Qualifiers and untyped registration names from the selected Player."""

    qualified: frozenset[str]
    verified: frozenset[str]
    raw_names: frozenset[str]


def _selected_player_binary() -> ClientBinary | None:
    """Avoid repeating expensive Linux client discovery for every proxy request."""
    with _SELECTION_LOCK:
        now = time.monotonic()
        if now >= _SELECTION.deadline:
            scanner = importlib.import_module('fleasion.modifications.fflag_static')
            detect = cast(
                'Callable[[], ClientBinary | None]', scanner.__dict__['detect_client_binary']
            )
            _SELECTION.binary = detect()
            _SELECTION.deadline = now + _SELECTION_RECHECK_SECONDS
        return _SELECTION.binary


def invalidate_selected_player_cache() -> None:
    """Recheck the chosen Player after a browser scan or client selection change."""
    with _SELECTION_LOCK:
        _SELECTION.deadline = 0.0


@lru_cache(maxsize=8)
def _read_installed_index(
    client: str,
    source: str,
    member: str | None,
    source_size: int,
    source_mtime_ns: int,
    *,
    scan_path: str,
    scan_size: int,
    scan_mtime_ns: int,
) -> InstalledNameEvidence | None:
    """Reparse an immutable saved scan only if its source or cache changes."""
    del scan_path, scan_size, scan_mtime_ns
    scanner = importlib.import_module('fleasion.modifications.fflag_static')
    load = cast('Callable[[str], StaticFlagCatalog | None]', scanner.__dict__['load_scan'])
    catalog = load(client)
    if catalog is None or (
        catalog.source != source
        or catalog.member != member
        or catalog.source_size != source_size
        or catalog.source_mtime_ns != source_mtime_ns
    ):
        return None
    return InstalledNameEvidence(
        qualified=frozenset(
            name for name in catalog.flags if full_prefix(name) in CLIENT_FFLAG_PREFIXES
        ),
        verified=frozenset(
            name for name in catalog.verified if full_prefix(name) in CLIENT_FFLAG_PREFIXES
        ),
        raw_names=frozenset(record.name for record in catalog.registrations),
    )


def installed_name_evidence(source: ClientBinary | None = None) -> InstalledNameEvidence | None:
    """Read the selected native client's existing scan without scanning or network access."""
    selected = source if source is not None else _selected_player_binary()
    if selected is None:
        return None
    try:
        binary_stat = selected.path.stat()
        cache = _SCAN_CACHE_DIR / f'{selected.client}.json'
        scan_stat = cache.stat()
    except OSError:
        return None
    return _read_installed_index(
        selected.client,
        str(selected.path),
        selected.member,
        binary_stat.st_size,
        binary_stat.st_mtime_ns,
        scan_path=str(cache),
        scan_size=scan_stat.st_size,
        scan_mtime_ns=scan_stat.st_mtime_ns,
    )


def full_prefix(name: str) -> str | None:
    """Return the prefix of a fully qualified client FastFlag, if present."""
    return next((p for p in _QUALIFIED_PREFIXES if name.startswith(p) and len(name) > len(p)), None)


@lru_cache(maxsize=4)
def _read_index(path: str, mtime_ns: int, size: int) -> frozenset[str] | None:
    del mtime_ns
    if size > 16_000_000:
        return None
    try:
        document: object = json.loads(Path(path).read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    mapping = as_object_dict(document)
    if mapping is None or mapping.get('version') != _BROWSER_CACHE_VERSION:
        return None
    fetched_at = mapping.get('fetched_at')
    if (
        not isinstance(fetched_at, int | float)
        or not 0 <= time.time() - fetched_at < FFLAG_CATALOG_CACHE_TTL_SECONDS
    ):
        return None
    flags = as_object_dict(mapping.get('flags'))
    if flags is None:
        return None
    return frozenset(
        name
        for name in flags
        if name.isascii() and name.isidentifier() and full_prefix(name) in CLIENT_FFLAG_PREFIXES
    )


def _public_typed_names(path: Path) -> frozenset[str] | None:
    """Read a fresh public inventory, preserving its independent 24-hour TTL."""
    try:
        stat = path.stat()
    except OSError:
        return None
    if not 0 <= time.time() - stat.st_mtime < FFLAG_CATALOG_CACHE_TTL_SECONDS:
        return None
    return _read_index(str(path), stat.st_mtime_ns, stat.st_size)


def known_typed_names(path: Path | None = None) -> frozenset[str] | None:
    """Combine public and selected-client qualifiers for UI and runtime resolution.

    Passing an explicit path retains the public-only cache interpretation for
    tests and offline callers. An absent public cache does not discard a saved
    native Player inventory, which has no time-based expiration.
    """
    public = _public_typed_names(path or _BROWSER_CACHE_PATH)
    if path is not None:
        return public
    installed = installed_name_evidence()
    if public is None and installed is None:
        return None
    public_names: frozenset[str] = public if public is not None else frozenset()
    installed_names: frozenset[str] = installed.qualified if installed is not None else frozenset()
    return public_names | installed_names


def known_untyped_names() -> frozenset[str]:
    """Names evidenced by native records, including those without a typed qualifier."""
    installed = installed_name_evidence()
    return installed.raw_names if installed is not None else frozenset()


def qualified_matches(name: str, known_names: frozenset[str] | None = None) -> tuple[str, ...]:
    """Resolve a literal full name, known suffix matches, or all candidates."""
    if full_prefix(name) or not name.isascii() or not name.isidentifier():
        return (name,)
    candidates = tuple(prefix + name for prefix in CLIENT_FFLAG_PREFIXES)
    matched = (
        tuple(candidate for candidate in candidates if candidate in known_names)
        if known_names
        else ()
    )
    return matched or candidates


def expand_unprefixed_fflags(
    flags: dict[str, str], known_names: frozenset[str] | None = None
) -> dict[str, str]:
    """Expand unprefixed names and let explicitly qualified names win collisions."""
    if not any(full_prefix(name) is None for name in flags):
        return dict(flags)
    names = known_names if known_names is not None else known_typed_names()
    expanded: dict[str, str] = {}
    for name, value in flags.items():
        if full_prefix(name):
            continue
        for match in qualified_matches(name, names):
            expanded[match] = value
    expanded.update((name, value) for name, value in flags.items() if full_prefix(name))
    return expanded
