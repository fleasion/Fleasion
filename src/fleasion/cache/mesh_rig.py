"""Parse embedded Roblox FileMesh skeletons and export them as glTF GLB files."""

from __future__ import annotations

import gzip
import json
import struct
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, SupportsInt, TypeVar, cast, runtime_checkable

import numpy as np
from numpy.typing import ArrayLike, NDArray

from . import mesh_processing

type _BoneRecord = tuple[
    int,
    int,
    int,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
    float,
]
type _SkinningData = tuple[list[_Envelope], list[Bone], list[_Subset]]
type _JsonValue = str | int | float | bool | Sequence[_JsonValue] | Mapping[str, _JsonValue] | None
type _NumericArray = NDArray[np.float32] | NDArray[np.uint8]
_Scalar = TypeVar('_Scalar', np.float32, np.uint8)


class _FaceLike(Protocol):
    a: int
    b: int
    c: int


class _DracoDecoder(Protocol):
    def decode(self, data: bytes) -> object: ...


@runtime_checkable
class _HasPoints(Protocol):
    points: ArrayLike


@runtime_checkable
class _HasNormals(Protocol):
    normals: ArrayLike | None


@runtime_checkable
class _HasTexCoord(Protocol):
    tex_coord: ArrayLike | None


@runtime_checkable
class _HasFaces(Protocol):
    faces: list[list[SupportsInt]]


@runtime_checkable
class _HasAttributeLookup(Protocol):
    def get_attribute_by_unique_id(self, unique_id: int) -> object: ...


@runtime_checkable
class _AttributeData(Protocol):
    def __contains__(self, key: object) -> bool: ...
    def __getitem__(self, key: str) -> object: ...


class MeshRigError(ValueError):
    """Raised when an embedded FileMesh rig is malformed or unsupported."""


@dataclass(frozen=True)
class Bone:
    name: str
    parent: int | None
    lod_parent: int | None
    culling_distance: float
    world_matrix: tuple[float, ...]


@dataclass(frozen=True)
class RigVertex:
    position: tuple[float, float, float]
    normal: tuple[float, float, float]
    texcoord: tuple[float, float]
    color: tuple[int, int, int, int]
    joints: tuple[int, int, int, int]
    weights: tuple[float, float, float, float]


@dataclass(frozen=True)
class RiggedMesh:
    version: str
    vertices: tuple[RigVertex, ...]
    faces: tuple[tuple[int, int, int], ...]
    bones: tuple[Bone, ...]
    has_facs: bool


@dataclass(frozen=True)
class _Envelope:
    subset_indices: tuple[int, int, int, int]
    weights: tuple[int, int, int, int]


@dataclass(frozen=True)
class _Subset:
    vertices_begin: int
    vertices_length: int
    bone_indices: tuple[int, ...]


def _decompress(data: bytes) -> bytes:
    if data.startswith(b'\x1f\x8b'):
        try:
            return gzip.decompress(data)
        except Exception as exc:
            raise MeshRigError(f'invalid gzip wrapper: {exc}') from exc
    return data


