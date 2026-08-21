import json
from unittest.mock import patch

from fleasion.proxy.addons.texture_stripper import TextureStripper, _decode_texpack_slot_quality


class _Config:
    def get_all_replacements(self):
        return {}, set(), {}, {}


def setup_function():
    TextureStripper.reset_routes()


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


def test_exact_id_replacement_takes_priority_over_type_removal():
    stripper = TextureStripper(_Config())
    body = json.dumps([
        {"assetId": 1234, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "a"},
        {"assetId": 5678, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "b"},
    ]).encode()

    modified, scraper_body = stripper.process_batch_request(
        body,
        {},
        ({1234: 999}, {63}, {}, {}),
        "batch",
    )

    assert json.loads(modified) == [
        {"assetId": 999, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "a"},
    ]
    assert json.loads(scraper_body) == [
        {"assetId": 1234, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "a"},
    ]


def test_cdn_replacement_takes_priority_over_type_removal(monkeypatch):
    stripper = TextureStripper(_Config())
    routed = []
    monkeypatch.setattr(
        stripper,
        '_route_cdn',
        lambda *args, **kwargs: routed.append((args, kwargs)),
    )
    body = json.dumps([
        {"assetId": 1234, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "a"},
    ]).encode()

    modified, _ = stripper.process_batch_request(
        body,
        {},
        ({}, {63}, {1234: "https://example.com/custom.png"}, {}),
        "batch",
    )

    assert json.loads(modified)[0]["assetId"] == 1234
    assert routed and routed[0][0][2] == "https://example.com/custom.png"
    assert routed[0][1]["map_index"] is None


def test_local_replacement_takes_priority_over_type_removal(monkeypatch, tmp_path):
    stripper = TextureStripper(_Config())
    routed = []
    monkeypatch.setattr(stripper, '_route_local', lambda *args, **kwargs: routed.append((args, kwargs)))
    replacement = tmp_path / "custom.png"
    replacement.write_bytes(b"png")
    body = json.dumps([
        {"assetId": 1234, "assetType": "TexturePack", "assetTypeId": 63, "requestId": "a"},
    ]).encode()

    modified, _ = stripper.process_batch_request(
        body,
        {},
        ({}, {63}, {}, {1234: str(replacement)}),
        "batch",
    )

    assert json.loads(modified)[0]["assetId"] == 1234
    assert routed and routed[0][0][2] == str(replacement)


def test_whole_texturepack_id_replacement_swaps_parent_without_downloading_xml():
    class _Scraper:
        def _fetch_asset_with_place_id_retry(self, *args, **kwargs):
            raise AssertionError('whole TexturePack must not be downloaded as a slot image')

    stripper = TextureStripper(_Config())
    stripper.set_cache_scraper(_Scraper())
    body = json.dumps([
        _texpack_request(1234, 'color', 'color-fidelity'),
        _texpack_request(1234, 'normal', 'normal-fidelity'),
        _texpack_request(1234, 'orm', 'orm-fidelity'),
    ]).encode()

    modified, scraper_body = stripper.process_batch_request(
        body,
        {},
        ({'TexturePack': 9999}, set(), {}, {}),
        'batch',
    )

    assert [entry['assetId'] for entry in json.loads(modified)] == [9999, 9999, 9999]
    assert [entry['assetId'] for entry in json.loads(scraper_body)] == [1234, 1234, 1234]


def test_predownloaded_texturepack_xml_is_not_served_as_slot_content(tmp_path):
    stripper = TextureStripper(_Config())
    manifest = tmp_path / 'replacement.dat'
    manifest.write_bytes(
        b'<roblox><texturepack_version>2</texturepack_version>'
        b'<color>10</color><normal>11</normal></roblox>'
    )
    stripper._predownloaded = {9999: str(manifest)}
    body = json.dumps([
        _texpack_request(1234, 'color', 'color-fidelity'),
        _texpack_request(1234, 'normal', 'normal-fidelity'),
        _texpack_request(1234, 'orm', 'orm-fidelity'),
    ]).encode()

    modified, _ = stripper.process_batch_request(
        body,
        {},
        ({'TexturePack': 9999}, set(), {}, {}),
        'batch',
    )

    assert [entry['assetId'] for entry in json.loads(modified)] == [9999, 9999, 9999]
    assert not stripper._local_redirects


