import os
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol, TypedDict, cast

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QEvent, QSharedMemory, QUrl
from PySide6.QtWidgets import QApplication

from fleasion import __version__, app as app_module
from fleasion.app import kill_other_fleasion_instances
from fleasion.utils import macos_proxy_helper


class _ArgsCallback[ResultT](Protocol):
    def __call__(self, *args: object) -> ResultT: ...


class _KwargsCallback[ResultT](Protocol):
    def __call__(self, **kwargs: object) -> ResultT: ...


class _ArgsKwargsCallback[ResultT](Protocol):
    def __call__(self, *args: object, **kwargs: object) -> ResultT: ...


class _ParentPathsKwargsCallback[ResultT](Protocol):
    def __call__(self, parent: object, paths: list[Path], **kwargs: object) -> ResultT: ...


class _EnabledConfigKwargsCallback[ResultT](Protocol):
    def __call__(self, enabled: bool, config_dir: Path, **kwargs: object) -> ResultT: ...


class _ValuesKwargsCallback[ResultT](Protocol):
    def __call__(self, values: list[Path], **kwargs: object) -> ResultT: ...


class _CleanupTokenKwargsCallback[ResultT](Protocol):
    def __call__(self, token: str, /, **kwargs: object) -> ResultT: ...


class _HandoffKwargsCallback[ResultT](Protocol):
    def __call__(self, handoff_token: str, child_pid: int, **kwargs: object) -> ResultT: ...


class _VisibleOwner(Protocol):
    visible: bool


class _Signal0(Protocol):
    def connect(self, callback: Callable[[], None]) -> object: ...

    def disconnect(self, callback: Callable[[], None]) -> object: ...


class _SignalStr(Protocol):
    def connect(self, callback: Callable[[str], None]) -> object: ...


class _RobloxUrlEventFilterAdapter(Protocol):
    roblox_uri_received: _SignalStr

    def eventFilter(self, watched: object | None, event: object) -> bool: ...

    def start(self) -> None: ...


class _ProxyErrorInvokerAdapter(Protocol):
    retry_proxy: _Signal0

    def handle_proxy_error(self, code: str, details: object) -> None: ...


class _ShowRobloxPermissionFailure(Protocol):
    def __call__(
        self,
        parent: object | None,
        denied_dirs: Iterable[Path],
        mod_manager: object | None = None,
        *,
        on_repaired: Callable[[], None] | None = None,
        failure_text: str | None = None,
    ) -> None: ...


class _ShowRunOnBootFailure(Protocol):
    def __call__(
        self,
        parent: object | None,
        proxy_mode: str | None = None,
        *,
        enabled: bool = True,
    ) -> bool: ...


class _RepairAutostartOnce(Protocol):
    def __call__(self, requesting_user_sid: str | None = None, *, enabled: bool = True) -> int: ...


class _PollPermissionRepair(Protocol):
    def __call__(
        self,
        mod_manager: object | None,
        deadline: float,
        *,
        on_repaired: Callable[[], None] | None = None,
    ) -> None: ...


class _ArmWindowsGdkProxy(Protocol):
    def __call__(self, proxy_master: object, timeout: float = 15.0) -> bool: ...


class _RunPrivilegedHostsCleanup(Protocol):
    def __call__(self, parent: object | None = None) -> bool: ...


class _RelaunchCompletionAdapter(TypedDict, total=False):
    wait_result: int
    exit_code_read: bool
    exit_code: int | None


class _RestartHandoffPath(Protocol):
    def __call__(self, token: str, phase: str = 'ready') -> Path | None: ...


class _RunRestartHandoffParent(Protocol):
    def __call__(
        self,
        token: str,
        launcher_pid: int,
        *,
        is_launcher_alive: Callable[[], bool],
        terminate_launcher: Callable[[], None],
    ) -> bool: ...


class _AbortRestartChild(Protocol):
    def __call__(
        self,
        token: str,
        parent_pid: int,
        application_pid: int | None,
        *,
        is_launcher_alive: Callable[[], bool],
        terminate_launcher: Callable[[], None],
        timeout: float = 10.0,
    ) -> bool: ...


class _WaitForRestartMarker(Protocol):
    def __call__(
        self,
        token: str,
        phase: str,
        *,
        is_launcher_alive: Callable[[], bool],
        expected_value: int | None = None,
        timeout: float = 15.0,
    ) -> int | None: ...


class _RobloxExitMonitorFactory(Protocol):
    def __call__(
        self,
        config_manager: object,
        proxy_master: object | None = None,
        mod_manager: object | None = None,
        env_lifecycle: object | None = None,
    ) -> app_module.RobloxExitMonitor: ...


def _private_attr(target: object, name: str) -> object:
    return getattr(target, name)


_handle_single_instance_command = cast(
    'Callable[[object, object], None]', _private_attr(app_module, '_handle_single_instance_command')
)
_linux_hosts_nix_snippet = cast(
    'Callable[[object], str]', _private_attr(app_module, '_linux_hosts_nix_snippet')
)
_looks_like_macos_fleasion_command = cast(
    'Callable[[str], bool]', _private_attr(app_module, '_looks_like_macos_fleasion_command')
)
_manual_upstream_credentials_missing = cast(
    'Callable[[object], bool]', _private_attr(app_module, '_manual_upstream_credentials_missing')
)
_repair_autostart_once = cast(
    '_RepairAutostartOnce', _private_attr(app_module, '_repair_autostart_once')
)
_repair_roblox_permissions_once = cast(
    'Callable[[str | None], int]', _private_attr(app_module, '_repair_roblox_permissions_once')
)
_repair_windows_firewall_once = cast(
    'Callable[[], int]', _private_attr(app_module, '_repair_windows_firewall_once')
)
_RobloxUrlEventFilter = cast(
    'Callable[[], _RobloxUrlEventFilterAdapter]', _private_attr(app_module, '_RobloxUrlEventFilter')
)
_run_privileged_hosts_cleanup = cast(
    '_RunPrivilegedHostsCleanup', _private_attr(app_module, '_run_privileged_hosts_cleanup')
)
_should_reclaim_stale_single_instance = cast(
    'Callable[[QSharedMemory.SharedMemoryError], bool]',
    _private_attr(app_module, '_should_reclaim_stale_single_instance'),
)
_should_sync_autostart_on_launch = cast(
    'Callable[[bool], bool]', _private_attr(app_module, '_should_sync_autostart_on_launch')
)
_show_env_proxy_stale_hosts_dialog = cast(
    'Callable[[], bool]', _private_attr(app_module, '_show_env_proxy_stale_hosts_dialog')
)
_show_roblox_permission_failure = cast(
    '_ShowRobloxPermissionFailure', _private_attr(app_module, '_show_roblox_permission_failure')
)
_show_run_on_boot_failure = cast(
    '_ShowRunOnBootFailure', _private_attr(app_module, '_show_run_on_boot_failure')
)
_show_windows_upstream_firewall_dialog = cast(
    'Callable[[object], None]', _private_attr(app_module, '_show_windows_upstream_firewall_dialog')
)
_windows_ca_permission_denied_dirs = cast(
    'Callable[[object], list[Path]]',
    _private_attr(app_module, '_windows_ca_permission_denied_dirs'),
)
_prepare_env_proxy_migration = cast(
    'Callable[[object], bool]', _private_attr(app_module, '_prepare_env_proxy_migration')
)
_show_env_proxy_migration = cast(
    'Callable[[object, object], None]', _private_attr(app_module, '_show_env_proxy_migration')
)
_ProxyErrorInvoker = cast(
    'Callable[[], _ProxyErrorInvokerAdapter]', _private_attr(app_module, '_ProxyErrorInvoker')
)
_poll_roblox_permission_repair = cast(
    '_PollPermissionRepair', _private_attr(app_module, '_poll_roblox_permission_repair')
)
_append_windows_requesting_user_args = cast(
    'Callable[[list[str]], bool]', _private_attr(app_module, '_append_windows_requesting_user_args')
)
_HOSTS_CLEANUP_NOT_ADMIN_EXIT = cast(
    'int', _private_attr(app_module, '_HOSTS_CLEANUP_NOT_ADMIN_EXIT')
)
_HOSTS_CLEANUP_WRITE_FAILED_EXIT = cast(
    'int', _private_attr(app_module, '_HOSTS_CLEANUP_WRITE_FAILED_EXIT')
)
_HOSTS_CLEANUP_UNEXPECTED_EXIT = cast(
    'int', _private_attr(app_module, '_HOSTS_CLEANUP_UNEXPECTED_EXIT')
)
_cleanup_hosts_once = cast('Callable[[], int]', _private_attr(app_module, '_cleanup_hosts_once'))
_launch_roblox_uri_for_instance = cast(
    'Callable[[object, str], bool]', _private_attr(app_module, '_launch_roblox_uri_for_instance')
)
_arm_windows_gdk_env_proxy_when_ready = cast(
    '_ArmWindowsGdkProxy', _private_attr(app_module, '_arm_windows_gdk_env_proxy_when_ready')
)
_restart_handoff_path = cast(
    '_RestartHandoffPath', _private_attr(app_module, '_restart_handoff_path')
)
_join_restart_handoff = cast(
    'Callable[[str, int], bool]', _private_attr(app_module, '_join_restart_handoff')
)
_wait_for_restart_release = cast(
    'Callable[[str, int], bool]', _private_attr(app_module, '_wait_for_restart_release')
)
_run_restart_handoff_parent = cast(
    '_RunRestartHandoffParent', _private_attr(app_module, '_run_restart_handoff_parent')
)
_resume_single_instance_after_handoff_failure = cast(
    'Callable[[], bool]', _private_attr(app_module, '_resume_single_instance_after_handoff_failure')
)
_abort_restart_child_and_wait = cast(
    '_AbortRestartChild', _private_attr(app_module, '_abort_restart_child_and_wait')
)
_wait_for_restart_marker = cast(
    '_WaitForRestartMarker', _private_attr(app_module, '_wait_for_restart_marker')
)
_write_restart_handoff_marker = cast(
    'Callable[[str, str, int], bool]', _private_attr(app_module, '_write_restart_handoff_marker')
)
_strip_restart_handoff_args = cast(
    'Callable[[list[str]], list[str]]', _private_attr(app_module, '_strip_restart_handoff_args')
)
_ROBLOX_EXIT_MONITOR_FACTORY = cast('_RobloxExitMonitorFactory', app_module.RobloxExitMonitor)


