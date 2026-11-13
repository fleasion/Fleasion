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


def test_x86_compiled_initializers_require_matching_constructor_reference() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    data, sections = _fixture()
    image = bytearray(data)
    code_va, code_off, _code_size = sections['.text']

    def initializer(offset: int, storage: int, thunk: int, value: int) -> None:
        start = code_va + offset
        code = (
            b'\xc7\x05' + _rip_disp(start, 10, storage) + struct.pack('<i', value)
            + b'\x48\x8d\x05' + _rip_disp(start + 10, 7, thunk)
        )
        image[code_off + offset:code_off + offset + len(code)] = code

    initializer(0x310, 0x500240, code_va + 0x140, 67)
    initializer(0x360, 0x500300, code_va + 0x200, 12345)  # Unrelated write
    registrations = extract_x86_registration_thunks(bytes(image), sections)
    values = extract_x86_compiled_initializers(bytes(image), sections, registrations)
    assert values == {('TaskSchedulerTargetFps', 2, 3): '67'}
    # An immediate write without a reference to the exact thunk is not evidence
    tampered = bytearray(image)
    tampered[code_off + 0x31A:code_off + 0x31D] = b'\x90' * 3
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        bytes(tampered), sections, extract_x86_registration_thunks(bytes(tampered), sections)
    )


