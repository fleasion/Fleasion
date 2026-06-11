import json

from Fleasion.gui import subplace_joiner_tab


def _settings_owner():
    owner = subplace_joiner_tab.SubplaceJoinerTab.__new__(subplace_joiner_tab.SubplaceJoinerTab)
    owner.recent_ids = []
    owner.favorites = []
    owner._custom_names = {}
    return owner


def test_subplace_settings_save_uses_user_owned_config_root(tmp_path, monkeypatch):
    monkeypatch.setattr(subplace_joiner_tab, "CONFIG_DIR", tmp_path)
    legacy_dir = tmp_path / "subplace"
    legacy_dir.mkdir()
    legacy_dir.chmod(0o555)

    owner = _settings_owner()
    owner.recent_ids = ["123", "456"]
    owner.favorites = ["456"]
    owner._custom_names = {"123": "First"}

    try:
        owner._save_settings()
    finally:
        legacy_dir.chmod(0o755)

    primary = tmp_path / "subplace_joiner_settings.json"
    assert primary.exists()
    assert not (legacy_dir / "settings.json").exists()
    data = json.loads(primary.read_text(encoding="utf-8"))
    assert data["recent_ids"] == ["123", "456"]
    assert data["favorites"] == ["456"]
    assert data["custom_names"] == {"123": "First"}


def test_subplace_settings_loads_legacy_file_and_migrates(tmp_path, monkeypatch):
    monkeypatch.setattr(subplace_joiner_tab, "CONFIG_DIR", tmp_path)
    legacy_dir = tmp_path / "subplace"
    legacy_dir.mkdir()
    legacy = legacy_dir / "settings.json"
    legacy.write_text(
        json.dumps({
            "recent_ids": ["987", ""],
            "favorites": ["654"],
            "custom_names": {"987": "Legacy"},
        }),
        encoding="utf-8",
    )

    owner = _settings_owner()
    owner._load_settings()

    assert owner.recent_ids == ["987"]
    assert owner.favorites == ["654"]
    assert owner._custom_names == {"987": "Legacy"}
    primary = tmp_path / "subplace_joiner_settings.json"
    assert primary.exists()
    assert json.loads(primary.read_text(encoding="utf-8"))["recent_ids"] == ["987"]


def test_subplace_settings_prefers_primary_over_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(subplace_joiner_tab, "CONFIG_DIR", tmp_path)
    legacy_dir = tmp_path / "subplace"
    legacy_dir.mkdir()
    (legacy_dir / "settings.json").write_text(
        json.dumps({"recent_ids": ["111"], "favorites": [], "custom_names": {}}),
        encoding="utf-8",
    )
    (tmp_path / "subplace_joiner_settings.json").write_text(
        json.dumps({"recent_ids": ["222"], "favorites": ["333"], "custom_names": {}}),
        encoding="utf-8",
    )

    owner = _settings_owner()
    owner._load_settings()

    assert owner.recent_ids == ["222"]
    assert owner.favorites == ["333"]
