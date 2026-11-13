"""Offline-only static Player FastFlag extraction and persistent cache tests."""

from __future__ import annotations

import json
import os
import struct
import zipfile
from pathlib import Path

import pytest

from fleasion.modifications import fflag_static
from fleasion.modifications.fflag_static import (
    ClientBinary,
    StaticFlagCatalog,
    StaticRecord,
    cached_cross_client_type_hints,
    load_scan,
    resolve_catalog_from_snapshot,
    save_scan,
    scan_binary,
    scan_is_current,
)


def _fake_pe(name: str) -> bytes:
    data = bytearray(8192)
    data[0:2] = b'MZ'
    struct.pack_into('<I', data, 0x3c, 0x100)
    data[0x100:0x104] = b'PE\0\0'
    struct.pack_into('<HH', data, 0x104, 0x8664, 2)
    struct.pack_into('<H', data, 0x114, 240)
    struct.pack_into('<H', data, 0x118, 0x20b)
    struct.pack_into('<Q', data, 0x130, 0x140000000)
    for i, (label, rva, at) in enumerate((('.rdata', 0x2000, 0x400), ('.data', 0x3000, 0x800))):
        header = 0x118 + 240 + 40 * i
        data[header:header + 8] = label.encode('ascii').ljust(8, b'\0')
        struct.pack_into('<IIII', data, header + 8, 0x100, rva, 0x200, at)
    name_bytes = name.encode('ascii')
    data[0x400:0x400 + len(name_bytes)] = name_bytes
    struct.pack_into('<QQQ', data, 0x808, 0x140002000, len(name_bytes), 0)
    return bytes(data)


def _fake_macho(name: str) -> bytes:
    data = bytearray(8192)
    data[:4] = b'\xcf\xfa\xed\xfe'
    struct.pack_into('<I', data, 4, 0x0100000c)  # ARM64, metadata without x86 thunks
    struct.pack_into('<II', data, 16, 2, 304)
    for i, (seg, sect, va, at) in enumerate((
        ('__TEXT', '__cstring', 0x100001000, 0x400),
        ('__DATA', '__const', 0x100002000, 0x800),
    )):
        pos = 32 + 152 * i
        struct.pack_into('<II', data, pos, 0x19, 152)
        data[pos + 8:pos + 24] = seg.encode('ascii').ljust(16, b'\0')
        struct.pack_into('<I', data, pos + 64, 1)
        data[pos + 72:pos + 88] = sect.encode('ascii').ljust(16, b'\0')
        data[pos + 88:pos + 104] = seg.encode('ascii').ljust(16, b'\0')
        struct.pack_into('<QQI', data, pos + 104, va, 0x100, at)
    name_bytes = name.encode('ascii')
    data[0x400:0x400 + len(name_bytes)] = name_bytes
    struct.pack_into('<QQQ', data, 0x808, 0x100001000, len(name_bytes), 0)
    return bytes(data)


def test_static_pe_and_macho_recover_record_without_inventing_prefixes(
    tmp_path: Path,
) -> None:
    base = 'DebugDrawBroadPhaseAABBs'
    for label, factory in (('win', _fake_pe), ('mac', _fake_macho)):
        binary = tmp_path / f'{label}.bin'
        binary.write_bytes(factory(base))
        source = ClientBinary(label, label, binary)
        result = scan_binary(source, {'DFFlag' + base: None})
        assert result.flags == {'DFFlag' + base: ('False' if label == 'mac' else None)}
        assert result.verified == frozenset()
        assert result.records == 1
        with pytest.raises(ValueError, match='qualified FastFlags'):
            scan_binary(source, {'FFlagUnrelated': 'True'})


def test_static_flag_type_mapping_never_fabricates_unverified_names() -> None:
    family = fflag_static._typed_candidates  # pyright: ignore[reportPrivateUsage]
    names = {'DFIntExample': '100', 'DFLogExample': None}
    assert family(StaticRecord('Example', 2, 'numeric', verified_type=True), names) == (
        ('DFIntExample', True),
    )
    assert family(StaticRecord('Example', 2, 'log', verified_type=True), names) == (
        ('DFLogExample', True),
    )
    assert family(StaticRecord('Example', 3, 'bool', verified_type=True), names) == ()
    assert family(StaticRecord('Example'), names) == ()
    assert family(StaticRecord('Example'), {'DFIntExample': None}) == (
        ('DFIntExample', False),
    )


def test_static_scan_snapshot_persists_until_explicit_recache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'snapshots')
    binary = tmp_path / 'Player.exe'
    binary.write_bytes(_fake_pe('DebugDrawBroadPhaseAABBs'))
    source = ClientBinary('windows', 'Windows', binary)
    result = scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None})
    save_scan(result)
    cached = load_scan('windows')
    assert cached is not None
    assert cached.flags == result.flags
    assert cached.registrations == result.registrations
    assert cached.registrations[0].name == 'DebugDrawBroadPhaseAABBs'
    assert scan_is_current(cached, source)
    binary.write_bytes(_fake_pe('AnotherFflagName'))
    assert not scan_is_current(cached, source)
    # Changing binary does not silently replace the saved catalog
    assert load_scan('windows') == cached
    save_scan(scan_binary(source, {'FFlagAnotherFflagName': None}))
    assert load_scan('windows') != cached