def test_x86_compiled_initializer_follows_adjacent_register_literal() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    image = bytearray(original)
    base, off, _size = sections['.text']
    location = base + 0x310
    instructions = (
        b'\xb8\x0f\x00\x00\x00'  # mov eax, 15
        + b'\x89\x05' + _rip_disp(location + 5, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(location + 11, 7, base + 0x140)
    )
    image[off + 0x310:off + 0x310 + len(instructions)] = instructions
    raw = bytes(image)
    registration = extract_x86_registration_thunks(raw, sections)
    assert extract_x86_compiled_initializers(raw, sections, registration) == {
        ('TaskSchedulerTargetFps', 2, 3): '15',
    }
    image[off + 0x310] = 0x90
    assert extract_x86_compiled_initializers(
        bytes(image), sections, extract_x86_registration_thunks(bytes(image), sections)
    ) == {}


def test_x86_adjacent_register_zero_initializers_are_detected() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    binary = bytearray(original)
    base, off, _length = sections['.text']
    for location, instruction, storage, thunk in (
        (0x300, b'\x45\x31\xe4\x44\x88\x25', 0x500200, base + 0x100),
        (0x340, b'\x31\xc0\x89\x05', 0x500240, base + 0x140),
    ):
        xor_len = 3 if location == 0x300 else 2
        store_len = len(instruction) - xor_len + 4
        start = base + location + xor_len
        code = (
            instruction + _rip_disp(start, store_len, storage)
            + b'\x48\x8d\x05' + _rip_disp(start + store_len, 7, thunk)
        )
        binary[off + location:off + location + len(code)] = code
    content = bytes(binary)
    registrations = extract_x86_registration_thunks(content, sections)
    assert extract_x86_compiled_initializers(content, sections, registrations) == {
        ('DebugDrawBroadPhaseAABBs', 2, 4): 'False',
        ('TaskSchedulerTargetFps', 2, 3): '0',
    }
    # A different XOR register does not establish the store's value
    binary[off + 0x340 + 1] = 0xC9
    values = extract_x86_compiled_initializers(
        bytes(binary), sections, extract_x86_registration_thunks(bytes(binary), sections)
    )
    assert ('TaskSchedulerTargetFps', 2, 3) not in values
    assert values[('DebugDrawBroadPhaseAABBs', 2, 4)] == 'False'


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
    # These exact, installed Mocktail builds include ELF-only constructors
    # that keep a read-only string in a callee-saved register and genuine
    # zero initializers after a canonical call/cleanup boundary. Values must
    # come from this particular binary, not a cross-client default table.
    expected_minimum = 14_415 if build.startswith('3222-') else 13_720
    assert len(catalog.verified) == len(catalog.flags)
    assert sum(value is not None for value in catalog.flags.values()) >= expected_minimum
    assert catalog.flags['DFStringHttpServiceInternalUrlWhitelist'] == ''
    assert catalog.flags['DFLogRbxTransportQuicServerSocket'] == '1030'
    assert catalog.flags['FLogPhysicsStepsPerSecond'] == '768'
    assert catalog.flags['DFFlagHttp3Backoff'] == 'False'
    assert catalog.flags['DFIntCurlFtsSoSndBuf'] == '0'
    assert catalog.flags['DFIntReportCacheDirSizesHundredthsPercent'] == '0'
    assert catalog.flags['FFlagAudioStagedSerialQueue'] == 'False'
    assert catalog.flags['FFlagAudioLockFreeTimekeeper'] == 'False'
    assert catalog.flags['DFFlagDebugDetailedSoundMemoryStats'] == 'False'
    assert catalog.flags['DFFlagEnableDynamicReloaderV2Telemetry'] == 'False'
    assert catalog.flags['DFIntDebugPannerUpdateNumerator'] == '1'
    assert catalog.flags['FIntAudioEmitterPannerMinDistanceDenominator'] == '1'
    if build.startswith('3222-'):
        assert catalog.flags['FFlagDebugAvatarTracking'] == 'False'
        assert catalog.flags['DFIntConnectionClosedPointsThrottleHundredthsPercent'] == '0'
    else:
        assert catalog.flags['FFlagUserVRVehicleCameraOrbital'] == 'False'
        assert catalog.flags['DFLogSessionCrypto'] == '768'
        assert catalog.flags['FLogJsonRpcVerbose'] == '768'
        assert catalog.flags['DFFlagSDFTextCanvasGroup'] == 'False'
        assert catalog.flags['DFStringSetUdmuxEndpointIp'] == ''
    assert 'DFIntCollisionScalingDefaultStudTenths' in catalog.verified
    assert 'FLogVR' in catalog.verified
    assert any(rec.name == 'VR' and rec.verified_type for rec in catalog.registrations)
    assert 'DFLogWriteOnlyBuffer' in catalog.verified
    assert 'FStringLuaErrorsInfluxSeries' in catalog.verified
    assert catalog.flags['FStringLuaErrorsInfluxSeries'] == 'LuaShellErrors'
    extracted = sum(value is not None for value in catalog.flags.values())
    assert extracted >= 12_900
    if build.startswith('3222-'):
        assert extracted >= 13_600
        assert catalog.flags['DFFlagTextChatMessageReceivedTypeSend'] == 'False'
    if binary.name == 'libroblox.so':
        assert catalog.flags['FLogCrash'] == '769'
        assert catalog.flags['DFLogInstanceStitchingLog'] == '65286'
    assert catalog.flags['DFIntS2PhysicsSenderRate'] == '15'
    assert sum(value is not None for value in catalog.flags.values()) > 5_000
    assert 'FLogLuaErrorsInfluxSeries' not in catalog.flags


def test_x86_long_constant_trace_preserves_only_understood_instructions() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    code = bytearray(original)
    base, off, _size = sections['.text']
    address = base + 0x310
    # A constant survives a harmless LEA and an unrelated pointer store
    instructions = (
        b'\xb8\x43\x00\x00\x00'
        + b'\x48\x8d\x0d' + _rip_disp(address + 5, 7, 0x500310)
        + b'\x48\x89\x0d' + _rip_disp(address + 12, 7, 0x500318)
        + b'\x89\x05' + _rip_disp(address + 19, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(address + 25, 7, base + 0x140)
    )
    code[off + 0x310:off + 0x310 + len(instructions)] = instructions
    contents = bytes(code)
    registrations = extract_x86_registration_thunks(contents, sections)
    assert extract_x86_compiled_initializers(contents, sections, registrations) == {
        ('TaskSchedulerTargetFps', 2, 3): '67',
    }
    # A genuine intervening LEA into the source register invalidates the value
    modified = bytearray(code)
    modified[off + 0x310 + 5:off + 0x310 + 8] = b'\x48\x8d\x05'
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        bytes(modified), sections,
        extract_x86_registration_thunks(bytes(modified), sections),
    )


def test_x86_long_trace_rejects_immediate_bytes_inside_other_instructions() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    code = bytearray(original)
    base, off, _size = sections['.text']
    address = base + 0x310
    # B8 is inside the LEA displacement, not an instruction boundary
    instructions = (
        b'\x48\x8d\x0d\xb8\x15\x00\x00'
        + b'\x48\x89\x0d' + _rip_disp(address + 7, 7, 0x500318)
        + b'\x89\x05' + _rip_disp(address + 14, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(address + 20, 7, base + 0x140)
    )
    code[off + 0x310:off + 0x310 + len(instructions)] = instructions
    contents = bytes(code)
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        contents, sections, extract_x86_registration_thunks(contents, sections),
    )
    # A REX prefix changes the destination register: R8D is not EAX
    rex_code = bytearray(original)
    instructions = (
        b'\x41\xb8\x43\x00\x00\x00'
        + b'\x89\x05' + _rip_disp(address + 6, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(address + 12, 7, base + 0x140)
    )
    rex_code[off + 0x310:off + 0x310 + len(instructions)] = instructions
    contents = bytes(rex_code)
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        contents, sections, extract_x86_registration_thunks(contents, sections),
    )


def test_x86_16bit_log_initializers_are_unsigned() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    code = bytearray(original)
    base, off, _size = sections['.text']
    address = base + 0x310
    instructions = (
        b'\x66\xc7\x05' + _rip_disp(address, 9, 0x5002C0) + b'\x06\xff'
        + b'\x48\x8d\x05' + _rip_disp(address + 9, 7, base + 0x1C0)
    )
    code[off + 0x310:off + 0x310 + len(instructions)] = instructions
    contents = bytes(code)
    assert extract_x86_compiled_initializers(
        contents, sections, extract_x86_registration_thunks(contents, sections),
    ) == {('VR', 1, 1): '65286'}


def test_elf_cstring_constructor_matches_an_actual_typed_thunk_storage() -> None:
    from fleasion.modifications.fflag_elf_strings import (  # noqa: PLC0415
        extract_elf_cstring_initializers,
    )

    original, sections = _fixture()
    image = bytearray(original)
    code_va, code_off, _ = sections['.text']
    rodata_va, rodata_off, _ = sections['.rodata']
    name = b'LuaShellErrors\0'
    image[rodata_off + 0x180:rodata_off + 0x180 + len(name)] = name
    ctor_va = code_va + 0x680
    ctor = bytes.fromhex('554889e5415741564154534989f74889fb4889f7e8')
    image[code_off + 0x680:code_off + 0x680 + len(ctor)] = ctor
    # The string-thunk's value slot is separate from the name stored in rodata.
    destination = 0x500100 + 0x180
    instruction_va = code_va + 0x300
    initialization = (
        b'\x48\x8d\x3d' + _rip_disp(instruction_va, 7, destination)
        + b'\x48\x8d\x35' + _rip_disp(instruction_va + 7, 7, rodata_va + 0x180)
        + b'\xe8' + _rip_disp(instruction_va + 14, 5, ctor_va)
        + b'\x48\x8d\x05' + _rip_disp(instruction_va + 19, 7, code_va + 0x180)
    )
    image[code_off + 0x300:code_off + 0x300 + len(initialization)] = initialization
    data = bytes(image)
    regs = extract_x86_registration_thunks(data, sections)
    string_slots = {r.storage_va for r in regs if r.type_id == 2 and r.storage_va is not None}
    assert string_slots == {destination}
    assert extract_elf_cstring_initializers(data, sections, string_slots) == {
        destination: 'LuaShellErrors',
    }
    # A true string literal is not enough without the corroborated constructor
    image[code_off + 0x680] ^= 0x01
    assert extract_elf_cstring_initializers(bytes(image), sections, string_slots) == {}


def test_x86_register_trace_preserves_abi_saved_register_across_calls() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    data = bytearray(original)
    base, offset, _size = sections['.text']
    start = base + 0x350
    # r12 is callee saved under the Linux x86-64 System V ABI.
    instructions = (
        b'\x41\xbc\x00\x00\x00\x00'  # mov r12d, 0
        + b'\xe8' + _rip_disp(start + 6, 5, base + 0x640)
        + b'\x44\x88\x25' + _rip_disp(start + 11, 7, 0x500200)
        + b'\x48\x8d\x05' + _rip_disp(start + 18, 7, base + 0x100)
    )
    data[offset + 0x350:offset + 0x350 + len(instructions)] = instructions
    image = bytes(data)
    registrations = extract_x86_registration_thunks(image, sections)
    values = extract_x86_compiled_initializers(image, sections, registrations)
    assert values == {('DebugDrawBroadPhaseAABBs', 2, 4): 'False'}
    # RAX is caller-saved; the same proof cannot cross even a direct call.
    changed = bytearray(original)
    start = base + 0x350
    caller_saved = (
        b'\xb8\x43\x00\x00\x00'
        + b'\xe8' + _rip_disp(start + 5, 5, base + 0x640)
        + b'\x89\x05' + _rip_disp(start + 10, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(start + 16, 7, base + 0x140)
    )
    changed[offset + 0x350:offset + 0x350 + len(caller_saved)] = caller_saved
    image = bytes(changed)
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        image, sections, extract_x86_registration_thunks(image, sections),
    )


def test_x86_constructor_real_constant_not_rejected_by_fake_overlapping_decode() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    code = bytearray(original)
    base, off, _ = sections['.text']
    at = base + 0x300
    # The preceding store's displacement contains B9 05. If a decoder
    # starts three bytes into it, the MOV itself looks like part of a
    # spurious instruction. The real store ends precisely at MOV EAX.
    preceding = b'\x48\x89\x0d\xdd\x4d\xb9\x05'
    sequence = (
        preceding
        + b'\xb8\xff\xff\xff\xff'
        + b'\x89\x05' + _rip_disp(at + 12, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(at + 18, 7, base + 0x140)
    )
    code[off + 0x300:off + 0x300 + len(sequence)] = sequence
    contents = bytes(code)
    assert extract_x86_compiled_initializers(
        contents, sections, extract_x86_registration_thunks(contents, sections),
    ) == {('TaskSchedulerTargetFps', 2, 3): '-1'}


def test_x86_trace_skips_only_sse_ops_not_general_register_writes() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )
    original, sections = _fixture()
    image = bytearray(original)
    base, off, _ = sections['.text']
    at = base + 0x340
    code = (
        b'\x45\x31\xf6'  # xor r14d, r14d
        + b'\x0f\x57\xc0'  # xorps xmm0, xmm0
        + b'\x0f\x11\x05' + _rip_disp(at + 6, 7, 0x500380)  # movups [rip], xmm0
        + b'\x44\x88\x35' + _rip_disp(at + 13, 7, 0x500200)
        + b'\x48\x8d\x05' + _rip_disp(at + 20, 7, base + 0x100)
    )
    image[off + 0x340:off + 0x340 + len(code)] = code
    data = bytes(image)
    assert extract_x86_compiled_initializers(
        data, sections, extract_x86_registration_thunks(data, sections),
    ) == {('DebugDrawBroadPhaseAABBs', 2, 4): 'False'}
    # MOVZX r14d, r14b overwrites the tracked GPR and is not a safe SSE step.
    image[off + 0x340 + 3:off + 0x340 + 6] = b'\x45\x0f\xb6'
    data = bytes(image)
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in extract_x86_compiled_initializers(
        data, sections, extract_x86_registration_thunks(data, sections),
    )


def test_x86_conditional_guard_accepts_only_both_preserved_branches() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    base, off, _ = sections['.text']
    site = base + 0x300
    # The branch either jumps directly to the store or executes a guarded
    # initializer that touches only caller-saved registers and memory
    prefix = (
        b'\x45\x31\xf6'  # xor r14d, r14d: literal seed
        + b'\x75\x0a'  # jne after the 10-byte fallthrough block
        + b'\xbf\x28\x00\x00\x00'  # mov edi, 40
        + b'\xe8' + _rip_disp(site + 10, 5, base + 0x600)  # call; preserves r14
    )
    assert len(prefix) == 15
    store = (
        b'\x44\x88\x35' + _rip_disp(site + 15, 7, 0x500200)
        + b'\x48\x8d\x05' + _rip_disp(site + 22, 7, base + 0x100)
    )
    assert len(store) == 14
    image = bytearray(original)
    image[off + 0x300:off + 0x300 + len(prefix + store)] = prefix + store
    content = bytes(image)
    assert extract_x86_compiled_initializers(
        content, sections, extract_x86_registration_thunks(content, sections),
    ) == {('DebugDrawBroadPhaseAABBs', 2, 4): 'False'}

    # A real source overwrite on the fallthrough path makes the result
    # path-dependent, so the candidate must remain unknown
    changed = bytearray(image)
    changed[off + 0x300 + 5:off + 0x300 + 15] = (
        b'\x41\xbe\x01\x00\x00\x00' + b'\x90' * 4
    )
    content = bytes(changed)
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in extract_x86_compiled_initializers(
        content, sections, extract_x86_registration_thunks(content, sections),
    )
    # Nested/unknown control-flow in the guarded block also fails closed
    changed = bytearray(image)
    changed[off + 0x300 + 5] = 0xEB
    content = bytes(changed)
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in extract_x86_compiled_initializers(
        content, sections, extract_x86_registration_thunks(content, sections),
    )


def test_x86_conditional_guard_rejects_indirect_and_too_long_branches() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        safe_x86_preserving_step,
    )

    # Direct conditional forward branches cannot be assumed harmless simply
    # because the taken path skips some instructions
    code = memoryview(b'\x75\x06\x41\xbe\x01\x00\x00\x00\x90')
    assert safe_x86_preserving_step(code, 0, 14) is None
    # A branch with a large unknown block or a backward jump is not traced
    assert safe_x86_preserving_step(memoryview(b'\x75\xfe'), 0, 14) is None
    assert safe_x86_preserving_step(memoryview(b'\x75\x7f' + b'\x90' * 128), 0, 14) == 129
    assert safe_x86_preserving_step(memoryview(b'\x0f\x85\xff\x00\x00\x00' + b'\x90' * 256), 0, 14) is None


