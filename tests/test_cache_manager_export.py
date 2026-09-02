import pytest

from fleasion.cache import cache_manager as cache_manager_module
from fleasion.cache import mesh_rig


MODEL_XML = b"""<roblox version="4">
<Item class="Folder" referent="RBX0">
  <Properties>
    <string name="Name">Folder</string>
  </Properties>
</Item>
</roblox>"""

MESH_PAYLOAD = (
    b"version 1.00\n"
    b"0\n"
    b"[0,0,0][0,1,0][0,0,0]"
    b"[1,0,0][0,1,0][1,0,0]"
    b"[0,1,0][0,1,0][0,1,0]"
)

AUDIO_PAYLOAD = b"OggS\x00\x02" + (b"\x00" * 32)

IMAGE_PAYLOADS = [
    pytest.param(b"\x89PNG\r\n\x1a\n" + (b"\x00" * 24), ".png", id="png"),
    pytest.param(b"\xff\xd8\xff\xe0" + (b"\x00" * 28), ".jpg", id="jpeg"),
    pytest.param(b"RIFF\x18\x00\x00\x00WEBP" + (b"\x00" * 20), ".webp", id="webp"),
    pytest.param(b"\xabKTX 11\xbb\r\n\x1a\n" + (b"\x00" * 20), ".ktx", id="ktx1"),
    pytest.param(b"\xabKTX 20\xbb\r\n\x1a\n" + (b"\x00" * 20), ".ktx", id="ktx2"),
]


class _Config:
    export_naming = ["id"]


