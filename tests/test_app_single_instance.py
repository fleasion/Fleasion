import os
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from fleasion import app as app_module
from fleasion import __version__ as APP_VERSION
from fleasion.utils import macos_proxy_helper
from fleasion.app import (
    _RobloxUrlEventFilter,
    _handle_single_instance_command,
    _linux_hosts_nix_snippet,
    _looks_like_macos_fleasion_command,
    _manual_upstream_credentials_missing,
    _repair_autostart_once,
    _repair_windows_firewall_once,
    _repair_roblox_permissions_once,
    _run_privileged_hosts_cleanup,
    _show_env_proxy_stale_hosts_dialog,
    _show_roblox_permission_failure,
    _show_run_on_boot_failure,
    _show_windows_upstream_firewall_dialog,
    _should_reclaim_stale_single_instance,
    _should_sync_autostart_on_launch,
    _windows_ca_permission_denied_dirs,
    kill_other_fleasion_instances,
)
from PySide6.QtCore import QEvent, QSharedMemory, QUrl
from PySide6.QtWidgets import QApplication


def test_macos_fleasion_process_matching_accepts_real_launch_forms():
    assert _looks_like_macos_fleasion_command(
        f'/Applications/Fleasion.app/Contents/MacOS/Fleasion-v{APP_VERSION} --no-dashboard'
    )
    assert _looks_like_macos_fleasion_command('/project/.venv/bin/Fleasion')
    assert _looks_like_macos_fleasion_command('/usr/bin/python3 /project/launcher.py')
    assert _looks_like_macos_fleasion_command('/usr/bin/python3 -m Fleasion')
    assert _looks_like_macos_fleasion_command(
        '/project/.venv/bin/python /project/.venv/bin/fleasion'
    )


def test_macos_fleasion_process_matching_rejects_unrelated_commands():
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c tail '/Users/test/Library/Application Support/FleasionNT/logs/fleasion.log'"
    )
    assert not _looks_like_macos_fleasion_command(
        f"/bin/zsh -c ps -axo command | rg 'Fleasion-v{APP_VERSION}|launcher.py'"
    )
    assert not _looks_like_macos_fleasion_command('/usr/bin/python3 /tmp/not-fleasion.py')


def test_fleasion_process_matching_rejects_linux_proxy_helper_commands():
    assert not _looks_like_macos_fleasion_command(
        '/opt/Fleasion/Fleasion --linux-proxy-helper --backend-port 8443'
    )
    assert not _looks_like_macos_fleasion_command(
        '/usr/bin/python3 /project/launcher.py --linux-proxy-helper --backend-port 8443'
    )
    assert not _looks_like_macos_fleasion_command(
        '/usr/bin/python3 /project/src/fleasion/linux_proxy_helper_daemon.py --backend-port 8443'
    )


def test_stale_single_instance_can_be_reclaimed_on_linux_without_gui_process(monkeypatch):
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [])

    assert _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_stale_single_instance_not_reclaimed_on_linux_with_gui_process(monkeypatch):
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [1234])

    assert not _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_kill_other_instances_prefers_graceful_exit(monkeypatch):
    calls = []

    monkeypatch.setattr(app_module, '_request_other_fleasion_instances_exit', lambda: True)
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [1234])
    monkeypatch.setattr(
        app_module.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs))
    )

    kill_other_fleasion_instances()

    assert calls == []


def test_single_instance_quit_command_exits_tray():
    class _SocketStub:
        def readAll(self):
            return b'quit\n'

    class _TrayStub:
        def __init__(self):
            self.exit_calls = 0

        def _exit_app(self):
            self.exit_calls += 1

    tray = _TrayStub()

    _handle_single_instance_command(_SocketStub(), tray)

    assert tray.exit_calls == 1


def test_single_instance_preserve_command_keeps_env_player():
    class _SocketStub:
        def readAll(self):
            return b'quit-preserve-env-player\n'

    class _TrayStub:
        def __init__(self):
            self.exit_kwargs = []

        def _exit_app(self, **kwargs):
            self.exit_kwargs.append(kwargs)

    tray = _TrayStub()

    _handle_single_instance_command(_SocketStub(), tray)

    assert tray.exit_kwargs == [{'preserve_roblox': True}]


def test_roblox_url_event_filter_queues_until_application_is_ready():
    received = []
    event_filter = _RobloxUrlEventFilter()
    event_filter.roblox_uri_received.connect(received.append)

    class _Event:
        def type(self):
            return QEvent.Type.FileOpen

        def url(self):
            return QUrl('roblox://experiences/start?placeId=1')

    assert event_filter.eventFilter(None, _Event()) is False
    assert received == []
    event_filter.start()
    assert received == ['roblox://experiences/start?placeId=1']


def test_single_instance_launch_command_preserves_uri(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app_module,
        'run_in_thread',
        lambda function: lambda *args: calls.append((function, args)),
    )

    class _SocketStub:
        def readAll(self):
            return b'launch-roblox\nroblox://experiences/start?placeId=1\n'

    class _TrayStub:
        config_manager = type(
            'Config', (), {'proxy_mode': 'hosts', 'proxy_features_enabled': False}
        )()
        proxy_master = None

    _handle_single_instance_command(_SocketStub(), _TrayStub())

    assert len(calls) == 1
    assert calls[0][1][1] == 'roblox://experiences/start?placeId=1'


def test_autostart_resync_includes_linux_normal_user(monkeypatch):
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)

    assert _should_sync_autostart_on_launch(True)
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    assert _should_sync_autostart_on_launch(True)
    assert not _should_sync_autostart_on_launch(False)


def test_autostart_resync_runs_without_admin_on_windows(monkeypatch):
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)

    assert _should_sync_autostart_on_launch(True)


def test_env_proxy_migration_forces_legacy_users_before_acknowledgement():
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=False,
    )

    assert app_module._prepare_env_proxy_migration(config) is True
    assert config.proxy_mode == 'env'
    assert config.env_proxy_migration_v1_complete is False


def test_env_proxy_migration_uses_first_time_guide_for_new_users():
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=False,
        env_proxy_migration_v1_complete=False,
    )

    assert app_module._prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'env'
    assert config.env_proxy_migration_v1_complete is False


def test_completed_env_proxy_migration_preserves_hosts_choice():
    config = SimpleNamespace(
        proxy_mode='hosts',
        first_time_setup_complete=True,
        env_proxy_migration_v1_complete=True,
    )

    assert app_module._prepare_env_proxy_migration(config) is False
    assert config.proxy_mode == 'hosts'