def test_companion_scan_fills_qualifier_only_when_selected_binary_has_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    companion_binary = tmp_path / 'android.bin'
    companion_binary.write_bytes(b'example binary')
    stat = companion_binary.stat()
    save_scan(StaticFlagCatalog(
        'sober', 'Sober', str(companion_binary),
        {'DFIntTaskSchedulerTargetFps': None, 'DFIntTextureQualityOverride': None},
        frozenset({'DFIntTaskSchedulerTargetFps', 'DFIntTextureQualityOverride'}),
        2, 0, 1.0, stat.st_size, stat.st_mtime_ns, None,
        binary_values=True,
    ))
    mac = tmp_path / 'RobloxPlayer'
    mac.write_bytes(_fake_macho('TaskSchedulerTargetFps'))
    hints = cached_cross_client_type_hints('rosemary')
    assert hints == frozenset({'DFIntTaskSchedulerTargetFps', 'DFIntTextureQualityOverride'})
    assert cached_cross_client_type_hints('sober') == frozenset()
    catalog = scan_binary(ClientBinary('rosemary', 'Rosemary', mac), dict.fromkeys(hints))
    assert catalog.flags == {'DFIntTaskSchedulerTargetFps': '0'}
    assert catalog.verified == frozenset()
    assert catalog.unresolved == 0


@pytest.mark.parametrize('previous_version', [6, 7])
def test_pre_v8_android_scans_are_rejected_before_cross_client_hinting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, previous_version: int,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'libroblox.so'
    binary.write_bytes(b'test Android snapshot fixture')
    original = StaticFlagCatalog(
        'sober', 'Sober', str(binary), {'FFlagL2T1': None},
        frozenset({'FFlagL2T1'}), 1, 0, 1234.0,
        binary.stat().st_size, binary.stat().st_mtime_ns, None,
    )
    save_scan(original)
    cache = tmp_path / 'scans/sober.json'
    payload = json.loads(cache.read_text())
    payload['version'] = previous_version
    cache.write_text(json.dumps(payload))
    assert load_scan('sober') is None
    assert cached_cross_client_type_hints('windows') == frozenset()


def test_previous_v6_scan_cache_remains_compatible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('TaskSchedulerTargetFps'))
    original = scan_binary(ClientBinary('windows', 'Windows', binary), {
        'DFIntTaskSchedulerTargetFps': '240',
    })
    save_scan(original)
    cache = tmp_path / 'scans' / 'windows.json'
    payload = json.loads(cache.read_text())
    payload['version'] = 6
    payload.pop('layout')
    cache.write_text(json.dumps(payload))
    loaded = load_scan('windows')
    assert loaded is not None and loaded.layout == 'structured'
    assert loaded.flags == original.flags


@pytest.mark.parametrize('legacy_version', [1, 2, 3, 4, 5])
def test_outdated_static_scan_schema_requires_fresh_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, legacy_version: int,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('DebugDrawBroadPhaseAABBs'))
    source = ClientBinary('windows', 'Windows', binary)
    updated = scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None})
    save_scan(updated)
    cached_path = tmp_path / 'scans' / 'windows.json'
    payload = json.loads(cached_path.read_text(encoding='utf-8'))
    payload['version'] = legacy_version
    cached_path.write_text(json.dumps(payload), encoding='utf-8')
    # Earlier schemas could contain incorrectly typed Android flags, so we
    # force regeneration instead of allowing those names into other scans
    assert load_scan('windows') is None
    assert cached_path.is_file()
    save_scan(scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None}))
    refreshed = load_scan('windows')
    assert refreshed is not None and refreshed.flags == updated.flags



def test_scan_refuses_racing_binary_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    original = _fake_pe('TaskSchedulerTargetFps')
    binary.write_bytes(original)

    def racing_read(_source: ClientBinary) -> bytes:
        binary.write_bytes(_fake_pe('TotallyDifferentFlag'))
        return original

    monkeypatch.setattr(fflag_static, '_read_binary', racing_read)
    with pytest.raises(ValueError, match='changed'):
        scan_binary(ClientBinary('windows', 'Windows', binary), {
            'DFIntTaskSchedulerTargetFps': '240',
        })


