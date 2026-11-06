"""Timed-out TLS callers cannot strand unbounded Windows scan work."""

from __future__ import annotations

import asyncio
import threading

import pytest

from fleasion.proxy.splice_keys import KeyProvider, MAX_PENDING_SCANS

_LABEL = 'CLIENT_TRAFFIC_SECRET_0'


def test_timeout_cancels_scan_waiting_for_a_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        provider = KeyProvider(linux_client_key='rosemary')
        setattr(provider, '_scan_slots', asyncio.Semaphore(1))
        entered = threading.Event()
        release = threading.Event()
        called: list[str] = []
        first, second = 'aa' * 32, 'bb' * 32

        def scan_one(random: str) -> dict[str, dict[str, bytes]]:
            called.append(random)
            entered.set()
            release.wait(timeout=2)
            return {random: {_LABEL: bytes.fromhex('cc' * 32)}}

        monkeypatch.setattr(provider, '_read', scan_one)
        task = asyncio.create_task(provider.get(first, _LABEL, wait_seconds=3))
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            with pytest.raises(TimeoutError):
                await provider.get(second, _LABEL, wait_seconds=0.06)
            await asyncio.sleep(0)
            assert second not in called
        finally:
            release.set()
        assert await task == bytes.fromhex('cc' * 32)
        await asyncio.sleep(0)
        assert called == [first]
        assert provider.pending_scan_count == 0
        provider.clear()

    asyncio.run(run())


def test_one_timeout_does_not_cancel_another_waiter(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        provider = KeyProvider(linux_client_key='rosemary')
        setattr(provider, '_scan_slots', asyncio.Semaphore(1))
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        blocked_random, target = '11' * 32, '22' * 32

        def scan_one(random: str) -> dict[str, dict[str, bytes]]:
            nonlocal calls
            calls += 1
            if random == blocked_random:
                entered.set()
                release.wait(timeout=2)
            return {random: {_LABEL: bytes.fromhex('cc' * 32)}}

        monkeypatch.setattr(provider, '_read', scan_one)
        blocking = asyncio.create_task(provider.get(blocked_random, _LABEL, wait_seconds=3))
        assert await asyncio.to_thread(entered.wait, 1)
        short = asyncio.create_task(provider.get(target, _LABEL, wait_seconds=0.06))
        long = asyncio.create_task(provider.get(target, _LABEL, wait_seconds=3))
        try:
            with pytest.raises(TimeoutError):
                await short
            assert provider.pending_scan_count <= 2
        finally:
            release.set()
        assert await long == bytes.fromhex('cc' * 32)
        assert await blocking == bytes.fromhex('cc' * 32)
        assert calls == 2
        provider.clear()

    asyncio.run(run())


def test_scan_queue_is_bounded_even_during_bursts(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        provider = KeyProvider(linux_client_key='rosemary')
        setattr(provider, '_scan_slots', asyncio.Semaphore(1))
        running = threading.Event()
        release = threading.Event()

        def scan_one(random: str) -> dict[str, dict[str, bytes]]:
            running.set()
            release.wait(timeout=2)
            return {random: {_LABEL: bytes.fromhex('cc' * 32)}}

        monkeypatch.setattr(provider, '_read', scan_one)
        # All tasks expire promptly; only the first running scan may linger
        tasks = [
            asyncio.create_task(provider.get(f'{number:064x}', _LABEL, wait_seconds=0.13))
            for number in range(1, 64)
        ]
        assert await asyncio.to_thread(running.wait, 1)
        assert provider.pending_scan_count <= MAX_PENDING_SCANS
        try:
            outcomes = await asyncio.gather(*tasks, return_exceptions=True)
            assert all(isinstance(item, TimeoutError) for item in outcomes)
        finally:
            release.set()
        await asyncio.sleep(0.15)
        assert provider.pending_scan_count == 0
        provider.clear()

    asyncio.run(run())


def test_replacement_waiter_does_not_inherit_cancelled_queued_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        provider = KeyProvider(linux_client_key='rosemary')
        setattr(provider, '_scan_slots', asyncio.Semaphore(1))
        entered = threading.Event()
        release = threading.Event()
        first, target = '33' * 32, '44' * 32
        calls: list[str] = []

        def scan_one(random: str) -> dict[str, dict[str, bytes]]:
            calls.append(random)
            if random == first:
                entered.set()
                release.wait(timeout=2)
            return {random: {_LABEL: bytes.fromhex('cc' * 32)}}

        monkeypatch.setattr(provider, '_read', scan_one)
        blocking = asyncio.create_task(provider.get(first, _LABEL, wait_seconds=3))
        assert await asyncio.to_thread(entered.wait, 1)
        try:
            with pytest.raises(TimeoutError):
                await provider.get(target, _LABEL, wait_seconds=0.03)
            replacement = asyncio.create_task(provider.get(target, _LABEL, wait_seconds=3))
        finally:
            release.set()
        assert await replacement == bytes.fromhex('cc' * 32)
        assert await blocking == bytes.fromhex('cc' * 32)
        assert calls == [first, target]
        provider.clear()

    asyncio.run(run())