def test_x86_register_zero_seed_after_canonical_cleanup_call_is_aligned() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
        extract_x86_compiled_initializers,
    )

    image, sections = _fixture()
    data = bytearray(image)
    base, off, _size = sections['.text']
    site = base + 0x300
    # This canonical call-tail pattern is seen in Android Clang builds.
    # A byte inside call rel32's displacement looks like an overlapping
    # instruction and previously hid the real XOR zero definition.
    head = bytes.fromhex('4889c7e8e89b18fc31c0')
    between = b'\x48\x8d\x3d' + _rip_disp(site + len(head), 7, base + 0x380)
    store_at = site + len(head) + len(between)
    store = b'\x88\x05' + _rip_disp(store_at, 6, 0x500200)
    ref_at = store_at + len(store)
    ref = b'\x48\x8d\x05' + _rip_disp(ref_at, 7, base + 0x100)
    body = head + between + store + ref
    data[off + 0x300:off + 0x300 + len(body)] = body
    raw = bytes(data)
    code = memoryview(raw)[off:off + sections['.text'][2]]
    assert not _seed_inside_instruction(code, 0x308)
    values = extract_x86_compiled_initializers(
        raw, sections, extract_x86_registration_thunks(raw, sections),
    )
    assert values['DebugDrawBroadPhaseAABBs', 2, 4] == 'False'

    invalid = bytearray(data)
    invalid[off + 0x308:off + 0x30A] = b'\x31\xc9'  # XOR ECX,ECX (not AL)
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in extract_x86_compiled_initializers(
        bytes(invalid), sections,
        extract_x86_registration_thunks(bytes(invalid), sections),
    )


