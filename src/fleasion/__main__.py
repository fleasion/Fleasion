"""Fleasion application entry point."""

from __future__ import annotations

import importlib
import sys


def main() -> None:
    """Start Fleasion or its bundled Linux proxy helper."""
    if '--linux-proxy-helper' in sys.argv[1:]:
        sys.argv.remove('--linux-proxy-helper')
        from fleasion.linux_proxy_helper_daemon import (  # ruff: ignore[import-outside-top-level]
            main as run_linux_proxy_helper,
        )

        run_linux_proxy_helper()
        return

    # Load NumPy before PySide6 on Windows so its native DLLs initialize first
    if sys.platform == 'win32':
        importlib.import_module('numpy')

    from fleasion.app import run_application  # ruff: ignore[import-outside-top-level]

    run_application()


if __name__ == '__main__':
    main()
