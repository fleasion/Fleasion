from __future__ import annotations

from fleasion import localization
from fleasion.startup_auth import build_auth_warning


def _windows_details(
    username: str = 'Spencer', profile: str = r'C:\Users\Spencer'
) -> dict[str, object]:
    local = profile + r'\AppData\Local'
    return {
        'username': username,
        'userprofile': profile,
        'local_appdata': local,
        'default_cookie_path': local + r'\Roblox\LocalStorage\RobloxCookies.dat',
        'home': profile,
        'attempted_paths': [local + r'\Roblox\LocalStorage\RobloxCookies.dat'],
        'existing_paths': [],
    }


def test_windows_auth_warning_distinguishes_same_profile_from_mismatched_profile() -> None:
    previous = localization.get_language()
    localization.set_language('en')
    try:
        same = build_auth_warning(_windows_details(), platform_name='win32')
        mismatch = build_auth_warning(
            _windows_details(username='Spencer', profile=r'C:\Users\Other'),
            platform_name='win32',
        )

        assert same['title'] == localization.tr('app.roblox_token_not_readable')
        assert same['can_open_login'] is False
        assert mismatch['can_open_login'] is False
        assert same['detail'] != mismatch['detail']
    finally:
        localization.set_language(previous)


def test_linux_auth_warning_offers_login_without_losing_diagnostics() -> None:
    previous = localization.get_language()
    localization.set_language('en')
    try:
        payload = build_auth_warning(
            {
                'home': '/home/player',
                'local_appdata': '/home/player/.var/app/org.vinegarhq.Sober',
                'default_cookie_path': '/home/player/.var/app/org.vinegarhq.Sober/data/auth.json',
                'attempted_paths': ['/tmp/a', '/tmp/b'],
                'existing_paths': ['/tmp/a'],
            },
            platform_name='linux',
        )

        assert payload['can_open_login'] is True
        assert payload['login_text'] == localization.tr('app.open_roblox_login')
        assert '/tmp/a' in str(payload['detail'])
    finally:
        localization.set_language(previous)


def test_macos_auth_warning_routes_to_login_source_picker() -> None:
    previous = localization.get_language()
    localization.set_language('en')
    try:
        payload = build_auth_warning(
            {
                'home': '/Users/player',
                'local_appdata': '/Users/player/Library/Application Support/Roblox',
                'default_cookie_path': '/Users/player/Library/Application Support/Roblox/RobloxCookies.dat',
                'attempted_paths': [],
                'existing_paths': [],
            },
            platform_name='darwin',
        )

        assert payload['can_open_login'] is True
        assert payload['login_text'] == localization.tr('app.roblox_login_source')
    finally:
        localization.set_language(previous)
