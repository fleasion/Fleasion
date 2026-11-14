"""Statically recover ARM64 macOS FastFlag constructors and accessor names.

Roblox's ARM64 Player initializes many descriptors in code instead of storing
complete name/value records on disk. Restrict recognition to corroborated
AArch64 ADRP/ADD materializations, registration name stores, or accessor
branches; values are taken only from same-constructor literal stores.
"""

from __future__ import annotations

import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

_Section = tuple[str, str, int, int, int]
_MAX_RECORDS = 60_000
_NAME = re.compile(rb'(?<![A-Za-z0-9_])[A-Za-z_][A-Za-z0-9_]{2,111}\x00')
_ARM64_ADRP = 0x90000000
_ARM64_ADD = 0x91000000
_ARM64_STP = 0xA9000000
_ARM64_STRB = 0x39000000
_ARM64_STRW = 0xB9000000
_ARM64_B = 0x14000000


@dataclass(frozen=True, slots=True)
class Arm64FlagRegistration:
    """Qualified namespace and storage clues from the actual Player image."""

    name: str
    variant: int | None
    storage_va: int | None
    compiled_u32: int | None
    boolean_storage: bool
    type_id: int | None = None


@dataclass(frozen=True, slots=True)
class _Initializer:
    storage_va: int
    raw: int | None
    is_boolean: bool


