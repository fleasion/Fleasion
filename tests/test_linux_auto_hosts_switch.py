"""Auto must switch Hosts-mode TLS recovery without restarting the privileged relay."""

# Internal proxy state is deliberately replaced with lightweight regression doubles.
# pyright: reportPrivateUsage=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false, reportAttributeAccessIssue=false, reportArgumentType=false, reportDeprecated=false

from __future__ import annotations

import asyncio
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from fleasion.app import roblox_monitor
from fleasion.proxy import master, server
from fleasion.utils.sober_launch_replay import SoberLaunchSnapshot

if TYPE_CHECKING:
    from collections.abc import Generator


@contextmanager
def _proxy_loop() -> Generator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    ready = threading.Event()

    def worker() -> None:
        asyncio.set_event_loop(loop)
        loop.call_soon(ready.set)
        loop.run_forever()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert ready.wait(2)
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=2)
        assert not thread.is_alive()
        loop.close()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_splice_backend_can_switch_without_restarting_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv('FLEASION_SPLICE_KEYLOG', raising=False)
    proxy = server.FleasionProxy.__new__(server.FleasionProxy)
    proxy._tls_splice = True
    proxy._splice_client_key = 'rosemary'
    original = server.FleasionProxy._new_splice_key_provider('rosemary')
    proxy._splice_keys = original
    proxy._verified_sober_session_identity = ('sober-session', 'old')
    proxy._splice_decrypted_hosts = {'clientsettings.roblox.com'}

    assert proxy.switch_splice_linux_client('sober')
    assert proxy._splice_client_key == 'sober'
    assert proxy._splice_keys is not original
    assert proxy._splice_keys is not None
    assert proxy._splice_keys.linux_client_key == 'sober'
    assert proxy._splice_keys.sober_native_keylog is not None
    assert original._stop.is_set()
    assert proxy._verified_sober_session_identity is None
    assert not proxy._splice_decrypted_hosts
    replacement = proxy._splice_keys
    assert proxy.switch_splice_linux_client('sober')
    assert proxy._splice_keys is replacement
    assert not proxy.switch_splice_linux_client('invalid')
    assert proxy._splice_keys is replacement
    assert proxy.switch_splice_linux_client('rosemary')
    assert replacement._stop.is_set()
    assert proxy._splice_keys is not None
    proxy._splice_keys.clear()


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_switch_arms_then_rebinds_without_helper_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master, 'IS_LINUX', True)
    events: list[str] = []
    owner = master.ProxyMaster.__new__(master.ProxyMaster)
    owner._running = True
    owner._active_linux_client_key = 'rosemary'
    owner._active_linux_client_installation = SimpleNamespace(key='rosemary')
    owner._hosts_sober_native_keylog_active = False
    owner._sober_native_keylog_armed = False
    owner._hosts_installed = True
    owner._use_env_proxy_mode = lambda: False  # type: ignore[method-assign]
    owner.wait_for_hosts_proxy_ready = lambda timeout=0: True  # type: ignore[method-assign]
    owner.refresh_username_spoofer_interception = (  # type: ignore[method-assign]
        lambda: events.append('refresh')
    )
    owner._stop_linux_sober_custom_fflag_timer = lambda: events.append('stop_timer')  # type: ignore[method-assign]
    owner._start_linux_sober_custom_fflag_timer = lambda: events.append('start_timer')  # type: ignore[method-assign]

    def arm(*, installation: object, allow_running_for_replay: bool) -> bool:
        assert getattr(installation, 'key') == 'sober'
        assert allow_running_for_replay
        events.append('arm')
        owner._hosts_sober_native_keylog_active = True
        owner._sober_native_keylog_armed = True
        return True

    owner._arm_linux_hosts_sober_native_keylog = arm  # type: ignore[method-assign]
    target = SimpleNamespace(key='sober')
    owner._proxy = SimpleNamespace(
        switch_splice_linux_client=lambda client: events.append(f'rebind:{client}') or True
    )
    with _proxy_loop() as loop:
        owner._loop = loop
        assert owner.switch_linux_hosts_splice_client(target, replay_sober=True)  # type: ignore[arg-type]
    assert events == ['arm', 'rebind:sober', 'stop_timer', 'start_timer', 'refresh']
    assert owner._active_linux_client_key == 'sober'
    assert owner._active_linux_client_installation is target
    assert owner._hosts_installed
    assert owner._hosts_sober_native_keylog_active


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_switch_rolls_back_on_key_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master, 'IS_LINUX', True)
    owner = master.ProxyMaster.__new__(master.ProxyMaster)
    original = SimpleNamespace(key='rosemary')
    owner._running = True
    owner._active_linux_client_key = 'rosemary'
    owner._active_linux_client_installation = original
    owner._hosts_sober_native_keylog_active = False
    owner._use_env_proxy_mode = lambda: False  # type: ignore[method-assign]
    owner.wait_for_hosts_proxy_ready = lambda timeout=0: True  # type: ignore[method-assign]
    owner._arm_linux_hosts_sober_native_keylog = (  # type: ignore[method-assign]
        lambda **kwargs: True
    )
    owner._clear_linux_hosts_sober_native_keylog = lambda: True  # type: ignore[method-assign]
    owner._proxy = SimpleNamespace(switch_splice_linux_client=lambda _key: False)
    with _proxy_loop() as loop:
        owner._loop = loop
        assert not owner.switch_linux_hosts_splice_client(  # type: ignore[arg-type]
            SimpleNamespace(key='sober'), replay_sober=True
        )
    assert owner._active_linux_client_installation is original
    assert owner._active_linux_client_key == 'rosemary'


