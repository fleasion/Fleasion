"""Build the standalone Fleasion application."""

from __future__ import annotations

import argparse
import importlib
import logging
import os
import subprocess
import sys
from pathlib import Path

from ._logger import setup_script_logging
from ._pyinstaller import run_pyinstaller

log = logging.getLogger(__name__)


CLEAN_BUILD_ENV = 'FLEASION_CLEAN_BUILD'
MACOS_SLICE_BUILD_ENV = 'FLEASION_MACOS_SLICE_BUILD'
REPRODUCIBLE_ENV = {
    'PYTHONHASHSEED': '0',
    'SOURCE_DATE_EPOCH': '0',
    'LC_ALL': 'C.UTF-8',
    'TZ': 'UTC',
}


class _BuildArgumentParser(argparse.ArgumentParser):
    """Argument parser for the standalone build command."""

    def __init__(self) -> None:
        super().__init__(description='Build the standalone Fleasion application.')
        self.add_argument(
            '--clean',
            action='store_true',
            help='discard PyInstaller caches and temporary build files',
        )


def main(arguments: list[str] | None = None) -> int:
    """Build Fleasion with its PyInstaller specification."""
    options = _BuildArgumentParser().parse_args(arguments)
    setup_script_logging()

    # Environment changes such as PYTHONHASHSEED only apply after an interpreter restart
    if any(os.environ.get(name) != value for name, value in REPRODUCIBLE_ENV.items()):
        environment = os.environ.copy()
        environment.update(REPRODUCIBLE_ENV)

        command = [sys.executable, '-m', 'fleasion.scripts.build']
        if options.clean:
            command.append('--clean')

        log.info('Restarting build with reproducible environment')
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
            command, cwd=Path.cwd(), env=environment, check=False, shell=False
        )
        return result.returncode

    os.environ[CLEAN_BUILD_ENV] = '1' if options.clean else '0'

    # Build macOS
    # Slice subprocesses bypass orchestration and run PyInstaller exactly once
    if sys.platform == 'darwin' and os.environ.get(MACOS_SLICE_BUILD_ENV) != '1':
        macos_build = importlib.import_module('.macos_build', __package__)
        macos_build.build_macos_release()
        return 0
    # Build Windows and Linux
    pyinstaller_arguments = ['--noconfirm', 'Fleasion.spec']
    if options.clean:
        pyinstaller_arguments.insert(0, '--clean')

    log.info('Building Fleasion from %s', Path.cwd())
    run_pyinstaller(pyinstaller_arguments, skip_setup_logging=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
