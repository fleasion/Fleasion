"""Regression tests for static ARM64 macOS FastFlag constructor recovery."""

from __future__ import annotations

import os
import struct
from pathlib import Path

import pytest

from fleasion.modifications.fflag_macho_arm64 import extract_arm64_flag_registrations
from fleasion.modifications.fflag_static import ClientBinary, scan_binary


def _encode_pair(address: int, target: int, reg: int) -> tuple[int, int]:
    displacement = ((target >> 12) - (address >> 12)) & 0x1FFFFF
    adrp = 0x90000000 | ((displacement & 3) << 29) | ((displacement >> 2) << 5) | reg
    add = 0x91000000 | ((target & 4095) << 10) | (reg << 5) | reg
    return adrp, add


def _fixture() -> tuple[bytes, list[tuple[str, str, int, int, int]]]:
    binary = bytearray(0x4000)
    code_va = 0x100001000
    names_va = 0x100002000
    data_va = 0x100003000
    sections = [
        ('__TEXT', '__text', code_va, 0x400, 0x400),
        ('__TEXT', '__cstring', names_va, 0x1000, 0x200),
        ('__DATA', '__data', data_va, 0x1400, 0x200),
    ]

    def name(offset: int, value: str) -> int:
        raw = value.encode() + b'\0'
        binary[0x1000 + offset:0x1000 + offset + len(raw)] = raw
        return names_va + offset

    def instructions(at: int, values: list[int]) -> None:
        struct.pack_into('<' + 'I' * len(values), binary, 0x400 + at, *values)

    integer = name(0x30, 'CollisionScalingDefaultStudTenths')
    boolean = name(0x80, 'DebugDrawBroadPhaseAABBs')
    value_int = data_va + 0x20
    value_bool = data_va + 0x60

    pc = code_va + 0x20
    instructions(0x20, [
        *_encode_pair(pc, value_int, 8),
        0x52800000 | (67 << 5) | 9,  # mov w9, 67
        0xB9000000 | (8 << 5) | 9,  # str w9, [x8]
        *_encode_pair(pc + 16, integer, 9),
        0x52800000 | (len('CollisionScalingDefaultStudTenths') << 5) | 10,
        0xA9000000 | (1 << 15) | (10 << 10) | (8 << 5) | 9,
    ])

    pc = code_va + 0x80
    instructions(0x80, [
        *_encode_pair(pc, value_bool, 8),
        0x39000000 | (8 << 5) | 31,  # strb wzr, [x8]
        *_encode_pair(pc + 12, boolean, 9),
        0x52800000 | (len('DebugDrawBroadPhaseAABBs') << 5) | 10,
        0xA9000000 | (1 << 15) | (10 << 10) | (8 << 5) | 9,
    ])
    pc = code_va + 0xC0
    instructions(0xC0, [
        *_encode_pair(pc, boolean, 0),
        *_encode_pair(pc + 8, value_bool, 1),
        0x52800000 | (2 << 5) | 2,  # DF namespace
        0x14000000,  # branch to known-style registration getter
    ])
    return bytes(binary), sections


def test_arm64_constructor_values_and_getter_qualified_boolean() -> None:
    binary, sections = _fixture()
    actual = extract_arm64_flag_registrations(binary, sections)
    assert set(actual) == {
        'CollisionScalingDefaultStudTenths',
        'DebugDrawBroadPhaseAABBs',
    }
    integer = actual['CollisionScalingDefaultStudTenths']
    assert integer.compiled_u32 == 67
    assert integer.variant is None
    assert integer.boolean_storage is False
    boolean = actual['DebugDrawBroadPhaseAABBs']
    assert boolean.compiled_u32 == 0
    assert boolean.variant == 2
    assert boolean.boolean_storage is True
    assert boolean.storage_va == 0x100003060


