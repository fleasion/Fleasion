from Fleasion.cache import cache_manager as cache_manager_module


MODEL_XML = b"""<roblox version="4">
<Item class="Folder" referent="RBX0">
  <Properties>
    <string name="Name">Folder</string>
  </Properties>
</Item>
</roblox>"""


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
