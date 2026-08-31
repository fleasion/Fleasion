"""Project version helpers."""

from __future__ import annotations

import argparse
import os
import re
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version as distribution_version
from pathlib import Path
from typing import TYPE_CHECKING

from packaging.version import InvalidVersion, Version

_DISTRIBUTION_NAME = 'fleasion'
_UNKNOWN_VERSION = '0.0.0'
_GITHUB_SHA_PATTERN = re.compile(r'[0-9a-fA-F]{7,64}')


if TYPE_CHECKING:

    from collections.abc import Mapping, Sequence
    def _object_dict(value: object) -> dict[str, object] | None: ...
else:

    def _object_dict(value: object) -> dict[str, object] | None:
        return value if isinstance(value, dict) else None


def read_project_version(pyproject_path: Path = Path('pyproject.toml')) -> str:
    """Read and validate the canonical version from a project file."""
    pyproject_value: object = tomllib.loads(pyproject_path.read_text(encoding='utf-8'))
    pyproject = _object_dict(pyproject_value)
    project = _object_dict(pyproject.get('project')) if pyproject is not None else None
    if project is None or project.get('name') != _DISTRIBUTION_NAME:
        msg = f'{pyproject_path} does not describe the {_DISTRIBUTION_NAME} project.'
        raise ValueError(msg)

    project_version = project.get('version')
    if not isinstance(project_version, str) or not project_version:
        msg = f'{pyproject_path} does not contain a project version.'
        raise ValueError(msg)

    Version(project_version)
    return project_version


def build_artifact_version(
    app_version: str,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Return the filename version for a packaged application build."""
    parsed = Version(app_version)
    if parsed.local is not None:
        msg = 'The canonical project version must not contain local metadata.'
        raise ValueError(msg)
    if not (parsed.is_prerelease or parsed.is_devrelease):
        return app_version

    build_environment = os.environ if environment is None else environment
    if build_environment.get('GITHUB_ACTIONS') != 'true':
        return f'{app_version}+local'

    github_sha = build_environment.get('GITHUB_SHA', '')
    if _GITHUB_SHA_PATTERN.fullmatch(github_sha) is None:
        msg = (
            'GITHUB_SHA must contain between 7 and 64 hexadecimal characters '
            'when GITHUB_ACTIONS=true.'
        )
        raise ValueError(msg)
    return f'{app_version}+g{github_sha[:7].lower()}'


def macos_bundle_version(app_version: str) -> str:
    """Return an Apple-compatible three-component numeric bundle version."""
    release = Version(app_version).release
    if len(release) > 3:
        msg = 'macOS bundle versions support at most three release components.'
        raise ValueError(msg)
    components = (*release, *(0 for _ in range(3 - len(release))))
    return '.'.join(str(component) for component in components)


def _read_pyproject_version() -> str | None:
    module_path = Path(__file__).resolve()
    try:
        project_root = module_path.parents[2]
    except IndexError:
        return None

    source_module_path = project_root / 'src' / 'fleasion' / module_path.name
    if source_module_path.resolve() != module_path:
        return None

    pyproject_path = project_root / 'pyproject.toml'
    if not pyproject_path.is_file():
        return None

    try:
        return read_project_version(pyproject_path)
    except InvalidVersion, ValueError:
        return None


def _read_installed_version() -> str | None:
    try:
        return distribution_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def read_version() -> str:
    if getattr(sys, 'frozen', False):
        return _read_installed_version() or _UNKNOWN_VERSION

    return _read_pyproject_version() or _read_installed_version() or _UNKNOWN_VERSION


def _cli(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Resolve Fleasion build versions.')
    parser.add_argument('--artifact-version', metavar='VERSION', required=True)
    options = parser.parse_args(arguments)
    try:
        resolved_version = build_artifact_version(options.artifact_version)
    except (InvalidVersion, ValueError) as exc:
        parser.error(str(exc))
    print(resolved_version)
    return 0


if __name__ == '__main__':
    raise SystemExit(_cli())
