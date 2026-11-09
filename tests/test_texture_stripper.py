from __future__ import annotations

import json
import tracemalloc
from typing import TYPE_CHECKING, Protocol, cast
from unittest.mock import patch

import pytest

from fleasion.proxy.addons import texture_stripper as texture_stripper_module
from fleasion.proxy.addons import cache_scraper as cache_scraper_module
from fleasion.proxy.addons.texture_stripper import TextureStripper

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from typing import Never

    from fleasion.config.manager import ConfigManager
    from fleasion.config.types import ReplacementMaps


type _JsonScalar = str | int | float | bool | None
type _JsonValue = _JsonScalar | list[_JsonValue] | dict[str, _JsonValue]
type _JsonObject = dict[str, _JsonValue]
type _RoutedCall = tuple[tuple[object, ...], dict[str, object]]


class _ConfigLike(Protocol):
    def get_all_replacements(self) -> ReplacementMaps: ...


class _DecodeTexpackSlotQuality(Protocol):
    def __call__(self, entry: _JsonObject) -> tuple[int, int] | None: ...


class _BuildTexpackRequestSlotMap(Protocol):
    def __call__(
        self,
        data: list[_JsonValue],
        slot_target_ids: set[int] | None = None,
    ) -> dict[int, int]: ...


class _DetectReplRig(Protocol):
    def __call__(self, local_path: str) -> str: ...


class _ConvertTexpackLocal(Protocol):
    def __call__(self, local_path: str, map_index: int | None = None) -> str: ...


class _NormalizeRgba8Ktx2(Protocol):
    def __call__(self, path: Path, *, mipmap_mode: str = 'color') -> Path: ...


class _BuildOrmComposite(Protocol):
    def __call__(
        self,
        parent_id: int | str,
        channel_pngs: dict[str, str | None],
        *,
        normal_source: str | int | None = None,
    ) -> str | None: ...


class _SetCacheScraper(Protocol):
    def __call__(self, scraper: object) -> None: ...


class _Config:
    @staticmethod
    def get_all_replacements() -> ReplacementMaps:
        return {}, set(), {}, {}


def _attr(target: object, name: str) -> object:
    return getattr(target, name)


def _set_attr(target: object, name: str, value: object) -> None:
    setattr(target, name, value)


def _make_stripper(config: _ConfigLike) -> TextureStripper:
    return TextureStripper(cast('ConfigManager', config))


def _process_batch_request(
    stripper: TextureStripper,
    body: bytes,
    req_headers: dict[str, str],
    replacements: ReplacementMaps,
    batch_id: str,
) -> tuple[bytes, bytes]:
    return cast(
        'tuple[bytes, bytes]',
        stripper.process_batch_request(body, req_headers, replacements, batch_id),
    )


def _set_cache_scraper(stripper: TextureStripper, scraper: object) -> None:
    setter = cast('_SetCacheScraper', _attr(stripper, 'set_cache_scraper'))
    setter(scraper)


def _decode_texpack_slot_quality(entry: _JsonObject) -> tuple[int, int] | None:
    decoder = cast(
        '_DecodeTexpackSlotQuality',
        _attr(texture_stripper_module, '_decode_texpack_slot_quality'),
    )
    return decoder(entry)


def _build_texpack_request_slot_map(
    stripper: TextureStripper,
    data: list[_JsonValue],
    slot_target_ids: set[int] | None = None,
) -> dict[int, int]:
    builder = cast(
        '_BuildTexpackRequestSlotMap',
        _attr(stripper, '_build_texpack_request_slot_map'),
    )
    return builder(data, slot_target_ids)


def _detect_repl_rig(stripper: TextureStripper, local_path: str) -> str:
    detector = cast('_DetectReplRig', _attr(stripper, '_detect_repl_rig'))
    return detector(local_path)


def _convert_texpack_local(local_path: str, *, map_index: int | None = None) -> str:
    converter = cast(
        '_ConvertTexpackLocal',
        _attr(TextureStripper, '_convert_texpack_local'),
    )
    return converter(local_path, map_index)


