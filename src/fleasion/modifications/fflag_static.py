"""Read static Roblox Player FastVariable records without launching Roblox.

The metadata layout is shared conceptually across platforms, but addresses and
relocations are encoded differently in PE, Mach-O and Android ELF binaries.
Unknown prefixes are never manufactured from raw binary strings.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import struct
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast

from fleasion.modifications.fflag_elf_thunks import extract_x86_registration_thunks
from fleasion.modifications.fflag_pe import (
    companion_has_protection_metadata,
    inspect_pe,
)
from fleasion.utils.json_types import as_object_dict, as_object_list
from fleasion.utils.paths import CONFIG_DIR, LOCAL_APPDATA

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from fleasion.utils.linux_clients import LinuxClientInstallation

_KIND = Literal[
    'bool',
    'numeric',
    'string',
    'log',
    'float',
    'numeric_or_log',
    'string_or_log',
    'log_label',
    'cstring_only',
    'non_boolean_scalar',
    'unknown',
]
_MAX_BINARY_SIZE = 320 * 1024 * 1024
_MAX_RECORDS = 150_000
_RECORD_NAME = re.compile(rb'[A-Za-z_][A-Za-z_0-9]{2,111}\Z')
_WINDOWS_LOG_CHANNEL_LABEL = re.compile(rb'\[(DFLog|FLog)::([A-Za-z_][A-Za-z0-9_]{2,111})\]')
_PREFIXES = (
    'DFFlag',
    'DFInt',
    'DFString',
    'DFLog',
    'DFFloat',
    'FFlag',
    'FInt',
    'FString',
    'FLog',
    'FFloat',
)
_SCAN_CACHE_DIR = CONFIG_DIR / 'fflag_scans'
_VALID_KINDS = frozenset(
    {
        'bool',
        'numeric',
        'string',
        'log',
        'float',
        'numeric_or_log',
        'string_or_log',
        'log_label',
        'cstring_only',
        'non_boolean_scalar',
        'unknown',
    }
)


@dataclass(frozen=True, slots=True)
class ClientBinary:
    """The installed, read-only Player binary or APK member to inspect."""

    client: str
    label: str
    path: Path
    member: str | None = None
    companion_path: Path | None = None


@dataclass(frozen=True, slots=True)
class StaticRecord:
    """A structured registration name, with optional type information."""

    name: str
    variant: int | None = None
    kind: _KIND = 'unknown'
    verified_type: bool = False


@dataclass(frozen=True, slots=True)
class StaticFlagCatalog:
    """Typed names and quality measurements from one local Player build."""

    client: str
    label: str
    source: str
    flags: dict[str, str | None]
    verified: frozenset[str]
    records: int
    unresolved: int
    saved_at: float
    source_size: int
    source_mtime_ns: int
    member: str | None
    source_sha256: str | None = None
    companion_sha256: str | None = None
    companion_metadata_recognized: bool | None = None
    registrations: tuple[StaticRecord, ...] = ()
    candidate_strings: tuple[str, ...] = ()
    layout: Literal['structured', 'string-evidence'] = 'structured'


def _unpack(fmt: str, data: bytes, at: int) -> tuple[int, ...]:
    return struct.unpack_from(fmt, data, at)


def _name_at(data: bytes, at: int, length: int) -> str | None:
    if not (3 <= length <= 112 and 0 <= at < len(data) - length):
        return None
    value = data[at : at + length]
    if data[at + length] != 0 or not _RECORD_NAME.fullmatch(value):
        return None
    return value.decode('ascii')


def _metadata_records(
    data: bytes,
    segments: list[tuple[int, int, int]],
    table_ranges: list[tuple[int, int]],
    matched_offsets: list[int] | None = None,
    matched_name_offsets: dict[str, tuple[int, int]] | None = None,
) -> dict[str, StaticRecord]:
    """Recover name-pointer/length/value triplets from immutable Player tables."""
    records: dict[str, StaticRecord] = {}
    for low, high in table_ranges:
        for at in range((low + 7) & ~7, min(high, len(data) - 23), 8):
            value, length = _unpack('<QQ', data, at)
            if not 3 <= length <= 112:
                continue
            name_offset = next(
                (
                    fileoff + value - virtual
                    for virtual, fileoff, size in segments
                    if virtual <= value < virtual + size
                ),
                -1,
            )
            name = _name_at(data, name_offset, length)
            if name:
                records.setdefault(name, StaticRecord(name))
                if matched_offsets is not None:
                    matched_offsets.append(at)
                if matched_name_offsets is not None:
                    matched_name_offsets.setdefault(name, (at, low))
                if len(records) > _MAX_RECORDS:
                    msg = 'Excessive static registration records'
                    raise ValueError(msg)
    return records


def _longest_record_run(offsets: list[int], stride: int = 24) -> int:
    """Measure structural coherence, not just isolated string-pointer hits."""
    hits = set(offsets)
    longest = 0
    for location in hits:
        if location - stride in hits:
            continue
        size = 1
        while location + stride * size in hits:
            size += 1
        longest = max(longest, size)
    return longest


def _pe_cstring_names(data: bytes, segments: list[tuple[int, int, int]]) -> set[str]:
    """Retain exact read-only C strings as *candidates*, not registrations."""
    result: set[str] = set()
    pattern = re.compile(rb'[A-Za-z_][A-Za-z0-9_]{2,111}\0')
    for _virtual, fileoff, size in segments:
        for match in pattern.finditer(data, fileoff, fileoff + size):
            if match.start() > fileoff and data[match.start() - 1] != 0:
                continue
            result.add(match.group()[:-1].decode('ascii'))
            if len(result) > _MAX_RECORDS:
                msg = 'Excessive read-only string candidates'
                raise ValueError(msg)
    return result


def _string_flag_candidates(names: set[str], published: Mapping[str, str | None]) -> set[str]:
    """Require external qualified evidence before using an embedded string."""
    return {
        name[len(prefix) :]
        for name in published
        for prefix in ('FLog', 'DFLog', 'FString', 'DFString')
        if name.startswith(prefix) and name[len(prefix) :] in names
    }


def _pe_log_label_records(
    data: bytes, segments: list[tuple[int, int, int]], names: set[str]
) -> list[StaticRecord]:
    """Treat qualified log labels as strong *hints*, not proof of registration.

    A label in a diagnostic string alone is insufficient. Require a separate,
    null-terminated name in the same binary's read-only data as well.
    """
    labels: set[tuple[str, int]] = set()
    for _virtual, fileoff, size in segments:
        for match in _WINDOWS_LOG_CHANNEL_LABEL.finditer(data, fileoff, fileoff + size):
            name = match.group(2).decode('ascii')
            if name in names:
                labels.add((name, 2 if match.group(1) == b'DFLog' else 1))
    return [
        StaticRecord(name, variant=variant, kind='log_label') for name, variant in sorted(labels)
    ]


def _pe_candidate_records(
    names: set[str], published: Mapping[str, str | None]
) -> list[StaticRecord]:
    """Retain names corroborated by exact strings and qualified hints."""
    known_suffixes = {
        full[len(prefix) :]
        for full in published
        for prefix in _PREFIXES
        if full.startswith(prefix) and full[len(prefix) :] in names
    }
    return [StaticRecord(name, kind='cstring_only') for name in sorted(known_suffixes)]


def _pe_records(
    data: bytes,
    published: Mapping[str, str | None] | None = None,
    collected_strings: set[str] | None = None,
    layout_out: list[str] | None = None,
) -> list[StaticRecord]:
    # PE addresses and section names are discovered for each installed build
    image = inspect_pe(data)
    strings = image.strings()
    tables = image.tables()
    if not strings or not tables:
        msg = 'Windows Player static metadata sections not found'
        raise ValueError(msg)
    matched_offsets: list[int] = []
    matched_name_offsets: dict[str, tuple[int, int]] = {}
    records = _metadata_records(data, strings, tables, matched_offsets, matched_name_offsets)
    if len(data) > 20 * 1024 * 1024 and _longest_record_run(matched_offsets) < 24:
        # Older Windows Players did not publish the later 24-byte registration
        # table. Do not mistake incidental name pointers for registrations
        # Instead use only explicitly labeled log channels and exact rodata
        # names corroborated by separately qualified source evidence
        names = _pe_cstring_names(data, strings)
        log_records = _pe_log_label_records(data, strings, names)
        hinted_records = _pe_candidate_records(names, published or {})
        if (
            len(names) < 10_000
            or len(log_records) < 250
            or (len(hinted_records) < 500 and len(log_records) < 500)
        ):
            msg = 'Windows Player registration records fail structural validation'
            raise ValueError(msg)
        if collected_strings is not None:
            collected_strings.update(names)
        if layout_out is not None:
            layout_out.append('string-evidence')
        log_names = {record.name for record in log_records}
        return [
            *(record for record in hinted_records if record.name not in log_names),
            *log_records,
        ]
    # The Windows scalar records are value, name pointer, name length.
    # A non-boolean stored value rules out both Flag families. Zero and one
    # remain ambiguous: integer flags can legitimately default to either
    # Do not infer the F/DF namespace or mark either type verified
    hits = set(matched_offsets)
    for name, (at, low) in matched_name_offsets.items():
        if at - 8 < low or (at - 24 not in hits and at + 24 not in hits):
            continue
        if _unpack('<Q', data, at - 8)[0] > 1:
            records[name] = StaticRecord(name, kind='non_boolean_scalar')
    # Some Windows FLog/FString names survive only as C strings. Exact
    # string presence is weaker than a structured registration, so these
    # records require published type evidence and are always inferred
    if published or collected_strings is not None:
        names = _pe_cstring_names(data, strings)
        if collected_strings is not None:
            collected_strings.update(names)
        if published:
            for name in _string_flag_candidates(names, published):
                records.setdefault(name, StaticRecord(name, kind='string_or_log'))
        # These diagnostic labels independently spell their FLog/DFLog
        # namespace, but cannot establish that registration actually ran
        return [*records.values(), *_pe_log_label_records(data, strings, names)]
    return list(records.values())


def _macho_sections(data: bytes) -> list[tuple[str, str, int, int, int]]:
    if len(data) < 32 or data[:4] != b'\xcf\xfa\xed\xfe':
        msg = 'Only thin, little-endian 64-bit Mach-O Players are supported'
        raise ValueError(msg)
    ncmds, sizeofcmds = _unpack('<II', data, 16)
    if ncmds > 256 or 32 + sizeofcmds > len(data):
        msg = 'Invalid Mach-O load commands'
        raise ValueError(msg)
    pos = 32
    out: list[tuple[str, str, int, int, int]] = []
    for _ in range(ncmds):
        cmd, cmdsize = _unpack('<II', data, pos)
        if cmdsize < 8 or pos + cmdsize > len(data):
            msg = 'Truncated Mach-O load command'
            raise ValueError(msg)
        if cmd == 0x19 and cmdsize >= 72:
            nsects = _unpack('<I', data, pos + 64)[0]
            if 72 + nsects * 80 > cmdsize:
                msg = 'Invalid Mach-O section count'
                raise ValueError(msg)
            for j in range(nsects):
                at = pos + 72 + j * 80
                sectname = data[at : at + 16].split(b'\0')[0].decode('ascii', 'replace')
                segname = data[at + 16 : at + 32].split(b'\0')[0].decode('ascii', 'replace')
                address, size = _unpack('<QQ', data, at + 32)
                fileoff = _unpack('<I', data, at + 48)[0]
                if fileoff + size <= len(data):
                    out.append((segname, sectname, address, fileoff, size))
        pos += cmdsize
    return out


def _macho_records(data: bytes) -> list[StaticRecord]:
    sections = _macho_sections(data)
    strings = [
        (address, off, size)
        for seg, sect, address, off, size in sections
        if seg == '__TEXT' and sect in {'__cstring', '__const'}
    ]
    tables = [
        (off, off + size)
        for seg, sect, _address, off, size in sections
        if seg.startswith('__DATA') and sect in {'__const', '__data', '__static_data'}
    ]
    if not strings or not tables:
        msg = 'Mach-O Player metadata sections not found'
        raise ValueError(msg)
    records = _metadata_records(data, strings, tables)
    # x86-64 Player accessor thunks encode the F/DF namespace, but several
    # helpers are shared across value types, so a helper address alone does
    # not establish Bool/Int/Log/String
    cpu = _unpack('<I', data, 4)[0]
    if cpu != 0x01000007:
        return list(records.values())

    code_segments = [
        (address, off, size)
        for seg, sect, address, off, size in sections
        if (seg, sect) == ('__TEXT', '__text')
    ]
    data_segments = [
        (address, off, size)
        for seg, sect, address, off, size in sections
        if seg.startswith('__DATA') and sect in {'__const', '__data', '__static_data'}
    ]

    def file_offset(address: int, segments: list[tuple[int, int, int]]) -> int:
        return next(
            (
                off + address - base
                for base, off, length in segments
                if base <= address < base + length
            ),
            -1,
        )

    # Common getter: lea rdi, [record.value]; mov esi, F_or_DF; jmp helper
    # This identifies the actual record and namespace without inferring type
    direct_pattern = re.compile(
        rb'\x55\x48\x89\xe5\x48\x8d\x3d(.{4})'
        rb'\xbe(.{4})\x5d\xe9(.{4})',
        re.DOTALL,
    )
    # Other getter: lea rdi, [cstring]; lea rsi, [record.value];
    # mov edx, F_or_DF; jmp helper
    named_pattern = re.compile(
        rb'\x55\x48\x89\xe5\x48\x8d\x3d(.{4})'
        rb'\x48\x8d\x35(.{4})\xba(.{4})\x5d\xe9(.{4})',
        re.DOTALL,
    )
    thunks: list[tuple[str, int, int | None]] = []
    for code_va, code_off, code_size in code_segments:
        for match in direct_pattern.finditer(data, code_off, code_off + code_size):
            start = code_va + match.start() - code_off
            value_address = start + 11 + _unpack('<i', match.group(1), 0)[0]
            value_offset = file_offset(value_address, data_segments)
            if value_offset < 16:
                continue
            name_ptr, name_len = _unpack('<QQ', data, value_offset - 16)
            name = _name_at(data, file_offset(name_ptr, strings), name_len)
            variant = _unpack('<i', match.group(2), 0)[0]
            if name and variant in {1, 2}:
                thunks.append((name, variant, None))
        for match in named_pattern.finditer(data, code_off, code_off + code_size):
            start = code_va + match.start() - code_off
            name_address = start + 11 + _unpack('<i', match.group(1), 0)[0]
            value_address = start + 18 + _unpack('<i', match.group(2), 0)[0]
            name_offset = file_offset(name_address, strings)
            if file_offset(value_address, data_segments) < 0 or name_offset < 0:
                continue
            raw_name = data[name_offset : name_offset + 112].split(b'\0', 1)[0]
            if not _RECORD_NAME.fullmatch(raw_name):
                continue
            variant = _unpack('<i', match.group(3), 0)[0]
            if variant not in {1, 2}:
                continue
            # The complete thunk is 29 bytes, including the five-byte JMP
            target = start + 29 + _unpack('<i', match.group(4), 0)[0]
            thunks.append((raw_name.decode('ascii'), variant, target))

    # Confirm the boolean helper only when two independent, known boolean
    # registration anchors resolve to the same target in THIS binary. Numeric
    # and log/string helpers are shared: never mark those types verified
    def helper_type_id(target: int) -> int | None:
        """Decode a tail-call wrapper's compiled registration-type argument."""
        wrapper = file_offset(target, code_segments)
        if wrapper < 0 or data[wrapper : wrapper + 4] != b'\x55\x48\x89\xe5':
            return None
        # push rbp; mov rbp,rsp; mov ecx,edx; mov rdx,rsi;
        # mov rsi,rdi; mov rdi,[rip+disp32]; pop rbp; jmp rel32
        if (
            data[wrapper + 4 : wrapper + 15] != b'\x89\xd1\x48\x89\xf2\x48\x89\xfe\x48\x8b\x3d'
            or data[wrapper + 19 : wrapper + 21] != b'\x5d\xe9'
        ):
            return None
        implementation = target + 25 + _unpack('<i', data, wrapper + 21)[0]
        implementation_off = file_offset(implementation, code_segments)
        if (
            implementation_off < 0
            or data[implementation_off : implementation_off + 6] != b'\x55\x48\x89\xe5\x41\xb8'
        ):
            return None
        return _unpack('<I', data, implementation_off + 6)[0]

    bool_anchors = {'DebugDrawBroadPhaseAABBs', 'MacSyncWKCookieStoreToNSCookieStoreV2'}
    anchor_targets = {
        name: {
            target
            for candidate, _variant, target in thunks
            if candidate == name and target is not None
        }
        for name in bool_anchors
    }
    anchored_bool_targets: set[int] = (
        anchor_targets['DebugDrawBroadPhaseAABBs']
        & anchor_targets['MacSyncWKCookieStoreToNSCookieStoreV2']
    )
    # Registration type 4 is the boolean specialization in current macOS
    # Players; resolve it from the actual wrapper's machine code, not a VA
    type_ids = {
        target: helper_type_id(target) for _name, _variant, target in thunks if target is not None
    }
    bool_targets = anchored_bool_targets | {
        target for target, type_id in type_ids.items() if type_id == 4
    }
    typed: dict[tuple[str, int, _KIND], StaticRecord] = {}
    for name, variant, target in thunks:
        is_boolean = target is not None and target in bool_targets
        type_id = type_ids.get(target) if target is not None else None
        kind: _KIND = (
            'bool'
            if is_boolean
            else 'numeric_or_log'
            if type_id == 1
            else 'string_or_log'
            if type_id == 2
            else 'unknown'
        )
        record = StaticRecord(name, variant, kind, verified_type=is_boolean)
        typed[name, variant, kind] = record
        records.pop(name, None)
    return [*records.values(), *typed.values()]


