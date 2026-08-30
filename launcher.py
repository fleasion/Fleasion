"""PyInstaller entry point."""

import sys

if '--linux-proxy-helper' in sys.argv[1:]:
    sys.argv.remove('--linux-proxy-helper')
    from fleasion import linux_proxy_helper_daemon

    linux_proxy_helper_daemon.main()
else:
    # Load NumPy before PySide6 on Windows. Some frozen Windows builds otherwise
    # fail while initializing NumPy's native DLLs after Qt has already loaded.
    if sys.platform == 'win32':
        import numpy  # ruff: ignore[unused-import, unconventional-import-alias]

    from fleasion import main

    main()