def _single_instance_control_server() -> object | None:
    return cast('object | None', _private_attr(app_module, '_single_instance_control_server'))


def _monitor_studio_signal(monitor: object) -> _Signal0:
    return cast('_Signal0', _private_attr(monitor, '_studio_detected'))


def _monitor_on_studio_detected(monitor: object) -> Callable[[], None]:
    return cast('Callable[[], None]', _private_attr(monitor, '_on_studio_detected'))


def _check_roblox_status_locked(monitor: object) -> None:
    cast('Callable[[], None]', _private_attr(monitor, '_check_roblox_status_locked'))()


def _monitor_studio_was_running(monitor: object) -> bool:
    return cast('bool', _private_attr(monitor, '_studio_was_running'))


def _monitor_suppresses_next_player_exit_cache_delete(monitor: object) -> bool:
    return cast('bool', _private_attr(monitor, '_suppress_next_player_exit_cache_delete'))


def _handle_macos_uri_interception(monitor: object, launch: object, target: str) -> None:
    callback = cast(
        'Callable[[object, str], None]', _private_attr(monitor, '_handle_macos_uri_interception')
    )
    callback(launch, target)


def _int_list_callback(callback: Callable[[], list[int]]) -> Callable[[], list[int]]:
    return callback


def _object_callback[ResultT](
    callback: Callable[[object], ResultT],
) -> Callable[[object], ResultT]:
    return callback


def _path_callback[ResultT](
    callback: Callable[[Path], ResultT],
) -> Callable[[Path], ResultT]:
    return callback


def _str_callback[ResultT](
    callback: Callable[[str], ResultT],
) -> Callable[[str], ResultT]:
    return callback


def _int_callback[ResultT](
    callback: Callable[[int], ResultT],
) -> Callable[[int], ResultT]:
    return callback


def _bool_callback[ResultT](
    callback: Callable[[bool], ResultT],
) -> Callable[[bool], ResultT]:
    return callback


def _float_callback[ResultT](
    callback: Callable[[float], ResultT],
) -> Callable[[float], ResultT]:
    return callback


def _details_callback[ResultT](
    callback: Callable[[dict[str, object]], ResultT],
) -> Callable[[dict[str, object]], ResultT]:
    return callback


def _callable_callback[ResultT](
    callback: Callable[[Callable[..., object]], ResultT],
) -> Callable[[Callable[..., object]], ResultT]:
    return callback


def _two_object_callback[ResultT](
    callback: Callable[[object, object], ResultT],
) -> Callable[[object, object], ResultT]:
    return callback


def _result_path_callback[ResultT](
    callback: Callable[[object, Path], ResultT],
) -> Callable[[object, Path], ResultT]:
    return callback


def _paths_config_callback[ResultT](
    callback: Callable[[list[Path], Path], ResultT],
) -> Callable[[list[Path], Path], ResultT]:
    return callback


def _marker_callback[ResultT](
    callback: Callable[[str, str, int], ResultT],
) -> Callable[[str, str, int], ResultT]:
    return callback


def _args_callback[ResultT](callback: _ArgsCallback[ResultT]) -> _ArgsCallback[ResultT]:
    return callback


def _kwargs_callback[ResultT](
    callback: _KwargsCallback[ResultT],
) -> _KwargsCallback[ResultT]:
    return callback


def _args_kwargs_callback[ResultT](
    callback: _ArgsKwargsCallback[ResultT],
) -> _ArgsKwargsCallback[ResultT]:
    return callback


def _parent_paths_kwargs_callback[ResultT](
    callback: _ParentPathsKwargsCallback[ResultT],
) -> _ParentPathsKwargsCallback[ResultT]:
    return callback


def _enabled_config_kwargs_callback[ResultT](
    callback: _EnabledConfigKwargsCallback[ResultT],
) -> _EnabledConfigKwargsCallback[ResultT]:
    return callback


def _values_kwargs_callback[ResultT](
    callback: _ValuesKwargsCallback[ResultT],
) -> _ValuesKwargsCallback[ResultT]:
    return callback


def _cleanup_token_kwargs_callback[ResultT](
    callback: _CleanupTokenKwargsCallback[ResultT],
) -> _CleanupTokenKwargsCallback[ResultT]:
    return callback


def _handoff_kwargs_callback[ResultT](
    callback: _HandoffKwargsCallback[ResultT],
) -> _HandoffKwargsCallback[ResultT]:
    return callback


def _visible_owner_callback[ResultT](
    callback: Callable[[_VisibleOwner], ResultT],
) -> Callable[[_VisibleOwner], ResultT]:
    return callback


def test_macos_fleasion_process_matching_accepts_real_launch_forms() -> None:
    assert _looks_like_macos_fleasion_command(
        f'/Applications/Fleasion.app/Contents/MacOS/Fleasion-v{__version__} --no-dashboard'
    )
    assert _looks_like_macos_fleasion_command('/project/.venv/bin/Fleasion')
    assert _looks_like_macos_fleasion_command('/usr/bin/python3 /project/launcher.py')
    assert _looks_like_macos_fleasion_command('/usr/bin/python3 -m Fleasion')
    assert _looks_like_macos_fleasion_command(
        '/project/.venv/bin/python /project/.venv/bin/fleasion'
    )


def test_macos_fleasion_process_matching_rejects_unrelated_commands() -> None:
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c tail '/Users/test/Library/Application Support/FleasionNT/logs/fleasion.log'"
    )
    assert not _looks_like_macos_fleasion_command(
        f"/bin/zsh -c ps -axo command | rg 'Fleasion-v{__version__}|launcher.py'"
    )
    assert not _looks_like_macos_fleasion_command('/usr/bin/python3 /tmp/not-fleasion.py')


def test_fleasion_process_matching_rejects_linux_proxy_helper_commands() -> None:
    assert not _looks_like_macos_fleasion_command(
        '/opt/Fleasion/Fleasion --linux-proxy-helper --backend-port 8443'
    )
    assert not _looks_like_macos_fleasion_command(
        '/usr/bin/python3 /project/launcher.py --linux-proxy-helper --backend-port 8443'
    )
    assert not _looks_like_macos_fleasion_command(
        '/usr/bin/python3 /project/src/fleasion/linux_proxy_helper_daemon.py --backend-port 8443'
    )


def test_stale_single_instance_can_be_reclaimed_on_linux_without_gui_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_other_fleasion_pids', _int_list_callback(list))

    assert _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_stale_single_instance_not_reclaimed_on_linux_with_gui_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [1234])

    assert not _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_kill_other_instances_prefers_graceful_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(app_module, '_request_other_fleasion_instances_exit', lambda: True)
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [1234])
    monkeypatch.setattr(
        app_module.subprocess,
        'run',
        _args_kwargs_callback(lambda *args, **kwargs: calls.append((args, kwargs))),
    )

    kill_other_fleasion_instances()

    assert calls == []


def test_single_instance_quit_command_exits_tray() -> None:
    class _SocketStub:
        def readAll(self) -> bytes:
            return b'quit\n'

    class _TrayStub:
        def __init__(self) -> None:
            self.exit_calls = 0

        def _exit_app(self) -> None:
            self.exit_calls += 1

    tray = _TrayStub()

    _handle_single_instance_command(_SocketStub(), tray)

    assert tray.exit_calls == 1


def test_single_instance_preserve_command_keeps_env_player() -> None:
    class _SocketStub:
        def readAll(self) -> bytes:
            return b'quit-preserve-env-player\n'

    class _TrayStub:
        def __init__(self) -> None:
            self.exit_kwargs: list[dict[str, object]] = []

        def _exit_app(self, **kwargs: object) -> None:
            self.exit_kwargs.append(kwargs)

    tray = _TrayStub()

    _handle_single_instance_command(_SocketStub(), tray)

    assert tray.exit_kwargs == [{'preserve_roblox': True}]


def test_roblox_url_event_filter_queues_until_application_is_ready() -> None:
    received: list[str] = []
    event_filter = _RobloxUrlEventFilter()
    event_filter.roblox_uri_received.connect(received.append)

    class _Event:
        def type(self) -> QEvent.Type:
            return QEvent.Type.FileOpen

        def url(self) -> QUrl:
            return QUrl('roblox://experiences/start?placeId=1')

    assert event_filter.eventFilter(None, _Event()) is False
    assert received == []
    event_filter.start()
    assert received == ['roblox://experiences/start?placeId=1']