def _require(data: bytes, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise MeshRigError(f'{label} exceeds payload size')


def _read_envelopes(data: bytes, offset: int, count: int) -> tuple[list[_Envelope], int]:
    _require(data, offset, count * 8, 'skinning data')
    envelopes: list[_Envelope] = []
    for _ in range(count):
        values = struct.unpack_from('<8B', data, offset)
        envelopes.append(_Envelope(values[:4], values[4:]))
        offset += 8
    return envelopes, offset


def _read_bones(data: bytes, offset: int, count: int) -> tuple[list[_BoneRecord], int]:
    _require(data, offset, count * 60, 'bone table')
    records: list[_BoneRecord] = []
    for _ in range(count):
        records.append(cast('_BoneRecord', struct.unpack_from('<IHHf12f', data, offset)))
        offset += 60
    return records, offset


def _bone_name(name_table: bytes, offset: int) -> str:
    if offset >= len(name_table):
        raise MeshRigError('bone name offset exceeds name table')
    end = name_table.find(b'\0', offset)
    if end < 0:
        raise MeshRigError('bone name is not null-terminated')
    try:
        return name_table[offset:end].decode('utf-8')
    except UnicodeDecodeError as exc:
        raise MeshRigError('bone name is not valid UTF-8') from exc


def _finish_bones(records: list[_BoneRecord], name_table: bytes) -> list[Bone]:
    bones: list[Bone] = []
    for record in records:
        name_offset, parent_raw, lod_parent_raw, culling_distance, *transform = record
        parent = None if parent_raw == 0xFFFF else parent_raw
        lod_parent = None if lod_parent_raw == 0xFFFF else lod_parent_raw
        matrix = (
            transform[0],
            transform[1],
            transform[2],
            transform[9],
            transform[3],
            transform[4],
            transform[5],
            transform[10],
            transform[6],
            transform[7],
            transform[8],
            transform[11],
            0.0,
            0.0,
            0.0,
            1.0,
        )
        bones.append(
            Bone(
                name=_bone_name(name_table, name_offset),
                parent=parent,
                lod_parent=lod_parent,
                culling_distance=culling_distance,
                world_matrix=matrix,
            )
        )
    _validate_bone_hierarchy(bones)
    return bones


def _validate_bone_hierarchy(bones: list[Bone]) -> None:
    for bone in bones:
        if bone.parent is not None and bone.parent >= len(bones):
            raise MeshRigError('bone parent index exceeds bone table')
        if bone.lod_parent is not None and bone.lod_parent >= len(bones):
            raise MeshRigError('bone LOD parent index exceeds bone table')

    states = [0] * len(bones)

    def visit(index: int) -> None:
        if states[index] == 1:
            raise MeshRigError('bone hierarchy contains a cycle')
        if states[index] == 2:
            return
        states[index] = 1
        parent = bones[index].parent
        if parent is not None:
            visit(parent)
        states[index] = 2

    for index in range(len(bones)):
        visit(index)


def _read_subsets(
    data: bytes, offset: int, count: int, bone_count: int
) -> tuple[list[_Subset], int]:
    _require(data, offset, count * 72, 'subset table')
    subsets: list[_Subset] = []
    for _ in range(count):
        _faces_begin, _faces_length, vertices_begin, vertices_length, mapped_count = (
            struct.unpack_from('<5I', data, offset)
        )
        if mapped_count > 26:
            raise MeshRigError('subset contains more than 26 mapped bones')
        mapped = struct.unpack_from('<26H', data, offset + 20)[:mapped_count]
        if any(index >= bone_count for index in mapped):
            raise MeshRigError('subset references an invalid bone')
        subsets.append(_Subset(vertices_begin, vertices_length, tuple(mapped)))
        offset += 72
    return subsets, offset


def _read_faces(
    data: bytes, offset: int, count: int, vertex_count: int
) -> tuple[list[mesh_processing.Face], int]:
    _require(data, offset, count * 12, 'face table')
    faces: list[mesh_processing.Face] = []
    for _ in range(count):
        face = struct.unpack_from('<III', data, offset)
        if any(index >= vertex_count for index in face):
            raise MeshRigError('face references an invalid vertex')
        faces.append(mesh_processing.Face(*(index + 1 for index in face)))
        offset += 12
    return faces, offset


def _map_weights(
    vertex_count: int,
    envelopes: list[_Envelope],
    subsets: list[_Subset],
) -> list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]]:
    if len(envelopes) != vertex_count:
        raise MeshRigError('skinning count does not match vertex count')

    vertex_subsets: list[_Subset | None] = [None] * vertex_count
    for subset in subsets:
        end = subset.vertices_begin + subset.vertices_length
        if end > vertex_count:
            raise MeshRigError('subset vertex range exceeds vertex table')
        for vertex_index in range(subset.vertices_begin, end):
            if vertex_subsets[vertex_index] is not None:
                raise MeshRigError('subset vertex ranges overlap')
            vertex_subsets[vertex_index] = subset

    mapped: list[tuple[tuple[int, int, int, int], tuple[float, float, float, float]]] = []
    for vertex_index, envelope in enumerate(envelopes):
        combined: dict[int, int] = {}
        subset = vertex_subsets[vertex_index]
        for local_index, raw_weight in zip(envelope.subset_indices, envelope.weights, strict=False):
            if raw_weight == 0:
                continue
            if subset is None or local_index >= len(subset.bone_indices):
                raise MeshRigError('skinning references an invalid subset bone')
            bone_index = subset.bone_indices[local_index]
            combined[bone_index] = combined.get(bone_index, 0) + raw_weight

        total = sum(combined.values())
        if total <= 0:
            raise MeshRigError('skinned vertex has no positive bone weights')
        if len(combined) > 4:
            raise MeshRigError('skinned vertex contains more than four bone influences')

        joints = list(combined)
        weights = [combined[index] / total for index in joints]
        joints.extend([0] * (4 - len(joints)))
        weights.extend([0.0] * (4 - len(weights)))
        mapped.append(
            (
                (joints[0], joints[1], joints[2], joints[3]),
                (weights[0], weights[1], weights[2], weights[3]),
            )
        )
    return mapped


