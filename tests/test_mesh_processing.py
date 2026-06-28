import gzip

from Fleasion.cache import mesh_processing


EARLY_MESH = (
    b"version 1.00\n"
    b"1\n"
    b"[0,0,0][0,1,0][0,0,0]"
    b"[1,0,0][0,1,0][1,0,0]"
    b"[0,1,0][0,1,0][0,1,0]"
)


def test_mesh_data_detection_accepts_early_meshes_and_gzip_wrappers():
    assert mesh_processing.is_mesh_data(EARLY_MESH)
    assert mesh_processing.is_mesh_data(gzip.compress(EARLY_MESH))


def test_early_mesh_converts_to_obj():
    obj = mesh_processing.convert(EARLY_MESH)

    assert obj is not None
    assert "v 0.0 0.0 0.0" in obj
    assert "v 0.5 0.0 0.0" in obj
    assert "f 1/1/1 2/2/2 3/3/3" in obj
