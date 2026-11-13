"""Static C-string constructor proof and rejection tests for Android Players."""

from __future__ import annotations

import struct

from fleasion.modifications.fflag_elf_strings import extract_elf_cstring_initializers


def _rip(source: int, instruction_length: int, destination: int) -> bytes:
    return struct.pack('<i', destination - source - instruction_length)


def _fixture() -> tuple[bytearray, dict[str, tuple[int, int, int]]]:
    data = bytearray(0x1000)
    sections = {
        '.text': (0x1000, 0x100, 0x200),
        '.rodata': (0x3000, 0x400, 0x200),
    }
    data[0x400:0x406] = b'Hello\0'
    data[0x420] = 0
    data[0x440:0x444] = b'Bye\0'
    data[0x280:0x280 + 21] = bytes.fromhex(
        '554889e5415741564154534989f74889fb4889f7e8'
    )
    ctor = 0x1180
    addr = 0x1020
    direct = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5000)
        + b'\x48\x8d\x35' + _rip(addr + 7, 7, 0x3000)
        + b'\xe8' + _rip(addr + 14, 5, ctor)
    )
    data[0x120:0x120 + len(direct)] = direct
    addr = 0x1060
    repeated = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5008)
        + b'\x48\x8d\x1d' + _rip(addr + 7, 7, 0x3020)
        + b'\x48\x89\xde'
        + b'\xe8' + _rip(addr + 17, 5, ctor)
        + b'\x48\x8d\x3d' + _rip(addr + 22, 7, 0x5010)
        + b'\x48\x89\xde'
        + b'\xe8' + _rip(addr + 32, 5, ctor)
    )
    data[0x160:0x160 + len(repeated)] = repeated
    addr = 0x10C0
    tail = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5018)
        + b'\x48\x8d\x35' + _rip(addr + 7, 7, 0x3040)
        + b'\x5d\xe9' + _rip(addr + 15, 5, ctor)
    )
    data[0x1C0:0x1C0 + len(tail)] = tail
    return data, sections


def test_elf_string_initializers_accept_confirmed_direct_reused_and_tail_literals() -> None:
    data, sections = _fixture()
    assert extract_elf_cstring_initializers(
        bytes(data), sections, {0x5000, 0x5008, 0x5010, 0x5018},
    ) == {0x5000: 'Hello', 0x5008: '', 0x5010: '', 0x5018: 'Bye'}


def test_elf_string_initializers_reject_non_constructor_and_unregistered_targets() -> None:
    data, sections = _fixture()
    data[0x280] ^= 0x01
    assert extract_elf_cstring_initializers(
        bytes(data), sections, {0x5000, 0x5008, 0x5010, 0x5018},
    ) == {}
    clean, sections = _fixture()
    assert extract_elf_cstring_initializers(bytes(clean), sections, {0xDEAD}) == {}


def test_elf_string_initializers_reject_invalid_utf8_and_conflicting_defaults() -> None:
    data, sections = _fixture()
    data[0x400:0x406] = b'\xff\x00\x00\x00\x00\x00'
    assert 0x5000 not in extract_elf_cstring_initializers(
        bytes(data), sections, {0x5000},
    )
    clean, sections = _fixture()
    addr = 0x10E0
    duplicate = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5000)
        + b'\x48\x8d\x35' + _rip(addr + 7, 7, 0x3040)
        + b'\xe8' + _rip(addr + 14, 5, 0x1180)
    )
    clean[0x1E0:0x1E0 + len(duplicate)] = duplicate
    assert 0x5000 not in extract_elf_cstring_initializers(
        bytes(clean), sections, {0x5000},
    )


def test_elf_string_initializer_traces_unrelated_metadata_stores_but_not_rbx_clobbers() -> None:
    data, sections = _fixture()
    # A constructor sequence may keep the literal in RBX across unrelated
    # registration metadata operations (as in the installed Android Players).
    addr = 0x1090
    shared = (
        b'\x48\x8d\x1d' + _rip(addr, 7, 0x3040)  # lea rbx, literal
        + b'\x48\x8d\x0d' + _rip(addr + 7, 7, 0x5030)  # lea rcx, metadata
        + b'\x48\x89\x0d' + _rip(addr + 14, 7, 0x5040)  # store rcx, unrelated
        + b'\x48\x8d\x3d' + _rip(addr + 21, 7, 0x5020)  # destination
        + b'\x48\x89\xde'  # mov rsi, rbx
        + b'\xe8' + _rip(addr + 31, 5, 0x1180)
    )
    assert len(shared) == 36
    data[0x190:0x190 + len(shared)] = shared
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5020}) == {
        0x5020: 'Bye',
    }
    clobbered = bytearray(data)
    # mov rbx, [rip+metadata] is a real RBX overwrite (not a literal seed).
    clobbered[0x190 + 7:0x190 + 10] = b'\x48\x8b\x1d'
    assert extract_elf_cstring_initializers(bytes(clobbered), sections, {0x5020}) == {}
    branching = bytearray(data)
    branching[0x190 + 14] = 0xE9  # an unknown control-flow transfer
    assert extract_elf_cstring_initializers(bytes(branching), sections, {0x5020}) == {}
    other_call = bytearray(data)
    other_call[0x190 + 31:0x190 + 36] = b'\xe8' + _rip(addr + 31, 5, 0x1100)
    assert extract_elf_cstring_initializers(bytes(other_call), sections, {0x5020}) == {}