def _monitor(monkeypatch: pytest.MonkeyPatch, proxy: object) -> roblox_monitor.RobloxExitMonitor:
    monkeypatch.setattr(roblox_monitor, 'sober_flatpak_session_identity', lambda: None)
    monkeypatch.setattr(roblox_monitor, 'running_sober_launchers', lambda: ())
    monkeypatch.setattr(roblox_monitor, 'running_sober_players', lambda: ())
    monkeypatch.setattr(roblox_monitor, 'flatpak_community_scopes', lambda _installed: frozenset())
    config = SimpleNamespace(
        linux_client='auto', proxy_mode='hosts', proxy_features_enabled=True
    )
    return roblox_monitor.RobloxExitMonitor(config, proxy_master=proxy)  # type: ignore[arg-type]


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_replays_new_sober_after_switching_key_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SoberLaunchSnapshot('app-flatpak-org.vinegarhq.Sober-123.scope', 100, 200, (), Path.home())
    events: list[object] = []

    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'
        _hosts_sober_native_keylog_active = False

        def wait_for_hosts_proxy_ready(self, timeout: float = 0) -> bool:
            assert timeout == 0
            return True

        def switch_linux_hosts_splice_client(
            self, installation: object, *, replay_sober: bool = False
        ) -> bool:
            events.append(('rebind', getattr(installation, 'key'), replay_sober))
            self._active_linux_client_key = str(getattr(installation, 'key'))
            self._hosts_sober_native_keylog_active = replay_sober
            return True

        def rearm_custom_fflag_delivery_for_player_launch(self) -> None:
            events.append('rearm')

    proxy = Proxy()
    monitor = _monitor(monkeypatch, proxy)
    monkeypatch.setattr(roblox_monitor, 'capture_sober_launch', lambda: snapshot)
    monkeypatch.setattr(roblox_monitor, 'replay_sober_launch', lambda s: events.append(('replay', s)) or True)
    selected = {'key': 'sober'}
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key=selected['key'], display_name=selected['key'].capitalize()),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: True)
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == [('rebind', 'sober', True), ('replay', snapshot), 'rearm']
    assert not monitor.was_running
    assert not monitor._player_was_running
    assert monitor._sober_handoff_until > 0
    selected['key'] = 'rosemary'
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == [('rebind', 'sober', True), ('replay', snapshot), 'rearm']


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_does_not_replay_preexisting_sober(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = 'app-flatpak-org.vinegarhq.Sober-123.scope'
    snapshot = SoberLaunchSnapshot(scope, 100, 200, (), Path.home())
    switched: list[tuple[str, bool]] = []

    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'
        _hosts_sober_native_keylog_active = False

        def wait_for_hosts_proxy_ready(self, timeout: float = 0) -> bool:
            return True

        def switch_linux_hosts_splice_client(
            self, installation: object, *, replay_sober: bool = False
        ) -> bool:
            switched.append((str(getattr(installation, 'key')), replay_sober))
            self._active_linux_client_key = str(getattr(installation, 'key'))
            return True

    proxy = Proxy()
    monitor = _monitor(monkeypatch, proxy)
    monitor._sober_preexisting_scope = scope
    monkeypatch.setattr(roblox_monitor, 'capture_sober_launch', lambda: snapshot)
    monkeypatch.setattr(
        roblox_monitor, 'replay_sober_launch',
        lambda _s: (_ for _ in ()).throw(AssertionError('must not kill preexisting Sober')),
    )
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key='sober', display_name='Sober'),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: True)
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert switched == [('sober', False)]