def _assemble(
    version: str,
    vertices: list[mesh_processing.Vertex],
    faces: list[mesh_processing.Face],
    envelopes: list[_Envelope],
    bones: list[Bone],
    subsets: list[_Subset],
    has_facs: bool,
) -> RiggedMesh:
    if not bones:
        raise MeshRigError('skinning section does not contain bones')
    mapped = _map_weights(len(vertices), envelopes, subsets)
    rig_vertices: list[RigVertex] = []
    for vertex, (joints, weights) in zip(vertices, mapped, strict=False):
        rig_vertices.append(
            RigVertex(
                position=(float(vertex.px), float(vertex.py), float(vertex.pz)),
                normal=(float(vertex.nx), float(vertex.ny), float(vertex.nz)),
                texcoord=(float(vertex.tu), float(vertex.tv)),
                color=(int(vertex.r), int(vertex.g), int(vertex.b), int(vertex.a)),
                joints=joints,
                weights=weights,
            )
        )
    rig_faces = tuple(
        (typed_face.a - 1, typed_face.b - 1, typed_face.c - 1)
        for face in faces
        for typed_face in (cast('_FaceLike', face),)
    )
    return RiggedMesh(version, tuple(rig_vertices), rig_faces, tuple(bones), has_facs)


def _parse_v4_v5(data: bytes, version: str) -> RiggedMesh | None:
    header_size = struct.unpack_from('<H', data, 13)[0]
    expected_header_size = 32 if version == 'version 5.00' else 24
    if header_size != expected_header_size:
        raise MeshRigError(f'unexpected {version} header size {header_size}')
    _require(data, 13, header_size, 'mesh header')

    vertex_count = struct.unpack_from('<I', data, 17)[0]
    face_count = struct.unpack_from('<I', data, 21)[0]
    lod_count = struct.unpack_from('<H', data, 25)[0]
    bone_count = struct.unpack_from('<H', data, 27)[0]
    name_table_size = struct.unpack_from('<I', data, 29)[0]
    subset_count = struct.unpack_from('<H', data, 33)[0]
    if bone_count == 0:
        return None

    offset = 13 + header_size
    _require(data, offset, vertex_count * 40, 'vertex table')
    vertices, offset = mesh_processing.read_vertices(data, offset, vertex_count, 40)
    envelopes, offset = _read_envelopes(data, offset, vertex_count)
    faces, offset = _read_faces(data, offset, face_count, vertex_count)

    _require(data, offset, lod_count * 4, 'LOD table')
    lods = list(struct.unpack_from(f'<{lod_count}I', data, offset)) if lod_count else []
    offset += lod_count * 4
    if len(lods) >= 2:
        if lods[0] > lods[1] or lods[1] > len(faces):
            raise MeshRigError('main LOD face range is invalid')
        if lods[1] > lods[0]:
            faces = faces[lods[0] : lods[1]]

    bone_records, offset = _read_bones(data, offset, bone_count)
    _require(data, offset, name_table_size, 'bone name table')
    name_table = data[offset : offset + name_table_size]
    offset += name_table_size
    bones = _finish_bones(bone_records, name_table)
    subsets, offset = _read_subsets(data, offset, subset_count, bone_count)

    has_facs = False
    if version == 'version 5.00':
        facs_format = struct.unpack_from('<I', data, 37)[0]
        facs_size = struct.unpack_from('<I', data, 41)[0]
        _require(data, offset, facs_size, 'FACS data')
        has_facs = facs_format != 0 and facs_size > 0
        offset += facs_size
    if offset != len(data):
        raise MeshRigError('unexpected trailing mesh data')

    return _assemble(version, vertices, faces, envelopes, bones, subsets, has_facs)


