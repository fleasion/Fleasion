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
MAX_VERSION = 5  # Updated to support v5 (Roblox changed format)
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


def _decode_faces5_state_machine(vertex_data: bytes, vertex_count: int) -> list[int]:
    """Decode V5 delta-encoded position indices (Faces5 state machine).

    Reference: krakow10/rbx_mesh mesh_data.rs  ``read_state_machine``

    Each decoded value is an absolute position index accumulated from per-value
    deltas.  Three encoding cases determined by the high bits of the lead byte:

    * ``v0 & 0x80 == 0 and v0 & 0x40 == 0``  (v0 = 0..63):
        Positive delta: ``index_out += v0``
    * ``v0 & 0x80 == 0 and v0 & 0x40 != 0``  (v0 = 64..127):
        Negative delta: ``index_out += (v0 | 0x80) - 256``
        (maps 64→-64 .. 127→-1)
    * ``v0 & 0x80 != 0``  (v0 = 128..255):
        3-byte large positive delta: read ``v1``, ``v2`` then
        ``index_out += v2 | (v1 << 8) | ((v0 & 0x7F) << 16)``

    The final index is ``index_out & 0x7FFFFF`` (23-bit modular ring).
    """
    indices: list[int] = []
    index_out = 0
    pos = 0
    data_len = len(vertex_data)

    for _ in range(vertex_count):
        if pos >= data_len:
            log.warning('V5 Faces5: ran out of vertex_data early (%d/%d)', len(indices), vertex_count)
            break

        v0 = vertex_data[pos]; pos += 1

        if v0 & 0x80 == 0:
            if v0 & 0x40 == 0:
                # positive delta: 0..63
                index_out += v0
            else:
                # negative delta: 64..127 → -64..-1
                # (v0 | 0x80) is 192..255; interpreted as signed i8: -64..-1
                # we negate then subtract, equivalent to index_out += signed_value
                index_out += (v0 | 0x80) - 256
        else:
            # 3-byte large positive delta
            if pos + 2 > data_len:
                log.warning('V5 Faces5: truncated 3-byte delta at pos %d', pos - 1)
                break
            v1 = vertex_data[pos];     pos += 1
            v2 = vertex_data[pos];     pos += 1
            index_out += v2 | (v1 << 8) | ((v0 & 0x7F) << 16)

        indices.append(index_out & 0x7FFFFF)

    return indices


