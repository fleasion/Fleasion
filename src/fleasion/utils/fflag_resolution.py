"""Resolve manually entered unprefixed FastFlags without changing stored names."""

from __future__ import annotations

import json
import time
from functools import lru_cache
from pathlib import Path

from fleasion.utils.json_types import as_object_dict
from fleasion.utils.paths import APP_CACHE_DIR, FFLAG_CATALOG_CACHE_FILENAME

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


def known_typed_names(path: Path | None = None) -> frozenset[str] | None:
    """Return a recent public browser inventory, or None if none is available."""
    cache = path or _BROWSER_CACHE_PATH
    try:
        stat = cache.stat()
    except OSError:
        return None
    # Enforce expiry on every lookup, including memoized parsed snapshots
    if not 0 <= time.time() - stat.st_mtime < FFLAG_CATALOG_CACHE_TTL_SECONDS:
        return None
    return _read_index(str(cache), stat.st_mtime_ns, stat.st_size)


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
