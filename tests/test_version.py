from pathlib import Path

import pytest

from fleasion import version
from fleasion.utils.metadata import APP_REPO


def test_repository_url_comes_from_distribution_metadata() -> None:
    assert APP_REPO == 'https://github.com/fleasion/Fleasion'


@pytest.mark.parametrize('app_version', ['2.4.0', '2.4.0.post1'])
def test_stable_artifact_version_has_no_provenance(app_version: str) -> None:
    assert version.build_artifact_version(app_version, {}) == app_version


@pytest.mark.parametrize(
    'app_version',
    ['2.4.0a1', '2.4.0b1', '2.4.0rc1', '2.4.0.dev1'],
)
def test_local_prerelease_artifact_version(app_version: str) -> None:
    assert version.build_artifact_version(app_version, {}) == f'{app_version}+local'


def test_github_prerelease_artifact_version_uses_short_sha() -> None:
    environment = {
        'GITHUB_ACTIONS': 'true',
        'GITHUB_SHA': 'ABCDEF0123456789ABCDEF0123456789ABCDEF01',
    }

    assert version.build_artifact_version('2.4.0b1', environment) == '2.4.0b1+gabcdef0'


@pytest.mark.parametrize('github_sha', ['', '123456', 'not-a-git-sha'])
def test_github_prerelease_artifact_version_rejects_invalid_sha(github_sha: str) -> None:
    environment = {'GITHUB_ACTIONS': 'true', 'GITHUB_SHA': github_sha}

    with pytest.raises(ValueError, match='GITHUB_SHA'):
        version.build_artifact_version('2.4.0b1', environment)


def test_artifact_version_rejects_canonical_local_metadata() -> None:
    with pytest.raises(ValueError, match='must not contain local metadata'):
        version.build_artifact_version('2.4.0b1+custom', {})


@pytest.mark.parametrize(
    'app_version,expected',
    [
        ('2.4', '2.4.0'),
        ('2.4.0b1', '2.4.0'),
        ('2.4.0+local', '2.4.0'),
    ],
)
def test_macos_bundle_version_is_numeric(app_version: str, expected: str) -> None:
    assert version.macos_bundle_version(app_version) == expected


def test_frozen_version_uses_distribution_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(version.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(version, '_read_pyproject_version', lambda: '0.1.0')
    monkeypatch.setattr(version, '_read_installed_version', lambda: '2.3.0')

    assert version.read_version() == '2.3.0'


def test_frozen_version_does_not_derive_artifact_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(version.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(version, '_read_installed_version', lambda: '2.4.0b1')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.setenv('GITHUB_SHA', 'abcdef0123456789abcdef0123456789abcdef01')

    assert version.read_version() == '2.4.0b1'


def test_source_version_falls_back_to_pyproject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package_path = tmp_path / 'src' / 'fleasion'
    package_path.mkdir(parents=True)
    fake_module_path = package_path / 'version.py'
    fake_module_path.touch()
    (tmp_path / 'pyproject.toml').write_text(
        "[project]\nname = 'fleasion'\nversion = '2.3.0'\n",
        encoding='utf-8',
    )

    monkeypatch.setattr(version, '__file__', str(fake_module_path))
    monkeypatch.setattr(version, '_read_installed_version', lambda: '0.1.0')

    assert version.read_version() == '2.3.0'


def test_unrelated_parent_pyproject_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