def test_single_instance_launch_command_preserves_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Callable[..., object], tuple[object, ...]]] = []
    monkeypatch.setattr(
        app_module,
        'run_in_thread',
        _callable_callback(
            lambda function: _args_callback(lambda *args: calls.append((function, args)))
        ),
    )

    class _SocketStub:
        def readAll(self) -> bytes:
            return b'launch-roblox\nroblox://experiences/start?placeId=1\n'

    class _TrayStub:
        config_manager = type(
            'Config', (), {'proxy_mode': 'hosts', 'proxy_features_enabled': False}
        )()
        proxy_master = None

    _handle_single_instance_command(_SocketStub(), _TrayStub())

    assert len(calls) == 1
    assert calls[0][1][1] == 'roblox://experiences/start?placeId=1'


def test_autostart_resync_includes_linux_normal_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)

    assert _should_sync_autostart_on_launch(True)
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    assert _should_sync_autostart_on_launch(True)
    assert not _should_sync_autostart_on_launch(False)


def test_autostart_resync_runs_without_admin_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)

    assert _should_sync_autostart_on_launch(True)


def test_env_proxy_migration_forces_legacy_users_before_acknowledgement() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=False,
    )

    assert _prepare_env_proxy_migration(config) is True
    assert config.proxy_mode == 'env'
    assert config.env_proxy_migration_v1_complete is False


def test_env_proxy_migration_uses_first_time_guide_for_new_users() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=False,
        env_proxy_migration_v1_complete=False,
    )

    assert _prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'env'
    assert config.env_proxy_migration_v1_complete is False


def test_completed_env_proxy_migration_preserves_hosts_choice() -> None:
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=True,
    )

    assert _prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'hosts'


def test_linux_env_proxy_migration_adopts_running_sober_without_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[object] = []
    config = SimpleNamespace(
        proxy_features_enabled=True,
        env_proxy_migration_v1_complete=False,
    )
    lifecycle = SimpleNamespace(
        handle_adopted_player_launch=_path_callback(
            lambda path: events.append(('adopt', Path(path)))
        )
    )
    monitor = SimpleNamespace(
        is_player_running=lambda: True,
        env_lifecycle=lifecycle,
        was_running=False,
        _player_was_running=False,
    )

    class _MessageBox:
        class Icon:
            Information = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        class StandardButton:
            Ok = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, _value: object) -> None:
            pass

        def setIcon(self, _value: object) -> None:
            pass

        def setText(self, _value: object) -> None:
            pass

        def setInformativeText(self, _value: object) -> None:
            pass

        def setWindowIcon(self, _value: object) -> None:
            pass

        def setStandardButtons(self, _value: object) -> None:
            pass

        def addButton(self, label: str, _role: object) -> object:
            button = object()
            if label == 'Apply for Future Launches':
                self._clicked = button
            return button

        def setDefaultButton(self, _value: object) -> None:
            pass

        def setEscapeButton(self, _value: object) -> None:
            pass

        def exec(self) -> None:
            events.append(('ack-state', config.env_proxy_migration_v1_complete))

        def clickedButton(self) -> object | None:
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, 'run_in_thread', _callable_callback(lambda function: function))

    _show_env_proxy_migration(config, monitor)

    assert events == [
        ('ack-state', False),
        ('adopt', Path('org.vinegarhq.Sober')),
    ]
    assert config.env_proxy_migration_v1_complete is True
    assert monitor.was_running is True
    assert cast('bool', _private_attr(monitor, '_player_was_running')) is True


def test_env_proxy_migration_does_not_relaunch_when_proxy_features_are_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = SimpleNamespace(
        proxy_features_enabled=False,
        env_proxy_migration_v1_complete=False,
    )
    lifecycle = SimpleNamespace(
        handle_player_launch=_path_callback(
            lambda _path: (_ for _ in ()).throw(
                AssertionError('disabled proxy features must not relaunch Roblox')
            )
        )
    )
    monitor = SimpleNamespace(
        is_player_running=lambda: True,
        env_lifecycle=lifecycle,
        was_running=False,
        _player_was_running=False,
    )

    class _MessageBox:
        class Icon:
            Information = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        class StandardButton:
            Ok = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, _value: object) -> None:
            pass

        def setIcon(self, _value: object) -> None:
            pass

        def setText(self, _value: object) -> None:
            pass

        def setInformativeText(self, _value: object) -> None:
            pass

        def setWindowIcon(self, _value: object) -> None:
            pass

        def setStandardButtons(self, _value: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)

    _show_env_proxy_migration(config, monitor)

    assert config.env_proxy_migration_v1_complete is True
    assert monitor.was_running is True
    assert cast('bool', _private_attr(monitor, '_player_was_running')) is True


def test_run_on_boot_failure_can_launch_one_time_admin_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected: list[str] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')

    class _MessageBox:
        class Icon:
            Warning = object()
            Information = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        class StandardButton:
            Ok = object()

        def __init__(self, _parent: object) -> None:
            self._buttons: list[tuple[str, object]] = []

        def setWindowTitle(self, _title: object) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, text: str) -> None:
            selected.append(text)

        def setWindowIcon(self, _icon: object) -> None:
            pass

        def setStandardButtons(self, _buttons: object) -> None:
            pass

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            self._buttons.append((text, button))
            if text == 'Repair Now (Recommended)':
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

    relaunches: list[dict[str, object]] = []
    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(lambda **kwargs: relaunches.append(kwargs) or True),
    )

    _show_run_on_boot_failure(None)

    assert 'confirms whether it worked' in selected[0]
    assert 'It worked' in selected[1]
    assert 'do not need to run the repair again' in selected[1]
    assert relaunches == [
        {
            'extra_args': '--repair-autostart',
            'parent_hwnd': None,
            'wait_for_completion': True,
        }
    ]


def test_nonwindows_run_on_boot_failure_never_offers_admin_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []

    class _MessageBox:
        class Icon:
            Warning = object()

        class StandardButton:
            Ok = object()

        def __init__(self, _parent: object) -> None:
            pass

        def setWindowTitle(self, _title: object) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, text: str) -> None:
            calls.append(('text', text))

        def setWindowIcon(self, _icon: object) -> None:
            pass

        def setStandardButtons(self, button: object) -> None:
            calls.append(('buttons', button))

        def exec(self) -> None:
            pass

    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(lambda **_kwargs: calls.append(('relaunch', True))),
    )

    _show_run_on_boot_failure(None)

    assert any(
        'Check the application log' in cast('str', value) for kind, value in calls if kind == 'text'
    )
    assert not any(kind == 'relaunch' for kind, _value in calls)


def test_nonwindows_permission_failure_does_not_offer_windows_acl(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        app_module,
        'QMessageBox',
        _args_callback(
            lambda *_args: (_ for _ in ()).throw(AssertionError('Windows dialog opened'))
        ),
    )

    _show_roblox_permission_failure(None, [tmp_path])


def test_windows_ca_permission_failure_extracts_install_for_acl_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    ca_file = install / 'ssl' / 'cacert.pem'
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')

    denied = _windows_ca_permission_denied_dirs(
        {
            'failed': [
                {
                    'resource_dir': str(install),
                    'ca_file': str(ca_file),
                    'error': "[Errno 13] Permission denied: 'cacert.pem'",
                },
                {
                    'resource_dir': str(tmp_path / 'unhealthy'),
                    'error': 'cacert.pem was not launch-healthy after direct patch',
                },
            ]
        }
    )

    assert denied == [install]


def test_windows_ca_permission_failure_offers_acl_and_retries_proxy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    offered: list[tuple[object, list[Path], dict[str, object]]] = []
    retries: list[bool | None] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: 'parent')
    monkeypatch.setattr(
        app_module,
        '_show_roblox_permission_failure',
        _parent_paths_kwargs_callback(
            lambda parent, paths, **kwargs: offered.append((parent, paths, kwargs))
        ),
    )

    invoker = _ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(True))
    invoker.handle_proxy_error(
        'roblox_ca_patch_failed',
        {'failed': [{'resource_dir': str(install), 'error': '[WinError 5] Access is denied'}]},
    )

    assert offered[0][0] == 'parent'
    assert offered[0][1] == [install]
    assert 'cacert.pem for Env Proxy' in cast('str', offered[0][2]['failure_text'])
    cast('Callable[[], None]', offered[0][2]['on_repaired'])()
    assert retries == [True]


def test_windows_ca_nonpermission_failure_keeps_diagnostic_dialog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    shown: list[dict[str, object]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(
        app_module,
        '_show_roblox_ca_patch_failed_dialog',
        _details_callback(lambda details: shown.append(details)),
    )
    monkeypatch.setattr(
        app_module,
        '_show_roblox_permission_failure',
        _args_kwargs_callback(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('ACL repair offered'))
        ),
    )
    details = {
        'failed': [
            {
                'resource_dir': str(install),
                'error': 'cacert.pem was not launch-healthy after direct patch',
            }
        ]
    }

    _ProxyErrorInvoker().handle_proxy_error('roblox_ca_patch_failed', details)

    assert shown == [details]


