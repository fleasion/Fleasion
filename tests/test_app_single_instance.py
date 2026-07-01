from Fleasion import app as app_module
from Fleasion.app import (
    _handle_single_instance_command,
    _looks_like_macos_fleasion_command,
    _should_reclaim_stale_single_instance,
    _should_sync_autostart_on_launch,
    kill_other_fleasion_instances,
)
from PyQt6.QtCore import QSharedMemory


def test_macos_fleasion_process_matching_accepts_real_launch_forms():
    assert _looks_like_macos_fleasion_command(
        "/Applications/Fleasion.app/Contents/MacOS/Fleasion-v2.2.1 --no-dashboard"
    )
    assert _looks_like_macos_fleasion_command("/project/.venv/bin/Fleasion")
    assert _looks_like_macos_fleasion_command("/usr/bin/python3 /project/launcher.py")
    assert _looks_like_macos_fleasion_command("/usr/bin/python3 -m Fleasion")


def test_macos_fleasion_process_matching_rejects_unrelated_commands():
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c tail '/Users/test/Library/Application Support/FleasionNT/logs/fleasion.log'"
    )
    assert not _looks_like_macos_fleasion_command(
        "/bin/zsh -c ps -axo command | rg 'Fleasion-v2.2.1|launcher.py'"
    )
    assert not _looks_like_macos_fleasion_command("/usr/bin/python3 /tmp/not-fleasion.py")


def test_fleasion_process_matching_rejects_linux_proxy_helper_commands():
    assert not _looks_like_macos_fleasion_command(
        "/opt/Fleasion/Fleasion --linux-proxy-helper --backend-port 8443"
    )
    assert not _looks_like_macos_fleasion_command(
        "/usr/bin/python3 /project/launcher.py --linux-proxy-helper --backend-port 8443"
    )
    assert not _looks_like_macos_fleasion_command(
        "/usr/bin/python3 /project/src/Fleasion/linux_proxy_helper_daemon.py --backend-port 8443"
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


def test_autostart_resync_includes_linux_normal_user(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "linux")
    monkeypatch.setattr(app_module, "_is_admin", lambda: False)

    assert _should_sync_autostart_on_launch(True)
    assert not _should_sync_autostart_on_launch(False)


def test_autostart_resync_still_requires_admin_on_windows(monkeypatch):
    monkeypatch.setattr(app_module.sys, "platform", "win32")
    monkeypatch.setattr(app_module, "_is_admin", lambda: False)

    assert not _should_sync_autostart_on_launch(True)

    monkeypatch.setattr(app_module, "_is_admin", lambda: True)
    assert _should_sync_autostart_on_launch(True)
