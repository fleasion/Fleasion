"""Unit tests for AndroidCommunityBackend TLS transport provider."""

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
from fleasion.proxy.tls_backends.android_community import (
    ANDROID_COMMUNITY_CLIENTS,
    AndroidCommunityBackend,
)


def _mock_no_android(_client: str | None = None) -> None:
    return None


def _mock_mocktail(client: str | None = None) -> tuple[int, str]:
    return 2214008, client or 'mocktail'


def test_android_community_backend_id() -> None:
    backend = AndroidCommunityBackend('mocktail')
    assert backend.backend_id is BackendId.ANDROID_COMMUNITY
    assert backend.client == 'mocktail'


def test_android_community_rejects_unknown_runtime() -> None:
    with pytest.raises(ValueError, match='Unknown community Android runtime'):
        AndroidCommunityBackend('unsupported_runtime')


def test_android_community_probe_not_implemented_on_non_linux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, 'platform', 'win32')
    backend = AndroidCommunityBackend('mocktail')
    probe = backend.probe()
    assert probe.backend is BackendId.ANDROID_COMMUNITY
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'requires Linux' in probe.reason
    assert not probe.ready


def test_android_community_probe_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(
        'fleasion.proxy.tls_backends.android_community.find_android_community_pid',
        _mock_no_android,
    )

    backend = AndroidCommunityBackend('cordial')
    probe = backend.probe()
    assert probe.backend is BackendId.ANDROID_COMMUNITY
    assert probe.status is BackendStatus.NOT_IMPLEMENTED
    assert 'cordial process not found' in probe.reason
    assert not probe.ready


def test_android_community_probe_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, 'platform', 'linux')
    monkeypatch.setattr(
        'fleasion.proxy.tls_backends.android_community.find_android_community_pid',
        _mock_mocktail,
    )

    backend = AndroidCommunityBackend('mocktail')
    probe = backend.probe()
    assert probe.backend is BackendId.ANDROID_COMMUNITY
    assert probe.status is BackendStatus.READY
    assert 'active (PID 2214008)' in probe.reason
    assert probe.ready


def test_android_community_open_session_not_ready_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr(
            'fleasion.proxy.tls_backends.android_community.find_android_community_pid',
            _mock_no_android,
        )

        backend = AndroidCommunityBackend('cordial')
        with pytest.raises(BackendUnavailableError, match='cordial process not found'):
            await backend.open_session('assetdelivery.roblox.com')

    asyncio.run(run())


def test_android_community_open_session_ready_raises_tunnel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        monkeypatch.setattr(sys, 'platform', 'linux')
        monkeypatch.setattr(
            'fleasion.proxy.tls_backends.android_community.find_android_community_pid',
            _mock_mocktail,
        )

        backend = AndroidCommunityBackend('mocktail')
        with pytest.raises(BackendUnavailableError, match='requires an active tunnel'):
            await backend.open_session('assetdelivery.roblox.com')

    asyncio.run(run())


def test_registry_creates_android_community_backend() -> None:
    for client in ANDROID_COMMUNITY_CLIENTS:
        backend = create_backend(BackendId.ANDROID_COMMUNITY, community_client=client)
        assert isinstance(backend, AndroidCommunityBackend)
        assert backend.client == client