def test_linux_env_proxy_migration_adopts_running_sober_without_relaunch(monkeypatch):
    events = []
    config = SimpleNamespace(
        proxy_features_enabled=True,
        env_proxy_migration_v1_complete=False,
    )
    lifecycle = SimpleNamespace(
        handle_adopted_player_launch=lambda path: events.append(('adopt', Path(path)))
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

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, _value):
            pass

        def setIcon(self, _value):
            pass

        def setText(self, _value):
            pass

        def setInformativeText(self, _value):
            pass

        def setWindowIcon(self, _value):
            pass

        def setStandardButtons(self, _value):
            pass

        def addButton(self, label, _role):
            button = object()
            if label == 'Apply for Future Launches':
                self._clicked = button
            return button

        def setDefaultButton(self, _value):
            pass

        def setEscapeButton(self, _value):
            pass

        def exec(self):
            events.append(('ack-state', config.env_proxy_migration_v1_complete))

        def clickedButton(self):
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, 'run_in_thread', lambda function: function)

    app_module._show_env_proxy_migration(config, monitor)

    assert events == [
        ('ack-state', False),
        ('adopt', Path('org.vinegarhq.Sober')),
    ]
    assert config.env_proxy_migration_v1_complete is True
    assert monitor.was_running is True
    assert monitor._player_was_running is True


def test_env_proxy_migration_does_not_relaunch_when_proxy_features_are_disabled(
    monkeypatch,
):
    config = SimpleNamespace(
        proxy_features_enabled=False,
        env_proxy_migration_v1_complete=False,
    )
    lifecycle = SimpleNamespace(
        handle_player_launch=lambda _path: (_ for _ in ()).throw(
            AssertionError('disabled proxy features must not relaunch Roblox')
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

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, _value):
            pass

        def setIcon(self, _value):
            pass

        def setText(self, _value):
            pass

        def setInformativeText(self, _value):
            pass

        def setWindowIcon(self, _value):
            pass

        def setStandardButtons(self, _value):
            pass

        def exec(self):
            pass

        def clickedButton(self):
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)

    app_module._show_env_proxy_migration(config, monitor)

    assert config.env_proxy_migration_v1_complete is True
    assert monitor.was_running is True
    assert monitor._player_was_running is True


def test_run_on_boot_failure_can_launch_one_time_admin_repair(monkeypatch):
    selected = []
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

        def __init__(self, _parent):
            self._buttons = []

        def setWindowTitle(self, _title):
            pass

        def setIcon(self, _icon):
            pass

        def setText(self, text):
            selected.append(text)

        def setWindowIcon(self, _icon):
            pass

        def setStandardButtons(self, _buttons):
            pass

        def addButton(self, text, _role):
            button = object()
            self._buttons.append((text, button))
            if text == 'Repair Now (Recommended)':
                self._clicked = button
            return button

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            pass

        def clickedButton(self):
            return self._clicked

    relaunches = []
    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        lambda **kwargs: relaunches.append(kwargs) or True,
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


def test_nonwindows_run_on_boot_failure_never_offers_admin_repair(monkeypatch):
    calls = []

    class _MessageBox:
        class Icon:
            Warning = object()

        class StandardButton:
            Ok = object()

        def __init__(self, _parent):
            pass

        def setWindowTitle(self, _title):
            pass

        def setIcon(self, _icon):
            pass

        def setText(self, text):
            calls.append(('text', text))

        def setWindowIcon(self, _icon):
            pass

        def setStandardButtons(self, button):
            calls.append(('buttons', button))

        def exec(self):
            pass

    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        lambda **_kwargs: calls.append(('relaunch', True)),
    )

    _show_run_on_boot_failure(None)

    assert any('Check the application log' in value for kind, value in calls if kind == 'text')
    assert not any(kind == 'relaunch' for kind, _value in calls)


def test_nonwindows_permission_failure_does_not_offer_windows_acl(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        app_module,
        'QMessageBox',
        lambda *_args: (_ for _ in ()).throw(AssertionError('Windows dialog opened')),
    )

    _show_roblox_permission_failure(None, [tmp_path])


def test_windows_ca_permission_failure_extracts_install_for_acl_repair(monkeypatch, tmp_path):
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


def test_windows_ca_permission_failure_offers_acl_and_retries_proxy(monkeypatch, tmp_path):
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    offered = []
    retries = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: 'parent')
    monkeypatch.setattr(
        app_module,
        '_show_roblox_permission_failure',
        lambda parent, paths, **kwargs: offered.append((parent, paths, kwargs)),
    )

    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(True))
    invoker.handle_proxy_error(
        'roblox_ca_patch_failed',
        {'failed': [{'resource_dir': str(install), 'error': '[WinError 5] Access is denied'}]},
    )

    assert offered[0][0] == 'parent'
    assert offered[0][1] == [install]
    assert 'cacert.pem for Env Proxy' in offered[0][2]['failure_text']
    offered[0][2]['on_repaired']()
    assert retries == [True]


def test_windows_ca_nonpermission_failure_keeps_diagnostic_dialog(monkeypatch, tmp_path):
    install = tmp_path / 'Roblox' / 'Versions' / 'version-test'
    shown = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(
        app_module,
        '_show_roblox_ca_patch_failed_dialog',
        lambda details: shown.append(details),
    )
    monkeypatch.setattr(
        app_module,
        '_show_roblox_permission_failure',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('ACL repair offered')),
    )
    details = {
        'failed': [
            {
                'resource_dir': str(install),
                'error': 'cacert.pem was not launch-healthy after direct patch',
            }
        ]
    }

    app_module._ProxyErrorInvoker().handle_proxy_error('roblox_ca_patch_failed', details)

    assert shown == [details]


def test_hosts_capacity_error_does_not_retry_proxy(monkeypatch):
    shown = []
    retries = []
    monkeypatch.setattr(
        app_module,
        '_show_hosts_capacity_dialog',
        lambda details: shown.append(details),
    )

    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(True))
    invoker.handle_proxy_error(
        'hosts_entries_would_exceed_limit',
        {'hosts_size_bytes': 600_000},
    )

    assert shown == [{'hosts_size_bytes': 600_000}]
    assert retries == []


