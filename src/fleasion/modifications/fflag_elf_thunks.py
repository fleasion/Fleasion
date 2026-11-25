"""Recover Android x86-64 Player FastVariable registrations from static thunks.

Some native Android clients initialize their registration names into .bss at
startup instead of publishing linker relocations to a descriptor table. This
scanner follows the immutable RIP-relative initializer instructions and typed
registration thunks without running or modifying the game binary.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

_Section = tuple[int, int, int]
_REGISTRATION_NAME = re.compile(rb'[A-Za-z_][A-Za-z0-9_]{1,111}\Z')

# RIP-relative lea/mov instructions produced by the native Android compiler
# Consecutive 7-byte instructions carry register values across interleaved
# writes: a name pointer need not be stored immediately after its lea
_RIP_INSTRUCTION = re.compile(
    rb'([\x48\x4c])([\x8d\x89])([\x05\x0d\x15\x1d\x25\x2d\x35\x3d])(.{4})',
    re.DOTALL,
)

# mov rdi, [global registry]; lea/mov rsi, name or name-storage;
# lea rdx, flag storage; mov ecx, F/DF; mov r8d, type; jmp helper
_TYPED_THUNK = re.compile(
    rb'\x48\x8b\x3d(.{4})\x48([\x8d\x8b])\x35(.{4})'
    rb'\x48\x8d\x15(.{4})\xb9(.{4})\x41\xb8(.{4})\xe9(.{4})',
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class X86Registration:
    """A name, namespace and type read directly from a registration thunk."""

    name: str
    variant: int
    type_id: int
    storage_va: int | None = field(default=None, compare=False)
    thunk_va: int | None = field(default=None, compare=False)


def _signed_displacement(value: bytes) -> int:
    return struct.unpack('<i', value)[0]


def extract_x86_registration_thunks(
    data: bytes, sections: Mapping[str, _Section]
) -> list[X86Registration]:
    """Recover qualified type evidence from x86-64 ELF registration thunks.

    Only read-only cstrings and data/BSS addresses backed by recognizable
    code patterns are accepted. A new compiler layout returns an empty list
    rather than guessed records.
    """
    code = sections.get('.text')
    strings = sections.get('.rodata')
    if code is None or strings is None:
        return []
    code_va, code_off, code_size = code
    string_va, string_off, string_size = strings
    writable = [sections[label] for label in ('.data', '.data.rel.ro', '.bss') if label in sections]
    if code_off + code_size > len(data) or string_off + string_size > len(data):
        return []

    def in_data(address: int) -> bool:
        return any(base <= address < base + size for base, _offset, size in writable)

    def in_code(address: int) -> bool:
        return code_va <= address < code_va + code_size

    def name_at(address: int) -> str | None:
        if not string_va <= address < string_va + string_size:
            return None
        pos = string_off + address - string_va
        end = data.find(b'\0', pos, min(pos + 113, string_off + string_size))
        if end < 0 or not _REGISTRATION_NAME.fullmatch(data[pos:end]):
            return None
        return data[pos:end].decode('ascii')

    initialized_names: dict[int, str] = {}
    ambiguous: set[int] = set()
    registers: dict[int, str] = {}
    previous_end = -1
    for match in _RIP_INSTRUCTION.finditer(data, code_off, code_off + code_size):
        # Only track registers across a consecutive run of fully recognized
        # instructions. Unknown intervening code might overwrite a register
        if match.start() != previous_end:
            registers.clear()
        previous_end = match.end()
        register = ((match.group(3)[0] >> 3) & 7) + (8 if match.group(1) == b'\x4c' else 0)
        start = code_va + match.start() - code_off
        target = start + 7 + _signed_displacement(match.group(4))
        if match.group(2) == b'\x8d':
            name = name_at(target)
            if name is None:
                registers.pop(register, None)
            else:
                registers[register] = name
        elif in_data(target) and (name := registers.get(register)) is not None:
            if target in initialized_names and initialized_names[target] != name:
                ambiguous.add(target)
            else:
                initialized_names[target] = name
    for destination in ambiguous:
        initialized_names.pop(destination, None)

    results: dict[tuple[str, int, int], X86Registration] = {}
    for match in _TYPED_THUNK.finditer(data, code_off, code_off + code_size):
        start = code_va + match.start() - code_off
        name_source = start + 14 + _signed_displacement(match.group(3))
        name = (
            name_at(name_source)
            if match.group(2) == b'\x8d'
            else initialized_names.get(name_source)
        )
        variant = struct.unpack('<I', match.group(5))[0]
        type_id = struct.unpack('<I', match.group(6))[0]
        storage = start + 21 + _signed_displacement(match.group(4))
        helper = start + 37 + _signed_displacement(match.group(7))
        if (
            name is None
            or variant not in {1, 2}
            or type_id not in {1, 2, 3, 4}
            or not in_data(storage)
            or not in_code(helper)
        ):
            continue
        key = (name, variant, type_id)
        results[key] = X86Registration(*key, storage, start)
    return list(results.values())
