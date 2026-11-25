"""Shared, short-lived view of /proc for Linux client detection.

Client detection walks every process several times per pass (once per client
family, plus pgrep forks). The table lists /proc once and memoizes the few
fields every walker filters on, so a pass reads each file at most once.
Walkers still read the remaining fields of the few matching processes
directly, so the identity checks themselves never use stale data.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

__all__ = ['ProcEntry', 'proc_entries', 'process_pids_by_name']

# Shorter than the 500 ms Roblox status poll, so every poll sees fresh processes
_TABLE_TTL_SECONDS = 0.25
_CMDLINE_HEAD_BYTES = 512
_SYSTEM_PROC_ROOT = Path('/proc')

_UNREAD = object()


class ProcEntry:
    """One numeric /proc directory with lazily read, memoized fields."""

    __slots__ = ('_cmdline_head', '_comm', '_uid', 'path', 'pid')

    def __init__(self, proc_root: Path, name: str) -> None:
        self.path = proc_root / name
        self.pid = int(name)
        self._comm: object = _UNREAD
        self._uid: object = _UNREAD
        self._cmdline_head: object = _UNREAD

    def comm(self) -> str | None:
        """Return the kernel process name, or None once the process is gone."""
        value = self._comm
        if value is _UNREAD:
            try:
                value = (self.path / 'comm').read_text(encoding='utf-8').strip()
            except OSError, UnicodeError:
                value = None
            self._comm = value
        return value if isinstance(value, str) else None

    def uid(self) -> int | None:
        """Return the owning user ID, or None once the process is gone."""
        value = self._uid
        if value is _UNREAD:
            try:
                value = self.path.stat().st_uid
            except OSError:
                value = None
            self._uid = value
        return value if isinstance(value, int) else None

    def cmdline_head(self) -> bytes | None:
        """Return the first bytes of argv, enough to identify argv[0]."""
        value = self._cmdline_head
        if value is _UNREAD:
            try:
                with (self.path / 'cmdline').open('rb') as stream:
                    value = stream.read(_CMDLINE_HEAD_BYTES)
            except OSError:
                value = None
            self._cmdline_head = value
        return value if isinstance(value, bytes) else None

    def owned_by_current_user(self) -> bool:
        return self.uid() == os.getuid()


class _Table:
    __slots__ = ('created', 'entries')

    def __init__(self, entries: tuple[ProcEntry, ...]) -> None:
        self.entries = entries
        self.created = time.monotonic()


_lock = threading.Lock()
_cached: _Table | None = None


def _read_table(proc_root: Path) -> tuple[ProcEntry, ...]:
    try:
        with os.scandir(proc_root) as listing:
            names = [entry.name for entry in listing if entry.name.isdecimal()]
    except OSError:
        return ()
    names.sort(key=int)
    return tuple(ProcEntry(proc_root, name) for name in names)


def proc_entries(proc_root: Path = _SYSTEM_PROC_ROOT) -> tuple[ProcEntry, ...]:
    """Return numeric /proc entries in PID order.

    Only the real /proc is shared between callers. Any other root (a test
    fixture or a mount namespace view) is listed fresh on every call.
    """
    global _cached  # ruff: ignore[global-statement]
    if proc_root != _SYSTEM_PROC_ROOT:
        return _read_table(proc_root)

    with _lock:
        table = _cached
        if table is not None and time.monotonic() - table.created < _TABLE_TTL_SECONDS:
            return table.entries

    # List outside the lock, a concurrent caller at worst lists twice
    entries = _read_table(proc_root)
    with _lock:
        _cached = _Table(entries)
    return entries


def process_pids_by_name(name: str, *, proc_root: Path = _SYSTEM_PROC_ROOT) -> list[int]:
    """Match ``pgrep -x name`` without forking.

    Like pgrep, this compares the kernel process name, which is at most 15
    characters, so longer names never match.
    """
    return [entry.pid for entry in proc_entries(proc_root) if entry.comm() == name]