def test_source_hash_detects_same_size_same_mtime_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'Player.exe'
    binary.write_bytes(_fake_pe('DebugDrawBroadPhaseAABBs'))
    source = ClientBinary('windows', 'Windows', binary)
    catalog = scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None})
    save_scan(catalog)
    assert scan_is_current(catalog, source)
    previous = binary.stat()
    raw = bytearray(binary.read_bytes())
    raw[-1] ^= 0x13
    binary.write_bytes(raw)
    os.utime(binary, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert binary.stat().st_size == previous.st_size
    assert binary.stat().st_mtime_ns == previous.st_mtime_ns
    assert not scan_is_current(catalog, source)
    assert load_scan('windows') == catalog


def test_companion_hash_changes_invalidate_cached_windows_scan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('DebugDrawBroadPhaseAABBs'))
    dll = tmp_path / 'RobloxPlayerBeta.dll'
    content = bytearray(_fake_pe('PlaceholderFlag'))
    header = 0x118 + 240
    content[header:header + 8] = b'.byfron\0'
    struct.pack_into('<I', content, header + 16, 0x400)
    struct.pack_into('<I', content, header + 36, 0x60000020)
    dll.write_bytes(content)
    source = ClientBinary('windows', 'Windows', binary, companion_path=dll)
    catalog = scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None})
    assert catalog.companion_sha256 is not None
    assert catalog.companion_metadata_recognized
    assert scan_is_current(catalog, source)
    save_scan(catalog)
    assert load_scan('windows') == catalog

    previous = dll.stat()
    changed = bytearray(dll.read_bytes())
    changed[-1] ^= 1
    dll.write_bytes(changed)
    os.utime(dll, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    assert not scan_is_current(catalog, source)
    assert scan_is_current(
        scan_binary(source, {'DFFlagDebugDrawBroadPhaseAABBs': None}), source
    )


def test_unsupported_companion_layout_does_not_erase_static_flags(
    tmp_path: Path,
) -> None:
    exe = tmp_path / 'RobloxPlayerBeta.exe'
    dll = tmp_path / 'RobloxPlayerBeta.dll'
    exe.write_bytes(_fake_pe('DebugDrawBroadPhaseAABBs'))
    dll.write_bytes(_fake_pe('UnrelatedRecord'))
    catalog = scan_binary(
        ClientBinary('windows', 'Windows', exe, companion_path=dll),
        {'DFFlagDebugDrawBroadPhaseAABBs': None},
    )
    assert catalog.flags == {'DFFlagDebugDrawBroadPhaseAABBs': None}
    assert catalog.verified == frozenset()
    assert catalog.companion_metadata_recognized is False


def test_raw_metadata_snapshot_can_gain_types_without_rescanning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('TaskSchedulerTargetFps'))
    catalog = scan_binary(
        ClientBinary('windows', 'Windows', binary),
        {'DFIntTaskSchedulerTargetFps': '240'},
    )
    save_scan(catalog)
    binary.unlink()
    cached = load_scan('windows')
    assert cached is not None
    assert cached.registrations[0].name == 'TaskSchedulerTargetFps'
    refreshed = resolve_catalog_from_snapshot(cached, {
        'DFIntTaskSchedulerTargetFps': '300',
    })
    assert refreshed.flags == {'DFIntTaskSchedulerTargetFps': '300'}
    assert not refreshed.verified
    assert refreshed.registrations == cached.registrations
    # In-memory resolution doesn't change a saved snapshot until explicitly saved
    assert load_scan('windows') == cached


def test_cached_inferred_log_labels_survive_without_public_type_hints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'Windows.exe'
    binary.write_bytes(_fake_pe('TaskSchedulerTargetFps'))
    source = ClientBinary('windows', 'Windows', binary)
    original = scan_binary(source, {'DFIntTaskSchedulerTargetFps': '240'})
    evidence = StaticRecord('DiagnosticChannel', variant=2, kind='log_label')
    enhanced = fflag_static.replace(
        original,
        flags={**original.flags, 'DFLogDiagnosticChannel': None},
        registrations=(*original.registrations, evidence),
        records=original.records + 1,
    )
    save_scan(enhanced)
    saved = load_scan('windows')
    assert saved is not None and saved.registrations == enhanced.registrations
    assert saved.verified == frozenset()
    binary.unlink()
    requalified = resolve_catalog_from_snapshot(saved, {})
    assert requalified.flags == {'DFLogDiagnosticChannel': None}
    assert requalified.verified == frozenset()


def test_windows_raw_string_cache_resolves_new_log_hints_without_binary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    data = bytearray(_fake_pe('TaskSchedulerTargetFps'))
    embedded = b'FutureLogFlag\0'
    data[0x480:0x480 + len(embedded)] = embedded
    binary.write_bytes(data)
    source = ClientBinary('windows', 'Windows', binary)
    before = scan_binary(source, {'DFIntTaskSchedulerTargetFps': '240'})
    assert 'FutureLogFlag' in before.candidate_strings
    assert not any(record.name == 'FutureLogFlag' for record in before.registrations)
    save_scan(before)
    cached = load_scan('windows')
    assert cached is not None and cached.candidate_strings == before.candidate_strings
    binary.unlink()
    updated = resolve_catalog_from_snapshot(cached, {
        'DFIntTaskSchedulerTargetFps': '240',
        'FLogFutureLogFlag': None,
        'FLogAbsentFlag': None,
    })
    assert updated.flags['FLogFutureLogFlag'] is None
    assert 'FLogFutureLogFlag' not in updated.verified
    assert 'FLogAbsentFlag' not in updated.flags
    assert updated.records == before.records + 1
    assert len(updated.registrations) == len(before.registrations) + 1
    assert load_scan('windows') == cached

    ambiguous = resolve_catalog_from_snapshot(cached, {
        'DFIntTaskSchedulerTargetFps': '240',
        'FLogFutureLogFlag': None,
        'FStringFutureLogFlag': None,
    })
    assert not any(name.endswith('FutureLogFlag') for name in ambiguous.flags)


