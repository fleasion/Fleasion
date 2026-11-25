"""Read Windows Player's static FastFlag value slots without claiming defaults.

Windows Player 0.745+ contains coherent 24-byte name/value/length records.
The first eight bytes are an initialized storage slot, not necessarily the
value used after C++ constructors and remote FastFlag configuration run.
A cross-client name/type hint can produce a *candidate* scalar interpretation,
but this never verifies the Windows namespace, type, or effective default.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from fleasion.modifications.fflag_pe import PeImage, inspect_pe

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

ScalarKind = Literal['bool', 'int', 'untyped']
_PREFIXES = (
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
_MAX_FLAGS = 60_000
_MAX_NAME_LEN = 112
_MIN_COHERENT_RUN = 24


@dataclass(frozen=True, slots=True)
class WindowsValueSlot:
    """Disk-initialized value slot with optional *inferred* type decoding."""

    name: str
    value_rva: int
    raw_u64: int
    qualifier_hint: str | None = None
    scalar_kind: ScalarKind = 'untyped'
    scalar_hint: bool | int | None = None


@dataclass(frozen=True, slots=True)
class _Candidate:
    name: str
    file_offset: int
    value_rva: int
    raw_u64: int


def _name_at(data: bytes, address: int, length: int, image: PeImage) -> str | None:
    if not 3 <= length <= _MAX_NAME_LEN:
        return None
    for section in image.sections:
        if (
            section.readable
            and not section.writable
            and not section.executable
            and section.virtual_address <= address < section.virtual_address + section.file_size
        ):
            pos = section.file_offset + address - section.virtual_address
            if pos + length >= section.file_offset + section.file_size:
                return None
            raw = data[pos : pos + length]
            if data[pos + length] != 0 or not (
                raw[0] == 95 or 65 <= raw[0] <= 90 or 97 <= raw[0] <= 122
            ):
                return None
            if not all(c == 95 or 48 <= c <= 57 or 65 <= c <= 90 or 97 <= c <= 122 for c in raw):
                return None
            return raw.decode('ascii')
    return None


def _candidates(data: bytes, image: PeImage) -> Iterator[_Candidate]:
    for section in image.sections:
        if not section.writable or section.executable or section.file_size < 24:
            continue
        low = section.file_offset
        high = min(len(data), low + section.file_size)
        for pointer_at in range((low + 8 + 7) & ~7, high - 16, 8):
            ptr, length = struct.unpack_from('<QQ', data, pointer_at)
            if length > _MAX_NAME_LEN or length < 3:
                continue
            name = _name_at(data, ptr, length, image)
            if name is None:
                continue
            value_at = pointer_at - 8
            rva = section.virtual_address - image.image_base + value_at - low
            yield _Candidate(name, value_at, rva, struct.unpack_from('<Q', data, value_at)[0])


def _qualifier_candidates(qualified_names: Collection[str]) -> dict[str, set[str]]:
    candidate_map: dict[str, set[str]] = {}
    for name in qualified_names:
        for prefix in _PREFIXES:
            if name.startswith(prefix):
                candidate_map.setdefault(name[len(prefix) :], set()).add(name)
                break
    return candidate_map


def _interpret(raw: int, qualifier: str | None) -> tuple[ScalarKind, bool | int | None]:
    if qualifier is None:
        return 'untyped', None
    if qualifier.startswith(('FFlag', 'DFFlag')):
        return ('bool', bool(raw)) if raw in {0, 1} else ('untyped', None)
    if qualifier.startswith(('FInt', 'DFInt')):
        # Int is 32-bit signed. Reject slots whose upper bytes are not
        # compatible with a zero-/sign-extended 32-bit initial value
        upper = raw >> 32
        if upper not in {0, 0xFFFFFFFF}:
            return 'untyped', None
        return 'int', struct.unpack('<i', struct.pack('<I', raw & 0xFFFFFFFF))[0]
    return 'untyped', None


def extract_windows_value_slots(
    data: bytes,
    *,
    qualified_type_hints: Collection[str] = (),
) -> tuple[WindowsValueSlot, ...]:
    """Return coherent read-only disk records, not verified runtime defaults.

    Parameters
    ----------
    data : bytes
        Installed x64 Windows Player executable bytes
    qualified_type_hints : Collection[str]
        Independently sourced fully qualified names; only an unambiguous name
        gets a type hint, never Windows-specific verification

    Returns
    -------
    tuple[WindowsValueSlot, ...]
        Only entries in a long, coherent, 24-byte registration table
    """
    image = inspect_pe(data)
    candidates = tuple(_candidates(data, image))
    indexed = {item.file_offset for item in candidates}
    longest = 0
    for at in indexed:
        if at - 24 in indexed:
            continue
        size = 1
        while at + 24 * size in indexed:
            size += 1
        longest = max(longest, size)
    if longest < _MIN_COHERENT_RUN:
        return ()
    hints = _qualifier_candidates(qualified_type_hints)
    result: list[WindowsValueSlot] = []
    for candidate in candidates:
        if candidate.file_offset - 24 not in indexed and candidate.file_offset + 24 not in indexed:
            continue
        possible = hints.get(candidate.name, set())
        qualifier = next(iter(possible)) if len(possible) == 1 else None
        kind, scalar = _interpret(candidate.raw_u64, qualifier)
        result.append(
            WindowsValueSlot(
                candidate.name,
                candidate.value_rva,
                candidate.raw_u64,
                qualifier,
                kind,
                scalar,
            )
        )
        if len(result) > _MAX_FLAGS:
            msg = 'Too many Windows static value slots'
            raise ValueError(msg)
    return tuple(result)
