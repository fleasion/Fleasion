"""x86-64 Mach-O compiled string constructor proof and rejection cases."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fleasion.modifications.fflag_macho_x86_strings import (
    extract_macho_x86_string_initializers,
)
from fleasion.modifications.fflag_static import ClientBinary, scan_binary

_CODE_VA = 0x1000
_CODE_OFF = 0x200
_CSTRING_VA = 0x3000
_CSTRING_OFF = 0x800
_STORAGE = 0x5000
_CTOR = _CODE_VA + 0x280
_CONSTRUCTOR_PREFIX = bytes.fromhex('554889e5415741564154534989f74889fb4889f7e8')
_Section = tuple[str, str, int, int, int]


def _lea(register: int, pc: int, target: int) -> bytes:
    return bytes((
        0x48 if register < 8 else 0x4C, 0x8D, 0x05 | ((register & 7) << 3),
    )) + struct.pack('<i', target - pc - 7)


def _mov(source: int, destination: int) -> bytes:
    return bytes((
        0x48 if source < 8 else 0x4C,
        0x89,
        0xC0 | ((source & 7) << 3) | destination,
    ))


def _call(pc: int, target: int = _CTOR) -> bytes:
    return b'\xe8' + struct.pack('<i', target - pc - 5)


def _fixture() -> tuple[bytearray, list[_Section]]:
    data = bytearray(0x1800)
    sections: list[_Section] = [
        ('__TEXT', '__text', _CODE_VA, _CODE_OFF, 0x400),
        ('__TEXT', '__cstring', _CSTRING_VA, _CSTRING_OFF, 0x200),
        ('__DATA', '__common', _STORAGE, 0, 0x200),
    ]
    data[_CSTRING_OFF + 0x20:_CSTRING_OFF + 0x26] = b'Hello\0'
    data[_CSTRING_OFF + 0x40:_CSTRING_OFF + 0x44] = b'Bye\0'
    data[_CSTRING_OFF + 0x60] = 0
    data[_CODE_OFF + 0x280:_CODE_OFF + 0x280 + len(_CONSTRUCTOR_PREFIX)] = (
        _CONSTRUCTOR_PREFIX
    )
    return data, sections


def _emit(image: bytearray, offset: int, code: bytes) -> None:
    image[_CODE_OFF + offset:_CODE_OFF + offset + len(code)] = code


def _direct(
    offset: int, storage: int, literal: int, *,
    dest_register: int = 12, source_register: int = 6,
) -> bytes:
    pc = _CODE_VA + offset
    initial = _lea(dest_register, pc, storage)
    source = _lea(source_register, pc + 7, literal)
    destination_move = _mov(dest_register, 7)
    source_move = b'' if source_register == 6 else _mov(source_register, 6)
    return (initial + source + destination_move + source_move
            + _call(pc + len(initial) + len(source) + len(destination_move) + len(source_move)))


def _reuse(offset: int, storage: int, *, dest: int = 12, src: int = 14) -> bytes:
    pc = _CODE_VA + offset
    body = _lea(dest, pc, storage) + _mov(dest, 7) + _mov(src, 6)
    return body + _call(pc + len(body))


def test_macho_x86_accepts_inline_direct_and_callee_saved_reuse() -> None:
    image, sections = _fixture()
    _emit(image, 0x20, _direct(0x20, _STORAGE, _CSTRING_VA + 0x20))
    inline = _direct(
        0x80, _STORAGE + 0x18, _CSTRING_VA + 0x40,
        dest_register=15, source_register=14,
    )
    _emit(image, 0x80, inline)
    intermediary = _call(_CODE_VA + 0x80 + len(inline), _CODE_VA + 0x1C0)
    _emit(image, 0x80 + len(inline), intermediary)
    reused = 0x80 + len(inline) + len(intermediary)
    _emit(image, reused, _reuse(reused, _STORAGE + 0x30, src=14))
    _emit(image, 0x180, _direct(0x180, _STORAGE + 0x48, _CSTRING_VA + 0x60))
    assert extract_macho_x86_string_initializers(
        bytes(image), sections, {_STORAGE + i for i in (0, 0x18, 0x30, 0x48)},
    ) == {
        _STORAGE: 'Hello',
        _STORAGE + 0x18: 'Bye',
        _STORAGE + 0x30: 'Bye',
        _STORAGE + 0x48: '',
    }


def test_macho_x86_refuses_unregistered_storage_and_invalid_constructor() -> None:
    image, sections = _fixture()
    _emit(image, 0x20, _direct(0x20, _STORAGE, _CSTRING_VA + 0x20))
    assert extract_macho_x86_string_initializers(
        bytes(image), sections, {_STORAGE + 0x18},
    ) == {}
    changed = bytearray(image)
    changed[_CODE_OFF + 0x280] ^= 1
    assert extract_macho_x86_string_initializers(
        bytes(changed), sections, {_STORAGE},
    ) == {}


def test_macho_x86_refuses_invalid_literal_and_conflicting_defaults() -> None:
    image, sections = _fixture()
    _emit(image, 0x20, _direct(0x20, _STORAGE, _CSTRING_VA + 0x20))
    bad_utf8 = bytearray(image)
    bad_utf8[_CSTRING_OFF + 0x20] = 0xFF
    assert extract_macho_x86_string_initializers(
        bytes(bad_utf8), sections, {_STORAGE},
    ) == {}
    conflicting = bytearray(image)
    _emit(conflicting, 0x80, _direct(0x80, _STORAGE, _CSTRING_VA + 0x40))
    assert extract_macho_x86_string_initializers(
        bytes(conflicting), sections, {_STORAGE},
    ) == {}


def test_macho_x86_reused_source_rejects_intervening_overwrites_and_branches() -> None:
    image, sections = _fixture()
    _emit(image, 0x30, _lea(15, _CODE_VA + 0x30, _CSTRING_VA + 0x20))
    _emit(image, 0x37, b'\x90' * (0x80 - 0x37))
    _emit(image, 0x80, _reuse(0x80, _STORAGE, src=15))

    def extract(binary: bytearray) -> dict[int, str]:
        return extract_macho_x86_string_initializers(bytes(binary), sections, {_STORAGE})

    assert extract(image) == {_STORAGE: 'Hello'}
    overwritten = bytearray(image)
    _emit(overwritten, 0x50, b'\x4d\x89\xe7')  # MOV R12,R15 clobbers the source.
    assert extract(overwritten) == {}
    different_callee = bytearray(image)
    _emit(different_callee, 0x8D, _call(_CODE_VA + 0x8D, _CODE_VA + 0x1C0))
    assert extract(different_callee) == {}
    skipped = bytearray(image)
    _emit(skipped, 0x2D, b'\x90\x75\x50')  # JNZ from 0x2E to 0x80
    # The false-path literal source is initialized at 0x30; a branch may
    # jump directly into the use at 0x80 and bypass its assignment.
    assert extract(skipped) == {}


def test_real_macho_x86_string_coverage_if_installed() -> None:
    path = Path.home() / (
        '.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/'
        'Roblox.app/Contents/MacOS/RobloxPlayer'
    )
    if not path.is_file():
        pytest.skip('No installed reference Mach-O x86-64 Roblox Player')
    result = scan_binary(ClientBinary('macos', 'macOS x86-64', path))
    values = {name: value for name, value in result.flags.items()
              if name.startswith(('FString', 'DFString'))}
    assert len(values) >= 750
    assert sum(value is not None for value in values.values()) >= 740
    assert values['FStringMacMenuBarLayerName'] == 'ConsumerPlatforms.MacMenuBar'
    assert values['DFStringHttpCurlProxyHostAndPort'] == ''
    assert values['DFStringHttpPointsReporterUrl'] == 'https://client-telemetry.roblox.com'


def test_macho_x86_string_literals_in_text_const_are_verified_but_writable_data_is_not() -> None:
    image, sections = _fixture()
    constant = _CSTRING_VA + 0x200
    writable = _STORAGE + 0x200
    sections.extend((
        ('__TEXT', '__const', constant, 0xA00, 0x100),
        ('__DATA', '__data', writable, 0xC00, 0x100),
    ))
    image[0xA00:0xA06] = b'Const\0'
    image[0xC00:0xC09] = b'Writable\0'
    _emit(image, 0x20, _direct(0x20, _STORAGE, constant))
    _emit(image, 0x80, _direct(0x80, _STORAGE + 0x18, writable))
    assert extract_macho_x86_string_initializers(
        bytes(image), sections, {_STORAGE, _STORAGE + 0x18},
    ) == {_STORAGE: 'Const'}


def test_elf_x86_callee_saved_strings_use_the_exact_local_elf_sections() -> None:
    from fleasion.modifications.fflag_macho_x86_strings import (  # noqa: PLC0415
        extract_elf_x86_callee_saved_string_initializers,
    )

    image, sections = _fixture()
    _emit(image, 0x20, _direct(0x20, _STORAGE, _CSTRING_VA + 0x20))
    elf_sections = {'.text': sections[0][2:], '.rodata': sections[1][2:]}
    values = extract_elf_x86_callee_saved_string_initializers(
        bytes(image), elf_sections, {_STORAGE},
    )
    assert values == {_STORAGE: 'Hello'}
    assert extract_elf_x86_callee_saved_string_initializers(
        bytes(image), {'.text': elf_sections['.text']}, {_STORAGE},
    ) == {}
    assert extract_elf_x86_callee_saved_string_initializers(
        bytes(image), elf_sections, {_STORAGE + 0x18},
    ) == {}
