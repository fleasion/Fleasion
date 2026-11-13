"""Static Android ELF registration-layout tests with synthetic packed relocations."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fleasion.modifications.fflag_static import ClientBinary, scan_binary


def _sleb(number: int) -> bytes:
    output = bytearray()
    while True:
        byte = number & 0x7F
        number >>= 7
        terminal = (number == 0 and not byte & 0x40) or (
            number == -1 and bool(byte & 0x40)
        )
        output.append(byte if terminal else byte | 0x80)
        if terminal:
            return bytes(output)


def _make_elf() -> bytes:
    """Build a relocatable ELF64 with real 32-byte flag descriptors."""
    data = bytearray(0x1200)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<H', data, 16, 3)
    struct.pack_into('<H', data, 18, 62)
    struct.pack_into('<Q', data, 40, 0x900)
    struct.pack_into('<HHH', data, 58, 64, 5, 4)

    shstr = b'\0.rodata\0.data.rel.ro\0.android.rela.dyn\0.shstrtab\0'
    names = {
        '.rodata': shstr.index(b'.rodata'),
        '.data.rel.ro': shstr.index(b'.data.rel.ro'),
        '.android.rela.dyn': shstr.index(b'.android.rela.dyn'),
        '.shstrtab': shstr.index(b'.shstrtab'),
    }
    data[0x800:0x800 + len(shstr)] = shstr
    items = (
        ('BooleanCorrect', 2, 0),
        ('IntegerCorrect', 2, 2),
        ('LogOrString', 1, 3),
        ('FStringOnly', 2, 3),
        ('IgnoredServer', 3, 0),
    )
    positions: list[int] = []
    cursor = 0x100
    for index, (name, variant, kind) in enumerate(items):
        raw = name.encode('ascii')
        data[cursor:cursor + len(raw) + 1] = raw + b'\0'
        positions.append(0x2000 + cursor - 0x100)
        descriptor = 0x300 + index * 32
        struct.pack_into('<Q', data, descriptor + 8, len(raw))
        struct.pack_into('<Q', data, descriptor + 16, 0)
        struct.pack_into('<Q', data, descriptor + 24, (kind << 32) | variant)
        cursor += len(raw) + 1

    # Android APS2: each relocation reconstructs a rodata pointer in the
    # descriptor, with signed LEB128 deltas and no runtime code execution
    packed = bytearray(b'APS2')
    packed.extend(_sleb(len(positions)))
    packed.extend(_sleb(0x5000))
    previous_address = 0
    for index, address in enumerate(positions):
        packed.extend(_sleb(1))
        packed.extend(_sleb(8))
        packed.extend(_sleb(0 if index == 0 else 32))
        packed.extend(_sleb(8))
        packed.extend(_sleb(address - previous_address))
        previous_address = address
    data[0x600:0x600 + len(packed)] = packed

    sections = (
        ('.rodata', 1, 0x2000, 0x100, 0x200),
        ('.data.rel.ro', 1, 0x5000, 0x300, 0x200),
        ('.android.rela.dyn', 0x60000002, 0, 0x600, len(packed)),
        ('.shstrtab', 3, 0, 0x800, len(shstr)),
    )
    for index, (name, kind, address, fileoff, size) in enumerate(sections, start=1):
        offset = 0x900 + index * 64
        struct.pack_into('<II', data, offset, names[name], kind)
        struct.pack_into('<QQQ', data, offset + 16, address, fileoff, size)
    return bytes(data)


def test_elf_type_metadata_is_after_the_name_and_not_previous_record(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'libroblox.so'
    path.write_bytes(_make_elf())
    source = ClientBinary('sober', 'Sober', path)
    catalog = scan_binary(source)
    assert catalog.verified == frozenset({
        'DFFlagBooleanCorrect',
        'DFIntIntegerCorrect',
        'FStringLogOrString',
        'DFStringFStringOnly',
    })
    assert catalog.flags == {
        'DFFlagBooleanCorrect': None,
        'DFIntIntegerCorrect': None,
        'FStringLogOrString': None,
        'DFStringFStringOnly': None,
    }
    assert catalog.records == 4
    assert catalog.unresolved == 0

    # Generic catalogs may incorrectly list a logging prefix, but the
    # selected binary's registration type takes precedence
    corrected = scan_binary(source, {
        'FLogLogOrString': 'trace',
        'FStringLogOrString': 'text',
        'DFStringFStringOnly': 'value',
    })
    assert corrected.flags['FStringLogOrString'] == 'text'
    assert corrected.flags['DFStringFStringOnly'] == 'value'
    assert 'FLogLogOrString' not in corrected.flags
    assert corrected.verified == catalog.verified


def test_elf_false_public_log_prefix_cannot_override_encoded_string_type(
    tmp_path: Path,
) -> None:
    path = tmp_path / 'libroblox.so'
    path.write_bytes(_make_elf())
    catalog = scan_binary(ClientBinary('sober', 'Sober', path), {
        'FLogLogOrString': None,
        'FStringLogOrString': None,
    })
    assert 'FLogLogOrString' not in catalog.flags
    assert 'FStringLogOrString' in catalog.verified
    assert catalog.unresolved == 0


def test_elf_rejects_flag_shaped_relocation_with_impossible_boolean_storage(
    tmp_path: Path,
) -> None:
    binary = bytearray(_make_elf())
    # The first record is a boolean descriptor at file offset 0x300
    # A 64-bit value with nonzero type-like high bits cannot be a bool
    struct.pack_into('<Q', binary, 0x300 + 16, 0x100000002)
    path = tmp_path / 'libroblox.so'
    path.write_bytes(binary)
    result = scan_binary(ClientBinary('sober', 'Sober', path))
    assert 'DFFlagBooleanCorrect' not in result.flags
    assert result.verified == frozenset({
        'DFIntIntegerCorrect', 'FStringLogOrString', 'DFStringFStringOnly',
    })
    assert result.unresolved == 0
    assert result.records == 3


def test_elf_accepts_a_valid_boolean_storage_bit(tmp_path: Path) -> None:
    binary = bytearray(_make_elf())
    struct.pack_into('<Q', binary, 0x300 + 16, 1)
    path = tmp_path / 'libroblox.so'
    path.write_bytes(binary)
    result = scan_binary(ClientBinary('sober', 'Sober', path))
    assert 'DFFlagBooleanCorrect' in result.verified


def test_real_sober_metadata_and_log_thunks_if_fixture_present() -> None:
    binary = Path('/tmp/fleasion_player_libroblox.so')
    if not binary.is_file():
        pytest.skip('The local Sober test Player is unavailable')
    catalog = scan_binary(ClientBinary('sober', 'Sober', binary))
    assert catalog.records == len(catalog.verified) == 14_986
    assert catalog.unresolved == 0
    assert {'DFIntTaskSchedulerTargetFps', 'DFFlagDebugDrawBroadPhaseAABBs',
            'DFLogWriteOnlyBuffer', 'FStringLuaErrorsInfluxSeries'} <= catalog.verified
    assert 'FLogLuaErrorsInfluxSeries' not in catalog.flags
    assert 'FFlagL2T1' not in catalog.flags
    assert 'DFFlagL2T2_KEY_SHIFT' not in catalog.flags


def test_elf_truncated_relocations_fail_cleanly(tmp_path: Path) -> None:
    content = bytearray(_make_elf())
    # The claimed 4 million relocations do not fit the actual APS2 stream
    content[0x604:0x606] = b'\xff\xff'
    path = tmp_path / 'libroblox.so'
    path.write_bytes(content)
    with pytest.raises(ValueError):
        scan_binary(ClientBinary('sober', 'Sober', path))