def test_successful_permission_repair_runs_proxy_retry_callback(monkeypatch, tmp_path):
    from fleasion.utils import windows_permissions

    callbacks = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        windows_permissions,
        'read_repair_result',
        lambda _path: {'ok': True, 'granted': ['version-test']},
    )
    monkeypatch.setattr(windows_permissions, 'clear_pending_repair', lambda _path: None)
    monkeypatch.setattr(windows_permissions, 'clear_repair_result', lambda _path: None)

    app_module._poll_roblox_permission_repair(
        None,
        deadline=10.0,
        on_repaired=lambda: callbacks.append(True),
    )

    assert callbacks == [True]


def test_permission_repair_poll_times_out_and_cleans_state(monkeypatch, tmp_path):
    from fleasion.utils import windows_permissions

    cleared = []
    warnings = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.time, 'monotonic', lambda: 20.0)
    monkeypatch.setattr(windows_permissions, 'read_repair_result', lambda _path: None)
    monkeypatch.setattr(
        windows_permissions,
        'clear_pending_repair',
        lambda path: cleared.append(('pending', path)),
    )
    monkeypatch.setattr(
        windows_permissions,
        'clear_repair_result',
        lambda path: cleared.append(('result', path)),
    )
    monkeypatch.setattr(
        app_module.QMessageBox,
        'warning',
        lambda *args: warnings.append(args),
    )

    app_module._poll_roblox_permission_repair(object(), deadline=10.0)

    assert cleared == [('pending', tmp_path), ('result', tmp_path)]
    assert len(warnings) == 1


def test_repair_autostart_once_syncs_only_from_admin(monkeypatch, tmp_path):
    calls = []

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
        type('Log', (), {'log': staticmethod(lambda *args: None)})(),
    )
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    from fleasion.utils import autostart
    from fleasion.utils import windows_permissions

    monkeypatch.setattr(
        windows_permissions,
        'windows_user_id_from_sid',
        lambda sid: 'TestDomain\\OriginalUser' if sid == 'S-1-5-21-1234' else '',
    )

    monkeypatch.setattr(
        autostart,
        'sync_autostart',
        lambda enabled, config_dir, **kwargs: calls.append((enabled, config_dir, kwargs)) or True,
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


def test_repair_autostart_once_can_remove_legacy_task(monkeypatch, tmp_path):
    calls = []

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
        type('Log', (), {'log': staticmethod(lambda *args: None)})(),
    )
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    from fleasion.utils import autostart

    monkeypatch.setattr(
        autostart,
        'sync_autostart',
        lambda enabled, config_dir, **kwargs: calls.append((enabled, config_dir, kwargs)) or True,
    )

    assert _repair_autostart_once(enabled=False) == 0
    assert calls == [
        (
            False,
            tmp_path,
            {'windows_user_id': None, 'proxy_mode': 'env'},
        )
    ]


def test_windows_elevation_carries_original_desktop_sid(monkeypatch):
    from fleasion.utils import windows_permissions

    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(
        windows_permissions,
        'current_windows_user_identity',
        lambda: ('S-1-5-21-1234', r'DesktopDomain\OriginalUser'),
    )
    args = ['--repair-autostart']

    assert app_module._append_windows_requesting_user_args(args)

    assert args == [
        '--repair-autostart',
        '--fleasion-requesting-user-sid=S-1-5-21-1234',
    ]


def test_roblox_permission_prompt_requests_targeted_elevation(monkeypatch, tmp_path):
    selected = []
    relaunches = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, _title):
            pass

        def setIcon(self, _icon):
            pass

        def setText(self, text):
            selected.append(text)

        def setWindowIcon(self, _icon):
            pass

        def addButton(self, text, _role):
            button = object()
            if text.startswith('Grant access'):
                self._clicked = button
            return button

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            pass

        def clickedButton(self):
            return self._clicked

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module, 'get_icon_path', lambda: None)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        lambda **kwargs: relaunches.append(kwargs) or True,
    )

    from fleasion.utils import windows_permissions

    pending = []
    monkeypatch.setattr(
        windows_permissions,
        'write_pending_repair',
        lambda paths, config_dir: pending.extend(paths) or True,
    )
    monkeypatch.setattr(windows_permissions, 'clear_repair_result', lambda _path: None)

    _show_roblox_permission_failure(None, [tmp_path / 'Roblox' / 'version-old'])

    assert 'current Windows account' in selected[0]
    assert pending == [tmp_path / 'Roblox' / 'version-old']
    assert relaunches == [{'extra_args': '--repair-roblox-permissions', 'parent_hwnd': None}]


def test_repair_roblox_permissions_once_writes_result_and_clears_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(lambda *args: None)})(),
    )

    from fleasion.utils import windows_permissions

    paths = [tmp_path / 'Roblox' / 'version-old']
    monkeypatch.setattr(windows_permissions, 'read_pending_repair', lambda _path: paths)
    monkeypatch.setattr(
        windows_permissions,
        'grant_current_user_modify_access',
        lambda values, **kwargs: {
            'ok': kwargs.get('user_sid') == 'S-1-5-21-1234',
            'granted': [str(values[0])],
            'failed': [],
        },
    )
    results = []
    monkeypatch.setattr(
        windows_permissions,
        'write_repair_result',
        lambda result, _path: results.append(result),
    )
    cleared = []
    monkeypatch.setattr(
        windows_permissions,
        'clear_pending_repair',
        lambda path: cleared.append(path),
    )

    assert _repair_roblox_permissions_once('S-1-5-21-1234') == 0
    assert results == [{'ok': True, 'granted': [str(paths[0])], 'failed': []}]
    assert cleared == [tmp_path]


def test_repair_windows_firewall_once_writes_result_and_clears_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(lambda *args: None)})(),
    )

    from fleasion.utils import windows_firewall

    monkeypatch.setattr(windows_firewall, 'read_pending_repair', lambda _path: True)
    monkeypatch.setattr(
        windows_firewall,
        'install_fleasion_firewall_rules',
        lambda: {'ok': True, 'rules': ['in', 'out'], 'failed': []},
    )
    results = []
    monkeypatch.setattr(
        windows_firewall,
        'write_repair_result',
        lambda result, _path: results.append(result),
    )
    cleared = []
    monkeypatch.setattr(
        windows_firewall,
        'clear_pending_repair',
        lambda path: cleared.append(path),
    )

    assert _repair_windows_firewall_once() == 0
    assert results == [{'ok': True, 'rules': ['in', 'out'], 'failed': []}]
    assert cleared == [tmp_path]


