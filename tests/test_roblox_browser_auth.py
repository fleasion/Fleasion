from http.cookiejar import Cookie, CookieJar
import json
import os
import stat

from Fleasion.utils import roblox_auth


def _cookie(name, value, domain=".roblox.com", expires=None):
    return Cookie(
        version=0,
        name=name,
        value=value,
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=True,
        expires=expires,
        discard=False,
        comment=None,
        comment_url=None,
        rest={},
        rfc2109=False,
    )


def _reset(monkeypatch, *, disable_persistent_cache=True):
    monkeypatch.setattr(roblox_auth, "_BROWSER_COOKIE_CACHE", None)
    monkeypatch.setattr(roblox_auth, "_BROWSER_COOKIE_SOURCE", "")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTO_DISCOVERY_ATTEMPTED", False)
    if disable_persistent_cache:
        monkeypatch.setattr(roblox_auth, "_read_cached_browser_roblosecurity", lambda: (None, ""))


def test_browser_discovery_is_domain_and_name_scoped(monkeypatch):
    _reset(monkeypatch)
    jar = CookieJar()
    jar.set_cookie(_cookie(".ROBLOSECURITY", "secret-cookie"))
    jar.set_cookie(_cookie("other", "not-used"))
    jar.set_cookie(_cookie(".ROBLOSECURITY", "wrong-domain", domain=".example.com"))
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [("Firefox", lambda **_: jar)])

    cookie, source = roblox_auth.discover_browser_roblosecurity()

    assert cookie == "secret-cookie"
    assert source == "Firefox"
    assert all("secret-cookie" not in entry for entry in roblox_auth.log_buffer.get_all())


def test_prompt_free_discovery_does_not_query_keychain_browsers(monkeypatch):
    _reset(monkeypatch)
    calls = []

    def loaders(include_keychain):
        calls.append(include_keychain)
        return [("Firefox", lambda **_: CookieJar())]

    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", loaders)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert calls == [False]


def test_prompt_capable_discovery_can_find_chrome_after_prompt_free_attempt(monkeypatch):
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))
    calls = []

    def loaders(include_keychain):
        calls.append(include_keychain)
        if include_keychain:
            return [("Chrome", lambda **_: chrome_jar)]
        return [("Firefox", lambda **_: CookieJar())]

    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", loaders)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == ("chrome-secret", "Chrome")
    assert calls == [False, True]


def test_prompt_capable_browser_list_includes_chrome_and_safari(monkeypatch):
    class _BrowserCookieStub:
        chrome = object()
        safari = object()
        brave = object()
        edge = object()
        chromium = object()
        opera = object()
        vivaldi = object()
        firefox = object()

    monkeypatch.setitem(__import__('sys').modules, 'browser_cookie3', _BrowserCookieStub())

    prompt_free = [name for name, _loader in roblox_auth._browser_cookie_loaders(False)]
    prompt_capable = [name for name, _loader in roblox_auth._browser_cookie_loaders(True)]

    assert prompt_free == ["Firefox"]
    assert prompt_capable[:2] == ["Chrome", "Safari"]


def test_macos_default_lookup_can_explicitly_request_keychain_browsers(monkeypatch):
    _reset(monkeypatch)
    calls = []

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_iter_user_profile_cookie_candidates", lambda: [])
    monkeypatch.setattr(
        roblox_auth,
        "discover_browser_roblosecurity",
        lambda include_keychain=False: (calls.append(include_keychain) or ("chrome-secret", "Chrome")),
    )

    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == "chrome-secret"
    assert calls == [True]


def test_macos_chrome_cookie_is_cached_encrypted_and_reused(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))
    calls = []

    def loaders(include_keychain):
        calls.append(include_keychain)
        return [("Chrome", lambda **_: chrome_jar)]

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", loaders)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == ("chrome-secret", "Chrome")
    assert calls == [True]
    assert cache_path.exists()
    assert key_path.exists()
    assert "chrome-secret" not in cache_path.read_text(encoding="utf-8")
    assert json.loads(cache_path.read_text(encoding="utf-8"))["source"] == "Chrome"
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600

    _reset(monkeypatch, disable_persistent_cache=False)
    calls.clear()
    monkeypatch.setattr(
        roblox_auth,
        "_browser_cookie_loaders",
        lambda include_keychain: calls.append(include_keychain) or [],
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == ("chrome-secret", "Chrome")
    assert calls == []


def test_macos_cached_chrome_cookie_is_deleted_when_invalid(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: None)
    roblox_auth._write_cached_browser_roblosecurity("chrome-secret", "Chrome")
    assert cache_path.exists()

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: False)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert not cache_path.exists()
