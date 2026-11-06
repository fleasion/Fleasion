"""Undocumented Roblox localization asset types remain consistently named."""

from __future__ import annotations

from fleasion.cache.cache_manager import CacheManager
from fleasion.proxy.addons.texture_stripper import TextureStripper


def test_localization_translation_type_60_is_recognized_by_all_consumers() -> None:
    assert CacheManager.ASSET_TYPES[60] == 'LocalizationTableTranslation'
    assert TextureStripper.ASSET_TYPES[60] == 'LocalizationTableTranslation'
