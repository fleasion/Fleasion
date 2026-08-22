import base64
import errno
import json
import os
import stat
import sys
import threading
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from fleasion.gui import rando_stuff_tab
from fleasion.utils import roblox_auth


class _FakeRequest:
    def __init__(self, url: str, body: dict):
        self.url = url
        self.headers = {'Content-Type': 'application/json'}
        self.raw_content = json.dumps(body).encode('utf-8')

    @property
    def pretty_url(self):
        return self.url

    @property
    def content(self):
        return self.raw_content


class _FakeFlow:
    def __init__(self, url: str, body: dict):
        self.request = _FakeRequest(url, body)
        self.response = None


class _FakeFlowResponse:
    def __init__(self, body: dict, status_code=200):
        self.status_code = status_code
        self.headers = {}
        self.content = json.dumps(body).encode('utf-8')


class _FakeResponse:
    def __init__(self, status_code=200, headers=None, data=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._data = data or {}

    def json(self):
        return self._data


def _account_manager_owner():
    owner = rando_stuff_tab.RandoStuffTab.__new__(rando_stuff_tab.RandoStuffTab)
    owner._lock = threading.Lock()
    owner._subplace_blacklisted_ids = set()
    owner._subplace_unblock_until = 0.0
    owner._subplace_block_mode = 'block'
    owner._blocked_subplace_log_at = {}
    owner._account_manager_job_id = ''
    owner._account_manager_capture_place_id = None
    owner._account_manager_teleport_place_id = None
    owner._doing_rejoin = False
    owner._awaiting_rejoin_response = False
    owner._active_rejoin_attempt_id = None
    owner._last_place_id = None
    owner._last_access_code = None
    owner._last_session_id = None
    owner._update_labels = lambda *_args, **_kwargs: None
    return owner


def test_account_cookie_storage_writes_encrypted_payload(tmp_path, monkeypatch):
    key_path = tmp_path / 'accounts.key'
    monkeypatch.setattr(rando_stuff_tab, 'ACCOUNTS_KEY_FILE', key_path)

    token = rando_stuff_tab._encrypt_cookie('cookie-secret')

    assert token.startswith(('dpapi:', 'fernet:'))
    assert 'cookie-secret' not in token
    assert rando_stuff_tab._decrypt_cookie(token) == 'cookie-secret'


def test_set_roblosecurity_clears_read_only_before_write(tmp_path, monkeypatch):
    cookie_path = tmp_path / 'RobloxCookies.dat'
    cookie_path.write_text(
        json.dumps(
            {
                'CookiesData': base64.b64encode(b'.ROBLOSECURITY\told-cookie').decode('ascii'),
            }
        ),
        encoding='utf-8',
    )
    cookie_path.chmod(0o444)
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'win32')
    monkeypatch.setattr(
        roblox_auth,
        'win32crypt',
        SimpleNamespace(
            CryptProtectData=lambda data, *_args: data,
            CryptUnprotectData=lambda data, *_args: (None, data),
        ),
    )

    assert roblox_auth.set_roblosecurity('new-cookie', cookie_path) is True

    data = json.loads(cookie_path.read_text(encoding='utf-8'))
    decoded = base64.b64decode(data['CookiesData']).decode('latin-1')
    assert decoded == '.ROBLOSECURITY\tnew-cookie'
    assert not (cookie_path.stat().st_mode & stat.S_IWRITE)


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

    assert decoded.startswith(
        'roblox-player:1+launchmode:play+gameinfo:ticket-123+launchtime:12345+'
    )
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


def test_extract_job_id_ignores_roblox_launcher_fragments():
    assert rando_stuff_tab._extract_job_id('JoinPlace=1930863474;') == ''
    assert (
        rando_stuff_tab._extract_job_id(
            'JoinPrivateGame:PlaceId=1930863474&AccessCode=abc&LinkCode=def'
        )
        == ''
    )
    assert (
        rando_stuff_tab._extract_job_id('prefix 00000000-0000-0000-0000-000000000001 suffix')
        == '00000000-0000-0000-0000-000000000001'
    )


def test_account_launch_preseeds_root_for_distinct_subplace(monkeypatch):
    owner = _account_manager_owner()
    launched = []
    preseeded = []

    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', lambda: '/RobloxPlayerBeta.exe')
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', lambda cookie: 'ticket-123')
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user', lambda target: launched.append(target) or True
    )
    monkeypatch.setattr(
        rando_stuff_tab,
        '_preseed_root_place_for_subplace',
        lambda root_place_id, cookie: preseeded.append((root_place_id, cookie)) or True,
    )

    owner._launch_account_thread(
        'cookie-secret', 'GullibleProkiller1', '537413528', '', '1930863474'
    )

    assert preseeded == [('537413528', 'cookie-secret')]
    assert owner._account_manager_teleport_place_id == '1930863474'
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert 'request=RequestGame' in decoded
    assert 'placeId=1930863474' in decoded