def test_windows_raw_string_cache_rejects_corrupt_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('TaskSchedulerTargetFps'))
    save_scan(scan_binary(ClientBinary('windows', 'Windows', binary), {
        'DFIntTaskSchedulerTargetFps': '240',
    }))
    cache = tmp_path / 'scans' / 'windows.json'
    payload = json.loads(cache.read_text())
    payload['candidate_strings'] = ['ValidName', 'Invalid-Name']
    cache.write_text(json.dumps(payload))
    assert load_scan('windows') is None


def test_static_snapshot_rejects_malformed_raw_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    binary = tmp_path / 'RobloxPlayerBeta.exe'
    binary.write_bytes(_fake_pe('TaskSchedulerTargetFps'))
    save_scan(scan_binary(
        ClientBinary('windows', 'Windows', binary),
        {'DFIntTaskSchedulerTargetFps': '240'},
    ))
    cache = tmp_path / 'scans' / 'windows.json'
    payload = json.loads(cache.read_text())
    assert len(payload['registrations']) == 1
    payload['registrations'] = [['TaskSchedulerTargetFps', 99, 'numeric', True]]
    cache.write_text(json.dumps(payload))
    assert load_scan('windows') is None


def test_static_scan_malformed_binary_and_oversize_rejected(tmp_path: Path) -> None:
    malformed = tmp_path / 'invalid.exe'
    malformed.write_bytes(b'MZ' + b'\x00' * 100)
    with pytest.raises(ValueError):
        scan_binary(ClientBinary('windows', 'Windows', malformed), {})
    archive = tmp_path / 'Roblox.apk'
    with zipfile.ZipFile(archive, 'w') as out:
        out.writestr('lib/x86_64/libroblox.so', b'not an ELF file')
    with pytest.raises(ValueError):
        scan_binary(ClientBinary('sober', 'Sober', archive, 'lib/x86_64/libroblox.so'))


@pytest.mark.parametrize(('label', 'path', 'expected', 'verified'), [
    ('Sober', '/tmp/fleasion_player_libroblox.so', 'DFIntTaskSchedulerTargetFps', True),
    ('Rosemary', '/home/spencer/.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/Roblox.app/Contents/MacOS/RobloxPlayer', 'DFFlagDebugDrawBroadPhaseAABBs', True),
    ('Windows', '/tmp/fleasion-roblox-windows-player-0.747.exe', 'DFFlagDebugDrawBroadPhaseAABBs', False),
])
def test_offline_real_player_binary_when_fixture_present(
    label: str, path: str, expected: str, verified: bool,
) -> None:
    binary = Path(path)
    if not binary.is_file():
        pytest.skip(f'Real {label} binary fixture is not available')
    source = ClientBinary(label.lower(), label, binary)
    public = {
        'DFFlagDebugDrawBroadPhaseAABBs': None,
        'DFIntTaskSchedulerTargetFps': None,
    }
    result = scan_binary(source, public)
    assert result.records > 7500
    assert expected in result.flags
    assert (expected in result.verified) is verified
    assert 'DFIntTextureQualityOverride' not in result.flags
    if label == 'Sober':
        # Regression against the previous 8-byte descriptor displacement
        assert 'DFIntCollisionScalingDefaultStudTenths' in result.verified
        assert 'FIntDMRecorderBatchRotationBytes' in result.verified
        assert 'DFFlagCollisionScalingDefaultStudTenths' not in result.flags


