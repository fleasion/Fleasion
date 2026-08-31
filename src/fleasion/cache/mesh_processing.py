# mesh_processing.py
# Complete Roblox mesh converter supporting versions 1.x through 7.00
# Handles all mesh formats including Draco-compressed v6/v7 meshes
import gzip
import importlib
import json
import struct
import zlib
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, SupportsInt, TypeIs, cast

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable

    from numpy.typing import NDArray

from fleasion.utils import log_buffer


class _DracoPoints(Protocol):
    points: object


class _DracoNormals(_DracoPoints, Protocol):
    normals: object | None


class _DracoTexCoords(_DracoPoints, Protocol):
    tex_coord: object | None


class _DracoFaces(_DracoPoints, Protocol):
    faces: Iterable[Iterable[SupportsInt]] | None


class _DracoAttributes(_DracoPoints, Protocol):
    def get_attribute_by_unique_id(self, unique_id: int) -> dict[str, object] | None: ...


class _DracoModule(Protocol):
    FileTypeException: type[Exception]

    def decode(self, buffer: bytes) -> object: ...


def _has_points(value: object) -> TypeIs[_DracoPoints]:
    return hasattr(value, 'points')


def _has_normals(value: _DracoPoints) -> TypeIs[_DracoNormals]:
    return hasattr(value, 'normals')


def _has_tex_coords(value: _DracoPoints) -> TypeIs[_DracoTexCoords]:
    return hasattr(value, 'tex_coord')


def _has_faces(value: _DracoPoints) -> TypeIs[_DracoFaces]:
    return hasattr(value, 'faces')


def _has_attributes(value: _DracoPoints) -> TypeIs[_DracoAttributes]:
    return hasattr(value, 'get_attribute_by_unique_id')


def _invalid_draco_face(indices: tuple[int, int, int], vertex_count: int) -> bool:
    return any(index < 0 or index >= vertex_count for index in indices)


try:
    DracoPy: _DracoModule | None = cast('_DracoModule', importlib.import_module('DracoPy'))
except ImportError:
    DracoPy = None
    log_buffer.log('Mesh', 'DracoPy not installed. v6/v7 mesh conversion will not work.')

DRACO_AVAILABLE = DracoPy is not None


# Shared Data Structures
class Vertex:
    """Represents a single vertex with all attributes"""

    def __init__(self) -> None:
        # Position
        self.px: float | np.float32 = 0.0
        self.py: float | np.float32 = 0.0
        self.pz: float | np.float32 = 0.0
        # Normal
        self.nx: float | np.float32 = 0.0
        self.ny: float | np.float32 = 0.0
        self.nz: float | np.float32 = 0.0
        # UV coordinates
        self.tu: float | np.float32 = 0.0
        self.tv: float | np.float32 = 0.0
        self.tw: float | np.float32 = 0.0
        # Tangent (signed byte)
        self.tx = self.ty = self.tz = self.ts = 0
        # Color (RGBA)
        self.r: int | np.uint8 = 255
        self.g: int | np.uint8 = 255
        self.b: int | np.uint8 = 255
        self.a: int | np.uint8 = 255


class Face:
    """Represents a triangular face (OBJ uses 1-based indexing)"""

    def __init__(self, a: int = 0, b: int = 0, c: int = 0) -> None:
        self.a, self.b, self.c = a, b, c


# Utility Functions
def fix_float(s: str) -> str:
    """Convert comma decimals to period decimals for OBJ format"""
    return s.replace(',', '.')