def _sleb(data: bytes, position: int, end: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 70, 7):
        if position >= end:
            msg = 'Truncated Android relocation data'
            raise ValueError(msg)
        byte = data[position]
        position += 1
        result |= (byte & 0x7F) << shift
        if byte < 0x80:
            if byte & 0x40:
                result -= 1 << (shift + 7)
            return result, position
    msg = 'Invalid Android signed LEB128'
    raise ValueError(msg)


def _elf_records(data: bytes) -> list[StaticRecord]:
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01':
        msg = 'Only little-endian ELF64 Player libraries are supported'
        raise ValueError(msg)
    architecture = _unpack('<H', data, 18)[0]
    if architecture not in {62, 183}:
        msg = 'Unsupported Android Player architecture'
        raise ValueError(msg)
    relative_relocation = 8 if architecture == 62 else 1027
    sections_offset = _unpack('<Q', data, 40)[0]
    section_size, section_count, names_index = _unpack('<HHH', data, 58)
    if (
        section_count > 256
        or section_size < 64
        or sections_offset + section_size * section_count > len(data)
    ):
        msg = 'Invalid ELF section headers'
        raise ValueError(msg)
    headers: list[tuple[int, int, int, int, int]] = []
    for i in range(section_count):
        at = sections_offset + i * section_size
        name, type_id = _unpack('<II', data, at)
        address, off, size = _unpack('<QQQ', data, at + 16)
        headers.append((name, type_id, address, off, size))
    if names_index >= len(headers):
        msg = 'ELF section names are unavailable'
        raise ValueError(msg)
    _n, _t, _a, names_off, names_size = headers[names_index]
    names_blob = data[names_off : names_off + names_size]

    def label(n: int) -> bytes:
        return names_blob[n:].split(b'\0', 1)[0] if n < len(names_blob) else b''

    rodata = next(
        (
            (address, off, size)
            for n, _type, address, off, size in headers
            if label(n) == b'.rodata'
        ),
        None,
    )
    descriptors = next(
        (
            (address, off, size)
            for n, _type, address, off, size in headers
            if label(n) == b'.data.rel.ro'
        ),
        None,
    )
    packed = next(
        ((off, size) for _n, type_id, _a, off, size in headers if type_id == 0x60000002), None
    )
    if not rodata or not descriptors or not packed:
        msg = 'Android packed FastFlag metadata not found'
        raise ValueError(msg)
    reloc_off, reloc_size = packed
    if reloc_size > 12 * 1024 * 1024 or data[reloc_off : reloc_off + 4] != b'APS2':
        msg = 'Unsupported Android relocation encoding'
        raise ValueError(msg)
    end = reloc_off + reloc_size
    pos = reloc_off + 4
    count, pos = _sleb(data, pos, end)
    offset, pos = _sleb(data, pos, end)
    if not 0 <= count <= 4_000_000:
        msg = 'Invalid Android relocation count'
        raise ValueError(msg)
    addend = 0
    records: dict[tuple[str, int, _KIND], StaticRecord] = {}
    section_addr, section_off, section_len = descriptors
    string_addr, string_off, string_len = rodata
    processed = 0
    while processed < count:
        group_size, pos = _sleb(data, pos, end)
        group_flags, pos = _sleb(data, pos, end)
        if not (0 < group_size <= count - processed and 0 <= group_flags <= 15):
            msg = 'Invalid Android relocation group'
            raise ValueError(msg)
        delta = 0
        info = 0
        if group_flags & 2:
            delta, pos = _sleb(data, pos, end)
        if group_flags & 1:
            info, pos = _sleb(data, pos, end)
        if group_flags & 8 and group_flags & 4:
            change, pos = _sleb(data, pos, end)
            addend += change
        for _ in range(group_size):
            if not group_flags & 2:
                delta, pos = _sleb(data, pos, end)
            offset += delta
            if not group_flags & 1:
                info, pos = _sleb(data, pos, end)
            if group_flags & 8:
                if not group_flags & 4:
                    change, pos = _sleb(data, pos, end)
                    addend += change
            else:
                addend = 0
            processed += 1
            if (info & 0xFFFFFFFF) != relative_relocation or not (
                section_addr <= offset <= section_addr + section_len - 32
                and string_addr <= addend < string_addr + string_len
            ):
                continue
            fileoff = section_off + offset - section_addr
            # ELF registration entries are 32 bytes:
            # name pointer, name length, initial storage, type/namespace
            # Metadata is at +24, NOT -8 (which is the previous entry)
            prefix_and_type = _unpack('<Q', data, fileoff + 24)[0]
            variant = prefix_and_type & 0xFFFFFFFF
            kind_index = prefix_and_type >> 32
            kind: _KIND
            if variant not in {1, 2}:
                continue
            if kind_index == 0:
                # Reject unrelated relocation-bearing records that mimic
                # flag descriptors but have impossible bool storage values
                # This excludes anomalous L2T* metadata in Sober's ELF
                if _unpack('<Q', data, fileoff + 16)[0] not in {0, 1}:
                    continue
                kind = 'bool'
            elif kind_index == 2:
                kind = 'numeric'
            elif kind_index == 3:
                # Android packed metadata encodes FString separately from
                # FastLog channels, which use their own registration thunks
                kind = 'string'
            elif kind_index == 4:
                kind = 'float'
            else:
                continue
            name_length = _unpack('<Q', data, fileoff + 8)[0]
            name = _name_at(data, string_off + addend - string_addr, name_length)
            if name:
                records[name, variant, kind] = StaticRecord(name, variant, kind, verified_type=True)
                if len(records) > _MAX_RECORDS:
                    msg = 'Excessive Android FastFlag records'
                    raise ValueError(msg)
    # Android x86-64 has two complimentary registration forms. Sober keeps
    # scalar/string descriptors in relocation tables but still emits separate
    # compiled log-channel thunks. Mocktail emits thunks for every family
    if architecture == 62:
        sections = {
            label(n).decode('ascii', 'replace'): (address, off, size)
            for n, _type, address, off, size in headers
        }
        thunks = extract_x86_registration_thunks(data, sections)
        if len(records) < 1000 and len(thunks) >= 1000:
            typed: dict[tuple[str, int, _KIND], StaticRecord] = {}
            for registration in thunks:
                kind: _KIND = (
                    'log'
                    if registration.type_id == 1
                    else 'string'
                    if registration.type_id == 2
                    else 'numeric'
                    if registration.type_id == 3
                    else 'bool'
                )
                typed[registration.name, registration.variant, kind] = StaticRecord(
                    registration.name, registration.variant, kind, verified_type=True
                )
            return list(typed.values())
        if len(records) >= 1000 and len(thunks) >= 100:
            for registration in thunks:
                if registration.type_id != 1:
                    continue
                record = StaticRecord(
                    registration.name, registration.variant, 'log', verified_type=True
                )
                records[registration.name, registration.variant, 'log'] = record

    return list(records.values())


