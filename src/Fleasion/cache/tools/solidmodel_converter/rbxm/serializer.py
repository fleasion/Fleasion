"""RBXM binary format serializer.

Exact inverse of deserializer.py — converts an RbxDocument back to the
Roblox binary model format (.rbxm).

Reference: RbxmDeserializer in deserializer.py and
           App/v8xml/SerializerBinary.cpp (Roblox 2016 source).
"""

from __future__ import annotations

import struct
from collections import defaultdict
from typing import Any

import lz4.block  # type: ignore[import-untyped]

from .binary_writer import (
    encode_ids,
    interleave_f32,
    interleave_i32,
    interleave_i64,
    interleave_u32,
    write_binary_string,
    write_f32,
    write_f64,
    write_string,
    write_u8,
    write_u32,
)
from .types import PropertyFormat, RbxDocument, RbxInstance, RbxProperty

MAGIC_HEADER = b'<roblox!\x89\xff\x0d\x0a\x1a\x0a'
FILE_VERSION = 0  # same version the deserializer reads


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_rbxm(doc: RbxDocument) -> bytes:
    """Serialize an RbxDocument to raw RBXM binary bytes."""
    s = RbxmSerializer(doc)
    return s.serialize()


# ---------------------------------------------------------------------------
# Serializer
# ---------------------------------------------------------------------------


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

    # ------------------------------------------------------------------
    # Pre-pass: assign type indices and walk instance tree
    # ------------------------------------------------------------------

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
                if prop.fmt == PropertyFormat.SHARED_STRING and isinstance(prop.value, bytes):
                    if prop.value not in self._shared_string_index:
                        self._shared_string_index[prop.value] = len(self._shared_strings)
                        self._shared_strings.append(prop.value)

    # ------------------------------------------------------------------
    # Top-level serialize
    # ------------------------------------------------------------------

    def serialize(self) -> bytes:
        type_count   = len(self._type_index)
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

    # ------------------------------------------------------------------
    # Chunk framing
    # ------------------------------------------------------------------

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

        compressed = lz4.block.compress(data, store_size=False)
        if len(compressed) < uncompressed_size:
            return (
                name_b
                + struct.pack('<III', len(compressed), uncompressed_size, 0)
                + compressed
            )
        else:
            # Uncompressed: compressed_size field = 0
            return (
                name_b
                + struct.pack('<III', 0, uncompressed_size, 0)
                + data
            )

    # ------------------------------------------------------------------
    # META chunk
    # ------------------------------------------------------------------

    def _build_meta(self) -> bytes:
        buf = bytearray()
        entries = self._doc.metadata.entries
        buf.extend(write_u32(len(entries)))
        for key, value in entries.items():
            buf.extend(write_string(key))
            buf.extend(write_string(value))
        return bytes(buf)

    # ------------------------------------------------------------------
    # SSTR chunk
    # ------------------------------------------------------------------

    def _build_sstr(self) -> bytes:
        import hashlib, base64  # noqa: E401 — local import to keep module light
        buf = bytearray()
        buf.extend(write_u32(0))  # version
        buf.extend(write_u32(len(self._shared_strings)))
        for blob in self._shared_strings:
            md5 = hashlib.md5(blob).digest()  # noqa: S324
            buf.extend(md5)
            buf.extend(write_binary_string(blob))
        return bytes(buf)

    # ------------------------------------------------------------------
    # INST chunk
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # PROP chunk
    # ------------------------------------------------------------------

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
            case PropertyFormat.STRING:                     return b''
            case PropertyFormat.BOOL:                       return False
            case PropertyFormat.INT | PropertyFormat.ENUM | PropertyFormat.BRICK_COLOR: return 0
            case PropertyFormat.FLOAT | PropertyFormat.DOUBLE: return 0.0
            case PropertyFormat.UDIM:                       return {'S': 0.0, 'O': 0}
            case PropertyFormat.UDIM2:                      return {'XS': 0.0, 'XO': 0, 'YS': 0.0, 'YO': 0}
            case PropertyFormat.RAY:                        return {'origin': {'X': 0.0, 'Y': 0.0, 'Z': 0.0}, 'direction': {'X': 0.0, 'Y': 0.0, 'Z': 0.0}}
            case PropertyFormat.FACES | PropertyFormat.AXES: return 0
            case PropertyFormat.COLOR3:                     return {'R': 0.0, 'G': 0.0, 'B': 0.0}
            case PropertyFormat.VECTOR2:                    return {'X': 0.0, 'Y': 0.0}
            case PropertyFormat.VECTOR3:                    return {'X': 0.0, 'Y': 0.0, 'Z': 0.0}
            case PropertyFormat.VECTOR2INT16:               return {'X': 0, 'Y': 0}
            case PropertyFormat.VECTOR3INT16:               return {'X': 0, 'Y': 0, 'Z': 0}
            case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
                return {'X': 0.0, 'Y': 0.0, 'Z': 0.0,
                        'R00': 1.0, 'R01': 0.0, 'R02': 0.0,
                        'R10': 0.0, 'R11': 1.0, 'R12': 0.0,
                        'R20': 0.0, 'R21': 0.0, 'R22': 1.0}
            case PropertyFormat.REF:                        return None
            case PropertyFormat.NUMBER_SEQUENCE:            return []
            case PropertyFormat.COLOR_SEQUENCE:             return []
            case PropertyFormat.NUMBER_RANGE:               return {'Min': 0.0, 'Max': 1.0}
            case PropertyFormat.RECT2D:                     return {'min': {'X': 0.0, 'Y': 0.0}, 'max': {'X': 0.0, 'Y': 0.0}}
            case PropertyFormat.PHYSICAL_PROPERTIES:        return None
            case PropertyFormat.COLOR3UINT8:                return {'R': 0, 'G': 0, 'B': 0}
            case PropertyFormat.INT64:                      return 0
            case PropertyFormat.SHARED_STRING:              return b''
            case _:                                         return None

    def _encode_prop_values(self, fmt: PropertyFormat, values: list[Any]) -> bytes | None:
        """Encode a list of property values in the binary RBXM format."""
        match fmt:
            case PropertyFormat.STRING:
                return self._enc_strings(values)
            case PropertyFormat.BOOL:
                return bytes([1 if v else 0 for v in values])
            case PropertyFormat.INT:
                return interleave_i32([int(v) for v in values])
            case PropertyFormat.FLOAT:
                return interleave_f32([float(v) for v in values])
            case PropertyFormat.DOUBLE:
                return b''.join(write_f64(float(v)) for v in values)
            case PropertyFormat.UDIM:
                return (interleave_f32([float(v['S']) for v in values])
                      + interleave_i32([int(v['O'])   for v in values]))
            case PropertyFormat.UDIM2:
                return (interleave_f32([float(v['XS']) for v in values])
                      + interleave_f32([float(v['YS']) for v in values])
                      + interleave_i32([int(v['XO'])   for v in values])
                      + interleave_i32([int(v['YO'])   for v in values]))
            case PropertyFormat.RAY:
                buf = bytearray()
                for v in values:
                    o, d = v['origin'], v['direction']
                    buf.extend(write_f32(o['X'])); buf.extend(write_f32(o['Y'])); buf.extend(write_f32(o['Z']))
                    buf.extend(write_f32(d['X'])); buf.extend(write_f32(d['Y'])); buf.extend(write_f32(d['Z']))
                return bytes(buf)
            case PropertyFormat.FACES | PropertyFormat.AXES:
                return bytes([int(v) for v in values])
            case PropertyFormat.BRICK_COLOR:
                return interleave_u32([int(v) for v in values])
            case PropertyFormat.COLOR3:
                return (interleave_f32([float(v['R']) for v in values])
                      + interleave_f32([float(v['G']) for v in values])
                      + interleave_f32([float(v['B']) for v in values]))
            case PropertyFormat.VECTOR2:
                return (interleave_f32([float(v['X']) for v in values])
                      + interleave_f32([float(v['Y']) for v in values]))
            case PropertyFormat.VECTOR3:
                return (interleave_f32([float(v['X']) for v in values])
                      + interleave_f32([float(v['Y']) for v in values])
                      + interleave_f32([float(v['Z']) for v in values]))
            case PropertyFormat.VECTOR2INT16:
                return b''.join(
                    struct.pack('<hh', int(v['X']), int(v['Y'])) for v in values
                )
            case PropertyFormat.VECTOR3INT16:
                return b''.join(
                    struct.pack('<hhh', int(v['X']), int(v['Y']), int(v['Z'])) for v in values
                )
            case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
                return self._enc_cframes(values)
            case PropertyFormat.ENUM:
                return interleave_u32([int(v) for v in values])
            case PropertyFormat.REF:
                return self._enc_refs(values)
            case PropertyFormat.NUMBER_SEQUENCE:
                return self._enc_number_sequences(values)
            case PropertyFormat.COLOR_SEQUENCE:
                return self._enc_color_sequences(values)
            case PropertyFormat.NUMBER_RANGE:
                buf = bytearray()
                for v in values:
                    buf.extend(write_f32(float(v['Min'])))
                    buf.extend(write_f32(float(v['Max'])))
                return bytes(buf)
            case PropertyFormat.RECT2D:
                return (interleave_f32([float(v['min']['X']) for v in values])
                      + interleave_f32([float(v['min']['Y']) for v in values])
                      + interleave_f32([float(v['max']['X']) for v in values])
                      + interleave_f32([float(v['max']['Y']) for v in values]))
            case PropertyFormat.PHYSICAL_PROPERTIES:
                return self._enc_physical_properties(values)
            case PropertyFormat.COLOR3UINT8:
                return (bytes([int(v['R']) for v in values])
                      + bytes([int(v['G']) for v in values])
                      + bytes([int(v['B']) for v in values]))
            case PropertyFormat.INT64:
                return interleave_i64([int(v) for v in values])
            case PropertyFormat.SHARED_STRING:
                return self._enc_shared_strings(values)
            case _:
                return None

    # ------------------------------------------------------------------
    # Property value encoders
    # ------------------------------------------------------------------

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
        xs, ys, zs = [], [], []
        for cf in values:
            buf.extend(write_u8(0))  # orient_id = 0 → custom matrix follows
            buf.extend(write_f32(float(cf['R00']))); buf.extend(write_f32(float(cf['R01']))); buf.extend(write_f32(float(cf['R02'])))
            buf.extend(write_f32(float(cf['R10']))); buf.extend(write_f32(float(cf['R11']))); buf.extend(write_f32(float(cf['R12'])))
            buf.extend(write_f32(float(cf['R20']))); buf.extend(write_f32(float(cf['R21']))); buf.extend(write_f32(float(cf['R22'])))
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
            else:
                buf.extend(write_u8(1))
                buf.extend(write_f32(float(v['Density'])))
                buf.extend(write_f32(float(v['Friction'])))
                buf.extend(write_f32(float(v['Elasticity'])))
                buf.extend(write_f32(float(v['FrictionWeight'])))
                buf.extend(write_f32(float(v['ElasticityWeight'])))
        return bytes(buf)

    def _enc_shared_strings(self, values: list[Any]) -> bytes:
        """Encode SHARED_STRING values as indices into the SSTR table."""
        indices = []
        for v in values:
            if isinstance(v, bytes) and v in self._shared_string_index:
                indices.append(self._shared_string_index[v])
            else:
                indices.append(0)
        return interleave_u32(indices)

    # ------------------------------------------------------------------
    # PRNT chunk
    # ------------------------------------------------------------------

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