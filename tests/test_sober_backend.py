"""Unit tests for SoberBackend TLS transport provider."""

from __future__ import annotations

import asyncio
import sys

import pytest

from fleasion.proxy.tls_backends import (
    BackendId,
    BackendStatus,
    BackendUnavailableError,
    create_backend,
)
from fleasion.proxy.tls_backends.sober import SoberBackend


def test_sober_backend_id() -> None:
    backend = SoberBackend()
    assert backend.backend_id is BackendId.SOBER


def test_sober_probe_not_implemented_on_non_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    backend = SoberBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.SOBER
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'requires Linux' in probe.reason
    assert not probe.ready


def test_sober_probe_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(
        'fleasion.proxy.tls_backends.sober.find_sober_pid',
        lambda: None,
    )

    backend = SoberBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.SOBER
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'Sober Roblox process not found' in probe.reason
    assert not probe.ready


def test_sober_probe_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(
        'fleasion.proxy.tls_backends.sober.find_sober_pid',
        lambda: 2231734,
    )

    backend = SoberBackend()
    probe = backend.probe()
    assert probe.backend is BackendId.SOBER
    assert probe.status is BackendStatus.READY
    assert 'active (PID 2231734)' in probe.reason
    assert probe.ready


def test_sober_open_session_not_ready_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr(
            'fleasion.proxy.tls_backends.sober.find_sober_pid',
            lambda: None,
        )

        backend = SoberBackend()
        with pytest.raises(BackendUnavailableError, match='Sober Roblox process not found'):
            await backend.open_session('assetdelivery.roblox.com')

    asyncio.run(run())


def test_sober_open_session_ready_raises_tunnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr(
            'fleasion.proxy.tls_backends.sober.find_sober_pid',
            lambda: 2231734,
        )

        backend = SoberBackend()
        with pytest.raises(BackendUnavailableError, match='live session handoff requires an active tunnel'):
            await backend.open_session('assetdelivery.roblox.com')

    asyncio.run(run())


def test_create_backend_sober() -> None:
    backend = create_backend(BackendId.SOBER)
    assert isinstance(backend, SoberBackend)
    assert backend.backend_id is BackendId.SOBER
