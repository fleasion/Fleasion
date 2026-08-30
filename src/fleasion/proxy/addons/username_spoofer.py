"""UsernameSpoofer: rewrites Roblox profile and gamejoin creator metadata."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING, Protocol, TypedDict, TypeIs

import requests

from fleasion.utils import log_buffer

if TYPE_CHECKING:
    from fleasion.config.manager import JsonObject, JsonValue
    from fleasion.proxy.server import ProxyFlow

PROFILE_ENDPOINT_FRAGMENT = '/v1/user/profiles/get-profiles'
GAMEJOIN_ENDPOINT_FRAGMENTS = (
    '/v1/join-game',
    '/v1/join-game-instance',
    '/v1/join-reserved-game',
)
EMPTY_NAME_SENTINEL = '\u200b'
NAME_KEYS = (
    'username',
    'displayName',
    'combinedName',
    'inExperienceCombinedName',
    'contactName',
    'platformName',
    'alias',
)


class _SpooferState(TypedDict):
    save_settings: bool
    others_name: str
    others_apply_ingame: bool
    others_verified: bool
    self_name: str
    self_apply_ingame: bool
    self_verified: bool
    self_game_creator: bool


class _ConfigSource(Protocol):
    @property
    def username_spoofer(self) -> JsonObject: ...


def _is_object_list(value: object) -> TypeIs[list[object]]:
    return isinstance(value, list)


def _is_object_dict(value: object) -> TypeIs[dict[object, object]]:
    return isinstance(value, dict)


def _is_json_value(value: object) -> TypeIs[JsonValue]:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if _is_object_list(value):
        return all(_is_json_value(item) for item in value)
    if _is_object_dict(value):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _load_json(data: str) -> JsonValue:
    value: object = json.loads(data)
    if TYPE_CHECKING:
        assert _is_json_value(value)
    return value


def _json_object(value: JsonValue) -> JsonObject:
    if TYPE_CHECKING:
        assert isinstance(value, dict)
    return value


class UsernameSpoofer:
    """Central username spoofer state and response modifier."""

    def __init__(self, config_manager: _ConfigSource | None = None) -> None:
        self._config = config_manager
        self._lock = threading.Lock()
        self._current_user_id: str | None = None
        self._current_username = ''
        self._runtime_state = self._load_state_from_config()

    @staticmethod
    def _default_state() -> _SpooferState:
        return {
            'save_settings': False,
            'others_name': '',
            'others_apply_ingame': False,
            'others_verified': False,
            'self_name': '',
            'self_apply_ingame': False,
            'self_verified': False,
            'self_game_creator': False,
        }

    def _load_state_from_config(self) -> _SpooferState:
        state = self._default_state()
        if self._config is None:
            return state
        saved = self._config.username_spoofer
        merged: JsonObject = {
            'save_settings': state['save_settings'],
            'others_name': state['others_name'],
            'others_apply_ingame': state['others_apply_ingame'],
            'others_verified': state['others_verified'],
            'self_name': state['self_name'],
            'self_apply_ingame': state['self_apply_ingame'],
            'self_verified': state['self_verified'],
            'self_game_creator': state['self_game_creator'],
        }
        merged.update(saved)
        if not merged.get('save_settings', False):
            return self._default_state()
        return self._normalize_state(merged)

    def _normalize_state(self, state: JsonObject) -> _SpooferState:
        base = self._default_state()
        return {
            'save_settings': bool(state.get('save_settings', base['save_settings'])),
            'others_name': str(state.get('others_name', base['others_name'])),
            'others_apply_ingame': bool(
                state.get('others_apply_ingame', base['others_apply_ingame'])
            ),
            'others_verified': bool(state.get('others_verified', base['others_verified'])),
            'self_name': str(state.get('self_name', base['self_name'])),
            'self_apply_ingame': bool(state.get('self_apply_ingame', base['self_apply_ingame'])),
            'self_verified': bool(state.get('self_verified', base['self_verified'])),
            'self_game_creator': bool(state.get('self_game_creator', base['self_game_creator'])),
        }

    @staticmethod
    def _state_enabled(state: _SpooferState) -> bool:
        return bool(
            state.get('others_apply_ingame')
            or state.get('others_verified')
            or state.get('self_apply_ingame')
            or state.get('self_verified')
            or state.get('self_game_creator')
        )

    def is_enabled(self) -> bool:
        with self._lock:
            return self._state_enabled(self._runtime_state)

    def set_runtime_state(self, state: JsonObject) -> None:
        normalized = self._normalize_state(state)
        with self._lock:
            self._runtime_state = normalized

    def set_current_user(self, user_id: str | None, username: str) -> None:
        normalized_user_id = str(user_id) if user_id is not None else None
        normalized_username = str(username or '')
        with self._lock:
            self._current_user_id = normalized_user_id
            self._current_username = normalized_username

    @staticmethod
    def _is_own_profile(
        profile: JsonObject, current_user_id: str | None, current_username: str
    ) -> bool:
        profile_user_id = profile.get('userId')
        if current_user_id and profile_user_id is not None:
            return str(profile_user_id) == current_user_id
        names = profile.get('names')
        if not isinstance(names, dict) or not current_username:
            return False
        return str(names.get('username', '')) == current_username

    @staticmethod
    def _effective_name_value(new_value: str) -> str:
        # Roblox appears to treat an empty string as "missing" and can fall
        # back to other name sources. Use a zero-width sentinel so a blank
        # spoof still renders visibly blank while remaining intentionally set.
        return EMPTY_NAME_SENTINEL if new_value == '' else new_value  # ruff: ignore[compare-to-empty-string]

    @classmethod
    def _set_name_fields(cls, profile: JsonObject, new_value: str) -> int:
        names = profile.get('names')
        if not isinstance(names, dict):
            profile['names'] = {}
            names = profile['names']
        effective_value = cls._effective_name_value(str(new_value))
        changed = 0
        for key in NAME_KEYS:
            if names.get(key) != effective_value:
                names[key] = effective_value
                changed += 1
        return changed

    def request(self, flow: ProxyFlow) -> None:  # ruff: ignore[no-self-use, unused-method-argument]
        return

    @staticmethod
    def _fetch_authenticated_user_id() -> int | None:
        from fleasion.utils.roblox_auth import (  # ruff: ignore[import-outside-top-level]
            get_roblosecurity,
        )

        cookie = get_roblosecurity()
        if not cookie:
            return None
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            sess = requests.Session()
            sess.trust_env = False
            sess.proxies = {}
            try:
                sess.cookies.set('.ROBLOSECURITY', cookie)
            except Exception:  # ruff: ignore[blind-except]
                sess.headers['Cookie'] = f'.ROBLOSECURITY={cookie};'
            resp = sess.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
            if resp.status_code != 200:  # ruff: ignore[magic-value-comparison]
                return None
            user_id = resp.json().get('id')
            return int(user_id) if user_id is not None else None
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('username-spoofer', f'Failed to fetch authenticated user id: {exc}')
            return None

    @staticmethod
    def _game_creator_type_user_value(current_value: JsonValue, key: str) -> str | int:
        if isinstance(current_value, str):
            if current_value.startswith('Enum.CreatorType.'):
                return 'Enum.CreatorType.User'
            return 'User'
        if isinstance(current_value, bool):
            return 'User'
        if isinstance(current_value, int):
            # Engine Enum.CreatorType is User=0, Group=1; web API creatorType
            # fields commonly use User=1, Group=2. The capital joinScript
            # field maps to the engine value.
            return 0 if key == 'CreatorType' else 1
        return 'User'

    @classmethod
    def _set_creator_id_type_pair(
        cls, value: JsonObject, creator_id_key: str, creator_type_key: str, user_id: int
    ) -> int:
        if creator_id_key not in value and creator_type_key not in value:
            return 0

        changed = 0
        if creator_id_key in value and value.get(creator_id_key) != user_id:
            value[creator_id_key] = user_id
            changed += 1

        if creator_type_key in value:
            user_type = cls._game_creator_type_user_value(
                value.get(creator_type_key), creator_type_key
            )
            if value.get(creator_type_key) != user_type:
                value[creator_type_key] = user_type
                changed += 1
        return changed

    @classmethod
    def _set_game_creator_fields(cls, value: JsonValue, user_id: int) -> int:
        if isinstance(value, list):
            return sum(cls._set_game_creator_fields(item, user_id) for item in value)
        if not isinstance(value, dict):
            return 0

        changed = 0
        for creator_id_key, creator_type_key in (
            ('CreatorId', 'CreatorType'),
            ('CreatorId', 'CreatorTypeEnum'),
            ('CreatorTargetId', 'CreatorType'),
            ('CreatorTargetId', 'CreatorTypeEnum'),
            ('creatorId', 'creatorType'),
            ('creatorTargetId', 'creatorType'),
        ):
            changed += cls._set_creator_id_type_pair(
                value, creator_id_key, creator_type_key, user_id
            )

        for child in value.values():
            changed += cls._set_game_creator_fields(child, user_id)
        return changed

    def _modify_gamejoin_response(self, flow: ProxyFlow) -> bool:
        if not any(fragment in flow.request.pretty_url for fragment in GAMEJOIN_ENDPOINT_FRAGMENTS):
            return False
        with self._lock:
            enabled = bool(self._runtime_state.get('self_game_creator'))
        if not enabled:
            return False
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            response = flow.response
            if TYPE_CHECKING:
                assert response is not None
            payload_object = _json_object(_load_json(response.content.decode('utf-8')))
            user_id = self._fetch_authenticated_user_id()
            if user_id is None:
                return False
            fields_changed = self._set_game_creator_fields(payload_object, user_id)
            if fields_changed <= 0:
                return False
            response.content = json.dumps(
                payload_object,
                separators=(',', ':'),
                ensure_ascii=False,
            ).encode('utf-8')
            return True  # ruff: ignore[try-consider-else]
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('username-spoofer', f'Failed to modify gamejoin response: {exc}')
            return False

    def response(self, flow: ProxyFlow) -> None:  # ruff: ignore[complex-structure, too-many-branches]
        if flow.response is None or not flow.response.content:
            return
        if self._modify_gamejoin_response(flow):
            return
        if PROFILE_ENDPOINT_FRAGMENT not in flow.request.pretty_url:
            return
        with self._lock:
            state = self._runtime_state.copy()
            current_user_id = self._current_user_id
            current_username = self._current_username
        if not (
            state.get('others_apply_ingame')
            or state.get('others_verified')
            or state.get('self_apply_ingame')
            or state.get('self_verified')
        ):
            return
        try:  # ruff: ignore[too-many-statements-in-try-clause]
            payload = _json_object(_load_json(flow.response.content.decode('utf-8')))
            profile_details = payload.get('profileDetails', [])
            if not isinstance(profile_details, list):
                return
            fields_changed = 0
            for profile in profile_details:
                if not isinstance(profile, dict):
                    continue
                if self._is_own_profile(profile, current_user_id, current_username):
                    if state.get('self_apply_ingame'):
                        fields_changed += self._set_name_fields(profile, state.get('self_name', ''))
                    if state.get('self_verified') and profile.get('isVerified') is not True:
                        profile['isVerified'] = True
                        fields_changed += 1
                elif state.get('others_apply_ingame'):
                    fields_changed += self._set_name_fields(profile, state.get('others_name', ''))
                    if state.get('others_verified') and profile.get('isVerified') is not True:
                        profile['isVerified'] = True
                        fields_changed += 1
                elif state.get('others_verified') and profile.get('isVerified') is not True:
                    profile['isVerified'] = True
                    fields_changed += 1
            if fields_changed <= 0:
                return
            flow.response.content = json.dumps(
                payload,
                separators=(',', ':'),
                ensure_ascii=False,
            ).encode('utf-8')
        except Exception as exc:  # ruff: ignore[blind-except]
            log_buffer.log('username-spoofer', f'Failed to modify profile response: {exc}')
