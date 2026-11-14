"""Prove ARM64 Mach-O FastString defaults from calls to std::string(C-string).

A named getter identifies a string flag's exact storage virtual address.
The string object usually lives in a zero-fill Mach-O section and its default
is constructed during program initialization. Only accept a UTF-8 read-only
literal passed with that exact storage address to a recognizable C-string
constructor; never interpret zero-fill storage as an empty default.
"""

from __future__ import annotations

import struct
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence, Set as AbstractSet

_Section = tuple[str, str, int, int, int]
_MAX_LITERAL = 8192
# Verified libc++ basic_string(const char*) constructor, immediately before
# it invokes strlen. Branch/call displacements below this prefix vary by build.
_STRING_CTOR_PREFIX = (
    0xA9BC5FF8,
    0xA90157F6,
    0xA9024FF4,
    0xA9037BFD,
    0x9100C3FD,
    0xAA0103F5,
    0xAA0003F3,
    0xAA0103E0,
)


def extract_arm64_macho_string_initializers(
    data: bytes,
    sections: Sequence[_Section],
    storage_addresses: AbstractSet[int],
) -> dict[int, str]:
    """Return proven compiled strings indexed by verified accessor storage VA."""
    text = next(
        (
            (va, fileoff, size)
            for seg, sect, va, fileoff, size in sections
            if (seg, sect) == ('__TEXT', '__text')
        ),
        None,
    )
    # Some Clang C-string byte arrays live in immutable __const rather
    # than __cstring. The constructor and exact X1 pointer still provide
    # the necessary proof of string content and destination.
    literals = [
        (va, off, size)
        for seg, sect, va, off, size in sections
        if (seg, sect) in {('__TEXT', '__cstring'), ('__TEXT', '__const')}
        and off + size <= len(data)
    ]
    if not text or not literals or not storage_addresses:
        return {}
    base, fileoff, size = text
    if size < 24 or fileoff + size > len(data):
        return {}
    count = size // 4
    code = memoryview(data)[fileoff : fileoff + size]

    def word(index: int) -> int:
        return struct.unpack_from('<I', code, index * 4)[0]

    def pair(index: int) -> tuple[int, int] | None:
        if index < 0 or index + 1 >= count:
            return None
        adrp, add = word(index), word(index + 1)
        if (
            adrp & 0x9F000000 != 0x90000000
            or add & 0xFFC00000 != 0x91000000
            or adrp & 31 != add & 31
            or add & 31 != (add >> 5) & 31
        ):
            return None
        displacement = (((adrp >> 5) & 0x7FFFF) << 2) | ((adrp >> 29) & 3)
        if displacement & (1 << 20):
            displacement -= 1 << 21
        offset = (add >> 10) & 4095
        return adrp & 31, ((base + 4 * index) & ~4095) + (displacement << 12) + offset

    def is_constructor(callsite: int) -> bool:
        call = word(callsite)
        if call & 0xFC000000 != 0x94000000:
            return False
        relative = call & 0x03FFFFFF
        if relative & (1 << 25):
            relative -= 1 << 26
        destination = callsite + relative
        return 0 <= destination <= count - len(_STRING_CTOR_PREFIX) and all(
            word(destination + i) == instruction
            for i, instruction in enumerate(_STRING_CTOR_PREFIX)
        )

    def literal_at(address: int) -> str | None:
        for base_va, off, size in literals:
            if not base_va <= address < base_va + size:
                continue
            start = off + address - base_va
            end = data.find(b'\0', start, min(off + size, start + _MAX_LITERAL + 1))
            if end < 0:
                continue
            try:
                return data[start:end].decode('utf-8')
            except UnicodeDecodeError:
                return None
        return None

    def preserves(  # ruff: ignore[too-many-return-statements]
        instruction: int,
        source: int,
    ) -> bool:
        """Accept only instructions that cannot overwrite a callee-saved Xn.

        Unsupported or conditional control flow fails closed; imported BL
        calls preserve x19-x28 under the Darwin ARM64 ABI.
        """
        if instruction in {0xD503201F, 0xD50320FF}:  # NOP / YIELD
            return True
        if instruction & 0xFC000000 == 0x94000000:  # BL
            return True
        if (
            instruction & 0x7C000000 == 0x14000000  # B
            or instruction & 0xFF000010 == 0x54000000  # B.cond
            or instruction & 0x7E000000 in {0x34000000, 0x36000000}
        ):
            return False
        if instruction & 0x9F000000 in {0x90000000, 0x10000000}:  # ADRP/ADR
            return instruction & 31 != source
        if instruction & 0x1F000000 == 0x11000000:  # ADD/SUB immediate
            return instruction & 31 != source
        if instruction & 0x1F800000 in {0x12800000, 0x12000000}:
            return instruction & 31 != source
        if instruction & 0x1F000000 in {0x0A000000, 0x0B000000, 0x1B000000}:
            return instruction & 31 != source
        if instruction & 0x3B200000 in {0x39000000, 0x38000000}:
            is_load = (instruction >> 22) & 3 != 0
            writeback = instruction & 0x3B200000 == 0x38000000 and (instruction >> 10) & 3 in {1, 3}
            return not (
                (is_load and instruction & 31 == source)
                or (writeback and (instruction >> 5) & 31 == source)
            )
        if instruction & 0x3B000000 in {0x29000000, 0x28000000}:
            writeback = (instruction >> 23) & 3 in {1, 3}
            return not (
                (
                    instruction & 0x00400000
                    and (instruction & 31 == source or (instruction >> 10) & 31 == source)
                )
                or (writeback and (instruction >> 5) & 31 == source)
            )
        if instruction & 0x1FE00000 == 0x1A800000:
            return instruction & 31 != source
        return False

    def source_literal(use: int, register: int) -> tuple[int, str] | None:
        """Prove an ADRP/ADD literal seed across a bounded linear sequence."""
        if register not in range(19, 29):
            return None
        for current in range(use - 1, max(0, use - 4097), -1):
            # ADRP followed by ADD defines the full address. The first
            # instruction is the seed for bypass detection.
            if (seed := pair(current - 1)) is not None and seed[0] == register:
                if (value := literal_at(seed[1])) is not None:
                    return current - 1, value
                return None
            if not preserves(word(current), register):
                return None
        return None

    # A reused source register (often X21) may span several static flag
    # registrations. Keep proposals pending until direct branch targets from
    # *the entire* text section have been checked for skipped definitions.
    reuse: list[tuple[int, int, int, str]] = []
    candidates: dict[int, set[str]] = defaultdict(set)
    for i in range(count - 5):
        destination = pair(i)
        if destination is None or destination[1] not in storage_addresses:
            continue
        source = pair(i + 2)
        if source is None or source[0] != 1:
            continue
        # MOV X0, Xn; the destination object is passed as the first argument.
        if word(i + 4) != 0xAA0003E0 | (destination[0] << 16):
            continue
        if not is_constructor(i + 5):
            continue
        if (value := literal_at(source[1])) is not None:
            candidates[destination[1]].add(value)

    for i in range(count - 4):
        destination = pair(i)
        if destination is None or destination[1] not in storage_addresses:
            continue
        if word(i + 2) != 0xAA0003E0 | (destination[0] << 16):
            continue
        source_insn = word(i + 3)
        register = (source_insn >> 16) & 31
        if source_insn != 0xAA0003E1 | (register << 16):
            continue
        if not is_constructor(i + 4):
            continue
        traced = source_literal(i + 3, register)
        if traced is not None:
            seed, value = traced
            reuse.append((destination[1], seed, i + 4, value))

    if reuse:
        covered: set[int] = set()
        for _address, seed, end, _value in reuse:
            covered.update(range(seed + 1, end + 1))
        # Branches entering after a seed can bypass its definition even when
        # all locally decoded instructions preserve its value.
        alternate_entries: set[int] = set()
        for i in range(count):
            instruction = word(i)
            target: int | None = None
            if instruction & 0xFC000000 in {0x14000000, 0x94000000}:
                displacement = instruction & 0x03FFFFFF
                if displacement & (1 << 25):
                    displacement -= 1 << 26
                target = i + displacement
            elif instruction & 0xFF000010 == 0x54000000 or instruction & 0x7E000000 == 0x34000000:
                displacement = (instruction >> 5) & 0x7FFFF
                if displacement & (1 << 18):
                    displacement -= 1 << 19
                target = i + displacement
            elif instruction & 0x7E000000 == 0x36000000:
                displacement = (instruction >> 5) & 0x3FFF
                if displacement & (1 << 13):
                    displacement -= 1 << 14
                target = i + displacement
            if target is not None and target in covered:
                alternate_entries.add(target)
        for address, seed, end, value in reuse:
            if not any(i in alternate_entries for i in range(seed + 1, end + 1)):
                candidates[address].add(value)
    # Conflicting constructors are ambiguous even if all are real calls.
    return {
        address: next(iter(values)) for address, values in candidates.items() if len(values) == 1
    }
