"""Recover ELF FastString defaults from statically confirmed C-string constructors.

The Android Player stores string objects in writable storage, usually BSS.
A read-only registration descriptor points at that object, not at its text.
Accept a default only when a recognized constructor receives the registered
storage address and an actual NUL-terminated literal from read-only data.
"""

from __future__ import annotations

import bisect
import re
import struct
from typing import TYPE_CHECKING

from fleasion.modifications.fflag_x86_initializers import safe_x86_preserving_step

if TYPE_CHECKING:
    from collections.abc import Mapping, Set as AbstractSet

_Section = tuple[int, int, int]
_DIRECT_CALL = re.compile(rb'\x48\x8d\x3d(.{4})\x48\x8d\x35(.{4})\xe8(.{4})', re.DOTALL)
# Some static initializers tail-call the same constructor after a canonical
# function epilogue. Registers used for arguments (RDI/RSI) survive these
# stack adjustments and pops; the constructor destination must still match.
_TAIL_EPILOGUE = (
    rb'(?:\x48\x83\xc4[\x08\x10\x18\x20\x28\x30\x38\x40])?'
    rb'(?:\x5b|\x5d|\x41[\x5c-\x5f]){0,6}'
)
_DIRECT_JUMP = re.compile(
    rb'\x48\x8d\x3d(.{4})\x48\x8d\x35(.{4})' + _TAIL_EPILOGUE + rb'\xe9(.{4})',
    re.DOTALL,
)
_RBX_CALL = re.compile(rb'\x48\x8d\x3d(.{4})\x48\x89\xde\xe8(.{4})', re.DOTALL)
_RBX_JUMP = re.compile(
    rb'\x48\x8d\x3d(.{4})\x48\x89\xde' + _TAIL_EPILOGUE + rb'\xe9(.{4})',
    re.DOTALL,
)
_RBX_LITERAL = re.compile(rb'\x48\x8d\x1d(.{4})', re.DOTALL)
_RBX_SEED_CALL = re.compile(
    rb'\x48\x8d\x3d(.{4})\x48\x8d\x1d(.{4})\x48\x89\xde\xe8(.{4})',
    re.DOTALL,
)
_R14_LITERAL = re.compile(rb'\x4c\x8d\x35(.{4})', re.DOTALL)
_R14_CALL = re.compile(rb'\x48\x8d\x3d(.{4})\x4c\x89\xf6\xe8(.{4})', re.DOTALL)
_R14_JUMP = re.compile(
    rb'\x48\x8d\x3d(.{4})\x4c\x89\xf6' + _TAIL_EPILOGUE + rb'\xe9(.{4})',
    re.DOTALL,
)
_R14_SEED_CALL = re.compile(
    rb'\x48\x8d\x3d(.{4})\x4c\x8d\x35(.{4})\x4c\x89\xf6\xe8(.{4})',
    re.DOTALL,
)
# LLVM/Clang may save the literal argument in R15 while constructing the
# current string. That extra MOV cannot invalidate the RSI argument.
_DIRECT_R15_SAVE = re.compile(
    rb'\x48\x8d\x3d(.{4})\x48\x8d\x35(.{4})\x49\x89\xf7\xe8(.{4})',
    re.DOTALL,
)
# A constructor may hold the registered destination in callee-saved R14
# and copy it into the RDI argument after loading a literal into RSI.
_R14_DEST_DIRECT = re.compile(
    rb'\x4c\x8d\x35(.{4})\x48\x8d\x35(.{4})\x4c\x89\xf7\xe8(.{4})',
    re.DOTALL,
)
# Mach-O libc++ registrations use callee-saved R15, RBX (or R14) for
# their static std::string destination; the exact literal and ctor are still
# checked, so these patterns are safe for both ELF and Mach-O images.
_R15_DEST_DIRECT = re.compile(
    rb'\x4c\x8d\x3d(.{4})\x48\x8d\x35(.{4})\x4c\x89\xff\xe8(.{4})',
    re.DOTALL,
)
_RBX_DEST_DIRECT = re.compile(
    rb'\x48\x8d\x1d(.{4})\x48\x8d\x35(.{4})\x48\x89\xdf\xe8(.{4})',
    re.DOTALL,
)
# push rbp; mov rbp,rsp; push r15,r14,r12,rbx;
# mov r15,rsi; mov rbx,rdi; mov rdi,rsi; call strlen
_CSTRING_CONSTRUCTOR_PREFIX = bytes.fromhex('554889e5415741564154534989f74889fb4889f7e8')
_MAX_LITERAL_SIZE = 8192
_MAX_RBX_TRACE = 2048