def test_x86_cleanup_call_does_not_prove_unrelated_moved_immediate() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
    )

    cases = (
        (bytes.fromhex('4889c7e8e89b18fc31c0'), False),
        (bytes.fromhex('c3e8b8edbc0431c0'), False),
        (bytes.fromhex('c3e8b8edbc04b900030000'), True),
        (bytes.fromhex('4889c7e8e89b18fc31c9'), False),
    )
    for instruction, embedded in cases:
        content = memoryview(b'\x90' * 20 + instruction + b'\x90' * 16)
        seed = instruction.find(b'\x31')
        if seed < 0:
            seed = instruction.find(b'\xb9')
        assert _seed_inside_instruction(content, 20 + seed) == embedded


def _x86_atomic_registry_guard() -> bytes:
    """An x86-64 atomic registration-slot retry loop from an Android build.

    It has three exits and a backward retry. Only the index/accumulator and
    memory change; R14 holds a compile-time constant throughout all paths.
    """
    return bytes.fromhex(
        '77 3c '  # ja after allocator; the initial guard
        'f0 0f b1 0d 16 39 79 05 '  # lock cmpxchg [rip+disp], ecx
        '74 10 '  # je successful update
        '89 c1 '  # mov ecx, eax
        'ff c1 '  # inc ecx
        '81 f9 00 04 00 00 '  # cmp ecx, 0x400
        '72 ea '  # jb retry atomic update
        '31 d2 '  # xor edx, edx
        'eb 22 '  # jmp after allocator
        '89 c8 '  # mov eax, ecx
        '48 c1 e0 04 '  # shl rax, 4
        '48 8d 15 e7 78 78 05 '  # lea rdx, [rip+disp]
        '48 8d 35 8f 24 11 fe '  # lea rsi, [rip+disp]
        '48 89 34 10 '  # mov [rax+rdx], rsi (SIB)
        'c7 44 10 08 00 00 00 00 '  # mov dword [rax+rdx+8], 0
        '89 ca '  # mov edx, ecx
    )


