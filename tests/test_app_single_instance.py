from types import SimpleNamespace

from fleasion import app as app_module
from fleasion import __version__ as APP_VERSION
from fleasion.utils import macos_proxy_helper
from fleasion.app import (
    _RobloxUrlEventFilter,
    _handle_single_instance_command,
    _linux_hosts_nix_snippet,
    _looks_like_macos_fleasion_command,
    _manual_upstream_credentials_missing,
    _should_reclaim_stale_single_instance,
    _should_sync_autostart_on_launch,
    kill_other_fleasion_instances,
)
from PyQt6.QtCore import QCoreApplication, QEvent, QSharedMemory, QUrl


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
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    monkeypatch.setattr(app_module, "_other_fleasion_pids", lambda: [])

    assert _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_stale_single_instance_not_reclaimed_on_linux_with_gui_process(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    monkeypatch.setattr(app_module, "_other_fleasion_pids", lambda: [1234])

    assert not _should_reclaim_stale_single_instance(QSharedMemory.SharedMemoryError.AlreadyExists)


def test_kill_other_instances_prefers_graceful_exit(monkeypatch):
    calls = []

    monkeypatch.setattr(app_module, '_request_other_fleasion_instances_exit', lambda: True)
    monkeypatch.setattr(app_module, '_other_fleasion_pids', lambda: [1234])
    monkeypatch.setattr(app_module.subprocess, 'run', lambda *args, **kwargs: calls.append((args, kwargs)))

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
        config_manager = type('Config', (), {'proxy_mode': 'hosts', 'proxy_features_enabled': False})()
        proxy_master = None

    _handle_single_instance_command(_SocketStub(), _TrayStub())

    assert len(calls) == 1
    assert calls[0][1][1] == 'roblox://experiences/start?placeId=1'


def test_autostart_resync_includes_linux_normal_user(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    monkeypatch.setattr(app_module, "_is_admin", lambda: False)

    assert _should_sync_autostart_on_launch(True)
    monkeypatch.setattr(app_module, "_is_admin", lambda: True)
    assert _should_sync_autostart_on_launch(True)
    assert not _should_sync_autostart_on_launch(False)


def test_autostart_resync_runs_without_admin_on_windows(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "win32")
    monkeypatch.setattr(app_module, "_is_admin", lambda: False)

    assert _should_sync_autostart_on_launch(True)


def test_env_proxy_studio_launch_is_completely_untouched(monkeypatch):
    qt_app = QCoreApplication.instance() or QCoreApplication([])
    config = SimpleNamespace(
        proxy_mode="env",
        proxy_features_enabled=True,
        auto_delete_cache_on_exit=False,
    )
    monitor = app_module.RobloxExitMonitor(config)
    monitor._studio_detected.disconnect(monitor._on_studio_detected)
    notifications = []
    monitor._studio_detected.connect(lambda: notifications.append(True))

    monkeypatch.setattr(app_module, "is_roblox_running", lambda: False)
    monkeypatch.setattr(app_module, "is_studio_running", lambda: True)
    monkeypatch.setattr(
        app_module,
        "get_roblox_studio_exe_path",
        lambda: (_ for _ in ()).throw(AssertionError("Env mode must not inspect Studio")),
    )

    monitor._check_roblox_status_locked()

    assert notifications == []
    assert monitor._studio_was_running is True
    assert qt_app is not None

def test_linux_hosts_nix_snippet_default_includes_profile_api_host():
    snippet = _linux_hosts_nix_snippet({})

    assert "127.0.0.1 apis.roblox.com" in snippet


def test_manual_upstream_credentials_missing_only_for_empty_selected_manual_mode():
    config = type(
        "Config",
        (),
        {
            "upstream_transport_mode": "http_connect",
            "upstream_http_connect_username": "",
            "upstream_http_connect_password": "",
            "upstream_socks5_username": "",
            "upstream_socks5_password": "",
        },
    )()

    assert _manual_upstream_credentials_missing(config) is True
    config.upstream_http_connect_username = "proxy-user"
    assert _manual_upstream_credentials_missing(config) is False
    config.upstream_transport_mode = "auto"
    config.upstream_http_connect_username = ""
    assert _manual_upstream_credentials_missing(config) is False


def test_macos_relay_failure_retry_action_restarts_proxy(monkeypatch):
    retries = []
    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        "_show_macos_relay_failed_dialog",
        lambda _details: "retry",
    )

    invoker.handle_proxy_error("macos_relay_failed", {"attempts": 3})

    assert retries == [None]


def test_macos_relay_failure_reinstall_action_replaces_helper_and_retries(monkeypatch):
    retries = []
    installs = []
    invoker = app_module._ProxyErrorInvoker()
    invoker.retry_proxy.connect(lambda: retries.append(None))
    monkeypatch.setattr(
        app_module,
        "_show_macos_relay_failed_dialog",
        lambda _details: "reinstall",
    )
    monkeypatch.setattr(
        macos_proxy_helper,
        "install_helper",
        lambda: installs.append(None) or (True, ""),
    )

    invoker.handle_proxy_error("macos_relay_failed", {"attempts": 3})

    assert installs == [None]
    assert retries == [None]