def test_privileged_hosts_cleanup_uses_pkexec_on_linux(monkeypatch):
    calls = []
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(
        'fleasion.utils.linux_proxy_helper.cleanup_hosts_with_pkexec',
        lambda: calls.append(True) or True,
    )

    assert _run_privileged_hosts_cleanup() is True
    assert calls == [True]


def test_privileged_hosts_cleanup_waits_for_windows_elevated_child(monkeypatch):
    calls = []
    parent = object()
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(
        app_module, '_window_handle', lambda widget: 123 if widget is parent else None
    )

    def _relaunch(**kwargs):
        completion = kwargs.pop('completion')
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


def test_privileged_hosts_cleanup_logs_known_elevated_child_failure(monkeypatch):
    logs = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: None)

    def _relaunch(**kwargs):
        kwargs['completion']['exit_code'] = app_module._HOSTS_CLEANUP_NOT_ADMIN_EXIT
        return False

    monkeypatch.setattr(app_module, '_relaunch_as_admin', _relaunch)
    monkeypatch.setattr(app_module.log_buffer, 'log', lambda *args: logs.append(args))

    assert _run_privileged_hosts_cleanup() is False
    assert any('did not receive an administrator token' in message for _category, message in logs)


def test_privileged_hosts_cleanup_classifies_still_active_timeout(monkeypatch):
    logs = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: None)

    def _relaunch(**kwargs):
        kwargs['completion'].update({'wait_result': 258, 'exit_code_read': False, 'exit_code': None})
        return False

    monkeypatch.setattr(app_module, '_relaunch_as_admin', _relaunch)
    monkeypatch.setattr(app_module.log_buffer, 'log', lambda *args: logs.append(args))

    assert _run_privileged_hosts_cleanup() is False
    assert any('still running' in message for _category, message in logs)
    assert not any('exit=259' in message for _category, message in logs)


def test_elevated_hosts_cleanup_reports_write_failure(monkeypatch):
    from fleasion.proxy import master as proxy_master

    monkeypatch.setattr(app_module, '_is_admin', lambda: True)

    def _remove(_hosts, *, error_details):
        error_details['error'] = 'access denied by host protection'
        return False

    monkeypatch.setattr(proxy_master, '_remove_hosts_entries', _remove)

    assert app_module._cleanup_hosts_once() == app_module._HOSTS_CLEANUP_WRITE_FAILED_EXIT


def test_elevated_hosts_cleanup_reports_unexpected_exception(monkeypatch):
    from fleasion.proxy import master as proxy_master

    monkeypatch.setattr(app_module, '_is_admin', lambda: True)
    monkeypatch.setattr(
        proxy_master,
        '_remove_hosts_entries',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError('boom')),
    )

    assert app_module._cleanup_hosts_once() == app_module._HOSTS_CLEANUP_UNEXPECTED_EXIT


def test_stale_env_hosts_dialog_runs_privileged_repair(monkeypatch):
    from fleasion.proxy import master as proxy_master

    stale = iter([True, False])
    calls = []
    monkeypatch.setattr(proxy_master, 'has_stale_hosts_entries', lambda _hosts: next(stale))
    monkeypatch.setattr(proxy_master, 'INTERCEPT_HOSTS', frozenset({'assetdelivery.roblox.com'}))
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(
        app_module,
        '_run_privileged_hosts_cleanup',
        lambda owner: calls.append((owner, owner.visible)) or True,
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            RejectRole = object()

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, _title):
            pass

        def setIcon(self, _icon):
            pass

        def setText(self, _text):
            pass

        def setInformativeText(self, _text):
            pass

        def addButton(self, text, _role):
            button = type('Button', (), {'setEnabled': lambda self, _enabled: None})()
            if text.startswith('Fix Hosts File'):
                self._clicked = button
            return button

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            pass

        def clickedButton(self):
            return self._clicked

        def show(self):
            self.visible = True

        def raise_(self):
            pass

        def activateWindow(self):
            pass

        def hide(self):
            self.visible = False

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)
    monkeypatch.setattr(app_module.QApplication, 'processEvents', lambda: None)

    _show_env_proxy_stale_hosts_dialog()

    assert len(calls) == 1
    owner, was_visible = calls[0]
    assert isinstance(owner, _MessageBox)
    assert was_visible is True
    assert owner.visible is False


def test_windows_upstream_dialog_requests_targeted_firewall_repair(monkeypatch, tmp_path):
    calls = []
    relaunches = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: None)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        lambda **kwargs: relaunches.append(kwargs) or True,
    )
    monkeypatch.setattr(app_module.QTimer, 'singleShot', lambda *_args: None)
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(lambda *args: calls.append(args))})(),
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            AcceptRole = object()
            ActionRole = object()
            RejectRole = object()

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, _title):
            pass

        def setIcon(self, _icon):
            pass

        def setText(self, text):
            calls.append(('text', text))

        def setInformativeText(self, text):
            calls.append(('informative', text))

        def addButton(self, text, _role):
            button = object()
            if text == 'Allow Fleasion Through Firewall':
                self._clicked = button
            return button

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            pass

        def clickedButton(self):
            return self._clicked

        def parentWidget(self):
            return None

    monkeypatch.setattr(app_module, 'QMessageBox', _MessageBox)

    from fleasion.utils import windows_firewall

    monkeypatch.setattr(
        windows_firewall,
        'get_fleasion_firewall_rule_status',
        lambda: {'ok': False, 'missing': ['out']},
    )
    pending = []
    monkeypatch.setattr(
        windows_firewall,
        'write_pending_repair',
        lambda config_dir: pending.append(config_dir),
    )
    monkeypatch.setattr(windows_firewall, 'clear_repair_result', lambda _path: None)
    monkeypatch.setattr(windows_firewall, 'clear_pending_repair', lambda _path: None)

    _show_windows_upstream_firewall_dialog(
        {'host': 'assetdelivery.roblox.com', 'proxy_mode': 'env'}
    )

    informative = next(value for kind, value in calls if kind == 'informative')
    assert 'Private and Public networks' in informative
    assert 'only Fleasion program rules' in informative
    assert pending == [tmp_path]
    assert relaunches == [{'extra_args': '--repair-firewall', 'parent_hwnd': None}]


