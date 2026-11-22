from __future__ import annotations

import asyncio
import concurrent.futures
import importlib


def test_thread_pool_executor_survives_pyside_attribute_hook() -> None:
    # Importing the package applies the startup fix under test
    importlib.import_module('fleasion.lazy_imports')
    importlib.import_module('PySide6.QtCore')

    async def run() -> str:
        return await asyncio.get_running_loop().run_in_executor(None, lambda: 'ok')

    assert isinstance(concurrent.futures.ThreadPoolExecutor, type)
    assert asyncio.run(run()) == 'ok'