def read_vertices(data: bytes, offset: int, count: int, vsize: int) -> tuple[list[Vertex], int]:
    """
    Read vertex data from binary mesh formats (v2-v5)
    Args:
        data: Binary mesh data
        offset: Starting position in data
        count: Number of vertices to read
        vsize: Size of each vertex (36 or 40 bytes)
    Returns:
        Tuple of (vertex list, new offset)
    """
    verts: list[Vertex] = []
    pos = offset
    for _ in range(count):
        v = Vertex()
        # Position (3 floats)
        (v.px,) = struct.unpack_from('<f', data, pos)
        pos += 4
        (v.py,) = struct.unpack_from('<f', data, pos)
        pos += 4
        (v.pz,) = struct.unpack_from('<f', data, pos)
        pos += 4
        # Normal (3 floats)
        (v.nx,) = struct.unpack_from('<f', data, pos)
        pos += 4
        (v.ny,) = struct.unpack_from('<f', data, pos)
        pos += 4
        (v.nz,) = struct.unpack_from('<f', data, pos)
        pos += 4
        # UV coordinates (2 floats)
        (v.tu,) = struct.unpack_from('<f', data, pos)
        pos += 4
        (tv,) = struct.unpack_from('<f', data, pos)
        pos += 4
        v.tv = 1.0 - tv  # Flip V coordinate for Roblox
        # Tangent (4 signed bytes)
        (v.tx,) = struct.unpack_from('<b', data, pos)
        pos += 1
        (v.ty,) = struct.unpack_from('<b', data, pos)
        pos += 1
        (v.tz,) = struct.unpack_from('<b', data, pos)
        pos += 1
        (v.ts,) = struct.unpack_from('<b', data, pos)
        pos += 1
        # Color (4 unsigned bytes, only in 40-byte format)
        if vsize == 40:
            (v.r,) = struct.unpack_from('<B', data, pos)
            pos += 1
            (v.g,) = struct.unpack_from('<B', data, pos)
            pos += 1
            (v.b,) = struct.unpack_from('<B', data, pos)
            pos += 1
            (v.a,) = struct.unpack_from('<B', data, pos)
            pos += 1
        verts.append(v)
    return verts, pos


def write_obj_data(
    v_lines: list[str], n_lines: list[str], t_lines: list[str], f_lines: list[str]
) -> str:
    """
    Generate OBJ file content from vertex/normal/texture/face data
    Args:
        v_lines: Vertex position lines
        n_lines: Vertex normal lines
        t_lines: Texture coordinate lines
        f_lines: Face lines
    Returns:
        Complete OBJ file content as string
    """
    lines = ['# Converted from Roblox mesh format\n']
    lines.append(f'# Vertices: {len(v_lines)}, Faces: {len(f_lines)}\n\n')
    lines.extend(line + '\n' for line in v_lines)
    lines.append('\n')
    lines.extend(line + '\n' for line in n_lines)
    lines.append('\n')
    lines.extend(line + '\n' for line in t_lines)
    lines.append('\n')
    lines.extend(line + '\n' for line in f_lines)
    return ''.join(lines)


# Version-Specific Processors
def _process_v1_unchecked(data: bytes) -> str | None:
    lines = data.decode('utf-8', errors='replace').splitlines()
    if len(lines) < 3:
        log_buffer.log('Mesh', 'Invalid v1 mesh: not enough lines')
        return None
    version = lines[0].strip()
    try:
        face_count = int(lines[1].strip())
    except ValueError as e:
        log_buffer.log('Mesh', f'Invalid v1 face count: {e}')
        return None
    # Parse JSON vertex data (on line 3)
    try:
        # Convert ][ to ],[ for valid JSON array
        content = json.loads('[' + lines[2].replace('][', '],[') + ']')
    except json.JSONDecodeError as e:
        log_buffer.log('Mesh', f'Failed to parse v1 JSON: {e}')
        return None
    # Each vertex group has 3 elements: position, normal, uv
    groups = len(content) // 3
    if groups != face_count * 3:
        log_buffer.log('Mesh', f'Invalid v1 mesh: {groups} vertices for {face_count} faces')
        return None
    position_scale = 0.5 if version == 'version 1.00' else 1.0
    verts: list[str] = []
    norms: list[str] = []
    uvs: list[str] = []
    faces: list[str] = []
    for i in range(groups):
        v = content[i * 3]  # Position [x, y, z]
        n = content[i * 3 + 1]  # Normal [x, y, z]
        uv = content[i * 3 + 2]  # UV [u, v, w]
        px = v[0] * position_scale
        py = v[1] * position_scale
        pz = v[2] * position_scale
        verts.append(f'v {fix_float(str(px))} {fix_float(str(py))} {fix_float(str(pz))}')
        norms.append(f'vn {fix_float(str(n[0]))} {fix_float(str(n[1]))} {fix_float(str(n[2]))}')
        uvs.append(
            f'vt {fix_float(str(uv[0]))} {fix_float(str(1 - uv[1]))} {fix_float(str(uv[2]))}'
        )
    # Create faces (every 3 vertices form a triangle)
    for i in range(0, groups, 3):
        idx = i + 1  # OBJ uses 1-based indexing
        faces.append(
            f'f {idx}/{idx}/{idx} {idx + 1}/{idx + 1}/{idx + 1} {idx + 2}/{idx + 2}/{idx + 2}'
        )
    return write_obj_data(verts, norms, uvs, faces)


