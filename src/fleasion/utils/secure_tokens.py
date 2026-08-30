"""Encrypted local token storage helpers."""

from __future__ import annotations

import base64
import contextlib
import os
import sys
from collections.abc import Callable  # ruff: ignore[typing-only-standard-library-import]
from pathlib import Path  # ruff: ignore[typing-only-standard-library-import]
from typing import TYPE_CHECKING, Protocol

from .logging import log_buffer


class _Win32Crypt(Protocol):
    CryptProtectData: Callable[
        [bytes, str | None, object | None, object | None, object | None, int], bytes
    ]
    CryptUnprotectData: Callable[
        [bytes, object | None, object | None, object | None, int], tuple[object, bytes]
    ]


class _FernetCipher(Protocol):
    def encrypt(self, data: bytes) -> bytes: ...
    def decrypt(self, token: bytes) -> bytes: ...


if TYPE_CHECKING:
    win32crypt: _Win32Crypt | None
else:
    try:
        import win32crypt
    except Exception:  # ruff: ignore[blind-except]
        win32crypt = None


def _get_fernet_cipher(key_file: Path, *, create: bool = True) -> _FernetCipher | None:
    try:
        from cryptography.fernet import Fernet  # ruff: ignore[import-outside-top-level]
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Auth', f'Token encryption unavailable: {type(exc).__name__}: {exc}')
        return None

    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if not key_file.exists():
            if not create:
                return None
            key_file.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            flags = (
                getattr(os, 'O_WRONLY', 1) | getattr(os, 'O_CREAT', 64) | getattr(os, 'O_EXCL', 128)
            )
            fd = os.open(key_file, flags, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(key)
        else:
            key = key_file.read_bytes().strip()
        with contextlib.suppress(OSError):
            os.chmod(key_file, 0o600)  # ruff: ignore[os-chmod]
        return Fernet(key)
    except Exception as exc:  # ruff: ignore[blind-except]
        log_buffer.log('Auth', f'Token encryption key failed: {type(exc).__name__}: {exc}')
        return None


def encrypt_token(token: str, key_file: Path) -> str:
    """Encrypt a token for local storage."""
    raw = token.encode('utf-8')
    if win32crypt is not None:
        encrypted = win32crypt.CryptProtectData(raw, None, None, None, None, 0)
        return 'dpapi:' + base64.b64encode(encrypted).decode('ascii')

    cipher = _get_fernet_cipher(key_file)
    if cipher is None:
        msg = 'No local token encryption backend is available'
        raise RuntimeError(msg)
    return 'fernet:' + cipher.encrypt(raw).decode('ascii')


def decrypt_token(stored: str, key_file: Path) -> str | None:  # ruff: ignore[too-many-return-statements]
    """Decrypt a token stored by :func:`encrypt_token`.

    Legacy unprefixed values are still accepted so existing account files can be
    read, but all new writes use an encrypted prefixed format.
    """
    try:  # ruff: ignore[too-many-statements-in-try-clause]
        if stored.startswith('dpapi:'):
            if win32crypt is None:
                return None
            encrypted = base64.b64decode(stored[len('dpapi:') :])
            return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode('utf-8')
        if stored.startswith('fernet:'):
            cipher = _get_fernet_cipher(key_file, create=False)
            if cipher is None:
                return None
            encrypted = stored[len('fernet:') :].encode('ascii')
            return cipher.decrypt(encrypted).decode('utf-8')

        encrypted = base64.b64decode(stored)
        if win32crypt is not None:
            return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode('utf-8')
        if sys.platform in {'darwin', 'win32'}:
            return None
        return encrypted.decode('utf-8')
    except Exception:  # ruff: ignore[blind-except]
        return None
