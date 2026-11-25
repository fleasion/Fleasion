import json
import stat
import sys
import threading
import time
from collections.abc import Callable, Iterable
from http.cookiejar import Cookie, CookieJar
from pathlib import Path
from typing import Never, Protocol, cast

import pytest

from fleasion.utils import roblox_auth


class _BrowserLoader(Protocol):
    def __call__(
        self,
        *,
        cookie_file: str | None = None,
        domain_name: str = '',
        key_file: str | None = None,
    ) -> Iterable[Cookie]: ...


type _BrowserLoaderEntry = tuple[str, _BrowserLoader]
type _BrowserLoadersFactory = Callable[[bool], list[_BrowserLoaderEntry]]


def _jar_loader(jar: CookieJar) -> _BrowserLoader:
    def load(
        *,
        cookie_file: str | None = None,
        domain_name: str = '',
        key_file: str | None = None,
    ) -> Iterable[Cookie]:
        del cookie_file, domain_name, key_file
        return jar

    return load


def _empty_jar_loader(
    *,
    cookie_file: str | None = None,
    domain_name: str = '',
    key_file: str | None = None,
) -> Iterable[Cookie]:
    del cookie_file, domain_name, key_file
    return CookieJar()


def _fixed_loaders(
    entries: list[_BrowserLoaderEntry], calls: list[bool] | None = None
) -> _BrowserLoadersFactory:
    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        if calls is not None:
            calls.append(include_keychain)
        return entries

    return loaders


def _validate_true(_cookie: str) -> bool:
    return True


def _validate_false(_cookie: str) -> bool:
    return False


def _validate_inconclusive(_cookie: str) -> None:
    return None


def _disabled_cached_lookup(*, delete_invalid: bool = True) -> tuple[None, str]:
    del delete_invalid
    return None, ''


def _empty_profile_candidates() -> list[Path]:
    return []


def _browser_cookie_loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
    callback = cast(
        '_BrowserLoadersFactory',
        roblox_auth.__dict__['_browser_cookie_loaders'],
    )
    return callback(include_keychain)


def _macos_browser_cookie_files(source: str) -> list[Path]:
    callback = cast(
        'Callable[[str], list[Path]]',
        roblox_auth.__dict__['_macos_browser_cookie_files'],
    )
    return callback(source)


def _make_browser_cookie_loader(source: str, loader: _BrowserLoader) -> _BrowserLoader:
    callback = cast(
        'Callable[[str, _BrowserLoader], _BrowserLoader]',
        roblox_auth.__dict__['_make_browser_cookie_loader'],
    )
    return callback(source, loader)


def _write_cached_browser_roblosecurity(cookie: str, source: str) -> None:
    callback = cast(
        'Callable[[str, str], None]',
        roblox_auth.__dict__['_write_cached_browser_roblosecurity'],
    )
    callback(cookie, source)


def _get_macos_browser_auth_cipher(*, create: bool) -> object | None:
    callback = cast(
        'Callable[[bool], object | None]',
        roblox_auth.__dict__['_get_macos_browser_auth_cipher'],
    )
    return callback(create)


def _clear_logged_auth_failures() -> None:
    failures = cast('set[str]', roblox_auth.__dict__['_LOGGED_AUTH_FAILURES'])
    failures.clear()


def _cookie(
    name: str, value: str, domain: str = '.roblox.com', expires: int | None = None
) -> Cookie:
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith('.'),
        path='/',
        path_specified=True,
        secure=True,
        expires=expires,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _reset(monkeypatch: pytest.MonkeyPatch, *, disable_persistent_cache: bool = True) -> None:
    monkeypatch.setattr(roblox_auth, '_BROWSER_COOKIE_CACHE', None)
    monkeypatch.setattr(roblox_auth, '_BROWSER_COOKIE_SOURCE', '')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTO_DISCOVERY_ATTEMPTED', False)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT', False)
    monkeypatch.setattr(roblox_auth, '_AUTH_READY_COOKIE', None)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)
    monkeypatch.setattr(roblox_auth, '_LAST_BROWSER_AUTH_ERROR_DETAILS', {})
    _clear_logged_auth_failures()
    if disable_persistent_cache:
        monkeypatch.setattr(
            roblox_auth, '_read_cached_browser_roblosecurity', _disabled_cached_lookup
        )


