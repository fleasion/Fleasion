"""RBXM binary format serializer.

Exact inverse of deserializer.py — converts an RbxDocument back to the
Roblox binary model format (.rbxm).

Reference: RbxmDeserializer in deserializer.py and
           App/v8xml/SerializerBinary.cpp (Roblox 2016 source).
"""

from __future__ import annotations

import hashlib
import struct
from collections import defaultdict
from typing import Any

import lz4.block as lz4_block  # pyright: ignore[reportMissingTypeStubs]

from .binary_writer import (
    encode_ids,
    interleave_bytes,
    interleave_f32,
    interleave_i32,
    interleave_i64,
    interleave_u32,
    interleave_u64,
    write_binary_string,
    write_f32,
    write_f64,
    write_string,
    write_u8,
    write_u16,
    write_u32,
)
from .types import PropertyFormat, RbxDocument, RbxInstance, RbxRawPropertyChunk

MAGIC_HEADER = b'<roblox!\x89\xff\x0d\x0a\x1a\x0a'
FILE_VERSION = 0  # same version the deserializer reads


# Public entry point


def write_rbxm(doc: RbxDocument) -> bytes:
    """Serialize an RbxDocument to raw RBXM binary bytes."""
    s = RbxmSerializer(doc)
    return s.serialize()


# Serializer


