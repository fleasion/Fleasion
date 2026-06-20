import importlib.util
import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATHS_PATH = _REPO_ROOT / "src" / "Fleasion" / "utils" / "paths.py"


def _load_paths_module(monkeypatch, tmp_path, *, xdg_config_home: Path | None):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("FLEASION_USER_HOME", raising=False)
    if xdg_config_home is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))

    spec = importlib.util.spec_from_file_location("fleasion_paths_under_test", _PATHS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_config_dir_uses_xdg_config_home(monkeypatch, tmp_path):
    xdg_config_home = tmp_path / "xdg-config"

    paths = _load_paths_module(monkeypatch, tmp_path, xdg_config_home=xdg_config_home)

    assert paths.CONFIG_DIR == xdg_config_home / "Fleasion"
    assert paths.CONFIG_FILE == xdg_config_home / "Fleasion" / "settings.json"
    assert paths.CONFIGS_FOLDER == xdg_config_home / "Fleasion" / "configs"


def test_linux_config_dir_defaults_to_home_dot_config(monkeypatch, tmp_path):
    paths = _load_paths_module(monkeypatch, tmp_path, xdg_config_home=None)

    assert paths.CONFIG_DIR == tmp_path / "home" / ".config" / "Fleasion"
