import base64
import errno
import json
import os
import stat
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Never, cast
from urllib.parse import unquote

import pytest

from fleasion.gui import rando_stuff_tab
from fleasion.proxy.server import ProxyFlow
from fleasion.utils import roblox_auth


type JsonObject = dict[str, object]


def _encrypt_cookie(cookie: str) -> str:
    callback = cast(
        'Callable[[str], str]',
        rando_stuff_tab.__dict__['_encrypt_cookie'],
    )
    return callback(cookie)


def _decrypt_cookie(token: str) -> str | None:
    callback = cast(
        'Callable[[str], str | None]',
        rando_stuff_tab.__dict__['_decrypt_cookie'],
    )
    return callback(token)


def _build_auth_ticket_app_uri(ticket: str, *, launch_time_ms: int | None = None) -> str:
    callback = cast(
        'Callable[..., str]',
        rando_stuff_tab.__dict__['_build_auth_ticket_app_uri'],
    )
    return callback(ticket, launch_time_ms=launch_time_ms)


def _build_auth_ticket_place_uri(
    ticket: str,
    place_id: str,
    *,
    job_id: str = '',
    tracker_id: int | None = None,
    join_attempt_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    callback = cast(
        'Callable[..., str]',
        rando_stuff_tab.__dict__['_build_auth_ticket_place_uri'],
    )
    return callback(
        ticket,
        place_id,
        job_id=job_id,
        tracker_id=tracker_id,
        join_attempt_id=join_attempt_id,
        launch_time_ms=launch_time_ms,
    )


def _build_auth_ticket_private_server_uri(
    ticket: str,
    place_id: str,
    *,
    access_code: str,
    link_code: str,
    tracker_id: int | None = None,
    join_attempt_id: str | None = None,
    launch_time_ms: int | None = None,
) -> str:
    callback = cast(
        'Callable[..., str]',
        rando_stuff_tab.__dict__['_build_auth_ticket_private_server_uri'],
    )
    return callback(
        ticket,
        place_id,
        access_code=access_code,
        link_code=link_code,
        tracker_id=tracker_id,
        join_attempt_id=join_attempt_id,
        launch_time_ms=launch_time_ms,
    )


def _extract_job_id(raw: str) -> str:
    callback = cast(
        'Callable[[str], str]',
        rando_stuff_tab.__dict__['_extract_job_id'],
    )
    return callback(raw)


def _preseed_root_place_for_subplace(root_place_id: str, cookie: str) -> bool:
    callback = cast(
        'Callable[[str, str], bool]',
        rando_stuff_tab.__dict__['_preseed_root_place_for_subplace'],
    )
    return callback(root_place_id, cookie)


def _rewrite_sober_cookie_header_callable() -> Callable[[str, str], str]:
    return cast(
        'Callable[[str, str], str]',
        roblox_auth.__dict__['_rewrite_sober_cookie_header'],
    )


def _requests_module() -> object:
    return cast(object, rando_stuff_tab.__dict__['_requests'])


def _owner_launch_account_thread(
    owner: rando_stuff_tab.RandoStuffTab,
    cookie: str,
    username: str,
    private_server_link: str = '',
    job_id: str = '',
    subplace_id: str = '',
) -> None:
    callback = cast(
        'Callable[[rando_stuff_tab.RandoStuffTab, str, str, str, str, str], None]',
        rando_stuff_tab.RandoStuffTab.__dict__['_launch_account_thread'],
    )
    callback(owner, cookie, username, private_server_link, job_id, subplace_id)


def _owner_write_cookie(owner: rando_stuff_tab.RandoStuffTab, cookie: str) -> None:
    callback = cast(
        'Callable[[rando_stuff_tab.RandoStuffTab, str], None]',
        rando_stuff_tab.RandoStuffTab.__dict__['_write_cookie_to_dat'],
    )
    callback(owner, cookie)


def _owner_switch_account(owner: rando_stuff_tab.RandoStuffTab) -> None:
    callback = cast(
        'Callable[[rando_stuff_tab.RandoStuffTab], None]',
        rando_stuff_tab.RandoStuffTab.__dict__['_on_switch_account'],
    )
    callback(owner)


def _as_proxy_flow(flow: object) -> ProxyFlow:
    return cast(ProxyFlow, flow)


def _protect_data(data: bytes, *_args: object) -> bytes:
    return data


def _unprotect_data(data: bytes, *_args: object) -> tuple[None, bytes]:
    return None, data


def _noop_labels(*_args: object, **_kwargs: object) -> None:
    return None


def _cookie_ticket(_cookie: str) -> str:
    return 'ticket-123'


def _find_fake_roblox_exe() -> str:
    return '/RobloxPlayerBeta.exe'


def _decrypt_cookie_secret(_value: str) -> str:
    return 'cookie-secret'


def _linux_client_sober() -> str:
    return 'Sober'


def _record_launch(targets: list[str]) -> Callable[[str], bool]:
    def launch(target: str) -> bool:
        targets.append(target)
        return True

    return launch


def _record_preseed(records: list[tuple[str, str]]) -> Callable[[str, str], bool]:
    def preseed(root_place_id: str, cookie: str) -> bool:
        records.append((root_place_id, cookie))
        return True

    return preseed


def _access_code(_place_id: str, _link_code: str, _cookie: str) -> str:
    return 'access-123'


def _noop_cookie_write(_cookie: str) -> None:
    return None


def _record_cookie_write(values: list[str]) -> Callable[[str], bool]:
    def write(cookie: str) -> bool:
        values.append(cookie)
        return True

    return write


def _deny_replace(*_args: object, **_kwargs: object) -> Never:
    raise PermissionError(errno.EACCES, 'denied')


def _fail_information(*_args: object, **_kwargs: object) -> Never:
    raise AssertionError('Linux account switching must not show the unsupported-platform dialog')


class _FakeRequest:
    def __init__(self, url: str, body: JsonObject) -> None:
        self.url = url
        self.headers = {'Content-Type': 'application/json'}
        self.raw_content = json.dumps(body).encode('utf-8')

    @property
    def pretty_url(self) -> str:
        return self.url

    @property
    def content(self) -> bytes:
        return self.raw_content


class _FakeFlow:
    def __init__(self, url: str, body: JsonObject) -> None:
        self.request = _FakeRequest(url, body)
        self.response: _FakeFlowResponse | None = None


class _FakeFlowResponse:
    def __init__(self, body: JsonObject, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {}
        self.content = json.dumps(body).encode('utf-8')


class _FakeResponse:
    def __init__(
        self,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        data: JsonObject | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self._data = data or {}

    def json(self) -> JsonObject:
        return self._data


def _account_manager_owner() -> rando_stuff_tab.RandoStuffTab:
    owner = rando_stuff_tab.RandoStuffTab.__new__(rando_stuff_tab.RandoStuffTab)
    state: dict[str, object] = {
        '_lock': threading.Lock(),
        '_subplace_blacklisted_ids': set[str](),
        '_subplace_unblock_until': 0.0,
        '_subplace_block_mode': 'block',
        '_blocked_subplace_log_at': {},
        '_account_manager_job_id': '',
        '_account_manager_capture_place_id': None,
        '_account_manager_teleport_place_id': None,
        '_doing_rejoin': False,
        '_awaiting_rejoin_response': False,
        '_active_rejoin_attempt_id': None,
        '_last_place_id': None,
        '_last_access_code': None,
        '_last_session_id': None,
        '_update_labels': _noop_labels,
    }
    owner.__dict__.update(state)
    return owner


def test_account_cookie_storage_writes_encrypted_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / 'accounts.key'
    monkeypatch.setattr(rando_stuff_tab, 'ACCOUNTS_KEY_FILE', key_path)

    token = _encrypt_cookie('cookie-secret')

    assert token.startswith(('dpapi:', 'fernet:'))
    assert 'cookie-secret' not in token
    assert _decrypt_cookie(token) == 'cookie-secret'


def test_set_roblosecurity_clears_read_only_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
            CryptProtectData=_protect_data,
            CryptUnprotectData=_unprotect_data,
        ),
    )

    assert roblox_auth.set_roblosecurity('new-cookie', cookie_path) is True

    data = json.loads(cookie_path.read_text(encoding='utf-8'))
    decoded = base64.b64decode(data['CookiesData']).decode('latin-1')
    assert decoded == '.ROBLOSECURITY\tnew-cookie'
    assert not (cookie_path.stat().st_mode & stat.S_IWRITE)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS-specific cookie storage')
def test_macos_cookie_storage_uses_fernet_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_path = tmp_path / 'accounts.key'
    monkeypatch.setattr(rando_stuff_tab, 'ACCOUNTS_KEY_FILE', key_path)

    token = _encrypt_cookie('cookie-secret')

    assert token.startswith('fernet:')
    assert 'cookie-secret' not in token
    assert _decrypt_cookie(token) == 'cookie-secret'
    assert key_path.exists()
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600


def test_auth_ticket_app_uri_builder_is_deterministic() -> None:
    uri = _build_auth_ticket_app_uri('ticket-123', launch_time_ms=12345)

    assert uri == (
        'roblox-player:1+launchmode:app+gameinfo:ticket-123+launchtime:12345'
        '+robloxLocale:en_us+gameLocale:en_us+channel:+LaunchExp:InApp'
    )


def test_auth_ticket_place_uri_builder_covers_normal_place() -> None:
    uri = _build_auth_ticket_place_uri(
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


def test_auth_ticket_place_uri_builder_covers_job_id() -> None:
    uri = _build_auth_ticket_place_uri(
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


def test_auth_ticket_private_server_uri_builder_covers_link_launch() -> None:
    uri = _build_auth_ticket_private_server_uri(
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


def test_extract_job_id_ignores_roblox_launcher_fragments() -> None:
    assert _extract_job_id('JoinPlace=1930863474;') == ''
    assert _extract_job_id('JoinPrivateGame:PlaceId=1930863474&AccessCode=abc&LinkCode=def') == ''
    assert (
        _extract_job_id('prefix 00000000-0000-0000-0000-000000000001 suffix')
        == '00000000-0000-0000-0000-000000000001'
    )


def test_account_launch_preseeds_root_for_distinct_subplace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    launched: list[str] = []
    preseeded: list[tuple[str, str]] = []

    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', _find_fake_roblox_exe)
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', _cookie_ticket)
    monkeypatch.setattr(rando_stuff_tab, 'launch_as_standard_user', _record_launch(launched))
    monkeypatch.setattr(
        rando_stuff_tab,
        '_preseed_root_place_for_subplace',
        _record_preseed(preseeded),
    )

    _owner_launch_account_thread(
        owner, 'cookie-secret', 'GullibleProkiller1', '537413528', '', '1930863474'
    )

    assert preseeded == [('537413528', 'cookie-secret')]
    assert cast('str | None', owner.__dict__['_account_manager_teleport_place_id']) == '1930863474'
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert 'request=RequestGame' in decoded
    assert 'placeId=1930863474' in decoded


def test_account_private_server_subplace_launch_preserves_private_game_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    launched: list[str] = []
    preseeded: list[tuple[str, str]] = []

    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', _find_fake_roblox_exe)
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', _cookie_ticket)
    monkeypatch.setattr(rando_stuff_tab, '_get_access_code', _access_code)
    monkeypatch.setattr(rando_stuff_tab, 'launch_as_standard_user', _record_launch(launched))
    monkeypatch.setattr(
        rando_stuff_tab,
        '_preseed_root_place_for_subplace',
        _record_preseed(preseeded),
    )

    _owner_launch_account_thread(
        owner,
        'cookie-secret',
        'GullibleProkiller1',
        'https://www.roblox.com/games/537413528/Build-A-Boat?privateServerLinkCode=link-123',
        '',
        '1930863474',
    )

    assert preseeded == [('537413528', 'cookie-secret')]
    assert cast('str | None', owner.__dict__['_account_manager_teleport_place_id']) == '1930863474'
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert 'request=RequestPrivateGame' in decoded
    assert 'placeId=1930863474' in decoded
    assert 'accessCode=access-123' in decoded
    assert 'linkCode=link-123' in decoded


def test_account_plain_windows_launch_uses_app_auth_ticket_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    launched: list[str] = []

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', True)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, '_find_roblox_exe', _find_fake_roblox_exe)
    monkeypatch.setattr(rando_stuff_tab, '_get_auth_ticket', _cookie_ticket)
    monkeypatch.setattr(rando_stuff_tab, 'launch_as_standard_user', _record_launch(launched))
    owner.__dict__['_write_cookie_to_dat'] = _noop_cookie_write

    _owner_launch_account_thread(owner, 'cookie-secret', 'KeepItComingBack0')

    assert len(launched) == 1
    assert launched[0].startswith('roblox-player:1+launchmode:app+gameinfo:ticket-123')


def _sober_cookie_fixture(
    tmp_path: Path,
    text: str,
    *,
    mode: int = 0o600,
    config: JsonObject | str | None = None,
) -> tuple[Path, Path]:
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


def _select_sober_cookie(monkeypatch: pytest.MonkeyPatch, cookie_path: Path) -> None:
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'linux')
    monkeypatch.setattr(
        roblox_auth,
        '_selected_linux_local_auth_candidate',
        lambda: (roblox_auth.SOBER_LOCAL_AUTH_PROVIDER, cookie_path),
    )


def test_linux_sober_cookie_storage_replaces_plaintext_cookie_header(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_preserves_owner_read_only_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        mode=0o400,
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True

    assert '.ROBLOSECURITY=new-cookie' in cookie_path.read_text(encoding='utf-8')
    assert stat.S_IMODE(cookie_path.stat().st_mode) == 0o400


def test_linux_sober_cookie_storage_collapses_duplicate_auth_cookies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_refuses_missing_auth_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_refuses_unknown_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
        config={'touch_mode': 'off'},
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    assert roblox_auth.set_roblosecurity('new-cookie') is True
    assert '.ROBLOSECURITY=new-cookie' in cookie_path.read_text(encoding='utf-8')


def test_linux_sober_cookie_storage_parses_commented_libsecret_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_fails_closed_on_malformed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_refuses_insecure_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_refuses_symlink(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_refuses_wrong_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_linux_sober_cookie_storage_reports_not_initialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path = (
        tmp_path / '.var' / 'app' / roblox_auth.SOBER_CLIENT.app_id / 'data' / 'sober' / 'cookies'
    )
    _select_sober_cookie(monkeypatch, cookie_path)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_not_initialized'
    assert 'Launch Sober and sign in once first' in str(exc_info.value)


def test_linux_sober_cookie_storage_keeps_original_on_replace_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    before = cookie_path.read_bytes()
    _select_sober_cookie(monkeypatch, cookie_path)
    monkeypatch.setattr(
        roblox_auth.os,
        'replace',
        _deny_replace,
    )

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'cookie_store_permission_denied'
    assert cookie_path.read_bytes() == before
    assert not list(cookie_path.parent.glob('.cookies.fleasion-*'))


def test_linux_sober_cookie_storage_detects_concurrent_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cookie_path, _config_path = _sober_cookie_fixture(
        tmp_path,
        'A=1; .ROBLOSECURITY=old-cookie; B=2',
    )
    _select_sober_cookie(monkeypatch, cookie_path)
    original_rewrite = _rewrite_sober_cookie_header_callable()

    def rewrite_after_external_change(cookie_text: str, cookie: str) -> str:
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


def test_linux_set_roblosecurity_refuses_uninstalled_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'linux')
    monkeypatch.setattr(roblox_auth, '_selected_linux_local_auth_candidate', lambda: None)

    with pytest.raises(roblox_auth.LinuxAuthWriteError) as exc_info:
        roblox_auth.set_roblosecurity('new-cookie')

    assert exc_info.value.code == 'linux_client_not_installed'


def test_linux_write_cookie_to_dat_uses_sober_local_auth_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    written: list[str] = []

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(rando_stuff_tab, 'set_roblosecurity', _record_cookie_write(written))

    _owner_write_cookie(owner, 'cookie-secret')

    assert written == ['cookie-secret']
    assert cast(bool, owner.__dict__['_account_switched']) is True


def test_linux_switch_account_writes_selected_cookie_to_sober_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    account: rando_stuff_tab.Account = {
        'username': 'LinuxUser',
        'cookie': 'encrypted-cookie',
    }
    owner.__dict__['_accounts'] = [account]
    owner.__dict__['_account_list'] = SimpleNamespace(currentRow=lambda: 0)
    selected: list[str] = []
    written: list[str] = []
    owner.__dict__['_set_selected_account'] = selected.append
    owner.__dict__['_write_cookie_to_dat'] = written.append

    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', _decrypt_cookie_secret)
    monkeypatch.setattr(rando_stuff_tab, '_linux_client_display_name', _linux_client_sober)
    monkeypatch.setattr(
        rando_stuff_tab.QMessageBox,
        'information',
        _fail_information,
    )

    _owner_switch_account(owner)

    assert written == ['cookie-secret']
    assert owner.__dict__['_last_switched_account'] is account
    assert selected == ['LinuxUser']


def test_linux_switch_account_explains_libsecret_and_does_not_select_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _account_manager_owner()
    account: rando_stuff_tab.Account = {
        'username': 'LinuxUser',
        'cookie': 'encrypted-cookie',
    }
    owner.__dict__['_accounts'] = [account]
    owner.__dict__['_account_list'] = SimpleNamespace(currentRow=lambda: 0)
    owner.__dict__['_last_switched_account'] = None
    selected: list[str] = []
    warnings: list[tuple[str, str]] = []
    owner.__dict__['_set_selected_account'] = selected.append

    def blocked(cookie: str) -> Never:
        del cookie
        raise roblox_auth.LinuxAuthWriteError(
            'libsecret_enabled',
            "Sober account switching is unavailable because Sober's use_libsecret setting is enabled.",
        )

    owner.__dict__['_write_cookie_to_dat'] = blocked
    monkeypatch.setattr(rando_stuff_tab, 'IS_WINDOWS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_MACOS', False)
    monkeypatch.setattr(rando_stuff_tab, 'IS_LINUX', True)
    monkeypatch.setattr(rando_stuff_tab, '_decrypt_cookie', _decrypt_cookie_secret)

    def show_warning(_owner: object, title: str, message: str) -> None:
        warnings.append((title, message))

    monkeypatch.setattr(rando_stuff_tab.QMessageBox, 'warning', show_warning)

    _owner_switch_account(owner)

    assert selected == []
    assert owner.__dict__['_last_switched_account'] is None
    assert warnings == [
        (
            'Account Switch Unavailable',
            "Sober account switching is unavailable because Sober's use_libsecret setting is enabled.",
        )
    ]


def test_account_subplace_root_preseed_disables_proxy_cert_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        def __init__(self) -> None:
            self.trust_env: bool = True
            self.proxies: dict[str, str] = {'https': 'proxy'}
            self.verify: bool = True
            self.headers: dict[str, str] = {}
            self.posts: list[tuple[str, dict[str, object]]] = []
            sessions.append(self)

        def post(self, url: str, **kwargs: object) -> _FakeResponse:
            self.posts.append((url, kwargs))
            if url == 'https://auth.roblox.com/v2/logout':
                return _FakeResponse(status_code=403, headers={'x-csrf-token': 'csrf'})
            return _FakeResponse(status_code=200, data={'status': 2})

    sessions: list[FakeSession] = []
    monkeypatch.setattr(_requests_module(), 'Session', FakeSession)

    assert _preseed_root_place_for_subplace('537413528', 'cookie-secret')

    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert sessions[0].proxies == {}
    assert sessions[0].verify is False
    assert sessions[0].headers['X-CSRF-TOKEN'] == 'csrf'
    post_json = cast(JsonObject, sessions[0].posts[1][1]['json'])
    assert post_json['placeId'] == 537413528
    assert post_json['isTeleport'] is True


def test_account_proxy_marks_distinct_subplace_launch_as_teleport() -> None:
    owner = _account_manager_owner()
    owner.__dict__['_account_manager_teleport_place_id'] = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': 1930863474, 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(_as_proxy_flow(flow))

    body = cast(JsonObject, json.loads(flow.request.content))
    assert body['placeId'] == 1930863474
    assert body['isTeleport'] is True


def test_account_proxy_marks_private_server_subplace_launch_as_teleport() -> None:
    owner = _account_manager_owner()
    owner.__dict__['_account_manager_teleport_place_id'] = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-private-game',
        {
            'placeId': 1930863474,
            'accessCode': 'access-123',
            'gameJoinAttemptId': 'join-1',
        },
    )

    owner.request(_as_proxy_flow(flow))

    body = cast(JsonObject, json.loads(flow.request.content))
    assert body['placeId'] == 1930863474
    assert body['accessCode'] == 'access-123'
    assert body['isTeleport'] is True

    flow.response = _FakeFlowResponse(
        {
            'jobId': 'JoinPrivateGame:PlaceId=1930863474&AccessCode=access-123&LinkCode=link-123',
            'status': 0,
        }
    )
    owner.response(_as_proxy_flow(flow))

    assert cast('str | None', owner.__dict__['_account_manager_teleport_place_id']) == '1930863474'

    retry_flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-private-game',
        {
            'placeId': 1930863474,
            'accessCode': 'access-123',
            'gameJoinAttemptId': 'join-1',
        },
    )

    owner.request(_as_proxy_flow(retry_flow))

    retry_body = cast(JsonObject, json.loads(retry_flow.request.content))
    assert retry_body['isTeleport'] is True

    retry_flow.response = _FakeFlowResponse(
        {
            'jobId': '00000000-0000-0000-0000-000000000001',
            'status': 2,
        }
    )
    owner.response(_as_proxy_flow(retry_flow))

    assert owner.__dict__['_account_manager_teleport_place_id'] is None


def test_account_proxy_does_not_mark_nonmatching_place_as_teleport() -> None:
    owner = _account_manager_owner()
    owner.__dict__['_account_manager_teleport_place_id'] = '1930863474'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': 537413528, 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(_as_proxy_flow(flow))

    body = cast(JsonObject, json.loads(flow.request.content))
    assert 'isTeleport' not in body


def test_account_proxy_preserves_teleport_when_redirecting_job_id() -> None:
    owner = _account_manager_owner()
    owner.__dict__['_account_manager_teleport_place_id'] = '1930863474'
    owner.__dict__['_account_manager_job_id'] = '00000000-0000-0000-0000-000000000001'
    flow = _FakeFlow(
        'https://gamejoin.roblox.com/v1/join-game',
        {'placeId': '1930863474', 'gameJoinAttemptId': 'join-1'},
    )

    owner.request(_as_proxy_flow(flow))

    body = cast(JsonObject, json.loads(flow.request.content))
    assert flow.request.url == 'https://gamejoin.roblox.com/v1/join-game-instance'
    assert body['gameId'] == '00000000-0000-0000-0000-000000000001'
    assert body['isTeleport'] is True
    assert owner.__dict__['_account_manager_job_id'] == ''
