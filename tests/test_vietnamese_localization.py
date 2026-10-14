"""Vietnamese localization coverage and regression tests."""

from __future__ import annotations

from fleasion import localization
from fleasion.translations.en import ENGLISH
from fleasion.translations.vi import VIETNAMESE


def test_vietnamese_is_selectable_and_formats_counted_strings() -> None:
    assert localization.normalize_language('vi-VN') == 'vi'
    assert localization.normalize_language('vi_VN') == 'vi'
    assert ('vi', 'Tiếng Việt') in localization.available_languages()

    original_language = localization.get_language()
    try:
        assert localization.set_language('vi-VN') == 'vi'
        assert localization.tr('language.picker.title') == 'Chọn ngôn ngữ'
        assert localization.tr('settings.language.section') == 'Ngôn ngữ'
        assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == 'OK (4s)'
        assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == '1 tài nguyên'
        assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == '3 tài nguyên'
    finally:
        localization.set_language(original_language)


def test_vietnamese_catalog_is_complete_and_preserves_technical_data() -> None:
    assert list(VIETNAMESE) == list(ENGLISH)
    for identifier in (
        'rbxm_preview.property.default_type_string',
        'ui.gui.modifications_tab.content_sounds_oof_ogg',
        'ui.gui.replacer_config.value',
        'ui.gui.modifications_tab.text',
        'ui.gui.rando_stuff_tab.warning_do_not_share_this_sharing_this',
    ):
        assert VIETNAMESE[identifier] == ENGLISH[identifier]

    sample_text = (
        VIETNAMESE['font_viewer.sample.lowercase']
        + VIETNAMESE['font_viewer.sample.uppercase']
    )
    assert set('ăâđêôơưàáảãạằắẳẵặầấẩẫậờớởỡợừứửữự') <= set(sample_text)


def test_vietnamese_onboarding_uses_matching_ui_labels() -> None:
    welcome = VIETNAMESE['onboarding.welcome.body']
    for identifier in (
        'ui.gui.replacer_config.replacer',
        'ui.gui.replacer_config.scraped_games',
        'ui.gui.replacer_config.asset_ids',
        'ui.gui.json_viewer.replacement_id',
        'ui.gui.replacer_config.clear_cache',
        'ui.gui.replacer_config.scraper',
    ):
        assert VIETNAMESE[identifier].rstrip(':') in welcome


def test_vietnamese_technical_actions_are_not_misleading() -> None:
    assert VIETNAMESE['rbxm_preview.property.default_name'] == 'NewProperty'
    assert VIETNAMESE['rbxm_preview.property.default_type_string'] == 'STRING'

    # Remove means an asset replacement action, not deleting a configuration row
    assert VIETNAMESE['replacer.action.remove'] == 'Loại bỏ'
    assert VIETNAMESE['replacer.action.remove'] != VIETNAMESE['replacer.config.delete']

    # Intercepting traffic to edit it is distinct from dropping the traffic
    intercept = VIETNAMESE['ui.gui.proxy_tab_ui.auto_intercept_by_api_url']
    dropped = VIETNAMESE['ui.gui.proxy_tab_ui.drop']
    assert 'chặn bắt' in intercept
    assert dropped == 'Loại bỏ'
    assert intercept != dropped
    assert 'chặn bắt' in VIETNAMESE['ui.gui.proxy_tab.this_tab_shows_all_network_traffic_coming']


def test_vietnamese_technical_asset_names_have_correct_meanings() -> None:
    assert VIETNAMESE['asset_filter.type.27'] == 'Thân'
    assert VIETNAMESE['asset_filter.type.34'] == 'Vé trò chơi'
    assert VIETNAMESE['cache.texture_map.bumpmap'] == 'Bản đồ độ nổi'
    assert VIETNAMESE['cache.texture_map.albedo'] != VIETNAMESE['cache.texture_map.diffuse']
    assert 'vị trí kết cấu' in VIETNAMESE['cache.export_format.slot_ktx2_files']
