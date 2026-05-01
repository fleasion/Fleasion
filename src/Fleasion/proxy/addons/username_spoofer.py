"""UsernameSpoofer: rewrites Roblox profile responses for in-game display names."""

import json
import threading

from ...utils import log_buffer

PROFILE_ENDPOINT_FRAGMENT = '/v1/user/profiles/get-profiles'
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


class UsernameSpoofer:
    """Central username spoofer state and response modifier."""

    def __init__(self, config_manager=None) -> None:
        self._config = config_manager
        self._lock = threading.Lock()
        self._current_user_id: str | None = None
        self._current_username = ''
        self._runtime_state = self._load_state_from_config()

    @staticmethod
    def _default_state() -> dict:
        return {
            'save_settings': False,
            'others_name': '',
            'others_apply_ingame': False,
            'others_verified': False,
            'self_name': '',
            'self_apply_ingame': False,
            'self_verified': False,
        }

    def _load_state_from_config(self) -> dict:
        state = self._default_state()
        if self._config is None:
            return state
        saved = getattr(self._config, 'username_spoofer', {})
        if isinstance(saved, dict):
            state.update(saved)
        if not state.get('save_settings', False):
            return self._default_state()
        return self._normalize_state(state)

    def _normalize_state(self, state: dict) -> dict:
        base = self._default_state()
        if isinstance(state, dict):
            base.update({
                'save_settings': bool(state.get('save_settings', base['save_settings'])),
                'others_name': str(state.get('others_name', base['others_name'])),
                'others_apply_ingame': bool(state.get('others_apply_ingame', base['others_apply_ingame'])),
                'others_verified': bool(state.get('others_verified', base['others_verified'])),
                'self_name': str(state.get('self_name', base['self_name'])),
                'self_apply_ingame': bool(state.get('self_apply_ingame', base['self_apply_ingame'])),
                'self_verified': bool(state.get('self_verified', base['self_verified'])),
            })
        return base

    @staticmethod
    def _state_enabled(state: dict) -> bool:
        return bool(
            state.get('others_apply_ingame')
            or state.get('others_verified')
            or state.get('self_apply_ingame')
            or state.get('self_verified')
        )

    def is_enabled(self) -> bool:
        with self._lock:
            return self._state_enabled(self._runtime_state)

    def set_runtime_state(self, state: dict) -> None:
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
    def _is_own_profile(profile: dict, current_user_id: str | None, current_username: str) -> bool:
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
        return EMPTY_NAME_SENTINEL if new_value == '' else new_value

    @classmethod
    def _set_name_fields(cls, profile: dict, new_value: str) -> int:
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

    def request(self, flow) -> None:
        return

    def response(self, flow) -> None:
        if flow.response is None or not flow.response.content:
            return
        if PROFILE_ENDPOINT_FRAGMENT not in flow.request.pretty_url:
            return
        with self._lock:
            state = dict(self._runtime_state)
            current_user_id = self._current_user_id
            current_username = self._current_username
        if not (
            state.get('others_apply_ingame')
            or state.get('others_verified')
            or state.get('self_apply_ingame')
            or state.get('self_verified')
        ):
            return
        try:
            payload = json.loads(flow.response.content.decode('utf-8'))
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
        except Exception as exc:
            log_buffer.log('username-spoofer', f'Failed to modify profile response: {exc}')
