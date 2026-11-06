"""TexturePack canonical promotion does not read entire existing mip payloads."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from fleasion.cache import cache_manager as cache_manager_module
from fleasion.proxy.addons.cache_scraper import CacheScraper

if TYPE_CHECKING:
    from typing import IO


def _fake_ktx2(width: int, marker: bytes) -> bytes:
    body = bytearray(80 + len(marker))
    body[:12] = b'\xabKTX 20\xbb\r\n\x1a\n'
    struct.pack_into('<II', body, 20, width, width)
    struct.pack_into('<I', body, 40, 1)
    body[80:] = marker
    return bytes(body)


def test_canonical_reads_only_header_when_resolution_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_manager_module, 'CONFIG_DIR', tmp_path)
    manager = cache_manager_module.CacheManager()
    scraper = CacheScraper(manager)
    target = manager.get_texturepack_slot_path(123456789, 0)
    original_open = Path.open
    read_sizes: list[int] = []

    class TracedFile:
        def __init__(self, real_file: IO[bytes]) -> None:
            self.real_file = real_file

        def __enter__(self) -> TracedFile:
            return self

        def __exit__(self, *_args: object) -> None:
            self.real_file.close()

        def read(self, size: int = -1) -> bytes:
            read_sizes.append(size)
            return self.real_file.read(size)

    def traced_open(path: Path, mode: str = 'r', *args: object, **kwargs: object) -> object:
        file = original_open(path, mode)
        if path == target and mode == 'rb':
            return TracedFile(file)
        return file

    try:
        method = getattr(scraper, '_store_texpack_slot_ktx2_unchecked')
        mid = _fake_ktx2(256, b'initial')
        method(123456789, 0, 1, mid)
        monkeypatch.setattr(Path, 'open', traced_open)

        method(123456789, 0, 2, _fake_ktx2(32, b'small'))
        assert read_sizes == [28]
        assert target.read_bytes() == mid

        read_sizes.clear()
        big = _fake_ktx2(1024, b'larger')
        method(123456789, 0, 3, big)
        assert read_sizes == [28]
        assert target.read_bytes() == big

        read_sizes.clear()
        equal = _fake_ktx2(1024, b'equal-new-payload')
        method(123456789, 0, 4, equal)
        assert read_sizes == [28, -1]
        assert target.read_bytes() == equal

        read_sizes.clear()
        method(123456789, 0, 4, equal)
        assert read_sizes == [28, -1]
        assert target.read_bytes() == equal
    finally:
        getattr(scraper, '_executor').shutdown(wait=True, cancel_futures=True)
