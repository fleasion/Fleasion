import json
import struct
from typing import Literal, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from fleasion.cache import mesh_rig

type _Vec2 = tuple[float, float]
type _Vec3 = tuple[float, float, float]
type _Mat3 = tuple[float, float, float, float, float, float, float, float, float]
type _JsonObject = dict[str, object]
type _AccessorDType = Literal['<u2', '<f4']
type _AccessorArray = NDArray[np.uint16] | NDArray[np.float32]


def _vertex(
    position: _Vec3,
    uv: _Vec2 = (0.0, 0.0),
    color: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> bytes:
    return struct.pack(
        '<8f4b4B',
        *position,
        0.0,
        0.0,
        1.0,
        *uv,
        0,
        0,
        0,
        0,
        *color,
    )


def _bone(
    name_offset: int,
    parent: int,
    position: _Vec3,
    rotation: _Mat3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
) -> bytes:
    transform = (*rotation, *position)
    return struct.pack('<IHHf12f', name_offset, parent, parent, 1.0, *transform)


def _rigged_v4(*, invalid_subset_index: bool = False, rotated: bool = False) -> bytes:
    names = b'Root\0Child\0'
    header = struct.pack('<HHIIHHIHBB', 24, 0, 3, 1, 2, 2, len(names), 1, 1, 0)
    vertices = b''.join(
        (
            _vertex((0.0, 0.0, 0.0), color=(255, 0, 0, 255)),
            _vertex((1.0, 0.0, 0.0), uv=(1.0, 0.0), color=(0, 255, 0, 255)),
            _vertex((0.0, 1.0, 0.0), uv=(0.0, 1.0), color=(0, 0, 255, 255)),
        )
    )
    envelopes = b''.join(
        (
            struct.pack('<8B', 0, 0, 0, 0, 255, 0, 0, 0),
            struct.pack('<8B', 1, 0, 0, 0, 255, 0, 0, 0),
            struct.pack('<8B', 0, 1, 0, 0, 128, 127, 0, 0),
        )
    )
    faces = struct.pack('<III', 0, 1, 2)
    lods = struct.pack('<II', 0, 1)
    rotation = (0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0) if rotated else None
    if rotation is None:
        bones = _bone(0, 0xFFFF, (0.0, 0.0, 0.0)) + _bone(5, 0, (0.0, 1.0, 0.0))
    else:
        bones = _bone(0, 0xFFFF, (0.0, 0.0, 0.0), rotation) + _bone(5, 0, (1.0, 0.0, 0.0), rotation)
    first_mapping = 2 if invalid_subset_index else 1
    subset = struct.pack(
        '<5I26H',
        0,
        1,
        0,
        3,
        2,
        first_mapping,
        0,
        *([0xFFFF] * 24),
    )
    return b'version 4.01\n' + header + vertices + envelopes + faces + lods + bones + names + subset


def _chunk(name: str, version: int, payload: bytes) -> bytes:
    return (
        name.encode('ascii').ljust(8, b'\0') + struct.pack('<II', version, len(payload)) + payload
    )


def _rigged_v6() -> bytes:
    fixed_layout = _rigged_v4()[13 + 24 :]
    vertices = fixed_layout[: 3 * 40]
    envelopes = fixed_layout[3 * 40 : 3 * 40 + 3 * 8]
    faces = fixed_layout[3 * 40 + 3 * 8 : 3 * 40 + 3 * 8 + 12]
    bones_offset = 3 * 40 + 3 * 8 + 12 + 2 * 4
    bones = fixed_layout[bones_offset : bones_offset + 2 * 60]
    names = b'Root\0Child\0'
    subset = fixed_layout[bones_offset + 2 * 60 + len(names) :]
    coremesh = struct.pack('<I', 3) + vertices + struct.pack('<I', 1) + faces
    skinning = (
        struct.pack('<I', 3)
        + envelopes
        + struct.pack('<I', 2)
        + bones
        + struct.pack('<I', len(names))
        + names
        + struct.pack('<I', 1)
        + subset
    )
    lods = struct.pack('<HBI2I', 0, 1, 2, 0, 1)
    return (
        b'version 6.00\n'
        + _chunk('COREMESH', 1, coremesh)
        + _chunk('LODS', 1, lods)
        + _chunk('SKINNING', 1, skinning)
    )


def _json_object(value: object) -> _JsonObject:
    assert isinstance(value, dict)
    mapping = cast('dict[object, object]', value)
    assert all(isinstance(key, str) for key in mapping)
    return cast('_JsonObject', mapping)


def _json_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast('list[object]', value)


def _json_object_list(value: object) -> list[_JsonObject]:
    return [_json_object(item) for item in _json_list(value)]


def _json_number_list(value: object) -> list[int | float]:
    items = _json_list(value)
    assert all(isinstance(item, int | float) for item in items)
    return cast('list[int | float]', items)


def _json_int(value: object) -> int:
    assert isinstance(value, int)
    return value


def _glb_document_and_binary(glb: bytes) -> tuple[_JsonObject, bytes]:
    magic, version, total_length = struct.unpack_from('<4sII', glb, 0)
    assert (magic, version, total_length) == (b'glTF', 2, len(glb))
    json_length, json_type = struct.unpack_from('<I4s', glb, 12)
    assert json_type == b'JSON'
    document = _json_object(json.loads(glb[20 : 20 + json_length]))
    binary_header = 20 + json_length
    binary_length, binary_type = struct.unpack_from('<I4s', glb, binary_header)
    assert binary_type == b'BIN\0'
    binary = glb[binary_header + 8 : binary_header + 8 + binary_length]
    return document, binary


def _accessor_array(
    document: _JsonObject,
    binary: bytes,
    accessor_index: int,
    dtype: _AccessorDType,
    width: int,
) -> _AccessorArray:
    accessors = _json_list(document['accessors'])
    accessor = _json_object(accessors[accessor_index])
    buffer_views = _json_list(document['bufferViews'])
    view = _json_object(buffer_views[_json_int(accessor['bufferView'])])
    count = _json_int(accessor['count'])
    view_offset = _json_int(view.get('byteOffset', 0))
    accessor_offset = _json_int(accessor.get('byteOffset', 0))
    if dtype == '<u2':
        values = np.frombuffer(
            binary,
            dtype=np.dtype('<u2'),
            count=count * width,
            offset=view_offset + accessor_offset,
        )
    else:
        values = np.frombuffer(
            binary,
            dtype=np.dtype('<f4'),
            count=count * width,
            offset=view_offset + accessor_offset,
        )
    return values.reshape(count, width)


def test_v4_subset_indices_are_remapped_to_global_bones() -> None:
    rig = mesh_rig.parse_rigged_mesh(_rigged_v4())

    assert rig is not None
    assert rig.version == 'version 4.01'
    assert [bone.name for bone in rig.bones] == ['Root', 'Child']
    assert [bone.parent for bone in rig.bones] == [None, 0]
    assert rig.bones[1].world_matrix[7] == 1.0
    assert rig.vertices[0].joints == (1, 0, 0, 0)
    assert rig.vertices[1].joints == (0, 0, 0, 0)
    assert rig.vertices[2].joints[:2] == (1, 0)
    assert rig.vertices[2].weights[:2] == pytest.approx((128 / 255, 127 / 255))


def test_v6_skinning_chunk_uses_the_same_validated_rig_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mesh_rig.mesh_processing, 'DRACO_AVAILABLE', False)

    rig = mesh_rig.parse_rigged_mesh(_rigged_v6())

    assert rig is not None
    assert rig.version == 'version 6.00'
    assert [bone.name for bone in rig.bones] == ['Root', 'Child']
    assert rig.vertices[0].joints == (1, 0, 0, 0)
    assert mesh_rig.has_embedded_rig(_rigged_v6())