def process_v1(data: bytes) -> str | None:
    """
    Process version 1.x mesh format (JSON-based)
    Args:
        data: Complete mesh file data
    Returns:
        OBJ file content as string, or None on failure
    """
    try:
        return _process_v1_unchecked(data)
    except (IndexError, TypeError, ValueError) as e:
        log_buffer.log('Mesh', f'Error processing v1 mesh: {e}')
        return None


def _apply_legacy_lod(
    data: bytes,
    offset: int,
    num_lod_offsets: int,
    faces: list[Face],
) -> list[Face]:
    try:
        lod_offsets = [
            struct.unpack_from('<I', data, offset + (index * 4))[0]
            for index in range(num_lod_offsets)
        ]
    except (IndexError, struct.error, TypeError, ValueError) as exc:
        log_buffer.log('Mesh', f'LOD parsing failed: {exc}')
        return faces
    if len(lod_offsets) < 2 or lod_offsets[1] <= 0 or lod_offsets[1] >= len(faces):
        return faces
    original_count = len(faces)
    reduced_faces = faces[: lod_offsets[1]]
    log_buffer.log(
        'Mesh',
        f'Applied LOD: {original_count} → {len(reduced_faces)} faces (offsets: {lod_offsets})',
    )
    return reduced_faces


def _process_v2_to_v5_unchecked(data: bytes, version_num: str) -> str | None:
    offset = 13  # Skip "version X.XX\n"

    # Read sizeof_header (common to all versions)
    header_size = struct.unpack_from('<H', data, offset)[0]

    # --- Parse header fields based on version ---
    sizeof_vertex = 40  # default
    num_verts = 0
    num_faces = 0
    num_lod_offsets = 0
    num_bones = 0

    if version_num == '2.00':
        # V2 header: sizeof_header(2) + sizeof_vertex(1) + sizeof_face(1)
        #          + numVerts(4) + numFaces(4) = 12 bytes
        sizeof_vertex = struct.unpack_from('<B', data, offset + 2)[0]
        # sizeof_face at offset+3 (always 12, not needed)
        num_verts = struct.unpack_from('<I', data, offset + 4)[0]
        num_faces = struct.unpack_from('<I', data, offset + 8)[0]
        num_lod_offsets = 0
        num_bones = 0

    elif version_num in {'3.00', '3.01'}:
        # V3 header: sizeof_header(2) + sizeof_vertex(1) + sizeof_face(1)
        #          + sizeof_LodOffset(2) + numLodOffsets(2)
        #          + numVerts(4) + numFaces(4) = 16 bytes
        sizeof_vertex = struct.unpack_from('<B', data, offset + 2)[0]
        # sizeof_face at offset+3
        # sizeof_LodOffset at offset+4 (always 4, skip)
        num_lod_offsets = struct.unpack_from('<H', data, offset + 6)[0]
        num_verts = struct.unpack_from('<I', data, offset + 8)[0]
        num_faces = struct.unpack_from('<I', data, offset + 12)[0]
        num_bones = 0

    elif version_num in {'4.00', '4.01'}:
        # V4 header: sizeof_header(2) + lodType(2) + numVerts(4) + numFaces(4)
        #          + numLodOffsets(2) + numBones(2) + sizeof_boneNames(4)
        #          + numSubsets(2) + numHighQualityLODs(1) + unused(1) = 24 bytes
        struct.unpack_from('<H', data, offset + 2)[0]  # lodType
        num_verts = struct.unpack_from('<I', data, offset + 4)[0]
        num_faces = struct.unpack_from('<I', data, offset + 8)[0]
        num_lod_offsets = struct.unpack_from('<H', data, offset + 12)[0]
        num_bones = struct.unpack_from('<H', data, offset + 14)[0]
        sizeof_vertex = 40  # V4 always uses 40-byte vertices

    elif version_num == '5.00':
        # V5 header: same as V4 + facsDataFormat(4) + facsDataSize(4) = 32 bytes
        struct.unpack_from('<H', data, offset + 2)[0]  # lodType
        num_verts = struct.unpack_from('<I', data, offset + 4)[0]
        num_faces = struct.unpack_from('<I', data, offset + 8)[0]
        num_lod_offsets = struct.unpack_from('<H', data, offset + 12)[0]
        num_bones = struct.unpack_from('<H', data, offset + 14)[0]
        sizeof_vertex = 40  # V5 always uses 40-byte vertices

    else:
        log_buffer.log('Mesh', f'Unsupported version in v2-v5 path: {version_num}')
        return None

    log_buffer.log(
        'Mesh',
        f'v{version_num} header: {num_verts} verts, {num_faces} faces, '
        f'vertex_size={sizeof_vertex}, bones={num_bones}, lod_offsets={num_lod_offsets}',
    )

    # Advance past the entire header
    offset = 13 + header_size

    # --- Read vertices ---
    verts, offset = read_vertices(data, offset, num_verts, sizeof_vertex)

    # --- Skip skinning data for V4/V5 when bones are present ---
    if version_num in {'4.00', '4.01', '5.00'} and num_bones > 0:
        skinning_size = num_verts * 8  # sizeof(FileMeshSkinning) = 8
        log_buffer.log(
            'Mesh',
            f'Skipping {skinning_size} bytes of skinning data ({num_verts} verts × 8 bytes)',
        )
        offset += skinning_size

    # --- Read faces ---
    faces: list[Face] = []
    for _ in range(num_faces):
        a, b, c = struct.unpack_from('<III', data, offset)
        faces.append(Face(a + 1, b + 1, c + 1))  # Convert to 1-based
        offset += 12

    # --- Read and apply LOD offsets (V3/V4/V5 only) ---
    if num_lod_offsets >= 2:
        faces = _apply_legacy_lod(data, offset, num_lod_offsets, faces)

    # --- Generate OBJ lines (Appends r, g, b to support Blender vertex colors) ---
    v_lines = [
        f'v {fix_float(f"{v.px:.6f}")} {fix_float(f"{v.py:.6f}")} {fix_float(f"{v.pz:.6f}")} '
        f'{fix_float(f"{v.r / 255.0:.6f}")} {fix_float(f"{v.g / 255.0:.6f}")} {fix_float(f"{v.b / 255.0:.6f}")}'
        for v in verts
    ]
    n_lines = [
        f'vn {fix_float(f"{v.nx:.6f}")} {fix_float(f"{v.ny:.6f}")} {fix_float(f"{v.nz:.6f}")}'
        for v in verts
    ]
    t_lines = [f'vt {fix_float(f"{v.tu:.6f}")} {fix_float(f"{v.tv:.6f}")} 0.0' for v in verts]
    f_lines = [f'f {f.a}/{f.a}/{f.a} {f.b}/{f.b}/{f.b} {f.c}/{f.c}/{f.c}' for f in faces]

    return write_obj_data(v_lines, n_lines, t_lines, f_lines)