@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_arms_next_sober_launch_after_preexisting_instance_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Proxy:
        is_running = True
        _active_linux_client_key = 'sober'
        _hosts_sober_native_keylog_active = False

        def arm_linux_hosts_sober_keylog_if_idle(self) -> bool:
            events.append('arm_idle')
            self._hosts_sober_native_keylog_active = True
            return True

    proxy = Proxy()
    monitor = _monitor(monkeypatch, proxy)
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key='sober', display_name='Sober'),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: False)
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == ['arm_idle']
    assert proxy._hosts_sober_native_keylog_active


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_replays_late_captured_sober_without_rebinding_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SoberLaunchSnapshot('app-flatpak-org.vinegarhq.Sober-456.scope', 100, 200, (), Path.home())
    events: list[object] = []

    class Proxy:
        is_running = True
        _active_linux_client_key = 'sober'
        _hosts_sober_native_keylog_active = False

        def wait_for_hosts_proxy_ready(self, timeout: float = 0) -> bool:
            return True

        def switch_linux_hosts_splice_client(
            self, installation: object, *, replay_sober: bool = False
        ) -> bool:
            events.append(('retry_keylog', getattr(installation, 'key'), replay_sober))
            self._hosts_sober_native_keylog_active = replay_sober
            return True

        def rearm_custom_fflag_delivery_for_player_launch(self) -> None:
            events.append('rearm')

    proxy = Proxy()
    monitor = _monitor(monkeypatch, proxy)
    monkeypatch.setattr(roblox_monitor, 'capture_sober_launch', lambda: snapshot)
    monkeypatch.setattr(roblox_monitor, 'replay_sober_launch', lambda s: events.append(('replay', s)) or True)
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key='sober', display_name='Sober'),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: True)
    assert not monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == [('retry_keylog', 'sober', True), ('replay', snapshot), 'rearm']


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_does_not_replay_sober_that_inherited_existing_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = SoberLaunchSnapshot('app-flatpak-org.vinegarhq.Sober-789.scope', 100, 200, (), Path.home())
    events: list[tuple[str, bool]] = []

    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'
        _hosts_sober_native_keylog_active = True

        def wait_for_hosts_proxy_ready(self, timeout: float = 0) -> bool:
            return True

        def switch_linux_hosts_splice_client(
            self, installation: object, *, replay_sober: bool = False
        ) -> bool:
            events.append((str(getattr(installation, 'key')), replay_sober))
            self._active_linux_client_key = str(getattr(installation, 'key'))
            return True

    proxy = Proxy()
    monitor = _monitor(monkeypatch, proxy)
    monkeypatch.setattr(roblox_monitor, 'capture_sober_launch', lambda: snapshot)
    monkeypatch.setattr(
        roblox_monitor, 'replay_sober_launch',
        lambda _s: (_ for _ in ()).throw(AssertionError('override already prepared')),
    )
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key='sober', display_name='Sober'),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: True)
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]
    assert events == [('sober', False)]


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_does_not_switch_to_nonrunning_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Proxy:
        is_running = True
        _active_linux_client_key = 'rosemary'

        def switch_linux_hosts_splice_client(self, *_args: object, **_kwargs: object) -> bool:
            raise AssertionError('must not switch before Sober is running')

    monitor = _monitor(monkeypatch, Proxy())
    monkeypatch.setattr(
        roblox_monitor, 'get_selected_linux_client_installation',
        lambda: SimpleNamespace(key='sober', display_name='Sober'),
    )
    monkeypatch.setattr(roblox_monitor, 'is_linux_client_running', lambda _selected: False)
    assert monitor._sync_linux_auto_proxy_client()  # pyright: ignore[reportPrivateUsage]


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_hosts_auto_retarget_to_rosemary_does_not_clear_live_sober_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(master, 'IS_LINUX', True)
    events: list[str] = []
    owner = master.ProxyMaster.__new__(master.ProxyMaster)
    owner._running = True
    owner._active_linux_client_key = 'sober'
    owner._active_linux_client_installation = SimpleNamespace(key='sober')
    owner._hosts_sober_native_keylog_active = True
    owner._sober_native_keylog_armed = True
    owner._use_env_proxy_mode = lambda: False  # type: ignore[method-assign]
    owner.wait_for_hosts_proxy_ready = lambda timeout=0: True  # type: ignore[method-assign]
    owner.refresh_username_spoofer_interception = (  # type: ignore[method-assign]
        lambda: events.append('refresh')
    )
    owner._stop_linux_sober_custom_fflag_timer = lambda: events.append('stop_timer')  # type: ignore[method-assign]
    owner._start_linux_sober_custom_fflag_timer = lambda: events.append('start_timer')  # type: ignore[method-assign]
    owner._clear_linux_hosts_sober_native_keylog = (  # type: ignore[method-assign]
        lambda: (_ for _ in ()).throw(AssertionError('active Sober may still use keylog'))
    )
    owner._proxy = SimpleNamespace(
        switch_splice_linux_client=lambda key: events.append(f'rebind:{key}') or True
    )
    with _proxy_loop() as loop:
        owner._loop = loop
        assert owner.switch_linux_hosts_splice_client(SimpleNamespace(key='rosemary'))  # type: ignore[arg-type]
    assert events == ['rebind:rosemary', 'stop_timer', 'start_timer', 'refresh']
    assert owner._active_linux_client_key == 'rosemary'
    assert owner._hosts_sober_native_keylog_active