def _fake_macho_with_named_getters(
    *, second_anchor: bool = True, auxiliary_type: int = 99,
) -> bytes:
    """Build two boolean thunks and one ambiguous int/log thunk offline."""
    data = bytearray(8192)
    data[:4] = b'\xcf\xfa\xed\xfe'
    struct.pack_into('<I', data, 4, 0x01000007)
    struct.pack_into('<II', data, 16, 2, 384)
    text = 32
    struct.pack_into('<II', data, text, 0x19, 232)
    data[text + 8:text + 24] = b'__TEXT'.ljust(16, b'\0')
    struct.pack_into('<I', data, text + 64, 2)
    for i, (section_name, address, offset) in enumerate((
        ('__text', 0x100000500, 0x500),
        ('__cstring', 0x100001000, 0x800),
    )):
        at = text + 72 + 80 * i
        data[at:at + 16] = section_name.encode().ljust(16, b'\0')
        data[at + 16:at + 32] = b'__TEXT'.ljust(16, b'\0')
        struct.pack_into('<QQI', data, at + 32, address, 0x200, offset)

    segment = text + 232
    struct.pack_into('<II', data, segment, 0x19, 152)
    data[segment + 8:segment + 24] = b'__DATA'.ljust(16, b'\0')
    struct.pack_into('<I', data, segment + 64, 1)
    at = segment + 72
    data[at:at + 16] = b'__const'.ljust(16, b'\0')
    data[at + 16:at + 32] = b'__DATA'.ljust(16, b'\0')
    struct.pack_into('<QQI', data, at + 32, 0x100002000, 0x200, 0xc00)

    def thunk(offset: int, name: str, name_offset: int, variant: int, helper: int) -> None:
        name_bytes = name.encode()
        data[name_offset:name_offset + len(name_bytes) + 1] = name_bytes + b'\0'
        record_offset = 0xc00 + (offset - 0x500) * 2
        struct.pack_into('<QQQ', data, record_offset,
                         0x100001000 + name_offset - 0x800, len(name_bytes), 0)
        value_va = 0x100002000 + record_offset - 0xc00 + 16
        code_va = 0x100000500 + offset - 0x500
        code = (
            b'\x55\x48\x89\xe5\x48\x8d\x3d'
            + struct.pack('<i', 0x100001000 + name_offset - 0x800 - (code_va + 11))
            + b'\x48\x8d\x35' + struct.pack('<i', value_va - (code_va + 18))
            + b'\xba' + struct.pack('<i', variant)
            + b'\x5d\xe9' + struct.pack('<i', helper - (code_va + 29))
        )
        assert len(code) == 29
        data[offset:offset + len(code)] = code

    # The second anchor can be deliberately absent: the machine-code type
    # discriminator must still identify the boolean registration helper
    thunk(0x500, 'DebugDrawBroadPhaseAABBs', 0x800, 2, 0x100000620)
    thunk(
        0x530,
        'MacSyncWKCookieStoreToNSCookieStoreV2' if second_anchor else 'UnlistedBooleanFlag',
        0x840,
        1,
        0x100000620,
    )
    thunk(0x560, 'SomeUncertainNumber', 0x880, 2, 0x100000680)
    # A 25-byte register-wrapper thunk followed by the typed implementation
    data[0x620:0x639] = (
        b'\x55\x48\x89\xe5\x89\xd1\x48\x89\xf2\x48\x89\xfe'
        b'\x48\x8b\x3d\x00\x00\x00\x00\x5d\xe9'
        + struct.pack('<i', 0x660 - (0x620 + 25))
    )
    data[0x660:0x66a] = b'\x55\x48\x89\xe5\x41\xb8' + struct.pack('<I', 4)
    data[0x680:0x699] = (
        b'\x55\x48\x89\xe5\x89\xd1\x48\x89\xf2\x48\x89\xfe'
        b'\x48\x8b\x3d\x00\x00\x00\x00\x5d\xe9'
        + struct.pack('<i', 0x6c0 - (0x680 + 25))
    )
    data[0x6c0:0x6ca] = (
        b'\x55\x48\x89\xe5\x41\xb8' + struct.pack('<I', auxiliary_type)
    )
    return bytes(data)


def test_macho_named_getter_distinguishes_boolean_from_shared_untyped_helper(
    tmp_path: Path,
) -> None:
    binary = tmp_path / 'RobloxPlayer'
    binary.write_bytes(_fake_macho_with_named_getters())
    source = ClientBinary('rosemary', 'Rosemary', binary)
    without_public = scan_binary(source, {})
    assert without_public.verified == frozenset({
        'DFFlagDebugDrawBroadPhaseAABBs',
        'FFlagMacSyncWKCookieStoreToNSCookieStoreV2',
    })
    assert 'DFIntSomeUncertainNumber' not in without_public.flags
    with_public = scan_binary(source, {'DFIntSomeUncertainNumber': '12'})
    assert with_public.flags['DFIntSomeUncertainNumber'] == '0'
    assert 'DFIntSomeUncertainNumber' not in with_public.verified
    assert len(with_public.verified) == 2
    # Unknown machine-code helper types cannot reject externally hinted kinds
    with_wrong_public = scan_binary(source, {'DFFlagSomeUncertainNumber': 'True'})
    assert 'DFFlagSomeUncertainNumber' not in with_wrong_public.verified


def test_macho_getter_recovers_log_type_from_compiled_helper(tmp_path: Path) -> None:
    binary = tmp_path / 'RobloxPlayer'
    binary.write_bytes(_fake_macho_with_named_getters(auxiliary_type=1))
    catalog = scan_binary(ClientBinary('macos', 'macOS', binary))
    assert 'DFLogSomeUncertainNumber' in catalog.verified
    assert catalog.flags['DFLogSomeUncertainNumber'] == '0'
    assert 'DFIntSomeUncertainNumber' not in catalog.flags


