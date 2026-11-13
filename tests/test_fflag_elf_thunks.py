"""Static x86-64 Android FastFlag thunk extraction regression tests."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fleasion.modifications import fflag_static
from fleasion.modifications.fflag_elf_thunks import (
    X86Registration,
    extract_x86_registration_thunks,
)
from fleasion.modifications.fflag_static import ClientBinary, scan_binary
from fleasion.utils.linux_clients import (
    CORDIAL_CLIENT,
    MOCKTAIL_CLIENT,
    NIGHTCAP_CLIENT,
    LinuxClientDescriptor,
    LinuxClientInstallation,
)


def _rip_disp(instruction_va: int, length: int, target: int) -> bytes:
    return struct.pack('<i', target - instruction_va - length)


def _fixture() -> tuple[bytes, dict[str, tuple[int, int, int]]]:
    """A small read-only synthetic ELF layout with nonidentity file offsets."""
    data = bytearray(0x3000)
    sections = {
        '.text': (0x100000, 0x400, 0x800),
        '.rodata': (0x200000, 0x1400, 0x300),
        '.bss': (0x500000, 0, 0x400),
        '.data': (0x600000, 0x1a00, 0x200),
    }
    text_va, text_offset, _ = sections['.text']
    rodata_va, rodata_offset, _ = sections['.rodata']
    registry_va = 0x600040
    helper_va = text_va + 0x780
    name_source_va = 0x5000A0

    def literal(offset: int, name: str) -> int:
        raw = name.encode('ascii')
        data[rodata_offset + offset:rodata_offset + offset + len(raw) + 1] = raw + b'\0'
        return rodata_va + offset

    boolean_va = literal(0x20, 'DebugDrawBroadPhaseAABBs')
    int_va = literal(0x70, 'TaskSchedulerTargetFps')
    log_va = literal(0xB0, 'LuaErrorsInfluxSeries')
    short_va = literal(0xE0, 'VR')
    short_source_va = 0x5000D0

    def initializer(offset: int, name_va: int, destination: int, rex: int) -> None:
        va = text_va + offset
        code = (
            bytes((rex, 0x8D, 0x05)) + _rip_disp(va, 7, name_va)
            + bytes((rex, 0x89, 0x05)) + _rip_disp(va + 7, 7, destination)
        )
        data[text_offset + offset:text_offset + offset + len(code)] = code

    initializer(0x20, int_va, name_source_va, 0x4C)

    # Interleaved RIP-relative instructions preserve the RSI name pointer
    # across unrelated RDI work before it is written into the BSS name slot
    def instruction(offset: int, rex: int, opcode: int, modrm: int, target: int) -> bytes:
        va = text_va + offset
        return bytes((rex, opcode, modrm)) + _rip_disp(va, 7, target)

    chain = (
        instruction(0x40, 0x48, 0x8D, 0x35, short_va)
        + instruction(0x47, 0x48, 0x8D, 0x3D, boolean_va)
        + instruction(0x4E, 0x48, 0x89, 0x3D, 0x5000E0)
        + instruction(0x55, 0x48, 0x89, 0x35, short_source_va)
    )
    data[text_offset + 0x40:text_offset + 0x40 + len(chain)] = chain

    def thunk(
        offset: int, name_va: int, *, indirect: bool, variant: int, type_id: int
    ) -> None:
        va = text_va + offset
        code = (
            b'\x48\x8b\x3d' + _rip_disp(va, 7, registry_va)
            + b'\x48' + (b'\x8b' if indirect else b'\x8d') + b'\x35'
            + _rip_disp(va + 7, 7, name_va)
            + b'\x48\x8d\x15' + _rip_disp(va + 14, 7, 0x500100 + offset)
            + b'\xb9' + struct.pack('<I', variant)
            + b'\x41\xb8' + struct.pack('<I', type_id)
            + b'\xe9' + _rip_disp(va + 32, 5, helper_va)
        )
        assert len(code) == 37
        data[text_offset + offset:text_offset + offset + len(code)] = code

    thunk(0x100, boolean_va, indirect=False, variant=2, type_id=4)
    thunk(0x140, name_source_va, indirect=True, variant=2, type_id=3)
    thunk(0x180, log_va, indirect=False, variant=1, type_id=2)
    thunk(0x1C0, short_source_va, indirect=True, variant=1, type_id=1)
    return bytes(data), sections


def test_x86_64_thunks_recover_names_types_and_namespaces_without_running() -> None:
    data, sections = _fixture()
    actual = extract_x86_registration_thunks(data, sections)
    assert set(actual) == {
        X86Registration('DebugDrawBroadPhaseAABBs', 2, 4),
        X86Registration('TaskSchedulerTargetFps', 2, 3),
        X86Registration('LuaErrorsInfluxSeries', 1, 2),
        X86Registration('VR', 1, 1),
    }


def test_x86_thunks_reject_invalid_type_and_out_of_range_initializer() -> None:
    data, sections = _fixture()
    changed = bytearray(data)
    # The numeric thunk's type ID is at byte +28 within its 37-byte body
    struct.pack_into('<I', changed, sections['.text'][1] + 0x140 + 28, 99)
    assert X86Registration('TaskSchedulerTargetFps', 2, 3) not in (
        extract_x86_registration_thunks(bytes(changed), sections)
    )
    sections['.bss'] = (0x800000, 0, 0x400)
    assert extract_x86_registration_thunks(data, sections) == []


@pytest.mark.parametrize('descriptor', [MOCKTAIL_CLIENT, NIGHTCAP_CLIENT, CORDIAL_CLIENT],
                         ids=['mocktail', 'nightcap', 'cordial'])
def test_platform_detects_native_android_client_payload(
    descriptor: LinuxClientDescriptor,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = descriptor.paths(home=tmp_path)
    installation = LinuxClientInstallation(client=descriptor, paths=paths, executable=None)
    selected = paths.data_root / ('builds' if installation.key == 'cordial' else 'payloads')
    # A Nightcap Flatpak puts payloads in data/mocktail/payloads, while
    # native Mocktail puts them in ~/.local/share/mocktail/payloads
    if installation.key == 'nightcap':
        selected = paths.flatpak_root / 'data' / 'mocktail' / 'payloads'
    elif installation.key == 'mocktail':
        selected = paths.flatpak_root / 'payloads'
    path = selected / '2.747.0.1' / 'libroblox.so'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'ELF Test')
    def selected_installation() -> LinuxClientInstallation:
        return installation

    monkeypatch.setattr(
        'fleasion.utils.platform_linux.get_selected_linux_client_installation',
        selected_installation,
    )
    detected = fflag_static._detect_linux_binary()  # pyright: ignore[reportPrivateUsage]
    assert detected is not None
    assert detected.path == path
    assert detected.client == installation.key


@pytest.mark.parametrize('build', ['2998-ade08266c67aee88ec9c1d00902150e1684dad3a',
                                   '3222-a67a91ef96c700c0730acd36a250c2f93cfafbc1'])
def test_real_mocktail_offline_type_recovery_if_fixture_present(build: str) -> None:
    binary = Path.home() / '.local/share/mocktail/payloads' / build / 'libroblox.so'
    if not binary.is_file():
        pytest.skip(f'Mocktail Player binary {build} is unavailable')
    catalog = scan_binary(ClientBinary('mocktail', 'Mocktail', binary), {
        'FLogLuaErrorsInfluxSeries': None,
    })
    assert catalog.records > 13_000
    assert len(catalog.verified) > 12_000
    assert 'DFIntTaskSchedulerTargetFps' in catalog.verified
    assert 'DFFlagDebugDrawBroadPhaseAABBs' in catalog.verified
    assert 'DFIntCollisionScalingDefaultStudTenths' in catalog.verified
    assert 'FLogVR' in catalog.verified
    assert any(rec.name == 'VR' and rec.verified_type for rec in catalog.registrations)
    assert 'DFLogWriteOnlyBuffer' in catalog.verified
    assert 'FStringLuaErrorsInfluxSeries' in catalog.verified
    assert 'FLogLuaErrorsInfluxSeries' not in catalog.flags
