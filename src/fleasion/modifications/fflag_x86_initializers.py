"""Recover Android x86-64 compiled scalar initializers from Player constructors.

A registration thunk identifies the destination storage, but that storage is
usually zero-filled on disk. Accept a scalar only when a static constructor
writes a literal directly, or loads a literal into a register immediately
before writing to the storage, or carries that literal through a bounded
sequence of verified non-clobbering instructions, and independently references
the registration thunk shortly afterwards. Register-computed values and
unsupported instructions remain unknown. String constructors are separate.
"""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fleasion.modifications.fflag_elf_thunks import X86Registration

_Section = tuple[int, int, int]
_RIP_LEA = re.compile(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d](.{4})', re.DOTALL)
# Instruction lengths include the immediate, since RIP is relative to the end
_MOV_IMMEDIATE = (
    (re.compile(rb'\xc7\x05(.{4})(.{4})', re.DOTALL), 10, 4),
    (re.compile(rb'\x48\xc7\x05(.{4})(.{4})', re.DOTALL), 11, 4),
    (re.compile(rb'\xc6\x05(.{4})(.)', re.DOTALL), 7, 1),
    (re.compile(rb'\x66\xc7\x05(.{4})(.{2})', re.DOTALL), 9, 2),
)
_RIP_REGISTER_STORE = re.compile(
    rb'(\x66)?([\x40-\x4f]?)([\x88\x89])([\x05\x0d\x15\x1d\x25\x2d\x35\x3d])(.{4})',
    re.DOTALL,
)
_MAX_CONSTRUCTOR_DISTANCE = 128


def _adjacent_register_literal(
    code: memoryview, at: int, register: int, *, width: int, store_rex: bool,
) -> int | None:
    """Decode adjacent register constants without crossing intervening code."""
    lower = register & 7
    if not (width == 1 and 4 <= register <= 7 and not store_rex):
        for length in (2, 3):
            if at < length:
                continue
            start = at - length
            rex = code[start] if length == 3 and 0x40 <= code[start] <= 0x4F else None
            if length == 3 and rex is None:
                continue
            opcode = code[start + (1 if rex is not None else 0)]
            modrm = code[at - 1]
            if (
                opcode in {0x31, 0x33, 0x29, 0x2B}
                and modrm & 0xC0 == 0xC0
                and (modrm >> 3) & 7 == lower
                and modrm & 7 == lower
                and (register < 8 if rex is None else
                     bool(rex & 4) == (register >= 8) and bool(rex & 1) == (register >= 8))
            ):
                return 0
    if width == 2:
        if register < 8 and at >= 4 and code[at - 4] == 0x66 and code[at - 3] == 0xB8 + lower:
            return struct.unpack_from('<H', code, at - 2)[0]
        if (
            register >= 8 and at >= 5 and code[at - 5] == 0x66
            and code[at - 4] == 0x41 and code[at - 3] == 0xB8 + lower
        ):
            return struct.unpack_from('<H', code, at - 2)[0]
    if (
        register < 8 and at >= 5 and code[at - 5] == 0xB8 + lower
        and (at < 6 or not 0x40 <= code[at - 6] <= 0x4F)
    ):
        return struct.unpack_from('<I', code, at - 4)[0]
    if (
        register >= 8 and at >= 6
        and code[at - 6] == 0x41 and code[at - 5] == 0xB8 + lower
    ):
        return struct.unpack_from('<I', code, at - 4)[0]
    return None


# The sparse seed index makes 16K lookback affordable. In both tested Android
# x86-64 builds, 32K did not recover any additional verified initializers.
_TRACE_LOOKBACK_BYTES = 16384


