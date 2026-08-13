"""Encrypted Roblox account persistence for QML-facing workflows."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..utils.paths import CONFIG_DIR
from ..utils.secure_tokens import decrypt_token, encrypt_token


@dataclass(frozen=True, slots=True)
class StoredAccount:
    """A Roblox account whose cookie remains encrypted at rest."""

    username: str
    encrypted_cookie: str
    user_id: str = ''


class AccountStore:
    """Read and write the existing accounts.json format without exposing cookies."""

    def __init__(self, path: Path | None = None, key_path: Path | None = None) -> None:
        self.path = path or CONFIG_DIR / 'accounts.json'
        self.key_path = key_path or CONFIG_DIR / 'accounts.key'

    def load(self) -> list[StoredAccount]:
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError, OSError, json.JSONDecodeError:
            return []
        if not isinstance(payload, list):
            return []
        accounts: list[StoredAccount] = []
        for value in payload:
            if not isinstance(value, Mapping):
                continue
            username = str(value.get('username') or '').strip()
            encrypted_cookie = str(value.get('cookie') or '').strip()
            if not username or not encrypted_cookie:
                continue
            accounts.append(
                StoredAccount(
                    username=username,
                    encrypted_cookie=encrypted_cookie,
                    user_id=str(value.get('user_id') or value.get('userId') or ''),
                )
            )
        return accounts

    def save(self, accounts: list[StoredAccount]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: list[dict[str, Any]] = [
            {
                'username': account.username,
                'cookie': account.encrypted_cookie,
                **({'user_id': account.user_id} if account.user_id else {}),
            }
            for account in accounts
        ]
        self.path.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def create(self, username: str, cookie: str, user_id: str = '') -> StoredAccount:
        return StoredAccount(
            username=username.strip(),
            encrypted_cookie=encrypt_token(cookie.strip(), self.key_path),
            user_id=user_id.strip(),
        )

    def cookie(self, account: StoredAccount) -> str | None:
        return decrypt_token(account.encrypted_cookie, self.key_path)
