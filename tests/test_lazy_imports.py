"""Python 3.15 lazy imports keep resolving after PySide6 is loaded."""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

_SCRIPT = """
import asyncio, concurrent.futures
import fleasion
import PySide6.QtCore

async def main():
    await asyncio.get_running_loop().run_in_executor(None, lambda: None)

asyncio.run(main())
print('ok')
"""


@pytest.mark.skipif(
    not hasattr(types, 'LazyImportType'), reason='lazy imports need Python 3.15+'
)
def test_default_executor_works_after_pyside6_is_imported() -> None:
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, '-c', _SCRIPT], capture_output=True, text=True, timeout=120, check=False
    )
    assert result.stdout.strip() == 'ok', result.stderr


def test_workaround_is_harmless_without_lazy_imports() -> None:
    from fleasion.lazy_imports import make_imports_eager

    make_imports_eager()
    make_imports_eager()