@pytest.mark.skipif(sys.platform != 'linux', reason='Linux client switch only')
def test_old_splice_session_cannot_claim_new_sober_decryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connections keep their original provider even if Auto swaps it mid-flight."""
    from fleasion.proxy.splice import SpliceConnection

    async def idle_pump(_self: object, _direction: object) -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(SpliceConnection, '_pump', idle_pump)
    marked: list[str] = []
    served: list[str] = []
    old_provider = object()
    sober_provider = object()

    class Wire:
        keys = None

        def close(self) -> None:
            pass

        async def wait_closed(self) -> None:
            pass

        def is_closing(self) -> bool:
            return False

    class Proxy:
        _splice_keys = sober_provider

        def mark_splice_decrypted(self, host: str) -> bool:
            marked.append(host)
            return True

        async def serve_spliced_session(self, *_args: object) -> None:
            served.append('http')

    async def exercise(provider: object) -> None:
        session = SpliceConnection.__new__(SpliceConnection)
        session.keys = provider
        session._ready = asyncio.Event()
        session._ready.set()
        session._transparent_passthrough = False
        session._http_started = False
        direction = SimpleNamespace(
            plain=asyncio.StreamReader(), writer=Wire(), wire=Wire(), close_alert=None
        )
        session.client = direction
        session.server = direction
        await asyncio.wait_for(session.run(Proxy(), 'gamejoin.roblox.com'), timeout=1)

    asyncio.run(exercise(old_provider))
    assert marked == []
    assert served == ['http']
    asyncio.run(exercise(sober_provider))
    assert marked == ['gamejoin.roblox.com']
    assert served == ['http', 'http']
