"""Intercept All never secretly downgrades additional hosts under key-scan load."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.proxy.server import FleasionProxy

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, value: bytes) -> None:
        self.buffer.extend(value)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _connect_reader(host: str) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
    reader.feed_eof()
    return reader


def test_all_additional_hosts_remain_intercepted_during_scan_overload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace()),
            host_certs={}, explicit_proxy=True, tls_splice=True,
            intercept_hosts={'assetdelivery.roblox.com'}, intercept_all_hosts=True,
        )
        setattr(proxy, '_splice_keys', SimpleNamespace(pending_scan_count=1000))
        spliced: list[str] = []
        tunneled: list[str] = []

        async def splice(
            _reader: asyncio.StreamReader, _writer: asyncio.StreamWriter,
            host: str, _port: int, *, explicit: bool
        ) -> None:
            assert explicit
            spliced.append(host)

        async def tunnel(
            _reader: asyncio.StreamReader, _writer: asyncio.StreamWriter,
            host: str, _port: int, **_kwargs: object
        ) -> None:
            tunneled.append(host)

        monkeypatch.setattr(proxy, '_serve_splice', splice)
        monkeypatch.setattr(proxy, '_tunnel_explicit_proxy_connection', tunnel)
        invoke = cast(
            'Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]',
            getattr(proxy, '_handle_explicit_proxy_client'),
        )
        for host in (
            'telemetry.roblox.com',
            'economy.roblox.com',
            'assetdelivery.roblox.com',
            'regional-server.roblox.com',
        ):
            await invoke(_connect_reader(host), cast('asyncio.StreamWriter', _Writer()))
        assert spliced == [
            'telemetry.roblox.com',
            'economy.roblox.com',
            'assetdelivery.roblox.com',
            'regional-server.roblox.com',
        ]
        assert tunneled == []

    asyncio.run(run())
