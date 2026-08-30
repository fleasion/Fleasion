from collections.abc import Callable, Mapping
from typing import cast

from fleasion.cache import cache_viewer as cache_viewer_module
from fleasion.cache.cache_viewer import COL_TOGGLE_WIDTH


def _metadata_needs_resolution(info: Mapping[str, object]) -> bool:
    callback = cast(
        'Callable[[Mapping[str, object]], bool]',
        cache_viewer_module.__dict__['_asset_metadata_needs_resolution'],
    )
    return callback(info)


def _copy_converted_varnames() -> tuple[str, ...]:
    method = getattr(cache_viewer_module.CacheViewerTab, '_copy_converted')
    return cast(tuple[str, ...], method.__code__.co_varnames)


def test_cache_viewer_toggle_column_width_constant_is_defined() -> None:
    assert COL_TOGGLE_WIDTH == 14


def test_numeric_creator_remains_pending_after_asset_metadata_resolves() -> None:
    info = {
        'resolved_name': 'reload3',
        'creator_id': 53537032,
        'creator_name': None,
        'created_at': '2015-11-26T14:44:33Z',
        'updated_at': '2025-11-26T14:44:33Z',
    }

    assert _metadata_needs_resolution(info)


def test_named_creator_completes_asset_metadata_resolution() -> None:
    info = {
        'resolved_name': 'reload3',
        'creator_id': 53537032,
        'creator_name': 'Aesthetical',
        'created_at': '2015-11-26T14:44:33Z',
        'updated_at': '2025-11-26T14:44:33Z',
    }

    assert not _metadata_needs_resolution(info)


def test_asset_without_creator_can_still_complete_resolution() -> None:
    info = {
        'resolved_name': 'creatorless',
        'creator_id': None,
        'creator_name': None,
        'created_at': '',
        'updated_at': '',
    }

    assert not _metadata_needs_resolution(info)


def test_copy_converted_keeps_gzip_binding_global() -> None:
    assert 'gzip_module' not in _copy_converted_varnames()


def test_cache_viewer_has_qapplication_for_screen_fallback() -> None:
    import fleasion.cache.cache_viewer as cache_viewer

    assert cache_viewer.QApplication is not None