def _typed_candidates(
    record: StaticRecord, published: Mapping[str, str | None]
) -> tuple[tuple[str, bool], ...]:
    prefix = ('F', 'DF')[record.variant - 1] if record.variant in {1, 2} else None
    if record.kind == 'log_label' and prefix:
        # This full qualifier occurs literally in read-only diagnostics
        # It remains inferred because diagnostic text isn't a registration
        return ((prefix + 'Log' + record.name, False),)
    if record.verified_type and prefix:
        suffix = {
            'bool': 'Flag',
            'numeric': 'Int',
            'log': 'Log',
            'string': 'String',
            'float': 'Float',
        }.get(record.kind)
        if suffix:
            return ((prefix + suffix + record.name, True),)
    candidate_families = _PREFIXES
    if record.kind == 'numeric_or_log':
        candidate_families = tuple(
            family for family in _PREFIXES if family.endswith(('Int', 'Log'))
        )
    elif record.kind == 'string_or_log':
        candidate_families = tuple(
            family for family in _PREFIXES if family.endswith(('String', 'Log'))
        )
    elif record.kind == 'non_boolean_scalar':
        candidate_families = tuple(family for family in _PREFIXES if not family.endswith('Flag'))
    if prefix:
        candidates = tuple(
            typed + record.name for typed in candidate_families if typed.startswith(prefix)
        )
    else:
        candidates = tuple(typed + record.name for typed in candidate_families)
    matched = tuple(candidate for candidate in candidates if candidate in published)
    # An unresolved prefix isn't sufficient evidence for a real qualified flag
    if len(matched) != 1:
        return ()
    return ((matched[0], False),)


