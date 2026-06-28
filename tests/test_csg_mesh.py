import math
import struct

from Fleasion.cache.tools.solidmodel_converter.csg_mesh import (
    CSGVertex,
    parse_csg_mesh_full,
    serialize_csg_mesh,
    xor_buffer,
)


def _vertex(px, py, pz, nx=0.0, ny=1.0, nz=0.0):
    return CSGVertex(
        px=px,
        py=py,
        pz=pz,
        nx=nx,
        ny=ny,
        nz=nz,
        cr=255,
        cg=255,
        cb=255,
        ca=255,
        extra_r=1,
        extra_g=0,
        extra_b=0,
        extra_a=0,
        u=0.0,
        v=0.0,
        u_studs=0.0,
        v_studs=0.0,
        u_decal=0.0,
        v_decal=0.0,
        tx=0.0,
        ty=1.0,
        tz=0.0,
        ed0=0.0,
        ed1=0.0,
        ed2=0.0,
        ed3=0.0,
    )


def test_csgmdl_v5_quantized_vectors_roundtrip_with_offset_encoding():
    vertices = [
        _vertex(0.0, 0.0, 0.0, nx=1.0, ny=0.0, nz=0.0),
        _vertex(1.0, 0.0, 0.0, nx=0.0, ny=-1.0, nz=0.0),
        _vertex(0.0, 1.0, 0.0, nx=0.0, ny=0.0, nz=1.0),
    ]

    parsed = parse_csg_mesh_full(serialize_csg_mesh(vertices, [0, 1, 2], version=5))

    assert math.isclose(parsed.vertices[0].nx, 1.0)
    assert math.isclose(parsed.vertices[0].ny, 0.0, abs_tol=1e-6)
    assert math.isclose(parsed.vertices[1].ny, -1.0)
    assert math.isclose(parsed.vertices[2].nz, 1.0)
    assert math.isclose(parsed.vertices[0].tx, 0.0, abs_tol=1e-6)
    assert math.isclose(parsed.vertices[0].ty, 1.0)


def test_csgmdl_v4_counted_range_list_is_used_as_submesh_boundaries():
    vertices = [
        _vertex(0.0, 0.0, 0.0),
        _vertex(1.0, 0.0, 0.0),
        _vertex(0.0, 1.0, 0.0),
        _vertex(1.0, 1.0, 0.0),
    ]
    encrypted_v2 = serialize_csg_mesh(vertices, [0, 1, 2, 1, 3, 2], version=2)
    plain = bytearray(xor_buffer(encrypted_v2))
    plain[6:10] = struct.pack('<i', 4)
    plain.extend(struct.pack('<4I', 3, 0, 3, 6))

    parsed = parse_csg_mesh_full(xor_buffer(bytes(plain)))

    assert parsed.version == 4
    assert parsed.indices == [0, 1, 2, 1, 3, 2]
    assert parsed.submesh_boundaries == [0, 3, 6]