def process_v2_to_v5(data: bytes, version_num: str) -> str | None:
    """
    Process version 2.00 through 5.00 mesh formats.

    Each version has a different header layout:
      V2: sizeof_header(u16) sizeof_vertex(u8) sizeof_face(u8) numVerts(u32) numFaces(u32)
      V3: sizeof_header(u16) sizeof_vertex(u8) sizeof_face(u8) sizeof_LodOffset(u16)
          numLodOffsets(u16) numVerts(u32) numFaces(u32)
      V4: sizeof_header(u16) lodType(u16) numVerts(u32) numFaces(u32) numLodOffsets(u16)
          numBones(u16) sizeof_boneNames(u32) numSubsets(u16) numHighQualityLODs(u8) unused(u8)
      V5: same as V4 + facsDataFormat(u32) facsDataSize(u32)

    After vertices, V4/V5 may have skinning data (8 bytes per vertex) if numBones > 0.
    After faces, V3/V4/V5 have LOD offset arrays.

    Args:
        data: Complete mesh file data
        version_num: Version string (e.g., "3.00", "4.01", "5.00")
    Returns:
        OBJ file content as string, or None on failure
    """
    try:
        return _process_v2_to_v5_unchecked(data, version_num)
    except (IndexError, struct.error, TypeError, ValueError) as e:
        log_buffer.log('Mesh', f'Error processing v{version_num} mesh: {e}')
        return None


