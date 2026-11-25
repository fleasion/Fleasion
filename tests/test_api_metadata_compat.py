from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, cast

from fleasion import utils
from fleasion.proxy.server import PendingIntercept
from fleasion.utils import anim_converter, updater

if TYPE_CHECKING:
    import pytest

_EXPECTED_UTILS_ALL = (
    'APP_AUTHOR',
    'APP_LOGIC',
    'APP_CONCEPT',
    'APP_DISCORD',
    'APP_REPO',
    'APP_NAME',
    'APP_VERSION',
    'APP_CACHE_DIR',
    'CLOG_URL',
    'CONFIG_DIR',
    'CONFIG_FILE',
    'CONFIGS_FOLDER',
    'ICON_FILENAME',
    'LOCAL_APPDATA',
    'LOG_FILE',
    'LOGS_DIR',
    'MACOS_PROXY_BACKEND_PORT',
    'MACOS_PROXY_HELPER_CONTROL_PORT',
    'MOD_CACHE_DIR',
    'MOD_ORIGINALS_DIR',
    'MODIFICATIONS_JSON',
    'ORIGINALS_DIR',
    'PREJSONS_DIR',
    'PROXY_CA_DIR',
    'PROXY_PORT',
    'PROXY_TARGET_HOST',
    'REPLACEMENTS_DIR',
    'ROBLOX_PROCESS',
    'ROBLOX_STUDIO_PROCESS',
    'STORAGE_DB',
    'STORAGE_DB_GDK',
    'STRIPPABLE_ASSET_TYPES',
    'USER_HOME',
    'LogBuffer',
    'TimeTracker',
    'time_tracker',
    'delete_cache',
    'get_icon_path',
    'format_count',
    'get_roblox_player_exe_path',
    'get_roblox_process_identity',
    'get_roblox_studio_exe_path',
    'is_roblox_running',
    'is_studio_running',
    'log_buffer',
    'launch_as_standard_user',
    'open_folder',
    'pluralize',
    'run_in_thread',
    'start_update_check',
    'show_message_box',
    'terminate_roblox',
    'wait_for_roblox_exit',
    'wait_for_roblox_window',
)


def test_utils_exports_preserve_pre_cleanup_order() -> None:
    assert tuple(utils.__all__) == _EXPECTED_UTILS_ALL


def test_internal_slots_preserve_pre_cleanup_order() -> None:
    assert PendingIntercept.__slots__ == ('entry_id', 'stage', 'data', 'event', 'action')
    instance_type = vars(anim_converter)['_Instance']
    instance_slots = cast('tuple[str, ...]', vars(instance_type)['__slots__'])
    assert instance_slots == ('class_name', 'name', 'properties', 'children', 'parent')


def test_start_update_check_preserves_varargs_wrapper_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signature = inspect.signature(utils.start_update_check)
    parameters = list(signature.parameters.values())
    assert [parameter.name for parameter in parameters] == ['args', 'kwargs']
    assert parameters[0].kind is inspect.Parameter.VAR_POSITIONAL
    assert parameters[1].kind is inspect.Parameter.VAR_KEYWORD

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_start_update_check(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(updater, 'start_update_check', fake_start_update_check)
    utils.start_update_check('legacy', enabled=True)

    assert calls == [(('legacy',), {'enabled': True})]
