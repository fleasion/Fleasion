"""Keep Python 3.15 lazy imports working once PySide6 is loaded."""

from __future__ import annotations

import contextlib
import sys
import types
from typing import cast


def _never_lazy(*_args: object) -> bool:
    return False


def make_imports_eager() -> None:
    """Import eagerly and resolve names that were already imported lazily.

    PySide6 6.12 on Python 3.15 stops lazily imported names from resolving when
    they are read as module attributes. asyncio's default executor then finds
    an unresolved ``concurrent.futures.ThreadPoolExecutor`` and every
    ``run_in_executor(None, ...)`` call fails. This must run before PySide6 is
    imported; it does nothing on Pythons without lazy imports.
    """
    set_filter = getattr(sys, 'set_lazy_imports_filter', None)
    lazy_type = getattr(types, 'LazyImportType', None)
    if set_filter is None or lazy_type is None:
        return
    set_filter(_never_lazy)
    for module in list(sys.modules.values()):
        namespace: object = getattr(module, '__dict__', None)
        if not isinstance(namespace, dict):
            continue
        for name, value in list(cast('dict[str, object]', namespace).items()):
            if isinstance(value, lazy_type):
                with contextlib.suppress(Exception):
                    getattr(module, name)


# Applied on import, which fleasion/__init__.py does before anything loads PySide6
make_imports_eager()