def _read_chunked_mesh(data: bytes) -> list[tuple[str, int, bytes]]:
    """Read v6/v7 chunks, whose declared size includes their whole payload."""
    chunks: list[tuple[str, int, bytes]] = []
    offset = 13  # Skip "version X.XX\n".
    while offset < len(data):
        if len(data) - offset < 16:
            msg = 'truncated chunk header'
            raise ValueError(msg)

        chunk_type = data[offset : offset + 8].decode('ascii', errors='replace').rstrip('\0')
        chunk_version, chunk_size = struct.unpack_from('<II', data, offset + 8)
        offset += 16
        chunk_end = offset + chunk_size
        if chunk_end > len(data):
            msg = f'{chunk_type} chunk exceeds file size'
            raise ValueError(msg)

        chunks.append((chunk_type, chunk_version, data[offset:chunk_end]))
        offset = chunk_end
    return chunks


def _read_raw_coremesh(data: bytes) -> tuple[list[Vertex], list[Face]]:
    """Read the uncompressed COREMESH v1 payload used by FileMesh v6."""
    if len(data) < 8:
        msg = 'COREMESH v1 payload is too small'
        raise ValueError(msg)

    num_verts = struct.unpack_from('<I', data, 0)[0]
    vertex_end = 4 + num_verts * 40
    if vertex_end + 4 > len(data):
        msg = 'COREMESH v1 vertex data exceeds chunk size'
        raise ValueError(msg)

    verts, offset = read_vertices(data, 4, num_verts, 40)
    num_faces = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    face_end = offset + num_faces * 12
    if face_end != len(data):
        msg = 'COREMESH v1 face data does not match chunk size'
        raise ValueError(msg)

    faces: list[Face] = []
    for _ in range(num_faces):
        a, b, c = struct.unpack_from('<III', data, offset)
        if a >= num_verts or b >= num_verts or c >= num_verts:
            msg = 'COREMESH v1 face references an invalid vertex'
            raise ValueError(msg)
        faces.append(Face(a + 1, b + 1, c + 1))
        offset += 12
    return verts, faces