def test_x86_atomic_guard_accepts_only_all_register_preserving_paths() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        safe_x86_preserving_step,
    )

    guard = _x86_atomic_registry_guard()
    assert len(guard) == 62
    assert safe_x86_preserving_step(memoryview(guard), 0, 14) == len(guard)
    # CMPXCHG implicitly modifies EAX on the failure path, so a value
    # supposedly held in EAX cannot be carried through this block.
    assert safe_x86_preserving_step(memoryview(guard), 0, 0) is None

    # Every reachable path, not just the fallthrough, must preserve R14.
    clobbered = bytearray(guard)
    clobbered[12:14] = b'\x41\x5e'  # POP R14, same size as MOV ECX,EAX
    assert safe_x86_preserving_step(memoryview(clobbered), 0, 14) is None

    # Unknown atomic instructions fail closed, as do out-of-block edges.
    unsupported = bytearray(guard)
    unsupported[3:5] = b'\x0f\xa1'  # not CMPXCHG
    assert safe_x86_preserving_step(memoryview(unsupported), 0, 14) is None
    escaping = bytearray(guard)
    escaping[1] = 0x40  # initial JA skips past the verified join
    assert safe_x86_preserving_step(memoryview(escaping), 0, 14) is None
    inner_escape = bytearray(guard)
    inner_escape[11] = 0x7F  # inner JE exits beyond the join
    assert safe_x86_preserving_step(memoryview(inner_escape), 0, 14) is None
    loop_escape = bytearray(guard)
    loop_escape[23] = 0x80  # JB branches back before the bounded block
    assert safe_x86_preserving_step(memoryview(loop_escape), 0, 14) is None


def test_x86_atomic_allocator_preserves_a_compiled_false_default() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    data = bytearray(original)
    base, offset, _size = sections['.text']
    start = base + 0x300
    prefix = (
        b'\x45\x31\xf6'  # xor r14d, r14d
        + b'\xba\x00\x00\x00\x00'  # mov edx, 0
        + b'\x81\xf9\xff\x03\x00\x00'  # cmp ecx, 0x3ff
        + _x86_atomic_registry_guard()
    )
    store_at = start + len(prefix)
    code = (
        prefix
        + b'\x44\x88\x35' + _rip_disp(store_at, 7, 0x500200)
        + b'\x48\x8d\x05' + _rip_disp(store_at + 7, 7, base + 0x100)
    )
    data[offset + 0x300:offset + 0x300 + len(code)] = code
    content = bytes(data)
    regs = extract_x86_registration_thunks(content, sections)
    assert extract_x86_compiled_initializers(content, sections, regs) == {
        ('DebugDrawBroadPhaseAABBs', 2, 4): 'False',
    }

    # On the atomic failure path, overwriting R14 invalidates the proof.
    changed = bytearray(data)
    at = offset + 0x300 + 3 + 5 + 6 + 12
    changed[at:at + 2] = b'\x41\x5e'  # pop r14
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in extract_x86_compiled_initializers(
        bytes(changed), sections,
        extract_x86_registration_thunks(bytes(changed), sections),
    )


