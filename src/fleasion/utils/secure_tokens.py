"""Encrypted local token storage helpers."""

from __future__ import annotations

import base64
import contextlib
import os
import sys
from typing import TYPE_CHECKING, Protocol, cast

from cryptography.fernet import Fernet

from .logging import log_buffer

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class _Win32Crypt(Protocol):
    CryptProtectData: Callable[
        [bytes, str | None, object | None, object | None, object | None, int], bytes
    ]
    CryptUnprotectData: Callable[
        [bytes, object | None, object | None, object | None, int], tuple[object, bytes]
    ]


win32crypt: _Win32Crypt | None = None
if sys.platform == 'win32':
    try:
        import win32crypt as _win32crypt_module  # pyright: ignore[reportMissingImports]
    except ImportError, OSError:
        pass
    else:
        win32crypt = cast('_Win32Crypt', _win32crypt_module)


def _load_or_create_fernet_key(
    key_file: Path, generate_key: Callable[[], bytes], *, create: bool
) -> bytes | None:
    if key_file.exists():
        return key_file.read_bytes().strip()
    if not create:
        return None

    key_file.parent.mkdir(parents=True, exist_ok=True)
    key = generate_key()
    flags = getattr(os, 'O_WRONLY', 1) | getattr(os, 'O_CREAT', 64) | getattr(os, 'O_EXCL', 128)
    fd = os.open(key_file, flags, 0o600)
    with os.fdopen(fd, 'wb') as key_handle:
        key_handle.write(key)
    return key


def _get_fernet_cipher(key_file: Path, *, create: bool = True) -> Fernet | None:
    try:
        key = _load_or_create_fernet_key(key_file, Fernet.generate_key, create=create)
        if key is None:
            return None
        with contextlib.suppress(OSError):
            key_file.chmod(0o600)
        return Fernet(key)
    except (OSError, ValueError) as exc:
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


def _decrypt_dpapi(encoded: str) -> str | None:
    if win32crypt is None:
        return None
    encrypted = base64.b64decode(encoded)
    return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode('utf-8')


def _decrypt_fernet(encoded: str, key_file: Path) -> str | None:
    cipher = _get_fernet_cipher(key_file, create=False)
    if cipher is None:
        return None
    return cipher.decrypt(encoded.encode('ascii')).decode('utf-8')


def _decrypt_legacy(encoded: str) -> str | None:
    encrypted = base64.b64decode(encoded)
    if win32crypt is not None:
        return win32crypt.CryptUnprotectData(encrypted, None, None, None, 0)[1].decode('utf-8')
    if sys.platform in {'darwin', 'win32'}:
        return None
    return encrypted.decode('utf-8')


def _decrypt_token_unchecked(stored: str, key_file: Path) -> str | None:
    if stored.startswith('dpapi:'):
        return _decrypt_dpapi(stored.removeprefix('dpapi:'))
    if stored.startswith('fernet:'):
        return _decrypt_fernet(stored.removeprefix('fernet:'), key_file)
    return _decrypt_legacy(stored)


def decrypt_token(stored: str, key_file: Path) -> str | None:
    """Decrypt a stored token, returning ``None`` for any backend failure."""
    with contextlib.suppress(Exception):
        return _decrypt_token_unchecked(stored, key_file)
    return None