def _apply_chunked_lod(faces: list[Face], lod_data: bytes | None) -> list[Face]:
    """Select the highest-quality face range from a v6/v7 LODS v1 payload."""
    if not lod_data:
        return faces
    if len(lod_data) < 7:
        msg = 'LODS payload is too small'
        raise ValueError(msg)

    num_offsets = struct.unpack_from('<I', lod_data, 3)[0]
    offsets_end = 7 + num_offsets * 4
    if offsets_end > len(lod_data):
        msg = 'LODS offsets exceed chunk size'
        raise ValueError(msg)
    if num_offsets < 2:
        return faces

    first, second = struct.unpack_from('<II', lod_data, 7)
    if first > second or second > len(faces):
        msg = 'LODS face range is invalid'
        raise ValueError(msg)
    if first == 0 and 0 < second < len(faces):
        log_buffer.log(
            'Mesh',
            f'Applying high-quality LOD: {len(faces):,} → {second:,} faces',
        )
        return faces[:second]
    return faces[first:second] if second > first else faces


def _mesh_to_obj(verts: list[Vertex], faces: list[Face]) -> str:
    """Serialize parsed FileMesh geometry using Fleasion's OBJ conventions."""
    v_lines = [
        f'v {fix_float(f"{v.px:.6f}")} {fix_float(f"{v.py:.6f}")} {fix_float(f"{v.pz:.6f}")} '
        f'{fix_float(f"{v.r / 255.0:.6f}")} {fix_float(f"{v.g / 255.0:.6f}")} {fix_float(f"{v.b / 255.0:.6f}")}'
        for v in verts
    ]
    n_lines = [
        f'vn {fix_float(f"{v.nx:.6f}")} {fix_float(f"{v.ny:.6f}")} {fix_float(f"{v.nz:.6f}")}'
        for v in verts
    ]
    t_lines = [f'vt {fix_float(f"{v.tu:.6f}")} {fix_float(f"{v.tv:.6f}")} 0.0' for v in verts]
    f_lines = [f'f {f.a}/{f.a}/{f.a} {f.b}/{f.b}/{f.b} {f.c}/{f.c}/{f.c}' for f in faces]
    return write_obj_data(v_lines, n_lines, t_lines, f_lines)


