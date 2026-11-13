"""Read Windows Player's on-disk FastVariable names and initialized slots.

FVariables names are optional, unverified *type hints* from another inventory.
This module never executes, patches, decrypts, or changes a Roblox binary.
Initialized storage is not a verified constructor or runtime default.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from fleasion.modifications.fflag_windows_values import (
    WindowsValueSlot,
    extract_windows_value_slots,
)

if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path

_MAX_PLAYER_SIZE = 320 * 1024 * 1024
_MAX_INVENTORY_SIZE = 16 * 1024 * 1024
_MAX_INVENTORY_LINES = 200_000
_FAMILIES = (
    'DFFlag',
    'DFInt',
    'DFString',
    'DFLog',
    'DFFloat',
    'FFlag',
    'FInt',
    'FString',
    'FLog',
    'FFloat',
)
_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_]{2,111}\Z')


@dataclass(frozen=True, slots=True)
class WindowsStaticFlagCatalog:
    """One immutable Player scan, without claiming original type verification."""

    source: Path
    sha256: str
    entries: tuple[WindowsValueSlot, ...]
    hint_source: Literal['fvariables', 'none']

    @property
    def hinted(self) -> int:
        """Number of records with unambiguous, interpretable type hints."""
        return sum(entry.scalar_kind != 'untyped' for entry in self.entries)


def parse_fvariables(payload: bytes) -> frozenset[str]:
    """Parse qualified names from the existing tracker FVariables format.

    Names from this public inventory are cross-build hints, not Windows types.
    """
    if len(payload) > _MAX_INVENTORY_SIZE:
        msg = 'FVariables inventory exceeds maximum permitted size'
        raise ValueError(msg)
    try:
        lines = payload.decode('utf-8-sig').splitlines()
    except UnicodeDecodeError as exc:
        msg = 'FVariables inventory is not UTF-8'
        raise ValueError(msg) from exc
    if len(lines) > _MAX_INVENTORY_LINES:
        msg = 'FVariables inventory contains too many entries'
        raise ValueError(msg)
    qualified: set[str] = set()
    for raw in lines:
        _, separator, tail = raw.partition('] ')
        name = (tail if separator else raw).strip()
        if not _NAME.fullmatch(name):
            continue
        if any(name.startswith(prefix) and len(name) > len(prefix) for prefix in _FAMILIES):
            qualified.add(name)
    return frozenset(qualified)


def scan_windows_player(
    path: Path,
    *,
    qualified_type_hints: Collection[str] = (),
    hint_source: Literal['fvariables', 'none'] = 'none',
) -> WindowsStaticFlagCatalog:
    """Scan the installed x64 Player EXE, checking for concurrent replacement.

    Parameters
    ----------
    path : Path
        Local Windows RobloxPlayerBeta.exe file, read only
    qualified_type_hints : Collection[str]
        Full names from FVariables, accepted only when unambiguous by suffix
    hint_source : Literal['fvariables', 'none']
        Provenance label for the type hints; never type verification

    Returns
    -------
    WindowsStaticFlagCatalog
        All coherent 24-byte Windows records and their raw storage values
    """
    before = path.stat()
    if not 64 <= before.st_size <= _MAX_PLAYER_SIZE:
        msg = 'Unsupported or oversized Windows Player executable'
        raise ValueError(msg)
    binary = path.read_bytes()
    after = path.stat()
    if (
        after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or len(binary) != before.st_size
    ):
        msg = 'Windows Player executable changed while scanning'
        raise ValueError(msg)
    slots = extract_windows_value_slots(binary, qualified_type_hints=qualified_type_hints)
    if not slots:
        msg = 'Unsupported Windows Player registration layout'
        raise ValueError(msg)
    return WindowsStaticFlagCatalog(
        source=path,
        sha256=hashlib.sha256(binary).hexdigest(),
        entries=slots,
        hint_source=hint_source if qualified_type_hints else 'none',
    )


def display_initial_value(slot: WindowsValueSlot) -> str:
    """Show typed guesses only when source hints and storage width agree."""
    if slot.scalar_kind == 'bool' and type(slot.scalar_hint) is bool:
        return 'True' if slot.scalar_hint else 'False'
    if slot.scalar_kind == 'int' and type(slot.scalar_hint) is int:
        return str(slot.scalar_hint)
    return f'0x{slot.raw_u64:016X}'


def selectable_qualified_name(slot: WindowsValueSlot) -> str | None:
    """Never emit an invented namespace for an untyped or invalid record."""
    if slot.scalar_kind not in {'bool', 'int'}:
        return None
    return slot.qualifier_hint
