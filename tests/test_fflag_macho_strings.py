"""ARM64 Mach-O C-string constructor proof, including adversarial negatives."""

from __future__ import annotations

import struct

from fleasion.modifications.fflag_macho_strings import extract_arm64_macho_string_initializers

_SECTION = tuple[str, str, int, int, int]
_TEXT = 0x100001000
_CSTRING = 0x100002000
_STORAGE = 0x100003020
_CTOR_PREFIX = (
    0xA9BC5FF8, 0xA90157F6, 0xA9024FF4, 0xA9037BFD,
    0x9100C3FD, 0xAA0103F5, 0xAA0003F3, 0xAA0103E0,
)


def _encode_pair(address: int, target: int, register: int) -> tuple[int, int]:
    pages = ((target >> 12) - (address >> 12)) & 0x1FFFFF
    return (
        0x90000000 | ((pages & 3) << 29) | ((pages >> 2) << 5) | register,
        0x91000000 | ((target & 4095) << 10) | (register << 5) | register,
    )


def _fixture() -> tuple[bytearray, list[_SECTION]]:
    data = bytearray(0x3000)
    sections: list[_SECTION] = [
        ('__TEXT', '__text', _TEXT, 0x400, 0x400),
        ('__TEXT', '__cstring', _CSTRING, 0x1000, 0x200),
        ('__DATA', '__common', 0x100003000, 0, 0x200),
    ]
    data[0x1020:0x1026] = b'Hello\0'
    data[0x1040:0x1044] = b'Bye\0'
    struct.pack_into('<8I', data, 0x400 + 0x100, *_CTOR_PREFIX)
    return data, sections


