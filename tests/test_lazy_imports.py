from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import sys
import types
from typing import TYPE_CHECKING

from fleasion.lazy_imports import module_attribute

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_thread_pool_executor_survives_pyside_attribute_hook() -> None:
    importlib.import_module('PySide6.QtCore')

    async def run() -> str:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: 'ok')

    assert isinstance(concurrent.futures.ThreadPoolExecutor, type)
    assert asyncio.run(run()) == 'ok'


def test_module_attribute_resolves_and_stores_lazy_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / 'lazy_binding_fixture.py').write_text('lazy import json\n', encoding='utf-8')
    monkeypatch.setattr(sys, 'path', [str(tmp_path), *sys.path])
    monkeypatch.delitem(sys.modules, 'lazy_binding_fixture', raising=False)
    module = importlib.import_module('lazy_binding_fixture')

    assert type(vars(module)['json']) is types.LazyImportType
    assert module_attribute(module, 'json') is importlib.import_module('json')
    assert vars(module)['json'] is importlib.import_module('json')
