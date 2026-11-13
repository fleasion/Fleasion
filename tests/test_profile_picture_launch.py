"""Profile-picture session ownership across ticket-based Roblox launches."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.gui import rando_stuff_tab as account_ui
from fleasion.proxy.addons import profile_picture_spoofer as pfp_module


@pytest.mark.parametrize('dispatched', [True, False])
def test_macos_ticket_launch_does_not_claim_me_until_new_client_confirmed(
    monkeypatch: pytest.MonkeyPatch, dispatched: bool
) -> None:
    """Confirmed ticket B becomes me; failed launch preserves account A."""
    owner = account_ui.RandoStuffTab.__new__(account_ui.RandoStuffTab)
    owner.__dict__.update({
        '_lock': threading.Lock(),
        '_account_manager_teleport_place_id': None,
        '_account_manager_capture_place_id': None,
    })
    spoofer = pfp_module.ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/b.png'}])
    spoofer.set_current_user('1')
    owner.__dict__['_proxy_master'] = SimpleNamespace(profile_picture_spoofer=spoofer)
    failures: list[tuple[str, str]] = []

    def warn(username: str, reason: str) -> None:
        failures.append((username, reason))

    owner.__dict__['_show_selected_account_launch_failed'] = warn
    state: dict[str, object] = {'process': ('old-A', 'player-A')}
    monkeypatch.setattr(pfp_module, '_client_process_identity', lambda: state['process'])

    def resolve(cookie: str) -> int:
        return 2 if cookie == 'selected-B' else 1

    monkeypatch.setattr(pfp_module, '_authenticated_user_id', resolve)
    monkeypatch.setattr(pfp_module, '_get_roblosecurity', lambda: 'local-A')
    monkeypatch.setattr(account_ui, 'IS_MACOS', True)
    monkeypatch.setattr(account_ui, 'IS_WINDOWS', False)
    monkeypatch.setattr(account_ui, '_is_rosemary_client', lambda: False)
    monkeypatch.setattr(account_ui, '_find_roblox_exe', lambda: Path('/test/RobloxPlayer'))
    def get_ticket(_cookie: str) -> str:
        return 'ticket'

    monkeypatch.setattr(account_ui, 'get_auth_ticket', get_ticket)
    completed: list[threading.Event] = []

    def launch(_uri: str) -> bool:
        assert spoofer._self_user_id() is None  # pyright: ignore[reportPrivateUsage]
        pending = spoofer._ticket_pending  # pyright: ignore[reportPrivateUsage]
        assert pending is not None
        completed.append(pending[1])
        if dispatched:
            state['process'] = ('new-B', 'player-B')
        return dispatched

    monkeypatch.setattr(account_ui, 'launch_as_standard_user', launch)
    owner._launch_account_thread('selected-B', 'AccountB')  # pyright: ignore[reportPrivateUsage]
    assert len(completed) == 1
    assert completed[0].wait(2)
    if dispatched:
        assert spoofer.current_user_id() == '2'
        assert failures == []
    else:
        assert spoofer.current_user_id() == '1'
        assert failures and failures[0][0] == 'AccountB'


def test_selected_launch_account_alone_does_not_modify_me(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account selection is not a ticket dispatch or confirmed active session."""
    owner = account_ui.RandoStuffTab.__new__(account_ui.RandoStuffTab)
    spoofer = pfp_module.ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/b.png'}])
    spoofer.set_current_user('1')
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
