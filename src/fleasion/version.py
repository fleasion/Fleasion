"""Project version helpers."""

from __future__ import annotations

import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_DISTRIBUTION_NAME = 'Fleasion'
_UNKNOWN_VERSION = '0.0.0'


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

    pyproject = tomllib.loads(pyproject_path.read_text(encoding='utf-8'))
    project = pyproject.get('project')
    if not isinstance(project, dict) or project.get('name') != _DISTRIBUTION_NAME:
        return None

    project_version = project.get('version')
    return project_version if isinstance(project_version, str) and project_version else None


def _read_installed_version() -> str | None:
    try:
        return version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return None


def read_version() -> str:
    if getattr(sys, 'frozen', False):
        return _read_installed_version() or _UNKNOWN_VERSION

    return _read_pyproject_version() or _read_installed_version() or _UNKNOWN_VERSION
