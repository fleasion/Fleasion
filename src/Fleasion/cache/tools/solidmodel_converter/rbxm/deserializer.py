"""RBXM binary format deserializer.

Parses the Roblox binary model format into an in-memory RbxDocument.
Reference: ROBLOX 2016 source App/v8xml/SerializerBinary.cpp
"""

from __future__ import annotations

import logging
import struct
from typing import Any, cast

import lz4.block  # type: ignore[import-untyped]

from .binary_reader import (
    decode_ids,
    deinterleave_f32,
    deinterleave_i32,
    deinterleave_i64,
    deinterleave_u32,
    read_binary_string,
    read_bytes,
    read_f32,
    read_f64,
    read_string,
    read_u8,
    read_u32,
)
from .types import (
    PropertyFormat,
    RbxDocument,
    RbxInstance,
    RbxMetadata,
    RbxProperty,
    RbxTypeInfo,
)

log = logging.getLogger(__name__)

MAGIC_HEADER = b'<roblox!\x89\xff\x0d\x0a\x1a\x0a'
FILE_HEADER_SIZE = 32  # 14 (magic+sig) + 2 (version) + 4 + 4 + 8 (reserved)

# 24 axis-aligned rotation matrices (orientation IDs 0..23).
# Each is a 3x3 matrix stored row-major as 9 floats.
_ORIENTATION_MATRICES: dict[int, tuple[float, ...]] = {
    0: (1, 0, 0, 0, 1, 0, 0, 0, 1),
    1: (1, 0, 0, 0, 0, -1, 0, 1, 0),
    2: (1, 0, 0, 0, -1, 0, 0, 0, -1),
    3: (1, 0, 0, 0, 0, 1, 0, -1, 0),
    4: (0, 1, 0, 1, 0, 0, 0, 0, -1),
    5: (0, 0, 1, 1, 0, 0, 0, 1, 0),
    6: (0, -1, 0, 1, 0, 0, 0, 0, 1),
    7: (0, 0, -1, 1, 0, 0, 0, -1, 0),
    8: (0, 1, 0, 0, 0, 1, 1, 0, 0),
    9: (0, 0, -1, 0, 1, 0, 1, 0, 0),
    10: (0, -1, 0, 0, 0, -1, 1, 0, 0),
    11: (0, 0, 1, 0, -1, 0, 1, 0, 0),
    12: (-1, 0, 0, 0, 1, 0, 0, 0, -1),
    13: (-1, 0, 0, 0, 0, 1, 0, 1, 0),
    14: (-1, 0, 0, 0, -1, 0, 0, 0, 1),
    15: (-1, 0, 0, 0, 0, -1, 0, -1, 0),
    16: (0, 1, 0, -1, 0, 0, 0, 0, 1),
    17: (0, 0, -1, -1, 0, 0, 0, 1, 0),
    18: (0, -1, 0, -1, 0, 0, 0, 0, -1),
    19: (0, 0, 1, -1, 0, 0, 0, -1, 0),
    20: (0, 1, 0, 0, 0, -1, -1, 0, 0),
    21: (0, 0, 1, 0, 1, 0, -1, 0, 0),
    22: (0, -1, 0, 0, 0, 1, -1, 0, 0),
    23: (0, 0, -1, 0, -1, 0, -1, 0, 0),
}


