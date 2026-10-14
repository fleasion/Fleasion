"""Regression coverage for the manually written Filipino catalog."""

from __future__ import annotations

import re

from fleasion import localization
from fleasion.translations.fil import FILIPINO


def test_filipino_catalog_is_complete_and_preserves_formatting() -> None:
    """All UI strings must have Filipino entries and safe substitutions."""
    placeholders = re.compile(r'\{[^{}]+\}')
    markup = re.compile(r'<[^>]+>')

    assert list(FILIPINO) == list(localization.ENGLISH)
    for identifier, english in localization.ENGLISH.items():
        filipino = FILIPINO[identifier]
        assert filipino, identifier
        assert sorted(placeholders.findall(filipino)) == sorted(
            placeholders.findall(english)
        ), identifier
        assert markup.findall(filipino) == markup.findall(english), identifier
        assert filipino.count('\n') == english.count('\n'), identifier


def test_filipino_language_selection_and_common_ui() -> None:
    """Both Filipino and Tagalog locale codes resolve to the Filipino UI."""
    assert ('fil', 'Filipino') in localization.available_languages()
    for code in ('fil', 'fil-PH', 'fil_PH', 'tl', 'tl-PH', 'tl_PH'):
        assert localization.normalize_language(code) == 'fil'

    previous = localization.get_language()
    try:
        assert localization.set_language('fil-PH') == 'fil'
        assert localization.tr('language.picker.title') == 'Pumili ng Wika'
        assert localization.tr('onboarding.welcome.ok_countdown', seconds=4) == 'OK (4s)'
        assert localization.tr_count(1, 'count.asset.one', 'count.asset.other') == '1 asset'
        assert localization.tr_count(3, 'count.asset.one', 'count.asset.other') == '3 asset'
    finally:
        localization.set_language(previous)


def test_filipino_onboarding_names_the_actual_controls() -> None:
    """Instructions must reproduce the visible labels instead of English names."""
    welcome = FILIPINO['onboarding.welcome.body']
    for identifier in (
        'ui.gui.replacer_config.replacer',
        'ui.gui.replacer_config.scraped_games',
        'ui.gui.replacer_config.asset_ids',
        'ui.gui.json_viewer.replacement_id',
        'replacer.rules.add_new',
        'ui.gui.replacer_config.enabled',
        'ui.gui.replacer_config.clear_cache',
        'ui.gui.replacer_config.scraper',
    ):
        assert FILIPINO[identifier].rstrip(':') in welcome, identifier

    directory_button = FILIPINO['app.click_here_to_open_directory']
    assert directory_button == FILIPINO['ui.app.click_here_to_open_directory']
    assert directory_button in FILIPINO['app.most_likely_causes_br_a_antivirus_security']
    assert directory_button in FILIPINO['ui.app.most_likely_causes_br_a_antivirus_security']
    assert 'ñ' in FILIPINO['font_viewer.sample.lowercase']
    assert 'Ñ' in FILIPINO['font_viewer.sample.uppercase']


def test_filipino_technical_terms_keep_their_meanings() -> None:
    """Do not translate engine asset names as unrelated everyday concepts."""
    assert FILIPINO['asset_filter.type.27'] == 'Torso'
    assert FILIPINO['modifications.builtin.avatar.torso'] == 'Torso'
    assert FILIPINO['asset_filter.type.9'] == 'Place'
    assert FILIPINO['asset_filter.type.74'] == 'FontFace'
    assert FILIPINO['asset_filter.type.51'] == 'Animation Kapag Nakatigil'
    assert FILIPINO['cache.texture_map.metalness'] == 'Antas ng Metalness'
    assert FILIPINO['json.texture_map.metalness'] == 'Antas ng Metalness'
    assert FILIPINO['proxy.table.method'] == 'HTTP Method'
    assert FILIPINO['proxy.table.path'] == 'URL Path'
    assert FILIPINO['ui.gui.modifications_tab.false'] == 'Hindi Totoo'
    assert FILIPINO['ui.gui.replacer_config.no_configs_enabled'] == 'Walang Config na On'
    assert 'Panig ng Skybox' in FILIPINO['ui.gui.modifications_tab.apply_to_all_sky_faces']
    assert 'I-stall' in FILIPINO['ui.gui.rando_stuff_tab.infinitely_stall_subplace']


def test_filipino_help_uses_matching_navigation_and_launch_terms() -> None:
    """Documentation should point to the localized UI and describe actual launches."""
    route = ' > '.join((
        FILIPINO['ui.gui.replacer_config.dashboard'],
        FILIPINO['ui.gui.replacer_config.miscellaneous'],
        FILIPINO['ui.gui.rando_stuff_tab.account_manager'],
        FILIPINO['ui.gui.rando_stuff_tab.import_browser_login'],
    ))
    for key in ('app.auth_warning.linux_guidance', 'app.auth_warning.macos_guidance'):
        assert route in FILIPINO[key]
        assert 'paglunsad ng Roblox gamit ang account' in FILIPINO[key]
        assert 'pagbubukas ng account' not in FILIPINO[key]
    assert 'paglunsad ng Roblox gamit ang account' in (
        FILIPINO['app.most_fleasion_account_aware_features_will_not']
    )
    assert FILIPINO['app.run_on_boot'] == FILIPINO['ui.gui.settings_tab.run_on_boot']
    assert FILIPINO['app.run_on_boot'] == FILIPINO['ui.tray.run_on_boot']
    assert FILIPINO['app.run_on_boot'] == 'Awtomatikong Pagsisimula'
    assert 'Pagbubukas sa Pag-boot' not in FILIPINO['app.startup_integration.body']


def test_filipino_font_previews_preserve_latin_pangrams() -> None:
    """Font samples must continue exercising the complete Latin alphabet."""
    for key in (
        'font_viewer.sample.pack_my_box',
        'font_viewer.sample.quick_brown_fox',
        'font_viewer.sample.quick_zebras',
    ):
        sentence = FILIPINO[key].lower()
        assert set('abcdefghijklmnopqrstuvwxyz') <= set(sentence), key