def _parse_csg_mesh_v5(encrypted_data: bytes, version: int) -> CSGMeshData:
    """Parse a CSGMDL version-5 binary blob.

    V5 body layout (``body = encrypted_data[10:]``, fully plaintext after the
    10-byte XOR-obfuscated header):

    .. code-block:: text

        [uint16]              N  — unique attribute entry count
        N × [f32×3]           positions
        [uint16=N][uint32=N*6] N × [i16×3]  normals  (quantised)
        [uint16=N]             N × [u8×4]   RGBA colors
        [uint16=N]             N × u8       NormalId  (face-normal axis 1-6)
        [uint16=N]             N × [f32×2]  UV coordinates
        [uint16=N][uint32=N*6] N × [i16×3]  tangents  (quantised)
        Faces5 block:
            [uint32]               vertex_count  — total decoded indices
            [uint32]               vertex_data_len
            [u8 × vertex_data_len] vertex_data   — delta-encoded indices
            [u8]                   range_marker_count
            [u32 × rmc]            range_markers

    The ``range_markers`` split the decoded index list into sub-meshes:
      * ``range_markers[0]`` — start of used indices (almost always 0)
      * ``range_markers[1]`` — end of visual mesh indices
      * ``range_markers[2]`` — end of all indices (visual + collision/BREP)

    Position indices are direct references into the attribute arrays (no modulo,
    no XOR); a position index ``i`` uses ``positions[i]``, ``normals[i]``,
    ``colors[i]``, ``tex[i]``, and ``tangents[i]``.
    """
    body = encrypted_data[10:]

    if len(body) < 22:
        raise ValueError(f'CSGMDL v{version}: body too short ({len(body)} bytes)')

    # ── N: unique attribute entry count ────────────────────────────────────
    N = struct.unpack_from('<H', body, 0)[0]
    if N == 0 or N > 100_000:
        raise ValueError(f'CSGMDL v{version}: implausible entry count {N}')

    # ── Positions: N × float32×3 ────────────────────────────────────────────
    pos_end = 2 + N * 12
    if pos_end > len(body):
        raise ValueError(f'CSGMDL v{version}: position block overflows body')

    positions: list[tuple[float, float, float]] = []
    for i in range(N):
        x, y, z = struct.unpack_from('<3f', body, 2 + i * 12)
        positions.append((x, y, z))

    # ── Normals: [uint16=N][uint32=N*6] N × int16×3  (quantised) ───────────
    norm_section_start = pos_end
    ns_count = struct.unpack_from('<H', body, norm_section_start)[0]
    ns_bytes = struct.unpack_from('<I', body, norm_section_start + 2)[0]
    normals: list[tuple[float, float, float]] = []
    norm_data_start = norm_section_start + 6
    if ns_count == N and norm_data_start + ns_bytes <= len(body):
        for i in range(N):
            rx, ry, rz = struct.unpack_from('<3h', body, norm_data_start + i * 6)
            fx, fy, fz = rx / 32767.0, ry / 32767.0, rz / 32767.0
            mag = (fx * fx + fy * fy + fz * fz) ** 0.5
            if mag > 1e-6:
                fx /= mag; fy /= mag; fz /= mag
            normals.append((fx, fy, fz))
    if len(normals) != N:
        normals = [(0.0, 1.0, 0.0)] * N
    norm_end = norm_section_start + 6 + N * 6

    # ── Colors: [uint16=N] N × uint8×4 ─────────────────────────────────────
    colors: list[tuple[int, int, int, int]] = []
    color_start = norm_end
    if color_start + 2 + N * 4 <= len(body):
        cs_n = struct.unpack_from('<H', body, color_start)[0]
        if cs_n == N:
            for i in range(N):
                r, g, b, a = body[color_start + 2 + i * 4 : color_start + 6 + i * 4]
                colors.append((r, g, b, a))
    if len(colors) != N:
        colors = [(127, 127, 127, 255)] * N
    color_end = color_start + 2 + N * 4

    # ── NormalId / UV-gen type: [uint16=N] N × uint8 ────────────────────────
    # Stores a NormalId value (1-6) encoding the dominant face axis for UV gen.
    extra_gen: list[int] = []
    extra_start = color_end
    if extra_start + 2 + N <= len(body):
        es_n = struct.unpack_from('<H', body, extra_start)[0]
        if es_n == N:
            extra_gen = list(body[extra_start + 2 : extra_start + 2 + N])
    if len(extra_gen) != N:
        extra_gen = [0] * N
    extra_end = extra_start + 2 + N

    # ── UV coordinates: [uint16=N] N × float32×2 ────────────────────────────
    uv_studs: list[tuple[float, float]] = []
    uv_start = extra_end
    if uv_start + 2 + N * 8 <= len(body):
        us_n = struct.unpack_from('<H', body, uv_start)[0]
        if us_n == N:
            for i in range(N):
                us, vs = struct.unpack_from('<2f', body, uv_start + 2 + i * 8)
                uv_studs.append((us, vs))
    if len(uv_studs) != N:
        uv_studs = [(0.0, 0.0)] * N
    uv_end = uv_start + 2 + N * 8

    # ── Tangents: [uint16=N][uint32=N*6] N × int16×3  (quantised) ──────────
    tang_start = uv_end
    tc = struct.unpack_from('<H', body, tang_start)[0]
    tb = struct.unpack_from('<I', body, tang_start + 2)[0]
    tangents: list[tuple[float, float, float]] = []
    tang_data_start = tang_start + 6
    if tc == N and tang_data_start + tb <= len(body):
        for i in range(N):
            tx_, ty_, tz_ = struct.unpack_from('<3h', body, tang_data_start + i * 6)
            tangents.append((tx_ / 32767.0, ty_ / 32767.0, tz_ / 32767.0))
    if len(tangents) != N:
        tangents = [(1.0, 0.0, 0.0)] * N
    tang_end = tang_start + 6 + tb  # 6-byte header + tb bytes of data

    # ── Faces5 block ────────────────────────────────────────────────────────
    # Immediately follows the tangents section; NO separate trailer.
    faces_start = tang_end
    if faces_start + 9 > len(body):
        raise ValueError(f'CSGMDL v{version}: Faces5 block missing (body too short after tangents)')

    vertex_count_f  = struct.unpack_from('<I', body, faces_start)[0]
    vertex_data_len = struct.unpack_from('<I', body, faces_start + 4)[0]
    vd_start = faces_start + 8
    vd_end   = vd_start + vertex_data_len

    if vd_end > len(body):
        raise ValueError(
            f'CSGMDL v{version}: vertex_data_len={vertex_data_len} overflows body '
            f'(faces_start={faces_start}, body_len={len(body)})'
        )

    vertex_data = body[vd_start:vd_end]

    # Range markers: uint8 count + count × uint32
    rmc_offset          = vd_end
    range_marker_count  = body[rmc_offset]
    rm_start            = rmc_offset + 1
    rm_end              = rm_start + range_marker_count * 4
    if rm_end > len(body):
        raise ValueError(f'CSGMDL v{version}: range_markers overflow body')
    range_markers = [
        struct.unpack_from('<I', body, rm_start + i * 4)[0]
        for i in range(range_marker_count)
    ]

    log.debug(
        'CSGMDL v%d: N=%d, vertex_count=%d, vertex_data_len=%d, range_markers=%s',
        version, N, vertex_count_f, vertex_data_len, range_markers,
    )

    # ── Decode delta-encoded indices via Faces5 state machine ───────────────
    all_indices = _decode_faces5_state_machine(vertex_data, vertex_count_f)

    # Split into sub-meshes using range markers.
    # Marker layout (from rbx_mesh source):
    #   range_markers[0] = start of used range  (almost always 0)
    #   range_markers[1] = end of visual mesh
    #   range_markers[2] = end of all indices
    marker_start  = range_markers[0] if len(range_markers) > 0 else 0
    marker_visual = range_markers[1] if len(range_markers) > 1 else len(all_indices)
    visual_indices = all_indices[marker_start:marker_visual]

    # ── Build CSGVertex list and index list from visual indices ─────────────
    # Each decoded value is a DIRECT index into the N attribute arrays
    # (positions, normals, colors, tex, tangents).  No modulo, no XOR needed.
    vertices: list[CSGVertex] = []
    seen: dict[int, int] = {}    # position_index → vertex_buffer_index
    indices: list[int] = []
    n_degenerate = 0

    for tri_start in range(0, len(visual_indices) - 2, 3):
        ia = visual_indices[tri_start]
        ib = visual_indices[tri_start + 1]
        ic = visual_indices[tri_start + 2]

        # Bounds check
        if ia >= N or ib >= N or ic >= N:
            n_degenerate += 1
            continue

        # Skip degenerate triangles
        if (positions[ia] == positions[ib] or
                positions[ib] == positions[ic] or
                positions[ia] == positions[ic]):
            n_degenerate += 1
            continue

        for idx in (ia, ib, ic):
            if idx not in seen:
                seen[idx] = len(vertices)
                px, py, pz = positions[idx]
                nx, ny, nz = normals[idx]
                cr, cg, cb, ca = colors[idx]
                us, vs = uv_studs[idx]
                gen = extra_gen[idx]
                tx_, ty_, tz_ = tangents[idx]
                vertices.append(CSGVertex(
                    px=px, py=py, pz=pz,
                    nx=nx, ny=ny, nz=nz,
                    cr=cr, cg=cg, cb=cb, ca=ca,
                    extra_r=gen, extra_g=0, extra_b=0, extra_a=0,
                    u=us, v=vs,
                    u_studs=us, v_studs=vs,
                    u_decal=0.0, v_decal=0.0,
                    tx=tx_, ty=ty_, tz=tz_,
                    ed0=0.0, ed1=0.0, ed2=0.0, ed3=0.0,
                ))
            indices.append(seen[idx])

    if n_degenerate:
        log.debug('CSGMDL v%d: skipped %d degenerate/out-of-range triangles', version, n_degenerate)

    log.info(
        'CSGMDL v%d: N=%d → %d vertices, %d tris (%d degenerate skipped); '
        'visual_indices=%d/%d total range_markers=%s',
        version, N, len(vertices), len(indices) // 3,
        n_degenerate, len(visual_indices), len(all_indices), range_markers,
    )

    return CSGMeshData(
        vertices=vertices,
        indices=indices,
        version=version,
        submesh_boundaries=[],
    )


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

    # v5+ uses a completely different body layout (plaintext, no XOR on body).
    if version >= 5:
        return _parse_csg_mesh_v5(encrypted_data, version)

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