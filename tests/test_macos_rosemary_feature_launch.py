"""User-requested Roblox URIs go to managed Rosemary, never OS registration."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from fleasion.gui import macos_rosemary_launch, rando_stuff_tab, subplace_joiner_tab
from fleasion.utils import macos_rosemary_gate

if TYPE_CHECKING:
    from collections.abc import Callable

    from fleasion.config.manager import ConfigManager


@pytest.fixture(scope='module')
def app() -> QApplication:
    current = QApplication.instance()
    return current if isinstance(current, QApplication) else QApplication([])


def test_feature_forwards_user_selected_uri_directly_to_managed_launcher(
    monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    del app
    window = QWidget()
    service = macos_rosemary_launch.ManagedRobloxLaunch(window, cast('object', object()), None)
    window._macos_rosemary_launch = service  # type: ignore[attr-defined]
    forwarded: list[str] = []
    target = 'roblox://experiences/start?placeId=1818'
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', True)

    def launch(uri: str, **_kwargs: object) -> None:
        forwarded.append(uri)
        service._busy = True  # pyright: ignore[reportPrivateUsage]

    monkeypatch.setattr(service, 'launch_target', launch)
    assert macos_rosemary_launch.launch_from_feature(window, target)
    assert forwarded == [target]
    assert not macos_rosemary_launch.launch_from_feature(window, target)
    assert forwarded == [target]
    window.close()


def test_managed_launch_completion_refreshes_proxy_gates(
    monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    del app
    parent = QWidget()
    refreshed: list[str] = []
    parent.set_proxy_features_enabled = lambda enabled: refreshed.append(f'proxy:{enabled}')  # type: ignore[attr-defined]
    parent.refresh_settings_tab = lambda: refreshed.append('settings')  # type: ignore[attr-defined]
    config = SimpleNamespace(proxy_features_enabled=True)
    controller = macos_rosemary_launch.ManagedRobloxLaunch(parent, cast('ConfigManager', config), None)
    monkeypatch.setattr(QMessageBox, 'information', lambda *_args: None)
    controller._on_finished(True, 'Ready')
    assert refreshed == ['proxy:True', 'settings']
    parent.close()


def test_feature_fails_closed_when_disabled(
    monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    del app
    window = QWidget()
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', False)
    messages: list[str] = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda _w, _t, msg: messages.append(msg))
    assert not macos_rosemary_launch.launch_from_feature(window, 'roblox://placeId=1818')
    assert 'not available' in messages[0]
    window.close()


def test_subplace_button_preserves_requested_place_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'darwin')
    monkeypatch.setattr(subplace_joiner_tab, '_get_roblosecurity', lambda: None)
    launched: list[str] = []
    owner = SimpleNamespace(
        _current_job_id='',
        joining_place=False,
        _rando_tab=None,
        _launch_roblox_uri=lambda uri: launched.append(uri) or True,
    )
    subplace_joiner_tab.SubplaceJoinerTab._join_place(owner, '1818')
    assert launched == ['roblox://experiences/start?placeId=1818']
    assert owner.joining_place


def test_subplace_worker_queues_target_for_managed_qt_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'darwin')
    queued: list[Callable[[], object]] = []
    submitted: list[str] = []
    owner = SimpleNamespace(_invoker=SimpleNamespace(call=SimpleNamespace(emit=queued.append)))
    monkeypatch.setattr(
        macos_rosemary_launch,
        'launch_from_feature',
        lambda _widget, target, **_kwargs: submitted.append(target) or True,
    )
    monkeypatch.setattr(
        subplace_joiner_tab, 'launch_as_standard_user',
        lambda _uri: pytest.fail('must not send an external OS URL event'),
    )
    target = 'roblox-player:1+launchmode:play+gameinfo:test-ticket'
    assert subplace_joiner_tab.SubplaceJoinerTab._launch_roblox_uri(owner, target)
    assert not submitted
    assert len(queued) == 1
    queued[0]()
    assert submitted == [target]


@pytest.mark.parametrize('accepted', [True, False])
def test_reserved_server_uses_managed_uri_and_preserves_rewrite_state(
    monkeypatch: pytest.MonkeyPatch, accepted: bool
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user',
        lambda _uri: pytest.fail('must not invoke the host Roblox URI handler'),
    )
    targets: list[str] = []
    monkeypatch.setattr(
        macos_rosemary_launch,
        'launch_from_feature',
        lambda _owner, uri: targets.append(uri) or accepted,
    )
    owner = SimpleNamespace(
        _place_id_input=SimpleNamespace(text=lambda: '1818'),
        _access_code_input=SimpleNamespace(text=lambda: 'test-reserved-access'),
        _lock=threading.Lock(),
        _doing_rejoin=False,
        _active_rejoin_attempt_id=None,
        _awaiting_rejoin_response=False,
    )
    rando_stuff_tab.RandoStuffTab._on_rejoin_clicked(owner)
    assert targets == ['roblox://experiences/start?placeId=1818']
    assert owner._doing_rejoin is accepted
    assert owner._last_access_code == 'test-reserved-access'


def test_account_authenticated_ticket_uri_is_queued_to_managed_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    requested: list[str] = []
    monkeypatch.setattr(
        macos_rosemary_launch,
        'launch_from_feature',
        lambda _owner, uri, **_kwargs: requested.append(uri) or True,
    )
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user',
        lambda _uri: pytest.fail('OS must not handle account ticket URI'),
    )
    queued: list[Callable[[], object]] = []
    owner = SimpleNamespace(_on_main=lambda fn: queued.append(fn) or True)
    target = 'roblox-player:1+launchmode:play+gameinfo:test-ticket'
    assert rando_stuff_tab.RandoStuffTab._dispatch_authenticated_ticket_uri(owner, target)
    assert requested == []
    queued[0]()
    assert requested == [target]


def test_follow_uses_account_dispatch_not_os_handler_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', lambda _secret: 'test-cookie')
    started: list[tuple[str, str]] = []

    def worker(cookie: str, name: str) -> None:
        started.append((cookie, name))

    owner = SimpleNamespace(
        _last_switched_account={'username': 'Test', 'cookie': 'encrypted'},
        _follow_user_input=SimpleNamespace(text=lambda: 'friend'),
        _follow_user_btn=SimpleNamespace(setEnabled=lambda _value: None),
        _follow_user_thread=worker,
    )
    # Avoid an actual worker thread while testing the user-initiated entry point
    class NoThread:
        def __init__(self, *, target: Callable[..., object], args: tuple[object, ...], daemon: bool):
            assert daemon
            self.target, self.args = target, args

        def start(self) -> None:
            self.target(*self.args)

    monkeypatch.setattr(rando_stuff_tab.threading, 'Thread', NoThread)
    rando_stuff_tab.RandoStuffTab._on_follow_user(owner)
    assert started == [('test-cookie', 'friend')]


def test_refresh_authenticated_join_reuses_destination_not_old_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        'roblox-player:1+launchmode:play+gameinfo:old-ticket+'
        'placelauncherurl:unchanged-place-1818+browsertrackerid:234'
    )
    cookies: list[str] = []

    def get_ticket(cookie: str) -> str:
        cookies.append(cookie)
        return 'fresh-ticket'

    monkeypatch.setattr(rando_stuff_tab, 'get_auth_ticket', get_ticket)
    result = rando_stuff_tab.refresh_authenticated_launch_uri(raw, 'selected-account-cookie')
    assert result == raw.replace('old-ticket', 'fresh-ticket')
    assert cookies == ['selected-account-cookie']


def test_missing_auth_ticket_fails_closed_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'get_auth_ticket', lambda _cookie: None)
    with pytest.raises(RuntimeError, match='new Roblox auth ticket'):
        rando_stuff_tab.refresh_authenticated_launch_uri(
            'roblox-player:1+launchmode:app+gameinfo:expired-ticket',
            'selected-account-cookie',
        )


def test_mac_subplace_ticket_retry_uses_same_selected_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'darwin')
    queued: list[Callable[[], object]] = []
    captured: list[tuple[str, object]] = []
    monkeypatch.setattr(rando_stuff_tab, 'get_auth_ticket', lambda cookie: 'new-ticket' if cookie else None)
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda _owner, target, *, refresh_launch_uri=None: (
            captured.append((target, refresh_launch_uri)) or True
        ),
    )
    owner = SimpleNamespace(_invoker=SimpleNamespace(call=SimpleNamespace(emit=queued.append)))
    initial = 'roblox-player:1+launchmode:play+gameinfo:old-ticket+place:1818'
    assert subplace_joiner_tab.SubplaceJoinerTab._launch_roblox_uri(
        owner, initial, retry_cookie='account-B-cookie'
    )
    queued[0]()
    assert captured[0][0] == initial
    refresh = captured[0][1]
    assert callable(refresh)
    assert refresh() == initial.replace('old-ticket', 'new-ticket')
