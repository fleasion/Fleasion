"""TLS-splice startup must probe CONNECT transport, not synthetic TLS 1.2."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from fleasion.proxy.master import ProxyMaster
from fleasion.proxy.server import FleasionProxy
from fleasion.proxy.upstream import UpstreamEndpoint

if TYPE_CHECKING:
    from fleasion.proxy.addons.cache_scraper import CacheScraper
    from fleasion.proxy.addons.texture_stripper import TextureStripper


def test_splice_startup_checks_real_connect_route_without_synthetic_tls() -> None:
    async def upstream(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.read()
        finally:
            writer.close()

    async def run() -> None:
        target = await asyncio.start_server(upstream, '127.0.0.1', 0)
        target_port = target.sockets[0].getsockname()[1]
        proxy = FleasionProxy(
            texture_stripper=cast('TextureStripper', SimpleNamespace()),
            cache_scraper=cast('CacheScraper', SimpleNamespace()),
            host_certs={},
            port=0,
            explicit_proxy=True,
            tls_splice=True,
            intercept_hosts={'probe.test'},
            upstream_endpoints={
                'probe.test': [UpstreamEndpoint('probe.test', target_port, '127.0.0.1')]
            },
        )
        try:
            await proxy.start()
            owner = ProxyMaster.__new__(ProxyMaster)
            owner._proxy = proxy
            ok = await owner._run_startup_tls_self_test(
                {'probe.test'},
                Path('/does-not-exist/ca.pem'),
                proxy.port,
                explicit_proxy=True,
            )
            assert ok is True
            assert owner._tls_startup_attempts[0]['mode'] == 'splice_connect'
            # CONNECT-only probes must not create 20-second TLS key waiters
            # or misleading "TLS splice failed" events.
            await asyncio.sleep(0.1)
            assert not proxy._splice_tasks
        finally:
            await proxy.stop()
            target.close()
            await target.wait_closed()

    asyncio.run(run())


def test_splice_transport_probe_reports_bad_connect_response() -> None:
    import fleasion.proxy.master as master

    async def reject(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b'\r\n\r\n')
        writer.write(b'HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n')
        await writer.drain()
        writer.close()

    async def run() -> None:
        listener = await asyncio.start_server(reject, '127.0.0.1', 0)
        port = listener.sockets[0].getsockname()[1]
        try:
            ok, failures = await master._splice_connect_probe_result(
                {'probe.test'}, port, explicit_proxy=True
            )
            assert not ok
            assert len(failures) == 1
            assert '502 Bad Gateway' in failures[0]
        finally:
            listener.close()
            await listener.wait_closed()

    asyncio.run(run())


def test_rosemary_splice_is_default_before_player_process_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleasion.proxy.master as master

    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.delenv('FLEASION_SPLICE', raising=False)
    monkeypatch.delenv('FLEASION_SPLICE_BACKEND', raising=False)
    monkeypatch.setattr(
        master.ProxyMaster, '_has_active_linux_splice_target', staticmethod(lambda _mode: False)
    )
    owner = master.ProxyMaster.__new__(master.ProxyMaster)
    owner._active_linux_client_key = 'rosemary'
    assert owner._use_splice_transport() is True
    owner._active_linux_client_key = 'sober'
    assert owner._use_splice_transport() is True
    owner._active_linux_client_key = 'mocktail'
    assert owner._use_splice_transport() is True
    monkeypatch.setenv('FLEASION_SPLICE', '0')
    owner._active_linux_client_key = 'rosemary'
    assert owner._use_splice_transport() is False


def test_rosemary_splice_skips_all_legacy_ca_installation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import fleasion.proxy.master as master

    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.delenv('FLEASION_SPLICE', raising=False)

    owner = master.ProxyMaster.__new__(master.ProxyMaster)
    owner._active_linux_client_key = 'rosemary'

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError('Splice must not access a legacy CA installation path')

    monkeypatch.setattr(master, '_install_ca_into_roblox', unexpected)
    monkeypatch.setattr(master, 'get_ca_pem', unexpected)
    certificates = {
        'ca_cert_path': tmp_path / 'missing-ca.crt',
        'ca_key_path': tmp_path / 'missing-ca.key',
        'proxy_ca_dir': tmp_path,
        'host_certs': {},
        'default_cert': (tmp_path / 'missing-host.crt', tmp_path / 'missing-host.key'),
    }
    state = owner._configure_proxy_startup_trust(True, certificates)
    assert state is not None
    assert state['ca_patch_ok'] is True
    assert state['ca_pem'] == ''
    assert state['selected_linux_client_key'] == 'rosemary'

    assert owner.ensure_env_proxy_roblox_ca(tmp_path / 'RobloxPlayer')['success'] is True
    import threading
    assert owner.monitor_env_proxy_roblox_ca(
        tmp_path / 'RobloxPlayer', threading.Event()
    )['success'] is True


def test_rosemary_running_cert_patch_guard_respects_explicit_legacy_opt_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fleasion.proxy.master as master
    from types import SimpleNamespace

    monkeypatch.setattr(master, 'IS_WINDOWS', False)
    monkeypatch.setattr(master, 'IS_MACOS', False)
    monkeypatch.setattr(master, 'IS_LINUX', True)
    monkeypatch.setattr(
        master, '_selected_linux_client_installation',
        lambda: SimpleNamespace(key='rosemary')
    )
    monkeypatch.delenv('FLEASION_SPLICE', raising=False)
    assert master._splice_ca_patch_unneeded()
    monkeypatch.setenv('FLEASION_SPLICE', '0')
    assert not master._splice_ca_patch_unneeded()