def _resolve_records(
    records: list[StaticRecord], published: Mapping[str, str | None]
) -> tuple[dict[str, str | None], set[str]]:
    """Retain distinct qualified flags sharing the same unprefixed suffix."""
    flags: dict[str, str | None] = {}
    verified: set[str] = set()
    for record in records:
        for qualified, confirmed in _typed_candidates(record, published):
            flags[qualified] = published.get(qualified)
            if confirmed:
                verified.add(qualified)
    return flags, verified


def _read_binary(source: ClientBinary) -> bytes:
    if not source.member:
        size = source.path.stat().st_size
        if size < 64 or size > _MAX_BINARY_SIZE:
            msg = 'Roblox Player binary exceeds permitted size'
            raise ValueError(msg)
        return source.path.read_bytes()
    with zipfile.ZipFile(source.path) as archive:
        info = archive.getinfo(source.member)
        if info.file_size < 64 or info.file_size > _MAX_BINARY_SIZE:
            msg = 'Roblox Player APK member exceeds permitted size'
            raise ValueError(msg)
        return archive.read(info)


def _file_sha256(path: Path) -> str:
    """Stream the complete file without loading another huge Player into RAM."""
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def scan_binary(
    source: ClientBinary, published: Mapping[str, str | None] | None = None
) -> StaticFlagCatalog:
    """Return qualified flag records from a particular Player binary on disk."""
    start_stat = source.path.stat()
    data = _read_binary(source)
    companion_recognized: bool | None = None
    embedded_strings: set[str] = set()
    layouts: list[str] = []
    if data[:2] == b'MZ':
        # Unrecognized DLL protection metadata must never block readable,
        # static registration names from the executable itself
        if source.companion_path is not None:
            try:
                companion_recognized = companion_has_protection_metadata(
                    source.companion_path.read_bytes()
                )
            except ValueError:
                companion_recognized = False
        records = _pe_records(data, published, embedded_strings, layouts)
    elif data[:4] == b'\xcf\xfa\xed\xfe':
        records = _macho_records(data)
    elif data[:4] == b'\x7fELF':
        records = _elf_records(data)
    else:
        msg = 'Unrecognized Roblox Player binary format'
        raise ValueError(msg)
    # Reject implausibly small results from large Players with a different
    # registration layout (some older Android builds), rather than claiming
    # a few incidental records form a complete inventory
    minimum_records = 500 if layouts else 1000
    if not records or (len(data) > 20 * 1024 * 1024 and len(records) < minimum_records):
        msg = 'This Player build uses an unsupported FastFlag registration layout'
        raise ValueError(msg)
    flags, verified = _resolve_records(records, published or {})
    if not flags:
        msg = 'No qualified FastFlags resolved; public type inventory may be needed'
        raise ValueError(msg)
    stat = source.path.stat()
    if stat.st_size != start_stat.st_size or stat.st_mtime_ns != start_stat.st_mtime_ns:
        msg = 'Roblox Player binary changed while it was being scanned'
        raise ValueError(msg)
    source_hash = _file_sha256(source.path)
    if source.member is None and source_hash != hashlib.sha256(data).hexdigest():
        msg = 'Roblox Player binary changed during the scan'
        raise ValueError(msg)
    return StaticFlagCatalog(
        source.client,
        source.label,
        str(source.path),
        flags,
        frozenset(verified),
        len(records),
        len(records) - len(flags),
        time.time(),
        stat.st_size,
        stat.st_mtime_ns,
        source.member,
        source_hash,
        _file_sha256(source.companion_path) if source.companion_path is not None else None,
        companion_recognized,
        tuple(records),
        tuple(sorted(embedded_strings)),
        'string-evidence' if layouts else 'structured',
    )


