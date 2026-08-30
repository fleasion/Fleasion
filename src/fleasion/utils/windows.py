"""Compatibility wrapper for platform-specific desktop utilities.

Historically Fleasion imported process/cache/launch helpers from this module.
Keep that import path stable while dispatching to the current OS backend.
"""

from __future__ import annotations

import sys

if sys.platform == 'win32':
    from .platform_windows import *  # ruff: ignore[undefined-local-with-import-star]
elif sys.platform == 'darwin':
    from .platform_macos import *  # ruff: ignore[undefined-local-with-import-star]
elif sys.platform.startswith('linux'):
    from .platform_linux import *  # ruff: ignore[undefined-local-with-import-star]
else:
    msg = 'Fleasion supports Windows, macOS, and Linux only.'
    raise RuntimeError(msg)