def _normalize_rgba8_ktx2(path: Path, *, mipmap_mode: str = 'color') -> Path:
    normalizer = cast(
        '_NormalizeRgba8Ktx2',
        _attr(TextureStripper, '_normalize_rgba8_ktx2'),
    )
    return normalizer(path, mipmap_mode=mipmap_mode)


def _build_orm_composite(
    stripper: TextureStripper,
    parent_id: int | str,
    channel_pngs: dict[str, str | None],
    *,
    normal_source: str | int | None = None,
) -> str | None:
    builder = cast('_BuildOrmComposite', _attr(stripper, '_build_orm_composite'))
    return builder(parent_id, channel_pngs, normal_source=normal_source)


def _local_redirects(stripper: TextureStripper) -> dict[str, str]:
    return cast('dict[str, str]', _attr(stripper, '_local_redirects'))


def _precheck_pending() -> set[int]:
    return cast('set[int]', _attr(TextureStripper, '_precheck_pending'))


def _precheck_retry_after(stripper: TextureStripper) -> dict[int, float]:
    return cast('dict[int, float]', _attr(stripper, '_precheck_retry_after'))


def _precheck_network_failure_count(stripper: TextureStripper) -> int:
    return cast('int', _attr(stripper, '_precheck_network_failure_count'))


def setup_function() -> None:
    TextureStripper.reset_routes()


def test_batch_request_ignores_dummy_id_replacements() -> None:
    stripper = _make_stripper(_Config())
    body = json.dumps(
        [
            {'assetId': 100, 'requestId': 'a'},
            {'assetId': 101, 'requestId': 'b'},
            {'assetId': 102, 'requestId': 'c'},
        ]
    ).encode()

    modified, scraper_body = _process_batch_request(
        stripper,
        body,
        {},
        ({100: 0, 101: 1, 102: 999}, set(), {}, {}),
        'batch',
    )

    assert json.loads(modified) == [
        {'assetId': 100, 'requestId': 'a'},
        {'assetId': 101, 'requestId': 'b'},
        {'assetId': 999, 'requestId': 'c'},
    ]
    assert json.loads(scraper_body) == [
        {'assetId': 100, 'requestId': 'a'},
        {'assetId': 101, 'requestId': 'b'},
        {'assetId': 102, 'requestId': 'c'},
    ]