def test_exact_local_texturepack_rule_overrides_type_id_rule(monkeypatch, tmp_path):
    stripper = TextureStripper(_Config())
    routed = []
    monkeypatch.setattr(stripper, '_route_local', lambda *args, **kwargs: routed.append((args, kwargs)))
    fish = tmp_path / 'fish.png'
    fish.write_bytes(b'png')
    body = json.dumps([
        _texpack_request(14108663921, 'carpet-color', 'color-fidelity'),
        _texpack_request(2222, 'wall-color', 'color-fidelity'),
    ]).encode()

    modified, scraper_body = stripper.process_batch_request(
        body,
        {},
        ({'TexturePack': 7547162198}, set(), {}, {14108663921: str(fish)}),
        'batch',
    )

    entries = json.loads(modified)
    assert entries[0]['assetId'] == 14108663921
    assert entries[1]['assetId'] == 7547162198
    assert len(routed) == 1
    assert routed[0][0][1:3] == (14108663921, str(fish))
    assert [entry['assetId'] for entry in json.loads(scraper_body)] == [
        14108663921,
        2222,
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


def test_animation_replacement_rig_detection_strips_bin_metadata(tmp_path):
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"RBXH amazon metadata" + b"<roblox!binary animation")
    stripper = TextureStripper(_Config())

    with patch("fleasion.utils.anim_converter.detect_rig", return_value="R15") as detect_rig:
        assert stripper._detect_repl_rig(str(replacement)) == "R15"

    detect_rig.assert_called_once_with(b'<roblox!binary animation')


def test_disabling_config_invalidates_queued_local_route(tmp_path):
    class _MutableConfig:
        replacements_generation = 0

        def __init__(self):
            self.replacements = ({}, set(), {}, {1234: str(replacement)})

        def get_all_replacements(self):
            return self.replacements

    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'animation')
    config = _MutableConfig()
    stripper = TextureStripper(config)
    body = json.dumps(
        [{'assetId': 1234, 'assetType': 'Image', 'assetTypeId': 1, 'requestId': 'a'}]
    ).encode()

    stripper.process_batch_request(body, {}, config.replacements, 'old-batch')
    assert stripper.has_pending()

    config.replacements = ({}, set(), {}, {})
    config.replacements_generation += 1

    assert not stripper.has_pending()
    assert stripper.check_cdn_request('fts.rbxcdn.com', '/old-content') is None


def test_reset_rejects_response_from_old_batch(tmp_path):
    class _MutableConfig:
        replacements_generation = 0

        def get_all_replacements(self):
            return {}, set(), {}, {1234: str(replacement)}

    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'animation')
    config = _MutableConfig()
    stripper = TextureStripper(config)
    body = json.dumps([{'assetId': 1234, 'requestId': 'a'}]).encode()
    response = json.dumps(
        [{'requestId': 'a', 'location': 'https://fts.rbxcdn.com/stale-content'}]
    ).encode()

    stripper.process_batch_request(body, {}, config.get_all_replacements(), 'old-batch')
    assert stripper.has_pending()

    TextureStripper.reset_routes('test cache clear')
    stripper.process_batch_response(body, response, {}, 'old-batch')

    assert not stripper.has_pending()
    assert stripper.check_cdn_request('fts.rbxcdn.com', '/stale-content') is None


def test_replacement_precheck_stops_and_backs_off_after_network_failure(
    tmp_path, monkeypatch
):
    class _ReplacementConfig:
        def get_all_replacements(self):
            return {100: 900001, 101: 900002}, set(), {}, {}

    class _OfflineScraper:
        def __init__(self):
            self.calls = []

        def _get_roblosecurity(self, wait=False):
            return None

        def _https_get(self, hostname, path, **_kwargs):
            self.calls.append((hostname, path))
            return None, None

    scraper = _OfflineScraper()
    stripper = TextureStripper(_ReplacementConfig())
    stripper._PREDOWNLOAD_DIR = tmp_path / 'predownloaded'
    stripper.set_cache_scraper(scraper)
    TextureStripper._precheck_pending.difference_update({900001, 900002})
    now = [100.0]
    monkeypatch.setattr(
        'fleasion.proxy.addons.texture_stripper.time.monotonic',
        lambda: now[0],
    )

    stripper.precheck_replacements()
    stripper.precheck_replacements()

    assert scraper.calls == [
        ('assetdelivery.roblox.com', '/v1/asset/?id=900001'),
    ]
    assert not ({900001, 900002} & TextureStripper._precheck_pending)
    assert set(stripper._precheck_retry_after) == {900001, 900002}
    assert stripper._precheck_network_failure_count == 1
    assert set(stripper._precheck_retry_after.values()) == {220.0}

    now[0] = 221.0
    stripper.precheck_replacements()

    assert len(scraper.calls) == 2
    assert stripper._precheck_network_failure_count == 2
    assert set(stripper._precheck_retry_after.values()) == {461.0}


