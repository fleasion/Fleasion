"""OBJ-to-CSGMDL converter.

Parses a Wavefront ``.obj`` file (including optional per-vertex RGB colors)
and produces a Roblox CSGMDL binary blob that can be injected directly into
the ``MeshData`` property of a PartOperation/UnionOperation instance inside an
RBXM document.

The converter is intentionally standalone — it does not depend on mitmproxy
or any proxy-side state so it can also be used as a batch-conversion utility.

Usage example::

    from obj_to_csg import export_csg_mesh
    csg_bytes = export_csg_mesh(Path("my_model.obj"))
    # csg_bytes can now be stored in a RbxProperty with fmt=PropertyFormat.STRING
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from .csg_mesh import CSGVertex, serialize_csg_mesh

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OBJ parser  (produces CSGVertex objects instead of the flat tuple layout
# used by obj_to_mesh.py — that module targets V2.00 .mesh files which have
# a different vertex structure than CSGMDL)
# ---------------------------------------------------------------------------


def _parse_float3(parts: list[str], start: int = 1) -> tuple[float, float, float]:
    return float(parts[start]), float(parts[start + 1]), float(parts[start + 2])


def _parse_float2(parts: list[str], start: int = 1) -> tuple[float, float]:
    return float(parts[start]), float(parts[start + 1])


def _clamp_byte(v: float) -> int:
    return min(255, max(0, int(v)))


def _parse_vertex_color(parts: list[str]) -> tuple[int, int, int]:
    """Parse optional RGB vertex color from a 'v x y z r g b' OBJ line.

    Supports both normalised (0.0–1.0) and absolute (0–255) ranges.
    """
    if len(parts) < 7:
        return 255, 255, 255
    r, g, b = float(parts[4]), float(parts[5]), float(parts[6])
    if r <= 1.0 and g <= 1.0 and b <= 1.0:
        return _clamp_byte(r * 255.0), _clamp_byte(g * 255.0), _clamp_byte(b * 255.0)
    return _clamp_byte(r), _clamp_byte(g), _clamp_byte(b)


def _compute_tangent(
    v0: tuple[float, float, float],
    v1: tuple[float, float, float],
    v2: tuple[float, float, float],
    uv0: tuple[float, float],
    uv1: tuple[float, float],
    uv2: tuple[float, float],
) -> tuple[float, float, float]:
    """Compute a simple tangent vector for a triangle (Lengyel's method).

    Falls back to (1, 0, 0) when the UV triangle is degenerate.
    """
    dp1 = (v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2])
    dp2 = (v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2])
    duv1 = (uv1[0] - uv0[0], uv1[1] - uv0[1])
    duv2 = (uv2[0] - uv0[0], uv2[1] - uv0[1])

    det = duv1[0] * duv2[1] - duv2[0] * duv1[1]
    if abs(det) < 1e-10:
        return 1.0, 0.0, 0.0

    r = 1.0 / det
    tx = r * (duv2[1] * dp1[0] - duv1[1] * dp2[0])
    ty = r * (duv2[1] * dp1[1] - duv1[1] * dp2[1])
    tz = r * (duv2[1] * dp1[2] - duv1[1] * dp2[2])
    mag = math.sqrt(tx * tx + ty * ty + tz * tz)
    if mag < 1e-10:
        return 1.0, 0.0, 0.0
    return tx / mag, ty / mag, tz / mag


def parse_obj_to_csg_vertices(
    obj_content: str,
) -> tuple[list[CSGVertex], list[int]]:
    """Parse OBJ text and return ``(vertices, flat_index_list)``.

    The returned index list is flat (every 3 consecutive values form a
    triangle) to match the layout expected by :func:`serialize_csg_mesh`.

    Tangent vectors are computed per-triangle and accumulated per unique
    vertex, then averaged and normalised before being stored in the vertex's
    ``tx/ty/tz`` fields.

    Parameters
    ----------
    obj_content
        Raw text content of a Wavefront OBJ file.

    Returns
    -------
    tuple of (vertices, indices)
        ``vertices`` is a list of fully-populated :class:`CSGVertex` objects.
        ``indices`` is a flat ``list[int]`` where every 3 entries form a face.
    """
    raw_v: list[tuple[float, float, float]] = []
    raw_vn: list[tuple[float, float, float]] = []
    raw_vt: list[tuple[float, float]] = []
    raw_vc: list[tuple[int, int, int]] = []  # per-position vertex colors

    # Unique (v_idx, vt_idx, vn_idx) → output vertex index
    unique_verts: dict[tuple[int, int, int], int] = {}
    vertices_out: list[CSGVertex] = []
    indices_out: list[int] = []

    # Accumulate tangents per output vertex index for later averaging
    tangent_accum: list[list[float]] = []  # [[tx, ty, tz], ...]

    # ── Pass 1: geometry data lines ─────────────────────────────────────────
    for raw_line in obj_content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if not parts:
            continue

        token = parts[0]

        if token == 'v':
            raw_v.append(_parse_float3(parts))
            raw_vc.append(_parse_vertex_color(parts))

        elif token == 'vn':
            raw_vn.append(_parse_float3(parts))

        elif token == 'vt':
            raw_vt.append(_parse_float2(parts))

        elif token == 'f':
            # Each face_part is "v", "v/vt", "v//vn", or "v/vt/vn"
            face_corners: list[tuple[int, int, int]] = []
            for face_part in parts[1:]:
                sp = face_part.split('/')
                vi = int(sp[0]) - 1
                ti = (int(sp[1]) - 1) if len(sp) >= 2 and sp[1] else -1
                ni = (int(sp[2]) - 1) if len(sp) >= 3 and sp[2] else -1
                face_corners.append((vi, ti, ni))

            # Fan-triangulate the face
            for i in range(1, len(face_corners) - 1):
                tri_corners = [face_corners[0], face_corners[i], face_corners[i + 1]]
                tri_out: list[int] = []

                for vi, ti, ni in tri_corners:
                    if vi < 0 or vi >= len(raw_v):
                        break  # skip degenerate face

                    # Build or reuse the unique vertex
                    key = (vi, ti, ni)
                    if key not in unique_verts:
                        vx, vy, vz = raw_v[vi]
                        cr, cg, cb = raw_vc[vi] if vi < len(raw_vc) else (255, 255, 255)

                        nx, ny, nz = (0.0, 1.0, 0.0)
                        if ni != -1 and 0 <= ni < len(raw_vn):
                            nx, ny, nz = raw_vn[ni]

                        tu, tv = 0.0, 0.0
                        if ti != -1 and 0 <= ti < len(raw_vt):
                            tu, tv = raw_vt[ti]
                        # Roblox's coordinate system flips the V axis
                        tv = 1.0 - tv

                        vert = CSGVertex(
                            px=vx, py=vy, pz=vz,
                            nx=nx, ny=ny, nz=nz,
                            cr=cr, cg=cg, cb=cb, ca=255,
                            # extra_r is a 1-indexed surface/part ID mapping the vertex to one
                            # of the source Parts from ChildData.  The engine rejects 0 as
                            # invalid.  We have no per-face part information from OBJ, so we
                            # claim surface 1 for every vertex (the safest valid value).
                            extra_r=1, extra_g=0, extra_b=0, extra_a=0,
                            u=tu, v=tv,
                            # uvStuds / uvDecal are auxiliary UV channels used by Roblox's
                            # SurfaceAppearance system.  The engine stores 0.0 for both when
                            # no stud/decal mapping is needed; non-zero values here confuse
                            # the texture pipeline and can cause visual or load failures.
                            u_studs=0.0, v_studs=0.0,
                            u_decal=0.0, v_decal=0.0,
                            # Tangent will be filled in during pass 2
                            tx=0.0, ty=0.0, tz=0.0,
                            ed0=0.0, ed1=0.0, ed2=0.0, ed3=0.0,
                        )
                        vert_idx = len(vertices_out)
                        unique_verts[key] = vert_idx
                        vertices_out.append(vert)
                        tangent_accum.append([0.0, 0.0, 0.0])

                    tri_out.append(unique_verts[key])

                if len(tri_out) == 3:
                    indices_out.extend(tri_out)

    # ── Pass 2: compute and accumulate tangents ──────────────────────────────
    # Walk over every triangle and add its tangent contribution to each
    # of the three corner vertices.
    for i in range(0, len(indices_out), 3):
        ia, ib, ic = indices_out[i], indices_out[i + 1], indices_out[i + 2]
        va, vb, vc = vertices_out[ia], vertices_out[ib], vertices_out[ic]

        p0 = (va.px, va.py, va.pz)
        p1 = (vb.px, vb.py, vb.pz)
        p2 = (vc.px, vc.py, vc.pz)
        uv0 = (va.u, va.v)
        uv1 = (vb.u, vb.v)
        uv2 = (vc.u, vc.v)

        tx, ty, tz = _compute_tangent(p0, p1, p2, uv0, uv1, uv2)

        for vi in (ia, ib, ic):
            tangent_accum[vi][0] += tx
            tangent_accum[vi][1] += ty
            tangent_accum[vi][2] += tz

    # ── Pass 3: normalise accumulated tangents and write back ────────────────
    for vi, acc in enumerate(tangent_accum):
        ax, ay, az = acc
        mag = math.sqrt(ax * ax + ay * ay + az * az)
        if mag > 1e-10:
            ax, ay, az = ax / mag, ay / mag, az / mag
        else:
            ax, ay, az = 1.0, 0.0, 0.0

        v = vertices_out[vi]
        vertices_out[vi] = CSGVertex(
            px=v.px, py=v.py, pz=v.pz,
            nx=v.nx, ny=v.ny, nz=v.nz,
            cr=v.cr, cg=v.cg, cb=v.cb, ca=v.ca,
            extra_r=v.extra_r, extra_g=v.extra_g, extra_b=v.extra_b, extra_a=v.extra_a,
            u=v.u, v=v.v,
            u_studs=v.u_studs, v_studs=v.v_studs,
            u_decal=v.u_decal, v_decal=v.v_decal,
            tx=ax, ty=ay, tz=az,
            ed0=v.ed0, ed1=v.ed1, ed2=v.ed2, ed3=v.ed3,
        )

    log.info(
        'OBJ parsed: %d positions, %d normals, %d UVs → %d unique vertices, %d triangles',
        len(raw_v), len(raw_vn), len(raw_vt),
        len(vertices_out), len(indices_out) // 3,
    )
    return vertices_out, indices_out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def export_csg_mesh(obj_path: Path, version: int = 2) -> bytes:
    """Read an OBJ file and return an XOR-encrypted CSGMDL binary blob.

    The returned bytes are ready for direct injection into a ``MeshData``
    property — no further processing is required.

    Parameters
    ----------
    obj_path
        Path to the source ``.obj`` file.  Must be readable.
    version
        CSGMDL format version to emit.  Defaults to 3 (accepted by all
        modern Roblox engine builds).

    Returns
    -------
    bytes
        Encrypted CSGMDL blob.

    Raises
    ------
    FileNotFoundError
        If ``obj_path`` does not exist.
    ValueError
        If the OBJ produces no usable geometry (empty file, all-degenerate
        faces, etc.).
    """
    obj_path = Path(obj_path)
    if not obj_path.exists():
        raise FileNotFoundError(f'OBJ file not found: {obj_path}')

    obj_content = obj_path.read_text(encoding='utf-8', errors='replace')
    vertices, indices = parse_obj_to_csg_vertices(obj_content)

    if not vertices or not indices:
        raise ValueError(f'OBJ file produced no usable geometry: {obj_path}')

    log.info(
        'Serializing CSGMDL v%d from %s: %d vertices, %d triangles',
        version, obj_path.name, len(vertices), len(indices) // 3,
    )
    return serialize_csg_mesh(vertices, indices, version=version)