def _constant_register_seed(  # ruff: ignore[too-many-return-statements]
    code: memoryview, at: int, register: int, store_width: int,
) -> tuple[int, int] | None:
    """Recognize a whole-register immediate or self-XOR before a store."""
    if at + 2 > len(code):
        return None
    low = register & 7
    prefix = code[at]
    if store_width == 1:
        # A byte store needs only a proven low byte, not a whole-register
        # initializer. Without REX, B4-B7 encode AH/CH/DH/BH rather than
        # SPL/BPL/SIL/DIL, and must never be mistaken for the low byte.
        if register < 4 and prefix == 0xB0 + low:
            return at + 2, code[at + 1]
        if (
            at + 3 <= len(code) and 0x40 <= prefix <= 0x4F
            and bool(prefix & 1) == (register >= 8)
            and code[at + 1] == 0xB0 + low
        ):
            return at + 3, code[at + 2]
    if prefix == 0x66 and at + 4 <= len(code):
        if register < 8 and code[at + 1] == 0xB8 + low and store_width <= 2:
            return at + 4, struct.unpack_from('<H', code, at + 2)[0]
        if (
            register >= 8 and at + 5 <= len(code)
            and code[at + 1] == 0x41 and code[at + 2] == 0xB8 + low
            and store_width <= 2
        ):
            return at + 5, struct.unpack_from('<H', code, at + 3)[0]
    previous_prefix = code[at - 1] if at else 0
    if (
        register < 8 and prefix == 0xB8 + low and at + 5 <= len(code)
        and previous_prefix != 0x66
        and not 0x40 <= previous_prefix <= 0x4F
    ):
        return at + 5, struct.unpack_from('<I', code, at + 1)[0]
    if (
        register >= 8 and prefix == 0x41 and at + 6 <= len(code)
        and code[at + 1] == 0xB8 + low
    ):
        return at + 6, struct.unpack_from('<I', code, at + 2)[0]
    # XOR reg,reg is a zeroing idiom, but do not accept xor reg,other
    offset = 1 if 0x40 <= prefix <= 0x4F else 0
    if at + offset + 2 <= len(code) and code[at + offset] in {0x31, 0x33}:
        rex = prefix if offset else 0
        modrm = code[at + offset + 1]
        reg_a = ((modrm >> 3) & 7) + (8 if rex & 4 else 0)
        reg_b = (modrm & 7) + (8 if rex & 1 else 0)
        if modrm & 0xC0 == 0xC0 and reg_a == reg_b == register:
            return at + offset + 2, 0
    return None


def _memory_end(code: memoryview, at: int, modrm: int) -> int | None:
    """Decode memory operand length, including SIB and displacement bytes.

    This checks framing only. Whether an instruction modifies the tracked
    register is checked separately; unknown opcodes always fail closed.
    """
    mode = modrm >> 6
    rm = modrm & 7
    if mode == 3:
        return None
    if rm == 4:  # x86 scale-index-base byte
        if at >= len(code):
            return None
        sib_base = code[at] & 7
        at += 1
        if mode == 0 and sib_base == 5:
            at += 4  # disp32 with no base
    elif mode == 0 and rm == 5:
        at += 4  # RIP-relative disp32
    if mode == 1:
        at += 1
    elif mode == 2:
        at += 4
    return at if at <= len(code) else None


def _direct_branch_edges(code: memoryview, at: int) -> tuple[int, ...] | None:
    """Return successors for a direct x86 branch; otherwise return None.

    Callers verify all branch targets stay inside their bounded region. This
    is a register-preservation proof, not a general-purpose disassembler.
    """
    op = code[at]
    if 0x70 <= op <= 0x7F and at + 2 <= len(code):
        end = at + 2
        return end, end + struct.unpack_from('<b', code, at + 1)[0]
    if op == 0x0F and at + 6 <= len(code) and 0x80 <= code[at + 1] <= 0x8F:
        end = at + 6
        return end, end + struct.unpack_from('<i', code, at + 2)[0]
    if op == 0xEB and at + 2 <= len(code):
        end = at + 2
        return (end + struct.unpack_from('<b', code, at + 1)[0],)
    if op == 0xE9 and at + 5 <= len(code):
        end = at + 5
        return (end + struct.unpack_from('<i', code, at + 1)[0],)
    return None