def _android_member(path: Path) -> str | None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = [
                name
                for name in archive.namelist()
                if re.fullmatch(r'lib/(?:x86_64|arm64-v8a)/libroblox\.so', name)
            ]
            return next(
                (name for name in entries if 'x86_64' in name), entries[0] if entries else None
            )
    except OSError, zipfile.BadZipFile:
        return None


def _detect_linux_binary() -> ClientBinary | None:
    callback = cast(
        'Callable[[], LinuxClientInstallation | None]',
        importlib.import_module(
            'fleasion.utils.platform_linux'
        ).get_selected_linux_client_installation,
    )
    installation = callback()
    if installation is None:
        return None
    if installation.client.runtime == 'rosemary':
        bundle = installation.app_bundle
        if bundle is None:
            return None
        binary = bundle / 'Contents' / 'MacOS' / 'RobloxPlayer'
        return (
            ClientBinary(installation.key, installation.display_name, binary)
            if binary.is_file()
            else None
        )
    root = installation.paths.data_root
    if installation.key in {'mocktail', 'nightcap', 'cordial'}:
        # Community clients store versioned native payloads outside Android's
        # APK package directory; only read their installed libroblox.so
        payload_roots = (
            installation.paths.flatpak_root / 'payloads',
            installation.paths.flatpak_root / 'data' / 'mocktail' / 'payloads',
            root / 'payloads',
        )
        # Cordial owns versioned builds under $XDG_DATA_HOME/cordial/builds
        # whereas Mocktail and Nightcap use payloads/<build-id>/libroblox.so
        if installation.key == 'cordial':
            payload_roots = (*payload_roots, root / 'builds')
        native_libs = sorted(
            (
                path
                for payload_root in payload_roots
                for path in payload_root.glob('*/libroblox.so')
                if path.is_file()
            ),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )
        if native_libs:
            return ClientBinary(installation.key, installation.display_name, native_libs[0])
    archives = sorted(
        root.glob('packages/*/com.roblox.client/split_config.*.apk'),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    for path in archives:
        if member := _android_member(path):
            return ClientBinary(installation.key, installation.display_name, path, member)
    return None


def detect_client_binary() -> ClientBinary | None:
    """Locate the selected installation, never a globally pinned Roblox build."""
    if sys.platform.startswith('linux'):
        return _detect_linux_binary()
    if sys.platform == 'darwin':
        for bundle in (Path('/Applications/Roblox.app'), Path.home() / 'Applications/Roblox.app'):
            binary = bundle / 'Contents' / 'MacOS' / 'RobloxPlayer'
            if binary.is_file():
                return ClientBinary('macos', 'Roblox for macOS', binary)
        return None
    if sys.platform == 'win32':
        root = LOCAL_APPDATA / 'Roblox' / 'Versions'
        matches = sorted(
            root.glob('*/RobloxPlayerBeta.exe'), key=lambda p: p.stat().st_mtime_ns, reverse=True
        )
        if not matches:
            return None
        executable = matches[0]
        companion = executable.with_name('RobloxPlayerBeta.dll')
        return ClientBinary(
            'windows',
            'Roblox for Windows',
            executable,
            companion_path=companion if companion.is_file() else None,
        )
    return None


def _cache_path(client: str) -> Path:
    if not re.fullmatch(r'[a-z0-9_-]{1,48}', client):
        msg = 'Invalid client key'
        raise ValueError(msg)
    return _SCAN_CACHE_DIR / f'{client}.json'


def save_scan(catalog: StaticFlagCatalog) -> None:
    """Persist a scan indefinitely; only explicit Recache replaces it."""
    path = _cache_path(catalog.client)
    temp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(
                {
                    'version': 8,
                    'client': catalog.client,
                    'label': catalog.label,
                    'source': catalog.source,
                    'member': catalog.member,
                    'flags': catalog.flags,
                    'verified': sorted(catalog.verified),
                    'records': catalog.records,
                    'unresolved': catalog.unresolved,
                    'saved_at': catalog.saved_at,
                    'source_size': catalog.source_size,
                    'source_mtime_ns': catalog.source_mtime_ns,
                    'source_sha256': catalog.source_sha256,
                    'companion_sha256': catalog.companion_sha256,
                    'companion_metadata_recognized': catalog.companion_metadata_recognized,
                    'registrations': [
                        [rec.name, rec.variant, rec.kind, rec.verified_type]
                        for rec in catalog.registrations
                    ],
                    'candidate_strings': catalog.candidate_strings,
                    'layout': catalog.layout,
                },
                separators=(',', ':'),
            ),
            encoding='utf-8',
        )
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def load_scan(client: str) -> StaticFlagCatalog | None:
    """Read a persisted static scan without imposing a time-based expiration."""
    path = _cache_path(client)
    try:
        payload: object = json.loads(path.read_text(encoding='utf-8'))
    except OSError, ValueError:
        return None
    return _parse_cached_scan(client, payload)


