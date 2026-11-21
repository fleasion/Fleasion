"""Keep Python 3.15 lazy imports (PEP 810) working alongside PySide6.

Shiboken replaces the module type's attribute lookup to create Qt classes on
demand, and that replacement returns a pending lazy import object as is,
without resolving it. Lazy names bound in another module then break as soon
as PySide6 is loaded: ``concurrent.futures.ThreadPoolExecutor`` is a
``lazy from`` import in 3.15, so asyncio's default executor fails with
"'lazy_import' object is not callable". Global lookups inside the binding
module and ``from module import name`` still resolve normally, so only
attribute access across modules needs care.
"""

from __future__ import annotations

import concurrent.futures
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

__all__ = ['module_attribute']

_LAZY_IMPORT_TYPE: type | None = getattr(types, 'LazyImportType', None)


def module_attribute(module: ModuleType, name: str) -> object:
    """Return a module global, resolving it first if it is a pending lazy import.

    ``vars(module)[name]`` never resolves lazy imports, and attribute access
    does not either once PySide6 is loaded.
    """
    namespace = vars(module)
    value = namespace[name]
    if _LAZY_IMPORT_TYPE is None or type(value) is not _LAZY_IMPORT_TYPE:
        return value
    resolve = getattr(_LAZY_IMPORT_TYPE, 'resolve')  # ruff: ignore[get-attr-with-constant]
    resolved = resolve(value)
    namespace[name] = resolved
    return resolved


# Public standard library names that are lazy in 3.15 and used across modules.
# ProcessPoolExecutor is lazy too, but resolving it imports multiprocessing
module_attribute(concurrent.futures, 'ThreadPoolExecutor')