def _guarded_preserving_block(
    code: memoryview, start: int, target: int, register: int,
) -> bool:
    """Prove all paths through a bounded forward branch preserve one GPR.

    The taken edge skips this guarded region, while its fallthrough may
    contain nested conditional branches and an atomic retry loop. Explore
    both successors for every branch, including backward loop edges. Every
    reachable instruction must be fully decoded and preserve the tracked
    register; an escape from the region or unknown instruction rejects it.
    """
    if not start < target <= len(code) or target - start > 192:
        return False
    pending = [start]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if current == target or current in visited:
            continue
        if current < start or current >= target or len(visited) >= 128:
            return False
        visited.add(current)
        if (edges := _direct_branch_edges(code, current)) is not None:
            if any(edge < start or edge > target for edge in edges):
                return False
            pending.extend(edges)
            continue
        following = safe_x86_preserving_step(code, current, register, allow_guard=False)
        if following is None or following <= current or following > target:
            return False
        pending.append(following)
    return True


def safe_x86_preserving_step(  # ruff: ignore[too-many-return-statements]
    code: memoryview, at: int, source_register: int, *, allow_guard: bool = True,
) -> int | None:
    """Skip only understood straight-line instructions preserving a register."""
    if at >= len(code):
        return None
    operand16 = code[at] == 0x66
    if operand16:
        at += 1
    rex = code[at] if at < len(code) and 0x40 <= code[at] <= 0x4F else 0
    if rex:
        at += 1
    if at >= len(code):
        return None
    op = code[at]
    at += 1
    # A direct call preserves the System V ABI's callee-saved registers.
    # This may extend literal lifetimes for RBX/RBP/R12-R15, but never for
    # caller-saved RAX/RCX/RDX/RSI/RDI/R8-R11. A negative source is used
    # for instruction-boundary checks only.
    if not operand16 and not rex and op == 0xE8:
        return at + 4 if at + 4 <= len(code) and source_register in {
            -1, 3, 5, 12, 13, 14, 15,
        } else None
    if allow_guard and source_register >= 0 and not operand16 and not rex:
        # Jcc rel8 and Jcc rel32 are safe only when both paths reconverge
        if 0x70 <= op <= 0x7F and at < len(code):
            target = at + 1 + struct.unpack_from('<b', code, at)[0]
            if _guarded_preserving_block(code, at + 1, target, source_register):
                return target
        elif op == 0x0F and at + 4 < len(code) and 0x80 <= code[at] <= 0x8F:
            target = at + 5 + struct.unpack_from('<i', code, at + 1)[0]
            if _guarded_preserving_block(code, at + 5, target, source_register):
                return target
    # A LOCK CMPXCHG on memory updates the memory slot and the implicit
    # accumulator (EAX/RAX) on failure. Its explicit register operand is
    # only read; all other GPRs, including callee-saved defaults, survive.
    if not operand16 and not rex and op == 0xF0:
        lock_rex = code[at] if at < len(code) and 0x40 <= code[at] <= 0x4F else 0
        operand_at = at + int(bool(lock_rex))
        if (
            operand_at + 3 <= len(code)
            and code[operand_at] == 0x0F and code[operand_at + 1] == 0xB1
        ):
            modrm = code[operand_at + 2]
            # CMPXCHG memory writes the implicit accumulator on failure,
            # never the explicit register operand. REX extends the latter.
            if modrm >> 6 != 3 and source_register not in {0, -1}:
                return _memory_end(code, operand_at + 3, modrm)
        return None
    # Recognized SSE instructions operate only on XMM registers / a RIP-
    # relative memory slot, so they cannot change a general-purpose source.
    # Reject non-RIP and non-register memory forms instead of assuming an
    # instruction's length from an unvalidated ModR/M.
    if not operand16 and not rex and op == 0x0F and at + 1 < len(code):
        simd_op = code[at]
        modrm = code[at + 1]
        if simd_op in {0x10, 0x11, 0x28, 0x29, 0x57}:
            if modrm & 0xC0 == 0xC0:
                return at + 2
            return _memory_end(code, at + 2, modrm)
        return None
    reg_extension = 8 if rex & 4 else 0
    rm_extension = 8 if rex & 1 else 0
    if op == 0x90 and not operand16:
        return at
    if 0x50 <= op <= 0x57:
        return at  # push has no register destination
    if 0x58 <= op <= 0x5F:
        return None if op - 0x58 + rm_extension == source_register else at
    if 0xB0 <= op <= 0xB7:
        if at >= len(code):
            return None
        low = op - 0xB0
        # Without REX, encodings 4-7 mean AH/CH/DH/BH, not SIL/DIL.
        # With REX, all eight encodings select low-byte GPRs.
        destination = (
            low + rm_extension if rex or low < 4
            else low - 4
        )
        return at + 1 if destination != source_register else None
    if 0xB8 <= op <= 0xBF:
        width = 2 if operand16 else 8 if rex & 8 else 4
        dest = op - 0xB8 + rm_extension
        end = at + width
        return end if dest != source_register and end <= len(code) else None
    if op in {0x8D, 0x8B, 0x8A, 0x89, 0x88, 0xC6, 0xC7}:
        if at >= len(code):
            return None
        modrm = code[at]
        at += 1
        reg = ((modrm >> 3) & 7) + reg_extension
        rm = (modrm & 7) + rm_extension
        mod = modrm >> 6
        if mod != 3:
            end = _memory_end(code, at, modrm)
            if end is None:
                return None
            if op == 0xC6:
                end += 1
            if op == 0xC7:
                end += 2 if operand16 else 4
            writes = op in {0x8D, 0x8B, 0x8A}
            return end if end <= len(code) and (not writes or reg != source_register) else None
        if mod == 3 and op in {0x8B, 0x89, 0x8A, 0x88, 0xC7}:
            dest = reg if op in {0x8B, 0x8A} else rm
            if op == 0xC7:
                at += 2 if operand16 else 4
            return at if dest != source_register and at <= len(code) else None
        return None
    if op in {0x31, 0x33, 0x29, 0x2B, 0x01, 0x03, 0x09, 0x0B, 0x21, 0x23,
              0x39, 0x3B, 0x85}:
        if at >= len(code):
            return None
        modrm = code[at]
        if modrm >> 6 != 3:
            return None
        reg = ((modrm >> 3) & 7) + reg_extension
        rm = (modrm & 7) + rm_extension
        dest = reg if op in {0x33, 0x2B, 0x03, 0x0B, 0x23, 0x3B} else rm
        writes = op not in {0x39, 0x3B, 0x85}
        return at + 1 if not writes or dest != source_register else None
    # INC/DEC and shift/rotate operate on their explicit destination only.
    # They occur in atomic registration-index allocation loops.
    if op in {0xFE, 0xFF, 0xC0, 0xC1, 0xD0, 0xD1, 0xD2, 0xD3}:
        if at >= len(code):
            return None
        modrm = code[at]
        if modrm >> 6 != 3:
            return None  # a memory operation not needed for this proof
        subop = (modrm >> 3) & 7
        dest = (modrm & 7) + rm_extension
        if op in {0xFE, 0xFF} and subop not in {0, 1}:
            return None
        if op not in {0xFE, 0xFF} and subop == 6:
            return None
        end = at + 1 + int(op in {0xC0, 0xC1})
        return end if end <= len(code) and dest != source_register else None
    if op in {0x80, 0x81, 0x83}:
        if at >= len(code):
            return None
        modrm = code[at]
        subop = (modrm >> 3) & 7
        width = 1 if op in {0x80, 0x83} else 2 if operand16 else 4
        if modrm >> 6 != 3:
            if subop != 7:  # CMP on memory reads without modifying a GPR
                return None
            memory_end = _memory_end(code, at + 1, modrm)
            end = memory_end + width if memory_end is not None else len(code) + 1
            return end if end <= len(code) else None
        dest = (modrm & 7) + rm_extension
        end = at + 1 + width
        return end if end <= len(code) and (subop == 7 or dest != source_register) else None
    # No unknown instructions, indirect branches or calls may intervene
    return None