class RbxmSerializer:
    """Builds a binary RBXM stream from an RbxDocument."""

    def __init__(self, doc: RbxDocument) -> None:
        self._doc = doc
        # Assign a stable, zero-based type index to every unique class name.
        # Order by first encounter in a breadth-first walk so the output is
        # deterministic and matches what the original file likely had.
        self._type_index: dict[str, int] = {}
        self._type_instances: dict[int, list[RbxInstance]] = defaultdict(list)
        self._all_instances: list[RbxInstance] = []
        self._shared_strings: list[bytes] = []
        self._shared_string_index: dict[bytes, int] = {}
        self._assign_types()
        self._collect_shared_strings()

    # Pre-pass: assign type indices and walk instance tree

    def _walk(self) -> list[RbxInstance]:
        """Breadth-first walk over all instances."""
        result: list[RbxInstance] = []
        queue = list(self._doc.roots)
        while queue:
            inst = queue.pop(0)
            result.append(inst)
            queue.extend(inst.children)
        return result

    def _assign_types(self) -> None:
        self._all_instances = self._walk()
        for inst in self._all_instances:
            if inst.class_name not in self._type_index:
                idx = len(self._type_index)
                self._type_index[inst.class_name] = idx
            self._type_instances[self._type_index[inst.class_name]].append(inst)

    def _collect_shared_strings(self) -> None:
        """Pre-scan SHARED_STRING properties and build the SSTR table."""
        for inst in self._all_instances:
            for prop in inst.properties.values():
                if (
                    prop.fmt == PropertyFormat.SHARED_STRING
                    and isinstance(prop.value, bytes)
                    and prop.value not in self._shared_string_index
                ):
                    self._shared_string_index[prop.value] = len(self._shared_strings)
                    self._shared_strings.append(prop.value)

    # Top-level serialize

    def serialize(self) -> bytes:
        type_count = len(self._type_index)
        object_count = len(self._all_instances)

        chunks = bytearray()

        if self._doc.metadata.entries:
            chunks.extend(self._build_chunk('META', self._build_meta()))

        if self._shared_strings:
            chunks.extend(self._build_chunk('SSTR', self._build_sstr()))

        for class_name, type_idx in self._type_index.items():
            chunks.extend(self._build_chunk('INST', self._build_inst(type_idx, class_name)))

        for type_idx, instances in self._type_instances.items():
            for prop_name in self._collect_prop_names(instances):
                prop_data = self._build_prop(type_idx, prop_name, instances)
                if prop_data is not None:
                    chunks.extend(self._build_chunk('PROP', prop_data))

        for prop_data in self._build_raw_props():
            chunks.extend(self._build_chunk('PROP', prop_data))

        for raw_chunk in self._doc.raw_chunks:
            chunks.extend(self._build_chunk(raw_chunk.name, raw_chunk.data))

        chunks.extend(self._build_chunk('PRNT', self._build_prnt()))
        chunks.extend(self._build_chunk('END\x00', b'</roblox>'))

        header = (
            MAGIC_HEADER
            + struct.pack('<H', FILE_VERSION)
            + struct.pack('<I', type_count)
            + struct.pack('<I', object_count)
            + b'\x00' * 8  # reserved
        )
        return header + bytes(chunks)

    # Chunk framing

    @staticmethod
    def _build_chunk(name: str, data: bytes) -> bytes:
        """Wrap chunk data with the 16-byte chunk header.

        Compresses with LZ4 if the compressed form is actually smaller.
        Uses uncompressed otherwise (compressed_size = 0 signals that).
        """
        name_b = name.encode('ascii')[:4].ljust(4, b'\x00')
        uncompressed_size = len(data)

        if uncompressed_size == 0:
            return name_b + struct.pack('<III', 0, 0, 0)

        compressed = bytes(
            lz4_block.compress(  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
                data, store_size=False
            )
        )
        if len(compressed) < uncompressed_size:
            return name_b + struct.pack('<III', len(compressed), uncompressed_size, 0) + compressed
        # Uncompressed: compressed_size field = 0
        return name_b + struct.pack('<III', 0, uncompressed_size, 0) + data

    # META chunk

    def _build_meta(self) -> bytes:
        buf = bytearray()
        entries = self._doc.metadata.entries
        buf.extend(write_u32(len(entries)))
        for key, value in entries.items():
            buf.extend(write_string(key))
            buf.extend(write_string(value))
        return bytes(buf)

    # SSTR chunk

    def _build_sstr(self) -> bytes:
        buf = bytearray()
        buf.extend(write_u32(0))  # version
        buf.extend(write_u32(len(self._shared_strings)))
        for blob in self._shared_strings:
            md5 = hashlib.md5(blob, usedforsecurity=False).digest()
            buf.extend(md5)
            buf.extend(write_binary_string(blob))
        return bytes(buf)

    # INST chunk

    def _build_inst(self, type_idx: int, class_name: str) -> bytes:
        instances = self._type_instances[type_idx]
        ids = [inst.referent for inst in instances]
        is_service = any(inst.is_service for inst in instances)

        buf = bytearray()
        buf.extend(write_u32(type_idx))
        buf.extend(write_string(class_name))
        buf.extend(write_u8(1 if is_service else 0))
        buf.extend(write_u32(len(ids)))
        buf.extend(encode_ids(ids))

        if is_service:
            for inst in instances:
                buf.extend(write_u8(1 if inst.is_service else 0))

        return bytes(buf)

    # PROP chunk

    @staticmethod
    def _collect_prop_names(instances: list[RbxInstance]) -> list[str]:
        """Return a sorted, deduplicated list of property names across instances."""
        names: set[str] = set()
        for inst in instances:
            names.update(inst.properties.keys())
        return sorted(names)

    def _build_prop(
        self,
        type_idx: int,
        prop_name: str,
        instances: list[RbxInstance],
    ) -> bytes | None:
        """Build a single PROP chunk.  Returns None if the property has no data."""
        # Gather values; use the first non-None prop to determine format
        fmt: PropertyFormat | None = None
        values: list[Any] = []
        for inst in instances:
            prop = inst.properties.get(prop_name)
            if prop is not None:
                if fmt is None:
                    fmt = prop.fmt
                values.append(prop.value)
            else:
                values.append(None)

        if fmt is None:
            return None  # property not present on any instance

        # Replace None with sensible defaults for the format
        values = [self._default_value(fmt) if v is None else v for v in values]

        encoded = self._encode_prop_values(fmt, values)
        if encoded is None:
            return None

        buf = bytearray()
        buf.extend(write_u32(type_idx))
        buf.extend(write_string(prop_name))
        buf.extend(write_u8(int(fmt)))
        buf.extend(encoded)
        return bytes(buf)

    @staticmethod
    def _default_value(fmt: PropertyFormat) -> Any:
        match fmt:
            case PropertyFormat.STRING:
                result: Any = b''
            case PropertyFormat.BOOL:
                result = False
            case PropertyFormat.INT | PropertyFormat.ENUM | PropertyFormat.BRICK_COLOR:
                result = 0
            case PropertyFormat.FLOAT | PropertyFormat.DOUBLE:
                result = 0.0
            case PropertyFormat.UDIM:
                result = {'S': 0.0, 'O': 0}
            case PropertyFormat.UDIM2:
                result = {'XS': 0.0, 'XO': 0, 'YS': 0.0, 'YO': 0}
            case PropertyFormat.RAY:
                result = {
                    'origin': {'X': 0.0, 'Y': 0.0, 'Z': 0.0},
                    'direction': {'X': 0.0, 'Y': 0.0, 'Z': 0.0},
                }
            case PropertyFormat.FACES | PropertyFormat.AXES:
                result = 0
            case PropertyFormat.COLOR3:
                result = {'R': 0.0, 'G': 0.0, 'B': 0.0}
            case PropertyFormat.VECTOR2:
                result = {'X': 0.0, 'Y': 0.0}
            case PropertyFormat.VECTOR3:
                result = {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
            case PropertyFormat.VECTOR2INT16:
                result = {'X': 0, 'Y': 0}
            case PropertyFormat.VECTOR3INT16:
                result = {'X': 0, 'Y': 0, 'Z': 0}
            case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
                result = {
                    'X': 0.0,
                    'Y': 0.0,
                    'Z': 0.0,
                    'R00': 1.0,
                    'R01': 0.0,
                    'R02': 0.0,
                    'R10': 0.0,
                    'R11': 1.0,
                    'R12': 0.0,
                    'R20': 0.0,
                    'R21': 0.0,
                    'R22': 1.0,
                }
            case PropertyFormat.OPTIONAL_CFRAME | PropertyFormat.REF:
                result = None
            case PropertyFormat.NUMBER_SEQUENCE | PropertyFormat.COLOR_SEQUENCE:
                result = []
            case PropertyFormat.NUMBER_RANGE:
                result = {'Min': 0.0, 'Max': 1.0}
            case PropertyFormat.RECT2D:
                result = {'min': {'X': 0.0, 'Y': 0.0}, 'max': {'X': 0.0, 'Y': 0.0}}
            case PropertyFormat.PHYSICAL_PROPERTIES:
                result = None
            case PropertyFormat.COLOR3UINT8:
                result = {'R': 0, 'G': 0, 'B': 0}
            case PropertyFormat.INT64 | PropertyFormat.SECURITY_CAPABILITIES:
                result = 0
            case PropertyFormat.SHARED_STRING | PropertyFormat.BYTECODE:
                result = b''
            case PropertyFormat.UNIQUE_ID:
                result = {'Index': 0, 'Time': 0, 'Random': 0}
            case PropertyFormat.FONT:
                result = {'Family': '', 'Weight': 400, 'Style': 0, 'CachedFaceId': ''}
            case PropertyFormat.CONTENT:
                result = None
            case _:
                result = None
        return result

    def _encode_prop_values(self, fmt: PropertyFormat, values: list[Any]) -> bytes | None:
        """Encode a list of property values in the binary RBXM format."""
        match fmt:
            case PropertyFormat.STRING:
                encoded = self._enc_strings(values)
            case PropertyFormat.BOOL:
                encoded = bytes([1 if v else 0 for v in values])
            case PropertyFormat.INT:
                encoded = interleave_i32([int(v) for v in values])
            case PropertyFormat.FLOAT:
                encoded = interleave_f32([float(v) for v in values])
            case PropertyFormat.DOUBLE:
                encoded = b''.join(write_f64(float(v)) for v in values)
            case PropertyFormat.UDIM:
                encoded = interleave_f32([float(v['S']) for v in values]) + interleave_i32(
                    [int(v['O']) for v in values]
                )
            case PropertyFormat.UDIM2:
                encoded = (
                    interleave_f32([float(v['XS']) for v in values])
                    + interleave_f32([float(v['YS']) for v in values])
                    + interleave_i32([int(v['XO']) for v in values])
                    + interleave_i32([int(v['YO']) for v in values])
                )
            case PropertyFormat.RAY:
                buf = bytearray()
                for v in values:
                    origin, direction = v['origin'], v['direction']
                    buf.extend(write_f32(origin['X']))
                    buf.extend(write_f32(origin['Y']))
                    buf.extend(write_f32(origin['Z']))
                    buf.extend(write_f32(direction['X']))
                    buf.extend(write_f32(direction['Y']))
                    buf.extend(write_f32(direction['Z']))
                encoded = bytes(buf)
            case PropertyFormat.FACES | PropertyFormat.AXES:
                encoded = bytes([int(v) for v in values])
            case PropertyFormat.BRICK_COLOR:
                encoded = interleave_u32([int(v) for v in values])
            case PropertyFormat.COLOR3:
                encoded = (
                    interleave_f32([float(v['R']) for v in values])
                    + interleave_f32([float(v['G']) for v in values])
                    + interleave_f32([float(v['B']) for v in values])
                )
            case PropertyFormat.VECTOR2:
                encoded = interleave_f32([float(v['X']) for v in values]) + interleave_f32(
                    [float(v['Y']) for v in values]
                )
            case PropertyFormat.VECTOR3:
                encoded = (
                    interleave_f32([float(v['X']) for v in values])
                    + interleave_f32([float(v['Y']) for v in values])
                    + interleave_f32([float(v['Z']) for v in values])
                )
            case PropertyFormat.VECTOR2INT16:
                encoded = b''.join(struct.pack('<hh', int(v['X']), int(v['Y'])) for v in values)
            case PropertyFormat.VECTOR3INT16:
                encoded = b''.join(
                    struct.pack('<hhh', int(v['X']), int(v['Y']), int(v['Z'])) for v in values
                )
            case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
                encoded = self._enc_cframes(values)
            case PropertyFormat.OPTIONAL_CFRAME:
                encoded = self._enc_optional_cframes(values)
            case PropertyFormat.ENUM:
                encoded = interleave_u32([int(v) for v in values])
            case PropertyFormat.REF:
                encoded = self._enc_refs(values)
            case PropertyFormat.NUMBER_SEQUENCE:
                encoded = self._enc_number_sequences(values)
            case PropertyFormat.COLOR_SEQUENCE:
                encoded = self._enc_color_sequences(values)
            case PropertyFormat.NUMBER_RANGE:
                buf = bytearray()
                for v in values:
                    buf.extend(write_f32(float(v['Min'])))
                    buf.extend(write_f32(float(v['Max'])))
                encoded = bytes(buf)
            case PropertyFormat.RECT2D:
                encoded = (
                    interleave_f32([float(v['min']['X']) for v in values])
                    + interleave_f32([float(v['min']['Y']) for v in values])
                    + interleave_f32([float(v['max']['X']) for v in values])
                    + interleave_f32([float(v['max']['Y']) for v in values])
                )
            case PropertyFormat.PHYSICAL_PROPERTIES:
                encoded = self._enc_physical_properties(values)
            case PropertyFormat.COLOR3UINT8:
                encoded = (
                    bytes([int(v['R']) for v in values])
                    + bytes([int(v['G']) for v in values])
                    + bytes([int(v['B']) for v in values])
                )
            case PropertyFormat.INT64:
                encoded = interleave_i64([int(v) for v in values])
            case PropertyFormat.SHARED_STRING:
                encoded = self._enc_shared_strings(values)
            case PropertyFormat.BYTECODE:
                encoded = self._enc_bytecodes(values)
            case PropertyFormat.UNIQUE_ID:
                encoded = self._enc_unique_ids(values)
            case PropertyFormat.FONT:
                encoded = self._enc_fonts(values)
            case PropertyFormat.SECURITY_CAPABILITIES:
                encoded = interleave_u64([int(v) for v in values])
            case PropertyFormat.CONTENT:
                encoded = self._enc_contents(values)
            case _:
                encoded = None
        return encoded

    # Property value encoders

    @staticmethod
    def _enc_strings(values: list[Any]) -> bytes:
        buf = bytearray()
        for v in values:
            if isinstance(v, bytes):
                buf.extend(write_binary_string(v))
            else:
                raw = str(v).encode('utf-8')
                buf.extend(write_u32(len(raw)))
                buf.extend(raw)
        return bytes(buf)

    @staticmethod
    def _enc_cframes(values: list[Any]) -> bytes:
        """Encode CFrame values.

        Always writes orient_id=0 followed by the full 9-float rotation
        matrix, then the positions as three interleaved float arrays.
        """
        buf = bytearray()
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for cf in values:
            buf.extend(write_u8(0))  # orient_id = 0 → custom matrix follows
            buf.extend(write_f32(float(cf['R00'])))
            buf.extend(write_f32(float(cf['R01'])))
            buf.extend(write_f32(float(cf['R02'])))
            buf.extend(write_f32(float(cf['R10'])))
            buf.extend(write_f32(float(cf['R11'])))
            buf.extend(write_f32(float(cf['R12'])))
            buf.extend(write_f32(float(cf['R20'])))
            buf.extend(write_f32(float(cf['R21'])))
            buf.extend(write_f32(float(cf['R22'])))
            xs.append(float(cf['X']))
            ys.append(float(cf['Y']))
            zs.append(float(cf['Z']))
        buf.extend(interleave_f32(xs))
        buf.extend(interleave_f32(ys))
        buf.extend(interleave_f32(zs))
        return bytes(buf)

    @staticmethod
    def _enc_refs(values: list[Any]) -> bytes:
        """Encode REF values as delta-encoded interleaved IDs, with -1 for null."""
        ids = [(-1 if v is None else int(v)) for v in values]
        return encode_ids(ids)

    @staticmethod
    def _enc_number_sequences(values: list[Any]) -> bytes:
        buf = bytearray()
        for seq in values:
            buf.extend(write_u32(len(seq)))
            for key in seq:
                buf.extend(write_f32(float(key['Time'])))
                buf.extend(write_f32(float(key['Value'])))
                buf.extend(write_f32(float(key['Envelope'])))
        return bytes(buf)

    @staticmethod
    def _enc_color_sequences(values: list[Any]) -> bytes:
        buf = bytearray()
        for seq in values:
            buf.extend(write_u32(len(seq)))
            for key in seq:
                buf.extend(write_f32(float(key['Time'])))
                buf.extend(write_f32(float(key['R'])))
                buf.extend(write_f32(float(key['G'])))
                buf.extend(write_f32(float(key['B'])))
                buf.extend(write_f32(0.0))  # envelope
        return bytes(buf)

    @staticmethod
    def _enc_physical_properties(values: list[Any]) -> bytes:
        buf = bytearray()
        for v in values:
            if v is None:
                buf.extend(write_u8(0))
            elif not v.get('CustomPhysics', True):
                buf.extend(write_u8(2 if v.get('HasAcousticAbsorption') else 0))
            else:
                has_acoustic_absorption = 'AcousticAbsorption' in v
                buf.extend(write_u8(3 if has_acoustic_absorption else 1))
                buf.extend(write_f32(float(v['Density'])))
                buf.extend(write_f32(float(v['Friction'])))
                buf.extend(write_f32(float(v['Elasticity'])))
                buf.extend(write_f32(float(v['FrictionWeight'])))
                buf.extend(write_f32(float(v['ElasticityWeight'])))
                if has_acoustic_absorption:
                    buf.extend(write_f32(float(v['AcousticAbsorption'])))
        return bytes(buf)

    def _enc_shared_strings(self, values: list[Any]) -> bytes:
        """Encode SHARED_STRING values as indices into the SSTR table."""
        indices: list[int] = []
        for v in values:
            if isinstance(v, bytes) and v in self._shared_string_index:
                indices.append(self._shared_string_index[v])
            else:
                indices.append(0)
        return interleave_u32(indices)

    @staticmethod
    def _enc_bytecodes(values: list[Any]) -> bytes:
        buf = bytearray()
        for value in values:
            if isinstance(value, bytes):
                buf.extend(write_binary_string(value))
            else:
                buf.extend(write_binary_string(str(value).encode('utf-8')))
        return bytes(buf)

    @staticmethod
    def _enc_optional_cframes(values: list[Any]) -> bytes:
        default = RbxmSerializer._default_value(PropertyFormat.CFRAME_MATRIX)
        cframes = [default if value is None else value for value in values]
        present = [value is not None for value in values]
        return (
            write_u8(int(PropertyFormat.CFRAME_MATRIX))
            + RbxmSerializer._enc_cframes(cframes)
            + write_u8(int(PropertyFormat.BOOL))
            + bytes([1 if value else 0 for value in present])
        )

    @staticmethod
    def _enc_unique_ids(values: list[Any]) -> bytes:
        records: list[bytes] = []
        for value in values:
            if isinstance(value, bytes):
                records.append(value)
                continue
            records.append(
                struct.pack(
                    '>IIQ',
                    int(value.get('Index', 0)) & 0xFFFF_FFFF,
                    int(value.get('Time', 0)) & 0xFFFF_FFFF,
                    int(value.get('Random', 0)) & 0xFFFF_FFFF_FFFF_FFFF,
                )
            )
        return interleave_bytes(records, 16)

    @staticmethod
    def _enc_fonts(values: list[Any]) -> bytes:
        style_names = {'Normal': 0, 'Italic': 1}
        buf = bytearray()
        for value in values:
            family = str(value.get('Family', '')).encode('utf-8')
            cached_face_id = str(value.get('CachedFaceId', '')).encode('utf-8')
            style = value.get('Style', 0)
            if isinstance(style, str):
                style = style_names.get(style, 0)
            buf.extend(write_binary_string(family))
            buf.extend(write_u16(int(value.get('Weight', 400))))
            buf.extend(write_u8(int(style)))
            buf.extend(write_binary_string(cached_face_id))
        return bytes(buf)

    @staticmethod
    def _enc_contents(values: list[Any]) -> bytes:
        source_types: list[int] = []
        uris: list[str] = []
        object_refs: list[int] = []
        external_object_refs: list[int] = []

        for value in values:
            if value is None:
                source_types.append(0)
            elif isinstance(value, str):
                if value:
                    source_types.append(1)
                    uris.append(value)
                else:
                    source_types.append(0)
            elif value.get('SourceType') == 'Uri':
                source_types.append(1)
                uris.append(str(value.get('Uri', '')))
            elif value.get('SourceType') == 'Object':
                source_types.append(2)
                ref = -1 if value.get('Ref') is None else int(value['Ref'])
                if value.get('External'):
                    external_object_refs.append(ref)
                else:
                    object_refs.append(ref)
            else:
                source_types.append(int(value.get('SourceType', 0)))

        buf = bytearray()
        buf.extend(interleave_u32(source_types))
        buf.extend(write_u32(len(uris)))
        for uri in uris:
            buf.extend(write_binary_string(uri.encode('utf-8')))
        buf.extend(write_u32(len(object_refs)))
        buf.extend(encode_ids(object_refs))
        buf.extend(write_u32(len(external_object_refs)))
        buf.extend(encode_ids(external_object_refs))
        return bytes(buf)

    def _build_raw_props(self) -> list[bytes]:
        props: list[bytes] = []
        for raw in self._doc.raw_property_chunks:
            prop = self._build_raw_prop(raw)
            if prop is not None:
                props.append(prop)
        return props

    def _build_raw_prop(self, raw: RbxRawPropertyChunk) -> bytes | None:
        type_idx = self._type_index.get(raw.class_name)
        if type_idx is None:
            return None
        if len(self._type_instances[type_idx]) != raw.instance_count:
            return None

        buf = bytearray()
        buf.extend(write_u32(type_idx))
        buf.extend(write_string(raw.prop_name))
        buf.extend(write_u8(raw.fmt_byte))
        buf.extend(raw.value_data)
        return bytes(buf)

    # PRNT chunk

    def _build_prnt(self) -> bytes:
        """Build the PRNT chunk.

        Every instance must appear here — children with their real parent
        referent, root instances with parent referent -1.  The engine
        validates this and rejects files where instances are absent.
        """
        # Pre-build a child→parent referent map for O(n) lookup
        child_to_parent: dict[int, int] = {}
        for inst in self._all_instances:
            for child in inst.children:
                child_to_parent[child.referent] = inst.referent

        child_ids: list[int] = []
        parent_ids: list[int] = []
        for inst in self._all_instances:
            child_ids.append(inst.referent)
            parent_ids.append(child_to_parent.get(inst.referent, -1))

        buf = bytearray()
        buf.extend(write_u8(0))  # format byte
        buf.extend(write_u32(len(child_ids)))
        buf.extend(encode_ids(child_ids))
        buf.extend(encode_ids(parent_ids))
        return bytes(buf)
