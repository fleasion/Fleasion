"""Recover Mach-O x86-64 compiled string literals through exact constructor calls.

A typed string accessor or ELF registration thunk independently establishes
the object storage address. The same constructor signature applies to ELF.
Clang may retain that object in R12/R14/R15/RBX and load its C-string literal
into RSI or another callee-saved register. This decoder accepts only a verified
libc++ std::string(const char*) constructor and a read-only UTF-8 literal that
reaches its argument. It never reads the zero-fill string object's bytes.
"""

from __future__ import annotations

import bisect
import re
import struct
from collections import defaultdict
from typing import TYPE_CHECKING

from fleasion.modifications.fflag_x86_initializers import verified_x86_preserving_trace

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence, Set as AbstractSet

_Section = tuple[str, str, int, int, int]
_RIP_LEA = re.compile(
    rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d](.{4})',
    re.DOTALL,
)
# push rbp; mov rbp,rsp; push r15,r14,r12,rbx;
# mov r15,rsi; mov rbx,rdi; mov rdi,rsi; call strlen
_CONSTRUCTOR_PREFIX = bytes.fromhex('554889e5415741564154534989f74889fb4889f7e8')
_SOURCE_REGS = (3, 12, 13, 14, 15)
_MAX_SOURCE_LOOKBACK = 3000
_MAX_SEED_CANDIDATES = 15
_MAX_LITERAL_SIZE = 8192


def _lea_opcode(register: int) -> bytes:
    return bytes((0x48 if register < 8 else 0x4C, 0x8D, 0x05 | ((register & 7) << 3)))


def _mov_opcode(register: int, destination: int) -> bytes:
    return bytes((
        0x48 if register < 8 else 0x4C, 0x89,
        0xC0 | ((register & 7) << 3) | destination,
    ))


def extract_macho_x86_string_initializers(
    data: bytes, sections: Sequence[_Section], storage_addresses: AbstractSet[int],
) -> dict[int, str]:
    """Return values proven from the selected Mach-O's own code and literals."""
    text = next((
        (va, off, size) for seg, sect, va, off, size in sections
        if (seg, sect) == ('__TEXT', '__text')
    ), None)
    # Clang can emit literal byte arrays in __const as well as __cstring.
    # Accept either immutable section only when the pointer is actually
    # passed to the independently verified std::string(C-string) constructor.
    literals = [
        (va, off, size) for seg, sect, va, off, size in sections
        if (seg, sect) in {('__TEXT', '__cstring'), ('__TEXT', '__const')}
        and off + size <= len(data)
    ]
    if not text or not literals or not storage_addresses:
        return {}
    code_va, code_off, code_size = text
    code_end = code_off + code_size
    if code_end > len(data):
        return {}
    code = memoryview(data)[code_off:code_end]
    source_reg_by_move = {_mov_opcode(reg, 6): reg for reg in _SOURCE_REGS}
    reg_by_lea = {_lea_opcode(reg): reg for reg in range(16)}
    destination_moves = {reg: _mov_opcode(reg, 7) for reg in _SOURCE_REGS}

    def literal_at(address: int) -> str | None:
        for base_va, string_off, string_size in literals:
            if not base_va <= address < base_va + string_size:
                continue
            offset = string_off + address - base_va
            end = data.find(
                b'\0', offset, min(offset + _MAX_LITERAL_SIZE + 1, string_off + string_size),
            )
            if end < 0:
                return None
            try:
                return data[offset:end].decode('utf-8')
            except UnicodeDecodeError:
                return None
        return None

    def ctor_call(at: int) -> bool:
        if at + 5 > code_end or data[at] != 0xE8:
            return False
        callee = code_va + at - code_off + 5 + struct.unpack_from('<i', data, at + 1)[0]
        offset = code_off + callee - code_va
        return (
            code_off <= offset <= code_end - len(_CONSTRUCTOR_PREFIX)
            and data[offset:offset + len(_CONSTRUCTOR_PREFIX)] == _CONSTRUCTOR_PREFIX
        )

    # One native binary-wide scan indexes both candidate destination LEAs
    # and read-only literal seeds. Only references to verified, typed storage
    # addresses can become results.
    anchors: dict[int, list[tuple[int, int]]] = {reg: [] for reg in _SOURCE_REGS}
    destinations: list[tuple[int, int, int]] = []
    for match in _RIP_LEA.finditer(data, code_off, code_end):
        register = reg_by_lea.get(data[match.start():match.start() + 3])
        if register is None:
            continue
        address = (
            code_va + match.start() - code_off + 7
            + struct.unpack('<i', match.group(1))[0]
        )
        if register in anchors and any(
            base_va <= address < base_va + size for base_va, _off, size in literals
        ):
            anchors[register].append((match.start() - code_off, address))
        if address in storage_addresses and (register in destination_moves or register == 7):
            destinations.append((match.start(), register, address))
    anchor_sites = {reg: [site for site, _ in values] for reg, values in anchors.items()}

    def prior_literal(register: int, use: int) -> str | None:
        if register not in anchors:
            return None
        seeds = anchors[register]
        index = bisect.bisect_left(anchor_sites[register], use) - 1
        for candidate in range(index, max(-1, index - _MAX_SEED_CANDIDATES), -1):
            seed, address = seeds[candidate]
            if use - seed > _MAX_SOURCE_LOOKBACK:
                break
            if verified_x86_preserving_trace(code, seed, seed + 7, use, register):
                return literal_at(address)
        return None

    candidates: dict[int, set[str]] = defaultdict(set)
    for site, destination_register, storage in destinations:
        at = site + 7
        if at + 7 > code_end:
            # Never read a supposed source LEA across the __text boundary.
            continue
        source_register: int | None = None
        inline_literal: int | None = None
        # Optional literal LEA immediately following the destination LEA.
        candidate_register = reg_by_lea.get(data[at:at + 3])
        if candidate_register == 6 or candidate_register in source_reg_by_move.values():
            inline_literal = (
                code_va + at - code_off + 7
                + struct.unpack_from('<i', data, at + 3)[0]
            )
            source_register = candidate_register
            at += 7
        if destination_register != 7:
            if data[at:at + 3] != destination_moves[destination_register]:
                continue
            at += 3
        if source_register != 6:
            source_register = source_reg_by_move.get(data[at:at + 3])
            if source_register is None:
                continue
            at += 3
        if not ctor_call(at):
            continue
        value = (
            literal_at(inline_literal) if inline_literal is not None
            else prior_literal(source_register, site - code_off)
        )
        if value is not None:
            candidates[storage].add(value)
    # Multiple distinct constructor literals for the same object are
    # inherently ambiguous; never choose whichever happened to match first.
    return {address: next(iter(values)) for address, values in candidates.items()
            if len(values) == 1}


def extract_elf_x86_callee_saved_string_initializers(
    data: bytes, sections: Mapping[str, tuple[int, int, int]],
    storage_addresses: AbstractSet[int],
) -> dict[int, str]:
    """Use the shared x86-64 libc++ constructor proof for Android ELF.

    The same Clang-generated C-string constructor and calling convention
    occur in both Mach-O and ELF clients. This is a section-address adapter,
    not an estimate from another client's flag values. The implementation
    still proves the caller, literal and storage in the supplied ELF alone.
    """
    text = sections.get('.text')
    rodata = sections.get('.rodata')
    if text is None or rodata is None:
        return {}
    return extract_macho_x86_string_initializers(
        data,
        [('__TEXT', '__text', *text), ('__TEXT', '__cstring', *rodata)],
        storage_addresses,
    )
