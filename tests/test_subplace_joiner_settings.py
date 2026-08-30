from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from fleasion.gui import subplace_joiner_tab

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest

_HTTP_ERROR_STATUS = 400


class _FakeResponse:
    def __init__(self, status_code: int = 200, data: object | None = None) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> object | None:
        return self._data

    def raise_for_status(self) -> None:
        if self.status_code >= _HTTP_ERROR_STATUS:
            raise RuntimeError(self.status_code)


@dataclass(slots=True)
class _ConfigManagerStub:
    proxy_mode: str
    proxy_features_enabled: bool


class _ProxyMasterStub:
    def __init__(self, intercepted_hosts: set[str] | None = None) -> None:
        self.intercepted_hosts: set[str] = (
            set() if intercepted_hosts is None else set(intercepted_hosts)
        )

    @staticmethod
    def roblox_env_proxy_url() -> str:
        return 'http://127.0.0.1:58443'

    def hosts_intercepts_host(self, host: str) -> bool:
        return host in self.intercepted_hosts


class _SessionStub:
    def __init__(self) -> None:
        self.verify: bool | str | None = None
        self.url = ''

    def post(self, url: str, **kwargs: object) -> _FakeResponse:
        self.url = url
        self.verify = cast('bool | str | None', kwargs.get('verify'))
        return _FakeResponse(200, {'status': 2})


class _SettingsOwner(subplace_joiner_tab.SubplaceJoinerTab):
    @classmethod
    def uninitialized(cls) -> _SettingsOwner:
        owner = cls.__new__(cls)
        owner.recent_ids = []
        owner.favorites = []
        owner.set_custom_names({})
        owner.reset_place_name_cache()
        return owner

    def set_custom_names(self, names: dict[str, str]) -> None:
        self._custom_names = names

    def custom_names(self) -> dict[str, str]:
        return self._custom_names

    def place_name_cache(self) -> dict[str, str]:
        return self._place_name_cache

    def reset_place_name_cache(self) -> None:
        self._place_name_cache = {}

    def save_settings(self) -> None:
        self._save_settings()

    def load_settings(self) -> None:
        self._load_settings()

    def resolve_place_name(self, place_id: str, cookie: str = '') -> str | None:
        return self._resolve_place_name(place_id, cookie)

    def fetch_place_name(self, place_id: str, callback: Callable[[str], object]) -> None:
        self._fetch_place_name(place_id, callback)

    def launch_roblox_uri(self, target: str) -> bool:
        return self._launch_roblox_uri(target)

    def install_get(self, callback: Callable[..., _FakeResponse]) -> None:
        vars(self)['_get'] = callback

    def install_place_name_resolver(self, callback: Callable[[str, str], str | None]) -> None:
        vars(self)['_resolve_place_name'] = callback

    def install_main_callback(self, callback: Callable[[Callable[[], object]], bool]) -> None:
        vars(self)['_on_main'] = callback

    def configure_proxy(
        self,
        config_manager: _ConfigManagerStub,
        proxy_master: _ProxyMasterStub,
    ) -> None:
        self._config_manager = config_manager
        self._proxy_master = proxy_master

    def request_verify(self, url: str) -> bool | str:
        return self._request_verify(url)

    def request_get(self, url: str) -> object:
        return self._get(url)

    def join_root(self, root_place_id: int | str, cookie: str | None = None) -> bool:
        return self._join_root(root_place_id, cookie)

    def install_session(self, session: object) -> None:
        def new_session(_cookie: str | None) -> object:
            return session

        vars(self)['_new_session'] = new_session


def _settings_owner() -> _SettingsOwner:
    return _SettingsOwner.uninitialized()


def test_subplace_request_verify_uses_local_ca_only_for_active_intercept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    ca_cert = ca_dir / 'ca.crt'
    ca_cert.write_text('test-ca', encoding='utf-8')
    monkeypatch.setattr(subplace_joiner_tab, 'PROXY_CA_DIR', ca_dir)

    proxy_master = _ProxyMasterStub({'gamejoin.roblox.com', 'apis.roblox.com'})
    owner = _settings_owner()
    owner.configure_proxy(_ConfigManagerStub('hosts', True), proxy_master)

    assert owner.request_verify('https://gamejoin.roblox.com/v1/join-game') == str(ca_cert)
    assert owner.request_verify('https://apis.roblox.com/universes/v1/places/1/universe') == str(
        ca_cert
    )
    assert owner.request_verify('https://games.roblox.com/v1/games/1/servers/Public') is True

    proxy_master.intercepted_hosts.clear()
    assert owner.request_verify('https://gamejoin.roblox.com/v1/join-game') is True


def test_subplace_get_verifies_intercepted_api_host_with_local_ca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    ca_cert = ca_dir / 'ca.crt'
    ca_cert.write_text('test-ca', encoding='utf-8')
    monkeypatch.setattr(subplace_joiner_tab, 'PROXY_CA_DIR', ca_dir)

    owner = _settings_owner()
    owner.configure_proxy(
        _ConfigManagerStub('hosts', True),
        _ProxyMasterStub({'apis.roblox.com'}),
    )
    captured: dict[str, object] = {}

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        captured['url'] = url
        captured.update(kwargs)
        return _FakeResponse()

    monkeypatch.setattr(subplace_joiner_tab.requests, 'get', fake_get)
    url = 'https://apis.roblox.com/universes/v1/places/1/universe'
    owner.request_get(url)

    assert captured['url'] == url
    assert captured['verify'] == str(ca_cert)


