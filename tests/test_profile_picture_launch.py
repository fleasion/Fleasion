# pyright: reportPrivateUsage=false
"""Account Manager selection drives profile-picture me across launch modes."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from fleasion.gui import rando_stuff_tab as account_ui
from fleasion.proxy.addons import profile_picture_spoofer as pfp_module


@pytest.mark.parametrize('approved', [True, False])
def test_mac_account_launch_uses_fleasion_without_changing_selected_identity(
    monkeypatch: pytest.MonkeyPatch,
    approved: bool,
) -> None:
    """The macOS ticket worker cannot bypass Fleasion or change the account mapping."""
    from fleasion.gui import macos_rosemary_launch

    owner = account_ui.RandoStuffTab.__new__(account_ui.RandoStuffTab)
    owner.__dict__.update({
        '_lock': threading.Lock(),
        '_account_manager_teleport_place_id': None,
        '_account_manager_capture_place_id': None,
    })
    spoofer = pfp_module.ProfilePictureSpoofer()
    spoofer.set_entries([{'enabled': True, 'target': 'me', 'image_url': 'https://example.com/b.png'}])
    spoofer.set_current_user('2')
    owner.__dict__['_proxy_master'] = SimpleNamespace(profile_picture_spoofer=spoofer)
    owner.__dict__['_on_main'] = lambda callback: callback()
    launches: list[str] = []
    monkeypatch.setattr(account_ui, 'IS_MACOS', True)
    monkeypatch.setattr(account_ui, 'IS_WINDOWS', False)
    monkeypatch.setattr(
        account_ui, 'launch_as_standard_user',
        lambda _target: pytest.fail('Mac account worker must never dispatch a Roblox URI'),
    )
    monkeypatch.setattr(
        macos_rosemary_launch, 'launch_from_feature',
        lambda _owner, feature: launches.append(feature) or approved,
    )
    owner._launch_account_thread('ticket-cookie', 'AccountB')  # pyright: ignore[reportPrivateUsage]
    assert spoofer.current_user_id() == '2'
    assert launches == ['Selected-account, game or private-server launching']


def test_account_manager_selection_updates_spoofer_even_for_macos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting B maps me to B immediately, independently of game join."""
    triggered: list[str] = []
    spoofer = SimpleNamespace(select_account_cookie=triggered.append)

    class FakeAccountUI:
        _account_list = SimpleNamespace(currentRow=lambda: 0)
        _accounts = [{'username': 'AccountB', 'cookie': 'encrypted'}]
        _last_switched_account: object | None = None
        _proxy_master = SimpleNamespace(profile_picture_spoofer=spoofer)

        def _set_selected_account(self, username: str) -> None:
            assert username == 'AccountB'

        def _push_profile_picture_current_account(self, cookie: str) -> None:
            assert cookie == 'cookie-B'
            triggered.append(cookie)

    monkeypatch.setattr(account_ui, 'IS_MACOS', True)
    monkeypatch.setattr(account_ui, '_is_rosemary_client', lambda: False)

    def decrypt(_token: str) -> str:
        return 'cookie-B'

    monkeypatch.setattr(account_ui, '_decrypt_cookie', decrypt)
    fake = FakeAccountUI()
    account_ui.RandoStuffTab._on_switch_account(fake)  # pyright: ignore[reportArgumentType]
    assert triggered == ['cookie-B']
    assert fake._last_switched_account is fake._accounts[0]


def test_delayed_startup_discovery_cannot_overwrite_selected_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slow initial auth lookup cannot change the selected Account Manager label."""
    class FakeAccountManager:
        _lock = threading.Lock()
        _last_switched_account = {'username': 'selected-B'}
        _proxy_master = None
        _username_spoofer_current_user_id: str | None = None
        _username_spoofer_current_username = ''

        def __init__(self) -> None:
            self.labels: list[str] = []

        def _push_username_spoofer_current_user(self) -> None:
            return

        def _on_main(self, callback: object) -> None:
            assert callable(callback)
            callback()

        def _set_selected_account(self, username: str) -> None:
            self.labels.append(username)

    monkeypatch.setattr(account_ui, 'get_roblosecurity', lambda: 'startup-cookie-A')
    def authenticated(_cookie: str) -> object:
        return SimpleNamespace(status_code=200)

    def payload(_response: object) -> dict[str, object]:
        return {'name': 'startup-A', 'id': 111}

    monkeypatch.setattr(account_ui, '_authenticated_user_response', authenticated)
    monkeypatch.setattr(account_ui, '_response_json_object', payload)
    fake = FakeAccountManager()
    account_ui.RandoStuffTab._resolve_current_user(fake)  # pyright: ignore[reportArgumentType]
    assert fake.labels == []
    assert fake._last_switched_account == {'username': 'selected-B'}
