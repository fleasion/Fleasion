import json

from Fleasion.proxy.addons.texture_stripper import TextureStripper, _decode_texpack_slot_quality


class _Config:
    def get_all_replacements(self):
        return {}, set(), {}, {}


def test_batch_request_ignores_dummy_id_replacements():
    stripper = TextureStripper(_Config())
    body = json.dumps([
        {"assetId": 100, "requestId": "a"},
        {"assetId": 101, "requestId": "b"},
        {"assetId": 102, "requestId": "c"},
    ]).encode()

    modified, scraper_body = stripper.process_batch_request(
        body,
        {},
        ({100: 0, 101: 1, 102: 999}, set(), {}, {}),
        "batch",
    )

    assert json.loads(modified) == [
        {"assetId": 100, "requestId": "a"},
        {"assetId": 101, "requestId": "b"},
        {"assetId": 999, "requestId": "c"},
    ]
    assert json.loads(scraper_body) == [
        {"assetId": 100, "requestId": "a"},
        {"assetId": 101, "requestId": "b"},
        {"assetId": 102, "requestId": "c"},
    ]


def _texpack_request(asset_id, request_id, crpl):
    return {
        "assetId": asset_id,
        "contentRepresentationPriorityList": crpl,
        "doNotFallbackToBaselineRepresentation": "true",
        "assetType": "TexturePack",
        "requestId": str(request_id),
    }


def test_texturepack_fidelity_decodes_slot_and_quality():
    assert _decode_texpack_slot_quality(_texpack_request(1, 0, "W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IkFFQT0ifV0=")) == (0, 1)
    assert _decode_texpack_slot_quality(_texpack_request(1, 0, "W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IklFQT0ifV0=")) == (1, 1)
    assert _decode_texpack_slot_quality(_texpack_request(1, 0, "W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IlFFQT0ifV0=")) == (2, 1)


def test_texturepack_partial_batch_uses_fidelity_before_occurrence_order():
    stripper = TextureStripper(_Config())
    requests = [
        _texpack_request(
            88088208586015,
            7,
            "W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IlFJQT0ifSx7ImZvcm1hdCI6Imt0eDIiLCJtYWpvclZlcnNpb24iOiI2cmRvIiwiZmlkZWxpdHkiOiJnZ0E9In1d",
        ),
        _texpack_request(
            88088208586015,
            8,
            "W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IklJQT0ifSx7ImZvcm1hdCI6Imt0eDIiLCJtYWpvclZlcnNpb24iOiI2cmRvIiwiZmlkZWxpdHkiOiJnUUE9In1d",
        ),
    ]

    assert stripper._build_texpack_request_slot_map(requests, {88088208586015}) == {
        0: 2,
        1: 1,
    }
