"""Build-independent, read-only PE section discovery for Player binaries.

This inspects section headers and static data only. It deliberately does not
execute, unpack, decrypt, or modify Roblox's protected executable code.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_PE_EXECUTE = 0x20000000
_PE_READ = 0x40000000
_PE_WRITE = 0x80000000
_MAX_PE_SECTIONS = 96
_MAX_PE_IMAGE = 320 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PeSection:
    """Validated file-backed PE section."""

    name: str
    virtual_address: int
    file_offset: int
    file_size: int
    flags: int

    @property
    def executable(self) -> bool:
        return bool(self.flags & _PE_EXECUTE) or self.name == '.text'

    @property
    def writable(self) -> bool:
        return bool(self.flags & _PE_WRITE) or self.name == '.data'

    @property
    def readable(self) -> bool:
        return bool(self.flags & _PE_READ) or not self.flags


@dataclass(frozen=True, slots=True)
class PeImage:
    """Relocatable section map; no fixed build RVAs or file offsets."""

    image_base: int
    machine: int
    sections: tuple[PeSection, ...]

    def section(self, name: str) -> PeSection | None:
        return next((part for part in self.sections if part.name == name), None)

    def strings(self) -> list[tuple[int, int, int]]:
        """Read-only initialized data with plausible flag-name strings."""
        return [
            (part.virtual_address, part.file_offset, part.file_size)
            for part in self.sections
            if part.readable
            and not part.executable
            and not part.writable
            and part.name not in {'.rsrc', '.reloc', '.pdata', '.xdata'}
            and part.file_size >= 64
        ]

    def tables(self) -> list[tuple[int, int]]:
        """Writable, file-backed tables which can hold registration records."""
        return [
            (part.file_offset, part.file_offset + part.file_size)
            for part in self.sections
            if part.writable and not part.executable and part.file_size >= 64
        ]


def inspect_pe(data: bytes) -> PeImage:
    """Read PE32+ headers with bounded sections, offsets, and architectures."""
    if len(data) < 0x100 or len(data) > _MAX_PE_IMAGE or data[:2] != b'MZ':
        msg = 'Invalid or oversized PE executable'
        raise ValueError(msg)
    pe_offset = struct.unpack_from('<I', data, 0x3C)[0]
    if pe_offset > len(data) - 24 or data[pe_offset : pe_offset + 4] != b'PE\0\0':
        msg = 'Invalid PE header'
        raise ValueError(msg)
    machine, section_count = struct.unpack_from('<HH', data, pe_offset + 4)
    optional_size = struct.unpack_from('<H', data, pe_offset + 20)[0]
    optional_offset = pe_offset + 24
    if (
        machine != 0x8664
        or not 0 < section_count <= _MAX_PE_SECTIONS
        or not 32 <= optional_size <= len(data) - optional_offset
        or struct.unpack_from('<H', data, optional_offset)[0] != 0x20B
    ):
        msg = 'Only valid x64 PE32+ images are supported'
        raise ValueError(msg)
    image_base = struct.unpack_from('<Q', data, optional_offset + 24)[0]
    section_offset = optional_offset + optional_size
    if section_offset + section_count * 40 > len(data):
        msg = 'Truncated PE sections'
        raise ValueError(msg)
    sections: list[PeSection] = []
    for index in range(section_count):
        at = section_offset + 40 * index
        raw_name = data[at : at + 8].split(b'\0', 1)[0]
        name = raw_name.decode('ascii', 'replace')
        virtual_size, rva, file_size, file_offset = struct.unpack_from('<IIII', data, at + 8)
        flags = struct.unpack_from('<I', data, at + 36)[0]
        if file_size == 0:
            continue
        if (
            file_offset > len(data)
            or file_size > len(data) - file_offset
            or rva + max(virtual_size, file_size) > 0x1_0000_0000
        ):
            msg = f'Invalid PE section {name}'
            raise ValueError(msg)
        sections.append(PeSection(name, image_base + rva, file_offset, file_size, flags))
    if not sections:
        msg = 'No file-backed PE sections'
        raise ValueError(msg)
    return PeImage(image_base, machine, tuple(sections))


def companion_has_protection_metadata(data: bytes) -> bool:
    """Check DLL protection-section availability without running its code."""
    image = inspect_pe(data)
    section = image.section('.byfron')
    return section is not None and section.file_size >= 1024 and section.executable