class RbxmDeserializer:
    """Deserializes a Roblox binary model (.rbxm) stream."""

    def __init__(self) -> None:
        self._type_infos: list[RbxTypeInfo] = []
        self._instances: dict[int, RbxInstance] = {}
        self._metadata = RbxMetadata()
        self._shared_strings: list[bytes] = []
        self._version: int = 0
        self._type_count: int = 0
        self._object_count: int = 0

    def deserialize(self, data: bytes) -> RbxDocument:
        """Parse a complete RBXM binary blob into an RbxDocument."""
        offset = self._read_file_header(data)
        offset = self._read_chunks(data, offset)
        roots = self._build_tree()

        return RbxDocument(
            version=self._version,
            type_count=self._type_count,
            object_count=self._object_count,
            metadata=self._metadata,
            instances=self._instances,
            roots=roots,
            shared_strings=self._shared_strings,
        )

    # --- Header ---

    def _read_file_header(self, data: bytes) -> int:
        magic = data[:14]
        if magic != MAGIC_HEADER:
            msg = f'Invalid RBXM header: {magic!r}'
            raise ValueError(msg)

        self._version = struct.unpack_from('<H', data, 14)[0]
        self._type_count = struct.unpack_from('<I', data, 16)[0]
        self._object_count = struct.unpack_from('<I', data, 20)[0]
        # bytes 24..31 are reserved

        log.info(
            'RBXM v%d: %d types, %d objects',
            self._version,
            self._type_count,
            self._object_count,
        )
        return FILE_HEADER_SIZE

    # --- Chunk reading ---

    def _read_chunks(self, data: bytes, offset: int) -> int:
        while offset < len(data):
            chunk_name = data[offset : offset + 4].decode('ascii')
            compressed_size = struct.unpack_from('<I', data, offset + 4)[0]
            uncompressed_size = struct.unpack_from('<I', data, offset + 8)[0]
            # offset+12: reserved u32
            offset += 16

            chunk_data: bytes
            if compressed_size == 0:
                # Uncompressed chunk
                chunk_data = data[offset : offset + uncompressed_size]
                offset += uncompressed_size
            else:
                # LZ4-compressed chunk
                raw = data[offset : offset + compressed_size]
                chunk_data = cast(
                    'bytes',
                    lz4.block.decompress(  # type: ignore[reportUnknownMemberType]
                        raw, uncompressed_size=uncompressed_size
                    ),
                )
                offset += compressed_size

            self._process_chunk(chunk_name, chunk_data)

            if chunk_name == 'END\x00':
                break

        return offset

    def _process_chunk(self, name: str, data: bytes) -> None:
        handler = {
            'META': self._handle_meta,
            'SSTR': self._handle_sstr,
            'INST': self._handle_inst,
            'PROP': self._handle_prop,
            'PRNT': self._handle_prnt,
        }.get(name)

        if handler is not None:
            handler(data)
        elif name == 'END\x00':
            log.debug('END chunk reached')
        else:
            log.warning('Unknown chunk type: %r', name)

    # --- META ---

    def _handle_meta(self, data: bytes) -> None:
        offset = 0
        count, offset = read_u32(data, offset)
        for _ in range(count):
            key, offset = read_string(data, offset)
            value, offset = read_string(data, offset)
            self._metadata.entries[key] = value
            log.debug('META: %s = %s', key, value)

    # --- SSTR (shared strings) ---

    def _handle_sstr(self, data: bytes) -> None:
        offset = 0
        _version, offset = read_u32(data, offset)
        count, offset = read_u32(data, offset)
        for _ in range(count):
            _md5, offset = read_bytes(data, offset, 16)
            blob, offset = read_binary_string(data, offset)
            self._shared_strings.append(blob)

    # --- INST ---

    def _handle_inst(self, data: bytes) -> None:
        offset = 0
        type_index, offset = read_u32(data, offset)
        class_name, offset = read_string(data, offset)
        is_service_byte, offset = read_u8(data, offset)
        is_service = is_service_byte != 0
        id_count, offset = read_u32(data, offset)

        ids, offset = decode_ids(data, offset, id_count)

        # If service type, read the service rooted flags
        service_flags: list[bool] = []
        if is_service:
            for _ in range(id_count):
                flag, offset = read_u8(data, offset)
                service_flags.append(flag != 0)

        info = RbxTypeInfo(
            type_index=type_index,
            class_name=class_name,
            is_service=is_service,
            instance_ids=ids,
        )

        # Extend list if needed
        while len(self._type_infos) <= type_index:
            self._type_infos.append(
                RbxTypeInfo(
                    type_index=len(self._type_infos),
                    class_name='',
                    is_service=False,
                    instance_ids=[],
                )
            )
        self._type_infos[type_index] = info

        # Create instance objects
        for i, inst_id in enumerate(ids):
            inst = RbxInstance(
                class_name=class_name,
                referent=inst_id,
                is_service=is_service and i < len(service_flags) and service_flags[i],
            )
            self._instances[inst_id] = inst

        log.debug(
            'INST[%d]: %s x%d (service=%s)',
            type_index,
            class_name,
            id_count,
            is_service,
        )

    # --- PROP ---

    def _handle_prop(self, data: bytes) -> None:
        offset = 0
        type_index, offset = read_u32(data, offset)
        prop_name, offset = read_string(data, offset)
        fmt_byte, offset = read_u8(data, offset)

        try:
            fmt = PropertyFormat(fmt_byte)
        except ValueError:
            log.warning(
                'Unknown property format %d for %s, skipping', fmt_byte, prop_name
            )
            return

        if type_index >= len(self._type_infos):
            log.warning('PROP references unknown type index %d', type_index)
            return

        info = self._type_infos[type_index]
        count = len(info.instance_ids)

        values = self._read_property_values(fmt, data, offset, count)

        for i, inst_id in enumerate(info.instance_ids):
            if inst_id in self._instances and i < len(values):
                self._instances[inst_id].properties[prop_name] = RbxProperty(
                    name=prop_name,
                    fmt=fmt,
                    value=values[i],
                )

        log.debug(
            'PROP[%d].%s: fmt=%s, %d values',
            type_index,
            prop_name,
            fmt.name,
            len(values),
        )

    def _read_property_values(
        self,
        fmt: PropertyFormat,
        data: bytes,
        offset: int,
        count: int,
    ) -> list[Any]:
        """Decode property values based on the format type."""
        match fmt:
            case PropertyFormat.STRING:
                return self._read_strings(data, offset, count)
            case PropertyFormat.BOOL:
                return self._read_bools(data, offset, count)
            case PropertyFormat.INT:
                return self._read_ints(data, offset, count)
            case PropertyFormat.FLOAT:
                return self._read_floats(data, offset, count)
            case PropertyFormat.DOUBLE:
                return self._read_doubles(data, offset, count)
            case PropertyFormat.UDIM:
                return self._read_udims(data, offset, count)
            case PropertyFormat.UDIM2:
                return self._read_udim2s(data, offset, count)
            case PropertyFormat.RAY:
                return self._read_rays(data, offset, count)
            case PropertyFormat.FACES:
                return self._read_faces(data, offset, count)
            case PropertyFormat.AXES:
                return self._read_axes(data, offset, count)
            case PropertyFormat.BRICK_COLOR:
                return self._read_brick_colors(data, offset, count)
            case PropertyFormat.COLOR3:
                return self._read_color3s(data, offset, count)
            case PropertyFormat.VECTOR2:
                return self._read_vector2s(data, offset, count)
            case PropertyFormat.VECTOR3:
                return self._read_vector3s(data, offset, count)
            case PropertyFormat.VECTOR2INT16:
                return self._read_vector2int16s(data, offset, count)
            case PropertyFormat.CFRAME_MATRIX | PropertyFormat.CFRAME_QUAT:
                return self._read_cframes(data, offset, count, fmt)
            case PropertyFormat.ENUM:
                return self._read_enums(data, offset, count)
            case PropertyFormat.REF:
                return self._read_refs(data, offset, count)
            case PropertyFormat.VECTOR3INT16:
                return self._read_vector3int16s(data, offset, count)
            case PropertyFormat.NUMBER_SEQUENCE:
                return self._read_number_sequences(data, offset, count)
            case PropertyFormat.COLOR_SEQUENCE:
                return self._read_color_sequences(data, offset, count)
            case PropertyFormat.NUMBER_RANGE:
                return self._read_number_ranges(data, offset, count)
            case PropertyFormat.RECT2D:
                return self._read_rect2ds(data, offset, count)
            case PropertyFormat.PHYSICAL_PROPERTIES:
                return self._read_physical_properties(data, offset, count)
            case PropertyFormat.COLOR3UINT8:
                return self._read_color3uint8s(data, offset, count)
            case PropertyFormat.INT64:
                return self._read_int64s(data, offset, count)
            case PropertyFormat.SHARED_STRING:
                return self._read_shared_strings(data, offset, count)
            case _:
                log.warning('Unhandled property format: %s', fmt)
                return [None] * count

    # --- Property readers ---

    def _read_strings(self, data: bytes, offset: int, count: int) -> list[str | bytes]:
        results: list[str | bytes] = []
        for _ in range(count):
            raw, offset = read_binary_string(data, offset)
            try:
                results.append(raw.decode('utf-8'))
            except UnicodeDecodeError:
                # Binary string (e.g. MeshData, ChildData, PhysicsData)
                results.append(raw)
        return results

    def _read_bools(self, data: bytes, offset: int, count: int) -> list[bool]:
        return [data[offset + i] != 0 for i in range(count)]

    def _read_ints(self, data: bytes, offset: int, count: int) -> list[int]:
        return deinterleave_i32(data, offset, count)

    def _read_floats(self, data: bytes, offset: int, count: int) -> list[float]:
        return deinterleave_f32(data, offset, count)

    def _read_doubles(self, data: bytes, offset: int, count: int) -> list[float]:
        results: list[float] = []
        for i in range(count):
            val, _ = read_f64(data, offset + i * 8)
            results.append(val)
        return results

    def _read_udims(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float | int]]:
        scales = deinterleave_f32(data, offset, count)
        offsets = deinterleave_i32(data, offset + count * 4, count)
        return [{'S': scales[i], 'O': offsets[i]} for i in range(count)]

    def _read_udim2s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float | int]]:
        xs = deinterleave_f32(data, offset, count)
        ys = deinterleave_f32(data, offset + count * 4, count)
        xo = deinterleave_i32(data, offset + count * 8, count)
        yo = deinterleave_i32(data, offset + count * 12, count)
        return [
            {'XS': xs[i], 'XO': xo[i], 'YS': ys[i], 'YO': yo[i]} for i in range(count)
        ]

    def _read_rays(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, dict[str, float]]]:
        results: list[dict[str, dict[str, float]]] = []
        for _ in range(count):
            ox, offset = read_f32(data, offset)
            oy, offset = read_f32(data, offset)
            oz, offset = read_f32(data, offset)
            dx, offset = read_f32(data, offset)
            dy, offset = read_f32(data, offset)
            dz, offset = read_f32(data, offset)
            results.append(
                {
                    'origin': {'X': ox, 'Y': oy, 'Z': oz},
                    'direction': {'X': dx, 'Y': dy, 'Z': dz},
                }
            )
        return results

    def _read_faces(self, data: bytes, offset: int, count: int) -> list[int]:
        return [data[offset + i] for i in range(count)]

    def _read_axes(self, data: bytes, offset: int, count: int) -> list[int]:
        return [data[offset + i] for i in range(count)]

    def _read_brick_colors(self, data: bytes, offset: int, count: int) -> list[int]:
        return deinterleave_u32(data, offset, count)

    def _read_color3s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float]]:
        rs = deinterleave_f32(data, offset, count)
        gs = deinterleave_f32(data, offset + count * 4, count)
        bs = deinterleave_f32(data, offset + count * 8, count)
        return [{'R': rs[i], 'G': gs[i], 'B': bs[i]} for i in range(count)]

    def _read_vector2s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float]]:
        xs = deinterleave_f32(data, offset, count)
        ys = deinterleave_f32(data, offset + count * 4, count)
        return [{'X': xs[i], 'Y': ys[i]} for i in range(count)]

    def _read_vector3s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float]]:
        xs = deinterleave_f32(data, offset, count)
        ys = deinterleave_f32(data, offset + count * 4, count)
        zs = deinterleave_f32(data, offset + count * 8, count)
        return [{'X': xs[i], 'Y': ys[i], 'Z': zs[i]} for i in range(count)]

    def _read_vector2int16s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, int]]:
        results: list[dict[str, int]] = []
        for _ in range(count):
            x = struct.unpack_from('<h', data, offset)[0]
            y = struct.unpack_from('<h', data, offset + 2)[0]
            offset += 4
            results.append({'X': x, 'Y': y})
        return results

    def _read_vector3int16s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, int]]:
        results: list[dict[str, int]] = []
        for _ in range(count):
            x = struct.unpack_from('<h', data, offset)[0]
            y = struct.unpack_from('<h', data, offset + 2)[0]
            z = struct.unpack_from('<h', data, offset + 4)[0]
            offset += 6
            results.append({'X': x, 'Y': y, 'Z': z})
        return results

    def _read_cframes(
        self,
        data: bytes,
        offset: int,
        count: int,
        fmt: PropertyFormat,
    ) -> list[dict[str, float]]:
        """Read CFrame values (rotation + interleaved position)."""
        rotations: list[tuple[float, ...]] = []

        for _ in range(count):
            orient_id, offset = read_u8(data, offset)
            if orient_id != 0:
                # Axis-aligned rotation from lookup table
                mat_idx = orient_id - 1
                mat = _ORIENTATION_MATRICES.get(mat_idx, (1, 0, 0, 0, 1, 0, 0, 0, 1))
                rotations.append(mat)
            elif fmt == PropertyFormat.CFRAME_QUAT:
                # Quaternion: 4 floats
                qx, offset = read_f32(data, offset)
                qy, offset = read_f32(data, offset)
                qz, offset = read_f32(data, offset)
                qw, offset = read_f32(data, offset)
                # Convert quaternion to rotation matrix
                rotations.append(_quat_to_matrix(qx, qy, qz, qw))
            else:
                # Full 3x3 matrix: 9 floats
                vals: list[float] = []
                for _ in range(9):
                    v, offset = read_f32(data, offset)
                    vals.append(v)
                rotations.append(tuple(vals))

        # Position components are interleaved after all rotations
        xs = deinterleave_f32(data, offset, count)
        ys = deinterleave_f32(data, offset + count * 4, count)
        zs = deinterleave_f32(data, offset + count * 8, count)

        results: list[dict[str, float]] = []
        for i in range(count):
            r = rotations[i]
            results.append(
                {
                    'X': xs[i],
                    'Y': ys[i],
                    'Z': zs[i],
                    'R00': r[0],
                    'R01': r[1],
                    'R02': r[2],
                    'R10': r[3],
                    'R11': r[4],
                    'R12': r[5],
                    'R20': r[6],
                    'R21': r[7],
                    'R22': r[8],
                }
            )
        return results

    def _read_enums(self, data: bytes, offset: int, count: int) -> list[int]:
        return deinterleave_u32(data, offset, count)

    def _read_refs(self, data: bytes, offset: int, count: int) -> list[int | None]:
        ids, _ = decode_ids(data, offset, count)
        return [None if v == -1 else v for v in ids]

    def _read_number_sequences(
        self, data: bytes, offset: int, count: int
    ) -> list[list[dict[str, float]]]:
        results: list[list[dict[str, float]]] = []
        for _ in range(count):
            num_keys, offset = read_u32(data, offset)
            keys: list[dict[str, float]] = []
            for _ in range(num_keys):
                time, offset = read_f32(data, offset)
                value, offset = read_f32(data, offset)
                envelope, offset = read_f32(data, offset)
                keys.append({'Time': time, 'Value': value, 'Envelope': envelope})
            results.append(keys)
        return results

    def _read_color_sequences(
        self, data: bytes, offset: int, count: int
    ) -> list[list[dict[str, float]]]:
        results: list[list[dict[str, float]]] = []
        for _ in range(count):
            num_keys, offset = read_u32(data, offset)
            keys: list[dict[str, float]] = []
            for _ in range(num_keys):
                time, offset = read_f32(data, offset)
                r, offset = read_f32(data, offset)
                g, offset = read_f32(data, offset)
                b, offset = read_f32(data, offset)
                _envelope, offset = read_f32(data, offset)
                keys.append({'Time': time, 'R': r, 'G': g, 'B': b})
            results.append(keys)
        return results

    def _read_number_ranges(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, float]]:
        results: list[dict[str, float]] = []
        for _ in range(count):
            low, offset = read_f32(data, offset)
            high, offset = read_f32(data, offset)
            results.append({'Min': low, 'Max': high})
        return results

    def _read_rect2ds(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, dict[str, float]]]:
        x0s = deinterleave_f32(data, offset, count)
        y0s = deinterleave_f32(data, offset + count * 4, count)
        x1s = deinterleave_f32(data, offset + count * 8, count)
        y1s = deinterleave_f32(data, offset + count * 12, count)
        return [
            {
                'min': {'X': x0s[i], 'Y': y0s[i]},
                'max': {'X': x1s[i], 'Y': y1s[i]},
            }
            for i in range(count)
        ]

    def _read_physical_properties(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, Any] | None]:
        results: list[dict[str, Any] | None] = []
        for _ in range(count):
            custom, offset = read_u8(data, offset)
            if custom != 0:
                density, offset = read_f32(data, offset)
                friction, offset = read_f32(data, offset)
                elasticity, offset = read_f32(data, offset)
                friction_weight, offset = read_f32(data, offset)
                elasticity_weight, offset = read_f32(data, offset)
                results.append(
                    {
                        'CustomPhysics': True,
                        'Density': density,
                        'Friction': friction,
                        'Elasticity': elasticity,
                        'FrictionWeight': friction_weight,
                        'ElasticityWeight': elasticity_weight,
                    }
                )
            else:
                results.append(None)
        return results

    def _read_color3uint8s(
        self, data: bytes, offset: int, count: int
    ) -> list[dict[str, int]]:
        rs = data[offset : offset + count]
        gs = data[offset + count : offset + 2 * count]
        bs = data[offset + 2 * count : offset + 3 * count]
        return [{'R': rs[i], 'G': gs[i], 'B': bs[i]} for i in range(count)]

    def _read_int64s(self, data: bytes, offset: int, count: int) -> list[int]:
        return deinterleave_i64(data, offset, count)

    def _read_shared_strings(self, data: bytes, offset: int, count: int) -> list[bytes]:
        indices = deinterleave_u32(data, offset, count)
        return [
            self._shared_strings[idx] if idx < len(self._shared_strings) else b''
            for idx in indices
        ]

    # --- PRNT ---

    def _handle_prnt(self, data: bytes) -> None:
        offset = 0
        _fmt, offset = read_u8(data, offset)
        link_count, offset = read_u32(data, offset)

        child_ids, offset = decode_ids(data, offset, link_count)
        parent_ids, offset = decode_ids(data, offset, link_count)

        for child_id, parent_id in zip(child_ids, parent_ids, strict=True):
            child = self._instances.get(child_id)
            parent = self._instances.get(parent_id)
            if child is not None and parent is not None:
                parent.children.append(child)

        log.debug('PRNT: %d links', link_count)

    # --- Tree building ---

    def _build_tree(self) -> list[RbxInstance]:
        """Identify root instances (those not parented to another)."""
        parented: set[int] = set()
        for inst in self._instances.values():
            for child in inst.children:
                parented.add(child.referent)

        return [
            inst for inst in self._instances.values() if inst.referent not in parented
        ]


def _quat_to_matrix(x: float, y: float, z: float, w: float) -> tuple[float, ...]:
    """Convert a quaternion to a 3x3 rotation matrix (row-major)."""
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z

    return (
        1 - 2 * (yy + zz),
        2 * (xy - wz),
        2 * (xz + wy),
        2 * (xy + wz),
        1 - 2 * (xx + zz),
        2 * (yz - wx),
        2 * (xz - wy),
        2 * (yz + wx),
        1 - 2 * (xx + yy),
    )