def test_exact_id_replacement_takes_priority_over_type_removal() -> None:
    stripper = _make_stripper(_Config())
    body = json.dumps(
        [
            {'assetId': 1234, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
            {'assetId': 5678, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'b'},
        ]
    ).encode()

    modified, scraper_body = _process_batch_request(
        stripper,
        body,
        {},
        ({1234: 999}, {63}, {}, {}),
        'batch',
    )

    assert json.loads(modified) == [
        {'assetId': 999, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
    ]
    assert json.loads(scraper_body) == [
        {'assetId': 1234, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
    ]


def test_cdn_replacement_takes_priority_over_type_removal(monkeypatch: pytest.MonkeyPatch) -> None:
    stripper = _make_stripper(_Config())
    routed: list[_RoutedCall] = []

    def record_route(*args: object, **kwargs: object) -> None:
        routed.append((args, kwargs))

    monkeypatch.setattr(stripper, '_route_cdn', record_route)
    body = json.dumps(
        [
            {'assetId': 1234, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
        ]
    ).encode()

    modified, _ = _process_batch_request(
        stripper,
        body,
        {},
        ({}, {63}, {1234: 'https://example.com/custom.png'}, {}),
        'batch',
    )

    assert json.loads(modified)[0]['assetId'] == 1234
    assert routed
    assert routed[0][0][2] == 'https://example.com/custom.png'
    assert routed[0][1]['map_index'] is None


def test_local_replacement_takes_priority_over_type_removal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stripper = _make_stripper(_Config())
    routed: list[_RoutedCall] = []

    def record_route(*args: object, **kwargs: object) -> None:
        routed.append((args, kwargs))

    monkeypatch.setattr(stripper, '_route_local', record_route)
    replacement = tmp_path / 'custom.png'
    replacement.write_bytes(b'png')
    body = json.dumps(
        [
            {'assetId': 1234, 'assetType': 'TexturePack', 'assetTypeId': 63, 'requestId': 'a'},
        ]
    ).encode()

    modified, _ = _process_batch_request(
        stripper,
        body,
        {},
        ({}, {63}, {}, {1234: str(replacement)}),
        'batch',
    )

    assert json.loads(modified)[0]['assetId'] == 1234
    assert routed
    assert routed[0][0][2] == str(replacement)


def test_whole_texturepack_id_replacement_swaps_parent_without_downloading_xml() -> None:
    class _Scraper:
        @staticmethod
        def _fetch_asset_with_place_id_retry(*_args: object, **_kwargs: object) -> Never:
            message = 'whole TexturePack must not be downloaded as a slot image'
            raise AssertionError(message)

    stripper = _make_stripper(_Config())
    _set_cache_scraper(stripper, _Scraper())
    body = json.dumps(
        [
            _texpack_request(1234, 'color', 'color-fidelity'),
            _texpack_request(1234, 'normal', 'normal-fidelity'),
            _texpack_request(1234, 'orm', 'orm-fidelity'),
        ]
    ).encode()

    modified, scraper_body = _process_batch_request(
        stripper,
        body,
        {},
        ({'TexturePack': 9999}, set(), {}, {}),
        'batch',
    )

    assert [entry['assetId'] for entry in json.loads(modified)] == [9999, 9999, 9999]
    assert [entry['assetId'] for entry in json.loads(scraper_body)] == [1234, 1234, 1234]


def test_predownloaded_texturepack_xml_is_not_served_as_slot_content(tmp_path: Path) -> None:
    stripper = _make_stripper(_Config())
    manifest = tmp_path / 'replacement.dat'
    manifest.write_bytes(
        b'<roblox><texturepack_version>2</texturepack_version>'
        b'<color>10</color><normal>11</normal></roblox>'
    )
    with manifest.open('ab') as replacement_file:
        replacement_file.truncate(16 * 1024 * 1024)
    _set_attr(stripper, '_predownloaded', {9999: str(manifest)})
    body = json.dumps(
        [
            _texpack_request(1234, 'color', 'color-fidelity'),
            _texpack_request(1234, 'normal', 'normal-fidelity'),
            _texpack_request(1234, 'orm', 'orm-fidelity'),
        ]
    ).encode()

    tracemalloc.start()
    try:
        modified, _ = _process_batch_request(
            stripper,
            body,
            {},
            ({'TexturePack': 9999}, set(), {}, {}),
            'batch',
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak < 2 * 1024 * 1024
    assert [entry['assetId'] for entry in json.loads(modified)] == [9999, 9999, 9999]
    assert not _local_redirects(stripper)


def test_animation_precheck_only_reads_prefix_of_non_animation_download(tmp_path: Path) -> None:
    stripper = _make_stripper(_Config())
    asset = tmp_path / 'replacement.dat'
    with asset.open('wb') as output:
        output.write(b'not an animation')
        output.truncate(16 * 1024 * 1024)
    _set_attr(stripper, '_predownloaded', {9999: str(asset)})
    _set_attr(stripper, '_CONV_CACHE_DIR', tmp_path / 'converted')
    tracemalloc.start()
    try:
        stripper.precheck_anim_rigs()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1024 * 1024


def test_animation_prefix_sniff_retains_metadata_stripping(tmp_path: Path) -> None:
    asset = tmp_path / 'replacement.bin'
    asset.write_bytes(b'header' * 20 + b'<roblox!KeyframeSequence')
    prefix = texture_stripper_module._read_replacement_prefix(  # pyright: ignore[reportPrivateUsage]
        asset, 64
    )
    assert prefix == b'<roblox!KeyframeSequence'


def test_animation_type_lookup_preserves_exact_name_matching() -> None:
    stripper = _make_stripper(_Config())
    virtual = cast('dict[str, str]', _attr(stripper, '_VIRTUAL_ANIM_RIG'))
    animation_types = cast('frozenset[int]', _attr(stripper, '_ANIM_TYPE_IDS'))
    expected = {
        name for type_id, name in stripper.ASSET_TYPES.items() if type_id in animation_types
    } | set(virtual)
    names = set(stripper.ASSET_TYPES.values()) | set(virtual)
    variants = names | {name.lower() for name in names} | {name.upper() for name in names}
    callback = cast('Callable[[object], bool]', _attr(stripper, '_is_anim_replacement_key'))
    for name in variants:
        assert callback(name) is (name in expected)
    assert not callback('123:1')
    assert not callback(True)


def test_animation_precheck_deduplicates_paths_in_first_seen_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = tmp_path / 'first.rbxm', tmp_path / 'second.rbxm'
    first.write_bytes(b'first')
    second.write_bytes(b'second')

    class _LocalConfig:
        @staticmethod
        def get_all_replacements() -> ReplacementMaps:
            return {}, set(), {}, {'Animation': str(first), 'WalkAnimation': str(first),
                                   'RunAnimation': str(second)}

    stripper = _make_stripper(_LocalConfig())
    _set_attr(stripper, '_predownloaded', {9999: str(first)})
    _set_attr(stripper, '_CONV_CACHE_DIR', tmp_path / 'converted')
    _set_attr(stripper, '_anim_repl_rig', {})
    seen: list[tuple[str, str, str]] = []

    def detect(_data: bytes) -> str:
        return 'R6'

    def convert(path: str, rig: str, data: bytes | None = None) -> str | None:
        assert data is not None
        seen.append(('keyframe', path, rig))
        return None

    def convert_curve(path: str, rig: str, data: bytes | None = None) -> str | None:
        assert data is not None
        seen.append(('curve', path, rig))
        return None

    monkeypatch.setattr(texture_stripper_module, '_detect_rig', detect)
    monkeypatch.setattr(stripper, '_get_or_create_converted', convert)
    monkeypatch.setattr(stripper, '_get_or_create_converted_curve', convert_curve)
    stripper.precheck_anim_rigs()
    assert seen == [
        (kind, str(path), rig)
        for path in (first, second)
        for kind, rig in (('keyframe', 'R15'), ('curve', 'R6'), ('curve', 'R15'))
    ]


def test_exact_local_texturepack_rule_overrides_type_id_rule(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stripper = _make_stripper(_Config())
    routed: list[_RoutedCall] = []

    def record_route(*args: object, **kwargs: object) -> None:
        routed.append((args, kwargs))

    monkeypatch.setattr(stripper, '_route_local', record_route)
    fish = tmp_path / 'fish.png'
    fish.write_bytes(b'png')
    body = json.dumps(
        [
            _texpack_request(14108663921, 'carpet-color', 'color-fidelity'),
            _texpack_request(2222, 'wall-color', 'color-fidelity'),
        ]
    ).encode()

    modified, scraper_body = _process_batch_request(
        stripper,
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


def _texpack_request(asset_id: int, request_id: int | str, crpl: str) -> _JsonObject:
    return {
        'assetId': asset_id,
        'contentRepresentationPriorityList': crpl,
        'doNotFallbackToBaselineRepresentation': 'true',
        'assetType': 'TexturePack',
        'requestId': str(request_id),
    }


def test_texturepack_fidelity_decodes_slot_and_quality() -> None:
    assert _decode_texpack_slot_quality(
        _texpack_request(
            1, 0, 'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IkFFQT0ifV0='
        )
    ) == (0, 1)
    assert _decode_texpack_slot_quality(
        _texpack_request(
            1, 0, 'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IklFQT0ifV0='
        )
    ) == (1, 1)
    assert _decode_texpack_slot_quality(
        _texpack_request(
            1, 0, 'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IlFFQT0ifV0='
        )
    ) == (2, 1)


def test_texturepack_partial_batch_uses_fidelity_before_occurrence_order() -> None:
    stripper = _make_stripper(_Config())
    requests: list[_JsonValue] = [
        _texpack_request(
            88088208586015,
            7,
            'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IlFJQT0ifSx7ImZvcm1hdCI6Imt0eDIiLCJtYWpvclZlcnNpb24iOiI2cmRvIiwiZmlkZWxpdHkiOiJnZ0E9In1d',
        ),
        _texpack_request(
            88088208586015,
            8,
            'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IklJQT0ifSx7ImZvcm1hdCI6Imt0eDIiLCJtYWpvclZlcnNpb24iOiI2cmRvIiwiZmlkZWxpdHkiOiJnUUE9In1d',
        ),
    ]

    assert _build_texpack_request_slot_map(stripper, requests, {88088208586015}) == {
        0: 2,
        1: 1,
    }


def test_slot_inference_handles_non_texture_batches_and_repeated_implicit_packs() -> None:
    stripper = _make_stripper(_Config())
    ordinary: list[_JsonValue] = [
        {'assetId': 1, 'assetType': 'MeshPart'},
        {'assetId': 2, 'assetType': 'Audio'},
    ]
    assert _build_texpack_request_slot_map(stripper, ordinary) == {}
    assert cache_scraper_module._build_texpack_request_slot_map(  # pyright: ignore[reportPrivateUsage]
        cast('list[object]', ordinary)
    ) == {}

    repeated: list[_JsonValue] = [
        {'assetId': 10, 'contentRepresentationPriorityList': ''},
        {'assetId': 10, 'contentRepresentationPriorityList': ''},
    ]
    assert stripper._build_texpack_request_slot_map(  # pyright: ignore[reportPrivateUsage]
        repeated, infer_repeated_assets=True
    ) == {0: 0, 1: 1}
    assert cache_scraper_module._build_texpack_request_slot_map(  # pyright: ignore[reportPrivateUsage]
        cast('list[object]', repeated)
    ) == {
        0: 0,
        1: 1,
    }
    invalid_keys: list[object] = [
        {'assetId': 10, 'contentRepresentationPriorityList': ''},
        {'assetId': 10, 'contentRepresentationPriorityList': '', 1: 'invalid key'},
    ]
    assert cache_scraper_module._build_texpack_request_slot_map(invalid_keys) == {}  # pyright: ignore[reportPrivateUsage]


def test_unmodified_nested_batch_retains_original_wire_bytes() -> None:
    stripper = _make_stripper(_Config())
    body = (
        b'[ { "assetId": 1, "assetType": "MeshPart", '
        b'"extra": {"nested": [true, null, {"value": 2.5}]} } ]'
    )
    result = stripper.process_batch_request(body, {}, ({999: 1000}, set(), {}, {}), 'nested')
    assert result == (body, body)
    assert stripper.process_batch_request(b'[{}', {}, ({999: 1000}, set(), {}, {}), 'bad') == b'[{}'


def test_removing_batch_entry_keeps_original_request_ids() -> None:
    stripper = _make_stripper(_Config())
    requests = [
        {'assetId': 101 + index, 'assetType': 'MeshPart', 'requestId': str(index)}
        for index in range(3)
    ]
    body = json.dumps(requests).encode()
    result, scraper_body = _process_batch_request(stripper, body, {}, ({}, {102}, {}, {}), 'b')
    assert json.loads(result) == [requests[0], requests[2]]
    assert json.loads(scraper_body) == [requests[0], requests[2]]


def test_unmatched_channel_rule_still_composites_orm_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    stripper = _make_stripper(_Config())
    routed: list[_RoutedCall] = []
    composites: list[dict[str, str | None]] = []

    def composite(
        _asset_id: int | str, channels: dict[str, str | None], *, normal_source: object = None
    ) -> str:
        del normal_source
        composites.append(channels)
        return '/composite.ktx2'

    def route(*args: object, **kwargs: object) -> None:
        routed.append((args, kwargs))

    monkeypatch.setattr(stripper, '_build_orm_composite', composite)
    monkeypatch.setattr(stripper, '_route_local', route)
    body = json.dumps(
        [
            _texpack_request(
                1234,
                'orm',
                'W3siZm9ybWF0Ijoia3R4MiIsIm1ham9yVmVyc2lvbiI6IjdyZG8iLCJmaWRlbGl0eSI6IlFFQT0ifV0=',
            )
        ]
    ).encode()
    _process_batch_request(stripper, body, {}, ({}, set(), {}, {'TexturePack:3': '/rough.png'}), 'b')
    assert composites == [{'roughness': '/rough.png'}]
    assert routed[0][0][1:3] == (1234, '/composite.ktx2')


def test_animation_replacement_rig_detection_strips_bin_metadata(tmp_path: Path) -> None:
    replacement = tmp_path / 'replacement.bin'
    replacement.write_bytes(b'RBXH amazon metadata' + b'<roblox!binary animation')
    stripper = _make_stripper(_Config())

    with patch('fleasion.utils.anim_converter.detect_rig', return_value='R15') as detect_rig:
        assert _detect_repl_rig(stripper, str(replacement)) == 'R15'

    detect_rig.assert_called_once_with(b'<roblox!binary animation')


def test_disabling_config_invalidates_queued_local_route(tmp_path: Path) -> None:
    class _MutableConfig:
        replacements_generation = 0

        def __init__(self) -> None:
            self.replacements: ReplacementMaps = ({}, set(), {}, {1234: str(replacement)})

        def get_all_replacements(self) -> ReplacementMaps:
            return self.replacements

    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'animation')
    config = _MutableConfig()
    stripper = _make_stripper(config)
    body = json.dumps(
        [{'assetId': 1234, 'assetType': 'Image', 'assetTypeId': 1, 'requestId': 'a'}]
    ).encode()

    _process_batch_request(stripper, body, {}, config.replacements, 'old-batch')
    assert stripper.has_pending()

    config.replacements = ({}, set(), {}, {})
    config.replacements_generation += 1

    assert not stripper.has_pending()
    assert stripper.check_cdn_request('fts.rbxcdn.com', '/old-content') is None


def test_discard_batch_cleans_orphans_without_touching_other_batches(tmp_path: Path) -> None:
    """Missing CDN locations must not leave stale routing candidates indefinitely."""
    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'local replacement')
    stripper = _make_stripper(_Config())
    body = json.dumps([{'assetId': 1234, 'requestId': 'a'}]).encode()
    replacements: ReplacementMaps = ({}, set(), {}, {1234: str(replacement)})

    _process_batch_request(stripper, body, {}, replacements, 'failed')
    _process_batch_request(stripper, body, {}, replacements, 'active')
    assert stripper.has_pending()

    stripper.discard_batch('failed')
    assert stripper.has_pending()
    pending = cast('dict[str, object]', _attr(stripper, '_pending'))
    assert all(not key.startswith('failed_') for key in pending)
    assert any(key.startswith('active_') for key in pending)

    stripper.discard_batch('active')
    assert not stripper.has_pending()
    assert cast('dict[str, int]', _attr(stripper, '_batch_generations')) == {}


def test_reset_rejects_response_from_old_batch(tmp_path: Path) -> None:
    class _MutableConfig:
        replacements_generation = 0

        @staticmethod
        def get_all_replacements() -> ReplacementMaps:
            return {}, set(), {}, {1234: str(replacement)}

    replacement = tmp_path / 'replacement.dat'
    replacement.write_bytes(b'animation')
    config = _MutableConfig()
    stripper = _make_stripper(config)
    body = json.dumps([{'assetId': 1234, 'requestId': 'a'}]).encode()
    response = json.dumps(
        [{'requestId': 'a', 'location': 'https://fts.rbxcdn.com/stale-content'}]
    ).encode()

    _process_batch_request(stripper, body, {}, config.get_all_replacements(), 'old-batch')
    assert stripper.has_pending()

    TextureStripper.reset_routes('test cache clear')
    stripper.process_batch_response(body, response, {}, 'old-batch')

    assert not stripper.has_pending()
    assert stripper.check_cdn_request('fts.rbxcdn.com', '/stale-content') is None


def test_replacement_precheck_stops_and_backs_off_after_network_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _ReplacementConfig:
        @staticmethod
        def get_all_replacements() -> ReplacementMaps:
            return {100: 900001, 101: 900002}, set(), {}, {}

    class _OfflineScraper:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        @staticmethod
        def _get_roblosecurity(*, wait: bool = False) -> None:
            _ = wait

        def _https_get(self, hostname: str, path: str, **_kwargs: object) -> tuple[None, None]:
            self.calls.append((hostname, path))
            return None, None

    scraper = _OfflineScraper()
    stripper = _make_stripper(_ReplacementConfig())
    _set_attr(stripper, '_PREDOWNLOAD_DIR', tmp_path / 'predownloaded')
    _set_cache_scraper(stripper, scraper)
    _precheck_pending().difference_update({900001, 900002})
    now: list[float] = [100.0]
    monkeypatch.setattr(
        'fleasion.proxy.addons.texture_stripper.time.monotonic',
        lambda: now[0],
    )

    stripper.precheck_replacements()
    stripper.precheck_replacements()

    assert scraper.calls == [
        ('assetdelivery.roblox.com', '/v1/asset/?id=900001'),
    ]
    assert not ({900001, 900002} & _precheck_pending())
    assert set(_precheck_retry_after(stripper)) == {900001, 900002}
    assert _precheck_network_failure_count(stripper) == 1
    assert set(_precheck_retry_after(stripper).values()) == {220.0}

    now[0] = 221.0
    stripper.precheck_replacements()

    assert len(scraper.calls) == 2
    assert _precheck_network_failure_count(stripper) == 2
    assert set(_precheck_retry_after(stripper).values()) == {461.0}


def test_convert_texpack_local_uses_slot_specific_mipmap_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.cache.tools.image_to_ktx2 import converter as image_converter

    source = tmp_path / 'replacement.png'
    source.write_bytes(b'not-decoded-by-this-test')
    seen_modes: list[str] = []

    def fake_convert(path: Path, *, mipmap_mode: str) -> Path:
        seen_modes.append(mipmap_mode)
        return path

    def preserve_normalized(path: Path, *, mipmap_mode: str) -> Path:
        _ = mipmap_mode
        return path

    monkeypatch.setattr(image_converter, 'get_or_create_ktx2_from_image', fake_convert)
    monkeypatch.setattr(
        TextureStripper,
        '_normalize_rgba8_ktx2',
        staticmethod(preserve_normalized),
    )

    for map_index, expected_mode in ((0, 'color'), (1, 'normal'), (2, 'linear')):
        assert _convert_texpack_local(str(source), map_index=map_index) == str(source)
        assert seen_modes[-1] == expected_mode


def test_rgba8_normalization_preserves_authored_mip_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.cache.tools.rgba_ktx2 import (
        read_rgba8_ktx2_levels,
        write_rgba8_ktx2_levels,
    )

    monkeypatch.setattr(texture_stripper_module, 'APP_CACHE_DIR', tmp_path / 'cache')
    source = tmp_path / 'authored.ktx2'
    base = bytes(range(16))
    tail = bytes((9, 8, 7, 6))
    write_rgba8_ktx2_levels([base, tail], 2, 2, source)

    normalized = _normalize_rgba8_ktx2(source, mipmap_mode='color')

    assert normalized == source
    assert read_rgba8_ktx2_levels(normalized.read_bytes()) == ([base, tail], 2, 2)


def test_rgba8_normalization_generates_missing_mips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.cache.tools.rgba_ktx2 import (
        read_rgba8_ktx2_levels,
        write_rgba8_ktx2_levels,
    )

    monkeypatch.setattr(texture_stripper_module, 'APP_CACHE_DIR', tmp_path / 'cache')
    source = tmp_path / 'single-level.ktx2'
    base = bytes(range(16))
    write_rgba8_ktx2_levels([base], 2, 2, source)

    normalized = _normalize_rgba8_ktx2(source, mipmap_mode='linear')
    parsed = read_rgba8_ktx2_levels(normalized.read_bytes())

    assert parsed is not None
    levels, width, height = parsed
    assert (width, height) == (2, 2)
    assert levels[0] == base
    assert len(levels) == 2


def test_orm_composite_resolves_normal_asset_id_for_variance_mips(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.cache.tools import orm_compositor

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
        def get_texturepack_slot_path(parent_id: int | str, slot: int) -> Path:
            assert parent_id == 1234
            return slot_dir / f'{parent_id}_slot{slot}.ktx2'

    class _Scraper:
        cache_manager = _CacheManager()

        @staticmethod
        def _get_roblosecurity() -> str:
            return 'cookie'

        @staticmethod
        def _fetch_asset_with_place_id_retry(
            asset_id: str, extra_headers: dict[str, str] | None = None
        ) -> tuple[bytes, int]:
            assert asset_id == '98765'
            assert extra_headers == {'Cookie': '.ROBLOSECURITY=cookie;'}
            return b'normal-asset-bytes', 200

    seen: dict[str, object] = {}

    def fake_composite(
        baseline: Path | None,
        channels: dict[str, Path | None],
        cache_dir: Path,
        *,
        normal_source: Path | None,
        normal_baseline: Path | None,
    ) -> str:
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
    stripper = _make_stripper(_Config())
    _set_cache_scraper(stripper, _Scraper())

    result = _build_orm_composite(
        stripper,
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


@pytest.mark.parametrize(
    ('path', 'expected'),
    [
        ('/v1/asset/?id=12345', '/v1/asset/?id=98765'),
        ('/v2/asset/?id=12345', '/v2/asset/?id=98765'),
        ('/v2/assetId/12345', '/v2/assetId/98765'),
        ('/v2/assetId/12345/?format=source', '/v2/assetId/98765/?format=source'),
        ('/v1/assetId/12345', '/v1/assetId/98765'),
        ('/v2/assetId/12345/version/1', '/v2/assetId/12345/version/1'),
        ('/v2/assetId/12345?id=999', '/v2/assetId/12345?id=999'),
        ('/v2/assetId/54321', '/v2/assetId/54321'),
        (
            '/v1/asset/?client=studio&id=12345&auth=private-value&version=3',
            '/v1/asset/?client=studio&id=98765&auth=private-value&version=3',
        ),
        ('/v1/asset?id=12345', '/v1/asset?id=98765'),
        ('/v1/asset/?id=00012345', '/v1/asset/?id=98765'),
        ('/v1/asset/?id=12345&id=67890', '/v1/asset/?id=12345&id=67890'),
        ('/v1/asset/?id=12345&id=bogus', '/v1/asset/?id=12345&id=bogus'),
        ('/v1/asset/?id=12345&id=', '/v1/asset/?id=12345&id='),
        ('/v1/asset/?id=12345&%69d=67890', '/v1/asset/?id=12345&%69d=67890'),
        ('/v1/asset/?id=12345&ID=999', '/v1/asset/?id=98765&ID=999'),
        ('/v1/assets/batch?id=12345', '/v1/assets/batch?id=12345'),
        ('/v1/asset/?notid=12345', '/v1/asset/?notid=12345'),
        ('/v1/asset/?id=%31%32%33%34%35', '/v1/asset/?id=%31%32%33%34%35'),
        ('/v1/asset/?id=54321', '/v1/asset/?id=54321'),
    ],
)
def test_unbatched_exact_id_rewrite_preserves_other_query_parameters(
    path: str, expected: str
) -> None:
    replacements: ReplacementMaps = ({12345: 98765}, set(), {}, {})
    assert TextureStripper.rewrite_direct_asset_id(path, replacements) == expected


_UNBATCHED_MODE_CASES: list[tuple[ReplacementMaps, str]] = [
    (({12345: 98765}, {12345}, {}, {}), '/v1/asset/?id=12345'),
    (({12345: 98765}, set(), {12345: 'https://example.org/a'}, {}), '/v1/asset/?id=12345'),
    (({12345: 98765}, set(), {}, {12345: '/tmp/replacement.mesh'}), '/v1/asset/?id=12345'),
    (({12345: 0}, set(), {}, {}), '/v1/asset/?id=12345'),
    (({12345: 1}, set(), {}, {}), '/v1/asset/?id=12345'),
    (({12345: 12345}, set(), {}, {}), '/v1/asset/?id=12345'),
]


@pytest.mark.parametrize(('replacements', 'expected'), _UNBATCHED_MODE_CASES)
def test_unbatched_id_rewrite_does_not_override_higher_priority_modes(
    replacements: ReplacementMaps, expected: str
) -> None:
    assert TextureStripper.rewrite_direct_asset_id('/v1/asset/?id=12345', replacements) == expected


def test_unbatched_id_rewrite_does_not_forward_predownloaded_private_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(TextureStripper, '_predownloaded', {98765: '/tmp/private-replacement.mesh'})
    replacements: ReplacementMaps = ({12345: 98765}, set(), {}, {})
    assert (
        TextureStripper.rewrite_direct_asset_id('/v1/asset/?id=12345', replacements)
        == '/v1/asset/?id=12345'
    )
