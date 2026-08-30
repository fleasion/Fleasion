import gzip
import struct
from types import SimpleNamespace
from typing import Never

import pytest

from fleasion.cache import mesh_processing

EARLY_MESH = b'version 1.00\n1\n[0,0,0][0,1,0][0,0,0][1,0,0][0,1,0][1,0,0][0,1,0][0,1,0][0,1,0]'


def test_mesh_data_detection_accepts_early_meshes_and_gzip_wrappers() -> None:
    assert mesh_processing.is_mesh_data(EARLY_MESH)
    assert mesh_processing.is_mesh_data(gzip.compress(EARLY_MESH))


def test_early_mesh_converts_to_obj() -> None:
    obj = mesh_processing.convert(EARLY_MESH)

    assert obj is not None
    assert 'v 0.0 0.0 0.0' in obj
    assert 'v 0.5 0.0 0.0' in obj
    assert 'f 1/1/1 2/2/2 3/3/3' in obj


def _chunk(name: str, version: int, payload: bytes) -> bytes:
    return (
        name.encode('ascii').ljust(8, b'\0') + struct.pack('<II', version, len(payload)) + payload
    )


def _vertex(
    position: tuple[float, float, float],
    uv: tuple[float, float],
    color: tuple[int, int, int, int],
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


def _v6_mesh() -> bytes:
    vertices = b''.join(
        (
            _vertex((0.0, 0.0, 0.0), (0.0, 0.0), (255, 0, 0, 255)),
            _vertex((1.0, 0.0, 0.0), (1.0, 0.0), (0, 255, 0, 255)),
            _vertex((0.0, 1.0, 0.0), (0.0, 1.0), (0, 0, 255, 255)),
            _vertex((1.0, 1.0, 0.0), (1.0, 1.0), (255, 255, 255, 255)),
        )
    )
    coremesh = (
        struct.pack('<I', 4)
        + vertices
        + struct.pack('<I', 2)
        + struct.pack('<III', 0, 1, 2)
        + struct.pack('<III', 1, 3, 2)
    )
    lods = struct.pack('<HBI3I', 0, 1, 3, 0, 1, 2)
    unknown_v2_chunk = _chunk('UNKNOWN', 2, b'abcd')
    return (
        b'version 6.00\n'
        + unknown_v2_chunk
        + _chunk('COREMESH', 1, coremesh)
        + _chunk('LODS', 1, lods)
    )


def test_v6_raw_coremesh_converts_without_draco(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mesh_processing, 'DRACO_AVAILABLE', False)

    obj = mesh_processing.convert(_v6_mesh())

    assert obj is not None
    assert '# Vertices: 4, Faces: 1' in obj
    assert 'v 0.000000 0.000000 0.000000 1.000000 0.000000 0.000000' in obj
    assert 'vn 0.000000 0.000000 1.000000' in obj
    assert 'vt 0.000000 1.000000 0.0' in obj
    assert 'f 1/1/1 2/2/2 3/3/3' in obj
    assert 'f 2/2/2 4/4/4 3/3/3' not in obj


def test_chunked_mesh_rejects_a_chunk_that_exceeds_the_file() -> None:
    malformed = b'version 6.00\n' + b'COREMESH' + struct.pack('<II', 1, 100) + b'\0'

    assert mesh_processing.convert(malformed) is None


def test_v7_draco_length_is_part_of_coremesh_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    decoded: list[bytes] = []

    def decode(data: bytes) -> SimpleNamespace:
        decoded.append(data)
        return SimpleNamespace(
            points=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
            normals=None,
            tex_coord=None,
            faces=((0, 1, 2),),
        )

    monkeypatch.setattr(mesh_processing, 'DRACO_AVAILABLE', True)
    monkeypatch.setattr(mesh_processing, 'DracoPy', SimpleNamespace(decode=decode))
    bitstream = b'DRACO test payload'
    coremesh = struct.pack('<I', len(bitstream)) + bitstream
    data = b'version 7.00\n' + _chunk('COREMESH', 2, coremesh)

    obj = mesh_processing.convert(data)

    assert obj is not None
    assert decoded == [bitstream]
    assert '# Vertices: 3, Faces: 1' in obj


def test_v7_rejects_a_mismatched_draco_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mesh_processing, 'DRACO_AVAILABLE', True)

    def fail_decode(_data: bytes) -> Never:
        msg = 'decoded'
        raise AssertionError(msg)

    monkeypatch.setattr(
        mesh_processing,
        'DracoPy',
        SimpleNamespace(decode=fail_decode),
    )
    coremesh = struct.pack('<I', 100) + b'too short'
    data = b'version 7.00\n' + _chunk('COREMESH', 2, coremesh)

    assert mesh_processing.convert(data) is None