def test_windows_upstream_dialog_escalates_when_firewall_rules_already_exist(monkeypatch):
    import webbrowser

    opened = []
    calls = []
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(
        webbrowser,
        'open',
        lambda url: opened.append(url),
    )
    monkeypatch.setattr(
        app_module,
        'log_buffer',
        type('Log', (), {'log': staticmethod(lambda *args: calls.append(args))})(),
    )

    class _MessageBox:
        class Icon:
            Warning = object()

        class ButtonRole:
            ActionRole = object()
            RejectRole = object()

        def __init__(self, _parent):
            self._clicked = None

        def setWindowTitle(self, title):
            calls.append(('title', title))

        def setIcon(self, _icon):
            pass

        def setText(self, text):
            calls.append(('text', text))

        def setInformativeText(self, text):
            calls.append(('informative', text))

        def addButton(self, text, _role):
            button = object()
            if text == 'Get Help on Discord':
                self._clicked = button
            return button

        def setDefaultButton(self, _button):
            pass

        def exec(self):
            pass

        def clickedButton(self):
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
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError('repair should not repeat')),
    )

    _show_windows_upstream_firewall_dialog({'host': 'assetdelivery.roblox.com'})

    informative = next(value for kind, value in calls if kind == 'informative')
    assert 'already installed' in informative
    assert 'Fleasion Discord' in informative
    assert opened == ['https://discord.gg/hXyhKehEZF']


def test_env_proxy_studio_launch_is_completely_untouched(monkeypatch):
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    monitor = app_module.RobloxExitMonitor(config)
    monitor._studio_detected.disconnect(monitor._on_studio_detected)
    notifications = []
    monitor._studio_detected.connect(lambda: notifications.append(True))

    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: True)
    monkeypatch.setattr(
        app_module,
        'get_roblox_studio_exe_path',
        lambda: (_ for _ in ()).throw(AssertionError('Env mode must not inspect Studio')),
    )

    monitor._check_roblox_status_locked()

    assert notifications == []
    assert monitor._studio_was_running is True
    assert qt_app is not None


def test_windows_desktop_player_launch_uses_env_lifecycle(monkeypatch, tmp_path):
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls = []

    class _Lifecycle:
        owns_player = False

        def handle_player_launch(self, exe_path):
            lifecycle_calls.append(Path(exe_path))
            return True

    proxy_master = SimpleNamespace(set_roblox_player_running=lambda _running: None)
    monitor = app_module.RobloxExitMonitor(
        config,
        proxy_master=proxy_master,
        env_lifecycle=_Lifecycle(),
    )
    player_exe = tmp_path / 'Roblox' / 'RobloxPlayerBeta.exe'
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: False)
    monkeypatch.setattr(app_module, 'get_roblox_player_exe_path', lambda: player_exe)
    monkeypatch.setattr(app_module, 'run_in_thread', lambda function: function)
    monkeypatch.setitem(
        app_module.sys.modules,
        'fleasion.utils.platform_windows',
        SimpleNamespace(
            is_roblox_gdk_env_proxy_armed=lambda: False,
            is_gdk_env_proxy_activation_in_progress=lambda: False,
            is_env_proxy_relaunched_player_running=lambda: False,
            is_roblox_gdk_exe_path=lambda _path: False,
        ),
    )

    monitor._check_roblox_status_locked()

    assert lifecycle_calls == [player_exe]
    assert monitor._suppress_next_player_exit_cache_delete is True
    assert qt_app is not None


def test_linux_browser_sober_launch_is_always_adopted_without_relaunch(monkeypatch):
    qt_app = QApplication.instance() or QApplication([])
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls = []

    class _Lifecycle:
        owns_player = False

        def handle_player_launch(self, _exe_path):
            lifecycle_calls.append('relaunch')
            return True

        def handle_adopted_player_launch(self, exe_path):
            lifecycle_calls.append(('adopt', Path(exe_path)))
            return True

    proxy_master = SimpleNamespace(
        set_roblox_player_running=lambda _running: None,
        _sober_env_proxy_override_active=False,
    )
    monitor = app_module.RobloxExitMonitor(
        config,
        proxy_master=proxy_master,
        env_lifecycle=_Lifecycle(),
    )
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(app_module, 'is_studio_running', lambda: False)
    monkeypatch.setattr(app_module, 'run_in_thread', lambda function: function)

    monitor._check_roblox_status_locked()

    assert lifecycle_calls == [('adopt', Path('org.vinegarhq.Sober'))]
    assert qt_app is not None


def test_linux_instance_uri_uses_sober_without_env_proxy_relaunch(monkeypatch):
    launches = []
    tray = SimpleNamespace(
        config_manager=SimpleNamespace(proxy_mode='env', proxy_features_enabled=True),
        proxy_master=SimpleNamespace(),
    )
    target = 'roblox://experiences/start?placeId=1'

    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module, 'launch_as_standard_user', lambda uri: launches.append(uri) or True)

    assert app_module._launch_roblox_uri_for_instance(tray, target)
    assert launches == [target]


def test_windows_gdk_arming_waits_for_final_proxy_port(monkeypatch):
    events = []
    cleanup = lambda: None

    class _Proxy:
        def wait_for_env_proxy_ready(self, timeout):
            events.append(('ready', timeout))
            return True

        def roblox_env_proxy_url(self):
            events.append(('url',))
            return 'http://127.0.0.1:49152'

    monkeypatch.setitem(
        app_module.sys.modules,
        'fleasion.utils.platform_windows',
        SimpleNamespace(
            arm_roblox_gdk_env_proxy=lambda url: events.append(('arm', url)) or True,
            disarm_roblox_gdk_env_proxy=cleanup,
        ),
    )
    monkeypatch.setattr(
        app_module.atexit,
        'register',
        lambda callback: events.append(('cleanup', callback)),
    )

    assert app_module._arm_windows_gdk_env_proxy_when_ready(_Proxy(), timeout=4.0)
    assert events == [
        ('ready', 4.0),
        ('url',),
        ('arm', 'http://127.0.0.1:49152'),
        ('cleanup', cleanup),
    ]


def test_windows_gdk_arming_stops_when_proxy_is_not_ready(monkeypatch):
    proxy = SimpleNamespace(
        wait_for_env_proxy_ready=lambda timeout: False,
        roblox_env_proxy_url=lambda: (_ for _ in ()).throw(
            AssertionError('an unready proxy has no final URL')
        ),
    )

    assert not app_module._arm_windows_gdk_env_proxy_when_ready(proxy)


