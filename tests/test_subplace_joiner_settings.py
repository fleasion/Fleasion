import json
import threading

from Fleasion.gui import subplace_joiner_tab


class _FakeResponse:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _settings_owner():
    owner = subplace_joiner_tab.SubplaceJoinerTab.__new__(subplace_joiner_tab.SubplaceJoinerTab)
    owner.recent_ids = []
    owner.favorites = []
    owner._custom_names = {}
    owner._place_name_cache = {}
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


def test_subplace_recent_name_resolves_with_authenticated_multiget():
    owner = _settings_owner()
    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse(200, [{"name": "Build A Boat For Treasure"}])

    owner._get = fake_get

    assert owner._resolve_place_name("537413528", "cookie-secret") == "Build A Boat For Treasure"
    assert calls[0][1]["cookies"] == {".ROBLOSECURITY": "cookie-secret"}


def test_subplace_recent_name_uses_public_fallback_without_cookie():
    owner = _settings_owner()

    def fake_get(url, **_kwargs):
        if "universes/v1/places" in url:
            return _FakeResponse(200, {"universeId": 210851291})
        if "games?universeIds" in url:
            return _FakeResponse(200, {"data": [{"name": "Build A Boat For Treasure"}]})
        raise AssertionError(url)

    owner._get = fake_get

    assert owner._resolve_place_name("537413528", "") == "Build A Boat For Treasure"


def test_subplace_recent_name_failure_does_not_cache_raw_place_id(monkeypatch):
    owner = _settings_owner()
    done = threading.Event()
    callbacks = []

    monkeypatch.setattr(subplace_joiner_tab, "_wait_for_roblosecurity", lambda: "")
    owner._resolve_place_name = lambda place_id, cookie="": None
    owner._on_main = lambda fn: (fn(), True)[1]

    owner._fetch_place_name("537413528", lambda name: (callbacks.append(name), done.set()))

    assert done.wait(2) is False
    assert callbacks == []
    assert owner._place_name_cache == {}