def test_browser_discovery_is_domain_and_name_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    jar = CookieJar()
    jar.set_cookie(_cookie('.ROBLOSECURITY', 'secret-cookie'))
    jar.set_cookie(_cookie('other', 'not-used'))
    jar.set_cookie(_cookie('.ROBLOSECURITY', 'wrong-domain', domain='.example.com'))
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Firefox', _jar_loader(jar))]),
    )

    cookie, source = roblox_auth.discover_browser_roblosecurity(explicit_import=True)

    assert cookie == 'secret-cookie'
    assert source == 'Firefox'
    assert all('secret-cookie' not in entry for entry in roblox_auth.log_buffer.get_all())


def test_prompt_free_discovery_does_not_query_keychain_browsers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch)
    calls: list[bool] = []

    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        calls.append(include_keychain)
        return [('Firefox', _empty_jar_loader)]

    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', loaders)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert calls == [False]


def test_prompt_capable_discovery_can_find_chrome_after_prompt_free_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))
    calls: list[bool] = []

    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        calls.append(include_keychain)
        if include_keychain:
            return [('Chrome', _jar_loader(chrome_jar))]
        return [('Firefox', _empty_jar_loader)]

    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', loaders)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (
        'chrome-secret',
        'Chrome',
    )
    assert calls == [False, True]


def test_prompt_capable_browser_list_includes_chrome_and_safari(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _BrowserCookieStub:
        chrome = object()
        safari = object()
        brave = object()
        edge = object()
        chromium = object()
        opera = object()
        vivaldi = object()
        firefox = object()

    monkeypatch.setattr(roblox_auth, '_browser_cookie3', _BrowserCookieStub())

    prompt_free = [name for name, _loader in _browser_cookie_loaders(False)]
    prompt_capable = [name for name, _loader in _browser_cookie_loaders(True)]

    assert prompt_free == ['Firefox']
    assert prompt_capable[:2] == ['Chrome', 'Safari']


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS keychain browser selection')
def test_macos_configured_lookup_can_request_selected_keychain_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch)
    calls: list[tuple[bool, object | None]] = []

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        roblox_auth, '_iter_user_profile_cookie_candidates', _empty_profile_candidates
    )
    monkeypatch.setattr(roblox_auth, '_get_configured_macos_auth_source', lambda: 'Chrome')

    def discover_browser(
        include_keychain: bool = False, **kwargs: object
    ) -> tuple[str | None, str]:
        calls.append((include_keychain, kwargs.get('browser')))
        return 'chrome-secret', 'Chrome'

    monkeypatch.setattr(roblox_auth, 'discover_browser_roblosecurity', discover_browser)

    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == 'chrome-secret'
    assert calls == [(True, 'Chrome')]


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS keychain browser selection')
def test_macos_default_lookup_is_prompt_free_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    calls: list[tuple[bool, dict[str, object]]] = []

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        roblox_auth, '_iter_user_profile_cookie_candidates', _empty_profile_candidates
    )
    monkeypatch.setattr(roblox_auth, '_get_configured_macos_auth_source', lambda: '')

    def discover_browser(
        include_keychain: bool = False, **kwargs: object
    ) -> tuple[str | None, str]:
        calls.append((include_keychain, kwargs))
        return None, ''

    monkeypatch.setattr(roblox_auth, 'discover_browser_roblosecurity', discover_browser)

    assert roblox_auth.get_roblosecurity() is None
    assert calls == [(False, {})]


def test_browser_discovery_can_target_selected_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))
    firefox_jar = CookieJar()
    firefox_jar.set_cookie(_cookie('.ROBLOSECURITY', 'firefox-secret'))
    calls: list[bool] = []

    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        calls.append(include_keychain)
        return [
            ('Chrome', _jar_loader(chrome_jar)),
            ('Firefox', _jar_loader(firefox_jar)),
        ]

    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', loaders)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser='Firefox') == (
        'firefox-secret',
        'Firefox',
    )
    assert calls == [True]