def _literal_after_complete_rip_store_ret(  # ruff: ignore[too-many-return-statements]
    code: memoryview, at: int,
) -> bool:
    """Prove a MOV imm32 starts after a completed RIP store and RET.

    A stand-alone static initializer may immediately follow the previous
    one's return without a function symbol. Check the entire instruction
    before RET, not the isolated C3 (which could occur in a displacement).
    Only use the boundary to establish instruction alignment; the actual
    default must still reach its typed storage through the normal trace.
    """
    if at < 7 or at >= len(code) or code[at - 1] != 0xC3:
        return False
    prefix = int(code[at] == 0x41)
    if at + prefix + 5 > len(code) or not 0xB8 <= code[at + prefix] <= 0xBF:
        return False
    for length in (7, 8, 11, 12):
        start = at - length
        if start < 0:
            continue
        if length == 7 and code[start] == 0x89 and code[start + 1] & 0xC7 == 0x05:
            return True  # MOV [RIP+disp32],r32; RET
        if (
            length == 8 and code[start] in {0x48, 0x4C}
            and code[start + 1] == 0x89 and code[start + 2] & 0xC7 == 0x05
        ):
            return True  # MOV [RIP+disp32],r64; RET
        if length == 11 and bytes(code[start:start + 2]) == b'\xC7\x05':
            return True  # MOV dword [RIP+disp32],imm32; RET
        if length == 12 and bytes(code[start:start + 3]) == b'\x48\xC7\x05':
            return True  # MOV qword [RIP+disp32],imm32; RET
    return False