def test_macho_direct_getter_recovers_integer_without_cross_client_hints(
    tmp_path: Path,
) -> None:
    data = bytearray(_fake_macho_with_named_getters())
    code_va = 0x100000560
    # Mac direct accessors address [value, name pointer, name length]. The
    # preceding descriptor also looks plausible but belongs to another flag.
    value_va = 0x1000020F0
    name_va = 0x100001080
    previous_name = 'PreviousNeighborFlag'
    data[0x8B0:0x8B0 + len(previous_name) + 1] = previous_name.encode() + b'\0'
    struct.pack_into('<QQQ', data, 0xCE0, 0x1000010B0, len(previous_name), 67)
    struct.pack_into('<QQ', data, 0xCF8, name_va, len('SomeUncertainNumber'))
    helper_va = 0x100000680
    code = (
        b'\x55\x48\x89\xe5\x48\x8d\x3d'
        + struct.pack('<i', value_va - (code_va + 11))
        + b'\xbe' + struct.pack('<I', 2)
        + b'\x5d\xe9' + struct.pack('<i', helper_va - (code_va + 22))
    )
    assert len(code) == 22
    data[0x560:0x560 + 29] = code + b'\x90' * 7
    data[0x680:0x680 + 22] = (
        b'\x55\x48\x89\xe5\x89\xf2\x48\x89\xfe'
        b'\x48\x8b\x3d\x00\x00\x00\x00\x5d\xe9'
        + struct.pack('<i', 0x6C0 - (0x680 + 22))
    )
    data[0x6C0:0x6D3] = (
        b'\x55\x48\x89\xe5\x89\xd1\x48\x89\xf2'
        b'\x48\x8b\x76\x08\x41\xb8'
        + struct.pack('<I', 3)
    )
    binary = tmp_path / 'RobloxPlayer'
    binary.write_bytes(data)
    catalog = scan_binary(ClientBinary('macos', 'macOS', binary))
    assert 'DFIntSomeUncertainNumber' in catalog.verified
    assert catalog.flags['DFIntSomeUncertainNumber'] == '67'
    assert 'DFLogSomeUncertainNumber' not in catalog.flags
    assert 'DFIntPreviousNeighborFlag' not in catalog.verified
    assert 'DFIntPreviousNeighborFlag' not in catalog.flags


def test_macho_boolean_type_discriminator_works_without_name_anchors(
    tmp_path: Path,
) -> None:
    binary = tmp_path / 'RobloxPlayer'
    binary.write_bytes(_fake_macho_with_named_getters(second_anchor=False))
    result = scan_binary(ClientBinary('macos', 'macOS', binary))
    assert result.verified == frozenset({
        'DFFlagDebugDrawBroadPhaseAABBs',
        'FFlagUnlistedBooleanFlag',
    })
    assert result.flags == {
        'DFFlagDebugDrawBroadPhaseAABBs': 'False',
        'FFlagUnlistedBooleanFlag': 'False',
    }


def test_real_macos_x86_direct_getters_bind_the_correct_neighbor_if_present() -> None:
    path = Path.home() / (
        '.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/'
        'Roblox.app/Contents/MacOS/RobloxPlayer'
    )
    if not path.is_file():
        pytest.skip('No independently installed macOS x86-64 Player fixture')
    catalog = scan_binary(ClientBinary('nutmeg', 'Nutmeg', path))
    assert len(catalog.verified) >= 14_000
    assert catalog.flags['DFIntImmersiveAdDeveloperFallbackImageUsedEventThrottleHundrethsPercent'] == '0'
    assert catalog.flags['DFIntAdGuiVideoInteractivityControlTransparencyThousandths'] == '800'
    assert catalog.flags['DFIntSmoothFullscreenZoomDurationMs'] == '2000'
    assert catalog.flags['DFIntProximityVolume'] == '10'
    assert catalog.flags['DFIntFullscreenVolume'] == '0'
    assert catalog.flags['DFIntGamePassPrefetchRefreshSeconds'] == '55'
    assert catalog.flags['DFIntThirdPartyInMemoryCacheCapacity'] == '3'


def test_real_macos_fps_prefix_recovers_from_sober_static_evidence_if_present() -> None:
    mac = Path.home() / (
        '.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/'
        'Roblox.app/Contents/MacOS/RobloxPlayer'
    )
    sober = Path('/tmp/fleasion_player_libroblox.so')
    if not (mac.is_file() and sober.is_file()):
        pytest.skip('Both actual Player binary fixtures are required')
    sober_catalog = scan_binary(ClientBinary('sober', 'Sober', sober))
    assert 'DFIntTaskSchedulerTargetFps' in sober_catalog.verified
    mac_catalog = scan_binary(
        ClientBinary('rosemary', 'Rosemary', mac),
        dict.fromkeys(sober_catalog.verified),
    )
    assert mac_catalog.flags['DFIntTaskSchedulerTargetFps'] == '0'
    assert 'DFIntTaskSchedulerTargetFps' in mac_catalog.verified
    assert 'DFIntTextureQualityOverride' not in mac_catalog.flags


def test_binary_values_survive_cache_re_resolving_without_public_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    original = StaticFlagCatalog(
        'mocktail', 'Mocktail', '/fake/libroblox.so',
        {'DFIntTaskSchedulerTargetFps': '67', 'DFFlagDebugDrawBroadPhaseAABBs': None},
        frozenset({'DFIntTaskSchedulerTargetFps', 'DFFlagDebugDrawBroadPhaseAABBs'}),
        2, 0, 1234.0, 120000000, 12, None,
        registrations=(
            StaticRecord('TaskSchedulerTargetFps', 2, 'numeric', True, '67'),
            StaticRecord('DebugDrawBroadPhaseAABBs', 2, 'bool', True),
        ),
        binary_values=True,
    )
    save_scan(original)
    saved = load_scan('mocktail')
    assert saved is not None and saved == original
    refreshed = resolve_catalog_from_snapshot(saved, {
        'DFIntTaskSchedulerTargetFps': '240',
        'DFFlagDebugDrawBroadPhaseAABBs': 'True',
    })
    assert refreshed.flags == original.flags
    cache = tmp_path / 'scans/mocktail.json'
    old = json.loads(cache.read_text())
    for previous_version in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23):
        old['version'] = previous_version
        cache.write_text(json.dumps(old))
        assert load_scan('mocktail') is None


