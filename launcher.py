"""PyInstaller and Qt for Python project entry point."""

from __future__ import annotations

import sys


def main() -> None:
    """Dispatch to the requested Fleasion runtime."""
    if '--linux-proxy-helper' in sys.argv[1:]:
        sys.argv.remove('--linux-proxy-helper')
        from fleasion import linux_proxy_helper_daemon

        linux_proxy_helper_daemon.main()
        return

    # Load NumPy before PySide6 on Windows. Some frozen Windows builds otherwise
    # fail while initializing NumPy's native DLLs after Qt has already loaded.
    if sys.platform == 'win32':
        import numpy  # noqa: F401

    from fleasion import main as qml_main

    qml_main()


if __name__ == '__main__':
    main()
