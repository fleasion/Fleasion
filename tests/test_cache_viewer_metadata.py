from fleasion.cache.cache_viewer import COL_TOGGLE_WIDTH, _asset_metadata_needs_resolution


def test_cache_viewer_toggle_column_width_constant_is_defined():
    assert COL_TOGGLE_WIDTH == 14


def test_numeric_creator_remains_pending_after_asset_metadata_resolves():
    info = {
        'resolved_name': 'reload3',
        'creator_id': 53537032,
        'creator_name': None,
        'created_at': '2015-11-26T14:44:33Z',
        'updated_at': '2025-11-26T14:44:33Z',
    }

    assert _asset_metadata_needs_resolution(info)


def test_named_creator_completes_asset_metadata_resolution():
    info = {
        'resolved_name': 'reload3',
        'creator_id': 53537032,
        'creator_name': 'Aesthetical',
        'created_at': '2015-11-26T14:44:33Z',
        'updated_at': '2025-11-26T14:44:33Z',
    }

    assert not _asset_metadata_needs_resolution(info)


def test_asset_without_creator_can_still_complete_resolution():
    info = {
        'resolved_name': 'creatorless',
        'creator_id': None,
        'creator_name': None,
        'created_at': '',
        'updated_at': '',
    }

    assert not _asset_metadata_needs_resolution(info)