def test_restart_handoff_token_cannot_select_an_arbitrary_path(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)

    assert app_module._restart_handoff_path('../outside') is None
    assert app_module._restart_handoff_path('A' * 32) is None
    assert app_module._restart_handoff_path('0' * 32, '../ready') is None
    assert app_module._restart_handoff_path('0' * 32, 'prepared') == (
        tmp_path / f'.restart-prepared-{"0" * 32}'
    )
    assert app_module._restart_handoff_path('0' * 32, 'release') == (
        tmp_path / f'.restart-release-{"0" * 32}'
    )
    assert app_module._restart_handoff_path('0' * 32, 'abort') == (
        tmp_path / f'.restart-abort-{"0" * 32}'
    )
    assert app_module._restart_handoff_path('0' * 32) == (
        tmp_path / f'.restart-ready-{"0" * 32}'
    )


def test_restart_child_joins_only_after_parent_release(monkeypatch, tmp_path):
    token = '9' * 32
    parent_pid = 2121
    child_pid = 3131
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.os, 'getpid', lambda: child_pid)
    (tmp_path / f'.restart-release-{token}').write_text(str(parent_pid), encoding='utf-8')

    assert app_module._join_restart_handoff(token, parent_pid)
    assert (tmp_path / f'.restart-prepared-{token}').read_text(encoding='utf-8') == str(
        child_pid
    )
    assert not (tmp_path / f'.restart-release-{token}').exists()


def test_restart_child_consumes_abort_before_ownership_transfer(monkeypatch, tmp_path):
    token = '5' * 32
    parent_pid = 4141
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    (tmp_path / f'.restart-abort-{token}').write_text(str(parent_pid), encoding='utf-8')

    assert not app_module._wait_for_restart_release(token, parent_pid)
    assert not (tmp_path / f'.restart-abort-{token}').exists()


def test_restart_handoff_parent_reclaims_only_after_application_exit(monkeypatch):
    token = 'a' * 32
    launcher_pid = 4242
    application_pid = 4243
    events = []
    state = {'attached': True}

    def wait_marker(_token, phase, *, is_launcher_alive, expected_value=None, timeout=0):
        assert _token == token
        assert is_launcher_alive()
        events.append(f'wait-{phase}')
        if phase == 'prepared':
            assert expected_value is None
            return application_pid
        assert expected_value == application_pid
        return None

    def suspend():
        events.append('suspend')
        state['attached'] = False
        return True

    def resume():
        events.append('resume')
        state['attached'] = True
        return True

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(app_module, '_pid_is_alive', lambda pid: pid == application_pid)
    monkeypatch.setattr(app_module, '_suspend_single_instance_for_handoff', suspend)
    monkeypatch.setattr(
        app_module,
        '_write_restart_handoff_marker',
        lambda _token, phase, value: events.append((phase, value)) or True,
    )
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        lambda *_args, **_kwargs: events.append('abort-confirmed') or True,
    )
    monkeypatch.setattr(app_module, '_resume_single_instance_after_handoff_failure', resume)
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        lambda _token: events.append('cleanup'),
    )

    assert not app_module._run_restart_handoff_parent(
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


def test_restart_handoff_reclaim_failure_is_uncertain_even_after_application_exit(monkeypatch):
    token = '4' * 32
    application_pid = 4546
    events = []

    def wait_marker(_token, phase, *, is_launcher_alive, expected_value=None, timeout=0):
        assert _token == token
        assert is_launcher_alive()
        if phase == 'prepared':
            return application_pid
        assert expected_value == application_pid
        return None

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(app_module, '_pid_is_alive', lambda pid: pid == application_pid)
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(app_module, '_write_restart_handoff_marker', lambda *_args: True)
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        lambda *_args, **_kwargs: events.append('abort-confirmed') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume-failed') or False,
    )
    monkeypatch.setattr(app_module, '_cleanup_restart_handoff', lambda _token: None)

    with pytest.raises(
        app_module.RestartHandoffUncertain,
        match='could not restore single-instance ownership',
    ):
        app_module._run_restart_handoff_parent(
            token,
            4545,
            is_launcher_alive=lambda: True,
            terminate_launcher=lambda: events.append('terminate-launcher'),
        )

    assert events == ['suspend', 'abort-confirmed', 'resume-failed']


def test_resume_single_instance_fails_when_control_server_cannot_be_restored(monkeypatch):
    shared_memory = SimpleNamespace(isAttached=lambda: True)
    monkeypatch.setattr(app_module, '_single_instance_shared_memory', shared_memory)
    monkeypatch.setattr(app_module, '_single_instance_app', object())
    monkeypatch.setattr(app_module, '_single_instance_tray', object())
    monkeypatch.setattr(app_module, '_single_instance_control_server', None)
    monkeypatch.setattr(app_module, '_start_single_instance_control_server', lambda *_args: None)

    assert not app_module._resume_single_instance_after_handoff_failure()
    assert app_module._single_instance_control_server is None


def test_restart_handoff_does_not_reclaim_if_application_exit_is_unconfirmed(monkeypatch):
    token = '7' * 32
    application_pid = 5152
    events = []

    def wait_marker(_token, phase, *, is_launcher_alive, expected_value=None, timeout=0):
        assert _token == token
        assert is_launcher_alive()
        if phase == 'prepared':
            return application_pid
        assert expected_value == application_pid
        return None

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(app_module, '_pid_is_alive', lambda pid: pid == application_pid)
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(app_module, '_write_restart_handoff_marker', lambda *_args: True)
    monkeypatch.setattr(
        app_module,
        '_abort_restart_child_and_wait',
        lambda *_args, **_kwargs: events.append('abort-unconfirmed') or False,
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        lambda _token, **_kwargs: None,
    )

    with pytest.raises(app_module.RestartHandoffUncertain):
        app_module._run_restart_handoff_parent(
            token,
            5151,
            is_launcher_alive=lambda: True,
            terminate_launcher=lambda: events.append('terminate-launcher'),
        )
    assert events == ['suspend', 'abort-unconfirmed']


def test_abort_does_not_treat_dead_onefile_launcher_as_dead_application(
    monkeypatch, tmp_path
):
    token = '6' * 32
    parent_pid = 6161
    application_pid = 6163
    state = {'launcher_alive': True}
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module, '_pid_is_alive', lambda pid: pid == application_pid)

    assert not app_module._abort_restart_child_and_wait(
        token,
        parent_pid,
        application_pid,
        is_launcher_alive=lambda: state['launcher_alive'],
        terminate_launcher=lambda: state.__setitem__('launcher_alive', False),
        timeout=0.02,
    )
    assert not state['launcher_alive']
    assert (tmp_path / f'.restart-abort-{token}').read_text(encoding='utf-8') == str(
        parent_pid
    )