def test_x86_preserving_step_decodes_sib_displacements_exactly() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _memory_end,  # pyright: ignore[reportPrivateUsage]
        safe_x86_preserving_step,
    )

    # RIP-less SIB base=5 with mod=00 consumes disp32; mode=01 consumes
    # disp8, and mode=10 consumes disp32 after the SIB byte.
    assert _memory_end(memoryview(b'%'), 0, 0x04) == 5
    assert _memory_end(memoryview(b''), 0, 0x44) == 2
    assert _memory_end(memoryview(b''), 0, 0x84) == 5
    assert _memory_end(memoryview(b'%'), 0, 0x04) is None
    assert _memory_end(memoryview(b''), 0, 0x44) is None
    assert _memory_end(memoryview(b''), 0, 0xC4) is None

    # SIB-addressed stores are side effects on memory, not the base GPR.
    mov = memoryview(bytes.fromhex('48 89 34 10'))
    assert safe_x86_preserving_step(mov, 0, 14) == len(mov)
    immediate = memoryview(bytes.fromhex('c7 44 10 08 00 00 00 00'))
    assert safe_x86_preserving_step(immediate, 0, 14) == len(immediate)
    # An explicit write to R14 invalidates the same preservation proof.
    changed = memoryview(bytes.fromhex('49 c1 e6 04'))  # shl r14, 4
    assert safe_x86_preserving_step(changed, 0, 14) is None


@pytest.mark.parametrize(
    ('seed', 'store_opcode', 'expected'),
    [
        ('b0 01', '88 05', 'True'),                 # MOV AL,1; store AL
        ('40 b6 01', '40 88 35', 'True'),           # MOV SIL,1; store SIL
        ('41 b5 01', '44 88 2d', 'True'),           # MOV R13B,1; store R13B
        ('b4 01', '88 05', None),                   # MOV AH,1 does not set AL
        ('b0 01 b0 00', '88 05', 'False'),          # Later byte move replaces AL
        ('b0 01 b8 00 00 00 00', '88 05', 'False'),  # Whole-register overwrite
    ],
)
def test_x86_single_byte_literal_seed_has_exact_register_alias(
    seed: str, store_opcode: str, expected: str | None,
) -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    source, sections = _fixture()
    image = bytearray(source)
    base, off, _size = sections['.text']
    address = base + 0x300
    initial = bytes.fromhex(seed)
    opcode = bytes.fromhex(store_opcode)
    store_site = address + len(initial)
    instruction = (
        initial
        + opcode + _rip_disp(store_site, len(opcode) + 4, 0x500200)
        + b'\x48\x8d\x05'
        + _rip_disp(store_site + len(opcode) + 4, 7, base + 0x100)
    )
    image[off + 0x300:off + 0x300 + len(instruction)] = instruction
    data = bytes(image)
    actual = extract_x86_compiled_initializers(
        data, sections, extract_x86_registration_thunks(data, sections),
    )
    assert actual.get(('DebugDrawBroadPhaseAABBs', 2, 4)) == expected


def test_x86_byte_literal_caller_saved_call_clobber_is_rejected() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        extract_x86_compiled_initializers,
    )

    source, sections = _fixture()
    base, off, _size = sections['.text']
    for seed, store_opcode, expected in (
        (bytes.fromhex('b0 01'), bytes.fromhex('88 05'), None),
        (bytes.fromhex('41 b5 01'), bytes.fromhex('44 88 2d'), 'True'),
    ):
        image = bytearray(source)
        site = base + 0x300
        call = b'\xe8' + _rip_disp(site + len(seed), 5, base + 0x780)
        at = site + len(seed) + len(call)
        code = (
            seed + call
            + store_opcode + _rip_disp(at, len(store_opcode) + 4, 0x500200)
            + b'\x48\x8d\x05'
            + _rip_disp(at + len(store_opcode) + 4, 7, base + 0x100)
        )
        image[off + 0x300:off + 0x300 + len(code)] = code
        data = bytes(image)
        values = extract_x86_compiled_initializers(
            data, sections, extract_x86_registration_thunks(data, sections),
        )
        assert values.get(('DebugDrawBroadPhaseAABBs', 2, 4)) == expected


def test_x86_safe_preservation_accepts_byte_moves_only_to_other_gprs() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        safe_x86_preserving_step,
    )

    assert safe_x86_preserving_step(memoryview(bytes.fromhex('b0 01')), 0, 13) == 2
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('b0 01')), 0, 0) is None
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('b4 01')), 0, 0) is None
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('40 b6 01')), 0, 6) is None
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('40 b6 01')), 0, 13) == 3
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('41 b5 01')), 0, 13) is None
    assert safe_x86_preserving_step(memoryview(bytes.fromhex('41 b5 01')), 0, 0) == 3
    # Reads from memory via CMP must be allowed; memory writes must not be
    # mistaken for a read-only comparison in the scalar preservation proof.
    assert safe_x86_preserving_step(
        memoryview(bytes.fromhex('48 83 7d a8 40')), 0, 13,
    ) == 5
    assert safe_x86_preserving_step(
        memoryview(bytes.fromhex('48 83 45 a8 40')), 0, 13,
    ) is None
    # Explicit R8D source of LOCK CMPXCHG is read, but implicit EAX is
    # written on failure, independently of the REX.R extension.
    atom = memoryview(bytes.fromhex('f0 44 0f b1 05 16 39 79 05'))
    assert safe_x86_preserving_step(atom, 0, 13) == len(atom)
    assert safe_x86_preserving_step(atom, 0, 0) is None