def test_hosts_capacity_error_does_not_retry_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    shown: list[dict[str, object]] = []
    retries: list[bool | None] = []
    monkeypatch.setattr(
        app_module,
        '_show_hosts_capacity_dialog',
        _details_callback(lambda details: shown.append(details)),
    )

    invoker = _ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(True))
    invoker.handle_proxy_error(
        'hosts_entries_would_exceed_limit',
        {'hosts_size_bytes': 600_000},
    )

    assert shown == [{'hosts_size_bytes': 600_000}]
    assert retries == []


def test_successful_permission_repair_runs_proxy_retry_callback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils import windows_permissions

    callbacks: list[bool] = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        windows_permissions,
        'read_repair_result',
        _path_callback(lambda _path: {'ok': True, 'granted': ['version-test']}),
    )
    monkeypatch.setattr(
        windows_permissions, 'clear_pending_repair', _path_callback(lambda _path: None)
    )
    monkeypatch.setattr(
        windows_permissions, 'clear_repair_result', _path_callback(lambda _path: None)
    )

    _poll_roblox_permission_repair(
        None,
        deadline=10.0,
        on_repaired=lambda: callbacks.append(True),
    )

    assert callbacks == [True]


def test_permission_repair_poll_times_out_and_cleans_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from fleasion.utils import windows_permissions

    cleared: list[object] = []
    warnings: list[object] = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.time, 'monotonic', lambda: 20.0)
    monkeypatch.setattr(
        windows_permissions, 'read_repair_result', _path_callback(lambda _path: None)
    )
    monkeypatch.setattr(
        windows_permissions,
        'clear_pending_repair',
        _path_callback(lambda path: cleared.append(('pending', path))),
    )
    monkeypatch.setattr(
        windows_permissions,
        'clear_repair_result',
        _path_callback(lambda path: cleared.append(('result', path))),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        'warning',
        _args_callback(lambda *args: warnings.append(args)),
    )

    _poll_roblox_permission_repair(object(), deadline=10.0)

    assert cleared == [('pending', tmp_path), ('result', tmp_path)]
    assert len(warnings) == 1


def test_repair_autostart_once_syncs_only_from_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []

    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(
        app_module,
        'ConfigManager',
        lambda: SimpleNamespace(proxy_mode='env'),
    )
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(_args_callback(lambda *args: None))})(),
    )
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    from fleasion.utils import autostart, windows_permissions

    monkeypatch.setattr(
        windows_permissions,
        'windows_user_id_from_sid',
        _str_callback(lambda sid: 'TestDomain\\OriginalUser' if sid == 'S-1-5-21-1234' else ''),
    )

    monkeypatch.setattr(
        autostart,
        'sync_autostart',
        _enabled_config_kwargs_callback(
            lambda enabled, config_dir, **kwargs: (
                calls.append((enabled, config_dir, kwargs)) or True
            )
        ),
    )

    assert _repair_autostart_once('S-1-5-21-1234') == 0
    assert calls == [
        (
            True,
            tmp_path,
            {
                'windows_user_id': 'TestDomain\\OriginalUser',
                'proxy_mode': 'env',
            },
        )
    ]


def test_repair_autostart_once_can_remove_legacy_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[object] = []

    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(
        app_module,
        'ConfigManager',
        lambda: SimpleNamespace(proxy_mode='env'),
    )
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(_args_callback(lambda *args: None))})(),
    )
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    from fleasion.utils import autostart

    monkeypatch.setattr(
        autostart,
        'sync_autostart',
        _enabled_config_kwargs_callback(
            lambda enabled, config_dir, **kwargs: (
                calls.append((enabled, config_dir, kwargs)) or True
            )
        ),
    )

    assert _repair_autostart_once(enabled=False) == 0
    assert calls == [
        (
            False,
            tmp_path,
            {'windows_user_id': None, 'proxy_mode': 'env'},
        )
    ]


def test_windows_elevation_carries_original_desktop_sid(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.utils import windows_permissions

    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(
        windows_permissions,
        'current_windows_user_identity',
        lambda: ('S-1-5-21-1234', r'DesktopDomain\OriginalUser'),
    )
    args = ['--repair-autostart']

    assert _append_windows_requesting_user_args(args)

    assert args == [
        '--repair-autostart',
        '--fleasion-requesting-user-sid=S-1-5-21-1234',
    ]


def test_roblox_permission_prompt_requests_targeted_elevation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    selected: list[str] = []
    relaunches: list[dict[str, object]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, _title: object) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, text: str) -> None:
            selected.append(text)

        def setWindowIcon(self, _icon: object) -> None:
            pass

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            if text.startswith('Grant access'):
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(lambda **kwargs: relaunches.append(kwargs) or True),
    )

    from fleasion.utils import windows_permissions

    pending: list[object] = []
    monkeypatch.setattr(
        windows_permissions,
        'write_pending_repair',
        _paths_config_callback(lambda paths, config_dir: pending.extend(paths) or True),
    )
    monkeypatch.setattr(
        windows_permissions, 'clear_repair_result', _path_callback(lambda _path: None)
    )

    _show_roblox_permission_failure(None, [tmp_path / 'Roblox' / 'version-old'])

    assert 'current Windows account' in selected[0]
    assert pending == [tmp_path / 'Roblox' / 'version-old']
    assert relaunches == [{'extra_args': '--repair-roblox-permissions', 'parent_hwnd': None}]


def test_repair_roblox_permissions_once_writes_result_and_clears_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(_args_callback(lambda *args: None))})(),
    )

    from fleasion.utils import windows_permissions

    paths = [tmp_path / 'Roblox' / 'version-old']
    monkeypatch.setattr(
        windows_permissions, 'read_pending_repair', _path_callback(lambda _path: paths)
    )
    monkeypatch.setattr(
        windows_permissions,
        'grant_current_user_modify_access',
        _values_kwargs_callback(
            lambda values, **kwargs: {
                'ok': kwargs.get('user_sid') == 'S-1-5-21-1234',
                'granted': [str(values[0])],
                'failed': [],
            }
        ),
    )
    results: list[object] = []
    monkeypatch.setattr(
        windows_permissions,
        'write_repair_result',
        _result_path_callback(lambda result, _path: results.append(result)),
    )
    cleared: list[object] = []
    monkeypatch.setattr(
        windows_permissions,
        'clear_pending_repair',
        _path_callback(lambda path: cleared.append(path)),
    )

    assert _repair_roblox_permissions_once('S-1-5-21-1234') == 0
    assert results == [{'ok': True, 'granted': [str(paths[0])], 'failed': []}]
    assert cleared == [tmp_path]


def test_repair_windows_firewall_once_writes_result_and_clears_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(_args_callback(lambda *args: None))})(),
    )

    from fleasion.utils import windows_firewall

    monkeypatch.setattr(windows_firewall, 'read_pending_repair', _path_callback(lambda _path: True))
    monkeypatch.setattr(
        windows_firewall,
        'install_fleasion_firewall_rules',
        lambda: {'ok': True, 'rules': ['in', 'out'], 'failed': []},
    )
    results: list[object] = []
    monkeypatch.setattr(
        windows_firewall,
        'write_repair_result',
        _result_path_callback(lambda result, _path: results.append(result)),
    )
    cleared: list[object] = []
    monkeypatch.setattr(
        windows_firewall,
        'clear_pending_repair',
        _path_callback(lambda path: cleared.append(path)),
    )

    assert _repair_windows_firewall_once() == 0
    assert results == [{'ok': True, 'rules': ['in', 'out'], 'failed': []}]
    assert cleared == [tmp_path]


def test_privileged_hosts_cleanup_uses_pkexec_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(
        'fleasion.utils.linux_proxy_helper.cleanup_hosts_with_pkexec',
        lambda: calls.append(True) or True,
    )

    assert _run_privileged_hosts_cleanup() is True
    assert calls == [True]


def test_privileged_hosts_cleanup_waits_for_windows_elevated_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    parent = object()
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(
        app_module,
        '_window_handle',
        _object_callback(lambda widget: 123 if widget is parent else None),
    )

    def _relaunch(**kwargs: object) -> bool:
        completion = cast('_RelaunchCompletionAdapter', kwargs.pop('completion'))
        assert completion == {}
        completion.update({'wait_result': 0, 'exit_code_read': True, 'exit_code': 0})
        calls.append(kwargs)
        return True

    monkeypatch.setattr(app_module, '_relaunch_as_admin', _relaunch)

    assert _run_privileged_hosts_cleanup(parent) is True
    assert calls == [
        {
            'extra_args': '--cleanup-hosts',
            'parent_hwnd': 123,
            'wait_for_completion': True,
            'wait_timeout_ms': 15 * 60 * 1000,
        }
    ]


def test_privileged_hosts_cleanup_logs_known_elevated_child_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[object, ...]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: None))

    def _relaunch(**kwargs: object) -> bool:
        completion = cast('_RelaunchCompletionAdapter', kwargs['completion'])
        completion['exit_code'] = _HOSTS_CLEANUP_NOT_ADMIN_EXIT
        return False

    monkeypatch.setattr(app_module, '_relaunch_as_admin', _relaunch)
    monkeypatch.setattr(
        app_module.log_buffer, 'log', _args_callback(lambda *args: logs.append(args))
    )

    assert _run_privileged_hosts_cleanup() is False
    assert any('did not receive an administrator token' in cast('str', entry[1]) for entry in logs)