def test_convert_texpack_local_uses_slot_specific_mipmap_modes(monkeypatch, tmp_path):
    from fleasion.cache.tools.image_to_ktx2 import converter as image_converter

    source = tmp_path / 'replacement.png'
    source.write_bytes(b'not-decoded-by-this-test')
    seen_modes = []

    def fake_convert(path, *, mipmap_mode):
        seen_modes.append(mipmap_mode)
        return path

    monkeypatch.setattr(image_converter, 'get_or_create_ktx2_from_image', fake_convert)
    monkeypatch.setattr(
        TextureStripper,
        '_normalize_rgba8_ktx2',
        staticmethod(lambda path, *, mipmap_mode: path),
    )

    for map_index, expected_mode in ((0, 'color'), (1, 'normal'), (2, 'linear')):
        assert TextureStripper._convert_texpack_local(str(source), map_index=map_index) == str(
            source
        )
        assert seen_modes[-1] == expected_mode


def test_rgba8_normalization_preserves_authored_mip_chain(monkeypatch, tmp_path):
    from fleasion.cache.tools.rgba_ktx2 import (
        read_rgba8_ktx2_levels,
        write_rgba8_ktx2_levels,
    )
    from fleasion.proxy.addons import texture_stripper as texture_stripper_module

    monkeypatch.setattr(texture_stripper_module, 'APP_CACHE_DIR', tmp_path / 'cache')
    source = tmp_path / 'authored.ktx2'
    base = bytes(range(16))
    tail = bytes((9, 8, 7, 6))
    write_rgba8_ktx2_levels([base, tail], 2, 2, source)

    normalized = TextureStripper._normalize_rgba8_ktx2(source, mipmap_mode='color')

    assert normalized == source
    assert read_rgba8_ktx2_levels(normalized.read_bytes()) == ([base, tail], 2, 2)


def test_rgba8_normalization_generates_missing_mips(monkeypatch, tmp_path):
    from fleasion.cache.tools.rgba_ktx2 import (
        read_rgba8_ktx2_levels,
        write_rgba8_ktx2_levels,
    )
    from fleasion.proxy.addons import texture_stripper as texture_stripper_module

    monkeypatch.setattr(texture_stripper_module, 'APP_CACHE_DIR', tmp_path / 'cache')
    source = tmp_path / 'single-level.ktx2'
    base = bytes(range(16))
    write_rgba8_ktx2_levels([base], 2, 2, source)

    normalized = TextureStripper._normalize_rgba8_ktx2(source, mipmap_mode='linear')
    parsed = read_rgba8_ktx2_levels(normalized.read_bytes())

    assert parsed is not None
    levels, width, height = parsed
    assert (width, height) == (2, 2)
    assert levels[0] == base
    assert len(levels) == 2


def test_orm_composite_resolves_normal_asset_id_for_variance_mips(monkeypatch, tmp_path):
    from fleasion.cache.tools import orm_compositor
    from fleasion.proxy.addons import texture_stripper as texture_stripper_module

    cache_root = tmp_path / 'cache'
    monkeypatch.setattr(texture_stripper_module, 'APP_CACHE_DIR', cache_root)

    slot_dir = tmp_path / 'slots'
    slot_dir.mkdir()
    orm_baseline = slot_dir / '1234_slot2.ktx2'
    normal_baseline = slot_dir / '1234_slot1.ktx2'
    orm_baseline.write_bytes(b'orm-baseline')
    normal_baseline.write_bytes(b'normal-baseline')

    class _CacheManager:
        @staticmethod
        def get_texturepack_slot_path(parent_id, slot):
            assert parent_id == 1234
            return slot_dir / f'{parent_id}_slot{slot}.ktx2'

    class _Scraper:
        cache_manager = _CacheManager()

        @staticmethod
        def _get_roblosecurity():
            return 'cookie'

        @staticmethod
        def _fetch_asset_with_place_id_retry(asset_id, extra_headers=None):
            assert asset_id == '98765'
            assert extra_headers == {'Cookie': '.ROBLOSECURITY=cookie;'}
            return b'normal-asset-bytes', 200

    seen = {}

    def fake_composite(baseline, channels, cache_dir, *, normal_source, normal_baseline):
        seen.update(
            baseline=baseline,
            channels=channels,
            cache_dir=cache_dir,
            normal_source=normal_source,
            normal_baseline=normal_baseline,
        )
        return str(tmp_path / 'composite.ktx2')

    monkeypatch.setattr(orm_compositor, 'composite_orm', fake_composite)

    roughness = tmp_path / 'roughness.png'
    roughness.write_bytes(b'roughness')
    stripper = TextureStripper(_Config())
    stripper.set_cache_scraper(_Scraper())

    result = stripper._build_orm_composite(
        1234,
        {'roughness': str(roughness)},
        normal_source=98765,
    )

    downloaded = cache_root / 'predownloaded' / '98765.dat'
    assert result == str(tmp_path / 'composite.ktx2')
    assert downloaded.read_bytes() == b'normal-asset-bytes'
    assert seen['baseline'] == orm_baseline
    assert seen['normal_source'] == downloaded
    assert seen['normal_baseline'] == normal_baseline
