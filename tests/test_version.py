from pathlib import Path

from pytest import MonkeyPatch

from fleasion import version


def test_frozen_version_uses_distribution_metadata(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(version.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(version, '_read_pyproject_version', lambda: '0.1.0')
    monkeypatch.setattr(version, '_read_installed_version', lambda: '2.3.0')

    assert version.read_version() == '2.3.0'


def test_source_version_falls_back_to_pyproject(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    package_path = tmp_path / 'src' / 'fleasion'
    package_path.mkdir(parents=True)
    fake_module_path = package_path / 'version.py'
    fake_module_path.touch()
    (tmp_path / 'pyproject.toml').write_text(
        "[project]\nname = 'Fleasion'\nversion = '2.3.0'\n",
        encoding='utf-8',
    )

    monkeypatch.setattr(version, '__file__', str(fake_module_path))
    monkeypatch.setattr(version, '_read_installed_version', lambda: '0.1.0')

    assert version.read_version() == '2.3.0'


def test_unrelated_parent_pyproject_is_ignored(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    package_path = tmp_path / 'temporary-extraction' / 'fleasion'
    package_path.mkdir(parents=True)
    fake_module_path = package_path / 'version.py'
    fake_module_path.touch()
    (tmp_path / 'pyproject.toml').write_text(
        "[project]\nname = 'unrelated-project'\nversion = '0.1.0'\n",
        encoding='utf-8',
    )

    monkeypatch.setattr(version, '__file__', str(fake_module_path))
    monkeypatch.setattr(version, '_read_installed_version', lambda: '2.3.0')

    assert version.read_version() == '2.3.0'