def test_restart_handoff_success_accepts_onefile_launcher_and_application_pids(
    monkeypatch,
):
    token = '8' * 32
    launcher_pid = 4343
    application_pid = 4344
    events = []

    def wait_marker(_token, phase, *, is_launcher_alive, expected_value=None, timeout=0):
        assert _token == token
        assert is_launcher_alive()
        events.append(f'wait-{phase}')
        if phase == 'prepared':
            assert expected_value is None
            return application_pid
        assert expected_value == application_pid
        return application_pid

    monkeypatch.setattr(app_module, '_wait_for_restart_marker', wait_marker)
    monkeypatch.setattr(app_module, '_pid_is_alive', lambda pid: pid == application_pid)
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: events.append('suspend') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_write_restart_handoff_marker',
        lambda _token, phase, value: events.append((phase, value)) or True,
    )
    monkeypatch.setattr(
        app_module,
        '_resume_single_instance_after_handoff_failure',
        lambda: events.append('resume') or True,
    )
    monkeypatch.setattr(
        app_module,
        '_cleanup_restart_handoff',
        lambda _token: events.append('cleanup'),
    )

    assert app_module._run_restart_handoff_parent(
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
    monkeypatch, tmp_path
):
    token = 'b' * 32
    application_pid = 5252
    alive_checks = iter([True, False])
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    (tmp_path / f'.restart-ready-{token}').write_text(str(application_pid), encoding='utf-8')

    assert (
        app_module._wait_for_restart_marker(
            token,
            'ready',
            is_launcher_alive=lambda: next(alive_checks),
            expected_value=application_pid,
            timeout=0.1,
        )
        is None
    )


def test_verified_restart_uses_protocol_args_without_kill_others(monkeypatch, tmp_path):
    token = 'c' * 32
    launches = []
    handoffs = []

    class _Process:
        pid = 6262

        def poll(self):
            return None

    def popen(launch, **kwargs):
        launches.append((launch, kwargs))
        return _Process()

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', lambda _bytes: token)
    monkeypatch.setattr(app_module.subprocess, 'Popen', popen)
    monkeypatch.setattr(
        app_module,
        '_run_restart_handoff_parent',
        lambda handoff_token, child_pid, **_kwargs: handoffs.append(
            (handoff_token, child_pid)
        )
        or True,
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
    assert launches[0][1]['env']['PYINSTALLER_RESET_ENVIRONMENT'] == '1'
    assert app_module.os.environ['PYINSTALLER_RESET_ENVIRONMENT'] == 'stale-parent-value'
    assert handoffs == [(token, 6262)]


def test_verified_restart_keeps_current_process_when_child_dies_before_prepared(
    monkeypatch, tmp_path
):
    token = 'e' * 32

    class _Process:
        pid = 7272

        def poll(self):
            return 1

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', lambda _bytes: token)
    monkeypatch.setattr(app_module.subprocess, 'Popen', lambda *_args, **_kwargs: _Process())
    monkeypatch.setattr(app_module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_module.sys, 'platform', 'linux')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module.sys, 'executable', '/tmp/Fleasion')

    assert not app_module.restart_fleasion_normally(verify_startup=True)