def _add_call(
    data: bytearray, offset: int, source: int, storage: int = _STORAGE,
    *, constructor: int = _TEXT + 0x100, register: int = 21,
) -> None:
    pc = _TEXT + offset
    instructions = (
        *_encode_pair(pc, storage, register),
        *_encode_pair(pc + 8, source, 1),
        0xAA0003E0 | (register << 16),  # MOV X0,Xregister
        0x94000000 | (((constructor - (pc + 20)) // 4) & 0x3FFFFFF),
    )
    struct.pack_into('<6I', data, 0x400 + offset, *instructions)


def test_arm64_macho_string_constructors_recover_empty_and_nonempty_literals() -> None:
    image, sections = _fixture()
    _add_call(image, 0x20, _CSTRING + 0x20)
    _add_call(image, 0x50, _CSTRING + 0x40, _STORAGE + 0x18, register=22)
    image[0x1080] = 0  # Explicit empty read-only C string
    _add_call(image, 0x80, _CSTRING + 0x80, _STORAGE + 0x30, register=19)
    assert extract_arm64_macho_string_initializers(
        bytes(image), sections, {_STORAGE, _STORAGE + 0x18, _STORAGE + 0x30},
    ) == {_STORAGE: 'Hello', _STORAGE + 0x18: 'Bye', _STORAGE + 0x30: ''}


def test_arm64_macho_string_rejects_other_calls_unverified_storage_and_invalid_utf8() -> None:
    image, sections = _fixture()
    _add_call(image, 0x20, _CSTRING + 0x20)
    assert extract_arm64_macho_string_initializers(
        bytes(image), sections, {_STORAGE + 0x18},
    ) == {}
    wrong_callee = bytearray(image)
    struct.pack_into('<I', wrong_callee, 0x400 + 0x100, 0xD503201F)
    assert extract_arm64_macho_string_initializers(
        bytes(wrong_callee), sections, {_STORAGE},
    ) == {}
    invalid_literal = bytearray(image)
    invalid_literal[0x1020:0x1026] = b'\xff\0\0\0\0\0'
    assert extract_arm64_macho_string_initializers(
        bytes(invalid_literal), sections, {_STORAGE},
    ) == {}
    incorrect_argument = bytearray(image)
    struct.pack_into('<I', incorrect_argument, 0x400 + 0x20 + 4 * 4, 0xAA1603E0)
    assert extract_arm64_macho_string_initializers(
        bytes(incorrect_argument), sections, {_STORAGE},
    ) == {}


def test_arm64_macho_string_rejects_conflicting_constructor_literals() -> None:
    image, sections = _fixture()
    _add_call(image, 0x20, _CSTRING + 0x20)
    _add_call(image, 0x50, _CSTRING + 0x40, _STORAGE)
    assert extract_arm64_macho_string_initializers(
        bytes(image), sections, {_STORAGE},
    ) == {}


def _reused_fixture() -> tuple[bytearray, list[_SECTION]]:
    image, sections = _fixture()
    # The string source is loaded once into callee-saved X22; many later
    # registrations may reuse it without changing X22.
    source = _encode_pair(_TEXT + 0x20, _CSTRING + 0x20, 22)
    struct.pack_into('<II', image, 0x400 + 0x20, *source)
    for offset in range(0x28, 0x80, 4):
        struct.pack_into('<I', image, 0x400 + offset, 0xD503201F)  # NOP
    call_at = _TEXT + 0x40
    struct.pack_into('<I', image, 0x400 + 0x40,
                     0x94000000 | (((_TEXT + 0x100 - call_at) // 4) & 0x03FFFFFF))
    site = _TEXT + 0x80
    instructions = (
        *_encode_pair(site, _STORAGE, 21),
        0xAA1503E0,  # MOV X0,X21 (storage)
        0xAA1603E1,  # MOV X1,X22 (C-string address)
        0x94000000 | (((_TEXT + 0x100 - (site + 16)) // 4) & 0x03FFFFFF),
    )
    struct.pack_into('<5I', image, 0x400 + 0x80, *instructions)
    return image, sections


def test_arm64_macho_string_callee_saved_source_requires_proven_linear_trace() -> None:
    image, sections = _reused_fixture()

    def extract(content: bytearray) -> dict[int, str]:
        return extract_arm64_macho_string_initializers(bytes(content), sections, {_STORAGE})

    assert extract(image) == {_STORAGE: 'Hello'}

    changed = bytearray(image)
    struct.pack_into('<I', changed, 0x400 + 0x30, 0xAA1F03F6)  # MOV X22,XZR
    assert extract(changed) == {}

    indirect_branch = bytearray(image)
    struct.pack_into('<I', indirect_branch, 0x400 + 0x34, 0xD61F0120)  # BR X9
    assert extract(indirect_branch) == {}

    skipped_seed = bytearray(image)
    struct.pack_into('<I', skipped_seed, 0x400 + 0x18,
                     0x54000000 | (5 << 5))  # B.EQ 0x2C (after source seed)
    assert extract(skipped_seed) == {}

    other_register = bytearray(image)
    struct.pack_into('<I', other_register, 0x400 + 0x20,
                     _encode_pair(_TEXT + 0x20, _CSTRING + 0x20, 23)[0])
    assert extract(other_register) == {}


def test_arm64_macho_reused_literal_cannot_override_conflicting_default() -> None:
    image, sections = _reused_fixture()
    _add_call(image, 0xB0, _CSTRING + 0x40)
    assert extract_arm64_macho_string_initializers(bytes(image), sections, {_STORAGE}) == {}


def test_arm64_macho_const_section_string_requires_verified_constructor() -> None:
    image, sections = _fixture()
    constant = 0x100004000
    writable = 0x100005000
    sections.extend((
        ('__TEXT', '__const', constant, 0x1800, 0x100),
        ('__DATA', '__data', writable, 0x1900, 0x100),
    ))
    image[0x1800:0x180a] = b'Immutable\0'
    image[0x1900:0x1909] = b'Writable\0'
    _add_call(image, 0x20, constant)
    _add_call(image, 0x50, writable, _STORAGE + 0x18)
    assert extract_arm64_macho_string_initializers(
        bytes(image), sections, {_STORAGE, _STORAGE + 0x18},
    ) == {_STORAGE: 'Immutable'}
