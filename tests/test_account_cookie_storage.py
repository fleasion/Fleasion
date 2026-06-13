import os
import stat
import sys
from urllib.parse import unquote

import pytest

from Fleasion.gui import rando_stuff_tab


def test_account_cookie_storage_writes_encrypted_payload(tmp_path, monkeypatch):
    key_path = tmp_path / 'accounts.key'
    monkeypatch.setattr(rando_stuff_tab, 'ACCOUNTS_KEY_FILE', key_path)

    token = rando_stuff_tab._encrypt_cookie('cookie-secret')

    assert token.startswith(('dpapi:', 'fernet:'))
    assert 'cookie-secret' not in token
    assert rando_stuff_tab._decrypt_cookie(token) == 'cookie-secret'


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS-specific cookie storage')
def test_macos_cookie_storage_uses_fernet_key(tmp_path, monkeypatch):
    key_path = tmp_path / 'accounts.key'
    monkeypatch.setattr(rando_stuff_tab, 'ACCOUNTS_KEY_FILE', key_path)

    token = rando_stuff_tab._encrypt_cookie('cookie-secret')

    assert token.startswith('fernet:')
    assert 'cookie-secret' not in token
    assert rando_stuff_tab._decrypt_cookie(token) == 'cookie-secret'
    assert key_path.exists()
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_auth_ticket_app_uri_builder_is_deterministic():
    uri = rando_stuff_tab._build_auth_ticket_app_uri('ticket-123', launch_time_ms=12345)

    assert uri == (
        'roblox-player:1+launchmode:app+gameinfo:ticket-123+launchtime:12345'
        '+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp'
    )


def test_auth_ticket_place_uri_builder_covers_normal_place():
    uri = rando_stuff_tab._build_auth_ticket_place_uri(
        'ticket-123',
        '1818',
        tracker_id=11111111111,
        join_attempt_id='join-1',
        launch_time_ms=12345,
    )
    decoded = unquote(uri)

    assert decoded.startswith('roblox-player:1+launchmode:play+gameinfo:ticket-123+launchtime:12345+')
    assert 'request=RequestGame' in decoded
    assert 'placeId=1818' in decoded
    assert 'browsertrackerid:11111111111' in decoded


def test_auth_ticket_place_uri_builder_covers_job_id():
    uri = rando_stuff_tab._build_auth_ticket_place_uri(
        'ticket-123',
        '1818',
        job_id='00000000-0000-0000-0000-000000000001',
        tracker_id=11111111111,
        join_attempt_id='join-1',
        launch_time_ms=12345,
    )
    decoded = unquote(uri)

    assert 'request=RequestGameJob' in decoded
    assert 'gameId=00000000-0000-0000-0000-000000000001' in decoded


def test_auth_ticket_private_server_uri_builder_covers_link_launch():
    uri = rando_stuff_tab._build_auth_ticket_private_server_uri(
        'ticket-123',
        '1818',
        access_code='access-123',
        link_code='link-123',
        tracker_id=11111111111,
        join_attempt_id='join-1',
        launch_time_ms=12345,
    )
    decoded = unquote(uri)

    assert 'request=RequestPrivateGame' in decoded
    assert 'placeId=1818' in decoded
    assert 'accessCode=access-123' in decoded
    assert 'linkCode=link-123' in decoded