def test_raw_initializer_does_not_invent_string_or_invalid_scalar_values() -> None:
    decode = fflag_static._display_raw_initializer  # pyright: ignore[reportPrivateUsage]
    assert decode('FFlagEnabled', 1) == 'True'
    assert decode('FFlagEnabled', 2) is None
    assert decode('DFIntCount', 0xFFFFFFFF) == '-1'
    assert decode('DFIntCount', 0x100000001) is None
    assert decode('DFStringText', 0) is None
    assert decode('DFFloatRatio', 0x3FC00000) == '1.5'
    assert decode('DFFloatRatio', 0x7F800000) is None


def test_compiled_arm64_values_round_trip_cache_and_override_disk_slots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    original = StaticFlagCatalog(
        'rosemary', 'Rosemary', '/fake/RobloxPlayer',
        {
            'DFIntCollisionScalingDefaultStudTenths': '67',
            'DFFlagDebugDrawBroadPhaseAABBs': 'False',
        },
        frozenset({'DFFlagDebugDrawBroadPhaseAABBs'}),
        2, 0, 1234.0, 123000000, 120, None,
        registrations=(
            StaticRecord(
                'CollisionScalingDefaultStudTenths', 2, 'unknown', False,
                raw_initial_u64=0, compiled_raw_u32=67,
            ),
            StaticRecord(
                'DebugDrawBroadPhaseAABBs', 2, 'bool', True,
                raw_initial_u64=1, compiled_raw_u32=0,
            ),
        ),
        binary_values=True,
    )
    save_scan(original)
    loaded = load_scan('rosemary')
    assert loaded is not None and loaded == original
    refreshed = resolve_catalog_from_snapshot(loaded, {
        'DFIntCollisionScalingDefaultStudTenths': '999',
        'DFFlagDebugDrawBroadPhaseAABBs': 'True',
    })
    assert refreshed.flags == original.flags
    cached_path = tmp_path / 'scans/rosemary.json'
    malformed = json.loads(cached_path.read_text())
    malformed['registrations'][0][-1] = 1 << 32
    cached_path.write_text(json.dumps(malformed))
    assert load_scan('rosemary') is None


def test_real_macos_x86_helper_types_if_fixture_present() -> None:
    binary = Path.home() / (
        '.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/'
        'Roblox.app/Contents/MacOS/RobloxPlayer'
    )
    if not binary.is_file():
        pytest.skip('The local official x86-64 macOS Player is unavailable')
    catalog = scan_binary(ClientBinary('macos', 'macOS', binary))
    assert len(catalog.verified) > 13_000
    assert catalog.flags['DFIntS2PhysicsSenderRate'] == '15'
    assert 'DFIntS2PhysicsSenderRate' in catalog.verified
    assert 'DFLogMacEmbeddedWebView' in catalog.verified
    assert catalog.flags['FLogCookieStore'] == '768'


def test_binary_string_cache_preserves_empty_and_long_compiled_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(fflag_static, '_SCAN_CACHE_DIR', tmp_path / 'scans')
    long_literal = 'x' * 1800
    original = StaticFlagCatalog(
        'mocktail', 'Mocktail', '/fake/libroblox.so',
        {'FStringShortDefault': '', 'DFStringLongDefault': long_literal},
        frozenset({'FStringShortDefault', 'DFStringLongDefault'}),
        2, 0, 1234.0, 120000000, 12, None,
        registrations=(
            StaticRecord('ShortDefault', 1, 'string', True, ''),
            StaticRecord('LongDefault', 2, 'string', True, long_literal),
        ),
        binary_values=True,
    )
    save_scan(original)
    loaded = load_scan('mocktail')
    assert loaded == original
    assert loaded is not None
    recalculated = resolve_catalog_from_snapshot(loaded, {
        'FStringShortDefault': 'wrong', 'DFStringLongDefault': 'wrong',
    })
    assert recalculated.flags == original.flags
    path = tmp_path / 'scans/mocktail.json'
    corrupted = json.loads(path.read_text())
    corrupted['registrations'][1][4] = 'x' * 8193
    path.write_text(json.dumps(corrupted))
    assert load_scan('mocktail') is None


