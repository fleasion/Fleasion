"""Arabic localization, catalog integrity and right-to-left application layout."""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import cast

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget

from fleasion import localization
from fleasion.translations.ar import ARABIC
from fleasion.translations.en import ENGLISH


def test_arabic_catalog_is_complete_and_preserves_markup() -> None:
    """Every source string has a manually authored Arabic counterpart."""
    assert list(ARABIC) == list(ENGLISH)
    assert len(ARABIC) == 1904

    for key, english in ENGLISH.items():
        arabic = ARABIC[key]
        assert arabic
        assert Counter(re.findall(r'\{[^{}]+\}', arabic)) == Counter(
            re.findall(r'\{[^{}]+\}', english)
        ), key
        assert re.findall(r'<[^>]+>', arabic) == re.findall(r'<[^>]+>', english), key
        assert arabic.count('\n') == english.count('\n'), key


def test_arabic_text_and_technical_literals_are_distinguished() -> None:
    assert ARABIC['language.picker.title'] == 'اختيار اللغة'
    assert ARABIC['app.cancel'] == 'إلغاء'
    assert ARABIC['ui.gui.replacer_config.replacer'] == 'أداة الاستبدال'
    assert ARABIC['ui.gui.settings_tab.proxy_mode'] == 'وضع الوكيل'
    assert ARABIC['ui.cache.animation_viewer.text'] == '?'
    assert ARABIC['font_viewer.sample.symbols'] == ENGLISH['font_viewer.sample.symbols']
    assert ARABIC['ui.gui.modifications_tab.content_sounds_oof_ogg'] == ENGLISH[
        'ui.gui.modifications_tab.content_sounds_oof_ogg'
    ]
    assert any('\u0627' <= c <= '\u064a' for c in ARABIC['font_viewer.sample.lowercase'])
    assert ARABIC['ui.gui.modifications_tab.false'] == 'غير صحيح'
    assert ARABIC['asset_filter.type.38'] == 'مكوّن إضافي'
    assert ARABIC['asset_filter.type.10'] != ARABIC['ui.gui.proxy_tab_ui.form']
    assert ARABIC['ui.gui.conflicts.col_winner'] == 'القاعدة المعتمدة'
    assert ARABIC['ui.gui.conflicts.col_losers'] == 'القواعد المتجاوزة'
    assert ARABIC['ui.gui.subplace_joiner_tab.jobids'] == 'معرّفات الجلسات'
    assert 'placeID' in ARABIC['ui.gui.rando_stuff_tab.reserved_server_placeid']
    assert 'Roblox' in ARABIC['ui.gui.rando_stuff_tab.this_account_will_be_used_for_fleasion']
    assert 'Shift Lock' in ARABIC['modifications.builtin.texture.shiftlock_cursor']
    assert ARABIC['ui.cache.rbxm_preview.delete_instance'] == 'حذف المثيل'
    assert ARABIC['ui.gui.settings_tab.upstream_transport'] == 'طريقة الاتصال الصادر'
    assert ARABIC['settings.scraped_games.section'] == 'الألعاب المفهرسة'
    assert ARABIC['settings.scraper.section'] == 'جامع الأصول'


def test_identical_english_text_has_consistent_arabic_translation() -> None:
    """Keep status strings, tray labels and onboarding aliases synchronized."""
    by_english: dict[str, str] = {}
    for key, english in ENGLISH.items():
        translation = ARABIC[key]
        if english in by_english:
            assert translation == by_english[english], key
        else:
            by_english[english] = translation


def test_arabic_locale_and_qt_layout_direction() -> None:
    """Arabic windows mirror correctly without changing an LTR session permanently."""
    previous_language = localization.get_language()
    application = QApplication.instance()
    app = cast('QApplication', application) if application is not None else QApplication([])
    previous_direction = app.layoutDirection()
    try:
        assert ('ar', 'العربية') in localization.available_languages()
        assert localization.set_language('ar-EG') == 'ar'
        assert localization.is_right_to_left()
        localization.apply_language_direction(app)
        assert app.layoutDirection() == Qt.LayoutDirection.RightToLeft
        arabic_window = QWidget()
        assert arabic_window.layoutDirection() == Qt.LayoutDirection.RightToLeft
        assert localization.tr('language.picker.title') == 'اختيار اللغة'
        assert localization.tr('onboarding.welcome.ok_countdown', seconds=5) == 'موافق (5s)'
        arabic_window.close()

        localization.set_language('en-US')
        localization.apply_language_direction(app)
        assert not localization.is_right_to_left()
        assert app.layoutDirection() == Qt.LayoutDirection.LeftToRight
    finally:
        localization.set_language(previous_language)
        app.setLayoutDirection(previous_direction)
