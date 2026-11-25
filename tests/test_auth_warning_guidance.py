from collections.abc import Callable
from typing import cast

from fleasion.app.dialogs import auth as dialogs_auth_module


def _details(**overrides: object) -> dict[str, object]:
    details: dict[str, object] = {
        'username': 'josei',
        'userprofile': r'C:\Users\josei',
        'local_appdata': r'C:\Users\josei\AppData\Local',
        'default_cookie_path': (
            r'C:\Users\josei\AppData\Local\Roblox\LocalStorage\RobloxCookies.dat'
        ),
    }
    details.update(overrides)
    return details


def _profile_matches(details: dict[str, object]) -> bool:
    callback = cast(
        'Callable[[dict[str, object]], bool]',
        dialogs_auth_module.__dict__['windows_auth_profile_matches_username'],
    )
    return callback(details)


def test_windows_auth_profile_match_detects_coherent_same_user_paths() -> None:
    assert _profile_matches(_details())


def test_windows_auth_profile_match_is_case_insensitive() -> None:
    assert _profile_matches(
        _details(
            username='JOSEI',
            userprofile=r'c:\users\josei',
            local_appdata=r'C:\Users\JOSEI\AppData\Local',
        )
    )


def test_windows_auth_profile_match_rejects_different_profile_username() -> None:
    assert not _profile_matches(_details(username='Admin', userprofile=r'C:\Users\josei'))


def test_windows_auth_profile_match_rejects_local_appdata_from_other_profile() -> None:
    assert not _profile_matches(_details(local_appdata=r'C:\Users\Admin\AppData\Local'))


def test_windows_auth_profile_match_rejects_cookie_path_outside_local_appdata() -> None:
    assert not _profile_matches(
        _details(
            default_cookie_path=(
                r'C:\Users\Admin\AppData\Local\Roblox\LocalStorage\RobloxCookies.dat'
            )
        )
    )
