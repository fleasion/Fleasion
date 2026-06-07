import unittest

from Fleasion.cache.roblox_document import (
    RBXM_MAGIC,
    classify_roblox_document,
    export_roblox_document,
    get_default_roblox_document_export_format,
    get_roblox_document_export_formats,
)


MODEL_XML = b"""<roblox version="4">
<Item class="Folder" referent="RBX0">
  <Properties>
    <string name="Name">Folder</string>
  </Properties>
</Item>
</roblox>"""

PLACE_XML = b"""<roblox version="4">
<Item class="DataModel" referent="RBX0">
  <Properties>
    <string name="Name">Game</string>
  </Properties>
</Item>
</roblox>"""

DECLARED_MODEL_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<roblox version="4">
<Item class="Folder" referent="RBX0" />
</roblox>"""


class RobloxDocumentTests(unittest.TestCase):
    def test_model_xml_exports_as_rbxm_or_rbxmx(self):
        self.assertEqual(classify_roblox_document(MODEL_XML), "rbxmx")
        self.assertEqual(get_default_roblox_document_export_format(MODEL_XML), "converted_document_rbxmx")
        self.assertEqual(
            get_roblox_document_export_formats(MODEL_XML),
            ["converted_document_rbxm", "converted_document_rbxmx"],
        )

        rbxm_data, rbxm_ext = export_roblox_document(MODEL_XML, "converted_document_rbxm")
        self.assertEqual(rbxm_ext, ".rbxm")
        self.assertTrue(rbxm_data.startswith(RBXM_MAGIC))
        self.assertEqual(classify_roblox_document(rbxm_data), "rbxm")
        self.assertEqual(get_default_roblox_document_export_format(rbxm_data), "converted_document_rbxm")

        rbxmx_data, rbxmx_ext = export_roblox_document(rbxm_data, "converted_document_rbxmx")
        self.assertEqual(rbxmx_ext, ".rbxmx")
        self.assertIn(b'<Item class="Folder"', rbxmx_data)

    def test_xml_declaration_does_not_block_document_detection(self):
        self.assertEqual(classify_roblox_document(DECLARED_MODEL_XML), "rbxmx")
        export_data, export_ext = export_roblox_document(
            DECLARED_MODEL_XML,
            "converted_document_rbxmx",
        )
        self.assertEqual(export_ext, ".rbxmx")
        self.assertTrue(export_data.startswith(b"<?xml"))

    def test_datamodel_document_exports_only_as_rbxl(self):
        self.assertEqual(classify_roblox_document(PLACE_XML), "rbxl")
        self.assertEqual(get_default_roblox_document_export_format(PLACE_XML), "converted_document_rbxl")
        self.assertEqual(
            get_roblox_document_export_formats(PLACE_XML),
            ["converted_document_rbxl"],
        )

        rbxl_data, rbxl_ext = export_roblox_document(PLACE_XML, "converted_document_rbxl")
        self.assertEqual(rbxl_ext, ".rbxl")
        self.assertTrue(rbxl_data.startswith(RBXM_MAGIC))
        self.assertEqual(classify_roblox_document(rbxl_data), "rbxl")

    def test_place_asset_type_exports_only_as_rbxl_even_with_model_payload(self):
        self.assertEqual(
            get_roblox_document_export_formats(MODEL_XML, asset_type=9),
            ["converted_document_rbxl"],
        )
        self.assertEqual(
            get_default_roblox_document_export_format(MODEL_XML, asset_type=9),
            "converted_document_rbxl",
        )

        rbxl_data, rbxl_ext = export_roblox_document(
            MODEL_XML,
            "converted_document_rbxl",
            asset_type=9,
        )
        self.assertEqual(rbxl_ext, ".rbxl")
        self.assertTrue(rbxl_data.startswith(RBXM_MAGIC))
        with self.assertRaises(ValueError):
            export_roblox_document(MODEL_XML, "converted_document_rbxm", asset_type=9)


if __name__ == "__main__":
    unittest.main()