def test_subplace_join_root_verifies_intercepted_gamejoin_with_local_ca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ca_dir = tmp_path / 'proxy_ca'
    ca_dir.mkdir()
    ca_cert = ca_dir / 'ca.crt'
    ca_cert.write_text('test-ca', encoding='utf-8')
    monkeypatch.setattr(subplace_joiner_tab, 'PROXY_CA_DIR', ca_dir)

    owner = _settings_owner()
    owner.configure_proxy(
        _ConfigManagerStub('hosts', True),
        _ProxyMasterStub({'gamejoin.roblox.com'}),
    )
    session = _SessionStub()
    owner.install_session(session)

    assert owner.join_root(12345, 'cookie')
    assert session.url == 'https://gamejoin.roblox.com/v1/join-game'
    assert session.verify == str(ca_cert)


def test_subplace_settings_save_uses_user_owned_config_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subplace_joiner_tab, 'CONFIG_DIR', tmp_path)
    legacy_dir = tmp_path / 'subplace'
    legacy_dir.mkdir()
    legacy_dir.chmod(0o555)

    owner = _settings_owner()
    owner.recent_ids = ['123', '456']
    owner.favorites = ['456']
    owner.set_custom_names({'123': 'First'})

    try:
        owner.save_settings()
    finally:
        legacy_dir.chmod(0o755)

    primary = tmp_path / 'subplace_joiner_settings.json'
    assert primary.exists()
    assert not (legacy_dir / 'settings.json').exists()
    data = json.loads(primary.read_text(encoding='utf-8'))
    assert data['recent_ids'] == ['123', '456']
    assert data['favorites'] == ['456']
    assert data['custom_names'] == {'123': 'First'}


def test_subplace_settings_loads_legacy_file_and_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subplace_joiner_tab, 'CONFIG_DIR', tmp_path)
    legacy_dir = tmp_path / 'subplace'
    legacy_dir.mkdir()
    legacy = legacy_dir / 'settings.json'
    legacy.write_text(
        json.dumps(
            {
                'recent_ids': ['987', ''],
                'favorites': ['654'],
                'custom_names': {'987': 'Legacy'},
            }
        ),
        encoding='utf-8',
    )

    owner = _settings_owner()
    owner.load_settings()

    assert owner.recent_ids == ['987']
    assert owner.favorites == ['654']
    assert owner.custom_names() == {'987': 'Legacy'}
    primary = tmp_path / 'subplace_joiner_settings.json'
    assert primary.exists()
    assert json.loads(primary.read_text(encoding='utf-8'))['recent_ids'] == ['987']


def test_subplace_settings_prefers_primary_over_legacy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subplace_joiner_tab, 'CONFIG_DIR', tmp_path)
    legacy_dir = tmp_path / 'subplace'
    legacy_dir.mkdir()
    (legacy_dir / 'settings.json').write_text(
        json.dumps({'recent_ids': ['111'], 'favorites': [], 'custom_names': {}}),
        encoding='utf-8',
    )
    (tmp_path / 'subplace_joiner_settings.json').write_text(
        json.dumps({'recent_ids': ['222'], 'favorites': ['333'], 'custom_names': {}}),
        encoding='utf-8',
    )

    owner = _settings_owner()
    owner.load_settings()

    assert owner.recent_ids == ['222']
    assert owner.favorites == ['333']


def test_subplace_recent_name_resolves_with_authenticated_multiget() -> None:
    owner = _settings_owner()
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_get(url: str, **kwargs: object) -> _FakeResponse:
        calls.append((url, kwargs))
        return _FakeResponse(200, [{'name': 'Build A Boat For Treasure'}])

    owner.install_get(fake_get)

    assert owner.resolve_place_name('537413528', 'cookie-secret') == 'Build A Boat For Treasure'
    assert calls[0][1]['cookies'] == {'.ROBLOSECURITY': 'cookie-secret'}


def test_subplace_recent_name_uses_public_fallback_without_cookie() -> None:
    owner = _settings_owner()

    def fake_get(url: str, **_kwargs: object) -> _FakeResponse:
        if 'universes/v1/places' in url:
            return _FakeResponse(200, {'universeId': 210851291})
        if 'games?universeIds' in url:
            return _FakeResponse(200, {'data': [{'name': 'Build A Boat For Treasure'}]})
        raise AssertionError(url)

    owner.install_get(fake_get)

    assert owner.resolve_place_name('537413528', '') == 'Build A Boat For Treasure'


def test_subplace_recent_name_failure_does_not_cache_raw_place_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _settings_owner()
    done = threading.Event()
    callbacks: list[str] = []

    def resolve_none(_place_id: str, _cookie: str = '') -> None:
        return None

    def run_on_main(fn: Callable[[], object]) -> bool:
        fn()
        return True

    def record_callback(name: str) -> None:
        callbacks.append(name)
        done.set()

    monkeypatch.setattr(subplace_joiner_tab, '_wait_for_roblosecurity', lambda: '')
    owner.install_place_name_resolver(resolve_none)
    owner.install_main_callback(run_on_main)

    owner.fetch_place_name('537413528', record_callback)

    assert done.wait(2) is False
    assert callbacks == []
    assert owner.place_name_cache() == {}


def test_linux_subplace_uri_uses_sober_handler_without_env_proxy_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner = _settings_owner()
    owner.configure_proxy(
        _ConfigManagerStub(proxy_mode='env', proxy_features_enabled=True),
        _ProxyMasterStub(),
    )
    launches: list[str] = []
    target = 'roblox://experiences/start?placeId=1'

    def launch(uri: str) -> bool:
        launches.append(uri)
        return True

    monkeypatch.setattr(subplace_joiner_tab.sys, 'platform', 'linux')
    monkeypatch.setattr(subplace_joiner_tab, 'launch_as_standard_user', launch)

    assert owner.launch_roblox_uri(target)
    assert launches == [target]
