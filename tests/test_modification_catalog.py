"""Tests for the presentation-independent built-in modification catalog."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from fleasion.modifications.catalog import (
    built_in_modifications,
    detect_modification_source,
)


def test_catalog_contains_every_legacy_builtin_workflow():
    entries = built_in_modifications()
    categories = {entry.category for entry in entries}

    assert categories == {
        'skybox',
        'indoor_skybox',
        'textures',
        'avatar_meshes',
        'sounds',
        'fonts',
    }
    assert sum(entry.category == 'skybox' for entry in entries) == 6
    assert sum(entry.category == 'indoor_skybox' for entry in entries) == 6
    assert sum(entry.key.startswith('r6-head-') for entry in entries) == 16
    assert all(entry.mute_source for entry in entries if entry.category == 'sounds')
    assert next(entry for entry in entries if entry.key == 'custom-font').is_font
    assert len({entry.key for entry in entries}) == len(entries)
    assert len({entry.target_path.casefold() for entry in entries}) == len(entries)


def test_source_detection_supports_local_asset_url_and_remove(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'fleasion.utils.http.socket.getaddrinfo',
        lambda _host, port, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                '',
                ('8.8.8.8', port),
            )
        ],
    )
    local_file = tmp_path / 'cursor.png'
    local_file.write_bytes(b'image')

    assert detect_modification_source('content/cursor.png', str(local_file)) == (
        'local_file',
        str(local_file),
    )
    assert detect_modification_source('content/cursor.png', '12345') == (
        'asset_id',
        '12345',
    )
    assert detect_modification_source(
        'content/cursor.png', 'https://cdn.example.com/cursor.png'
    ) == ('cdn_url', 'https://cdn.example.com/cursor.png')
    assert detect_modification_source('content/sounds/oof.ogg', 'remove') == (
        'bundled',
        'bundled:empty.ogg',
    )


@pytest.mark.parametrize(
    'value',
    (
        'http://localhost/file.bin',
        'http://127.0.0.1/file.bin',
        'http://10.0.0.1/file.bin',
        'http://user:password@example.com/file.bin',
        'bundled:../../secret',
    ),
)
def test_source_detection_rejects_unsafe_sources(value: str):
    with pytest.raises((FileNotFoundError, ValueError)):
        detect_modification_source('content/example.bin', value)


def test_source_detection_rejects_hostname_resolving_to_loopback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        'fleasion.utils.http.socket.getaddrinfo',
        lambda _host, port, **_kwargs: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                '',
                ('127.0.0.1', port),
            )
        ],
    )

    with pytest.raises(ValueError, match='non-global'):
        detect_modification_source(
            'content/example.bin',
            'https://public-looking.example/file.bin',
        )