def test_x86_zero_seed_recovered_after_canonical_call_or_index_increment() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
    )

    cases = (
        (bytes.fromhex('48 89 02 e8 79 e8 ca ff 45 31 f6'), 8),
        (bytes.fromhex('8b 05 20 1b 89 05 8d 50 01 31 c9'), 9),
    )
    for sequence, offset in cases:
        code = memoryview(b'\x90' * 20 + sequence + b'\x90' * 20)
        assert not _seed_inside_instruction(code, 20 + offset)
    invalid = bytes.fromhex('48 89 02 e8 79 e8 ca ff 45 31 f5')
    code = memoryview(b'\x90' * 20 + invalid + b'\x90' * 20)
    # XOR R13D,R14D is not a zeroing seed for either register.
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _constant_register_seed,  # pyright: ignore[reportPrivateUsage]
    )
    assert _constant_register_seed(code, 28, 14, 1) is None
    assert _constant_register_seed(code, 28, 13, 1) is None


def test_x86_constructor_after_verified_ret_recovers_zero_only_at_real_boundary() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    base, off, _size = sections['.text']
    data = bytearray(original)
    start = base + 0x300
    previous = b'\x48\x89\x05' + _rip_disp(start, 7, 0x500300)
    seed = b'\x31\xc0'  # XOR EAX,EAX; a new adjacent initializer
    store_at = start + len(previous) + 1 + len(seed)
    code = (
        previous + b'\xc3' + seed
        + b'\x88\x05' + _rip_disp(store_at, 6, 0x500200)
        + b'\x48\x8d\x05' + _rip_disp(store_at + 6, 7, base + 0x100)
    )
    data[off + 0x300:off + 0x300 + len(code)] = code
    binary = bytes(data)
    view = memoryview(binary)[off:off + sections['.text'][2]]
    assert not _seed_inside_instruction(view, 0x308)
    assert extract_x86_compiled_initializers(
        binary, sections, extract_x86_registration_thunks(binary, sections),
    )['DebugDrawBroadPhaseAABBs', 2, 4] == 'False'

    # An aligned entrypoint cannot manufacture zero from XOR EAX,ECX.
    # The preceding function's RET is not itself evidence of the value.
    changed = bytearray(data)
    changed[off + 0x308:off + 0x30A] = b'\x31\xc8'
    result = extract_x86_compiled_initializers(
        bytes(changed), sections,
        extract_x86_registration_thunks(bytes(changed), sections),
    )
    assert ('DebugDrawBroadPhaseAABBs', 2, 4) not in result


def test_x86_logging_initializer_proves_ax_constant_after_stack_spill() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    base, off, _size = sections['.text']
    image = bytearray(original)
    site = base + 0x300
    prologue = b'\x48\x89\x45\xe8'  # MOV [RBP-0x18],RAX
    literal = b'\x66\xb8\x06\x04'  # MOV AX,0x0406
    at = site + len(prologue) + len(literal)
    instruction = (
        prologue + literal
        + b'\x66\x89\x05' + _rip_disp(at, 7, 0x5002c0)
        + b'\x48\x8d\x05' + _rip_disp(at + 7, 7, base + 0x1C0)
    )
    image[off + 0x300:off + 0x300 + len(instruction)] = instruction
    raw = bytes(image)
    view = memoryview(raw)[off:off + sections['.text'][2]]
    assert not _seed_inside_instruction(view, 0x304)
    assert extract_x86_compiled_initializers(
        raw, sections, extract_x86_registration_thunks(raw, sections),
    )['VR', 1, 1] == '1030'

    overwritten = bytearray(image)
    overwritten[off + 0x305] = 0xB9  # MOV CX,0x0406 does not initialize AX
    assert ('VR', 1, 1) not in extract_x86_compiled_initializers(
        bytes(overwritten), sections,
        extract_x86_registration_thunks(bytes(overwritten), sections),
    )


@pytest.mark.parametrize('boundary', ['call', 'return'])
def test_x86_logging_ax_literal_after_verified_call_or_return(boundary: str) -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    image = bytearray(original)
    base, off, size = sections['.text']
    start = base + 0x300
    if boundary == 'call':
        # MOV [RDX],RAX is a verified three-byte predecessor of CALL rel32.
        prior = b'\x48\x89\x02'
        preceding = prior + b'\xe8' + _rip_disp(start + len(prior), 5, base + 0x780)
    else:
        # The previous constructor ends in a complete RIP-store followed
        # by RET. Its displacement cannot be reused as an AX seed.
        prior = b'\x48\x89\x05' + _rip_disp(start, 7, 0x500300)
        preceding = prior + b'\xc3'
    seed_at = 0x300 + len(preceding)
    store_at = start + len(preceding) + 4
    binary = (
        preceding + b'\x66\xb8\x00\x03'  # MOV AX,0x300, real compiled level
        + b'\x66\x89\x05' + _rip_disp(store_at, 7, 0x5002c0)
        + b'\x48\x8d\x05' + _rip_disp(store_at + 7, 7, base + 0x1c0)
    )
    image[off + 0x300:off + 0x300 + len(binary)] = binary
    raw = bytes(image)
    code = memoryview(raw)[off:off + size]
    assert not _seed_inside_instruction(code, seed_at)
    assert extract_x86_compiled_initializers(
        raw, sections, extract_x86_registration_thunks(raw, sections),
    )['VR', 1, 1] == '768'

    # A different low-word destination must not be attributed to AX.
    changed = bytearray(image)
    changed[off + seed_at + 1] = 0xb9  # MOV CX,0x300
    modified = bytes(changed)
    assert ('VR', 1, 1) not in extract_x86_compiled_initializers(
        modified, sections, extract_x86_registration_thunks(modified, sections),
    )