def _parse_skinning_chunk(data: bytes) -> _SkinningData | None:
    _require(data, 0, 4, 'SKINNING header')
    envelope_count = struct.unpack_from('<I', data, 0)[0]
    envelopes, offset = _read_envelopes(data, 4, envelope_count)
    _require(data, offset, 4, 'SKINNING bone count')
    bone_count = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    if bone_count == 0:
        return None

    bone_records, offset = _read_bones(data, offset, bone_count)
    _require(data, offset, 4, 'SKINNING name table size')
    name_table_size = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    _require(data, offset, name_table_size, 'SKINNING name table')
    name_table = data[offset : offset + name_table_size]
    offset += name_table_size
    bones = _finish_bones(bone_records, name_table)

    _require(data, offset, 4, 'SKINNING subset count')
    subset_count = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    subsets, offset = _read_subsets(data, offset, subset_count, bone_count)
    if offset != len(data):
        raise MeshRigError('unexpected trailing SKINNING data')
    return envelopes, bones, subsets


def _decode_draco_vertices(
    data: bytes,
) -> tuple[list[mesh_processing.Vertex], list[mesh_processing.Face]]:
    if len(data) < 4:
        raise MeshRigError('COREMESH v2 payload is too small')
    stream_size = struct.unpack_from('<I', data, 0)[0]
    if stream_size != len(data) - 4:
        raise MeshRigError('COREMESH v2 Draco size does not match chunk size')
    if not mesh_processing.DRACO_AVAILABLE:
        raise MeshRigError('DracoPy is required to export a v7 rig')

    decoder = cast('_DracoDecoder', mesh_processing.DracoPy)
    try:
        mesh = decoder.decode(data[4:])
    except Exception as exc:
        raise MeshRigError(f'Draco decoding failed: {exc}') from exc
    if mesh is None or not isinstance(mesh, _HasPoints):
        raise MeshRigError('Draco decoding returned invalid mesh data')

    positions: NDArray[np.float32] = np.asarray(mesh.points, dtype=np.float32)
    if positions.ndim != 2 or positions.shape[1] != 3 or len(positions) == 0:
        raise MeshRigError('Draco positions have an invalid shape')
    vertices = [mesh_processing.Vertex() for _ in positions]
    for vertex, position in zip(vertices, positions, strict=False):
        vertex.px, vertex.py, vertex.pz = position

    def attribute(unique_id: int, width: int, dtype: type[_Scalar]) -> NDArray[_Scalar] | None:
        if not isinstance(mesh, _HasAttributeLookup):
            return None
        try:
            result = mesh.get_attribute_by_unique_id(unique_id)
            if result is None or not isinstance(result, _AttributeData) or 'data' not in result:
                return None
            values: NDArray[_Scalar] = np.asarray(
                cast('ArrayLike', result['data']),
                dtype=dtype,
            )
            return values.reshape(-1, width) if values.ndim == 1 else values
        except Exception:
            return None

    normals: NDArray[np.float32] | None = attribute(1, 3, np.float32)
    if normals is None and isinstance(mesh, _HasNormals) and mesh.normals is not None:
        normals = np.asarray(mesh.normals, dtype=np.float32).reshape(-1, 3)
    texcoords: NDArray[np.float32] | None = attribute(2, 2, np.float32)
    if texcoords is None and isinstance(mesh, _HasTexCoord) and mesh.tex_coord is not None:
        texcoords = np.asarray(mesh.tex_coord, dtype=np.float32).reshape(-1, 2)
    colors: NDArray[np.uint8] | None = attribute(4, 4, np.uint8)

    values_and_labels: tuple[tuple[_NumericArray | None, str], ...] = (
        (normals, 'normal'),
        (texcoords, 'UV'),
        (colors, 'color'),
    )
    for values, label in values_and_labels:
        if values is not None and len(values) != len(vertices):
            raise MeshRigError(f'Draco {label} count does not match vertex count')
    for index, vertex in enumerate(vertices):
        if normals is not None:
            vertex.nx, vertex.ny, vertex.nz = normals[index]
        if texcoords is not None:
            vertex.tu = texcoords[index][0]
            vertex.tv = 1.0 - texcoords[index][1]
        if colors is not None:
            vertex.r, vertex.g, vertex.b, vertex.a = colors[index]

    faces: list[mesh_processing.Face] = []
    face_rows = mesh.faces if isinstance(mesh, _HasFaces) else []
    for triangle in face_rows:
        a, b, c = map(int, triangle)
        if min(a, b, c) < 0 or max(a, b, c) >= len(vertices):
            raise MeshRigError('Draco face references an invalid vertex')
        faces.append(mesh_processing.Face(a + 1, b + 1, c + 1))
    return vertices, faces


