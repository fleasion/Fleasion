import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest


def _find_ktx_dll(module: object) -> str | None:
    callback = cast('Callable[[], str | None]', vars(module)['_find_ktx_dll'])
    return callback()


def test_frozen_ktx_loader_does_not_search_executable_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.cache.tools.ktx_to_png import ktx_to_png

    source_dir = tmp_path / 'source'
    meipass = tmp_path / '_MEI54321'
    exe_dir = tmp_path / 'downloads'
    source_dir.mkdir()
    meipass.mkdir()
    exe_dir.mkdir()
    exe_dll = exe_dir / 'ktx.dll'
    exe_dll.write_bytes(b"not Fleasion's bundled DLL")

    monkeypatch.setattr(ktx_to_png, '__file__', str(source_dir / 'ktx_to_png.py'))
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, '_MEIPASS', str(meipass), raising=False)
    monkeypatch.setattr(sys, 'executable', str(exe_dir / 'Fleasion.exe'))

    assert _find_ktx_dll(ktx_to_png) is None

    bundled_dll = meipass / 'ktx.dll'
    bundled_dll.write_bytes(b'bundled DLL')

    assert _find_ktx_dll(ktx_to_png) == str(bundled_dll)