def extract_elf_cstring_initializers(
    data: bytes,
    sections: Mapping[str, _Section],
    storage_addresses: AbstractSet[int],
) -> dict[int, str]:
    """Return verified UTF-8 literals keyed by registered storage VA.

    Parameters
    ----------
    data : bytes
        On-disk ELF image
    sections : Mapping[str, tuple[int, int, int]]
        Named section virtual address, file offset and size
    storage_addresses : AbstractSet[int]
        Validated string-object storage pointers from ELF relocations

    Returns
    -------
    dict[int, str]
        Compiled literals with corroborated destination and constructor
    """
    code = sections.get('.text')
    literals = sections.get('.rodata')
    if not code or not literals or not storage_addresses:
        return {}
    code_va, code_off, code_size = code
    string_va, string_off, string_size = literals
    if code_off + code_size > len(data) or string_off + string_size > len(data):
        return {}
    code_end = code_off + code_size

    def is_constructor(target: int) -> bool:
        loc = code_off + target - code_va
        return (
            code_off <= loc <= code_end - len(_CSTRING_CONSTRUCTOR_PREFIX)
            and data[loc:loc + len(_CSTRING_CONSTRUCTOR_PREFIX)]
            == _CSTRING_CONSTRUCTOR_PREFIX
        )

    def literal_at(address: int) -> str | None:
        if not string_va <= address < string_va + string_size:
            return None
        offset = string_off + address - string_va
        end = data.find(b'\0', offset, min(offset + _MAX_LITERAL_SIZE + 1, string_off + string_size))
        if end < 0:
            return None
        try:
            return data[offset:end].decode('utf-8')
        except UnicodeDecodeError:
            return None

    recovered: dict[int, str] = {}
    ambiguous: set[int] = set()

    def accept(destination: int, initial: str | None) -> None:
        if initial is None:
            return
        if destination in recovered and recovered[destination] != initial:
            ambiguous.add(destination)
        else:
            recovered[destination] = initial

    for pattern in (
        _DIRECT_CALL, _DIRECT_JUMP, _DIRECT_R15_SAVE, _R14_DEST_DIRECT,
        _R15_DEST_DIRECT, _RBX_DEST_DIRECT,
    ):
        for match in pattern.finditer(data, code_off, code_end):
            site = code_va + match.start() - code_off
            destination = site + 7 + struct.unpack('<i', match.group(1))[0]
            if destination not in storage_addresses:
                continue
            source = site + 14 + struct.unpack('<i', match.group(2))[0]
            callee = site + len(match.group(0)) + struct.unpack('<i', match.group(3))[0]
            if is_constructor(callee):
                accept(destination, literal_at(source))

    # Some initializers load a literal into a callee-saved register and
    # construct several adjacent strings with the same default. Recover the
    # first destination and each successive destination only if a verified
    # straight-line trace preserves the particular register.
    code_bytes = memoryview(data)

    def preserved_register(start: int, stop: int, register: int) -> bool:
        """Allow only constructor calls and instructions preserving source."""
        at = start
        while at < stop:
            if data[at] == 0xE8 and at + 5 <= stop:
                target = code_va + at - code_off + 5 + struct.unpack_from('<i', data, at + 1)[0]
                if not is_constructor(target):
                    return False
                at += 5
                continue
            following = safe_x86_preserving_step(code_bytes, at, register)
            if following is None or following > stop:
                return False
            at = following
        return at == stop

    for register, literal_pattern, seed_pattern, call_patterns in (
        (3, _RBX_LITERAL, _RBX_SEED_CALL, (_RBX_CALL, _RBX_JUMP)),
        (14, _R14_LITERAL, _R14_SEED_CALL, (_R14_CALL, _R14_JUMP)),
    ):
        for match in seed_pattern.finditer(data, code_off, code_end):
            site = code_va + match.start() - code_off
            destination = site + 7 + struct.unpack('<i', match.group(1))[0]
            if destination not in storage_addresses:
                continue
            source = site + 14 + struct.unpack('<i', match.group(2))[0]
            callee = site + len(match.group(0)) + struct.unpack('<i', match.group(3))[0]
            if is_constructor(callee):
                accept(destination, literal_at(source))

        anchors: list[tuple[int, int]] = []
        for match in literal_pattern.finditer(data, code_off, code_end):
            site = code_va + match.start() - code_off
            source = site + 7 + struct.unpack('<i', match.group(1))[0]
            if string_va <= source < string_va + string_size:
                anchors.append((match.start(), source))
        anchor_sites = [site for site, _source in anchors]

        for pattern in call_patterns:
            for match in pattern.finditer(data, code_off, code_end):
                site = code_va + match.start() - code_off
                destination = site + 7 + struct.unpack('<i', match.group(1))[0]
                if destination not in storage_addresses:
                    continue
                callee = site + len(match.group(0)) + struct.unpack('<i', match.group(2))[0]
                if not is_constructor(callee):
                    continue
                last = bisect.bisect_left(anchor_sites, match.start()) - 1
                for index in range(last, -1, -1):
                    anchor, source = anchors[index]
                    if match.start() - anchor > _MAX_RBX_TRACE:
                        break
                    if preserved_register(anchor + 7, match.start(), register):
                        accept(destination, literal_at(source))
                        break

    return {address: value for address, value in recovered.items() if address not in ambiguous}