def _read_chunked_mesh(data: bytes) -> list[tuple[str, int, bytes]]:
    """Read v6/v7 chunks with the same framing used by mesh_processing."""
    chunks: list[tuple[str, int, bytes]] = []
    offset = 13
    while offset < len(data):
        if len(data) - offset < 16:
            raise ValueError('truncated chunk header')
        chunk_type = data[offset : offset + 8].decode('ascii', errors='replace').rstrip('\0')
        chunk_version, chunk_size = struct.unpack_from('<II', data, offset + 8)
        offset += 16
        chunk_end = offset + chunk_size
        if chunk_end > len(data):
            raise ValueError(f'{chunk_type} chunk exceeds file size')
        chunks.append((chunk_type, chunk_version, data[offset:chunk_end]))
        offset = chunk_end
    return chunks


def _read_raw_coremesh(
    data: bytes,
) -> tuple[list[mesh_processing.Vertex], list[mesh_processing.Face]]:
    """Read the uncompressed COREMESH v1 payload used by FileMesh v6."""
    if len(data) < 8:
        raise ValueError('COREMESH v1 payload is too small')
    num_verts = struct.unpack_from('<I', data, 0)[0]
    vertex_end = 4 + num_verts * 40
    if vertex_end + 4 > len(data):
        raise ValueError('COREMESH v1 vertex data exceeds chunk size')
    verts, offset = mesh_processing.read_vertices(data, 4, num_verts, 40)
    num_faces = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    face_end = offset + num_faces * 12
    if face_end != len(data):
        raise ValueError('COREMESH v1 face data does not match chunk size')
    faces: list[mesh_processing.Face] = []
    for _ in range(num_faces):
        a, b, c = struct.unpack_from('<III', data, offset)
        if a >= num_verts or b >= num_verts or c >= num_verts:
            raise ValueError('COREMESH v1 face references an invalid vertex')
        faces.append(mesh_processing.Face(a + 1, b + 1, c + 1))
        offset += 12
    return verts, faces


