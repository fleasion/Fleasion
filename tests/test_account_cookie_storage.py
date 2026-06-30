import base64
import json
import os
import stat
import sys
import threading
from types import SimpleNamespace
from urllib.parse import unquote

import pytest

from Fleasion.gui import rando_stuff_tab
from Fleasion.utils import roblox_auth


class _FakeRequest:
    def __init__(self, url: str, body: dict):
        self.url = url
        self.headers = {"Content-Type": "application/json"}
        self.raw_content = json.dumps(body).encode("utf-8")

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
        self.content = json.dumps(body).encode("utf-8")


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
    owner._subplace_block_mode = "block"
    owner._blocked_subplace_log_at = {}
    owner._account_manager_job_id = ""
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
        json.dumps({
            'CookiesData': base64.b64encode(b'.ROBLOSECURITY\told-cookie').decode('ascii'),
        }),
        encoding='utf-8',
    )
    cookie_path.chmod(0o444)
    monkeypatch.setattr(
        roblox_auth,
        'win32crypt',
        SimpleNamespace(CryptProtectData=lambda data, *_args: data),
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


def test_extract_job_id_ignores_roblox_launcher_fragments():
    assert rando_stuff_tab._extract_job_id("JoinPlace=1930863474;") == ""
    assert (
        rando_stuff_tab._extract_job_id(
            "JoinPrivateGame:PlaceId=1930863474&AccessCode=abc&LinkCode=def"
        )
        == ""
    )
    assert (
        rando_stuff_tab._extract_job_id("prefix 00000000-0000-0000-0000-000000000001 suffix")
        == "00000000-0000-0000-0000-000000000001"
    )


def test_account_launch_preseeds_root_for_distinct_subplace(monkeypatch):
    owner = _account_manager_owner()
    launched = []
    preseeded = []

    monkeypatch.setattr(rando_stuff_tab, "_find_roblox_exe", lambda: "/RobloxPlayerBeta.exe")
    monkeypatch.setattr(rando_stuff_tab, "_get_auth_ticket", lambda cookie: "ticket-123")
    monkeypatch.setattr(rando_stuff_tab, "launch_as_standard_user", lambda target: launched.append(target) or True)
    monkeypatch.setattr(
        rando_stuff_tab,
        "_preseed_root_place_for_subplace",
        lambda root_place_id, cookie: preseeded.append((root_place_id, cookie)) or True,
    )

    owner._launch_account_thread("cookie-secret", "GullibleProkiller1", "537413528", "", "1930863474")

    assert preseeded == [("537413528", "cookie-secret")]
    assert owner._account_manager_teleport_place_id == "1930863474"
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert "request=RequestGame" in decoded
    assert "placeId=1930863474" in decoded


def test_account_private_server_subplace_launch_preserves_private_game_uri(monkeypatch):
    owner = _account_manager_owner()
    launched = []
    preseeded = []

    monkeypatch.setattr(rando_stuff_tab, "_find_roblox_exe", lambda: "/RobloxPlayerBeta.exe")
    monkeypatch.setattr(rando_stuff_tab, "_get_auth_ticket", lambda cookie: "ticket-123")
    monkeypatch.setattr(rando_stuff_tab, "_get_access_code", lambda place_id, link_code, cookie: "access-123")
    monkeypatch.setattr(rando_stuff_tab, "launch_as_standard_user", lambda target: launched.append(target) or True)
    monkeypatch.setattr(
        rando_stuff_tab,
        "_preseed_root_place_for_subplace",
        lambda root_place_id, cookie: preseeded.append((root_place_id, cookie)) or True,
    )

    owner._launch_account_thread(
        "cookie-secret",
        "GullibleProkiller1",
        "https://www.roblox.com/games/537413528/Build-A-Boat?privateServerLinkCode=link-123",
        "",
        "1930863474",
    )

    assert preseeded == [("537413528", "cookie-secret")]
    assert owner._account_manager_teleport_place_id == "1930863474"
    assert len(launched) == 1
    decoded = unquote(launched[0])
    assert "request=RequestPrivateGame" in decoded
    assert "placeId=1930863474" in decoded
    assert "accessCode=access-123" in decoded
    assert "linkCode=link-123" in decoded


def test_account_plain_windows_launch_uses_app_auth_ticket_uri(monkeypatch):
    owner = _account_manager_owner()
    launched = []

    monkeypatch.setattr(rando_stuff_tab, "IS_WINDOWS", True)
    monkeypatch.setattr(rando_stuff_tab, "IS_MACOS", False)
    monkeypatch.setattr(rando_stuff_tab, "_find_roblox_exe", lambda: "/RobloxPlayerBeta.exe")
    monkeypatch.setattr(rando_stuff_tab, "_get_auth_ticket", lambda cookie: "ticket-123")
    monkeypatch.setattr(rando_stuff_tab, "launch_as_standard_user", lambda target: launched.append(target) or True)
    owner._write_cookie_to_dat = lambda cookie: None

    owner._launch_account_thread("cookie-secret", "KeepItComingBack0")

    assert len(launched) == 1
    assert launched[0].startswith("roblox-player:1+launchmode:app+gameinfo:ticket-123")


def test_account_subplace_root_preseed_disables_proxy_cert_verification(monkeypatch):
    sessions = []

    class FakeSession:
        def __init__(self):
            self.trust_env = True
            self.proxies = {"https": "proxy"}
            self.verify = True
            self.headers = {}
            self.posts = []
            sessions.append(self)

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs))
            if url == "https://auth.roblox.com/v2/logout":
                return _FakeResponse(status_code=403, headers={"x-csrf-token": "csrf"})
            return _FakeResponse(status_code=200, data={"status": 2})

    monkeypatch.setattr(rando_stuff_tab._requests, "Session", FakeSession)

    assert rando_stuff_tab._preseed_root_place_for_subplace("537413528", "cookie-secret")

    assert len(sessions) == 1
    assert sessions[0].trust_env is False
    assert sessions[0].proxies == {}
    assert sessions[0].verify is False
    assert sessions[0].headers["X-CSRF-TOKEN"] == "csrf"
    assert sessions[0].posts[1][1]["json"]["placeId"] == 537413528
    assert sessions[0].posts[1][1]["json"]["isTeleport"] is True