def test_arm64_constructor_rejects_unmatched_length_without_losing_getter() -> None:
    binary, sections = _fixture()
    changed = bytearray(binary)
    # Replace the name's length in the boolean constructor with a wrong value
    offset = 0x400 + 0x80 + 6 * 4
    struct.pack_into('<I', changed, offset, 0x52800000 | (25 << 5) | 10)
    flags = extract_arm64_flag_registrations(bytes(changed), sections)
    assert flags['DebugDrawBroadPhaseAABBs'].compiled_u32 is None
    assert flags['DebugDrawBroadPhaseAABBs'].variant == 2


def test_local_arm64_player_if_fixture_supplied() -> None:
    path_str = os.environ.get('FLEASION_ARM64_PLAYER_PATH')
    if not path_str or not (path := Path(path_str)).is_file():
        pytest.skip('No independently supplied official ARM64 Player')
    qualified_hints = {
        'DFIntCollisionScalingDefaultStudTenths': None,
        'DFIntTaskSchedulerTargetFps': None,
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntS2PhysicsSenderRate': None,
    }
    catalog = scan_binary(ClientBinary('macos', 'macOS ARM64', path), qualified_hints)
    assert len(catalog.registrations) >= 12_000
    assert catalog.flags['DFIntCollisionScalingDefaultStudTenths'] == '67'
    assert catalog.flags['DFIntTaskSchedulerTargetFps'] == '0'
    assert catalog.flags['DFFlagDebugDrawBroadPhaseAABBs'] == 'False'
    assert catalog.flags['DFIntS2PhysicsSenderRate'] == '15'