def _apply_chunked_lod(
    faces: list[mesh_processing.Face], lod_data: bytes | None
) -> list[mesh_processing.Face]:
    """Select the highest-quality face range from a v6/v7 LODS v1 payload."""
    if not lod_data:
        return faces
    if len(lod_data) < 7:
        raise ValueError('LODS payload is too small')
    num_offsets = struct.unpack_from('<I', lod_data, 3)[0]
    offsets_end = 7 + num_offsets * 4
    if offsets_end > len(lod_data):
        raise ValueError('LODS offsets exceed chunk size')
    if num_offsets < 2:
        return faces
    first, second = struct.unpack_from('<II', lod_data, 7)
    if first > second or second > len(faces):
        raise ValueError('LODS face range is invalid')
    if first == 0 and 0 < second < len(faces):
        mesh_processing.log_buffer.log(
            'Mesh',
            f'Applying high-quality LOD: {len(faces):,} → {second:,} faces',
        )
        return faces[:second]
    return faces[first:second] if second > first else faces


def _parse_v6_v7(data: bytes, version: str) -> RiggedMesh | None:
    chunks = _read_chunked_mesh(data)
    coremesh = None
    lod_data = None
    skinning = None
    has_facs = False
    for chunk_type, chunk_version, chunk_data in chunks:
        if chunk_type == 'COREMESH':
            coremesh = (chunk_version, chunk_data)
        elif chunk_type == 'LODS' and chunk_version == 1:
            lod_data = chunk_data
        elif chunk_type == 'SKINNING':
            if chunk_version != 1:
                raise MeshRigError(f'unsupported SKINNING chunk version {chunk_version}')
            skinning = _parse_skinning_chunk(chunk_data)
        elif chunk_type == 'FACS' and chunk_version == 1:
            _require(chunk_data, 0, 4, 'FACS chunk')
            facs_size = struct.unpack_from('<I', chunk_data, 0)[0]
            if facs_size != len(chunk_data) - 4:
                raise MeshRigError('FACS size does not match chunk size')
            has_facs = facs_size > 0

    if skinning is None:
        return None
    if coremesh is None:
        raise MeshRigError('rigged mesh does not contain COREMESH geometry')

    coremesh_version, coremesh_data = coremesh
    if version == 'version 6.00' and coremesh_version == 1:
        vertices, faces = _read_raw_coremesh(coremesh_data)
    elif version == 'version 7.00' and coremesh_version == 2:
        vertices, faces = _decode_draco_vertices(coremesh_data)
    else:
        raise MeshRigError(f'unsupported COREMESH v{coremesh_version} for {version}')
    faces = _apply_chunked_lod(faces, lod_data)

    envelopes, bones, subsets = skinning
    return _assemble(version, vertices, faces, envelopes, bones, subsets, has_facs)


def parse_rigged_mesh(data: bytes) -> RiggedMesh | None:
    """Return an embedded FileMesh rig, or ``None`` for a valid static/older mesh."""
    data = _decompress(data)
    if len(data) < 13:
        return None
    version = data[:12].decode('ascii', errors='ignore')
    try:
        if version in {'version 4.00', 'version 4.01', 'version 5.00'}:
            return _parse_v4_v5(data, version)
        if version in {'version 6.00', 'version 7.00'}:
            return _parse_v6_v7(data, version)
        return None
    except (IndexError, struct.error, ValueError) as exc:
        if isinstance(exc, MeshRigError):
            raise
        raise MeshRigError(str(exc)) from exc


