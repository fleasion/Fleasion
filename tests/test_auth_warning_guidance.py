from fleasion.app import _windows_auth_profile_matches_username


def _details(**overrides):
    details = {
        'username': 'josei',
        'userprofile': r'C:\Users\josei',
        'local_appdata': r'C:\Users\josei\AppData\Local',
        'default_cookie_path': (
            r'C:\Users\josei\AppData\Local\Roblox\LocalStorage\RobloxCookies.dat'
        ),
    }
    details.update(overrides)
    return details


def test_windows_auth_profile_match_detects_coherent_same_user_paths():
    assert _windows_auth_profile_matches_username(_details())


def test_windows_auth_profile_match_is_case_insensitive():
    assert _windows_auth_profile_matches_username(
        _details(
            username='JOSEI',
            userprofile=r'c:\users\josei',
            local_appdata=r'C:\Users\JOSEI\AppData\Local',
        )
    )


def test_windows_auth_profile_match_rejects_different_profile_username():
    assert not _windows_auth_profile_matches_username(
        _details(username='Admin', userprofile=r'C:\Users\josei')
    )


def test_windows_auth_profile_match_rejects_local_appdata_from_other_profile():
    assert not _windows_auth_profile_matches_username(
        _details(local_appdata=r'C:\Users\Admin\AppData\Local')
    )


def test_windows_auth_profile_match_rejects_cookie_path_outside_local_appdata():
    assert not _windows_auth_profile_matches_username(
        _details(
            default_cookie_path=(
                r'C:\Users\Admin\AppData\Local\Roblox\LocalStorage\RobloxCookies.dat'
            )
        )
    )