def _seed_inside_instruction(  # ruff: ignore[too-many-return-statements]
    code: memoryview, at: int,
) -> bool:
    """Reject apparent opcodes embedded in a recognized previous instruction.

    A real immediate move can itself appear within the displacement of an
    *incorrectly aligned* candidate before it. When a canonical seven-byte
    RIP-relative instruction ends exactly at the seed, preserve that verified
    boundary rather than trusting an overlapping decode from its displacement.
    """
    if _literal_after_complete_rip_store_ret(code, at):
        return False
    # A canonical call/cleanup tail ending immediately before a full-width
    # XOR reg,reg is an instruction boundary, even if the call displacement
    # accidentally decodes as an overlapping opcode. This occurs at several
    # Android static initializer entrypoints. Keep this exception *only* for
    # a self-XOR: treating an arbitrary MOV immediate as aligned here has
    # produced spurious values in unrelated call displacements.
    if (
        at >= 6 and code[at - 5] == 0xE8
        and (
            code[at - 6] == 0xC3  # ret; call rel32; xor reg,reg
            or (at >= 8 and bytes(code[at - 8:at - 5]) in {
                b'\x48\x89\xc7',  # MOV RDI,RAX; CALL; XOR
                b'\x48\x89\x02',  # MOV [RDX],RAX; CALL; XOR
            })
        )
    ):
        rex = code[at] if at < len(code) and 0x40 <= code[at] <= 0x4F else 0
        op_at = at + int(bool(rex))
        if op_at + 1 < len(code) and code[op_at] in {0x31, 0x33}:
            modrm = code[op_at + 1]
            if (
                modrm & 0xC0 == 0xC0
                and ((modrm >> 3) & 7) + (8 if rex & 4 else 0)
                == (modrm & 7) + (8 if rex & 1 else 0)
            ):
                return False
    # A 16-bit logging level can be initialized directly after CALL rel32
    # or RET. Prove the boundary using a complete previous instruction
    # ending exactly at the call/return, not just an isolated E8/C3 byte:
    # either byte can also occur inside a displacement or immediate.
    if at + 4 <= len(code) and bytes(code[at:at + 2]) == b'\x66\xb8':
        for length, opcode in ((1, 0xC3), (5, 0xE8)):
            boundary = at - length
            if boundary < 1 or code[boundary] != opcode:
                continue
            if any(
                safe_x86_preserving_step(code, previous, -1) == boundary
                for previous in range(max(0, boundary - 11), boundary)
            ):
                return False
    # The loaded registration index and its derived +1 value form an
    # independently bounded 9-byte pair: MOV EAX,[RIP+disp32];
    # LEA EDX,[RAX+1]; XOR ECX,ECX. Do not mistake bytes in the MOV
    # displacement for an overlapping instruction covering the XOR seed.
    if (
        at >= 9 and bytes(code[at - 9:at - 7]) == b'\x8b\x05'
        and bytes(code[at - 3:at]) == b'\x8d\x50\x01'
        and bytes(code[at:at + 2]) == b'\x31\xc9'
    ):
        return False
    # A new constructor may begin immediately after an earlier one ends in
    # MOV [RIP+disp32],GPR; RET; XOR reg,reg. Both the preceding store and
    # RET are exact, contiguous instruction encodings; reject an isolated
    # RET-looking byte inside an immediate or displacement.
    if (
        at >= 8 and code[at - 1] == 0xC3
        and code[at - 8] in {0x48, 0x4C}
        and code[at - 7] == 0x89
        and code[at - 6] & 0xC7 == 0x05
    ):
        rex = code[at] if at < len(code) and 0x40 <= code[at] <= 0x4F else 0
        opcode_at = at + int(bool(rex))
        if opcode_at + 1 < len(code) and code[opcode_at] in {0x31, 0x33}:
            modrm = code[opcode_at + 1]
            if (
                modrm & 0xC0 == 0xC0
                and ((modrm >> 3) & 7) + (8 if rex & 4 else 0)
                == (modrm & 7) + (8 if rex & 1 else 0)
            ):
                return False
    # An actual 16-bit immediate follows a four-byte stack-slot spill in
    # Clang's FLog initializers. This boundary establishes the MOV AX seed
    # even if an overlapping decode starts in the preceding instruction.
    if (
        at >= 4 and bytes(code[at - 4:at]) == b'\x48\x89\x45\xe8'
        and bytes(code[at:at + 2]) == b'\x66\xb8'
    ):
        return False
    if (
        at >= 7 and code[at - 7] in {0x48, 0x4C}
        and code[at - 6] in {0x8D, 0x89, 0x8B}
        and code[at - 5] in {0x05, 0x0D, 0x15, 0x1D, 0x25, 0x2D, 0x35, 0x3D}
    ):
        return False
    for previous in range(max(0, at - 11), at):
        following = safe_x86_preserving_step(code, previous, -1)
        if following is not None and following > at:
            return True
    return False