def has_embedded_rig(data: bytes) -> bool:
    """Return whether a payload contains valid embedded bones and vertex weights."""
    try:
        data = _decompress(data)
        if len(data) < 29:
            return False
        version = data[:12].decode('ascii', errors='ignore')
        if version in {'version 4.00', 'version 4.01', 'version 5.00'}:
            return struct.unpack_from('<H', data, 27)[0] > 0
        if version not in {'version 6.00', 'version 7.00'}:
            return False
        if version == 'version 7.00' and not mesh_processing.DRACO_AVAILABLE:
            return False
        expected_coremesh_version = 1 if version == 'version 6.00' else 2
        has_coremesh = False
        valid_skinning = False
        for chunk_type, chunk_version, chunk_data in _read_chunked_mesh(data):
            if chunk_type == 'COREMESH':
                has_coremesh = chunk_version == expected_coremesh_version
            elif chunk_type == 'SKINNING':
                if chunk_version != 1:
                    return False
                skinning = _parse_skinning_chunk(chunk_data)
                if skinning is None:
                    return False
                envelopes, _bones, subsets = skinning
                _map_weights(len(envelopes), envelopes, subsets)
                valid_skinning = True
        return has_coremesh and valid_skinning
    except MeshRigError, ValueError, struct.error:
        return False


def _pad4(buffer: bytearray, value: int = 0) -> None:
    buffer.extend(bytes([value]) * (-len(buffer) % 4))


