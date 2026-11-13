"""Static Android ELF registration-layout tests with synthetic packed relocations."""

from __future__ import annotations

import os
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
    assert corrected.flags['FStringLogOrString'] is None
    assert corrected.flags['DFStringFStringOnly'] is None
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


def _make_elf_with_relocated_storage(*, invalid_boolean_address: bool = False) -> bytes:
    """Add an actual writable scalar section and loader-resolved storage pointers."""
    binary = bytearray(_make_elf())
    struct.pack_into('<H', binary, 60, 6)
    old_shstr = b'\0.rodata\0.data.rel.ro\0.android.rela.dyn\0.shstrtab\0'
    name_offset = len(old_shstr)
    binary[0x800:0x800 + name_offset + 6] = old_shstr + b'.data\0'
    struct.pack_into('<Q', binary, 0x900 + 4 * 64 + 32, name_offset + 6)
    header = 0x900 + 5 * 64
    struct.pack_into('<II', binary, header, name_offset, 1)
    struct.pack_into('<QQQ', binary, header + 16, 0x6000, 0x700, 0x80)
    binary[0x700] = 1
    struct.pack_into('<i', binary, 0x708, -15)

    packed = bytearray(b'APS2')
    names = ('BooleanCorrect', 'IntegerCorrect', 'LogOrString',
             'FStringOnly', 'IgnoredServer')
    targets: list[tuple[int, int]] = []
    position = 0x2000
    for index, name in enumerate(names):
        targets.append((0x5000 + index * 32, position))
        storage = 0x6000 + index * 8
        if invalid_boolean_address and index == 0:
            storage = 0x4000
        targets.append((0x5000 + index * 32 + 16, storage))
        position += len(name) + 1
    packed.extend(_sleb(len(targets)))
    packed.extend(_sleb(0x5000))
    current_offset, current_addend = 0x5000, 0
    for offset, target in targets:
        packed.extend(_sleb(1))
        packed.extend(_sleb(8))
        packed.extend(_sleb(offset - current_offset))
        packed.extend(_sleb(8))
        packed.extend(_sleb(target - current_addend))
        current_offset, current_addend = offset, target
    assert len(packed) < 0x100
    binary[0x600:0x600 + len(packed)] = packed
    struct.pack_into('<Q', binary, 0x900 + 3 * 64 + 32, len(packed))
    return bytes(binary)


def test_sober_relocated_storage_recovers_actual_disk_scalar_values(tmp_path: Path) -> None:
    binary = tmp_path / 'libroblox.so'
    binary.write_bytes(_make_elf_with_relocated_storage())
    catalog = scan_binary(ClientBinary('sober', 'Sober', binary))
    assert catalog.flags['DFFlagBooleanCorrect'] == 'True'
    assert catalog.flags['DFIntIntegerCorrect'] == '-15'
    assert catalog.flags['FStringLogOrString'] is None
    assert catalog.flags['DFStringFStringOnly'] is None
    assert catalog.verified == frozenset(catalog.flags)


def test_relocated_storage_outside_writable_data_is_not_interpreted(tmp_path: Path) -> None:
    binary = tmp_path / 'libroblox.so'
    binary.write_bytes(_make_elf_with_relocated_storage(invalid_boolean_address=True))
    catalog = scan_binary(ClientBinary('sober', 'Sober', binary))
    assert catalog.flags['DFFlagBooleanCorrect'] is None
    assert catalog.flags['DFIntIntegerCorrect'] == '-15'


@pytest.mark.parametrize('client', ['sober', 'mocktail', 'nightcap', 'cordial'])
def test_android_clients_share_relocated_storage_decoder(client: str, tmp_path: Path) -> None:
    binary = tmp_path / 'libroblox.so'
    binary.write_bytes(_make_elf_with_relocated_storage())
    catalog = scan_binary(ClientBinary(client, client, binary))
    assert catalog.flags['DFFlagBooleanCorrect'] == 'True'
    assert catalog.flags['DFIntIntegerCorrect'] == '-15'
    assert catalog.flags['FStringLogOrString'] is None


def test_real_sober_scalar_and_log_defaults_if_fixture_supplied() -> None:
    pathname = os.environ.get('FLEASION_SOBER_PLAYER_PATH')
    if not pathname or not (binary := Path(pathname)).is_file():
        pytest.skip('No independently supplied Sober ELF fixture')
    catalog = scan_binary(ClientBinary('sober', 'Sober', binary))
    assert len(catalog.verified) > 13_000
    assert sum(value is not None for value in catalog.flags.values()) > 13_000
    assert catalog.flags['DFIntCollisionScalingDefaultStudTenths'] == '67'
    assert catalog.flags['DFIntS2PhysicsSenderRate'] == '15'
    assert catalog.flags['FLogCrash'] == '769'
    assert catalog.flags['DFLogWriteOnlyBuffer'] == '518'
    assert catalog.flags['FStringLuaErrorsInfluxSeries'] == 'LuaShellErrors'


def test_elf_relocated_string_defaults_include_valid_empty_strings(tmp_path: Path) -> None:
    binary = bytearray(_make_elf_with_relocated_storage())
    struct.pack_into('<H', binary, 60, 7)
    name_table = b'\0.rodata\0.data.rel.ro\0.android.rela.dyn\0.shstrtab\0.data\0.text\0'
    binary[0x800:0x800 + len(name_table)] = name_table
    struct.pack_into('<Q', binary, 0x900 + 4 * 64 + 32, len(name_table))
    struct.pack_into('<II', binary, 0x900 + 6 * 64, name_table.index(b'.text'), 1)
    struct.pack_into('<QQQ', binary, 0x900 + 6 * 64 + 16, 0x7000, 0xC00, 0x100)
    binary[0x270:0x27F] = b'compiled-value\0'
    binary[0x280] = 0
    binary[0xC80:0xC80 + 21] = bytes.fromhex(
        '554889e5415741564154534989f74889fb4889f7e8'
    )
    for idx, (dest, literal) in enumerate(((0x6010, 0x2170), (0x6018, 0x2180))):
        va = 0x7000 + idx * 19
        raw = (
            b'\x48\x8d\x3d' + struct.pack('<i', dest - (va + 7))
            + b'\x48\x8d\x35' + struct.pack('<i', literal - (va + 14))
            + b'\xe8' + struct.pack('<i', 0x7080 - (va + 19))
        )
        binary[0xC00 + idx * 19:0xC00 + (idx + 1) * 19] = raw
    path = tmp_path / 'libroblox.so'
    path.write_bytes(binary)
    catalog = scan_binary(ClientBinary('sober', 'Sober', path))
    assert catalog.flags['FStringLogOrString'] == 'compiled-value'
    assert catalog.flags['DFStringFStringOnly'] == ''