def test_place_asset_formats_are_limited_to_rbxl(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    assert manager.store_asset("123", 9, MODEL_XML)
    formats = manager.get_available_export_formats_for_asset("123", 9)

    assert "converted_document_rbxl" in formats
    assert "converted_document_rbxm" not in formats
    assert "converted_document_rbxmx" not in formats

    exported = manager.export_asset("123", 9, export_format="converted_document_rbxl")
    assert exported is not None
    assert exported.suffix == ".rbxl"


def test_image_typed_mesh_payload_is_displayed_as_mesh(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    assert manager.store_asset("456", 1, MESH_PAYLOAD)

    assert manager.get_type_name_for_asset("456", 1) == "Mesh"
    info = manager.get_asset_info("456", 1)
    assert info["detected_type"] == "Mesh"
    assert info["type_name"] == "Mesh"

    formats = manager.get_available_export_formats_for_asset("456", 1)
    assert "converted_obj" in formats
    assert "converted_png" not in formats


def test_old_image_typed_mesh_payload_is_healed_lazily(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    assert manager.store_asset("789", 1, MESH_PAYLOAD)

    info = manager.get_asset_info("789", 1)
    info.pop("detected_type", None)
    info["type_name"] = "Image"

    assert manager.get_type_name_for_asset("789", 1) == "Mesh"
    assert info["detected_type"] == "Mesh"
    assert info["type_name"] == "Mesh"


def test_old_image_typed_mesh_payload_uses_only_a_header_for_type_detection(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    assert manager.store_asset("790", 1, MESH_PAYLOAD + (b"x" * 20_000))

    info = manager.get_asset_info("790", 1)
    info.pop("detected_type", None)
    info["type_name"] = "Image"

    def fail_full_asset_read(*_args, **_kwargs):
        raise AssertionError("type detection must not read the full payload")

    manager.get_asset = fail_full_asset_read

    assert manager.get_type_name_for_asset("790", 1) == "Mesh"
    assert info["detected_type"] == "Mesh"


def test_image_typed_audio_payload_is_displayed_as_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    assert manager.store_asset("101", 1, AUDIO_PAYLOAD)

    assert manager.get_type_name_for_asset("101", 1) == "Audio"
    info = manager.get_asset_info("101", 1)
    assert info["detected_type"] == "Audio"
    assert info["type_name"] == "Audio"
    assert manager._detect_extension(AUDIO_PAYLOAD, 3) == ".ogg"


@pytest.mark.parametrize(("payload", "expected_extension"), IMAGE_PAYLOADS)
def test_mesh_typed_image_payload_is_displayed_and_exported_as_image(
    tmp_path, monkeypatch, payload, expected_extension
):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    assert manager.store_asset("81504106", 4, payload)

    assert manager.get_type_name_for_asset("81504106", 4) == "Image"
    info = manager.get_asset_info("81504106", 4)
    assert info["detected_type"] == "Image"
    assert info["type_name"] == "Image"

    formats = manager.get_available_export_formats_for_asset("81504106", 4)
    assert "converted_png" in formats
    assert "converted_obj" not in formats
    assert manager._detect_extension(payload, 4) == expected_extension


def test_old_mesh_typed_image_payload_is_healed_lazily_from_header(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    payload = b"\x89PNG\r\n\x1a\n" + (b"x" * 20_000)
    assert manager.store_asset("81504106", 4, payload)

    info = manager.get_asset_info("81504106", 4)
    info.pop("detected_type", None)
    info["type_name"] = "Mesh"

    def fail_full_asset_read(*_args, **_kwargs):
        raise AssertionError("type detection must not read the full payload")

    manager.get_asset = fail_full_asset_read

    assert manager.get_type_name_for_asset("81504106", 4) == "Image"
    assert info["detected_type"] == "Image"
    assert info["type_name"] == "Image"


def test_clear_memory_cache_evicts_loaded_asset_payloads(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    assert manager.store_asset("102", 9, MODEL_XML)
    assert manager.get_asset("102", 9) == MODEL_XML
    assert manager._asset_cache

    assert manager.clear_memory_cache() == 1
    assert manager._asset_cache == {}


def test_rigged_glb_is_only_offered_for_payloads_with_embedded_skinning(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    assert manager.store_asset('rigged', 4, b'rigged mesh')
    assert manager.store_asset('static', 4, b'static mesh')
    monkeypatch.setattr(mesh_rig, 'has_embedded_rig', lambda data: data == b'rigged mesh')

    assert 'converted_rigged_glb' in manager.get_available_export_formats_for_asset('rigged', 4)
    assert 'converted_rigged_glb' not in manager.get_available_export_formats_for_asset('static', 4)


def test_rigged_glb_exports_as_a_separate_converted_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    assert manager.store_asset('123', 4, b'rigged mesh')
    monkeypatch.setattr(mesh_rig, 'export_glb', lambda _data: b'glTF rig data')

    exported = manager.export_asset('123', 4, export_format='converted_rigged_glb')

    assert exported is not None
    assert exported.suffix == '.glb'
    assert exported.read_bytes() == b'glTF rig data'


def test_texturepack_slot_paths_live_in_persistent_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    slot_path = manager.get_texturepack_slot_path('9920600052', 1)

    assert slot_path == tmp_path / 'FleasionNT' / 'Cache' / 'TexturePack' / 'slots' / '9920600052_slot1.ktx2'
    assert slot_path.parent.is_dir()


def test_deleting_texturepack_removes_persistent_raw_and_slot_archives(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())
    asset_id = '9920600052'

    assert manager.store_asset(asset_id, 63, b'<TexturePack/>')
    assert manager.store_raw_asset(asset_id, 63, b'raw-sidecar')
    canonical = manager.get_texturepack_slot_path(asset_id, 0)
    canonical.write_bytes(b'canonical')
    archived = manager.get_texturepack_slot_pack_path(
        asset_id,
        0,
        3,
        0,
        1024,
        1024,
        1,
        'a' * 64,
    )
    archived.write_bytes(b'archive')

    assert manager.delete_asset(asset_id, 63)
    assert not manager.get_raw_asset_path(asset_id, 63).exists()
    assert not canonical.exists()
    assert not archived.exists()