def test_x86_fake_branch_inside_rip_displacement_cannot_bypass_seed() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_inside_instruction,  # pyright: ignore[reportPrivateUsage]
        _seed_may_be_skipped,  # pyright: ignore[reportPrivateUsage]
    )

    # The bytes 70 21 resemble JO +33, but are part of the displacement
    # in MOV [RIP+disp32],RCX. The preceding 5B resembles POP RBX but is
    # also inside the same instruction. Neither is an actual instruction.
    code = bytearray(b'\x90' * 70)
    code[0:7] = bytes.fromhex('48 89 0d 5b 70 21 05')
    code[33:36] = bytes.fromhex('45 31 f6')  # XOR R14D,R14D (real seed)
    code[36:43] = bytes.fromhex('44 89 35 00 00 00 00')
    view = memoryview(code)
    assert _seed_inside_instruction(view, 3)  # false POP at displacement byte
    assert _seed_inside_instruction(view, 4)  # false JO inside displacement
    assert not _seed_may_be_skipped(view, 33, 39, 14)


def test_x86_real_conditional_branch_still_invalidates_skippable_seed() -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _seed_may_be_skipped,  # pyright: ignore[reportPrivateUsage]
    )

    code = bytearray(b'\x90' * 40)
    code[1:3] = b'\x75\x08'  # Real JNZ to index 11, bypassing seed at 4
    code[4:7] = bytes.fromhex('45 31 f6')  # XOR R14D,R14D
    assert _seed_may_be_skipped(memoryview(code), 4, 13, 14)
    code[1] = 0x90  # No proven control-flow bypass anymore
    assert not _seed_may_be_skipped(memoryview(code), 4, 13, 14)


@pytest.mark.parametrize(
    'prior',
    [
        '89 15 5b 6f 21 05',  # MOV [RIP+disp32],EDX
        '48 89 0d 5b 6f 21 05',  # MOV [RIP+disp32],RCX
        'c7 05 5b 6f 21 05 18 72 31 bf',  # MOV dword [RIP+disp32],imm32
        '48 c7 05 5b 6f 21 05 18 72 31 bf',  # MOV qword [RIP+disp32],imm32
    ],
)
def test_x86_immediate_after_completed_rip_store_and_return(prior: str) -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _literal_after_complete_rip_store_ret,  # pyright: ignore[reportPrivateUsage]
        extract_x86_compiled_initializers,
    )

    original, sections = _fixture()
    base, off, size = sections['.text']
    image = bytearray(original)
    start = base + 0x300
    previous = bytes.fromhex(prior) + b'\xc3'
    seed_at = 0x300 + len(previous)
    site = start + len(previous) + 5
    initializer = (
        previous + b'\xb8\x32\x00\x00\x00'  # MOV EAX,50
        + b'\x89\x05' + _rip_disp(site, 6, 0x500240)
        + b'\x48\x8d\x05' + _rip_disp(site + 6, 7, base + 0x140)
    )
    image[off + 0x300:off + 0x300 + len(initializer)] = initializer
    raw = bytes(image)
    code = memoryview(raw)[off:off + size]
    assert _literal_after_complete_rip_store_ret(code, seed_at)
    assert extract_x86_compiled_initializers(
        raw, sections, extract_x86_registration_thunks(raw, sections),
    )['TaskSchedulerTargetFps', 2, 3] == '50'

    # A seed for another GPR does not establish the EAX value at this store.
    replaced = bytearray(image)
    replaced[off + seed_at] = 0xBA  # MOV EDX,50 instead of EAX
    changed = bytes(replaced)
    assert ('TaskSchedulerTargetFps', 2, 3) not in extract_x86_compiled_initializers(
        changed, sections, extract_x86_registration_thunks(changed, sections),
    )


@pytest.mark.parametrize(
    ('previous', 'expected'),
    [
        ('48 8d 05 5b 6f 21 05 c3', False),  # LEA, not a storage write
        ('48 89 05 5b 6f 21 05 90', False),  # No RET
        ('c7 05 5b 6f 21 05 18 72 31 c3', False),  # C3 inside immediate
        ('48 c7 45 f0 18 72 31 bf c3', False),  # Stack, not RIP-relative
        ('48 89 05 5b 6f 21 05 c3', True),
    ],
)
def test_x86_immediate_boundary_rejects_incomplete_predecessors(
    previous: str, expected: bool,
) -> None:
    from fleasion.modifications.fflag_x86_initializers import (  # noqa: PLC0415
        _literal_after_complete_rip_store_ret,  # pyright: ignore[reportPrivateUsage]
    )

    prefix = bytes.fromhex(previous)
    code = memoryview(b'\x90' * 40 + prefix + b'\xb8\x32\x00\x00\x00' + b'\x90' * 16)
    assert _literal_after_complete_rip_store_ret(code, 40 + len(prefix)) is expected
    assert not _literal_after_complete_rip_store_ret(code, 40 + len(prefix) + 1)
