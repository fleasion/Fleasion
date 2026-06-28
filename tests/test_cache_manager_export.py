from Fleasion.cache import cache_manager as cache_manager_module


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


def test_image_typed_audio_payload_is_displayed_as_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(cache_manager_module, "CONFIG_DIR", tmp_path)
    manager = cache_manager_module.CacheManager(config_manager=_Config())

    assert manager.store_asset("101", 1, AUDIO_PAYLOAD)

    assert manager.get_type_name_for_asset("101", 1) == "Audio"
    info = manager.get_asset_info("101", 1)
    assert info["detected_type"] == "Audio"
    assert info["type_name"] == "Audio"
    assert manager._detect_extension(AUDIO_PAYLOAD, 3) == ".ogg"