def verified_x86_preserving_trace(
    code: memoryview, seed_start: int, seed_end: int, use: int, register: int,
) -> bool:
    """Prove a callee-saved register keeps its value from seed to use.

    A complete decode rejects unsupported control-flow transfers and writes
    to the source register. Nearby branches that can skip the seed are rejected;
    an apparent seed inside another instruction is not executable evidence.
    """
    if not 0 <= seed_start < seed_end <= use <= len(code):
        return False
    if register not in {3, 5, 12, 13, 14, 15}:
        return False
    if _seed_inside_instruction(code, seed_start):
        return False
    current = seed_end
    while current < use:
        following = safe_x86_preserving_step(code, current, register)
        if following is None or following <= current or following > use:
            return False
        current = following
    return current == use and not _seed_may_be_skipped(
        code, seed_start, use, register,
    )


def _candidate_seed_sites(
    data: bytes, code_offset: int, at: int, register: int, width: int,
) -> list[int]:
    """Index only possible literal opcodes using native bytes.find().

    A 16K all-byte scan on every matching FastFlag store is quadratic in the
    density of registrations. Searching the few relevant opcodes in C avoids
    interpreting every displacement byte in Python, without relaxing any
    instruction-boundary or clobber validation in the subsequent proof.
    """
    lower = register & 7
    modrm = 0xC0 + 9 * lower
    if register < 8:
        patterns = [bytes((0xB8 + lower,)), bytes((0x31, modrm)), bytes((0x33, modrm))]
        if width == 1:
            if register < 4:
                patterns.append(bytes((0xB0 + lower,)))
            patterns.extend(bytes((rex, 0xB0 + lower)) for rex in range(0x40, 0x50, 2))
        if width <= 2:
            patterns.append(bytes((0x66, 0xB8 + lower)))
    else:
        patterns = [bytes((0x41, 0xB8 + lower))]
        if width == 1:
            patterns.extend(bytes((rex, 0xB0 + lower)) for rex in range(0x41, 0x50, 2))
        for rex in (0x45, 0x4D):
            patterns.extend((bytes((rex, 0x31, modrm)), bytes((rex, 0x33, modrm))))
        if width <= 2:
            patterns.append(bytes((0x66, 0x41, 0xB8 + lower)))
    begin = code_offset + max(0, at - _TRACE_LOOKBACK_BYTES)
    end = code_offset + at
    sites: set[int] = set()
    for pattern in patterns:
        current = data.find(pattern, begin, end)
        while current >= 0:
            sites.add(current - code_offset)
            current = data.find(pattern, current + 1, end)
    return sorted(sites)