def test_account_proxy_marks_distinct_subplace_launch_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = "1930863474"
    flow = _FakeFlow(
        "https://gamejoin.roblox.com/v1/join-game",
        {"placeId": 1930863474, "gameJoinAttemptId": "join-1"},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert body["placeId"] == 1930863474
    assert body["isTeleport"] is True


def test_account_proxy_marks_private_server_subplace_launch_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = "1930863474"
    flow = _FakeFlow(
        "https://gamejoin.roblox.com/v1/join-private-game",
        {
            "placeId": 1930863474,
            "accessCode": "access-123",
            "gameJoinAttemptId": "join-1",
        },
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert body["placeId"] == 1930863474
    assert body["accessCode"] == "access-123"
    assert body["isTeleport"] is True

    flow.response = _FakeFlowResponse({
        "jobId": "JoinPrivateGame:PlaceId=1930863474&AccessCode=access-123&LinkCode=link-123",
        "status": 0,
    })
    owner.response(flow)

    assert owner._account_manager_teleport_place_id == "1930863474"

    retry_flow = _FakeFlow(
        "https://gamejoin.roblox.com/v1/join-private-game",
        {
            "placeId": 1930863474,
            "accessCode": "access-123",
            "gameJoinAttemptId": "join-1",
        },
    )

    owner.request(retry_flow)

    retry_body = json.loads(retry_flow.request.content)
    assert retry_body["isTeleport"] is True

    retry_flow.response = _FakeFlowResponse({
        "jobId": "00000000-0000-0000-0000-000000000001",
        "status": 2,
    })
    owner.response(retry_flow)

    assert owner._account_manager_teleport_place_id is None


def test_account_proxy_does_not_mark_nonmatching_place_as_teleport():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = "1930863474"
    flow = _FakeFlow(
        "https://gamejoin.roblox.com/v1/join-game",
        {"placeId": 537413528, "gameJoinAttemptId": "join-1"},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert "isTeleport" not in body


def test_account_proxy_preserves_teleport_when_redirecting_job_id():
    owner = _account_manager_owner()
    owner._account_manager_teleport_place_id = "1930863474"
    owner._account_manager_job_id = "00000000-0000-0000-0000-000000000001"
    flow = _FakeFlow(
        "https://gamejoin.roblox.com/v1/join-game",
        {"placeId": "1930863474", "gameJoinAttemptId": "join-1"},
    )

    owner.request(flow)

    body = json.loads(flow.request.content)
    assert flow.request.url == "https://gamejoin.roblox.com/v1/join-game-instance"
    assert body["gameId"] == "00000000-0000-0000-0000-000000000001"
    assert body["isTeleport"] is True
    assert owner._account_manager_job_id == ""