def test_privileged_hosts_cleanup_classifies_still_active_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logs: list[tuple[object, ...]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: None))

    def _relaunch(**kwargs: object) -> bool:
        completion = cast('_RelaunchCompletionAdapter', kwargs['completion'])
        completion.update({'wait_result': 258, 'exit_code_read': False, 'exit_code': None})
        return False

    monkeypatch.setattr(app_module, '_relaunch_as_admin', _relaunch)
    monkeypatch.setattr(
        app_module.log_buffer, 'log', _args_callback(lambda *args: logs.append(args))
    )

    assert _run_privileged_hosts_cleanup() is False
    assert any('still running' in cast('str', entry[1]) for entry in logs)
    assert not any('exit=259' in cast('str', entry[1]) for entry in logs)


def test_elevated_hosts_cleanup_reports_write_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.proxy import master as proxy_master

    monkeypatch.setattr(app_module, '_is_admin', lambda: True)

    def _remove(_hosts: object, *, error_details: dict[str, object]) -> bool:
        error_details['error'] = 'access denied by host protection'
        return False

    monkeypatch.setattr(proxy_master, '_remove_hosts_entries', _remove)

    assert _cleanup_hosts_once() == _HOSTS_CLEANUP_WRITE_FAILED_EXIT


def test_elevated_hosts_cleanup_reports_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.proxy import master as proxy_master

    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(
        proxy_master,
        '_remove_hosts_entries',
        _args_kwargs_callback(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('boom'))
        ),
    )

    assert _cleanup_hosts_once() == _HOSTS_CLEANUP_UNEXPECTED_EXIT


def test_stale_env_hosts_dialog_runs_privileged_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.proxy import master as proxy_master

    stale = iter([True, False])
    calls: list[tuple[_VisibleOwner, bool]] = []
    monkeypatch.setattr(
        proxy_master, 'has_stale_hosts_entries', _object_callback(lambda _hosts: next(stale))
    )
    monkeypatch.setattr(proxy_master, 'INTERCEPT_HOSTS', frozenset({'assetdelivery.roblox.com'}))
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_run_privileged_hosts_cleanup',
        _visible_owner_callback(lambda owner: calls.append((owner, owner.visible)) or True),
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, _title: object) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, _text: object) -> None:
            pass

        def setInformativeText(self, _text: object) -> None:
            pass

        def addButton(self, text: str, _role: object) -> object:
            button = type(
                'Button', (), {'setEnabled': _two_object_callback(lambda self, _enabled: None)}
            )()
            if text.startswith('Fix Hosts File'):
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

        def show(self) -> None:
            self.visible = True

        def raise_(self) -> None:
            pass

        def activateWindow(self) -> None:
            pass

        def hide(self) -> None:
            self.visible = False

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module.QApplication, 'processEvents', lambda: None)

    _show_env_proxy_stale_hosts_dialog()

    assert len(calls) == 1
    owner, was_visible = calls[0]
    assert isinstance(owner, _MessageBox)
    assert was_visible is True
    assert owner.visible is False


def test_windows_upstream_dialog_requests_targeted_firewall_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[object, object]] = []
    relaunches: list[dict[str, object]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: None))
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(lambda **kwargs: relaunches.append(kwargs) or True),
    )
    monkeypatch.setattr(app_module.QTimer, 'singleShot', _args_callback(lambda *_args: None))
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type(
            'Log',
            (),
            {
                'log': staticmethod(
                    _args_callback(lambda *args: calls.append(cast('tuple[object, object]', args)))
                )
            },
        )(),
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            ActionRole = object()
            RejectRole = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, _title: object) -> None:
            pass

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, text: str) -> None:
            calls.append(('text', text))

        def setInformativeText(self, text: str) -> None:
            calls.append(('informative', text))

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            if text == 'Allow Fleasion Through Firewall':
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

        def parentWidget(self) -> None:
            return None

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)

    from fleasion.utils import windows_firewall

    monkeypatch.setattr(
        windows_firewall,
        'get_fleasion_firewall_rule_status',
        lambda: {'ok': False, 'missing': ['out']},
    )
    pending: list[object] = []
    monkeypatch.setattr(
        windows_firewall,
        'write_pending_repair',
        _path_callback(lambda config_dir: pending.append(config_dir)),
    )
    monkeypatch.setattr(windows_firewall, 'clear_repair_result', _path_callback(lambda _path: None))
    monkeypatch.setattr(
        windows_firewall, 'clear_pending_repair', _path_callback(lambda _path: None)
    )

    _show_windows_upstream_firewall_dialog(
        {'host': 'assetdelivery.roblox.com', 'proxy_mode': 'env'}
    )

    informative = next(cast('str', value) for kind, value in calls if kind == 'informative')
    assert 'Private and Public networks' in informative
    assert 'only Fleasion program rules' in informative
    assert pending == [tmp_path]
    assert relaunches == [{'extra_args': '--repair-firewall', 'parent_hwnd': None}]


def test_windows_upstream_dialog_escalates_when_firewall_rules_already_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import webbrowser

    opened: list[str] = []
    calls: list[tuple[object, object]] = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(
        webbrowser,
        'open',
        _str_callback(lambda url: opened.append(url)),
    )
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type(
            'Log',
            (),
            {
                'log': staticmethod(
                    _args_callback(lambda *args: calls.append(cast('tuple[object, object]', args)))
                )
            },
        )(),
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            ActionRole = object()
            RejectRole = object()

        def __init__(self, _parent: object) -> None:
            self._clicked = None

        def setWindowTitle(self, title: str) -> None:
            calls.append(('title', title))

        def setIcon(self, _icon: object) -> None:
            pass

        def setText(self, text: str) -> None:
            calls.append(('text', text))

        def setInformativeText(self, text: str) -> None:
            calls.append(('informative', text))

        def addButton(self, text: str, _role: object) -> object:
            button = object()
            if text == 'Get Help on Discord':
                self._clicked = button
            return button

        def setDefaultButton(self, _button: object) -> None:
            pass

        def exec(self) -> None:
            pass

        def clickedButton(self) -> object | None:
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)

    from fleasion.utils import windows_firewall

    monkeypatch.setattr(
        windows_firewall,
        'get_fleasion_firewall_rule_status',
        lambda: {'ok': True, 'rules': ['in', 'out'], 'missing': []},
    )
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(
            lambda **_kwargs: (_ for _ in ()).throw(AssertionError('repair should not repeat'))
        ),
    )

    _show_windows_upstream_firewall_dialog({'host': 'assetdelivery.roblox.com'})

    informative = next(cast('str', value) for kind, value in calls if kind == 'informative')
    assert 'already installed' in informative
    assert 'Fleasion Discord' in informative
    assert opened == ['https://discord.gg/hXyhKehEZF']


def test_env_proxy_studio_launch_is_completely_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    monitor = _ROBLOX_EXIT_MONITOR_FACTORY(config)
    _monitor_studio_signal(monitor).disconnect(_monitor_on_studio_detected(monitor))
    notifications: list[bool] = []
    _monitor_studio_signal(monitor).connect(lambda: notifications.append(True))

    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: True)
    monkeypatch.setattr(
        app_module,
        'get_roblox_studio_exe_path',
        lambda: (_ for _ in ()).throw(AssertionError('Env mode must not inspect Studio')),
    )

    _check_roblox_status_locked(monitor)

    assert notifications == []
    assert _monitor_studio_was_running(monitor) is True
    assert qt_app is not None


def test_windows_desktop_player_launch_uses_env_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls: list[object] = []

    class _Lifecycle:
        owns_player = False

        def handle_player_launch(self, exe_path: str | Path) -> bool:
            lifecycle_calls.append(Path(exe_path))
            return True

    proxy_master = SimpleNamespace(set_roblox_player_running=_bool_callback(lambda _running: None))
    monitor = _ROBLOX_EXIT_MONITOR_FACTORY(
        config,
        proxy_master=proxy_master,
        env_lifecycle=_Lifecycle(),
    )
    player_exe = tmp_path / 'Roblox' / 'RobloxPlayerBeta.exe'
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: False)
    monkeypatch.setattr(app_module, 'get_roblox_player_exe_path', lambda: player_exe)
    monkeypatch.setattr(app_module, 'run_in_thread', _callable_callback(lambda function: function))
    monkeypatch.setitem(
        app_module.sys.modules,
        'fleasion.utils.platform_windows',
        SimpleNamespace(
            is_roblox_gdk_env_proxy_armed=lambda: False,
            is_gdk_env_proxy_activation_in_progress=lambda: False,
            is_env_proxy_relaunched_player_running=lambda: False,
            is_roblox_gdk_exe_path=_path_callback(lambda _path: False),
        ),
    )

    _check_roblox_status_locked(monitor)

    assert lifecycle_calls == [player_exe]
    assert _monitor_suppresses_next_player_exit_cache_delete(monitor) is True
    assert qt_app is not None


def test_linux_browser_sober_launch_is_always_adopted_without_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls: list[object] = []

    class _Lifecycle:
        owns_player = False

        def handle_player_launch(self, _exe_path: object) -> bool:
            lifecycle_calls.append('relaunch')
            return True

        def handle_adopted_player_launch(self, exe_path: str | Path) -> bool:
            lifecycle_calls.append(('adopt', Path(exe_path)))
            return True

    proxy_master = SimpleNamespace(
        set_roblox_player_running=_bool_callback(lambda _running: None),
        _sober_env_proxy_override_active=False,
    )
    monitor = _ROBLOX_EXIT_MONITOR_FACTORY(
        config,
        proxy_master=proxy_master,
        env_lifecycle=_Lifecycle(),
    )
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: False)
    monkeypatch.setattr(app_module, 'run_in_thread', _callable_callback(lambda function: function))

    _check_roblox_status_locked(monitor)

    assert lifecycle_calls == [('adopt', Path('org.vinegarhq.Sober'))]
    assert qt_app is not None


