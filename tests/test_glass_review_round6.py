"""Regressions for blur write recovery, noncanonical keys, and new translations."""

from __future__ import annotations

from string import Formatter
from typing import TYPE_CHECKING

import pytest

from fleasion.config import manager
from fleasion.gui.conflicts import find_conflicts
from fleasion.gui.glass import desktop_blur
from fleasion.localization import get_language, set_language, tr
from fleasion.translations.en import ENGLISH

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> manager.ConfigManager:
    monkeypatch.setattr(manager, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(manager, 'CONFIG_FILE', tmp_path / 'settings.json')
    monkeypatch.setattr(manager, 'CONFIGS_FOLDER', tmp_path / 'configs')
    return manager.ConfigManager()


@pytest.fixture
def active_desktop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[desktop_blur.DesktopBlur, dict[str, str], Path, list[bool]]:
    values = {'blur': 'true', 'enable-all': 'false', 'whitelist': "['Firefox']"}
    backup = tmp_path / 'blur-backup.json'
    fail_writes = [True]

    def run(*args: str) -> str | None:
        if args[0] == 'gnome-extensions':
            return 'State: ACTIVE'
        method = args[1]
        key = args[2].rsplit('/', 1)[-1]
        if method == 'read':
            return values.get(key, '')
        if method == 'write':
            if fail_writes[0]:
                return None
            values[key] = args[3]
        elif method == 'reset':
            values.pop(key, None)
        return ''

    monkeypatch.setattr(desktop_blur, '_run', run)
    monkeypatch.setattr(desktop_blur.sys, 'platform', 'linux')
    monkeypatch.setattr(desktop_blur, '_BACKUP_FILE', backup)
    monkeypatch.setattr(desktop_blur, 'CONFIG_DIR', tmp_path)
    return desktop_blur.DesktopBlur(), values, backup, fail_writes


def test_failed_blur_write_discards_unapplied_backup_and_next_toggle_retries(
    active_desktop: tuple[desktop_blur.DesktopBlur, dict[str, str], Path, list[bool]]
) -> None:
    blur, values, backup, fail_writes = active_desktop
    assert blur.status(refresh=True) == 'off'
    assert blur.can_toggle()
    assert not blur.enable()
    assert values['whitelist'] == "['Firefox']"
    assert not backup.exists()
    assert not blur.owns_entry()
    fail_writes[0] = False
    assert blur.toggle()
    pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
    assert pending is not None
    assert pending.result(timeout=3) == ('on', True)
    assert blur.poll_refresh()
    assert values['whitelist'] == "['Firefox', 'fleasion']"
    assert backup.exists()
    blur.stop_polling()


def test_failed_blur_write_with_applied_changes_keeps_ownership(
    active_desktop: tuple[desktop_blur.DesktopBlur, dict[str, str], Path, list[bool]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blur, values, backup, _fail_writes = active_desktop

    def write_then_report_failure(key: str, value: str) -> bool:
        values[key] = value
        return False

    monkeypatch.setattr(desktop_blur, '_write', write_then_report_failure)
    assert blur.enable()
    assert values['whitelist'] == "['Firefox', 'fleasion']"
    assert backup.exists()
    assert blur.owns_entry()


def test_failed_blur_write_with_external_whitelist_change_cleans_unapplied_backup(
    active_desktop: tuple[desktop_blur.DesktopBlur, dict[str, str], Path, list[bool]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blur, values, backup, _fail_writes = active_desktop

    def modify_other_entry(_key: str, _value: str) -> bool:
        values['whitelist'] = "['Firefox', 'Other']"
        return False

    monkeypatch.setattr(desktop_blur, '_write', modify_other_entry)
    assert not blur.enable()
    assert not backup.exists()  # Fleasion was not added; preserve the user's edits
    assert values['whitelist'] == "['Firefox', 'Other']"


def test_toggle_recovers_stale_backup_left_by_previous_failed_write(
    active_desktop: tuple[desktop_blur.DesktopBlur, dict[str, str], Path, list[bool]]
) -> None:
    blur, values, backup, fail_writes = active_desktop
    backup.write_text(
        '{"whitelist": "[\'Firefox\']", '
        '"whitelist_applied": "[\'Firefox\', \'fleasion\']"}',
        encoding='utf-8',
    )
    fail_writes[0] = False
    assert blur.status(refresh=True) == 'off'
    assert blur.toggle()
    pending = blur._toggle_pending  # pyright: ignore[reportPrivateUsage]
    assert pending is not None
    assert pending.result(timeout=3) == ('on', True)
    assert blur.poll_refresh()
    assert values['whitelist'] == "['Firefox', 'fleasion']"
    assert backup.exists()
    blur.stop_polling()


@pytest.mark.parametrize(
    ('left', 'right', 'expected'),
    [
        ('TexturePack:00', 'TexturePack:0', False),
        ('TexturePack:00', 'TexturePack', False),
        ('1001:01', 'TexturePack:1', False),
        ('01001:1', 'TexturePack:1', False),
        ('TexturePack:0', 'TexturePack', True),
        ('1001:1', 'TexturePack:1', True),
    ],
)
def test_imported_noncanonical_slot_keys_do_not_create_potential_overlaps(
    config: manager.ConfigManager, left: str, right: str, expected: bool
) -> None:
    for name, key in (('a', left), ('b', right)):
        assert config.create_config(name)
        config.set_replacement_rules(name, [{'replace_ids': [key], 'with_id': 12345678}])
        config.set_config_enabled(name, True)
    found = find_conflicts(config)
    assert bool(found) is expected
    if expected:
        assert all(conflict.potential for conflict in found)


def _variables(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field is not None}


def test_new_ui_translations_have_matching_placeholders_and_real_localized_strings() -> None:
    from fleasion.translations.de import GERMAN
    from fleasion.translations.es import SPANISH
    from fleasion.translations.fr import FRENCH
    from fleasion.translations.kk import KAZAKH
    from fleasion.translations.pl import POLISH
    from fleasion.translations.pt import PORTUGUESE
    from fleasion.translations.ru import RUSSIAN
    from fleasion.translations.tr import TURKISH
    from fleasion.translations.zh import CHINESE

    prefix_keys = ('ui.gui.command_palette.', 'settings.appearance.glass_', 'ui.gui.conflicts.')
    keys = [key for key in ENGLISH if key.startswith(prefix_keys)]
    keys.extend(('settings.appearance.theme.glass', 'tray.theme.glass'))
    dictionaries = {
        'de': GERMAN, 'es': SPANISH, 'fr': FRENCH, 'kk': KAZAKH,
        'pl': POLISH, 'pt': PORTUGUESE, 'ru': RUSSIAN,
        'tr': TURKISH, 'zh': CHINESE,
    }
    previous = get_language()
    try:
        for language, translations in dictionaries.items():
            assert set(keys) <= translations.keys(), language
            set_language(language)
            for key in keys:
                localized, english = translations[key], ENGLISH[key]
                assert _variables(localized) == _variables(english), (language, key)
                assert localized != english or key.endswith(('.kind_action', '.kind_theme')) and language in {'fr'}, (language, key)
                values = {'value0': 4, 'value1': 2}
                assert tr(key, **values) == localized.format(**values)
    finally:
        set_language(previous)
