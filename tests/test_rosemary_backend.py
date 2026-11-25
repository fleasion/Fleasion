"""Unit tests for RosemaryBackend TLS transport provider."""

from __future__ import annotations

import sys

import pytest

from fleasion.proxy.tls_backends import (
    BackendId,
    BackendStatus,
    BackendUnavailableError,
    create_backend,
)
from fleasion.proxy.tls_backends.rosemary import RosemaryBackend


def test_rosemary_backend_id() -> None:
    backend = RosemaryBackend()
    assert backend.backend_id is BackendId.ROSEMARY


def test_rosemary_probe_not_implemented_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    backend = RosemaryBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.ROSEMARY
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'requires Linux' in probe.reason
    assert not probe.ready


def test_rosemary_probe_process_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('fleasion.proxy.tls_backends.rosemary.find_rosemary_pid', lambda: None)

    backend = RosemaryBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.ROSEMARY
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'process not found' in probe.reason
    assert not probe.ready


def test_rosemary_probe_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr('fleasion.proxy.tls_backends.rosemary.find_rosemary_pid', lambda: 2147728)

    backend = RosemaryBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.ROSEMARY
    assert probe.status is BackendStatus.READY
    assert 'active (PID 2147728)' in probe.reason
    assert probe.ready


def test_rosemary_open_session_not_ready_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr('fleasion.proxy.tls_backends.rosemary.find_rosemary_pid', lambda: None)

        backend = RosemaryBackend()
        with pytest.raises(BackendUnavailableError, match='process not found'):
            await backend.open_session('assetdelivery.roblox.com')

    import asyncio
    asyncio.run(run())


def test_rosemary_open_session_ready_raises_tunnel(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr('fleasion.proxy.tls_backends.rosemary.find_rosemary_pid', lambda: 2147728)

        backend = RosemaryBackend()
        with pytest.raises(BackendUnavailableError, match='requires an active tunnel'):
            await backend.open_session('assetdelivery.roblox.com')

    import asyncio
    asyncio.run(run())


def test_registry_creates_rosemary_backend() -> None:
    backend = create_backend(BackendId.ROSEMARY)
    assert isinstance(backend, RosemaryBackend)