def test_linux_instance_uri_uses_sober_without_env_proxy_relaunch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launches: list[object] = []
    tray = SimpleNamespace(
        config_manager=SimpleNamespace(proxy_mode='env', proxy_features_enabled=True),
        proxy_master=SimpleNamespace(),
    )
    target = 'roblox://experiences/start?placeId=1'

    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(
        app_module,
        'launch_as_standard_user',
        _str_callback(lambda uri: launches.append(uri) or True),
    )

    assert _launch_roblox_uri_for_instance(tray, target)
    assert launches == [target]


def test_windows_gdk_arming_waits_for_final_proxy_port(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []

    def cleanup() -> None:
        pass

    class _Proxy:
        def wait_for_env_proxy_ready(self, timeout: float) -> bool:
            events.append(('ready', timeout))
            return True

        def roblox_env_proxy_url(self) -> str:
            events.append(('url',))
            return 'http://127.0.0.1:49152'

    monkeypatch.setitem(
        app_module.sys.modules,
        'fleasion.utils.platform_windows',
        SimpleNamespace(
            arm_roblox_gdk_env_proxy=_str_callback(lambda url: events.append(('arm', url)) or True),
            disarm_roblox_gdk_env_proxy=cleanup,
        ),
    )
    monkeypatch.setattr(
        app_module.atexit,
        'register',
        _object_callback(lambda callback: events.append(('cleanup', callback))),
    )

    assert _arm_windows_gdk_env_proxy_when_ready(_Proxy(), timeout=4.0)
    assert events == [
        ('ready', 4.0),
        ('url',),
        ('arm', 'http://127.0.0.1:49152'),
        ('cleanup', cleanup),
    ]


def test_windows_gdk_arming_stops_when_proxy_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    proxy = SimpleNamespace(
        wait_for_env_proxy_ready=_float_callback(lambda timeout: False),
        roblox_env_proxy_url=lambda: (_ for _ in ()).throw(
            AssertionError('an unready proxy has no final URL')
        ),
    )

    assert not _arm_windows_gdk_env_proxy_when_ready(proxy)


def test_restart_handoff_token_cannot_select_an_arbitrary_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    assert _restart_handoff_path('../outside') is None
    assert _restart_handoff_path('A' * 32) is None
    assert _restart_handoff_path('0' * 32, '../ready') is None
    assert _restart_handoff_path('0' * 32, 'prepared') == (
        tmp_path / f'.restart-prepared-{"0" * 32}'
    )
    assert _restart_handoff_path('0' * 32, 'release') == (tmp_path / f'.restart-release-{"0" * 32}')
    assert _restart_handoff_path('0' * 32, 'abort') == (tmp_path / f'.restart-abort-{"0" * 32}')
    assert _restart_handoff_path('0' * 32) == (tmp_path / f'.restart-ready-{"0" * 32}')


def test_restart_child_joins_only_after_parent_release(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = '9' * 32
    parent_pid = 2121
    child_pid = 3131
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.os, 'getpid', lambda: child_pid)
    (tmp_path / f'.restart-release-{token}').write_text(str(parent_pid), encoding='utf-8')

    assert _join_restart_handoff(token, parent_pid)
    assert (tmp_path / f'.restart-prepared-{token}').read_text(encoding='utf-8') == str(child_pid)
    assert not (tmp_path / f'.restart-release-{token}').exists()


def test_restart_child_consumes_abort_before_ownership_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = '5' * 32
    parent_pid = 4141
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    (tmp_path / f'.restart-abort-{token}').write_text(str(parent_pid), encoding='utf-8')

    assert not _wait_for_restart_release(token, parent_pid)
    assert not (tmp_path / f'.restart-abort-{token}').exists()


def test_restart_handoff_parent_reclaims_only_after_application_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = 'a' * 32
    launcher_pid = 4242
    application_pid = 4243
    events: list[object] = []
    state = {'attached': True}

    def wait_marker(
        _token: str,
        phase: str,
        *,
        is_launcher_alive: Callable[[], bool],
        expected_value: int | None = None,
        timeout: float = 0,
    ) -> int | None:
        assert _token == token
        assert is_launcher_alive()
        events.append(f'wait-{phase}')
        if phase == 'prepared':
            assert expected_value is None
            return application_pid
        assert expected_value == application_pid
        return None

    def suspend() -> bool:
        events.append('suspend')
        state['attached'] = False
        return True

    def resume() -> bool:
        events.append('resume')
        state['attached'] = True
        return True

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(
        app_module, '_pid_is_alive', _int_callback(lambda pid: pid == application_pid)
    )
    monkeypatch.setattr(app_module, '_suspend_single_instance_for_handoff', suspend)
    monkeypatch.setattr(
        app_module,
        '_write_restart_handoff_marker',
        _marker_callback(lambda _token, phase, value: events.append((phase, value)) or True),
    )
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        _args_kwargs_callback(lambda *_args, **_kwargs: events.append('abort-confirmed') or True),
    )
    monkeypatch.setattr(app_module, '_resume_single_instance_after_handoff_failure', resume)
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        _str_callback(lambda _token: events.append('cleanup')),
    )

    assert not _run_restart_handoff_parent(
        token,
        launcher_pid,
        is_launcher_alive=lambda: True,
        terminate_launcher=lambda: events.append('terminate-launcher'),
    )
    assert events == [
        'wait-prepared',
        'suspend',
        ('release', app_module.os.getpid()),
        'wait-ready',
        'abort-confirmed',
        'resume',
        'cleanup',
    ]


def test_restart_handoff_reclaim_failure_is_uncertain_even_after_application_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = '4' * 32
    application_pid = 4546
    events: list[object] = []

    def wait_marker(
        _token: str,
        phase: str,
        *,
        is_launcher_alive: Callable[[], bool],
        expected_value: int | None = None,
        timeout: float = 0,
    ) -> int | None:
        assert _token == token
        assert is_launcher_alive()
        if phase == 'prepared':
            return application_pid
        assert expected_value == application_pid
        return None

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(
        app_module, '_pid_is_alive', _int_callback(lambda pid: pid == application_pid)
    )
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(
        app_module, '_write_restart_handoff_marker', _args_callback(lambda *_args: True)
    )
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        _args_kwargs_callback(lambda *_args, **_kwargs: events.append('abort-confirmed') or True),
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume-failed') or False,
    )
    monkeypatch.setattr(app_module, '_cleanup_restart_handoff', _str_callback(lambda _token: None))

    with pytest.raises(
        app_module.RestartHandoffUncertain,
        match='could not restore single-instance ownership',
    ):
        _run_restart_handoff_parent(
            token,
            4545,
            is_launcher_alive=lambda: True,
            terminate_launcher=lambda: events.append('terminate-launcher'),
        )

    assert events == ['suspend', 'abort-confirmed', 'resume-failed']


def test_resume_single_instance_fails_when_control_server_cannot_be_restored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_memory = SimpleNamespace(isAttached=lambda: True)
    monkeypatch.setattr(app_module, '_single_instance_shared_memory', shared_memory)
    monkeypatch.setattr(app_module, '_single_instance_app', object())
    monkeypatch.setattr(app_module, '_single_instance_tray', object())
    monkeypatch.setattr(app_module, '_single_instance_control_server', None)
    monkeypatch.setattr(
        app_module, '_start_single_instance_control_server', _args_callback(lambda *_args: None)
    )

    assert not _resume_single_instance_after_handoff_failure()
    assert _single_instance_control_server() is None


def test_restart_handoff_does_not_reclaim_if_application_exit_is_unconfirmed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = '7' * 32
    application_pid = 5152
    events: list[object] = []

    def wait_marker(
        _token: str,
        phase: str,
        *,
        is_launcher_alive: Callable[[], bool],
        expected_value: int | None = None,
        timeout: float = 0,
    ) -> int | None:
        assert _token == token
        assert is_launcher_alive()
        if phase == 'prepared':
            return application_pid
        assert expected_value == application_pid
        return None

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(
        app_module, '_pid_is_alive', _int_callback(lambda pid: pid == application_pid)
    )
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(
        app_module, '_write_restart_handoff_marker', _args_callback(lambda *_args: True)
    )
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        _args_kwargs_callback(
            lambda *_args, **_kwargs: events.append('abort-unconfirmed') or False
        ),
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        _cleanup_token_kwargs_callback(lambda _token, **_kwargs: None),
    )

    with pytest.raises(app_module.RestartHandoffUncertain):
        _run_restart_handoff_parent(
            token,
            5151,
            is_launcher_alive=lambda: True,
            terminate_launcher=lambda: events.append('terminate-launcher'),
        )
    assert events == ['suspend', 'abort-unconfirmed']


