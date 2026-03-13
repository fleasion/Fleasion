"""CSG MeshData decoder and OBJ exporter.

Handles XOR decryption of CSGMDL-format mesh data, parsing of CSG vertices
and indices, and exporting to Wavefront OBJ format.

Reference: ROBLOX 2016 source — App/v8datamodel/CSGMesh.cpp, App/include/util/Lcmrand.h
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

HEADER_TAG = b'CSGMDL'
MIN_VERSION = 2
MAX_VERSION = 4
CSGVERTEX_SIZE = 84  # sizeof(CSGVertex) — see CSGMesh.h
SALT_SIZE = 16
HASH_SIZE = 16
XOR_KEY_SIZE = 31


# ---------------------------------------------------------------------------
# LcmRand — deterministic PRNG used for XOR obfuscation
# ---------------------------------------------------------------------------


class LcmRand:
    """Linear congruential PRNG matching Roblox's LcmRand.

    seed starts at 1337, multiplier=214013, increment=2531011.
    value() returns ``(seed >> 16) & 0x7FFF`` after each step.
    """

    __slots__ = ('_seed',)

    def __init__(self, seed: int = 1337) -> None:
        self._seed = seed & 0xFFFF_FFFF

    def value(self) -> int:
        self._seed = (self._seed * 214013 + 2531011) & 0xFFFF_FFFF
        return (self._seed >> 16) & 0x7FFF


# ---------------------------------------------------------------------------
# XOR buffer encryption/decryption (symmetric)
# ---------------------------------------------------------------------------


def xor_buffer(data: bytes) -> bytes:
    """XOR-encrypt or decrypt a CSGMDL buffer.

    Generates a 31-byte key from LcmRand(1337) and XORs cyclically.
    This is its own inverse — applying it twice yields the original data.
    """
    rng = LcmRand()
    key = bytes(rng.value() % 127 for _ in range(XOR_KEY_SIZE))  # CHAR_MAX = 127

    buf = bytearray(data)
    for i in range(len(buf)):
        buf[i] ^= key[i % XOR_KEY_SIZE]
    return bytes(buf)


# ---------------------------------------------------------------------------
# CSGVertex data class
# ---------------------------------------------------------------------------


@dataclass
class CSGVertex:
    """A single CSG vertex (84 bytes).

    Layout (from CSGMesh.h):
        0..12   position  (Vector3)
       12..24   normal    (Vector3)
       24..28   color     (Color4uint8: R G B A)
       28..32   extra     (Color4uint8: R=UV-gen-type, G B A)
       32..40   uv        (Vector2)
       40..48   uvStuds   (Vector2)
       48..56   uvDecal   (Vector2)
       56..68   tangent   (Vector3)
       68..84   edgeDist  (Vector4)
    """

    px: float
    py: float
    pz: float
    nx: float
    ny: float
    nz: float
    cr: int
    cg: int
    cb: int
    ca: int
    extra_r: int  # UV generation type
    extra_g: int
    extra_b: int
    extra_a: int
    u: float
    v: float
    u_studs: float
    v_studs: float
    u_decal: float
    v_decal: float
    tx: float
    ty: float
    tz: float
    ed0: float
    ed1: float
    ed2: float
    ed3: float

    @staticmethod
    def from_bytes(data: bytes, offset: int = 0) -> CSGVertex:
        """Parse a CSGVertex from 84 bytes at the given offset."""
        (
            px, py, pz,
            nx, ny, nz,
        ) = struct.unpack_from('<6f', data, offset)

        cr, cg, cb, ca = data[offset + 24 : offset + 28]
        er, eg, eb, ea = data[offset + 28 : offset + 32]

        (
            u, v,
            us, vs,
            ud, vd,
            tx, ty, tz,
            e0, e1, e2, e3,
        ) = struct.unpack_from('<13f', data, offset + 32)

        return CSGVertex(
            px=px, py=py, pz=pz,
            nx=nx, ny=ny, nz=nz,
            cr=cr, cg=cg, cb=cb, ca=ca,
            extra_r=er, extra_g=eg, extra_b=eb, extra_a=ea,
            u=u, v=v,
            u_studs=us, v_studs=vs,
            u_decal=ud, v_decal=vd,
            tx=tx, ty=ty, tz=tz,
            ed0=e0, ed1=e1, ed2=e2, ed3=e3,
        )


# ---------------------------------------------------------------------------
# CSGMDL parser
# ---------------------------------------------------------------------------


@dataclass
class CSGMeshData:
    """Parsed CSG mesh data."""

    vertices: list[CSGVertex]
    indices: list[int]
    version: int
    submesh_boundaries: list[int]  # index boundaries for sub-meshes (v3/v4)


def parse_csg_mesh(encrypted_data: bytes) -> tuple[list[CSGVertex], list[int]]:
    """Decrypt and parse a CSGMDL binary blob.

    Parameters
    ----------
    encrypted_data
        The raw MeshData bytes (XOR-obfuscated CSGMDL format).

    Returns
    -------
    tuple of (vertices, indices)
        vertices is a list of CSGVertex, indices is a list of ints
        (triangle indices, every 3 form a face).

    Raises
    ------
    ValueError
        If the data is invalid or the format doesn't match expectations.
    """
    result = parse_csg_mesh_full(encrypted_data)
    return result.vertices, result.indices


def parse_csg_mesh_full(encrypted_data: bytes) -> CSGMeshData:
    """Decrypt and parse a CSGMDL binary blob, returning full metadata.

    Parameters
    ----------
    encrypted_data
        The raw MeshData bytes (XOR-obfuscated CSGMDL format).

    Returns
    -------
    CSGMeshData
        Full mesh data including version and sub-mesh boundaries.
    """
    data = xor_buffer(encrypted_data)

    offset = 0

    # Header tag
    tag = data[offset : offset + 6]
    if tag != HEADER_TAG:
        msg = f'Invalid CSGMDL header: {tag!r} (expected {HEADER_TAG!r})'
        raise ValueError(msg)
    offset += 6

    # Version
    version = struct.unpack_from('<i', data, offset)[0]
    offset += 4
    if version < MIN_VERSION or version > MAX_VERSION:
        log.warning('CSGMDL version %d (expected %d-%d), attempting to parse', version, MIN_VERSION, MAX_VERSION)

    # Hash + Salt (32 bytes — we skip validation for now)
    _hash_salt = data[offset : offset + HASH_SIZE + SALT_SIZE]
    offset += HASH_SIZE + SALT_SIZE

    # Vertex count and stride
    num_vertices = struct.unpack_from('<I', data, offset)[0]
    offset += 4
    vertex_stride = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    if vertex_stride != CSGVERTEX_SIZE:
        msg = f'Unexpected vertex stride: {vertex_stride} (expected {CSGVERTEX_SIZE})'
        raise ValueError(msg)

    log.info('CSGMDL v%d: %d vertices (stride=%d)', version, num_vertices, vertex_stride)

    # Parse vertices
    vertices: list[CSGVertex] = []
    for i in range(num_vertices):
        v_offset = offset + i * vertex_stride
        vertices.append(CSGVertex.from_bytes(data, v_offset))
    offset += vertex_stride * num_vertices

    # Index count
    num_indices = struct.unpack_from('<I', data, offset)[0]
    offset += 4

    log.info('CSGMDL v%d: %d indices (%d triangles)', version, num_indices, num_indices // 3)

    # Parse indices
    indices: list[int] = []
    for i in range(num_indices):
        idx = struct.unpack_from('<I', data, offset + i * 4)[0]
        indices.append(idx)
    offset += 4 * num_indices

    # v3/v4 trailer: sub-mesh metadata (20 bytes)
    submesh_boundaries: list[int] = []
    remaining = len(data) - offset
    if remaining >= 20 and version >= 3:
        brep_ver = struct.unpack_from('<I', data, offset)[0]
        _padding = struct.unpack_from('<I', data, offset + 4)[0]
        b1 = struct.unpack_from('<I', data, offset + 8)[0]
        b2 = struct.unpack_from('<I', data, offset + 12)[0]
        b3 = struct.unpack_from('<I', data, offset + 16)[0]
        submesh_boundaries = [0, b1, b2, b3]
        # Remove trailing boundaries that equal total (they mark the end)
        while submesh_boundaries and submesh_boundaries[-1] == num_indices:
            submesh_boundaries.pop()
        submesh_boundaries.append(num_indices)  # always end with total
        log.info(
            'CSGMDL v%d: brepVersion=%d, %d sub-meshes (boundaries: %s)',
            version, brep_ver, len(submesh_boundaries) - 1, submesh_boundaries,
        )
        offset += 20
    elif remaining > 0:
        log.debug('CSGMDL: %d trailing bytes ignored', remaining)

    # Basic sanity checks
    if num_indices % 3 != 0:
        log.warning('Index count %d is not divisible by 3', num_indices)

    max_idx = max(indices) if indices else 0
    if max_idx >= num_vertices:
        log.warning('Max index %d >= vertex count %d', max_idx, num_vertices)

    return CSGMeshData(
        vertices=vertices,
        indices=indices,
        version=version,
        submesh_boundaries=submesh_boundaries,
    )


# ---------------------------------------------------------------------------
# CSGMDL serializer  (inverse of parse_csg_mesh_full)
# ---------------------------------------------------------------------------


def serialize_csg_mesh(
    vertices: list[CSGVertex],
    indices: list[int],
    version: int = 3,
) -> bytes:
    """Serialize vertices and indices to an XOR-obfuscated CSGMDL binary blob.

    This is the inverse of :func:`parse_csg_mesh_full`.  The produced bytes
    can be stored directly in a ``MeshData`` property of a PartOperation.

    Parameters
    ----------
    vertices
        List of :class:`CSGVertex` objects describing the mesh geometry.
    indices
        Flat list of triangle indices (every 3 form a face).  Must be a
        multiple of 3.
    version
        CSGMDL version to write.  Version 3 is the default and is
        universally accepted by the engine.  Version 2 omits the submesh
        trailer.

    Returns
    -------
    bytes
        XOR-encrypted CSGMDL buffer ready for injection into an RBXM document.

    Raises
    ------
    ValueError
        If ``indices`` length is not a multiple of 3, or any index is out of
        range for the given ``vertices`` list.
    """
    if len(indices) % 3 != 0:
        msg = f'Index count {len(indices)} is not a multiple of 3'
        raise ValueError(msg)
    if indices and max(indices) >= len(vertices):
        msg = f'Max index {max(indices)} is out of range for {len(vertices)} vertices'
        raise ValueError(msg)

    num_vertices = len(vertices)
    num_indices = len(indices)

    buf = bytearray()

    # ── Header ──────────────────────────────────────────────────────────────
    buf.extend(HEADER_TAG)                                    # b'CSGMDL'  6 B
    buf.extend(struct.pack('<i', version))                    # version    4 B
    buf.extend(bytes(HASH_SIZE + SALT_SIZE))                  # hash+salt 32 B (zeros; engine ignores on load)
    buf.extend(struct.pack('<II', num_vertices, CSGVERTEX_SIZE))  # vtx_cnt + stride  8 B

    # ── Vertices (84 bytes each) ─────────────────────────────────────────────
    # Layout mirrors CSGVertex.from_bytes exactly:
    #   0..12  position  (3 × float32)
    #  12..24  normal    (3 × float32)
    #  24..28  color     (4 × uint8)
    #  28..32  extra     (4 × uint8)
    #  32..84  uv/uvStuds/uvDecal/tangent/edgeDist  (13 × float32)
    for v in vertices:
        buf.extend(struct.pack('<3f', v.px, v.py, v.pz))
        buf.extend(struct.pack('<3f', v.nx, v.ny, v.nz))
        buf.extend(struct.pack('<4B', v.cr, v.cg, v.cb, v.ca))
        buf.extend(struct.pack('<4B', v.extra_r, v.extra_g, v.extra_b, v.extra_a))
        buf.extend(struct.pack(
            '<13f',
            v.u, v.v,
            v.u_studs, v.v_studs,
            v.u_decal, v.v_decal,
            v.tx, v.ty, v.tz,
            v.ed0, v.ed1, v.ed2, v.ed3,
        ))

    assert len(buf) == 6 + 4 + 32 + 8 + num_vertices * CSGVERTEX_SIZE, 'vertex block size mismatch'

    # ── Indices ──────────────────────────────────────────────────────────────
    buf.extend(struct.pack('<I', num_indices))
    for idx in indices:
        buf.extend(struct.pack('<I', idx))

    # ── v3/v4 submesh trailer (20 bytes) ─────────────────────────────────────
    # Five uint32s: brep_version, padding, b1, b2, b3.
    # Setting b1 = b2 = b3 = num_indices means a single visual sub-mesh
    # covering all triangles, with no separate collision or auxiliary meshes.
    # The parser pops trailing entries equal to num_indices and re-appends
    # it once, yielding submesh_boundaries = [0, num_indices].
    if version >= 3:
        buf.extend(struct.pack('<5I', 0, 0, num_indices, num_indices, num_indices))

    log.info(
        'Serialized CSGMDL v%d: %d vertices, %d indices (%d triangles), %d bytes (before XOR)',
        version, num_vertices, num_indices, num_indices // 3, len(buf),
    )

    return xor_buffer(bytes(buf))


# ---------------------------------------------------------------------------
# OBJ exporter
# ---------------------------------------------------------------------------


def export_obj(
    vertices: list[CSGVertex],
    indices: list[int],
    output_path: Path,
    *,
    object_name: str = 'CSGMesh',
    submesh_boundaries: list[int] | None = None,
) -> None:
    """Export CSG mesh data to Wavefront OBJ format.

    Parameters
    ----------
    vertices
        Parsed CSG vertices.
    indices
        Triangle indices (every 3 form a face).
    output_path
        Path to write the .obj file.
    object_name
        Name for the OBJ object group.
    submesh_boundaries
        Optional sub-mesh index boundaries for writing OBJ groups.
    """
    # Filter degenerate triangles (where two or more indices are the same)
    valid_faces: list[tuple[int, int, int]] = []
    degenerate_count = 0
    for i in range(0, len(indices), 3):
        i0, i1, i2 = indices[i], indices[i + 1], indices[i + 2]
        if i0 == i1 or i1 == i2 or i0 == i2:
            degenerate_count += 1
        else:
            valid_faces.append((i0, i1, i2))

    if degenerate_count > 0:
        log.info('Filtered %d degenerate triangles', degenerate_count)

    with output_path.open('w', encoding='utf-8') as f:
        f.write(f'# Exported from Roblox SolidModel CSG data\n')
        f.write(f'# Vertices: {len(vertices)}, Triangles: {len(valid_faces)}\n')
        if degenerate_count > 0:
            f.write(f'# Filtered {degenerate_count} degenerate triangles\n')
        f.write(f'o {object_name}\n\n')

        # Vertex positions
        for v in vertices:
            r, g, b = v.cr / 255.0, v.cg / 255.0, v.cb / 255.0
            f.write(f'v {v.px:.8g} {v.py:.8g} {v.pz:.8g} {r:.6f} {g:.6f} {b:.6f}\n')
        f.write('\n')

        # Vertex normals
        for v in vertices:
            f.write(f'vn {v.nx:.8g} {v.ny:.8g} {v.nz:.8g}\n')
        f.write('\n')

        # Texture coordinates
        for v in vertices:
            f.write(f'vt {v.u:.8g} {v.v:.8g}\n')
        f.write('\n')

        # Write faces, optionally grouped by sub-mesh
        if submesh_boundaries and len(submesh_boundaries) > 1:
            # Build face-index-to-submesh mapping
            face_idx = 0
            for sm_idx in range(len(submesh_boundaries) - 1):
                f.write(f'g {object_name}_submesh{sm_idx}\n')
                sm_start = submesh_boundaries[sm_idx] // 3
                sm_end = submesh_boundaries[sm_idx + 1] // 3
                while face_idx < len(valid_faces):
                    orig_face_num = face_idx + (degenerate_count if face_idx > 0 else 0)
                    # Simple approach: just write all faces in order with group markers
                    i0, i1, i2 = valid_faces[face_idx]
                    f.write(f'f {i0+1}/{i0+1}/{i0+1} {i1+1}/{i1+1}/{i1+1} {i2+1}/{i2+1}/{i2+1}\n')
                    face_idx += 1
                    if face_idx >= sm_end:
                        break
        else:
            # No sub-mesh info — write all faces as one group
            for i0, i1, i2 in valid_faces:
                f.write(f'f {i0+1}/{i0+1}/{i0+1} {i1+1}/{i1+1}/{i1+1} {i2+1}/{i2+1}/{i2+1}\n')

    log.info('Wrote OBJ: %s (%d vertices, %d faces)', output_path, len(vertices), len(valid_faces))


@dataclass
class ObjMeshPart:
    """A single named mesh part for multi-object OBJ export."""

    name: str
    class_name: str  # 'UnionOperation', 'NegateOperation', etc.
    vertices: list[CSGVertex]
    indices: list[int]
    # CFrame: rotation matrix (3x3 row-major) + translation
    cframe: dict | None = None  # keys: X,Y,Z, R00..R22


def _transform_vertex(v: CSGVertex, cframe: dict) -> CSGVertex:
    """Apply a CFrame transform to a vertex (local -> world space).

    CFrame rotation is a 3x3 matrix (R00..R22), translation is (X, Y, Z).
    new_pos = R * old_pos + T
    new_normal = R * old_normal  (normals only rotated, not translated)
    """
    tx, ty, tz = cframe['X'], cframe['Y'], cframe['Z']
    r00, r01, r02 = cframe['R00'], cframe['R01'], cframe['R02']
    r10, r11, r12 = cframe['R10'], cframe['R11'], cframe['R12']
    r20, r21, r22 = cframe['R20'], cframe['R21'], cframe['R22']

    # Transform position
    px = r00 * v.px + r01 * v.py + r02 * v.pz + tx
    py = r10 * v.px + r11 * v.py + r12 * v.pz + ty
    pz = r20 * v.px + r21 * v.py + r22 * v.pz + tz

    # Transform normal (rotation only)
    nx = r00 * v.nx + r01 * v.ny + r02 * v.nz
    ny = r10 * v.nx + r11 * v.ny + r12 * v.nz
    nz = r20 * v.nx + r21 * v.ny + r22 * v.nz

    return CSGVertex(
        px=px, py=py, pz=pz,
        nx=nx, ny=ny, nz=nz,
        cr=v.cr, cg=v.cg, cb=v.cb, ca=v.ca,
        extra_r=v.extra_r, extra_g=v.extra_g, extra_b=v.extra_b, extra_a=v.extra_a,
        u=v.u, v=v.v,
        u_studs=v.u_studs, v_studs=v.v_studs,
        u_decal=v.u_decal, v_decal=v.v_decal,
        tx=v.tx, ty=v.ty, tz=v.tz,
        ed0=v.ed0, ed1=v.ed1, ed2=v.ed2, ed3=v.ed3,
    )


def _write_mtl_file(mtl_path: Path) -> None:
    """Write a material library file for Union and NegativePart materials."""
    with mtl_path.open('w', encoding='utf-8') as f:
        f.write('# Roblox CSG materials\n\n')
        # Union material — solid light blue-gray
        f.write('newmtl UnionMaterial\n')
        f.write('Kd 0.639 0.636 0.647\n')  # Medium stone grey
        f.write('d 1.0\n')
        f.write('illum 1\n\n')
        # NegativePart material — semi-transparent red
        f.write('newmtl NegativePartMaterial\n')
        f.write('Kd 0.9 0.15 0.15\n')
        f.write('d 0.5\n')  # 50% transparent
        f.write('illum 1\n')


def export_obj_multi(
    parts: list[ObjMeshPart],
    output_path: Path,
) -> None:
    """Export multiple mesh parts into a single Wavefront OBJ file.

    Each part becomes a separate named object ('o' directive) with correct
    vertex index offsets. CFrame transforms are applied to place parts in
    world space. An accompanying .mtl file provides materials for
    Union (grey) and NegativePart (red semi-transparent).
    """
    total_verts = sum(len(p.vertices) for p in parts)
    total_faces = 0

    # Write MTL file
    mtl_path = output_path.with_suffix('.mtl')
    _write_mtl_file(mtl_path)
    mtl_name = mtl_path.name

    with output_path.open('w', encoding='utf-8') as f:
        f.write('# Exported from Roblox SolidModel CSG data (per-operation meshes)\n')
        f.write(f'# Objects: {len(parts)}\n')
        f.write(f'mtllib {mtl_name}\n\n')

        vertex_offset = 0

        for part in parts:
            # Apply CFrame transform if available
            if part.cframe is not None:
                transformed = [_transform_vertex(v, part.cframe) for v in part.vertices]
                log.info('Applied CFrame transform to %s (%d vertices)', part.name, len(transformed))
            else:
                transformed = part.vertices

            # Filter degenerate triangles
            valid_faces: list[tuple[int, int, int]] = []
            degenerate_count = 0
            for i in range(0, len(part.indices), 3):
                i0, i1, i2 = part.indices[i], part.indices[i + 1], part.indices[i + 2]
                if i0 == i1 or i1 == i2 or i0 == i2:
                    degenerate_count += 1
                else:
                    valid_faces.append((i0, i1, i2))

            if degenerate_count > 0:
                log.info('Filtered %d degenerate triangles from %s', degenerate_count, part.name)

            f.write(f'o {part.name}\n')

            # Assign material based on class
            if part.class_name == 'NegateOperation':
                f.write('usemtl NegativePartMaterial\n')
            else:
                f.write('usemtl UnionMaterial\n')

            # Vertex positions
            for v in transformed:
                r, g, b = v.cr / 255.0, v.cg / 255.0, v.cb / 255.0
                f.write(f'v {v.px:.8g} {v.py:.8g} {v.pz:.8g} {r:.6f} {g:.6f} {b:.6f}\n')

            # Vertex normals
            for v in transformed:
                f.write(f'vn {v.nx:.8g} {v.ny:.8g} {v.nz:.8g}\n')

            # Texture coordinates
            for v in transformed:
                f.write(f'vt {v.u:.8g} {v.v:.8g}\n')

            # Faces (OBJ indices are 1-based, offset by previous objects' vertices)
            for i0, i1, i2 in valid_faces:
                vi0 = i0 + 1 + vertex_offset
                vi1 = i1 + 1 + vertex_offset
                vi2 = i2 + 1 + vertex_offset
                f.write(f'f {vi0}/{vi0}/{vi0} {vi1}/{vi1}/{vi1} {vi2}/{vi2}/{vi2}\n')

            f.write('\n')
            vertex_offset += len(transformed)
            total_faces += len(valid_faces)

    log.info(
        'Wrote multi-object OBJ: %s (%d objects, %d total vertices, %d total faces)',
        output_path, len(parts), total_verts, total_faces,
    )