def test_elf_string_initializer_accepts_verified_constructor_tail_with_stack_epilogue() -> None:
    data, sections = _fixture()
    addr = 0x1110
    tail = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5030)
        + b'\x48\x8d\x35' + _rip(addr + 7, 7, 0x3040)
        + b'\x48\x83\xc4\x08\x5b\x5d\xe9' + _rip(addr + 20, 5, 0x1180)
    )
    data[0x210:0x210 + len(tail)] = tail
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5030}) == {
        0x5030: 'Bye',
    }
    bad = bytearray(data)
    bad[0x210 + 20:0x210 + 25] = b'\xe9' + _rip(addr + 20, 5, 0x1100)
    assert extract_elf_cstring_initializers(bytes(bad), sections, {0x5030}) == {}


def test_elf_string_initializers_track_verified_r14_literal_construction() -> None:
    data, sections = _fixture()
    addr = 0x1120
    sequence = (
        b'\x48\x8d\x3d' + _rip(addr, 7, 0x5050)
        + b'\x4c\x8d\x35' + _rip(addr + 7, 7, 0x3040)
        + b'\x4c\x89\xf6'
        + b'\xe8' + _rip(addr + 17, 5, 0x1180)
        + b'\x48\x8d\x3d' + _rip(addr + 22, 7, 0x5058)
        + b'\x4c\x89\xf6'
        + b'\xe8' + _rip(addr + 32, 5, 0x1180)
    )
    data[0x220:0x220 + len(sequence)] = sequence
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5050, 0x5058}) == {
        0x5050: 'Bye', 0x5058: 'Bye',
    }
    changed = bytearray(data)
    changed[0x220 + 22:0x220 + 29] = (
        b'\x4c\x8d\x35' + _rip(addr + 22, 7, 0x3000)
    )
    assert 0x5058 not in extract_elf_cstring_initializers(
        bytes(changed), sections, {0x5058},
    )


def test_elf_string_initializer_accepts_verified_multi_pop_tail_epilogue() -> None:
    data, sections = _fixture()
    at = 0x1110
    tail = (
        b'\x48\x8d\x3d' + _rip(at, 7, 0x5060)
        + b'\x48\x8d\x35' + _rip(at + 7, 7, 0x3040)
        + b'\x48\x83\xc4\x20\x5b\x41\x5e\x5d\xe9'
        + _rip(at + 22, 5, 0x1180)
    )
    data[0x210:0x210 + len(tail)] = tail
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5060}) == {
        0x5060: 'Bye',
    }


def test_elf_string_initializers_accept_r14_destination_argument() -> None:
    data, sections = _fixture()
    addr = 0x1100
    code = (
        b'\x4c\x8d\x35' + _rip(addr, 7, 0x5070)
        + b'\x48\x8d\x35' + _rip(addr + 7, 7, 0x3040)
        + b'\x4c\x89\xf7'
        + b'\xe8' + _rip(addr + 17, 5, 0x1180)
    )
    data[0x200:0x200 + len(code)] = code
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5070}) == {
        0x5070: 'Bye',
    }
    data[0x200 + 14:0x200 + 17] = b'\x4c\x89\xf6'
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5070}) == {}


def test_macho_x86_string_constructor_accepts_callee_saved_destination_registers() -> None:
    data, sections = _fixture()
    for address, dest, insn, move, literal in (
        (0x1100, 0x5080, b'\x4c\x8d\x3d', b'\x4c\x89\xff', 0x3000),
        (0x1130, 0x5088, b'\x48\x8d\x1d', b'\x48\x89\xdf', 0x3040),
    ):
        code = (
            insn + _rip(address, 7, dest)
            + b'\x48\x8d\x35' + _rip(address + 7, 7, literal)
            + move
            + b'\xe8' + _rip(address + 17, 5, 0x1180)
        )
        data[0x100 + address - 0x1000:0x100 + address - 0x1000 + len(code)] = code
    assert extract_elf_cstring_initializers(bytes(data), sections, {0x5080, 0x5088}) == {
        0x5080: 'Hello', 0x5088: 'Bye',
    }
    bad_callee = bytearray(data)
    bad_callee[0x100 + 0x1100 - 0x1000 + 18:0x100 + 0x1100 - 0x1000 + 22] = _rip(
        0x1100 + 17, 5, 0x1150,
    )
    assert 0x5080 not in extract_elf_cstring_initializers(
        bytes(bad_callee), sections, {0x5080},
    )
    bad_register = bytearray(data)
    bad_register[0x100 + 0x1130 - 0x1000 + 14:0x100 + 0x1130 - 0x1000 + 17] = (
        b'\x48\x89\xde'  # mov rsi,rbx; not mov rdi,rbx
    )
    assert 0x5088 not in extract_elf_cstring_initializers(
        bytes(bad_register), sections, {0x5088},
    )