def test_abort_does_not_treat_dead_onefile_launcher_as_dead_application(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = '6' * 32
    parent_pid = 6161
    application_pid = 6163
    state = {'launcher_alive': True}
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module, '_pid_is_alive', _int_callback(lambda pid: pid == application_pid)
    )

    assert not _abort_restart_child_and_wait(
        token,
        parent_pid,
        application_pid,
        is_launcher_alive=lambda: state['launcher_alive'],
        terminate_launcher=lambda: state.__setitem__('launcher_alive', False),
        timeout=0.02,
    )
    assert not state['launcher_alive']
    assert (tmp_path / f'.restart-abort-{token}').read_text(encoding='utf-8') == str(parent_pid)


def test_restart_handoff_success_accepts_onefile_launcher_and_application_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = '8' * 32
    launcher_pid = 4343
    application_pid = 4344
    events: list[object] = []

    def wait_marker(
        _token: str,
        phase: str,
        *,
        is_launcher_alive: Callable[[], bool],
        expected_value: int | None = None,
        timeout: float = 0,
    ) -> int | None:
        assert _token == token
        assert is_launcher_alive()
        events.append(f'wait-{phase}')
        if phase == 'prepared':
            assert expected_value is None
            return application_pid
        assert expected_value == application_pid
        return application_pid

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(
        app_module, '_pid_is_alive', _int_callback(lambda pid: pid == application_pid)
    )
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_write_restart_handoff_marker',
        _marker_callback(lambda _token, phase, value: events.append((phase, value)) or True),
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        _str_callback(lambda _token: events.append('cleanup')),
    )

    assert _run_restart_handoff_parent(
        token,
        launcher_pid,
        is_launcher_alive=lambda: True,
        terminate_launcher=lambda: events.append('terminate-launcher'),
    )
    assert events == [
        'wait-prepared',
        'suspend',
        ('release', app_module.os.getpid()),
        'wait-ready',
        'cleanup',
    ]


def test_restart_marker_rejects_launcher_that_dies_after_writing_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = 'b' * 32
    application_pid = 5252
    alive_checks = iter([True, False])
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    (tmp_path / f'.restart-ready-{token}').write_text(str(application_pid), encoding='utf-8')

    assert (
        _wait_for_restart_marker(
            token,
            'ready',
            is_launcher_alive=lambda: next(alive_checks),
            expected_value=application_pid,
            timeout=0.1,
        )
        is None
    )


def test_verified_restart_uses_protocol_args_without_kill_others(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = 'c' * 32
    launches: list[tuple[list[str], dict[str, object]]] = []
    handoffs: list[tuple[str, int]] = []

    class _Process:
        pid = 6262

        def poll(self) -> None:
            return None

    def popen(launch: list[str], **kwargs: object) -> object:
        launches.append((launch, kwargs))
        return _Process()

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', _int_callback(lambda _bytes: token))
    monkeypatch.setattr(app_module.subprocess, 'Popen', popen)
    monkeypatch.setattr(
        app_module,
        '_run_restart_handoff_parent',
        _handoff_kwargs_callback(
            lambda handoff_token, child_pid, **_kwargs: (
                handoffs.append((handoff_token, child_pid)) or True
            )
        ),
    )
    monkeypatch.setattr(app_module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(
        app_module.sys,
        'argv',
        [
            'Fleasion',
            '--restart-handoff-token',
            'd' * 32,
            '--restart-handoff-parent-pid',
            '1',
            '--kill-others',
        ],
    )
    monkeypatch.setattr(app_module.sys, 'executable', '/tmp/Fleasion')
    monkeypatch.setattr(app_module.os, 'getpid', lambda: 3131)
    monkeypatch.setenv('PYINSTALLER_RESET_ENVIRONMENT', 'stale-parent-value')

    assert app_module.restart_fleasion_normally(verify_startup=True)
    assert launches[0][0] == [
        '/tmp/Fleasion',
        '--restart-handoff-token',
        token,
        '--restart-handoff-parent-pid',
        '3131',
    ]
    launch_env = cast('dict[str, str]', launches[0][1]['env'])
    assert launch_env['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert app_module.os.environ['PYINSTALLER_RESET_ENVIRONMENT'] == 'stale-parent-value'
    assert handoffs == [(token, 6262)]


def test_verified_restart_keeps_current_process_when_child_dies_before_prepared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = 'e' * 32

    class _Process:
        pid = 7272

        def poll(self) -> int:
            return 1

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', _int_callback(lambda _bytes: token))
    monkeypatch.setattr(
        app_module.subprocess, 'Popen', _args_kwargs_callback(lambda *_args, **_kwargs: _Process())
    )
    monkeypatch.setattr(app_module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module.sys, 'executable', '/tmp/Fleasion')

    assert not app_module.restart_fleasion_normally(verify_startup=True)


def test_windows_verified_hosts_restart_invokes_uac_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = 'f' * 32
    relaunches: list[dict[str, object]] = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', _int_callback(lambda _bytes: token))
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: 123))
    monkeypatch.setattr(app_module.os, 'getpid', lambda: 8181)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        _kwargs_callback(lambda **kwargs: relaunches.append(kwargs) or True),
    )
    monkeypatch.setattr(
        app_module.subprocess,
        'Popen',
        _args_kwargs_callback(
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError('Windows hosts handoff must not spawn a non-elevated bootstrap')
            )
        ),
    )

    assert app_module.restart_fleasion_normally(verify_startup=True, require_admin=True)
    assert relaunches == [
        {
            'extra_args': '',
            'parent_hwnd': 123,
            'restart_handoff_token': token,
            'restart_handoff_parent_pid': 8181,
        }
    ]


def test_windows_verified_hosts_restart_waits_for_final_elevated_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = '2' * 32
    parent_pid = 8282
    launcher_pid = 9291
    application_pid = 9292
    events: list[object] = []
    state = {'attached': True, 'launcher_alive': True, 'application_alive': True}
    child_release = threading.Event()

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', _int_callback(lambda _bytes: token))
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module.os, 'getpid', lambda: parent_pid)
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: None))
    monkeypatch.setattr(
        app_module,
        '_pid_is_alive',
        _int_callback(lambda pid: pid == application_pid and state['application_alive']),
    )

    def suspend() -> bool:
        events.append('parent-release-single-instance')
        state['attached'] = False
        return True

    monkeypatch.setattr(app_module, '_suspend_single_instance_for_handoff', suspend)
    monkeypatch.setattr(
        app_module,
        '_single_instance_shared_memory',
        SimpleNamespace(isAttached=lambda: state['attached']),
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('unexpected-parent-resume') or True,
    )

    def simulated_uac_relaunch(**kwargs: object) -> bool:
        assert kwargs['restart_handoff_token'] == token
        assert kwargs['restart_handoff_parent_pid'] == parent_pid

        def elevated_child() -> None:
            events.append('elevated-prepared')
            assert _write_restart_handoff_marker(token, 'prepared', application_pid)
            assert _wait_for_restart_release(token, parent_pid)
            events.append('elevated-released')
            events.append('elevated-ready')
            assert _write_restart_handoff_marker(token, 'ready', application_pid)
            child_release.wait(1.0)

        child = threading.Thread(target=elevated_child)
        child.start()
        try:
            return _run_restart_handoff_parent(
                token,
                launcher_pid,
                is_launcher_alive=lambda: state['launcher_alive'],
                terminate_launcher=lambda: state.__setitem__('launcher_alive', False),
            )
        finally:
            child_release.set()
            child.join(timeout=1.0)

    monkeypatch.setattr(app_module, '_relaunch_as_admin', simulated_uac_relaunch)

    assert app_module.restart_fleasion_normally(verify_startup=True, require_admin=True)
    assert events == [
        'elevated-prepared',
        'parent-release-single-instance',
        'elevated-released',
        'elevated-ready',
    ]
    assert not any(tmp_path.glob(f'.restart-*-{token}'))


def test_windows_uac_failure_keeps_parent_ownership_for_rollback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token = '1' * 32
    suspended: list[bool] = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', _int_callback(lambda _bytes: token))
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', _object_callback(lambda _widget: None))
    monkeypatch.setattr(app_module, '_relaunch_as_admin', _kwargs_callback(lambda **_kwargs: False))
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: suspended.append(True) or True,
    )

    assert not app_module.restart_fleasion_normally(verify_startup=True, require_admin=True)
    assert suspended == []


def test_restart_handoff_credentials_are_stripped_before_nested_relaunch() -> None:
    assert _strip_restart_handoff_args(
        [
            '--foo',
            '--restart-handoff-token',
            'a' * 32,
            '--restart-handoff-parent-pid=42',
            '--bar',
        ]
    ) == ['--foo', '--bar']


def test_env_to_hosts_live_switch_avoids_process_restart(monkeypatch: pytest.MonkeyPatch) -> None:
    from fleasion.gui import settings_tab

    events: list[object] = []
    proxy_master = SimpleNamespace(
        can_live_switch_to_hosts=lambda: True,
        restart_for_mode_switch=lambda: events.append('restart_proxy'),
    )
    tray = SimpleNamespace(
        proxy_master=proxy_master,
        restart_fleasion=lambda: (_ for _ in ()).throw(
            AssertionError('live hosts switch must not relaunch Fleasion')
        ),
        notify_proxy_mode_changed=lambda: events.append('notify'),
    )
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        run_on_boot=False,
    )
    tab = SimpleNamespace(
        _config=config,
        _tray=tray,
        _proxy_mode_combo=SimpleNamespace(currentData=lambda: 'hosts'),
    )

    cast(
        'Callable[[object], None]',
        _private_attr(settings_tab.SettingsTab, '_on_proxy_mode_changed'),
    )(tab)

    assert config.proxy_mode == 'hosts'
    assert events == ['restart_proxy', 'notify']


