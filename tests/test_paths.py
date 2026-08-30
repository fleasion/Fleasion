import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform == 'win32', reason='Linux-only path configuration tests'
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PATHS_PATH = _REPO_ROOT / 'src' / 'fleasion' / 'utils' / 'paths.py'


def _load_paths_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, xdg_config_home: Path | None
):
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.delenv('FLEASION_USER_HOME', raising=False)
    if xdg_config_home is None:
        monkeypatch.delenv('XDG_CONFIG_HOME', raising=False)
    else:
        monkeypatch.setenv('XDG_CONFIG_HOME', str(xdg_config_home))

    spec = importlib.util.spec_from_file_location('fleasion_paths_under_test', _PATHS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_linux_config_dir_uses_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_config_home = tmp_path / 'xdg-config'

    paths = _load_paths_module(monkeypatch, tmp_path, xdg_config_home=xdg_config_home)

    assert paths.CONFIG_DIR == xdg_config_home / 'Fleasion'
    assert paths.CONFIG_FILE == xdg_config_home / 'Fleasion' / 'settings.json'
    assert paths.CONFIGS_FOLDER == xdg_config_home / 'Fleasion' / 'configs'


def test_linux_config_dir_defaults_to_home_dot_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    paths = _load_paths_module(monkeypatch, tmp_path, xdg_config_home=None)

    assert paths.CONFIG_DIR == tmp_path / 'home' / '.config' / 'Fleasion'
