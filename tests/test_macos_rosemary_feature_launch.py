"""macOS feature buttons must not dispatch Roblox URIs or impersonate an account."""

from __future__ import annotations

import os
import threading
from types import SimpleNamespace
from typing import cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from fleasion.gui import macos_rosemary_launch, rando_stuff_tab, subplace_joiner_tab
from fleasion.utils import macos_rosemary_gate

@pytest.fixture(scope='module')
def app() -> QApplication:
    current = QApplication.instance()
    return current if isinstance(current, QApplication) else QApplication([])


def test_feature_requires_explicit_confirmation_before_plain_managed_launch(
    monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    del app
    parent = QWidget()
    service = macos_rosemary_launch.ManagedRobloxLaunch(
        parent, cast('object', object()), None
    )
    parent._macos_rosemary_launch = service  # type: ignore[attr-defined]
    events: list[str] = []
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', True)
    monkeypatch.setattr(service, 'launch', lambda: events.append('managed'))
    monkeypatch.setattr(
        QMessageBox, 'question',
        lambda _parent, _title, message, *_args: (
            events.append(message) or QMessageBox.StandardButton.No
        ),
    )
    assert not macos_rosemary_launch.launch_from_feature(parent, 'Reserved server')
    assert len(events) == 1
    assert 'manually' in events[0]
    monkeypatch.setattr(
        QMessageBox, 'question',
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    assert macos_rosemary_launch.launch_from_feature(parent, 'Reserved server')
    assert events[-1] == 'managed'
    parent.close()


def test_feature_does_not_bypass_disabled_managed_gate(
    monkeypatch: pytest.MonkeyPatch, app: QApplication
) -> None:
    del app
    parent = QWidget()
    monkeypatch.setattr(macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', False)
    shown: list[str] = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda _w, _t, message: shown.append(message))
    assert not macos_rosemary_launch.launch_from_feature(parent, 'Subplace join')
    assert 'not available' in shown[0]
    parent.close()


def test_subplace_join_button_uses_managed_feature_handler_on_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, str]] = []
    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda owner, feature: calls.append((owner, feature)) or True,
    )
    owner = SimpleNamespace(_current_job_id='', joining_place=True)
    subplace_joiner_tab.SubplaceJoinerTab._join_place(owner, '1818', job_id='test-job')
    assert owner._current_job_id == ''
    assert not owner.joining_place
    assert calls == [(owner, 'Subplace or specific-server joining')]


def test_subplace_uri_dispatch_is_blocked_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        subplace_joiner_tab, 'launch_as_standard_user',
        lambda _value: pytest.fail('OS Roblox URI must not be dispatched'),
    )
    assert not subplace_joiner_tab.SubplaceJoinerTab._launch_roblox_uri(
        SimpleNamespace(), 'roblox://experiences/start?placeId=1818'
    )


def test_reserved_server_button_does_not_dispatch_uri_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user',
        lambda _target: pytest.fail('OS Roblox URI must not be dispatched'),
    )
    calls: list[str] = []
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda _owner, feature: calls.append(feature) or True,
    )
    owner = SimpleNamespace(
        _place_id_input=SimpleNamespace(text=lambda: '1818'),
        _access_code_input=SimpleNamespace(text=lambda: 'test-access'),
        _lock=threading.Lock(),
        _doing_rejoin=False,
        _active_rejoin_attempt_id='stale-attempt',
        _awaiting_rejoin_response=True,
    )
    rando_stuff_tab.RandoStuffTab._on_rejoin_clicked(owner)
    assert calls == ['Reserved-server joining']
    assert not owner._doing_rejoin
    assert owner._active_rejoin_attempt_id is None
    assert not owner._awaiting_rejoin_response


def test_account_manager_launch_button_does_not_dispatch_uri_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', lambda _cookie: 'test-cookie')
    calls: list[str] = []
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda _owner, feature: calls.append(feature) or True,
    )
    owner = SimpleNamespace(
        _last_switched_account={'username': 'Test', 'cookie': 'encrypted'},
        _private_server_input=SimpleNamespace(text=lambda: ''),
        _subplace_id_input=SimpleNamespace(text=lambda: ''),
        _job_id_input=SimpleNamespace(text=lambda: ''),
    )
    rando_stuff_tab.RandoStuffTab._on_launch_account(owner)
    assert calls == ['Selected-account, game or private-server launching']


def test_account_follow_button_stays_inside_fleasion_on_mac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', lambda _cookie: 'test-cookie')
    calls: list[str] = []
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda _owner, feature: calls.append(feature) or True,
    )
    owner = SimpleNamespace(
        _last_switched_account={'username': 'Test', 'cookie': 'encrypted'},
        _follow_user_input=SimpleNamespace(text=lambda: 'testtarget'),
        _follow_user_btn=SimpleNamespace(
            setEnabled=lambda _enabled: pytest.fail('must not launch follow URI')
        ),
    )
    rando_stuff_tab.RandoStuffTab._on_follow_user(owner)
    assert calls == ['Account follow-user joining']


def test_account_ticket_worker_never_dispatches_native_roblox_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', True)
    calls: list[str] = []
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user',
        lambda _uri: pytest.fail('OS Roblox URI dispatch must not occur'),
    )
    result = rando_stuff_tab.RandoStuffTab._dispatch_authenticated_ticket_uri(
        SimpleNamespace(),
        'roblox-player:1+launchmode:play+gameinfo:placeholder',
        lambda _uri: pytest.fail('no OS handler should be called'),
    )
    assert not result
    assert not calls
