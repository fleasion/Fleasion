"""Run PyInstaller with Fleasion's console logging configuration."""

from __future__ import annotations

from ._logger import setup_script_logging


def run_pyinstaller(
    arguments: list[str] | None = None, *, skip_setup_logging: bool = False
) -> None:
    """Configure logging and run PyInstaller in the current process."""
    if not skip_setup_logging:
        setup_script_logging()

    from PyInstaller.__main__ import run  # ruff: ignore[import-outside-top-level]

    run(arguments)


def main() -> None:
    run_pyinstaller()


if __name__ == '__main__':
    main()