def export_glb(data: bytes) -> bytes:
    """Export a skinned FileMesh as a single-file glTF 2.0 binary asset."""
    rig = parse_rigged_mesh(data)
    if rig is None:
        raise MeshRigError('mesh does not contain an embedded rig')
    if len(rig.bones) > 0xFFFF:
        raise MeshRigError('GLB export supports at most 65535 bones')

    positions = np.asarray([vertex.position for vertex in rig.vertices], dtype='<f4')
    normals = np.asarray([vertex.normal for vertex in rig.vertices], dtype='<f4')
    texcoords = np.asarray([vertex.texcoord for vertex in rig.vertices], dtype='<f4')
    colors = np.asarray([vertex.color for vertex in rig.vertices], dtype=np.uint8)
    joints = np.asarray([vertex.joints for vertex in rig.vertices], dtype='<u2')
    weights = np.asarray([vertex.weights for vertex in rig.vertices], dtype='<f4')
    index_dtype = '<u2' if len(rig.vertices) <= 0xFFFF else '<u4'
    indices = np.asarray(rig.faces, dtype=index_dtype).reshape(-1)

    world_matrices: list[NDArray[np.float64]] = [
        np.asarray(bone.world_matrix, dtype=np.float64).reshape(4, 4) for bone in rig.bones
    ]
    local_matrices: list[NDArray[np.float64]] = []
    inverse_bind_matrices: list[NDArray[np.float64]] = []
    for index, bone in enumerate(rig.bones):
        world = world_matrices[index]
        try:
            inverse_bind_matrices.append(np.linalg.inv(world))
            local = (
                world if bone.parent is None else np.linalg.inv(world_matrices[bone.parent]) @ world
            )
        except np.linalg.LinAlgError as exc:
            raise MeshRigError(f'bone {bone.name!r} has a singular bind matrix') from exc
        local_matrices.append(local)

    binary = bytearray()
    buffer_views: list[dict[str, _JsonValue]] = []
    accessors: list[dict[str, _JsonValue]] = []

    def add_accessor(
        array: NDArray[np.generic],
        component_type: int,
        accessor_type: str,
        *,
        target: int | None = None,
        normalized: bool = False,
        bounds: bool = False,
    ) -> int:
        _pad4(binary)
        array = np.ascontiguousarray(array)
        byte_offset = len(binary)
        payload = array.tobytes()
        binary.extend(payload)
        view: dict[str, _JsonValue] = {
            'buffer': 0,
            'byteOffset': byte_offset,
            'byteLength': len(payload),
        }
        if target is not None:
            view['target'] = target
        view_index = len(buffer_views)
        buffer_views.append(view)
        accessor: dict[str, _JsonValue] = {
            'bufferView': view_index,
            'componentType': component_type,
            'count': int(array.shape[0]),
            'type': accessor_type,
        }
        if normalized:
            accessor['normalized'] = True
        if bounds:
            # Only the POSITION float32 accessor requests bounds.
            bounded_array = cast('NDArray[np.float32]', array)
            minimums: NDArray[np.float32] = bounded_array.min(axis=0)
            maximums: NDArray[np.float32] = bounded_array.max(axis=0)
            accessor['min'] = [float(value) for value in minimums]
            accessor['max'] = [float(value) for value in maximums]
        accessors.append(accessor)
        return len(accessors) - 1

    position_accessor = add_accessor(positions, 5126, 'VEC3', target=34962, bounds=True)
    normal_accessor = add_accessor(normals, 5126, 'VEC3', target=34962)
    texcoord_accessor = add_accessor(texcoords, 5126, 'VEC2', target=34962)
    color_accessor = add_accessor(colors, 5121, 'VEC4', target=34962, normalized=True)
    joints_accessor = add_accessor(joints, 5123, 'VEC4', target=34962)
    weights_accessor = add_accessor(weights, 5126, 'VEC4', target=34962)
    index_component = 5123 if indices.dtype.itemsize == 2 else 5125
    index_accessor = add_accessor(indices, index_component, 'SCALAR', target=34963)
    inverse_bind_array = np.asarray([matrix.T for matrix in inverse_bind_matrices], dtype='<f4')
    inverse_bind_accessor = add_accessor(inverse_bind_array, 5126, 'MAT4')
    _pad4(binary)

    nodes: list[dict[str, _JsonValue]] = []
    for index, bone in enumerate(rig.bones):
        node: dict[str, _JsonValue] = {
            'name': bone.name,
            'matrix': [float(value) for value in local_matrices[index].flatten(order='F')],
        }
        children = [
            child_index for child_index, child in enumerate(rig.bones) if child.parent == index
        ]
        if children:
            node['children'] = children
        nodes.append(node)
    roots = [index for index, bone in enumerate(rig.bones) if bone.parent is None]
    mesh_node = len(nodes)
    mesh_node_data: dict[str, _JsonValue] = {'name': 'RiggedMesh', 'mesh': 0, 'skin': 0}
    if roots:
        mesh_node_data['children'] = roots
    nodes.append(mesh_node_data)

    document: dict[str, _JsonValue] = {
        'asset': {'version': '2.0', 'generator': 'Fleasion'},
        'scene': 0,
        'scenes': [{'nodes': [mesh_node]}],
        'nodes': nodes,
        'meshes': [
            {
                'name': 'RobloxMesh',
                'primitives': [
                    {
                        'attributes': {
                            'POSITION': position_accessor,
                            'NORMAL': normal_accessor,
                            'TEXCOORD_0': texcoord_accessor,
                            'COLOR_0': color_accessor,
                            'JOINTS_0': joints_accessor,
                            'WEIGHTS_0': weights_accessor,
                        },
                        'indices': index_accessor,
                        'mode': 4,
                    }
                ],
            }
        ],
        'skins': [
            {
                'name': 'RobloxRig',
                'joints': list(range(len(rig.bones))),
                'inverseBindMatrices': inverse_bind_accessor,
                **({'skeleton': roots[0]} if roots else {}),
            }
        ],
        'buffers': [{'byteLength': len(binary)}],
        'bufferViews': buffer_views,
        'accessors': accessors,
        'extras': {'robloxFileMeshVersion': rig.version, 'hasFacsData': rig.has_facs},
    }

    json_data = bytearray(
        json.dumps(document, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    )
    _pad4(json_data, 0x20)
    total_length = 12 + 8 + len(json_data) + 8 + len(binary)
    return (
        struct.pack('<4sII', b'glTF', 2, total_length)
        + struct.pack('<I4s', len(json_data), b'JSON')
        + json_data
        + struct.pack('<I4s', len(binary), b'BIN\0')
        + binary
    )
