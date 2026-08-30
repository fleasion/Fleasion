import json
from pathlib import Path

import pytest

from fleasion.modifications.fflag_profiles import FastFlagProfileManager


def test_profiles_save_load_rename_and_delete(tmp_path: Path) -> None:
    profiles = FastFlagProfileManager(tmp_path)

    assert (
        profiles.save('Performance', {'DFIntTaskSchedulerTargetFps': 240, 'FFlagExample': True})
        == 'Performance'
    )
    assert profiles.list_profiles() == ['Performance']
    assert profiles.load('Performance') == {
        'DFIntTaskSchedulerTargetFps': '240',
        'FFlagExample': 'True',
    }

    assert profiles.rename('Performance', 'Quality.json') == 'Quality'
    assert profiles.list_profiles() == ['Quality']
    profiles.delete('Quality')
    assert profiles.list_profiles() == []


def test_profiles_reject_unsafe_names_and_invalid_content(tmp_path: Path) -> None:
    profiles = FastFlagProfileManager(tmp_path)

    with pytest.raises(ValueError, match='invalid character'):
        profiles.save('../outside', {})
    with pytest.raises(ValueError, match='cannot be empty'):
        profiles.save(' .json ', {})

    tmp_path.mkdir(exist_ok=True)
    (tmp_path / 'broken.json').write_text(json.dumps(['not', 'an', 'object']), encoding='utf-8')
    with pytest.raises(ValueError, match='name/value pairs'):
        profiles.load('broken')


def test_profiles_reject_unsupported_values(tmp_path: Path) -> None:
    profiles = FastFlagProfileManager(tmp_path)
    (tmp_path / 'invalid.json').write_text('{"FFlagExample": ["no"]}', encoding='utf-8')

    with pytest.raises(ValueError, match='string, number, or boolean'):
        profiles.load('invalid')
