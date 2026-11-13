"""Version-independent PE section discovery and protected-code diagnostics."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fleasion.modifications.fflag_pe import (
    companion_has_protection_metadata,
    inspect_pe,
)
from fleasion.modifications.fflag_static import (
    ClientBinary,
    _longest_record_run,  # pyright: ignore[reportPrivateUsage]
    scan_binary,
)


def _synthetic_pe(
    *,
    name_section: str = '.rdata',
    table_section: str = '.data',
    name_rva: int = 0x3000,
    table_rva: int = 0x6000,
) -> bytes:
    """Generate an x64 PE with arbitrary section names and addresses."""
    payload = bytearray(0x4800)
    payload[:2] = b'MZ'
    struct.pack_into('<I', payload, 0x3C, 0x100)
    payload[0x100:0x104] = b'PE\0\0'
    struct.pack_into('<HH', payload, 0x104, 0x8664, 2)
    struct.pack_into('<H', payload, 0x114, 240)
    struct.pack_into('<H', payload, 0x118, 0x20B)
    base = 0x140000000
    struct.pack_into('<Q', payload, 0x130, base)
    sections = (
        (name_section, name_rva, 0x400, 0x1000, 0x40000040),
        (table_section, table_rva, 0x1800, 0x1000, 0xC0000040),
    )
    for index, (name, rva, fileoff, size, flags) in enumerate(sections):
        header = 0x118 + 240 + index * 40
        payload[header : header + 8] = name.encode().ljust(8, b'\0')
        struct.pack_into('<IIII', payload, header + 8, size, rva, size, fileoff)
        struct.pack_into('<I', payload, header + 36, flags)
    name = b'TaskSchedulerTargetFps'
    payload[0x400 : 0x400 + len(name) + 1] = name + b'\0'
    struct.pack_into('<QQQ', payload, 0x1810, base + name_rva, len(name), 0)
    return bytes(payload)


@pytest.mark.parametrize(
    ('names', 'table', 'n_rva', 't_rva'),
    [
        ('.rdata', '.data', 0x3000, 0x6000),
        ('.names', '.flags', 0x50000, 0x70000),
        ('A', 'B', 0xFE000, 0xA000),
    ],
)
def test_scan_discovers_data_layout_without_build_offsets(
    tmp_path: Path,
    names: str,
    table: str,
    n_rva: int,
    t_rva: int,
) -> None:
    content = _synthetic_pe(
        name_section=names,
        table_section=table,
        name_rva=n_rva,
        table_rva=t_rva,
    )
    source_path = tmp_path / 'RobloxPlayerBeta.exe'
    source_path.write_bytes(content)
    image = inspect_pe(content)
    assert image.section(table) is not None
    assert len(image.strings()) == 1
    assert len(image.tables()) == 1
    result = scan_binary(
        ClientBinary('windows', 'Windows', source_path),
        {
            'DFIntTaskSchedulerTargetFps': '240',
        },
    )
    assert result.records == 1
    assert result.flags == {'DFIntTaskSchedulerTargetFps': '240'}
    assert not result.verified


def test_pe_default_above_one_excludes_boolean_prefixes_only_in_coherent_runs(
    tmp_path: Path,
) -> None:
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    payload = bytearray(_synthetic_pe())
    # The scalar belongs immediately BEFORE its own name pointer
    payload[0x418 : 0x418 + len(b'AnotherScalar') + 1] = b'AnotherScalar\0'
    struct.pack_into('<Q', payload, 0x1808, 300)
    struct.pack_into('<QQQ', payload, 0x1828, 0x140003018, 13, 0)
    binary.write_bytes(payload)
    scan = ClientBinary('windows', 'Windows', binary)
    result = scan_binary(
        scan,
        {
            'DFIntTaskSchedulerTargetFps': '240',
            'FFlagTaskSchedulerTargetFps': 'True',
            'FIntAnotherScalar': '7',
        },
    )
    assert 'DFIntTaskSchedulerTargetFps' in result.flags
    assert 'FFlagTaskSchedulerTargetFps' not in result.flags
    assert 'FIntAnotherScalar' in result.flags
    assert not result.verified
    solely_wrong = scan_binary(
        scan,
        {
            'FFlagTaskSchedulerTargetFps': 'True',
            'FIntAnotherScalar': '7',
        },
    )
    assert 'FFlagTaskSchedulerTargetFps' not in solely_wrong.flags
    assert 'FIntAnotherScalar' in solely_wrong.flags


def test_pe_string_flags_in_rodata_are_inferred_not_verified(tmp_path: Path) -> None:
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    data = bytearray(_synthetic_pe())
    name = b'LuaErrorsInfluxSeries'
    data[0x480 : 0x480 + len(name) + 1] = name + b'\0'
    binary.write_bytes(data)
    source = ClientBinary('windows', 'Windows', binary)
    raw = scan_binary(source, {'DFIntTaskSchedulerTargetFps': '240'})
    assert raw.records == 1
    hints = scan_binary(
        source,
        {
            'DFIntTaskSchedulerTargetFps': '240',
            'FLogLuaErrorsInfluxSeries': None,
        },
    )
    assert hints.records == 2
    assert hints.flags['FLogLuaErrorsInfluxSeries'] is None
    assert 'FLogLuaErrorsInfluxSeries' not in hints.verified
    assert 'FStringLuaErrorsInfluxSeries' not in hints.flags
    ambiguous = scan_binary(
        source,
        {
            'DFIntTaskSchedulerTargetFps': '240',
            'FLogLuaErrorsInfluxSeries': None,
            'FStringLuaErrorsInfluxSeries': None,
        },
    )
    assert not any(name.endswith('LuaErrorsInfluxSeries') for name in ambiguous.flags)


def test_windows_qualified_log_labels_add_inferred_flags_without_public_sources(
    tmp_path: Path,
) -> None:
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    data = bytearray(_synthetic_pe())
    # Actual names must also be separate, read-only null-terminated strings
    for offset, value in (
        (0x480, b'NetworkTrace\0'),
        (0x4A0, b'CollisionTrace\0'),
        (0x4C0, b'[FLog::NetworkTrace] logged a message\0'),
        (0x520, b'[DFLog::CollisionTrace] logged a message\0'),
        (0x580, b'[DFLog::AbsentTrace] no matching name\0'),
        (0x620, b'DataOnlyTrace\0'),
    ):
        data[offset : offset + len(value)] = value
    data[0x1900 : 0x1900 + len(b'[FLog::DataOnlyTrace] writable\0')] = (
        b'[FLog::DataOnlyTrace] writable\0'
    )
    binary.write_bytes(data)
    result = scan_binary(ClientBinary('windows', 'Windows', binary))
    assert set(result.flags) == {'FLogNetworkTrace', 'DFLogCollisionTrace'}
    assert not result.verified
    assert result.records == 3
    assert not any(
        'AbsentTrace' in name or 'DataOnlyTrace' in name for name in result.flags
    )
    assert {rec.kind for rec in result.registrations if rec.name.endswith('Trace')} == {
        'log_label',
    }
    assert {'NetworkTrace', 'CollisionTrace'} <= set(result.candidate_strings)
    # A separate published string with the same suffix is independent evidence
    overlap = scan_binary(
        ClientBinary('windows', 'Windows', binary),
        {
            'DFIntTaskSchedulerTargetFps': '240',
            'FStringNetworkTrace': 'http://example.invalid',
        },
    )
    assert 'FLogNetworkTrace' in overlap.flags
    assert 'FStringNetworkTrace' in overlap.flags
    assert 'FLogNetworkTrace' not in overlap.verified


def test_windows_log_channel_labels_match_android_typed_logs_if_fixtures_present() -> (
    None
):
    windows = Path('/tmp/fleasion-roblox-windows-player-0.747.exe')
    sober = Path('/tmp/fleasion_player_libroblox.so')
    if not windows.is_file() or not sober.is_file():
        pytest.skip('The local official Windows and Sober binaries are unavailable')
    android = scan_binary(ClientBinary('sober', 'Sober', sober))
    pe = scan_binary(ClientBinary('windows', 'Windows', windows))
    direct_logs = {
        ('FLog' if rec.variant == 1 else 'DFLog') + rec.name
        for rec in pe.registrations
        if rec.kind == 'log_label'
    }
    assert len(direct_logs) == 775
    assert len(direct_logs & android.verified) >= 700
    assert not pe.verified
    assert direct_logs <= pe.flags.keys()


def test_pe_rejects_corrupt_sections_and_wrong_machine() -> None:
    content = bytearray(_synthetic_pe())
    struct.pack_into('<I', content, 0x3C, len(content) - 3)
    with pytest.raises(ValueError, match='PE header'):
        inspect_pe(bytes(content))
    content = bytearray(_synthetic_pe())
    struct.pack_into('<H', content, 0x104, 0xAA64)
    with pytest.raises(ValueError, match='x64'):
        inspect_pe(bytes(content))
    content = bytearray(_synthetic_pe())
    # First section file range escapes the file
    struct.pack_into('<I', content, 0x118 + 240 + 20, 0xFFFFFF00)
    with pytest.raises(ValueError, match='section'):
        inspect_pe(bytes(content))


def test_record_run_validation_rejects_scattered_matches() -> None:
    assert _longest_record_run([]) == 0
    assert _longest_record_run([100, 124, 148, 196, 500]) == 3
    assert _longest_record_run([100 + 40 * i for i in range(1000)]) == 1
    assert _longest_record_run([100 + 24 * i for i in range(100)]) == 100


def test_companion_detection_checks_section_not_hardcoded_rva() -> None:
    content = bytearray(_synthetic_pe(name_section='.byfron'))
    header = 0x118 + 240
    struct.pack_into('<I', content, header + 36, 0x60000020)
    assert companion_has_protection_metadata(bytes(content))
    struct.pack_into('<I', content, header + 36, 0x40000040)
    assert not companion_has_protection_metadata(bytes(content))


def test_real_official_windows_pair_if_available() -> None:
    exe = Path('/tmp/fleasion-roblox-windows-player-0.747.exe')
    dll = exe.with_suffix('.dll')
    if not exe.is_file() or not dll.is_file():
        pytest.skip('Official Windows Player test pair not available')
    raw_exe = exe.read_bytes()
    assert inspect_pe(raw_exe).section('.data') is not None
    assert companion_has_protection_metadata(dll.read_bytes())
    result = scan_binary(
        ClientBinary('windows', 'Windows', exe, companion_path=dll),
        {'DFFlagDebugDrawBroadPhaseAABBs': None},
    )
    assert result.records > 10_000
    assert result.companion_metadata_recognized is True
    assert 'DFFlagDebugDrawBroadPhaseAABBs' in result.flags
    assert not result.verified
    string_results = scan_binary(
        ClientBinary('windows', 'Windows', exe),
        {'FLogLuaErrorsInfluxSeries': None},
    )
    assert 'FLogLuaErrorsInfluxSeries' in string_results.flags
    assert 'FLogLuaErrorsInfluxSeries' not in string_results.verified


def test_pre_745_static_string_evidence_is_inferred_and_requalifiable(
    tmp_path: Path,
) -> None:
    # Older Windows EXEs contain the name strings but do not publish the
    # later 24-byte registration table. Exercise the fallback independently
    # of the downloaded historical binaries
    source = bytearray(_synthetic_pe())
    payload = bytearray(23 * 1024 * 1024)
    payload[:0x400] = source[:0x400]
    sections_start = 0x118 + 240
    struct.pack_into(
        '<IIII',
        payload,
        sections_start + 8,
        20 * 1024 * 1024,
        0x3000,
        20 * 1024 * 1024,
        0x1000,
    )
    struct.pack_into(
        '<IIII', payload, sections_start + 40 + 8, 0x1000, 0x2000000, 0x1000, 0x1500000
    )
    cursor = 0x1000
    for index in range(12_000):
        raw = f'Name{index:05d}\0'.encode()
        payload[cursor : cursor + len(raw)] = raw
        cursor += len(raw)
    for index in range(300):
        for raw in (
            f'Channel{index:05d}\0'.encode(),
            f'[FLog::Channel{index:05d}] sample message\0'.encode(),
        ):
            payload[cursor : cursor + len(raw)] = raw
            cursor += len(raw)
    path = tmp_path / 'RobloxPlayerBeta.exe'
    path.write_bytes(payload)
    hints = {f'DFIntName{i:05d}': None for i in range(510)}
    hints['DFFlagMissingEntirely'] = None
    result = scan_binary(ClientBinary('windows', 'Windows', path), hints)
    assert result.layout == 'string-evidence'
    assert result.verified == frozenset()
    assert result.records == len(result.flags) == 810
    assert len(result.candidate_strings) >= 12_300
    assert 'DFIntName00001' in result.flags
    assert 'DFFlagMissingEntirely' not in result.flags
    assert 'FLogChannel00025' in result.flags
    assert all(
        rec.kind in {'cstring_only', 'log_label'} for rec in result.registrations
    )

    # Cached strings can be qualified with future hints without rescanning
    from fleasion.modifications.fflag_static import (
        resolve_catalog_from_snapshot,
    )

    later = resolve_catalog_from_snapshot(
        result,
        {
            'DFIntName00155': None,
            'FIntName00155': None,
            'DFIntName00511': None,
        },
    )
    assert 'DFIntName00155' not in later.flags  # conflicting prefixes
    assert 'FIntName00155' not in later.flags
    assert 'DFIntName00511' in later.flags
    assert 'FLogChannel00025' in later.flags
    assert not later.verified


def test_legacy_pe_name_only_evidence_rejects_unrelated_data(tmp_path: Path) -> None:
    payload = bytearray(23 * 1024 * 1024)
    fixture = _synthetic_pe()
    payload[:0x400] = fixture[:0x400]
    first = 0x118 + 240
    struct.pack_into(
        '<IIII', payload, first + 8, 20 * 1024 * 1024, 0x3000, 20 * 1024 * 1024, 0x1000
    )
    struct.pack_into(
        '<IIII', payload, first + 40 + 8, 0x1000, 0x2000000, 0x1000, 0x1500000
    )
    path = tmp_path / 'RobloxPlayerBeta.exe'
    path.write_bytes(payload)
    with pytest.raises(ValueError, match='structural validation'):
        scan_binary(
            ClientBinary('windows', 'Windows', path),
            {
                'DFIntTaskSchedulerTargetFps': '240',
            },
        )