def test_account_private_server_subplace_launch_preserves_private_game_uri(monkeypatch):
    owner = _account_manager_owner()
    launched = []
    preseeded = []

    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', lambda: '/RobloxPlayerBeta.exe')
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', lambda cookie: 'ticket-123')
    monkeypatch.setattr(
        rando_stuff_tab, '_get_access_code', lambda place_id, link_code, cookie: 'access-123'
    )
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user', lambda target: launched.append(target) or True
    )
    monkeypatch.setattr(
        rando_stuff_tab,
        '_preseed_root_place_for_subplace',
        lambda root_place_id, cookie: preseeded.append((root_place_id, cookie)) or True,
    )

    owner._launch_account_thread(
        'cookie-secret',
        'GullibleProkiller1',
        'https://www.roblox.com/games/537413528/Build-A-Boat?privateServerLinkCode=link-123',
        '',
        '1930863474',
    )

    assert preseeded == [('537413528', 'cookie-secret')]
    assert owner._account_manager_teleport_place_id == '1930863474'
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert 'request=RequestPrivateGame' in decoded
    assert 'placeId=1930863474' in decoded
    assert 'accessCode=access-123' in decoded
    assert 'linkCode=link-123' in decoded


def test_account_plain_windows_launch_uses_app_auth_ticket_uri(monkeypatch):
    owner = _account_manager_owner()
    launched = []

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', True)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', lambda: '/RobloxPlayerBeta.exe')
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', lambda cookie: 'ticket-123')
    monkeypatch.setattr(
        rando_stuff_tab, 'launch_as_standard_user', lambda target: launched.append(target) or True
    )
    owner._write_cookie_to_dat = lambda cookie: None

    owner._launch_account_thread('cookie-secret', 'KeepItComingBack0')

    assert len(launched) == 1
    assert launched[0].startswith('roblox-player:1+launchmode:app+gameinfo:ticket-123')


def _sober_cookie_fixture(
    tmp_path,
    text: str,
    *,
    mode: int = 0o600,
    config: dict | str | None = None,
):
    root = tmp_path / '.var' / 'app' / roblox_auth.SOBER_CLIENT.app_id
    cookie_path = root / 'data' / 'sober' / 'cookies'
    config_path = root / 'config' / 'sober' / 'config.json'
    cookie_path.parent.mkdir(parents=True)
    cookie_path.write_text(text, encoding='utf-8')
    cookie_path.chmod(mode)
    if config is not None:
        config_path.parent.mkdir(parents=True)
        payload = config if isinstance(config, str) else json.dumps(config)
        config_path.write_text(payload, encoding='utf-8')
    return cookie_path, config_path


def _select_sober_cookie(monkeypatch, cookie_path):
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'linux')
    monkeypatch.setattr(
        roblox_auth,
        '_selected_linux_local_auth_candidate',
        lambda: (roblox_auth.SOBER_LOCAL_AUTH_PROVIDER, cookie_path),
    )