def test_windows_verified_hosts_restart_invokes_uac_directly(monkeypatch, tmp_path):
    token = 'f' * 32
    relaunches = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', lambda _bytes: token)
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: 123)
    monkeypatch.setattr(app_module.os, 'getpid', lambda: 8181)
    monkeypatch.setattr(
        app_module,
        '_relaunch_as_admin',
        lambda **kwargs: relaunches.append(kwargs) or True,
    )
    monkeypatch.setattr(
        app_module.subprocess,
        'Popen',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('Windows hosts handoff must not spawn a non-elevated bootstrap')
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
    monkeypatch, tmp_path
):
    token = '2' * 32
    parent_pid = 8282
    launcher_pid = 9291
    application_pid = 9292
    events = []
    state = {'attached': True, 'launcher_alive': True, 'application_alive': True}
    child_release = threading.Event()

    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', lambda _bytes: token)
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module.os, 'getpid', lambda: parent_pid)
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: None)
    monkeypatch.setattr(
        app_module,
        '_pid_is_alive',
        lambda pid: pid == application_pid and state['application_alive'],
    )

    def suspend():
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

    def simulated_uac_relaunch(**kwargs):
        assert kwargs['restart_handoff_token'] == token
        assert kwargs['restart_handoff_parent_pid'] == parent_pid

        def elevated_child():
            events.append('elevated-prepared')
            assert app_module._write_restart_handoff_marker(token, 'prepared', application_pid)
            assert app_module._wait_for_restart_release(token, parent_pid)
            events.append('elevated-released')
            events.append('elevated-ready')
            assert app_module._write_restart_handoff_marker(token, 'ready', application_pid)
            child_release.wait(1.0)

        child = threading.Thread(target=elevated_child)
        child.start()
        try:
            return app_module._run_restart_handoff_parent(
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


def test_windows_uac_failure_keeps_parent_ownership_for_rollback(monkeypatch, tmp_path):
    token = '1' * 32
    suspended = []
    monkeypatch.setattr(app_module, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(app_module.secrets, 'token_hex', lambda _bytes: token)
    monkeypatch.setattr(app_module.sys, 'platform', 'win32')
    monkeypatch.setattr(app_module.sys, 'argv', ['Fleasion'])
    monkeypatch.setattr(app_module, '_is_admin', lambda: False)
    monkeypatch.setattr(app_module, '_visible_parent_widget', lambda: None)
    monkeypatch.setattr(app_module, '_window_handle', lambda _widget: None)
    monkeypatch.setattr(app_module, '_relaunch_as_admin', lambda **_kwargs: False)
    monkeypatch.setattr(
        app_module,
        '_suspend_single_instance_for_handoff',
        lambda: suspended.append(True) or True,
    )

    assert not app_module.restart_fleasion_normally(verify_startup=True, require_admin=True)
    assert suspended == []


def test_restart_handoff_credentials_are_stripped_before_nested_relaunch():
    assert app_module._strip_restart_handoff_args(
        [
            '--foo',
            '--restart-handoff-token',
            'a' * 32,
            '--restart-handoff-parent-pid=42',
            '--bar',
        ]
    ) == ['--foo', '--bar']


def test_env_to_hosts_live_switch_avoids_process_restart(monkeypatch):
    from fleasion.gui import settings_tab

    events = []
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

    settings_tab.SettingsTab._on_proxy_mode_changed(tab)

    assert config.proxy_mode == 'hosts'
    assert events == ['restart_proxy', 'notify']


def test_env_to_hosts_with_proxy_disabled_only_persists_mode():
    from fleasion.gui import settings_tab

    events = []
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

    settings_tab.SettingsTab._on_proxy_mode_changed(tab)

    assert config.proxy_mode == 'hosts'
    assert events == ['notify']


def test_env_to_hosts_failed_replacement_rolls_back_mode_and_autostart(monkeypatch):
    from fleasion.gui import settings_tab

    sync_modes = []
    warnings = []
    selected_indexes = []
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
        findData=lambda mode: 0 if mode == 'env' else -1,
        blockSignals=lambda _blocked: None,
        setCurrentIndex=lambda index: selected_indexes.append(index),
    )
    tab = SimpleNamespace(_config=config, _tray=tray, _proxy_mode_combo=combo)

    monkeypatch.setattr(
        settings_tab,
        'sync_autostart',
        lambda _enabled, _config_dir, *, proxy_mode: sync_modes.append(proxy_mode) or True,
    )
    monkeypatch.setattr(
        settings_tab.QMessageBox,
        'warning',
        lambda *_args: warnings.append(_args[1]),
    )

    settings_tab.SettingsTab._on_proxy_mode_changed(tab)

    assert config.proxy_mode == 'env'
    assert sync_modes == ['hosts', 'env']
    assert selected_indexes == [0]
    assert warnings == ['Proxy Mode Change Failed']


def test_env_to_hosts_uncertain_replacement_does_not_reclaim_or_rewrite_state(monkeypatch):
    from fleasion.gui import settings_tab

    sync_modes = []
    criticals = []
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

    monkeypatch.setattr(
        settings_tab,
        'sync_autostart',
        lambda _enabled, _config_dir, *, proxy_mode: sync_modes.append(proxy_mode) or True,
    )
    monkeypatch.setattr(
        settings_tab.QMessageBox,
        'critical',
        lambda *_args: criticals.append(_args[1]),
    )

    settings_tab.SettingsTab._on_proxy_mode_changed(tab)

    assert config.proxy_mode == 'hosts'
    assert sync_modes == ['hosts']
    assert criticals == ['Proxy Mode Change Incomplete']


def test_windows_hosts_to_env_live_switch_rearms_gdk_after_proxy_restart(monkeypatch):
    from fleasion.gui import settings_tab

    events = []
    proxy_master = SimpleNamespace(
        restart_for_mode_switch=lambda: events.append('restart_proxy'),
    )
    monitor = SimpleNamespace(
        env_lifecycle=SimpleNamespace(handle_player_launch=lambda _path: True),
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
        lambda _parent: SimpleNamespace(exec=lambda: events.append('dialog')),
    )
    monkeypatch.setattr(settings_tab, 'run_in_thread', lambda function: function)
    monkeypatch.setattr(
        app_module,
        '_arm_windows_gdk_env_proxy_when_ready',
        lambda proxy: events.append(('arm_gdk', proxy)) or True,
    )

    settings_tab.SettingsTab._on_proxy_mode_changed(tab)

    assert config.proxy_mode == 'env'
    assert events == [
        'dialog',
        'restart_proxy',
        ('arm_gdk', proxy_master),
        'notify',
    ]


def test_macos_uri_watcher_handoff_passes_target_to_special_lifecycle(monkeypatch, tmp_path):
    qt_app = QApplication.instance() or QApplication([])
    from fleasion.utils import platform_macos

    monkeypatch.setattr(app_module.sys, 'platform', 'darwin')
    config = SimpleNamespace(
        proxy_mode='env',
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    lifecycle_calls = []

    class _Lifecycle:
        owns_player = False
        operation_in_progress = False

        def handle_intercepted_player_launch(self, *args):
            lifecycle_calls.append(args)

    class _Interceptor:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return True

        def stop(self):
            return None

    proxy_master = SimpleNamespace(
        set_roblox_player_running=lambda _running: None,
        wait_for_env_proxy_ready=lambda timeout=0.0: True,
    )
    monkeypatch.setattr(platform_macos, 'MacOSRobloxUriInterceptor', _Interceptor)
    monitor = app_module.RobloxExitMonitor(
        config, proxy_master=proxy_master, env_lifecycle=_Lifecycle()
    )
    monitor._studio_detected.disconnect(monitor._on_studio_detected)
    target = 'roblox-player:1+launchmode:play+placeId:6484006319'
    launch = SimpleNamespace(
        pid=123,
        executable_path=tmp_path / 'Roblox.app' / 'Contents' / 'MacOS' / 'RobloxPlayer',
    )

    monitor._handle_macos_uri_interception(launch, target)

    assert lifecycle_calls == [(Path(launch.executable_path), target)]
    assert qt_app is not None


def test_linux_hosts_nix_snippet_default_includes_profile_api_host():
    snippet = _linux_hosts_nix_snippet({})

    assert '127.0.0.1 apis.roblox.com' in snippet


def test_manual_upstream_credentials_missing_only_for_empty_selected_manual_mode():
    config = type(
        'Config',
        (),
        {
            'upstream_transport_mode': 'http_connect',
            'upstream_http_connect_username': '',
            'upstream_http_connect_password': '',
            'upstream_socks5_username': '',
            'upstream_socks5_password': '',
        },
    )()

    assert _manual_upstream_credentials_missing(config) is True
    config.upstream_http_connect_username = 'proxy-user'
    assert _manual_upstream_credentials_missing(config) is False
    config.upstream_transport_mode = 'auto'
    config.upstream_http_connect_username = ''
    assert _manual_upstream_credentials_missing(config) is False


def test_macos_relay_failure_retry_action_restarts_proxy(monkeypatch):
    retries = []
    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        '_show_macos_relay_failed_dialog',
        lambda _details: 'retry',
    )

    invoker.handle_proxy_error('macos_relay_failed', {'attempts': 3})

    assert retries == [None]


def test_macos_relay_failure_reinstall_action_replaces_helper_and_retries(monkeypatch):
    retries = []
    installs = []
    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        '_show_macos_relay_failed_dialog',
        lambda _details: 'reinstall',
    )
    monkeypatch.setattr(
        macos_proxy_helper,
        'install_helper',
        lambda: installs.append(None) or (True, ''),
    )

    invoker.handle_proxy_error('macos_relay_failed', {'attempts': 3})

    assert installs == [None]
    assert retries == [None]