def test_env_to_hosts_with_proxy_disabled_only_persists_mode() -> None:
    from fleasion.gui import settings_tab

    events: list[object] = []
    proxy_master = SimpleNamespace(
        can_live_switch_to_hosts=lambda: False,
        restart_for_mode_switch=lambda: events.append('restart_proxy'),
    )
    tray = SimpleNamespace(
        proxy_master=proxy_master,
        restart_fleasion=lambda: events.append('restart_app') or True,
        notify_proxy_mode_changed=lambda: events.append('notify'),
    )
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=False,
        run_on_boot=False,
    )
    tab = SimpleNamespace(
        _config=config,
        _tray=tray,
        _proxy_mode_combo=SimpleNamespace(currentData=lambda: 'hosts'),
    )

    cast(
        'Callable[[object], None]',
        _private_attr(settings_tab.SettingsTab, '_on_proxy_mode_changed'),
    )(tab)

    assert config.proxy_mode == 'hosts'
    assert events == ['notify']


def test_env_to_hosts_failed_replacement_rolls_back_mode_and_autostart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.gui import settings_tab

    sync_modes: list[str] = []
    warnings: list[object] = []
    selected_indexes: list[int] = []
    proxy_master = SimpleNamespace(can_live_switch_to_hosts=lambda: False)
    tray = SimpleNamespace(
        proxy_master=proxy_master,
        restart_fleasion=lambda: False,
        notify_proxy_mode_changed=lambda: (_ for _ in ()).throw(
            AssertionError('failed switch must not be announced as active')
        ),
    )
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        run_on_boot=True,
    )
    combo = SimpleNamespace(
        currentData=lambda: 'hosts',
        findData=_str_callback(lambda mode: 0 if mode == 'env' else -1),
        blockSignals=_bool_callback(lambda _blocked: None),
        setCurrentIndex=_int_callback(lambda index: selected_indexes.append(index)),
    )
    tab = SimpleNamespace(_config=config, _tray=tray, _proxy_mode_combo=combo)

    def _sync_autostart(_enabled: bool, _config_dir: Path, *, proxy_mode: str) -> bool:
        sync_modes.append(proxy_mode)
        return True

    monkeypatch.setattr(settings_tab, 'sync_autostart', _sync_autostart)
    monkeypatch.setattr(
        settings_tab.QMessageBox,
        'warning',
        _args_callback(lambda *_args: warnings.append(_args[1])),
    )

    cast(
        'Callable[[object], None]',
        _private_attr(settings_tab.SettingsTab, '_on_proxy_mode_changed'),
    )(tab)

    assert config.proxy_mode == 'env'
    assert sync_modes == ['hosts', 'env']
    assert selected_indexes == [0]
    assert warnings == ['Proxy Mode Change Failed']


def test_env_to_hosts_uncertain_replacement_does_not_reclaim_or_rewrite_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.gui import settings_tab

    sync_modes: list[str] = []
    criticals: list[object] = []
    proxy_master = SimpleNamespace(can_live_switch_to_hosts=lambda: False)
    tray = SimpleNamespace(
        proxy_master=proxy_master,
        restart_fleasion=lambda: None,
        notify_proxy_mode_changed=lambda: (_ for _ in ()).throw(
            AssertionError('uncertain switch must not be announced as active')
        ),
    )
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        run_on_boot=True,
    )
    combo = SimpleNamespace(currentData=lambda: 'hosts')
    tab = SimpleNamespace(_config=config, _tray=tray, _proxy_mode_combo=combo)

    def _sync_autostart(_enabled: bool, _config_dir: Path, *, proxy_mode: str) -> bool:
        sync_modes.append(proxy_mode)
        return True

    monkeypatch.setattr(settings_tab, 'sync_autostart', _sync_autostart)
    monkeypatch.setattr(
        settings_tab.QMessageBox,
        'critical',
        _args_callback(lambda *_args: criticals.append(_args[1])),
    )

    cast(
        'Callable[[object], None]',
        _private_attr(settings_tab.SettingsTab, '_on_proxy_mode_changed'),
    )(tab)

    assert config.proxy_mode == 'hosts'
    assert sync_modes == ['hosts']
    assert criticals == ['Proxy Mode Change Incomplete']


def test_windows_hosts_to_env_live_switch_rearms_gdk_after_proxy_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fleasion.gui import settings_tab

    events: list[object] = []
    proxy_master = SimpleNamespace(
        restart_for_mode_switch=lambda: events.append('restart_proxy'),
    )
    monitor = SimpleNamespace(
        env_lifecycle=SimpleNamespace(handle_player_launch=_path_callback(lambda _path: True)),
        is_player_running=lambda: False,
    )
    tray = SimpleNamespace(
        proxy_master=proxy_master,
        roblox_monitor=monitor,
        notify_proxy_mode_changed=lambda: events.append('notify'),
    )
    config = SimpleNamespace(
        proxy_mode='hosts',
        proxy_features_enabled=True,
        run_on_boot=False,
    )
    tab = SimpleNamespace(
        _config=config,
        _tray=tray,
        _proxy_mode_combo=SimpleNamespace(currentData=lambda: 'env'),
    )
    monkeypatch.setattr(settings_tab.sys, 'platform', 'win32')
    monkeypatch.setattr(
        settings_tab,
        'EnvProxyWarningDialog',
        _object_callback(lambda _parent: SimpleNamespace(exec=lambda: events.append('dialog'))),
    )
    monkeypatch.setattr(
        settings_tab, 'run_in_thread', _callable_callback(lambda function: function)
    )
    monkeypatch.setattr(
        app_module,
        '_arm_windows_gdk_env_proxy_when_ready',
        _object_callback(lambda proxy: events.append(('arm_gdk', proxy)) or True),
    )

    cast(
        'Callable[[object], None]',
        _private_attr(settings_tab.SettingsTab, '_on_proxy_mode_changed'),
    )(tab)

    assert config.proxy_mode == 'env'
    assert events == [
        'dialog',
        'restart_proxy',
        ('arm_gdk', proxy_master),
        'notify',
    ]


def test_macos_uri_watcher_handoff_passes_target_to_special_lifecycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    qt_app = QApplication.instance() or QApplication([])
    from fleasion.utils import platform_macos

    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls: list[object] = []

    class _Lifecycle:
        owns_player = False
        operation_in_progress = False

        def handle_intercepted_player_launch(self, *args: object) -> None:
            lifecycle_calls.append(args)

    class _Interceptor:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def start(self) -> bool:
            return True

        def stop(self) -> None:
            return None

    proxy_master = SimpleNamespace(
        set_roblox_player_running=_bool_callback(lambda _running: None),
        wait_for_env_proxy_ready=_float_callback(lambda timeout=0.0: True),
    )
    monkeypatch.setattr(platform_macos, 'MacOSRobloxUriInterceptor', _Interceptor)
    monitor = _ROBLOX_EXIT_MONITOR_FACTORY(
        config, proxy_master=proxy_master, env_lifecycle=_Lifecycle()
    )
    _monitor_studio_signal(monitor).disconnect(_monitor_on_studio_detected(monitor))
    target = 'roblox-player:1+launchmode:play+placeId:6484006319'
    launch = SimpleNamespace(
        pid=123,
        executable_path=tmp_path / 'Roblox.app' / 'Contents' / 'MacOS' / 'RobloxPlayer',
    )

    _handle_macos_uri_interception(monitor, launch, target)

    assert lifecycle_calls == [(Path(launch.executable_path), target)]
    assert qt_app is not None


def test_linux_hosts_nix_snippet_default_includes_profile_api_host() -> None:
    snippet = _linux_hosts_nix_snippet({})

    assert '127.0.0.1 apis.roblox.com' in snippet


def test_manual_upstream_credentials_missing_only_for_empty_selected_manual_mode() -> None:
    class _Config:
        def __init__(self) -> None:
            self.upstream_transport_mode = 'http_connect'
            self.upstream_http_connect_username = ''
            self.upstream_http_connect_password = ''
            self.upstream_socks5_username = ''
            self.upstream_socks5_password = ''

    config = _Config()

    assert _manual_upstream_credentials_missing(config) is True
    config.upstream_http_connect_username = 'proxy-user'
    assert _manual_upstream_credentials_missing(config) is False
    config.upstream_transport_mode = 'auto'
    config.upstream_http_connect_username = ''
    assert _manual_upstream_credentials_missing(config) is False


def test_macos_relay_failure_retry_action_restarts_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    retries: list[bool | None] = []
    invoker = _ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        '_show_macos_relay_failed_dialog',
        _details_callback(lambda _details: 'retry'),
    )

    invoker.handle_proxy_error('macos_relay_failed', {'attempts': 3})

    assert retries == [None]


def test_macos_relay_failure_reinstall_action_replaces_helper_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retries: list[bool | None] = []
    installs: list[None] = []
    invoker = _ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        '_show_macos_relay_failed_dialog',
        _details_callback(lambda _details: 'reinstall'),
    )
    monkeypatch.setattr(
        macos_proxy_helper,
        'install_helper',
        lambda: installs.append(None) or (True, ''),
    )

    invoker.handle_proxy_error('macos_relay_failed', {'attempts': 3})

    assert installs == [None]
    assert retries == [None]