def _process_v6_v7_unchecked(data: bytes) -> str | None:
    version = data[:12].decode('ascii', errors='replace').strip()
    coremesh = None
    lod_data = None
    for chunk_type, chunk_version, chunk_data in _read_chunked_mesh(data):
        if chunk_type == 'COREMESH':
            coremesh = (chunk_version, chunk_data)
        elif chunk_type == 'LODS' and chunk_version == 1:
            lod_data = chunk_data

    if coremesh is None:
        msg = 'no COREMESH chunk found'
        raise ValueError(msg)

    coremesh_version, coremesh_data = coremesh
    if coremesh_version == 1:
        if version != 'version 6.00':
            msg = f'COREMESH v1 is not valid for {version}'
            raise ValueError(msg)
        verts, faces = _read_raw_coremesh(coremesh_data)
        log_buffer.log(
            'Mesh',
            f'Raw v6 mesh decoded: {len(verts):,} vertices, {len(faces):,} faces',
        )
    elif coremesh_version == 2:
        if version != 'version 7.00':
            msg = f'COREMESH v2 is not valid for {version}'
            raise ValueError(msg)
        if len(coremesh_data) < 4:
            msg = 'COREMESH v2 payload is too small'
            raise ValueError(msg)

        draco_size = struct.unpack_from('<I', coremesh_data, 0)[0]
        if draco_size != len(coremesh_data) - 4:
            msg = 'COREMESH v2 Draco size does not match chunk size'
            raise ValueError(msg)
        if not DRACO_AVAILABLE:
            log_buffer.log('Mesh', 'DracoPy not available - cannot process v7 meshes')
            return None
        if DracoPy is None:
            msg = 'DracoPy availability state is inconsistent'
            raise RuntimeError(msg)

        try:
            mesh = DracoPy.decode(coremesh_data[4:])
        except (DracoPy.FileTypeException, RuntimeError, TypeError, ValueError) as e:
            log_buffer.log('Mesh', f'DracoPy decoding error: {e}')
            return None
        if mesh is None or not _has_points(mesh):
            msg = 'Draco decode returned invalid mesh data'
            raise ValueError(msg)

        positions: NDArray[np.float32] = np.array(mesh.points, dtype=np.float32)
        num_verts = len(positions)
        if num_verts == 0:
            msg = 'Draco mesh has no vertices'
            raise ValueError(msg)
        verts = [Vertex() for _ in range(num_verts)]
        for i in range(num_verts):
            verts[i].px, verts[i].py, verts[i].pz = positions[i]

        normals: NDArray[np.float32] | None = None
        if _has_attributes(mesh):
            try:
                normal_attr = mesh.get_attribute_by_unique_id(1)
                if normal_attr is not None and 'data' in normal_attr:
                    normals = np.array(normal_attr['data'], dtype=np.float32)
                    if normals.ndim == 1:
                        normals = normals.reshape(-1, 3)
            except KeyError, RuntimeError, TypeError, ValueError:
                pass
        if normals is None and _has_normals(mesh) and mesh.normals is not None:
            normals = np.array(mesh.normals, dtype=np.float32)
            if normals.ndim == 1:
                normals = normals.reshape(-1, 3)
        if normals is not None:
            if len(normals) == num_verts:
                for i in range(num_verts):
                    verts[i].nx, verts[i].ny, verts[i].nz = normals[i]
            else:
                log_buffer.log(
                    'Mesh',
                    f'Warning: Normal count mismatch ({len(normals)} vs {num_verts})',
                )

        tex_coords: NDArray[np.float32] | None = None
        if _has_attributes(mesh):
            try:
                uv_attr = mesh.get_attribute_by_unique_id(2)
                if uv_attr is not None and 'data' in uv_attr:
                    tex_coords = np.array(uv_attr['data'], dtype=np.float32)
                    if tex_coords.ndim == 1:
                        tex_coords = tex_coords.reshape(-1, 2)
            except KeyError, RuntimeError, TypeError, ValueError:
                pass

        colors: NDArray[np.uint8] | None = None
        if _has_attributes(mesh):
            try:
                color_attr = mesh.get_attribute_by_unique_id(4)
                if color_attr is not None and 'data' in color_attr:
                    colors = np.array(color_attr['data'], dtype=np.uint8)
                    if colors.ndim == 1:
                        colors = colors.reshape(-1, 4)
            except KeyError, RuntimeError, TypeError, ValueError:
                pass
        if colors is not None:
            if len(colors) == num_verts:
                for i in range(num_verts):
                    verts[i].r, verts[i].g, verts[i].b, verts[i].a = colors[i]
            else:
                log_buffer.log(
                    'Mesh',
                    f'Warning: Color count mismatch ({len(colors)} vs {num_verts})',
                )

        if tex_coords is None and _has_tex_coords(mesh) and mesh.tex_coord is not None:
            tex_coords = np.array(mesh.tex_coord, dtype=np.float32)
            if tex_coords.ndim == 1:
                tex_coords = tex_coords.reshape(-1, 2)
        if tex_coords is not None:
            if len(tex_coords) == num_verts:
                for i in range(num_verts):
                    verts[i].tu = tex_coords[i][0]
                    verts[i].tv = 1.0 - tex_coords[i][1]
            else:
                log_buffer.log(
                    'Mesh',
                    f'Warning: UV count mismatch ({len(tex_coords)} vs {num_verts})',
                )

        faces: list[Face] = []
        if _has_faces(mesh) and mesh.faces is not None:
            for triangle in mesh.faces:
                a, b, c = map(int, triangle)
                if _invalid_draco_face((a, b, c), num_verts):
                    msg = 'Draco face references an invalid vertex'
                    raise ValueError(msg)
                faces.append(Face(a + 1, b + 1, c + 1))
        log_buffer.log(
            'Mesh',
            f'Draco mesh decoded: {num_verts:,} vertices, {len(faces):,} faces',
        )
    else:
        msg = f'unsupported COREMESH chunk version {coremesh_version}'
        raise ValueError(msg)

    faces = _apply_chunked_lod(faces, lod_data)
    return _mesh_to_obj(verts, faces)


