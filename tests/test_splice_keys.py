"""Unit tests for the bounded-lifetime splice key provider."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
import pytest

from fleasion.proxy.splice_keys import KeyProvider

if TYPE_CHECKING:
    from pathlib import Path


def test_key_provider_retries_missing_or_partial_keylog(tmp_path: Path) -> None:
    async def run() -> None:
        keylog = tmp_path / 'keys.log'
        provider = KeyProvider(keylog)
        client_random = 'ab' * 32

        async def publish() -> None:
            await asyncio.sleep(0.15)
            keylog.write_text(
                '# Test keys only\nmalformed line\n'
                f'CLIENT_TRAFFIC_SECRET_0 {client_random} not-hex\n'
                f'CLIENT_TRAFFIC_SECRET_0 {client_random} {"cd" * 32}\n',
                encoding='ascii',
            )

        task = asyncio.create_task(publish())
        assert (
            await provider.get(client_random, 'CLIENT_TRAFFIC_SECRET_0', wait_seconds=2)
            == bytes.fromhex('cd' * 32)
        )
        await task
        provider.clear()

    asyncio.run(run())


def test_key_provider_timeout_raises(tmp_path: Path) -> None:
    async def run() -> None:
        keylog = tmp_path / 'missing.log'
        provider = KeyProvider(keylog)
        with pytest.raises(TimeoutError):
            await provider.get('aa' * 32, 'CLIENT_TRAFFIC_SECRET_0', wait_seconds=0.1)
        provider.clear()

    asyncio.run(run())



def test_linux_splice_key_provider_scans_only_the_configured_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import splice_keys as keys

    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        keys, 'find_rosemary_pid',
        lambda: calls.append(('find-rosemary', None)) or 111,
    )
    monkeypatch.setattr(
        keys, 'find_sober_pid',
        lambda: calls.append(('find-sober', None)) or 222,
    )
    monkeypatch.setattr(
        keys, 'find_android_community_pid',
        lambda client=None: calls.append(('find-community', client)) or (333, 'mocktail'),
    )
    monkeypatch.setattr(
        keys, 'scan_rosemary',
        lambda pid, **_kwargs: calls.append(('scan-rosemary', pid)) or {'rosemary': {}},
    )
    monkeypatch.setattr(
        keys, 'scan_sober',
        lambda pid, **_kwargs: calls.append(('scan-sober', pid)) or {'sober': {}},
    )
    monkeypatch.setattr(
        keys, 'scan_android_community',
        lambda pid, **_kwargs: calls.append(('scan-mocktail', pid)) or {'mocktail': {}},
    )
    import sys
    assert sys.platform == 'linux'

    for selected, expected in [
        ('sober', [('find-sober', None), ('scan-sober', 222)]),
        ('mocktail', [('find-community', 'mocktail'), ('scan-mocktail', 333)]),
        ('rosemary', [('find-rosemary', None), ('scan-rosemary', 111)]),
    ]:
        calls.clear()
        provider = KeyProvider(linux_client_key=selected)
        result = provider._read('aa' * 32)
        assert selected in result
        assert calls == expected
        provider.clear()

    calls.clear()
    monkeypatch.setattr(keys, 'find_android_community_pid', lambda _client: None)
    assert KeyProvider(linux_client_key='mocktail')._read('aa' * 32) == {}
    assert calls == []