def _validated_flags(value: object) -> dict[str, str | None] | None:
    raw_flags = as_object_dict(value)
    if raw_flags is None or len(raw_flags) > _MAX_RECORDS:
        return None
    flags: dict[str, str | None] = {}
    for name, entry in raw_flags.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{2,111}', name) or (
            entry is not None and not isinstance(entry, str)
        ):
            return None
        flags[name] = entry
    return flags


def _validated_registrations(value: object) -> tuple[StaticRecord, ...] | None:
    """Validate the cached raw records before using them as type hints."""
    raw = as_object_list(value)
    if raw is None or len(raw) > _MAX_RECORDS:
        return None
    records: list[StaticRecord] = []
    for item in raw:
        fields = as_object_list(item)
        if fields is None or len(fields) != 4:
            return None
        name, variant, kind, typed = fields
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{1,111}', name):
            return None
        if variant is not None and (type(variant) is not int or variant not in {1, 2}):
            return None
        if not isinstance(kind, str) or kind not in _VALID_KINDS or type(typed) is not bool:
            return None
        records.append(StaticRecord(name, variant, cast('_KIND', kind), typed))
    return tuple(records)


def resolve_catalog_from_snapshot(
    catalog: StaticFlagCatalog, published: Mapping[str, str | None]
) -> StaticFlagCatalog:
    """Resolve new typed hints against cached binary evidence, without I/O."""
    if not catalog.registrations:
        return catalog
    if catalog.layout == 'string-evidence':
        names = set(catalog.candidate_strings)
        labels = [rec for rec in catalog.registrations if rec.kind == 'log_label']
        label_names = {rec.name for rec in labels}
        records = [
            rec for rec in _pe_candidate_records(names, published) if rec.name not in label_names
        ] + labels
    else:
        records = list(catalog.registrations)
    if catalog.layout == 'structured' and catalog.candidate_strings:
        present_names = {record.name for record in records if record.kind != 'log_label'}
        records.extend(
            StaticRecord(name, kind='string_or_log')
            for name in sorted(_string_flag_candidates(set(catalog.candidate_strings), published))
            if name not in present_names
        )
    flags, verified = _resolve_records(records, published)
    return replace(
        catalog,
        flags=flags,
        verified=frozenset(verified),
        records=len(records),
        unresolved=max(0, len(records) - len(flags)),
        registrations=tuple(records),
    )


