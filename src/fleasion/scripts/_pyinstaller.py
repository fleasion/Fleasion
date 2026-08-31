"""Run PyInstaller with Fleasion's console logging configuration."""

from __future__ import annotations

import importlib

from ._logger import setup_script_logging


def run_pyinstaller(
    arguments: list[str] | None = None, *, skip_setup_logging: bool = False
) -> None:
    """Configure logging and run PyInstaller in the current process."""
    if not skip_setup_logging:
        setup_script_logging()

    pyinstaller_main = importlib.import_module('PyInstaller.__main__')
    pyinstaller_main.run(arguments)


def main() -> None:
    run_pyinstaller()


if __name__ == '__main__':
    main()