def _relative_branch(pc: int, destination: int, *, link: bool = False) -> int:
    assert (destination - pc) % 4 == 0
    return (0x94000000 if link else 0x14000000) | (((destination - pc) // 4) & 0x03FFFFFF)


def _numeric_getter_chain() -> tuple[bytes, list[tuple[str, str, int, int, int]]]:
    original, sections = _fixture()
    image = bytearray(original)
    code_va = 0x100001000
    data_va = 0x100003000

    def emit(at: int, *instructions: int) -> None:
        struct.pack_into('<' + 'I' * len(instructions), image, 0x400 + at, *instructions)

    # The constructor passes a code pointer to the accessor as X2, rather
    # than including a type tag in the name descriptor.
    emit(0x40, *_encode_pair(code_va + 0x40, data_va + 0xA0, 0))
    emit(0x48, *_encode_pair(code_va + 0x48, code_va + 0x200, 2))
    emit(0x50, 0x52800061, _relative_branch(code_va + 0x54, code_va + 0x300, link=True))
    # Direct numeric getter forwards storage X0 and namespace W1.
    emit(0x200, *_encode_pair(code_va + 0x200, data_va + 0x20, 0))
    emit(0x208, 0x52800041, _relative_branch(code_va + 0x20C, code_va + 0x240))
    manager_page, _ = _encode_pair(code_va + 0x248, data_va + 0xA0, 8)
    emit(0x240, 0xAA0103E2, 0xAA0003E1, manager_page)
    # The helper fetches a flag-manager pointer, then tail calls the typed method.
    emit(0x24C, 0xF9400100 | (0xA0 // 8 << 10),
         _relative_branch(code_va + 0x250, code_va + 0x280))
    emit(0x280, 0xA9BF7BFD, 0x910003FD, 0x52800064,
         _relative_branch(code_va + 0x28C, code_va + 0x300, link=True),
         0x52800020, 0xA8C17BFD, 0xD65F03C0)
    return bytes(image), sections


def test_arm64_numeric_getter_is_qualified_only_by_validated_typed_helper() -> None:
    original, sections = _numeric_getter_chain()
    flags = extract_arm64_flag_registrations(original, sections)
    numeric = flags['CollisionScalingDefaultStudTenths']
    assert numeric.variant == 2
    assert numeric.type_id == 3
    assert numeric.compiled_u32 == 67
    assert not numeric.boolean_storage

    # The helper must agree on its type, and the getter on its storage.
    bad_helper = bytearray(original)
    struct.pack_into('<I', bad_helper, 0x400 + 0x288, 0x52800084)  # W4=4 (bool)
    assert extract_arm64_flag_registrations(bytes(bad_helper), sections)[
        'CollisionScalingDefaultStudTenths'
    ].type_id is None
    bad_storage = bytearray(original)
    pair = _encode_pair(0x100001200, 0x100003040, 0)
    struct.pack_into('<II', bad_storage, 0x400 + 0x200, *pair)
    assert extract_arm64_flag_registrations(bytes(bad_storage), sections)[
        'CollisionScalingDefaultStudTenths'
    ].type_id is None
    bad_call = bytearray(original)
    struct.pack_into('<I', bad_call, 0x400 + 0x54,
                     _relative_branch(0x100001054, 0x100001300))
    assert extract_arm64_flag_registrations(bytes(bad_call), sections)[
        'CollisionScalingDefaultStudTenths'
    ].type_id is None


def test_arm64_log_channel_has_typed_helper_and_halfword_compiled_level() -> None:
    original, sections = _fixture()
    image = bytearray(original)
    code_va, data_va, name_va = 0x100001000, 0x100003000, 0x1000020D0
    image[0x1000 + 0xD0:0x1000 + 0xD0 + len('ExampleLog') + 1] = b'ExampleLog\0'

    def emit(at: int, *instructions: int) -> None:
        struct.pack_into('<' + 'I' * len(instructions), image, 0x400 + at, *instructions)

    emit(0x140, *_encode_pair(code_va + 0x140, data_va + 0xC0, 8))
    emit(0x148, 0x52800000 | (774 << 5) | 9, 0x79000109)  # W9=774; STRH W9,[X8]
    emit(0x150, *_encode_pair(code_va + 0x150, name_va, 9))
    emit(0x158, 0xF9000509)  # STR X9,[X8,#8] - name pointer in log descriptor
    emit(0x15C, *_encode_pair(code_va + 0x15C, data_va + 0xA0, 0))
    emit(0x164, *_encode_pair(code_va + 0x164, code_va + 0x2C0, 2))
    emit(0x16C, 0x52800061, _relative_branch(code_va + 0x170, code_va + 0x3A0, link=True))
    emit(0x2C0, *_encode_pair(code_va + 0x2C0, name_va, 0))
    emit(0x2C8, *_encode_pair(code_va + 0x2C8, data_va + 0xC0, 1))
    emit(0x2D0, 0x52800022, _relative_branch(code_va + 0x2D4, code_va + 0x320))
    manager_page, _ = _encode_pair(code_va + 0x32C, data_va + 0xA0, 8)
    emit(0x320, 0xAA0203E3, 0xAA0103E2, 0xAA0003E1,
         manager_page, 0xF9400100 | (0xA0 // 8 << 10),
         _relative_branch(code_va + 0x334, code_va + 0x360))
    emit(0x360, 0xA9BF7BFD, 0x910003FD, 0x52800024,
         _relative_branch(code_va + 0x36C, code_va + 0x3A0, link=True),
         0x52800020, 0xA8C17BFD, 0xD65F03C0)
    regs = extract_arm64_flag_registrations(bytes(image), sections)
    log = regs['ExampleLog']
    assert log.variant == 1
    assert log.type_id == 1
    assert log.compiled_u32 == 774
    assert not log.boolean_storage


def test_arm64_numeric_two_instruction_movz_movk_default_is_verified() -> None:
    _original, sections = _fixture()
    data = bytearray(_original)
    code = 0x100001000
    value = 0x100003020
    name = 0x100002030
    # 0x001FA400 is constructed from two 16-bit halves before STR W9.
    program = [
        *_encode_pair(code + 0x20, value, 8),
        0x52800009 | (0xA400 << 5),  # MOVZ W9, #0xA400
        0x72A00009 | (0x001F << 5),  # MOVK W9, #0x1F, LSL #16
        0xB9000109,  # STR W9, [X8]
        *_encode_pair(code + 0x34, name, 9),
        0x5280000A | (len('CollisionScalingDefaultStudTenths') << 5),
        0xA9000000 | (1 << 15) | (10 << 10) | (8 << 5) | 9,
    ]
    struct.pack_into('<' + 'I' * len(program), data, 0x400 + 0x20, *program)
    found = extract_arm64_flag_registrations(bytes(data), sections)
    assert found['CollisionScalingDefaultStudTenths'].compiled_u32 == 0x001FA400

    # MOVK cannot be treated as a constant without the matching MOVZ seed.
    wrong_seed = bytearray(data)
    struct.pack_into('<I', wrong_seed, 0x400 + 0x28,
                     0x52800008 | (0xA400 << 5))  # MOVZ W8, not W9
    found = extract_arm64_flag_registrations(bytes(wrong_seed), sections)
    assert found['CollisionScalingDefaultStudTenths'].compiled_u32 is None

    wrong_half = bytearray(data)
    struct.pack_into('<I', wrong_half, 0x400 + 0x2C,
                     0x72800009 | (0x001F << 5))  # MOVK W9, LSL #0
    found = extract_arm64_flag_registrations(bytes(wrong_half), sections)
    assert found['CollisionScalingDefaultStudTenths'].compiled_u32 is None

    # A branch into MOVK skips the low-half MOVZ. The result must remain
    # unknown even though the two constant instructions are adjacent.
    bypassed = bytearray(data)
    struct.pack_into('<I', bypassed, 0x400 + 0x10,
                     0x54000000 | (7 << 5))  # B.EQ 0x2C (MOVK)
    found = extract_arm64_flag_registrations(bytes(bypassed), sections)
    assert found['CollisionScalingDefaultStudTenths'].compiled_u32 is None


def _arm64_callee_saved_scalar_fixture() -> tuple[bytes, list[tuple[str, str, int, int, int]]]:
    original, sections = _numeric_getter_chain()
    data = bytearray(original)
    base, storage, label = 0x100001000, 0x100003020, 0x100002030
    instructions = [
        0x52800C93,  # MOVZ W19, #100: constant is 4+ instructions from store
        _relative_branch(base + 0x14, base + 0x380, link=True),  # BL; preserves X19
        *_encode_pair(base + 0x18, storage, 8),
        0xB9000113,  # STR W19, [X8]
        *_encode_pair(base + 0x24, label, 9),
        0x5280000A | (len('CollisionScalingDefaultStudTenths') << 5),
        0xA9000000 | (1 << 15) | (10 << 10) | (8 << 5) | 9,
    ]
    struct.pack_into('<' + 'I' * len(instructions), data, 0x400 + 0x10, *instructions)
    # The seed at 0x10 is not a branch target by default.
    return bytes(data), sections


def test_arm64_callee_saved_scalar_tracing_rejects_clobbers_and_branch_bypasses() -> None:
    original, sections = _arm64_callee_saved_scalar_fixture()
    def value(data: bytes) -> int | None:
        return extract_arm64_flag_registrations(data, sections)[
            'CollisionScalingDefaultStudTenths'
        ].compiled_u32

    assert value(original) == 100

    # A sign-extending LDRSW writes X19 even though its opcode lacks the
    # normal LDR bit: treating it as a store would fabricate the constant.
    loading = bytearray(original)
    struct.pack_into('<I', loading, 0x400 + 0x14, 0xB9800113)
    assert value(bytes(loading)) is None

    # Store operations with pre-indexed addressing also modify their base.
    # Even if X19 is not one of the STP source registers, it is no longer
    # the same constant after the address writeback.
    changed_base = bytearray(original)
    struct.pack_into('<I', changed_base, 0x400 + 0x14, 0xA9812668)
    assert value(bytes(changed_base)) is None

    single_writeback = bytearray(original)
    struct.pack_into('<I', single_writeback, 0x400 + 0x14, 0xF8008E68)
    assert value(bytes(single_writeback)) is None

    # Calls preserve X19, not a caller-saved W9. An unrelated MOV must not
    # be retroactively attributed to the descriptor's compiled default.
    unsupported = bytearray(original)
    struct.pack_into('<I', unsupported, 0x400 + 0x14, 0xD61F0120)  # BR X9
    assert value(bytes(unsupported)) is None

    # An external conditional branch that enters after the MOVZ can bypass
    # the definition. The entire executable must be checked for this edge.
    skip_seed = bytearray(original)
    struct.pack_into('<I', skip_seed, 0x400 + 0x08, 0x54000000 | (3 << 5))
    assert value(bytes(skip_seed)) is None

    # Any intervening load of the tracked source invalidates this proof.
    overwritten = bytearray(original)
    struct.pack_into('<I', overwritten, 0x400 + 0x14, 0x52800013 | (7 << 5))
    assert value(bytes(overwritten)) == 7


def test_arm64_callee_saved_log_level_is_traced_through_unmodified_descriptors() -> None:
    original, sections = _fixture()
    data = bytearray(original)
    base, storage, label = 0x100001000, 0x1000030C0, 0x1000020D0
    data[0x1000 + 0xD0:0x1000 + 0xD0 + 11] = b'ExampleLog\0'

    def emit(at: int, *instructions: int) -> None:
        struct.pack_into('<' + 'I' * len(instructions), data, 0x400 + at, *instructions)

    emit(0x130, 0x52800013 | (774 << 5),
         0xD503201F, 0xD503201F, 0xD503201F)  # MOVZ W19; NOP padding
    emit(0x140, *_encode_pair(base + 0x140, storage, 8))
    emit(0x148, 0xD503201F, 0x79000113)  # NOP; STRH W19,[X8]
    emit(0x150, *_encode_pair(base + 0x150, label, 9))
    emit(0x158, 0xF9000509)  # STR X9,[X8,#8]
    emit(0x15C, *_encode_pair(base + 0x15C, 0x1000030A0, 0))
    emit(0x164, *_encode_pair(base + 0x164, base + 0x2C0, 2))
    emit(0x16C, 0x52800061, _relative_branch(base + 0x170, base + 0x3A0, link=True))
    emit(0x2C0, *_encode_pair(base + 0x2C0, label, 0))
    emit(0x2C8, *_encode_pair(base + 0x2C8, storage, 1))
    emit(0x2D0, 0x52800022, _relative_branch(base + 0x2D4, base + 0x320))
    manager_page, _ = _encode_pair(base + 0x32C, 0x1000030A0, 8)
    emit(0x320, 0xAA0203E3, 0xAA0103E2, 0xAA0003E1,
         manager_page, 0xF9400100 | (0xA0 // 8 << 10),
         _relative_branch(base + 0x334, base + 0x360))
    emit(0x360, 0xA9BF7BFD, 0x910003FD, 0x52800024,
         _relative_branch(base + 0x36C, base + 0x3A0, link=True),
         0x52800020, 0xA8C17BFD, 0xD65F03C0)
    result = extract_arm64_flag_registrations(bytes(data), sections)
    log = result['ExampleLog']
    assert (log.type_id, log.variant, log.compiled_u32) == (1, 1, 774)


def test_arm64_named_string_getter_qualifies_type_without_a_scalar_constructor() -> None:
    original, sections = _numeric_getter_chain()
    modified = bytearray(original)
    # The verified typed implementation now passes 2=string. The string
    # object's address is still in the accessor, but its initializer is
    # absent, so the type must be verified without inventing a default.
    struct.pack_into('<I', modified, 0x400 + 0x288, 0x52800044)
    registration = extract_arm64_flag_registrations(bytes(modified), sections)[
        'CollisionScalingDefaultStudTenths'
    ]
    assert registration.type_id == 2
    assert registration.variant == 2
    assert registration.storage_va == 0x100003020
