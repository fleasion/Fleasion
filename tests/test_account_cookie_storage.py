import os
import stat
import sys

import pytest

from Fleasion.gui import rando_stuff_tab


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