def _inside_rip_relative_operand(code: memoryview, at: int) -> bool:
    """Recognize displacement bytes of a complete RIP-relative GPR instruction.

    A random byte sequence inside a displacement can appear to be a POP or
    conditional jump. The backscan must not use it as a branch predecessor.
    Unlike broad overlapping-opcode detection, this requires the exact
    seven-byte REX/MOV-or-LEA/RIP-relative-operand encoding.
    """
    for site in range(max(0, at - 6), at):
        if (
            site + 7 <= len(code)
            and code[site] in {0x48, 0x4C}
            and code[site + 1] in {0x8D, 0x8B, 0x89}
            and code[site + 2] & 0xC7 == 0x05
            and site < at < site + 7
        ):
            return True
    return False


def _seed_may_be_skipped(code: memoryview, start: int, at: int, register: int) -> bool:
    """Reject a literal bypassed by corroborated nearby control flow.

    A jump-looking sequence *inside* a displacement is not a branch. Require
    an independently decodable predecessor ending at the candidate branch,
    and a verified non-clobbering path from its target to the storage write.
    This catches optional seed writes without poisoning most real constants.
    """
    for branch in range(max(0, start - 192), start):
        op = code[branch]
        if 0x70 <= op <= 0x7F or op == 0xEB:
            if branch + 2 > start:
                continue
            target = branch + 2 + struct.unpack_from('<b', code, branch + 1)[0]
        elif op == 0xE9 or (
            op == 0x0F and branch + 1 < start and 0x80 <= code[branch + 1] <= 0x8F
        ):
            length = 6 if op == 0x0F else 5
            if branch + length > start:
                continue
            target = branch + length + struct.unpack_from('<i', code, branch + length - 4)[0]
        else:
            continue
        if not start < target <= at:
            continue
        if not any(
            safe_x86_preserving_step(code, previous, register, allow_guard=False) == branch
            and not _inside_rip_relative_operand(code, previous)
            for previous in range(max(0, branch - 10), branch)
        ):
            continue
        cursor = target
        while cursor < at:
            next_at = safe_x86_preserving_step(code, cursor, register)
            if next_at is None or next_at > at:
                break
            cursor = next_at
        if cursor == at:
            return True
    return False


def _bounded_register_literal(
    data: bytes, code: memoryview, at: int, register: int, width: int, *, code_offset: int,
) -> int | None:
    """Trace a literal through recognized non-clobbering instructions only."""
    resolved: set[int] = set()
    for start in _candidate_seed_sites(data, code_offset, at, register, width):
        seed = _constant_register_seed(code, start, register, width)
        if seed is None:
            continue
        current, raw = seed
        while current < at:
            following = safe_x86_preserving_step(code, current, register)
            if following is None or following > at:
                break
            current = following
        if (
            current == at and not _seed_inside_instruction(code, start)
            and not _seed_may_be_skipped(code, start, at, register)
        ):
            resolved.add(raw)
            if len(resolved) > 1:
                return None
    return next(iter(resolved)) if len(resolved) == 1 else None