def _validated_candidate_strings(value: object) -> tuple[str, ...] | None:
    raw = as_object_list(value)
    if (
        raw is None
        or len(raw) > _MAX_RECORDS
        or not all(
            isinstance(name, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{2,111}', name)
            for name in raw
        )
    ):
        return None
    return tuple(name for name in raw if isinstance(name, str))


def _parse_cached_scan(client: str, payload: object) -> StaticFlagCatalog | None:
    mapping = as_object_dict(payload)
    # Previously verified Sober-family snapshots may contain 13 unrelated
    # relocation descriptors masquerading as booleans; rescan that family
    if (
        mapping is None
        or mapping.get('client') != client
        or mapping.get('version') not in {6, 7, 8}
        or (client in {'sober', 'mocktail', 'nightcap', 'cordial'} and mapping.get('version') != 8)
    ):
        return None
    flags = _validated_flags(mapping.get('flags'))
    raw_verified = as_object_list(mapping.get('verified'))
    registrations = _validated_registrations(mapping.get('registrations'))
    candidate_strings = _validated_candidate_strings(mapping.get('candidate_strings'))
    layout = mapping.get('layout', 'structured')
    verified_names_valid = (
        flags is not None
        and raw_verified is not None
        and all(isinstance(name, str) and name in flags for name in raw_verified)
    )
    if (
        flags is None
        or raw_verified is None
        or registrations is None
        or candidate_strings is None
        or not (layout in {'structured', 'string-evidence'} and verified_names_valid)
    ):
        return None
    label, source, member = (mapping.get(key) for key in ('label', 'source', 'member'))
    raw_records = mapping.get('records')
    raw_unresolved = mapping.get('unresolved')
    raw_saved = mapping.get('saved_at')
    raw_size = mapping.get('source_size')
    raw_mtime = mapping.get('source_mtime_ns')
    if (
        not isinstance(label, str)
        or not isinstance(source, str)
        or (member is not None and not isinstance(member, str))
    ):
        return None
    if (
        not isinstance(raw_records, int)
        or not isinstance(raw_unresolved, int)
        or (not isinstance(raw_size, int) or not isinstance(raw_mtime, int))
    ):
        return None
    source_hash = mapping.get('source_sha256')
    companion_hash = mapping.get('companion_sha256')
    recognized = mapping.get('companion_metadata_recognized')
    if (
        not isinstance(raw_saved, int | float)
        or not (0 <= raw_unresolved <= raw_records <= _MAX_RECORDS)
        or (recognized is not None and not isinstance(recognized, bool))
        or not all(
            digest is None
            or (isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) is not None)
            for digest in (source_hash, companion_hash)
        )
    ):
        return None
    return StaticFlagCatalog(
        client,
        label,
        source,
        flags,
        frozenset(name for name in raw_verified if isinstance(name, str)),
        raw_records,
        raw_unresolved,
        float(raw_saved),
        raw_size,
        raw_mtime,
        member,
        source_hash if isinstance(source_hash, str) else None,
        companion_hash if isinstance(companion_hash, str) else None,
        recognized if isinstance(recognized, bool) else None,
        registrations,
        candidate_strings,
        cast("Literal['structured', 'string-evidence']", layout),
    )