def extract_arm64_flag_registrations(
    data: bytes, sections: Sequence[_Section]
) -> dict[str, Arm64FlagRegistration]:
    """Trace static ARM64 registrations without invoking any guest code.

    Parameters
    ----------
    data : bytes
        Complete ARM64 Mach-O image
    sections : Sequence[_Section]
        Segment, section, virtual address, file offset, and size tuples

    Returns
    -------
    dict[str, Arm64FlagRegistration]
        Names with optional independently corroborated namespace and scalar
    """
    text = next(
        (
            (va, off, size)
            for seg, sect, va, off, size in sections
            if (seg, sect) == ('__TEXT', '__text')
        ),
        None,
    )
    if text is None:
        return {}
    base, off, size = text
    if size < 24 or off + size > len(data):
        return {}
    writable = [(va, size) for seg, _sect, va, _off, size in sections if seg.startswith('__DATA')]
    names: dict[int, str] = {}
    for seg, sect, address, fileoff, length in sections:
        if seg != '__TEXT' or sect not in {'__cstring', '__const'}:
            continue
        for match in _NAME.finditer(data, fileoff, fileoff + length):
            names[address + match.start() - fileoff] = match.group()[:-1].decode('ascii')
    if not names or not writable:
        return {}

    code = memoryview(data)[off : off + size]
    count = size // 4

    def word(index: int) -> int:
        return struct.unpack_from('<I', code, index * 4)[0]

    def pair(index: int) -> tuple[int, int] | None:
        if index < 0 or index + 1 >= count:
            return None
        adrp, add = word(index), word(index + 1)
        if (
            adrp & 0x9F000000 != _ARM64_ADRP
            or add & 0xFF000000 != _ARM64_ADD
            or (adrp & 31) != (add & 31)
            or (add & 31) != ((add >> 5) & 31)
        ):
            return None
        # A64 page-relative displacement is a signed, shifted 21-bit number
        displacement = ((adrp >> 5) & 0x7FFFF) * 4 + ((adrp >> 29) & 3)
        if displacement & (1 << 20):
            displacement -= 1 << 21
        page = (base + index * 4) & ~4095
        offset = ((add >> 10) & 4095) << (12 if (add >> 22) & 1 else 0)
        return adrp & 31, page + (displacement << 12) + offset

    def literal(index: int, register: int) -> int | None:
        if index < 0:
            return None
        instruction = word(index)
        if instruction & 31 != register:
            return None
        hw = (instruction >> 21) & 3
        if hw > 1:
            return None
        value = ((instruction >> 5) & 0xFFFF) << (hw * 16)
        if instruction & 0x7F800000 == 0x52800000:  # MOVZ Wn, #imm
            return value
        if instruction & 0x7F800000 == 0x12800000:  # MOVN Wn, #imm
            return (~value) & 0xFFFFFFFF
        return None

    def scalar_literal(index: int, register: int) -> int | None:
        """Decode a scalar MOVZ or an adjacent MOVZ/MOVK 32-bit constant.

        No register reuse or implicit storage zero is assumed. A MOVK alone
        modifies only half a register; its previous low half must be proven.
        """
        immediate = literal(index, register)
        if immediate is not None:
            return immediate
        if index < 1:
            return None
        update = word(index)
        if (
            update & 0x7F800000 != 0x72800000  # MOVK Wn
            or update & 31 != register
            or (update >> 21) & 3 != 1  # high 16 bits
        ):
            return None
        low = word(index - 1)
        if (
            low & 0x7F800000 != 0x52800000  # MOVZ Wn
            or low & 31 != register
            or (low >> 21) & 3 != 0
        ):
            return None
        return ((update >> 5) & 0xFFFF) << 16 | ((low >> 5) & 0xFFFF)

    def store(index: int) -> tuple[int, int, bool] | None:
        instruction = word(index)
        opcode = instruction & 0xFFC00000
        if opcode not in {_ARM64_STRB, _ARM64_STRW}:
            return None
        unit = 1 if opcode == _ARM64_STRB else 4
        if ((instruction >> 10) & 4095) * unit != 0:
            return None
        return (instruction >> 5) & 31, instruction & 31, unit == 1

    def is_writable(address: int) -> bool:
        return any(va <= address < va + length for va, length in writable)

    def branch_target(index: int, *, link: bool = False) -> int | None:
        """Decode an in-image unconditional B/BL, not an indirect branch."""
        if not 0 <= index < count:
            return None
        instruction = word(index)
        if instruction & 0xFC000000 != (0x94000000 if link else _ARM64_B):
            return None
        displacement = instruction & 0x03FFFFFF
        if displacement & (1 << 25):
            displacement -= 1 << 26
        destination = index + displacement
        return destination if 0 <= destination < count else None

    def typed_getter(  # ruff: ignore[too-many-return-statements]
        getter_va: int,
        storage_va: int,
        name_va: int,
    ) -> tuple[int, int] | None:
        """Prove a getter's namespace and helper's literal type discriminator.

        The constructor passes the getter as x2, allowing us to recover the
        qualified type without external flag-name dictionaries. Both the
        address forwarded by the getter and the exact helper call chain must
        be corroborated. Unrecognized wrappers remain unqualified.
        """
        if getter_va & 3 or not base <= getter_va < base + size:
            return None
        getter = (getter_va - base) // 4
        first = pair(getter)
        if first is None or first[0] != 0:
            return None
        if first[1] == storage_va:
            variant = literal(getter + 2, 1)
            wrapper = branch_target(getter + 3)
            named = False
        elif first[1] == name_va:
            second = pair(getter + 2)
            if second is None or second != (1, storage_va):
                return None
            variant = literal(getter + 4, 2)
            wrapper = branch_target(getter + 5)
            named = True
        else:
            return None
        if variant not in {1, 2} or wrapper is None:
            return None
        # The ABI adapter forwards either two or three arguments into a
        # common typed implementation, loading the same flag-manager pointer.
        prefix = (0xAA0203E3, 0xAA0103E2, 0xAA0003E1) if named else (0xAA0103E2, 0xAA0003E1)
        if wrapper + len(prefix) + 2 >= count or any(
            word(wrapper + j) != expected for j, expected in enumerate(prefix)
        ):
            return None
        adrp, load = word(wrapper + len(prefix)), word(wrapper + len(prefix) + 1)
        if adrp & 0x9F00001F != 0x90000008 or load & 0xFFC003FF != 0xF9400100:
            return None
        implementation = branch_target(wrapper + len(prefix) + 2)
        if (
            implementation is None
            or implementation + 10 >= count
            or (word(implementation), word(implementation + 1)) != (0xA9BF7BFD, 0x910003FD)
        ):
            return None
        for j in range(implementation + 2, implementation + 7):
            type_id = literal(j, 4)
            if type_id not in {1, 2, 3, 4}:
                continue
            if branch_target(j + 1, link=True) is not None and (
                word(j + 2),
                word(j + 3),
                word(j + 4),
            ) == (0x52800020, 0xA8C17BFD, 0xD65F03C0):
                return variant, type_id
        return None

    def register_preserved(  # ruff: ignore[too-many-return-statements]
        instruction: int,
        source: int,
    ) -> bool:
        """Allow only AArch64 forms proven not to write a callee-saved GPR.

        Unknown instructions and control-flow transfers fail closed. BL/BLR
        preserve X19-X28 under the Darwin ARM64 calling convention.
        """
        if instruction in {0xD503201F, 0xD50320FF}:  # NOP, YIELD
            return True
        if instruction & 0xFC000000 == 0x94000000 or instruction & 0xFFFFFC1F == 0xD63F0000:
            return 19 <= source <= 28
        if (
            instruction & 0x7C000000 == 0x14000000  # B (not BL)
            or instruction & 0xFF000010 == 0x54000000  # B.cond
            or instruction & 0x7E000000 in {0x34000000, 0x36000000}  # CBZ/TBZ
        ):
            return False
        if instruction & 0x9F000000 in {_ARM64_ADRP, 0x10000000}:  # ADRP/ADR
            return instruction & 31 != source
        if instruction & 0x1F000000 == 0x11000000:  # ADD/SUB immediate
            return instruction & 31 != source
        if instruction & 0x1F800000 in {0x12800000, 0x12000000}:
            return instruction & 31 != source  # MOV wide / logical immediate
        if instruction & 0x1F000000 in {0x0A000000, 0x0B000000, 0x1B000000}:
            return instruction & 31 != source  # logical/add/multiply register
        if instruction & 0x3B200000 in {0x39000000, 0x38000000}:
            # Bit 22 alone misses sign-extending loads (LDRSW/LDRSB).
            # Every nonzero opc might write Rt; PRFM is conservatively a load.
            is_load = (instruction >> 22) & 3 != 0
            # Unscaled pre/post-indexed STR also writes its base Rn. The
            # scaled unsigned-offset form never writes back.
            writeback = instruction & 0x3B200000 == 0x38000000 and (instruction >> 10) & 3 in {1, 3}
            return not (
                (is_load and instruction & 31 == source)
                or (writeback and (instruction >> 5) & 31 == source)
            )
        if instruction & 0x3B000000 in {0x29000000, 0x28000000}:
            # STP/LDP pre/post-indexing can change its base even when both
            # data registers merely supply values for a store.
            writeback = (instruction >> 23) & 3 in {1, 3}
            return not (
                (
                    instruction & 0x00400000
                    and (instruction & 31 == source or (instruction >> 10) & 31 == source)
                )
                or (writeback and (instruction >> 5) & 31 == source)
            )
        if instruction & 0x1FE00000 == 0x1A800000:  # CSEL/CSINC
            return instruction & 31 != source
        return False

    def trace_constant(store_site: int, source: int) -> tuple[int, int] | None:
        """Find a prior immediate within one proven straight-line region."""
        if source not in range(19, 29):
            return None
        for index in range(store_site - 1, max(-1, store_site - 4097), -1):
            if (value := scalar_literal(index, source)) is not None:
                # MOVK's low half is defined by the preceding MOVZ. Its
                # address also has to remain reachable on every path.
                seed = index - 1 if word(index) & 0x7F800000 == 0x72800000 else index
                return value, seed
            if not register_preserved(word(index), source):
                return None
        return None

    pending_traces: list[tuple[str, _Initializer, _Initializer, int, int]] = []

    def accept_traced(
        name: str,
        storage: int,
        raw: int | None,
        source: int,
        store_site: int,
        *,
        boolean: bool = False,
        halfword: bool = False,
    ) -> _Initializer:
        """Keep unproven traces pending until all external branch targets are checked."""
        original = _Initializer(storage, raw, boolean)
        if raw is not None:
            # Even adjacent MOVZ/MOVK instructions can be entered midway
            # by a branch. Require the MOVZ seed to dominate the final STR.
            if (
                source != 31
                and store_site >= 2
                and word(store_site - 1) & 0x7F800000 == 0x72800000
                and word(store_site - 1) & 31 == source
            ):
                unproven = _Initializer(storage, None, boolean)
                pending_traces.append((name, unproven, original, store_site - 2, store_site))
                return unproven
            return original
        if source == 31:
            return original
        traced = trace_constant(store_site, source)
        if traced is None:
            return original
        value, seed = traced
        if boolean and value not in {0, 1}:
            return original
        replacement = _Initializer(storage, value & 0xFFFF if halfword else value, boolean)
        pending_traces.append((name, original, replacement, seed, store_site))
        return original

    def filter_branch_bypasses() -> None:
        """Reject any trace whose defining instruction can be skipped via B/BL/Jcc.

        Inspect the entire text segment for direct branches into the traced
        intervals, including branches originating *outside* the local window.
        A skipped seed is not a proven binary default.
        """
        if not pending_traces:
            return
        covered: set[int] = set()
        for _name, _before, _after, seed, store_site in pending_traces:
            covered.update(range(seed + 1, store_site + 1))
        alternate_entries: set[int] = set()
        for at in range(count):
            instruction = word(at)
            target: int | None = None
            if instruction & 0xFC000000 in {_ARM64_B, 0x94000000}:
                offset = instruction & 0x03FFFFFF
                target = at + (offset - (1 << 26) if offset & (1 << 25) else offset)
            elif instruction & 0xFF000010 == 0x54000000 or instruction & 0x7E000000 == 0x34000000:
                offset = (instruction >> 5) & 0x7FFFF
                target = at + (offset - (1 << 19) if offset & (1 << 18) else offset)
            elif instruction & 0x7E000000 == 0x36000000:
                offset = (instruction >> 5) & 0x3FFF
                target = at + (offset - (1 << 14) if offset & (1 << 13) else offset)
            if target is not None and target in covered:
                alternate_entries.add(target)
        for name, before, after, seed, store_site in pending_traces:
            if any(at in alternate_entries for at in range(seed + 1, store_site + 1)):
                continue
            values = constructors.get(name)
            if values is None:
                continue
            values.discard(before)
            values.add(after)

    getters: dict[str, set[tuple[int, int]]] = defaultdict(set)
    typed_getters: dict[str, set[tuple[int, int, int]]] = defaultdict(set)
    constructors: dict[str, set[_Initializer]] = defaultdict(set)

    def link_getter(name: str, name_va: int, storage: int, after: int) -> None:
        """Validate the registered x2 getter and its own typed helper chain."""
        for site in range(after + 2, min(after + 10, count - 3)):
            supplied = pair(site)
            if supplied is None or supplied[0] != 2:
                continue
            context = pair(site - 2)
            if context is None or context[0] != 0 or not is_writable(context[1]):
                continue
            if literal(site + 2, 1) != 3 or branch_target(site + 3, link=True) is None:
                continue
            if (proved := typed_getter(supplied[1], storage, name_va)) is None:
                continue
            variant, type_id = proved
            typed_getters[name].add((variant, storage, type_id))
            getters[name].add((variant, storage))
            break

    for i in range(2, count - 5):
        name_pair = pair(i)
        if name_pair is None or (name := names.get(name_pair[1])) is None:
            continue
        name_reg = name_pair[0]
        if name_reg == 0:
            storage_pair = pair(i + 2)
            if (
                storage_pair is not None
                and storage_pair[0] == 1
                and is_writable(storage_pair[1])
                and (variant := literal(i + 4, 2)) in {1, 2}
                and word(i + 5) & 0xFC000000 == _ARM64_B
            ):
                getters[name].add((variant, storage_pair[1]))
                # String objects often live in __DATA,__common (zero-fill),
                # with no file-backed name/value descriptor. Their accessor
                # independently forwards the exact name and object address
                # into a type=2 helper. This proves the qualified FString or
                # DFString name, never a compiled default of the object.
                proved = typed_getter(base + i * 4, storage_pair[1], name_pair[1])
                if proved == (variant, 2):
                    typed_getters[name].add((variant, storage_pair[1], 2))

        # Store a cstring pointer and length at +8 into the same descriptor
        # used by the scalar store, with either an existing or inline length
        stp: tuple[int, int] | None = None
        for distance in (2, 3):
            instruction = word(i + distance)
            if (
                instruction & 0xFFC00000 == _ARM64_STP
                and instruction & 31 == name_reg
                and ((instruction >> 15) & 127) * 8 == 8
                and (distance == 2 or literal(i + 2, (instruction >> 10) & 31) == len(name))
            ):
                stp = ((instruction >> 5) & 31, i + distance)
                break
        if stp is None:
            # Log channels use a 16-byte descriptor with the name pointer
            # written by STR Xn, [Xm,#8] and a separate STRH numeric level.
            # The independently registered getter proves the type, so this
            # shape must not be confused with a generic pointer assignment.
            pointer_write = next(
                (
                    (j, (w >> 5) & 31)
                    for distance in (2, 3)
                    for j in (i + distance,)
                    for w in (word(j),)
                    if w & 0xFFC00000 == 0xF9000000 and w & 31 == name_reg and (w >> 10) & 4095 == 1
                ),
                None,
            )
            if pointer_write is None:
                continue
            pointer_site, storage_reg = pointer_write
            storage = next(
                (
                    candidate[1]
                    for j in range(i - 1, max(i - 13, -1), -1)
                    if (candidate := pair(j)) is not None and candidate[0] == storage_reg
                ),
                None,
            )
            if storage is None or not is_writable(storage):
                continue
            link_getter(name, name_pair[1], storage, pointer_site)
            if not any(
                t in {1, 2} and address == storage
                for _variant, address, t in typed_getters.get(name, ())
            ):
                continue
            # This compiled immediate is a log-level value, not a string
            # pointer and not a fallback from a different Android build.
            raw: int | None = None
            level_store: int | None = None
            store_source = 31
            for j in range(i - 1, max(i - 6, -1), -1):
                candidate = word(j)
                if (
                    candidate & 0xFFC00000 == 0x79000000
                    and (candidate >> 5) & 31 == storage_reg
                    and (candidate >> 10) & 4095 == 0
                ):
                    store_source = candidate & 31
                    level_store = j
                    raw = scalar_literal(j - 1, store_source)
                    if raw is not None:
                        raw &= 0xFFFF  # STRH writes only the low halfword
                    break
            original = (
                accept_traced(name, storage, raw, store_source, level_store, halfword=True)
                if level_store is not None
                else _Initializer(storage, None, is_boolean=False)
            )
            constructors[name].add(original)
            if len(getters) + len(constructors) > _MAX_RECORDS:
                msg = 'Excessive ARM64 FastFlag registrations'
                raise ValueError(msg)
            continue
        storage_reg, stp_site = stp
        initializer_site = next(
            (
                j
                for j in range(i - 1, max(i - 12, -1), -1)
                if (stored := store(j)) is not None and stored[0] == storage_reg
            ),
            None,
        )
        if initializer_site is None:
            continue
        stored = store(initializer_site)
        if stored is None:
            continue
        storage = next(
            (
                candidate[1]
                for j in range(initializer_site - 2, max(initializer_site - 9, -1), -1)
                if (candidate := pair(j)) is not None and candidate[0] == storage_reg
            ),
            None,
        )
        if storage is None or not is_writable(storage):
            continue
        source = stored[1]
        raw = 0 if source == 31 else scalar_literal(initializer_site - 1, source)
        # Verify the exact typed getter before tracing any distant literals.
        # The existing immediate-only path stays available to inferred types.
        link_getter(name, name_pair[1], storage, stp_site)
        type_proven = any(
            address == storage and type_id in ({4} if stored[2] else {3})
            for _variant, address, type_id in typed_getters.get(name, ())
        )
        initial = (
            accept_traced(name, storage, raw, source, initializer_site, boolean=stored[2])
            if type_proven or raw is not None
            else _Initializer(storage, raw, stored[2])
        )
        constructors[name].add(initial)
        if len(getters) + len(constructors) > _MAX_RECORDS:
            msg = 'Excessive ARM64 FastFlag registrations'
            raise ValueError(msg)

    filter_branch_bypasses()

    results: dict[str, Arm64FlagRegistration] = {}
    for name in getters.keys() | constructors.keys():
        variants = {v for v, _storage in getters.get(name, ())}
        variant = next(iter(variants)) if len(variants) == 1 else None
        getter_storages = {storage for _v, storage in getters.get(name, ())}
        getter_storage = next(iter(getter_storages)) if len(getter_storages) == 1 else None
        initializers = constructors.get(name, set())
        if getter_storage is not None:
            initializers = {item for item in initializers if item.storage_va == getter_storage}
        known = {item.raw for item in initializers if item.raw is not None}
        compiled = next(iter(known)) if len(known) == 1 else None
        constructor_storages = {item.storage_va for item in initializers}
        storage = getter_storage or (
            next(iter(constructor_storages)) if len(constructor_storages) == 1 else None
        )
        boolean = bool(initializers) and all(item.is_boolean for item in initializers)
        candidate_types = {
            type_id
            for proposed_variant, proposed_storage, type_id in typed_getters.get(name, ())
            if proposed_variant == variant and proposed_storage == storage
        }
        type_id = next(iter(candidate_types)) if len(candidate_types) == 1 else None
        if (type_id == 4 and not boolean) or (type_id in {1, 3} and boolean):
            type_id = None
        results[name] = Arm64FlagRegistration(name, variant, storage, compiled, boolean, type_id)
    return results