def test_glb_contains_geometry_skin_hierarchy_and_inverse_bind_matrices() -> None:
    document, binary = _glb_document_and_binary(mesh_rig.export_glb(_rigged_v4()))
    asset = _json_object(document['asset'])
    skins = _json_object_list(document['skins'])
    nodes = _json_object_list(document['nodes'])
    meshes = _json_object_list(document['meshes'])

    assert asset['version'] == '2.0'
    assert skins[0]['joints'] == [0, 1]
    assert skins[0]['skeleton'] == 0
    assert nodes[0]['name'] == 'Root'
    assert nodes[0]['children'] == [1]
    assert nodes[1]['name'] == 'Child'
    assert nodes[2]['mesh'] == 0
    assert nodes[2]['skin'] == 0

    primitives = _json_object_list(meshes[0]['primitives'])
    attributes = _json_object(primitives[0]['attributes'])
    joints = _accessor_array(document, binary, _json_int(attributes['JOINTS_0']), '<u2', 4)
    weights = _accessor_array(document, binary, _json_int(attributes['WEIGHTS_0']), '<f4', 4)
    inverse_binds = _accessor_array(
        document,
        binary,
        _json_int(skins[0]['inverseBindMatrices']),
        '<f4',
        16,
    )
    assert tuple(joints[0]) == (1, 0, 0, 0)
    assert tuple(joints[1]) == (0, 0, 0, 0)
    assert weights.sum(axis=1) == pytest.approx((1.0, 1.0, 1.0))
    assert inverse_binds[1][13] == pytest.approx(-1.0)


def test_glb_converts_world_space_bone_frames_to_parent_local_matrices() -> None:
    document, _binary = _glb_document_and_binary(mesh_rig.export_glb(_rigged_v4(rotated=True)))
    nodes = _json_object_list(document['nodes'])

    child_matrix = _json_number_list(nodes[1]['matrix'])
    assert child_matrix[:12] == pytest.approx(
        (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    )
    assert child_matrix[12:15] == pytest.approx((0.0, -1.0, 0.0))


def test_invalid_subset_bone_mapping_is_rejected() -> None:
    with pytest.raises(mesh_rig.MeshRigError, match='invalid bone'):
        mesh_rig.parse_rigged_mesh(_rigged_v4(invalid_subset_index=True))


def test_static_mesh_does_not_report_a_rig() -> None:
    assert not mesh_rig.has_embedded_rig(b'version 2.00\n')
    assert mesh_rig.parse_rigged_mesh(b'version 2.00\n') is None
