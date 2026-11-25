"""Keep Python 3.15 lazy imports (PEP 810) working alongside PySide6.

Shiboken replaces the module type's attribute lookup to create Qt classes on
demand, and that replacement returns a pending lazy import object as is,
without resolving it. Lazy names bound in another module then break as soon
as PySide6 is loaded: ``concurrent.futures.ThreadPoolExecutor`` is a
``lazy from`` import in 3.15, so asyncio's default executor fails with
"'lazy_import' object is not callable". Global lookups inside the binding
module and ``from module import name`` still resolve normally, so only
attribute access across modules needs care.

Importing this module resolves the affected standard library names up front.
"""

from __future__ import annotations

import concurrent.futures
import types
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

__all__: list[str] = []


def _resolve_lazy_global(module: ModuleType, name: str) -> None:
    """Replace a pending lazy import in a module's namespace with its value.

    ``vars(module)`` never resolves lazy imports, so this reads and writes the
    namespace directly.
    """
    namespace = vars(module)
    value = namespace[name]
    if type(value) is types.LazyImportType:
        namespace[name] = value.resolve()


# Public standard library names that are lazy in 3.15 and used across modules.
# ProcessPoolExecutor is lazy too, but resolving it imports multiprocessing
_resolve_lazy_global(concurrent.futures, 'ThreadPoolExecutor')