def _format_scalar(raw: int, width: int, type_id: int) -> str | None:
    if type_id == 4:
        return ('True' if raw else 'False') if raw in {0, 1} else None
    if type_id in {1, 3}:
        if width == 1 or (width == 2 and type_id == 3):
            return None
        if width == 2:
            return str(raw & 0xFFFF)
        if width == 4:
            return str(struct.unpack('<i', struct.pack('<I', raw & 0xFFFFFFFF))[0])
        return None
    return None


def extract_x86_compiled_initializers(
    data: bytes,
    sections: Mapping[str, _Section],
    registrations: Sequence[X86Registration],
) -> dict[tuple[str, int, int], str]:
    """Return verified immediate constructor values, never inferred defaults.

    Parameters
    ----------
    data : bytes
        Complete on-disk Player ELF image
    sections : Mapping[str, _Section]
        Section virtual address, file offset and size
    registrations : Sequence[X86Registration]
        Typed thunks with identified storage and code addresses

    Returns
    -------
    dict[tuple[str, int, int], str]
        Values keyed by unprefixed name, namespace and type ID
    """
    code = sections.get('.text')
    if code is None:
        return {}
    base, off, size = code
    if off + size > len(data):
        return {}
    thunk_by_address = {
        reg.thunk_va: reg for reg in registrations
        if reg.thunk_va is not None and reg.storage_va is not None
    }
    if not thunk_by_address:
        return {}
    code_bytes = memoryview(data)[off:off + size]
    references: dict[int, list[int]] = defaultdict(list)
    for match in _RIP_LEA.finditer(code_bytes):
        site = base + match.start()
        target = site + 7 + struct.unpack('<i', match.group(1))[0]
        if (registration := thunk_by_address.get(target)) is not None and registration.storage_va is not None:
            references[registration.storage_va].append(site)
    if not references:
        return {}

    writes: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
    for pattern, length, width in _MOV_IMMEDIATE:
        for match in pattern.finditer(code_bytes):
            site = base + match.start()
            dest = site + length + struct.unpack('<i', match.group(1))[0]
            if dest in references:
                raw = int.from_bytes(match.group(2), 'little')
                writes[dest].append((site, raw, width))

    for match in _RIP_REGISTER_STORE.finditer(code_bytes):
        site = base + match.start()
        destination = site + len(match.group(0)) + struct.unpack('<i', match.group(5))[0]
        if destination not in references:
            continue
        rex = match.group(2)[0] if match.group(2) else 0
        reg = ((match.group(4)[0] >> 3) & 7) + (8 if rex & 4 else 0)
        width = 1 if match.group(3) == b'\x88' else 2 if match.group(1) else 8 if rex & 8 else 4
        raw = _adjacent_register_literal(
            code_bytes, match.start(), reg,
            width=width, store_rex=bool(match.group(2)),
        )
        if (
            raw is None and width in {1, 2, 4}
            and not (width == 1 and 4 <= reg <= 7 and not match.group(2))
        ):
            raw = _bounded_register_literal(data, code_bytes, match.start(), reg, width, code_offset=off)
        if raw is not None:
            mask = (1 << (width * 8)) - 1
            writes[destination].append((site, raw & mask, width))

    recovered: dict[tuple[str, int, int], str] = {}
    for reg in registrations:
        if reg.storage_va is None or reg.thunk_va is None:
            continue
        # A nearby reference to THIS registration thunk anchors the write to
        # a constructor, not to an arbitrary runtime setter using that slot
        sites = references.get(reg.storage_va, ())
        candidates = {
            value
            for site, raw, width in writes.get(reg.storage_va, ())
            if any(0 < reference - site <= _MAX_CONSTRUCTOR_DISTANCE for reference in sites)
            if (value := _format_scalar(raw, width, reg.type_id)) is not None
        }
        if len(candidates) == 1:
            recovered[reg.name, reg.variant, reg.type_id] = next(iter(candidates))
    return recovered
