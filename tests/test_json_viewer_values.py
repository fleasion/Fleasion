import pytest

pytest.importorskip('PySide6')

from fleasion.gui.json_viewer import _coerce_import_value


def test_json_viewer_does_not_truncate_animation_length_into_asset_id():
    assert _coerce_import_value(14098254579) == 14098254579
    assert _coerce_import_value(1.25) is None
    assert _coerce_import_value(0.15) is None


def test_json_viewer_accepts_only_integer_numeric_strings():
    assert _coerce_import_value('94820576007871') == 94820576007871
    assert _coerce_import_value('1.25') is None
    assert _coerce_import_value(True) is None