def process_v6_v7(data: bytes) -> str | None:
    """Process chunked v6 geometry and Draco-compressed v7 geometry."""
    try:
        return _process_v6_v7_unchecked(data)
    except (IndexError, RuntimeError, struct.error, TypeError, ValueError) as e:
        log_buffer.log('Mesh', f'Error processing v6/v7 mesh: {e}')
        return None


SUPPORTED_MESH_HEADERS = (
    'version 1.',
    'version 2.00',
    'version 3.00',
    'version 3.01',
    'version 4.00',
    'version 4.01',
    'version 5.00',
    'version 6.00',
    'version 7.00',
)


def _mesh_header(data: bytes) -> str:
    if data.startswith(b'\x1f\x8b'):
        try:
            data = gzip.decompress(data)
        except EOFError, OSError, zlib.error:
            return ''
    return data[:12].decode('utf-8', errors='ignore').strip()


def is_mesh_data(data: bytes) -> bool:
    """Return True when bytes look like a Roblox mesh payload."""
    if not data or len(data) < 12:
        return False
    header = _mesh_header(data)
    return any(header.startswith(prefix) for prefix in SUPPORTED_MESH_HEADERS)


# Main Conversion Function
def convert(data: bytes, output_path: str | None = None) -> str | None:
    """
    Convert Roblox mesh data to OBJ format
    Args:
        data: Binary mesh file data
        output_path: Optional path to write OBJ file to
    Returns:
        OBJ file content as string, or None on failure
    """
    if not data or len(data) < 12:
        log_buffer.log('Mesh', 'Invalid mesh data: file too small')
        return None
    if data.startswith(b'\x1f\x8b'):
        try:
            data = gzip.decompress(data)
        except (EOFError, OSError, zlib.error) as e:
            log_buffer.log('Mesh', f'Failed to decompress gzip mesh data: {e}')
            return None
    # Detect version from header
    header = _mesh_header(data)
    log_buffer.log('Mesh', f'Detected mesh version: {header}')
    obj_content = None
    # Route to appropriate processor
    if header.startswith('version 1.'):
        obj_content = process_v1(data)
    elif header in {
        'version 2.00',
        'version 3.00',
        'version 3.01',
        'version 4.00',
        'version 4.01',
        'version 5.00',
    }:
        version_num = header.split()[1]  # Extract "X.XX"
        obj_content = process_v2_to_v5(data, version_num)
    elif header in {'version 6.00', 'version 7.00'}:
        obj_content = process_v6_v7(data)
    else:
        log_buffer.log('Mesh', f'Unsupported mesh version: {header}')
        return None
    # Write to file if path provided
    if obj_content and output_path:
        try:
            Path(output_path).write_text(obj_content, encoding='utf-8')
            log_buffer.log('Mesh', f'OBJ file written to: {output_path}')
        except (OSError, UnicodeError) as e:
            log_buffer.log('Mesh', f'Failed to write OBJ file: {e}')
    return obj_content


# Standalone Usage
if __name__ == '__main__':
    import sys
    from pathlib import Path

    if len(sys.argv) < 2:
        log_buffer.log('Mesh', 'Usage: python mesh_processing.py <mesh_file>')
        log_buffer.log('Mesh', 'Example: python mesh_processing.py model.mesh')
        sys.exit(1)
    mesh_path = Path(sys.argv[1])
    if not mesh_path.exists():
        log_buffer.log('Mesh', f'File not found: {mesh_path}')
        sys.exit(1)
    # Read mesh data
    data = mesh_path.read_bytes()
    # Convert to OBJ
    output_path = mesh_path.with_suffix('.obj')
    obj_content = convert(data, str(output_path))
    if obj_content:
        log_buffer.log('Mesh', '\n✓ Conversion successful!')
        log_buffer.log('Mesh', f' Input: {mesh_path}')
        log_buffer.log('Mesh', f' Output: {output_path}')
    else:
        log_buffer.log('Mesh', '\n✗ Conversion failed')
        sys.exit(1)