def test_macos_chromium_cookie_files_include_modern_network_databases(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    base = tmp_path / 'Library' / 'Application Support' / 'Google' / 'Chrome'
    default_network = base / 'Default' / 'Network' / 'Cookies'
    profile_network = base / 'Profile 1' / 'Network' / 'Cookies'
    legacy_default = base / 'Default' / 'Cookies'
    default_network.parent.mkdir(parents=True)
    profile_network.parent.mkdir(parents=True)
    legacy_default.parent.mkdir(parents=True, exist_ok=True)
    default_network.write_text('', encoding='utf-8')
    profile_network.write_text('', encoding='utf-8')
    legacy_default.write_text('', encoding='utf-8')

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'USER_HOME', tmp_path)

    files = _macos_browser_cookie_files('Chrome')

    assert default_network in files
    assert profile_network in files
    assert legacy_default in files


def test_macos_firefox_cookie_files_include_modern_and_legacy_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    profiles = tmp_path / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
    modern = profiles / 'abc123.default-release' / 'cookies.sqlite'
    legacy = profiles / 'xyz789.default' / 'cookies.sqlite'
    modern.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    modern.write_text('', encoding='utf-8')
    legacy.write_text('', encoding='utf-8')

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'USER_HOME', tmp_path)

    files = _macos_browser_cookie_files('Firefox')

    assert modern in files
    assert legacy in files


def test_macos_firefox_loader_combines_profile_cookie_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    profiles = tmp_path / 'Library' / 'Application Support' / 'Firefox' / 'Profiles'
    modern = profiles / 'abc123.default-release' / 'cookies.sqlite'
    legacy = profiles / 'xyz789.default' / 'cookies.sqlite'
    modern.parent.mkdir(parents=True)
    legacy.parent.mkdir(parents=True)
    modern.write_text('', encoding='utf-8')
    legacy.write_text('', encoding='utf-8')
    calls: list[str | None] = []

    def loader(cookie_file: str | None = None, **_kwargs: object) -> CookieJar:
        calls.append(cookie_file)
        jar = CookieJar()
        value = 'modern-secret' if cookie_file == str(modern) else 'legacy-secret'
        jar.set_cookie(_cookie('.ROBLOSECURITY', value))
        return jar

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'USER_HOME', tmp_path)

    wrapped = _make_browser_cookie_loader('Firefox', loader)
    values = {cookie.value for cookie in wrapped(domain_name='roblox.com')}

    assert calls == [str(modern), str(legacy)]
    assert values == {'modern-secret', 'legacy-secret'}


def test_macos_safari_loader_continues_when_container_cookie_file_is_protected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    legacy = tmp_path / 'Library' / 'Cookies' / 'Cookies.binarycookies'
    container = (
        tmp_path
        / 'Library'
        / 'Containers'
        / 'com.apple.Safari'
        / 'Data'
        / 'Library'
        / 'Cookies'
        / 'Cookies.binarycookies'
    )
    legacy.parent.mkdir(parents=True)
    container.parent.mkdir(parents=True)
    legacy.write_text('', encoding='utf-8')
    container.write_text('', encoding='utf-8')
    jar = CookieJar()
    jar.set_cookie(_cookie('.ROBLOSECURITY', 'safari-secret'))
    calls: list[str | None] = []

    def loader(cookie_file: str | None = None, **_kwargs: object) -> CookieJar:
        calls.append(cookie_file)
        if cookie_file == str(container):
            msg = 'Operation not permitted'
            raise PermissionError(msg)
        return jar

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'USER_HOME', tmp_path)

    wrapped = _make_browser_cookie_loader('Safari', loader)

    assert list(wrapped(domain_name='roblox.com')) == list(jar)
    assert calls == [str(legacy), str(container)]


def test_macos_safari_permission_error_marks_full_disk_access_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    container = (
        tmp_path
        / 'Library'
        / 'Containers'
        / 'com.apple.Safari'
        / 'Data'
        / 'Library'
        / 'Cookies'
        / 'Cookies.binarycookies'
    )
    container.parent.mkdir(parents=True)
    container.write_text('', encoding='utf-8')

    def loader(cookie_file: str | None = None, **_kwargs: object) -> Never:
        raise PermissionError(1, 'Operation not permitted', cookie_file)

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'USER_HOME', tmp_path)
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Safari', _make_browser_cookie_loader('Safari', loader))]),
    )

    assert roblox_auth.discover_browser_roblosecurity(
        include_keychain=True, explicit_import=True, browser='Safari'
    ) == (None, '')
    details = roblox_auth.get_last_browser_auth_error_details()
    assert details['source'] == 'Safari'
    assert details['error_type'] == 'PermissionError'
    assert details['cookie_file'] == str(container)
    assert details['full_disk_access_required'] is True


