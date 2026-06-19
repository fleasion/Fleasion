from http.cookiejar import Cookie, CookieJar
import json
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


def _reset(monkeypatch, *, disable_persistent_cache=True):
    monkeypatch.setattr(roblox_auth, "_BROWSER_COOKIE_CACHE", None)
    monkeypatch.setattr(roblox_auth, "_BROWSER_COOKIE_SOURCE", "")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTO_DISCOVERY_ATTEMPTED", False)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_BLOCKS_AUTOMATIC_IMPORT", False)
    monkeypatch.setattr(roblox_auth, "_AUTH_READY_COOKIE", None)
    roblox_auth._LOGGED_AUTH_FAILURES.clear()
    if disable_persistent_cache:
        monkeypatch.setattr(roblox_auth, "_read_cached_browser_roblosecurity", lambda **_: (None, ""))


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


def test_macos_configured_lookup_can_request_selected_keychain_browser(monkeypatch):
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
        lambda include_keychain=False, **kwargs: (calls.append((include_keychain, kwargs)) or (None, "")),
    )

    assert roblox_auth.get_roblosecurity() is None
    assert calls == [(False, {})]


def test_macos_startup_lookup_can_request_configured_keychain_browser(monkeypatch):
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


def test_macos_chromium_cookie_files_include_modern_network_databases(tmp_path, monkeypatch):
    _reset(monkeypatch)
    base = tmp_path / "Library" / "Application Support" / "Google" / "Chrome"
    default_network = base / "Default" / "Network" / "Cookies"
    profile_network = base / "Profile 1" / "Network" / "Cookies"
    legacy_default = base / "Default" / "Cookies"
    default_network.parent.mkdir(parents=True)
    profile_network.parent.mkdir(parents=True)
    legacy_default.parent.mkdir(parents=True, exist_ok=True)
    default_network.write_text("", encoding="utf-8")
    profile_network.write_text("", encoding="utf-8")
    legacy_default.write_text("", encoding="utf-8")

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "USER_HOME", tmp_path)

    files = roblox_auth._macos_browser_cookie_files("Chrome")

    assert default_network in files
    assert profile_network in files
    assert legacy_default in files


def test_macos_safari_loader_continues_when_container_cookie_file_is_protected(tmp_path, monkeypatch):
    _reset(monkeypatch)
    legacy = tmp_path / "Library" / "Cookies" / "Cookies.binarycookies"
    container = (
        tmp_path
        / "Library"
        / "Containers"
        / "com.apple.Safari"
        / "Data"
        / "Library"
        / "Cookies"
        / "Cookies.binarycookies"
    )
    legacy.parent.mkdir(parents=True)
    container.parent.mkdir(parents=True)
    legacy.write_text("", encoding="utf-8")
    container.write_text("", encoding="utf-8")
    jar = CookieJar()
    jar.set_cookie(_cookie(".ROBLOSECURITY", "safari-secret"))
    calls = []

    def loader(cookie_file=None, **_kwargs):
        calls.append(cookie_file)
        if cookie_file == str(container):
            raise PermissionError("Operation not permitted")
        return jar

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "USER_HOME", tmp_path)

    wrapped = roblox_auth._make_browser_cookie_loader("Safari", loader)

    assert list(wrapped(domain_name="roblox.com")) == list(jar)
    assert calls == [str(legacy), str(container)]


def test_browser_discovery_tries_next_cookie_when_newest_fails_validation(monkeypatch):
    _reset(monkeypatch)
    jar = CookieJar()
    now = int(time.time())
    jar.set_cookie(_cookie(".ROBLOSECURITY", "stale-cookie", domain=".roblox.com", expires=now + 300))
    jar.set_cookie(_cookie(".ROBLOSECURITY", "valid-cookie", domain="www.roblox.com", expires=now + 200))
    validations = []

    def validate(cookie):
        validations.append(cookie)
        return cookie == "valid-cookie"

    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [("Edge", lambda **_: jar)])
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", validate)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, browser="Edge") == (
        "valid-cookie",
        "Edge",
    )
    assert validations == ["stale-cookie", "valid-cookie"]


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


def test_macos_chrome_family_cookie_is_cached_encrypted_and_reused(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    brave_jar = CookieJar()
    brave_jar.set_cookie(_cookie(".ROBLOSECURITY", "brave-secret"))
    calls = []

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [("Brave", lambda **_: brave_jar)])
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == ("brave-secret", "Brave")
    assert json.loads(cache_path.read_text(encoding="utf-8"))["source"] == "Brave"

    _reset(monkeypatch, disable_persistent_cache=False)
    calls.clear()
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == ("brave-secret", "Brave")
    assert calls == []


def test_macos_prompt_capable_cached_chrome_cookie_is_deleted_when_invalid(tmp_path, monkeypatch):
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

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (None, "")
    assert not cache_path.exists()


def test_macos_prompt_free_invalid_cached_chrome_cookie_is_preserved(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: False)
    roblox_auth._write_cached_browser_roblosecurity("chrome-secret", "Chrome")
    assert cache_path.exists()

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: False)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert cache_path.exists()


def test_macos_cached_browser_cookie_preserves_missing_key(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    calls = []

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding="utf-8")
    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert cache_path.exists()
    assert not key_path.exists()
    assert calls == [False]


def test_macos_cached_browser_cookie_missing_key_blocks_automatic_prompt(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    calls = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding="utf-8")
    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [("Chrome", lambda **_: chrome_jar)])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True) == (None, "")
    assert calls == []
    assert cache_path.exists()


def test_explicit_browser_import_overrides_ambiguous_cache_block(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    calls = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "chrome-secret"))

    cache_path.write_text('{"source":"Chrome","cookie":"encrypted"}', encoding="utf-8")
    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [("Chrome", lambda **_: chrome_jar)])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, explicit_import=True) == ("chrome-secret", "Chrome")
    assert calls == [True]


def test_explicit_browser_import_bypasses_existing_valid_cache(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    calls = []
    chrome_jar = CookieJar()
    chrome_jar.set_cookie(_cookie(".ROBLOSECURITY", "fresh-chrome-secret"))

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)
    roblox_auth._write_cached_browser_roblosecurity("stale-cache-secret", "Chrome")
    monkeypatch.setattr(
        roblox_auth,
        "_browser_cookie_loaders",
        lambda include_keychain: calls.append(include_keychain) or [("Chrome", lambda **_: chrome_jar)],
    )

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=True, explicit_import=True) == (
        "fresh-chrome-secret",
        "Chrome",
    )
    assert calls == [True]

    _reset(monkeypatch, disable_persistent_cache=False)
    monkeypatch.setattr(roblox_auth, "_validate_roblosecurity", lambda cookie: True)
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (
        "fresh-chrome-secret",
        "Chrome",
    )


def test_macos_cached_browser_cookie_preserves_malformed_json(tmp_path, monkeypatch):
    _reset(monkeypatch, disable_persistent_cache=False)
    cache_path = tmp_path / "browser_auth_cache.json"
    key_path = tmp_path / "browser_auth_cache.key"
    calls = []

    monkeypatch.setattr(roblox_auth.sys, "platform", "darwin")
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_FILE", cache_path)
    monkeypatch.setattr(roblox_auth, "_BROWSER_AUTH_CACHE_KEY_FILE", key_path)
    assert roblox_auth._get_macos_browser_auth_cipher(create=True) is not None
    cache_path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(roblox_auth, "_browser_cookie_loaders", lambda include_keychain: calls.append(include_keychain) or [])

    assert roblox_auth.discover_browser_roblosecurity(include_keychain=False) == (None, "")
    assert cache_path.exists()
    assert calls == [False]