@pytest.mark.parametrize('runtime', ['native', 'flatpak'])
def test_nightcap_and_cordial_binary_discovery_tracks_the_selected_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runtime: str,
) -> None:
    from dataclasses import replace  # noqa: PLC0415

    from fleasion.utils import platform_linux  # noqa: PLC0415
    from fleasion.utils.linux_clients import (  # noqa: PLC0415
        CORDIAL_CLIENT, NIGHTCAP_CLIENT, LinuxClientInstallation,
    )

    instances: dict[str, tuple[LinuxClientInstallation, Path]] = {}
    for client in (NIGHTCAP_CLIENT, CORDIAL_CLIENT):
        installed = replace(client, runtime=runtime)
        paths = installed.paths(home=tmp_path)
        installation = LinuxClientInstallation(installed, paths, executable=None)
        if client.key == 'nightcap':
            binary = (
                paths.flatpak_root / 'data/mocktail/payloads/test/libroblox.so'
                if runtime == 'flatpak'
                else paths.flatpak_root / 'payloads/test/libroblox.so'
            )
        else:
            binary = paths.data_root / 'builds/2.746.0.1266/libroblox.so'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_bytes(client.key.encode())
        instances[client.key] = (installation, binary)

    # Neither global downloads nor another client's newer binary may override
    # the selected installation when a user asks to scan its compiled defaults.
    for key in ('nightcap', 'cordial'):
        installation, expected = instances[key]
        monkeypatch.setattr(
            platform_linux, 'get_selected_linux_client_installation',
            lambda installation=installation: installation,
        )
        found = fflag_static._detect_linux_binary()  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        assert found is not None
        assert found.client == key
        assert found.path == expected


@pytest.mark.parametrize(('client', 'environment'), [
    ('nightcap', 'FLEASION_NIGHTCAP_PLAYER_PATH'),
    ('cordial', 'FLEASION_CORDIAL_PLAYER_PATH'),
])
def test_installed_community_android_client_can_extract_its_own_compiled_defaults(
    client: str, environment: str,
) -> None:
    binary = os.environ.get(environment)
    if not binary or not Path(binary).is_file():
        pytest.skip(f'No independently downloaded {client} Roblox binary fixture')
    actual = scan_binary(ClientBinary(client, client.title(), Path(binary)))
    assert len(actual.verified) > 14_000
    assert len(actual.flags) == len(actual.verified)
    assert sum(value is not None for value in actual.flags.values()) / len(actual.flags) > 0.95
    assert actual.flags['FStringLuaErrorsInfluxSeries'] == 'LuaShellErrors'


def test_macho_x86_named_string_getter_accepts_zero_fill_storage_without_default(
    tmp_path: Path,
) -> None:
    # The code has the name, storage address, namespace, and helper type=2.
    # The std::string storage itself occupies __DATA,__common: offset zero
    # is not file-backed. It must never be interpreted as an empty literal.
    data = bytearray(_fake_macho_with_named_getters(auxiliary_type=2))
    struct.pack_into('<II', data, 16, 2, 464)  # two load commands, 232 bytes each
    segment = 32 + 232
    struct.pack_into('<II', data, segment, 0x19, 232)
    struct.pack_into('<I', data, segment + 64, 2)
    at = segment + 152
    data[at:at + 16] = b'__common'.ljust(16, b'\0')
    data[at + 16:at + 32] = b'__DATA'.ljust(16, b'\0')
    struct.pack_into('<QQI', data, at + 32, 0x100003000, 0x200, 0)
    getter = 0x100000560
    storage = 0x100003020
    struct.pack_into('<i', data, 0x560 + 14, storage - (getter + 18))
    binary = tmp_path / 'RobloxPlayer'
    binary.write_bytes(data)
    catalog = scan_binary(ClientBinary('macos', 'macOS x86', binary))
    assert 'DFStringSomeUncertainNumber' in catalog.verified
    assert catalog.flags['DFStringSomeUncertainNumber'] is None
    string = next(
        r for r in catalog.registrations if r.name == 'SomeUncertainNumber'
        and r.kind == 'string'
    )
    assert string.raw_initial_u64 is None
    assert string.initial_value is None


def test_real_macos_string_flag_recovery_works_without_other_clients_if_present() -> None:
    builds = [
        ('x86-64', Path.home() / (
            '.local/share/nutmeg/versions/version-8001f8762ae44efe-x86_64/'
            'Roblox.app/Contents/MacOS/RobloxPlayer'
        ), 750, 748),
        ('ARM64', Path('/tmp/roblox-arm64-player'), 760, 675),
    ]
    available = [(name, path, minimum, defaults) for name, path, minimum, defaults in builds
                 if path.is_file()]
    if not available:
        pytest.skip('No independently installed macOS Player reference binaries')
    for name, path, minimum, defaults in available:
        catalog = scan_binary(ClientBinary('macos', f'macOS {name}', path))
        strings = {key: value for key, value in catalog.flags.items()
                   if key.startswith(('FString', 'DFString'))}
        assert len(strings) >= minimum
        assert sum(value is not None for value in strings.values()) >= defaults
        assert all(key in catalog.verified for key in strings)
        assert strings['FStringLuaErrorsInfluxSeries'] == 'LuaShellErrors'
        assert strings['DFStringTelemetryV2Url'] == 'https://ecsv2.roblox.com/client/pbe'
        assert strings['FStringAdUnitDefaultNames'] == ''
