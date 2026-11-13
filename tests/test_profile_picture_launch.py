"""Verified Player session ownership across ticket-based Roblox launches."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from fleasion.gui import rando_stuff_tab as account_ui
from fleasion.proxy.addons import profile_picture_spoofer as pfp_module


def _join_flow(user_id: int) -> object:
    return SimpleNamespace(
        request=SimpleNamespace(
            pretty_url='https://gamejoin.roblox.com/v1/join-game',
            content=b'{}',
        ),
        response=SimpleNamespace(
            content=json.dumps({'joinScript': {'UserId': user_id}}).encode(),
            status_code=200,
        ),
    )


def _deliver_join(spoofer: pfp_module.ProfilePictureSpoofer, user_id: int) -> None:
    from fleasion.proxy.server import ProxyFlow

    flow = cast('ProxyFlow', _join_flow(user_id))
    spoofer.request(flow)
    spoofer.response(flow)


@pytest.mark.parametrize(
    ('dispatched', 'actual_user_id', 'expected'),
    [(True, 2, '2'), (True, 1, '1'), (False, None, '1')],
)
def test_ticket_launch_does_not_assume_selected_account_is_active(
    monkeypatch: pytest.MonkeyPatch,
    dispatched: bool,
    actual_user_id: int | None,
    expected: str,
) -> None:
    """Only Roblox's join response proves the active account, not the selected ticket."""
    owner = account_ui.RandoStuffTab.__new__(account_ui.RandoStuffTab)
    owner.__dict__.update({
        '_lock': threading.Lock(),
        '_account_manager_teleport_place_id': None,
        '_account_manager_capture_place_id': None,
    })
    state: dict[str, object] = {'process': ('old-A', 'player-A')}
    monkeypatch.setattr(pfp_module, '_client_process_identity', lambda: state['process'])
    spoofer = pfp_module.ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/b.png'}])
    _deliver_join(spoofer, 1)
    assert spoofer.current_user_id() == '1'
    owner.__dict__['_proxy_master'] = SimpleNamespace(profile_picture_spoofer=spoofer)
    failures: list[tuple[str, str]] = []

    def warn(username: str, reason: str) -> None:
        failures.append((username, reason))

    owner.__dict__['_show_selected_account_launch_failed'] = warn
    monkeypatch.setattr(account_ui, 'IS_MACOS', True)
    monkeypatch.setattr(account_ui, 'IS_WINDOWS', False)
    monkeypatch.setattr(account_ui, '_is_rosemary_client', lambda: False)
    monkeypatch.setattr(account_ui, '_find_roblox_exe', lambda: Path('/test/RobloxPlayer'))

    def get_ticket(_cookie: str) -> str:
        return 'ticket'

    monkeypatch.setattr(account_ui, 'get_auth_ticket', get_ticket)

    def launch(_uri: str) -> bool:
        assert spoofer.current_user_id() is None
        assert spoofer._ticket_pending is not None  # pyright: ignore[reportPrivateUsage]
        if dispatched:
            state['process'] = ('new-client', 'new-session')
        return dispatched

    monkeypatch.setattr(account_ui, 'launch_as_standard_user', launch)
    owner._launch_account_thread('selected-B', 'AccountB')  # pyright: ignore[reportPrivateUsage]
    if dispatched:
        assert spoofer.current_user_id() is None  # New process alone is not authentication
        assert actual_user_id is not None
        _deliver_join(spoofer, actual_user_id)
        assert failures == []
    else:
        assert failures and failures[0][0] == 'AccountB'
    assert spoofer.current_user_id() == expected


def test_selected_launch_account_alone_does_not_modify_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account selection is not a ticket dispatch or confirmed active session."""
    monkeypatch.setattr(pfp_module, '_client_process_identity', lambda: ('player-A', 1))
    owner = account_ui.RandoStuffTab.__new__(account_ui.RandoStuffTab)
    spoofer = pfp_module.ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/b.png'}])
    _deliver_join(spoofer, 1)
    owner.__dict__.update({
        '_proxy_master': SimpleNamespace(profile_picture_spoofer=spoofer),
        '_accounts': [{'username': 'AccountB', 'cookie': 'encrypted'}],
        '_account_list': SimpleNamespace(currentRow=lambda: 0),
    })
    selected: list[str] = []
    owner.__dict__['_set_selected_account'] = selected.append
    monkeypatch.setattr(account_ui, 'IS_MACOS', True)
    monkeypatch.setattr(account_ui, '_is_rosemary_client', lambda: False)

    def decrypt(_token: str) -> str:
        return 'selected-B'

    monkeypatch.setattr(account_ui, '_decrypt_cookie', decrypt)
    owner._on_switch_account()  # pyright: ignore[reportPrivateUsage]
    assert selected == ['AccountB']
    assert spoofer.current_user_id() == '1'
    assert spoofer._ticket_pending is None  # pyright: ignore[reportPrivateUsage]
