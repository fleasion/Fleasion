"""Diagnostics for bounded TLS key lookup queueing and scan work."""

from __future__ import annotations

import asyncio
import time

import pytest

from fleasion.proxy import splice_keys as keys
from fleasion.proxy.splice_keys import KeyProvider


def test_slow_tls_key_diagnostics_separate_queue_and_scan_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages: list[str] = []
    def capture(_category: str, message: str) -> None:
        messages.append(message)

    monkeypatch.setattr(keys.log_buffer, 'log', capture)
    provider = KeyProvider(linux_client_key='rosemary')
    provider._scan_slots = asyncio.Semaphore(1)  # pyright: ignore[reportPrivateUsage]
    label = 'CLIENT_TRAFFIC_SECRET_0'
    first, second = 'aa' * 32, 'bb' * 32

    def scan_one(random: str) -> dict[str, dict[str, bytes]]:
        time.sleep(0.65)
        return {random: {label: bytes(32)}}

    monkeypatch.setattr(provider, '_read', scan_one)

    async def run() -> None:
        values = await asyncio.gather(
            provider.get(first, label, wait_seconds=3),
            provider.get(second, label, wait_seconds=3),
        )
        assert values == [bytes(32), bytes(32)]
        provider.clear()

    asyncio.run(run())
    queued = [
        message for message in messages
        if 'queue_ms=' in message and int(message.split('queue_ms=', 1)[1].split()[0]) >= 400
    ]
    assert queued
    assert queued[0].startswith('Slow TLS key lookup: client=rosemary ')
    fields = dict(field.split('=', 1) for field in queued[0].split() if '=' in field)
    assert int(fields['queue_ms']) >= 400
    assert int(fields['scan_ms']) >= 500
    assert int(fields['elapsed_ms']) >= int(fields['scan_ms'])
    assert fields['found'] == 'True'
