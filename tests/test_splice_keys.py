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
