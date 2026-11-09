from __future__ import annotations

import os
import sys
import tracemalloc
from typing import TYPE_CHECKING

import pytest

from fleasion.proxy import server

if TYPE_CHECKING:
    from pathlib import Path


def test_large_local_asset_range_does_not_buffer_the_whole_file(tmp_path: Path) -> None:
    path = tmp_path / 'large.ogg'
    with path.open('wb') as output:
        output.seek(16 * 1024 * 1024)
        output.write(b'end')
    tracemalloc.start()
    try:
        response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
            str(path), b'bytes=16777216-16777218'
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.endswith(b'\r\n\r\nend')
    assert b'Content-Range: bytes 16777216-16777218/16777219\r\n' in response
    assert peak < 1024 * 1024


def test_range_for_metadata_wrapped_asset_uses_unwrapped_offsets(tmp_path: Path) -> None:
    path = tmp_path / 'asset.bin'
    path.write_bytes(b'prefix<roblox>payload</roblox>')
    response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
        str(path), b'bytes=0-6'
    )
    assert response.endswith(b'\r\n\r\n<roblox')
    assert b'Content-Range: bytes 0-6/24\r\n' in response


def test_small_range_uses_the_regular_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'small.ogg'
    path.write_bytes(b'abc' * (128 * 1024 // 3))

    def unexpected_fast_path(*_args: object, **_kwargs: object) -> None:
        pytest.fail('Small files should use the regular reader')

    monkeypatch.setattr(server, '_serve_local_file_range', unexpected_fast_path)
    response = server._serve_local_file(str(path), b'bytes=0-2')  # pyright: ignore[reportPrivateUsage]
    assert response.endswith(b'\r\n\r\nabc')


def test_changed_file_falls_back_to_fresh_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'changing.ogg'
    path.write_bytes(b'x' * (512 * 1024))
    real_fstat = server.os.fstat
    checks = 0

    def changing_fstat(fd: int) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            path.write_bytes(b'new content')
        return real_fstat(fd)

    monkeypatch.setattr(server.os, 'fstat', changing_fstat)
    response = server._serve_local_file(str(path), b'bytes=0-2')  # pyright: ignore[reportPrivateUsage]
    assert response.endswith(b'\r\n\r\nnew')
    assert b'Content-Range: bytes 0-2/11\r\n' in response


def test_file_growth_rechecks_unsatisfiable_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'growing.ogg'
    path.write_bytes(b'x' * (512 * 1024))
    real_fstat = server.os.fstat
    checks = 0

    def growing_fstat(fd: int) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            path.write_bytes(b'y' * (1024 * 1024))
        return real_fstat(fd)

    monkeypatch.setattr(server.os, 'fstat', growing_fstat)
    response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
        str(path), b'bytes=600000-600002'
    )
    assert response.endswith(b'\r\n\r\nyyy')
    assert b'Content-Range: bytes 600000-600002/1048576\r\n' in response


def test_same_size_file_edit_rechecks_modification_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / 'edited.ogg'
    size = 512 * 1024
    path.write_bytes(b'x' * size)
    timestamp = path.stat().st_mtime_ns
    real_fstat = server.os.fstat
    checks = 0

    def editing_fstat(fd: int) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            path.write_bytes(b'y' * size)
            os.utime(path, ns=(timestamp - 1_000_000_000, timestamp - 1_000_000_000))
        return real_fstat(fd)

    monkeypatch.setattr(server.os, 'fstat', editing_fstat)
    response = server._serve_local_file(str(path), b'bytes=0-2')  # pyright: ignore[reportPrivateUsage]
    assert response.endswith(b'\r\n\r\nyyy')


def test_head_of_large_local_asset_keeps_headers_without_loading_body(tmp_path: Path) -> None:
    path = tmp_path / 'large.ogg'
    with path.open('wb') as output:
        output.truncate(16 * 1024 * 1024)
    tracemalloc.start()
    try:
        response = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
            str(path), head_only=True
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.endswith(b'\r\n\r\n')
    assert b'Content-Length: 16777216\r\n' in response
    assert peak < 1024 * 1024


def test_head_rechecks_changed_file_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / 'changing.ogg'
    path.write_bytes(b'x' * (512 * 1024))
    real_fstat = server.os.fstat
    checks = 0

    def changing_fstat(fd: int) -> object:
        nonlocal checks
        checks += 1
        if checks == 2:
            path.write_bytes(b'new content')
        return real_fstat(fd)

    monkeypatch.setattr(server.os, 'fstat', changing_fstat)
    head = server._serve_local_file(str(path), head_only=True)  # pyright: ignore[reportPrivateUsage]
    assert head.endswith(b'\r\n\r\n')
    assert b'Content-Length: 11\r\n' in head


def test_head_matches_get_headers_after_metadata_removal(tmp_path: Path) -> None:
    path = tmp_path / 'asset.bin'
    path.write_bytes(b'prefix<roblox>payload</roblox>')
    response = server._serve_local_file(str(path))  # pyright: ignore[reportPrivateUsage]
    head = server._serve_local_file(  # pyright: ignore[reportPrivateUsage]
        str(path), head_only=True
    )
    assert head == response.partition(b'\r\n\r\n')[0] + b'\r\n\r\n'


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux procfs')
def test_virtual_file_with_reported_zero_size_uses_actual_content(tmp_path: Path) -> None:
    path = tmp_path / 'process.png'
    path.symlink_to('/proc/self/cmdline')
    content = path.read_bytes()
    assert content and path.stat().st_size == 0

    response = server._serve_local_file(str(path), b'bytes=0-4')  # pyright: ignore[reportPrivateUsage]
    assert response.startswith(b'HTTP/1.1 206 Partial Content\r\n')
    assert response.endswith(b'\r\n\r\n' + content[:5])
    head = server._serve_local_file(str(path), head_only=True)  # pyright: ignore[reportPrivateUsage]
    assert f'Content-Length: {len(content)}\r\n'.encode() in head