def test_browser_discovery_tries_next_cookie_when_newest_fails_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch)
    jar = CookieJar()
    now = int(time.time())
    jar.set_cookie(
        _cookie('.ROBLOSECURITY', 'stale-cookie', domain='.roblox.com', expires=now + 300)
    )
    jar.set_cookie(
        _cookie('.ROBLOSECURITY', 'valid-cookie', domain='www.roblox.com', expires=now + 200)
    )
    validations: list[str] = []

    def validate(cookie: str) -> bool:
        validations.append(cookie)
        return cookie == 'valid-cookie'

    monkeypatch.setattr(
        roblox_auth, '_browser_cookie_loaders', _fixed_loaders([('Edge', _jar_loader(jar))])
    )
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', validate)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser='Edge') == (
        'valid-cookie',
        'Edge',
    )
    assert validations == ['stale-cookie', 'valid-cookie']


def test_macos_browser_discovery_rejects_inconclusive_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _reset(monkeypatch)
    jar = CookieJar()
    jar.set_cookie(_cookie('.ROBLOSECURITY', 'maybe-cookie'))

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Firefox', _jar_loader(jar))]),
    )
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_inconclusive)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')


def test_prompt_capable_browser_discovery_is_single_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))
    calls: list[bool] = []

    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        def chrome_loader(
            *,
            cookie_file: str | None = None,
            domain_name: str = '',
            key_file: str | None = None,
        ) -> Iterable[Cookie]:
            del cookie_file, domain_name, key_file
            calls.append(include_keychain)
            time.sleep(0.02)
            return chrome_jar

        return [('Chrome', chrome_loader)]

    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', loaders)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)

    results: list[tuple[str | None, str]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser='Chrome')
            )
        )
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [('chrome-secret', 'Chrome')] * 5
    assert calls == [True]


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS credential-storage fixture')
def test_manual_token_storage_is_encrypted_and_used_when_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    token_path = tmp_path / 'manual_auth_token.json'
    key_path = tmp_path / 'manual_auth_token.key'

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_MANUAL_AUTH_TOKEN_FILE', token_path)
    monkeypatch.setattr(roblox_auth, '_MANUAL_AUTH_TOKEN_KEY_FILE', key_path)
    monkeypatch.setattr(
        roblox_auth, '_iter_user_profile_cookie_candidates', _empty_profile_candidates
    )
    monkeypatch.setattr(roblox_auth, '_get_configured_macos_auth_source', lambda: 'manual')

    assert roblox_auth.store_manual_roblosecurity('manual-secret')
    assert 'manual-secret' not in token_path.read_text(encoding='utf-8')
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == 'manual-secret'


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS credential-storage fixture')
def test_macos_invalid_manual_token_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch)
    token_path = tmp_path / 'manual_auth_token.json'
    key_path = tmp_path / 'manual_auth_token.key'

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_MANUAL_AUTH_TOKEN_FILE', token_path)
    monkeypatch.setattr(roblox_auth, '_MANUAL_AUTH_TOKEN_KEY_FILE', key_path)
    monkeypatch.setattr(
        roblox_auth, '_iter_user_profile_cookie_candidates', _empty_profile_candidates
    )
    monkeypatch.setattr(roblox_auth, '_get_configured_macos_auth_source', lambda: 'manual')

    assert roblox_auth.store_manual_roblosecurity('manual-secret')
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_false)

    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) is None