def test_linux_sober_cookie_storage_replaces_plaintext_cookie_header(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'RBXEventTrackerV2=tracker; .ROBLOSECURITY=old-cookie; OtherCookie=keep-me',
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True

    text = cookie_path.read_text(encoding='utf-8')
    assert '.ROBLOSECURITY=new-cookie' in text
    assert 'old-cookie' not in text
    assert 'RBXEventTrackerV2=tracker' in text
    assert 'OtherCookie=keep-me' in text
    assert stat.S_IMODE(cookie_path.stat().st_mode) == 0o600


def test_linux_sober_cookie_storage_preserves_owner_read_only_mode(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        mode=0o400,
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True

    assert '.ROBLOSECURITY=new-cookie' in cookie_path.read_text(encoding='utf-8')
    assert stat.S_IMODE(cookie_path.stat().st_mode) == 0o400


def test_linux_sober_cookie_storage_collapses_duplicate_auth_cookies(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        '.ROBLOSECURITY=old-one; A=1; .ROBLOSECURITY=old-two; B=2',
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True

    text = cookie_path.read_text(encoding='utf-8')
    assert text.count('.ROBLOSECURITY=') == 1
    assert '.ROBLOSECURITY=new-cookie' in text
    assert 'old-one' not in text
    assert 'old-two' not in text
    assert 'A=1' in text and 'B=2' in text


def test_linux_sober_cookie_storage_refuses_missing_auth_cookie(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'RBXEventTrackerV2=tracker; OtherCookie=keep-me',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_missing_auth_cookie'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_refuses_unknown_format(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'not-a-cookie-header',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_unknown_format'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_refuses_control_characters_in_cookie_names(
    tmp_path, monkeypatch
):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'Bad\x01Name=value; .ROBLOSECURITY=old-cookie',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_unknown_format'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_blocks_libsecret_without_touching_plaintext(
    tmp_path, monkeypatch
):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        config={'use_libsecret': True},
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'libsecret_enabled'
    assert 'use_libsecret' in str(exc_info.value)
    assert cookie_path.read_bytes() == before
    assert roblox_auth.SOBER_LOCAL_AUTH_PROVIDER.read_roblosecurity(cookie_path) is None


def test_linux_sober_cookie_storage_defaults_to_plaintext_when_libsecret_key_missing(
    tmp_path, monkeypatch
):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        config={'touch_mode': 'off'},
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True
    assert '.ROBLOSECURITY=new-cookie' in cookie_path.read_text(encoding='utf-8')


def test_linux_sober_cookie_storage_parses_commented_libsecret_config(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        config="""{
            // Sober supports comments in generated config examples.
            "use_libsecret": true,
            "note": "https://example.invalid//not-a-comment"
        }""",
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'libsecret_enabled'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_fails_closed_on_malformed_config(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        config='{broken-json',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'sober_config_unreadable'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_refuses_insecure_permissions(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        mode=0o644,
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_insecure_permissions'
    assert cookie_path.read_bytes() == before


def test_linux_sober_cookie_storage_refuses_symlink(tmp_path, monkeypatch):
    real_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    link_path = real_path.with_name('cookies-link')
    link_path.symlink_to(real_path)
    before = real_path.read_bytes()
    _select_sober_cookie(monkeypatch, link_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_unsafe_path'
    assert real_path.read_bytes() == before


def test_linux_sober_cookie_storage_refuses_wrong_owner(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    _select_sober_cookie(monkeypatch, cookie_path)
    actual_uid = cookie_path.stat().st_uid
    monkeypatch.setattr(roblox_auth.os, 'getuid', lambda: actual_uid + 1)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_wrong_owner'


def test_linux_sober_cookie_storage_reports_not_initialized(tmp_path, monkeypatch):
    cookie_path = (
        tmp_path / '.var' / 'app' / roblox_auth.SOBER_CLIENT.app_id / 'data' / 'sober' / 'cookies'
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_not_initialized'
    assert 'Launch Sober and sign in once first' in str(exc_info.value)


def test_linux_sober_cookie_storage_keeps_original_on_replace_permission_error(
    tmp_path, monkeypatch
):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)
    monkeypatch.setattr(
        roblox_auth.os,
        'replace',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError(errno.EACCES, 'denied')),
    )

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_permission_denied'
    assert cookie_path.read_bytes() == before
    assert not list(cookie_path.parent.glob('.cookies.fleasion-*'))


def test_linux_sober_cookie_storage_detects_concurrent_change(tmp_path, monkeypatch):
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    _select_sober_cookie(monkeypatch, cookie_path)
    original_rewrite = roblox_auth._rewrite_sober_cookie_header

    def rewrite_after_external_change(cookie_text, cookie):
        updated = original_rewrite(cookie_text, cookie)
        cookie_path.write_text(
            'A=external-change-with-different-size; .ROBLOSECURITY=external-cookie; B=2',
            encoding='utf-8',
        )
        cookie_path.chmod(0o600)
        return updated

    monkeypatch.setattr(roblox_auth, '_rewrite_sober_cookie_header', rewrite_after_external_change)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_changed_during_write'
    text = cookie_path.read_text(encoding='utf-8')
    assert 'external-cookie' in text
    assert 'new-cookie' not in text


def test_linux_set_roblosecurity_refuses_uninstalled_client(monkeypatch):
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'linux')
    monkeypatch.setattr(roblox_auth, '_selected_linux_local_auth_candidate', lambda: None)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'linux_client_not_installed'


def test_linux_write_cookie_to_dat_uses_sober_local_auth_storage(monkeypatch):
    owner = _account_manager_owner()
    written = []

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(
        rando_stuff_tab, 'set_roblosecurity', lambda cookie: written.append(cookie) or True
    )

    owner._write_cookie_to_dat('cookie-secret')

    assert written == ['cookie-secret']
    assert owner._account_switched is True


def test_linux_switch_account_writes_selected_cookie_to_sober_storage(monkeypatch):
    owner = _account_manager_owner()
    account = {'username': 'LinuxUser', 'cookie': 'encrypted-cookie'}
    owner._accounts = [account]
    owner._account_list = SimpleNamespace(currentRow=lambda: 0)
    selected = []
    written = []
    owner._set_selected_account = selected.append
    owner._write_cookie_to_dat = written.append

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', lambda _value: 'cookie-secret')
    monkeypatch.setattr(rando_stuff_tab, '_linux_client_display_name', lambda: 'Sober')
    monkeypatch.setattr(
        rando_stuff_tab.QMessageBox,
        'information',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('Linux account switching must not show the unsupported-platform dialog')
        ),
    )

    owner._on_switch_account()

    assert written == ['cookie-secret']
    assert owner._last_switched_account is account
    assert selected == ['LinuxUser']


def test_linux_switch_account_explains_libsecret_and_does_not_select_account(monkeypatch):
    owner = _account_manager_owner()
    account = {'username': 'LinuxUser', 'cookie': 'encrypted-cookie'}
    owner._accounts = [account]
    owner._account_list = SimpleNamespace(currentRow=lambda: 0)
    owner._last_switched_account = None
    selected = []
    warnings = []
    owner._set_selected_account = selected.append

    def blocked(_cookie):
        raise roblox_auth.LinuxAuthWriteError(
            'libsecret_enabled',
            "Sober account switching is unavailable because Sober's use_libsecret setting is enabled.",
        )

    owner._write_cookie_to_dat = blocked
    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', lambda _value: 'cookie-secret')
    monkeypatch.setattr(
        rando_stuff_tab.QMessageBox,
        'warning',
        lambda _owner, title, message: warnings.append((title, message)),
    )

    owner._on_switch_account()

    assert selected == []
    assert owner._last_switched_account is None
    assert warnings == [
        (
            'Account Switch Unavailable',
            "Sober account switching is unavailable because Sober's use_libsecret setting is enabled.",
        )
    ]


def test_account_subplace_root_preseed_disables_proxy_cert_verification(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            self.proxies = {'https': 'proxy'}
            self.verify = True
            self.headers = {}
            self.posts = []
            sessions.append(self)

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            if url == 'https://auth.roblox.com/v2/logout':
                return _FakeResponse(status_code=403, headers={'x-csrf-token': 'csrf'})
            return _FakeResponse(status_code=200, data={'status': 2})

    monkeypatch.setattr(rando_stuff_tab._requests, 'Session', FakeSession)

    assert rando_stuff_tab._preseed_root_place_for_subplace('537413528', 'cookie-secret')

    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert sessions[0].proxies == {}
    assert sessions[0].verify is False
    assert sessions[0].headers['X-CSRF-TOKEN'] == 'csrf'
    assert sessions[0].posts[1][1]['json']['placeId'] == 537413528
    assert sessions[0].posts[1][1]['json']['isTeleport'] is True


def test_account_proxy_marks_distinct_subplace_launch_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': 1930863474, 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert body['placeId'] == 1930863474
    assert body['isTeleport'] is True


def test_account_proxy_marks_private_server_subplace_launch_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-private-game',
        {
            'placeId': 1930863474,
            'accessCode': 'access-123',
            'gameJoinAttemptId': 'join-1',
        },
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert body['placeId'] == 1930863474
    assert body['accessCode'] == 'access-123'
    assert body['isTeleport'] is True

    flow.response = _FakeFlowResponse(
        {
            'jobId': 'JoinPrivateGame:PlaceId=1930863474&AccessCode=access-123&LinkCode=link-123',
            'status': 0,
        }
    )
    owner.response(flow)

    assert owner._account_manager_teleport_place_id == '1930863474'

    retry_flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-private-game',
        {
            'placeId': 1930863474,
            'accessCode': 'access-123',
            'gameJoinAttemptId': 'join-1',
        },
    )

    owner.request(retry_flow)

    retry_body = json.loads(retry_flow.request.content)
    assert retry_body['isTeleport'] is True

    retry_flow.response = _FakeFlowResponse(
        {
            'jobId': '00000000-0000-0000-0000-000000000001',
            'status': 2,
        }
    )
    owner.response(retry_flow)

    assert owner._account_manager_teleport_place_id is None


def test_account_proxy_does_not_mark_nonmatching_place_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': 537413528, 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert 'isTeleport' not in body


def test_account_proxy_preserves_teleport_when_redirecting_job_id():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = '1930863474'
    owner._account_manager_job_id = '00000000-0000-0000-0000-000000000001'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': '1930863474', 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert flow.request.url == 'https://gamejoin.roblox.com/v1/join-game-instance'
    assert body['gameId'] == '00000000-0000-0000-0000-000000000001'
    assert body['isTeleport'] is True
    assert owner._account_manager_job_id == ''
