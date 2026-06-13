from http.cookiejar import Cookie, CookieJar
import os
import stat
import threading
import time

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


def _reset(monkeypatch):
    roblox_auth._BROWSER_COOKIE_CACHE = None
    roblox_auth._BROWSER_COOKIE_SOURCE = ""
    roblox_auth._BROWSER_AUTO_DISCOVERY_ATTEMPTED = False
    roblox_auth._AUTH_READY_COOKIE = None
    roblox_auth._LOGGED_AUTH_FAILURES.clear()


def test_browser_discovery_is_domain_and_name_scoped(monkeypatch):
    _reset(monkeypatch)
    jar = CookieJar()
    jar.set_cookie(_cookie(".ROBLOSECURITY", "secret-cookie"))
    jar.set_cookie(_cookie("other", "not-used"))
    jar.set_cookie(_cookie(".ROBLOSECURITY", "wrong-domain", domain=".example.com"))
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [("Firefox", lambda **_: jar)])

    cookie, source = roblox_auth.discover_browser_roblosecurity(explicit_import=True)

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
    monkeypatch.setattr(roblox_auth, "_get_configured_macos_auth_source", lambda: "Chrome")
    monkeypatch.setattr(
        roblox_auth,
        "discover_browser_roblosecurity",
        lambda include_keychain=False, **kwargs: (
            calls.append((include_keychain, kwargs.get("browser"))) or ("chrome-secret", "Chrome")
        ),
    )

    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == "chrome-secret"
    assert calls == [(True, "Chrome")]


def test_macos_default_lookup_is_prompt_free_by_default(monkeypatch):
    _reset(monkeypatch)
    calls = []

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_iter_user_profile_cookie_candidates", lambda: [])
    monkeypatch.setattr(roblox_auth, "_get_configured_macos_auth_source", lambda: "")
    monkeypatch.setattr(
        roblox_auth,
        "discover_browser_roblosecurity",
        lambda include_keychain=False, **kwargs: (calls.append(include_keychain) or (None, "")),
    )

    assert roblox_auth.get_roblosecurity() is None
    assert calls == [False]


def test_macos_startup_lookup_can_request_keychain_browsers(monkeypatch):
    _reset(monkeypatch)
    calls = []

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_iter_user_profile_cookie_candidates", lambda: [])
    monkeypatch.setattr(roblox_auth, "_get_configured_macos_auth_source", lambda: "Chrome")
    monkeypatch.setattr(
        roblox_auth,
        "discover_browser_roblosecurity",
        lambda include_keychain=False, **kwargs: (
            calls.append((include_keychain, kwargs.get("browser"))) or ("chrome-secret", "Chrome")
        ),
    )

    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == "chrome-secret"
    assert calls == [(True, "Chrome")]


def test_browser_discovery_can_target_selected_browser(monkeypatch):
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))
    firefox_jar = CookieJar()
    firefox_jar.set_cookie(_cookie(".ROBLOSECURITY", "firefox-secret"))
    calls = []

    def loaders(include_keychain):
        calls.append(include_keychain)
        return [
            ("Chrome", lambda **_: chrome_jar),
            ("Firefox", lambda **_: firefox_jar),
        ]

    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", loaders)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser="Firefox") == (
        "firefox-secret",
        "Firefox",
    )
    assert calls == [True]


def test_browser_discovery_does_not_write_persistent_browser_cache(tmp_path, monkeypatch):
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))
    legacy_cache = tmp_path / "old_browser_login.json"

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [("Chrome", lambda **_: chrome_jar)])
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == ("chrome-secret", "Chrome")
    assert not legacy_cache.exists()


def test_prompt_capable_browser_discovery_is_single_flight(monkeypatch):
    _reset(monkeypatch)
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))
    calls = []

    def loaders(include_keychain):
        def chrome_loader(**_kwargs):
            calls.append(include_keychain)
            time.sleep(0.02)
            return chrome_jar
        return [("Chrome", chrome_loader)]

    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", loaders)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(
                roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser="Chrome")
            )
        )
        for _ in range(5)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [("chrome-secret", "Chrome")] * 5
    assert calls == [True]


def test_manual_token_storage_is_encrypted_and_used_when_selected(tmp_path, monkeypatch):
    _reset(monkeypatch)
    token_path = tmp_path / "manual_auth_token.json"
    key_path = tmp_path / "manual_auth_token.key"

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_MANUAL_AUTH_TOKEN_FILE", token_path)
    monkeypatch.setattr(roblox_auth, "_MANUAL_AUTH_TOKEN_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_iter_user_profile_cookie_candidates", lambda: [])
    monkeypatch.setattr(roblox_auth, "_get_configured_macos_auth_source", lambda: "manual")

    assert roblox_auth.store_manual_roblosecurity("manual-secret")
    assert "manual-secret" not in token_path.read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(key_path).st_mode) == 0o600
    assert roblox_auth.get_roblosecurity(include_keychain_browsers=True) == "manual-secret"


def test_macos_wait_for_token_retries_until_notified(monkeypatch):
    _reset(monkeypatch)
    calls = []

    def fake_lookup(include_keychain_browsers=True):
        calls.append(include_keychain_browsers)
        return "ready-secret" if len(calls) >= 2 else None

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "get_roblosecurity", fake_lookup)

    def wake_later():
        time.sleep(0.01)
        roblox_auth.notify_auth_source_changed()

    threading.Thread(target=wake_later, daemon=True).start()

    assert roblox_auth.wait_for_roblosecurity(retry_interval=5) == "ready-secret"
    assert calls == [True, True]
