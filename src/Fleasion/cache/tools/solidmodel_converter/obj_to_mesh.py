"""Standalone OBJ to Roblox V2.00 Mesh converter.

Converts Wavefront OBJ files to Roblox's proprietary Version 2.00 binary mesh format.
Includes support for vertex colors if present in the OBJ file.
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path
from hashlib import md5

from ....utils import LOCAL_APPDATA

log = logging.getLogger(__name__)

# Temporary directory for converted meshes
CONVERTED_MESHES_DIR = LOCAL_APPDATA / 'FleasionNT' / 'Temp' / 'ConvertedMeshes'


def parse_obj_for_mesh(obj_content: str) -> tuple[list[tuple[float, ...]], list[tuple[int, int, int, int]], list[tuple[int, int, int]]]:
    """Parse OBJ text to extract interleaved vertices, colors, and faces.

    Returns:
        tuple containing (vertices, colors, indices)
        - vertices: list of (px, py, pz, nx, ny, nz, tu, tv, tw)
        - colors: list of (r, g, b, a) [0-255 uint8 values]
        - faces: list of (a, b, c) indices
    """
    raw_v: list[tuple[float, float, float]] = []
    raw_vn: list[tuple[float, float, float]] = []
    raw_vt: list[tuple[float, float]] = []
    raw_vc: list[tuple[int, int, int]] = []

    unique_verts: dict[tuple[int, int, int], int] = {}
    
    vertices_out: list[tuple[float, ...]] = []
    colors_out: list[tuple[int, int, int, int]] = []
    indices_out: list[tuple[int, int, int]] = []

    for line in obj_content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue

        parts = line.split()
        if not parts:
            continue

        if parts[0] == 'v':
            raw_v.append((float(parts[1]), float(parts[2]), float(parts[3])))
            # Parse RGB vertex colors if present
            if len(parts) >= 7:
                r, g, b = float(parts[4]), float(parts[5]), float(parts[6])
                # Convert 0.0-1.0 float ranges to 0-255 uint8, or clamp if they're absolute
                if r <= 1.0 and g <= 1.0 and b <= 1.0:
                    raw_vc.append((int(r * 255.0), int(g * 255.0), int(b * 255.0)))
                else:
                    raw_vc.append((min(255, max(0, int(r))), min(255, max(0, int(g))), min(255, max(0, int(b)))))
            else:
                raw_vc.append((255, 255, 255))
        elif parts[0] == 'vn':
            raw_vn.append((float(parts[1]), float(parts[2]), float(parts[3])))
        elif parts[0] == 'vt':
            raw_vt.append((float(parts[1]), float(parts[2])))
        elif parts[0] == 'f':
            face_verts = []
            for face_part in parts[1:]:
                indices_split = face_part.split('/')
                v_idx = int(indices_split[0]) - 1
                vt_idx = -1
                vn_idx = -1
                if len(indices_split) >= 2 and indices_split[1]:
                    vt_idx = int(indices_split[1]) - 1
                if len(indices_split) >= 3 and indices_split[2]:
                    vn_idx = int(indices_split[2]) - 1
                face_verts.append((v_idx, vt_idx, vn_idx))

            # Triangulate face (simple fan triangulation)
            for i in range(1, len(face_verts) - 1):
                tri = [face_verts[0], face_verts[i], face_verts[i + 1]]
                tri_indices = []
                for tv_idx, tvt_idx, tvn_idx in tri:
                    if tv_idx < 0 or tv_idx >= len(raw_v):
                        continue

                    key = (tv_idx, tvt_idx, tvn_idx)
                    if key not in unique_verts:
                        vx, vy, vz = raw_v[tv_idx]
                        cr, cg, cb = raw_vc[tv_idx]
                        
                        nx, ny, nz = 0.0, 1.0, 0.0
                        if tvn_idx != -1 and 0 <= tvn_idx < len(raw_vn):
                            nx, ny, nz = raw_vn[tvn_idx]
                            
                        tu, tv = 0.0, 0.0
                        if tvt_idx != -1 and 0 <= tvt_idx < len(raw_vt):
                            tu, tv = raw_vt[tvt_idx]
                            
                        # Roblox Version 2.00 flips V coordinate: (1.0f - tv)
                        tv = 1.0 - tv

                        # vertex: 9 floats (px, py, pz, nx, ny, nz, tu, tv, tw)
                        # tw is standard padding/tangent W -> 0.0
                        vert_data = (vx, vy, vz, nx, ny, nz, tu, tv, 0.0)
                        vert_color = (cr, cg, cb, 255) # Alpha opaque
                        
                        vert_idx = len(vertices_out)
                        vertices_out.append(vert_data)
                        colors_out.append(vert_color)
                        unique_verts[key] = vert_idx

                    tri_indices.append(unique_verts[key])
                
                if len(tri_indices) == 3:
                    indices_out.append(tuple(tri_indices))

    return vertices_out, colors_out, indices_out


def export_v2_mesh(vertices: list[tuple[float, ...]], colors: list[tuple[int, int, int, int]], indices: list[tuple[int, int, int]]) -> bytes:
    """Export to Roblox V2.00 Mesh binary format."""
    has_colors = len(colors) == len(vertices) and any(c != (255, 255, 255, 255) for c in colors)
    # Actually, the user wants Vertex Color support "same as it currently does in C++ source"
    # In C++, it was: rbxMesh.hasColors = obj.HasVertexColors && (ver == "2.00")
    # For safety natively enabled if we have them. Let's just always enable them or check if ANY vertex color is non-white
    # Or just always export them if they're present since Version 2.00 supports it. 
    # Let's unconditionally use them to match "with Vertex Color support"
    has_colors = True

    header_size = 12
    vertex_size = 40 if has_colors else 36
    face_size = 12
    vertex_count = len(vertices)
    face_count = len(indices)

    # struct header {
    #     uint16_t headerSize; // 2
    #     uint8_t vertexSize; // 1
    #     uint8_t faceSize; // 1
    #     uint32_t vertexCount; // 4
    #     uint32_t faceCount; // 4
    # }
    header_data = struct.pack('<HBBII', header_size, vertex_size, face_size, vertex_count, face_count)

    buf = bytearray()
    buf.extend(b'version 2.00\n')
    buf.extend(header_data)

    for i in range(vertex_count):
        # 9 floats
        buf.extend(struct.pack('<9f', *vertices[i]))
        if has_colors:
            # 4 uint8_t
            buf.extend(struct.pack('<4B', *colors[i]))

    for idx_tuple in indices:
        # 3 uint32_t
        buf.extend(struct.pack('<3I', *idx_tuple))

    return bytes(buf)


def convert_obj_to_mesh(obj_path: Path, output_mesh_path: Path) -> None:
    """Read an OBJ file and export a V2.00 Mesh file."""
    obj_content = obj_path.read_text(encoding='utf-8', errors='replace')
    verts, colors, indices = parse_obj_for_mesh(obj_content)
    mesh_bytes = export_v2_mesh(verts, colors, indices)
    output_mesh_path.write_bytes(mesh_bytes)


def get_or_create_mesh_from_obj(obj_path: str | Path) -> Path:
    """Convert an OBJ to a mesh, caching it dynamically.
    
    Returns the Path to the converted .mesh file.
    """
    obj_p = Path(obj_path).resolve()
    
    if not obj_p.exists():
        raise FileNotFoundError(f"OBJ file not found: {obj_path}")

    CONVERTED_MESHES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Hash the original path so we get a consistent temp cache name
    path_hash = md5(str(obj_p).encode('utf-8')).hexdigest()
    mesh_filename = f"{obj_p.stem}_{path_hash}.mesh"
    cached_mesh_p = CONVERTED_MESHES_DIR / mesh_filename

    generate = False
    if not cached_mesh_p.exists():
        generate = True
    else:
        # Check if OBJ was updated after our cached mesh
        obj_mtime = obj_p.stat().st_mtime
        mesh_mtime = cached_mesh_p.stat().st_mtime
        if obj_mtime > mesh_mtime:
            generate = True

    if generate:
        log.info('Converting OBJ to Mesh (V2.00): %s -> %s', obj_p, cached_mesh_p)
        convert_obj_to_mesh(obj_p, cached_mesh_p)
    else:
        log.debug('Using cached converted mesh: %s', cached_mesh_p)

    return cached_mesh_p