def test_macos_wait_for_token_retries_until_notified(monkeypatch: pytest.MonkeyPatch) -> None:
    _reset(monkeypatch)
    calls: list[bool] = []

    def fake_lookup(include_keychain_browsers: bool = True) -> str | None:
        calls.append(include_keychain_browsers)
        return 'ready-secret' if len(calls) >= 2 else None

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, 'get_roblosecurity', fake_lookup)

    def wake_later() -> None:
        time.sleep(0.01)
        roblox_auth.notify_auth_source_changed()

    threading.Thread(target=wake_later, daemon=True).start()

    assert roblox_auth.wait_for_roblosecurity(retry_interval=5) == 'ready-secret'
    assert calls == [True, True]


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS credential-storage fixture')
def test_macos_chrome_cookie_is_cached_encrypted_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))
    calls: list[bool] = []

    def loaders(include_keychain: bool) -> list[_BrowserLoaderEntry]:
        calls.append(include_keychain)
        return [('Chrome', _jar_loader(chrome_jar))]

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', loaders)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (
        'chrome-secret',
        'Chrome',
    )
    assert calls == [True]
    assert cache_path.exists()
    assert key_path.exists()
    assert 'chrome-secret' not in cache_path.read_text(encoding='utf-8')
    assert json.loads(cache_path.read_text(encoding='utf-8'))['source'] == 'Chrome'
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

    _reset(monkeypatch, disable_persistent_cache=False)
    calls.clear()
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (
        'chrome-secret',
        'Chrome',
    )
    assert calls == []


def test_macos_chrome_family_cookie_is_cached_encrypted_and_reused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    brave_jar = CookieJar()
    brave_jar.set_cookie(_cookie('.ROBLOSECURITY', 'brave-secret'))
    calls: list[bool] = []

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Brave', _jar_loader(brave_jar))], calls),
    )
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (
        'brave-secret',
        'Brave',
    )
    assert json.loads(cache_path.read_text(encoding='utf-8'))['source'] == 'Brave'

    _reset(monkeypatch, disable_persistent_cache=False)
    calls.clear()
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (
        'brave-secret',
        'Brave',
    )
    assert calls == []


def test_macos_prompt_capable_cached_chrome_cookie_is_deleted_when_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_inconclusive)
    _write_cached_browser_roblosecurity('chrome-secret', 'Chrome')
    assert cache_path.exists()

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_false)
    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', _fixed_loaders([]))

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (None, '')
    assert not cache_path.exists()


def test_macos_prompt_free_invalid_cached_chrome_cookie_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_false)
    _write_cached_browser_roblosecurity('chrome-secret', 'Chrome')
    assert cache_path.exists()

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_false)
    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', _fixed_loaders([]))

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert cache_path.exists()


def test_macos_cached_browser_cookie_preserves_missing_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    calls: list[bool] = []

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding='utf-8')
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert cache_path.exists()
    assert not key_path.exists()
    assert calls == [False]


def test_macos_cached_browser_cookie_missing_key_blocks_automatic_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    calls: list[bool] = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding='utf-8')
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Chrome', _jar_loader(chrome_jar))], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (None, '')
    assert calls == []
    assert cache_path.exists()


def test_explicit_browser_import_overrides_ambiguous_cache_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    calls: list[bool] = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'chrome-secret'))

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding='utf-8')
    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Chrome', _jar_loader(chrome_jar))], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(
        include_keychain=True, explicit_import=True
    ) == ('chrome-secret', 'Chrome')
    assert calls == [True]


def test_explicit_browser_import_bypasses_existing_valid_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    calls: list[bool] = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie('.ROBLOSECURITY', 'fresh-chrome-secret'))

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)
    _write_cached_browser_roblosecurity('stale-cache-secret', 'Chrome')
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([('Chrome', _jar_loader(chrome_jar))], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(
        include_keychain=True, explicit_import=True
    ) == (
        'fresh-chrome-secret',
        'Chrome',
    )
    assert calls == [True]

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, '_validate_roblosecurity', _validate_true)
    monkeypatch.setattr(roblox_auth, '_browser_cookie_loaders', _fixed_loaders([]))

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (
        'fresh-chrome-secret',
        'Chrome',
    )


def test_macos_cached_browser_cookie_preserves_malformed_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / 'browser_auth_cache.json'
    key_path = tmp_path / 'browser_auth_cache.key'
    calls: list[bool] = []

    monkeypatch.setattr(roblox_auth.sys, 'platform', 'darwin')
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_FILE', cache_path)
    monkeypatch.setattr(roblox_auth, '_BROWSER_AUTH_CACHE_KEY_FILE', key_path)
    assert _get_macos_browser_auth_cipher(create=True) is not None
    cache_path.write_text('{not json', encoding='utf-8')
    monkeypatch.setattr(
        roblox_auth,
        '_browser_cookie_loaders',
        _fixed_loaders([], calls),
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, '')
    assert cache_path.exists()
    assert calls == [False]