def cached_cross_client_type_hints(selected_client: str) -> frozenset[str]:
    """Use independently typed *cached* Player scans as unverified type hints.

    A name is only included in a new scan if the selected Player itself has
    the corresponding static registration string. This cannot establish the
    type in the selected Player, so such matches remain marked inferred.
    """
    hints: set[str] = set()
    try:
        cached_paths = sorted(_SCAN_CACHE_DIR.glob('*.json'))[:24]
    except OSError:
        return frozenset()
    for path in cached_paths:
        client = path.stem
        if client == selected_client:
            continue
        try:
            catalog = load_scan(client)
        except ValueError:
            continue
        if catalog is not None:
            hints.update(catalog.verified)
    return frozenset(hints)


def scan_is_current(catalog: StaticFlagCatalog, source: ClientBinary | None) -> bool:
    if source is None:
        return False
    try:
        stat = source.path.stat()
        identity_matches = (
            catalog.source == str(source.path)
            and catalog.member == source.member
            and catalog.source_size == stat.st_size
            and catalog.source_mtime_ns == stat.st_mtime_ns
        )
        if not identity_matches or (
            catalog.source_sha256 is None or catalog.source_sha256 != _file_sha256(source.path)
        ):
            return False
        if source.companion_path is None:
            return catalog.companion_sha256 is None
        return catalog.companion_sha256 is not None and catalog.companion_sha256 == _file_sha256(
            source.companion_path
        )
    except OSError:
        return False
