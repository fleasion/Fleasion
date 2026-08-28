from dataclasses import replace
from pathlib import Path

import pytest

pytest.importorskip('pwd', reason='Linux platform tests require the POSIX pwd module')

from fleasion.utils import platform_linux
from fleasion.utils.linux_clients import SOBER_CLIENT, LinuxClientInstallation
from fleasion.utils.roblox_dirs import _normalise_roblox_dir


def _flatpak_installation(
    client,
    home: Path,
    executable: str | Path = '/usr/bin/flatpak',
) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=home, environ={}),
        executable=Path(executable),
    )


def test_unavailable_explicit_client_keeps_configured_descriptor_metadata(monkeypatch):
    future = replace(
        SOBER_CLIENT,
        key='future',
        display_name='Future Client',
        app_id='org.example.Future',
        desktop_ids=('org.example.Future.desktop',),
    )
    monkeypatch.setattr(
        platform_linux,
        'LINUX_CLIENTS_BY_KEY',
        {'sober': SOBER_CLIENT, 'future': future},
    )
    monkeypatch.setattr(platform_linux, '_linux_client_preference', 'future')
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: None,
    )

    assert platform_linux.selected_linux_client_key() == 'future'
    assert platform_linux.selected_linux_client_display_name() == 'Future Client'
    assert platform_linux.selected_linux_client_app_id() == 'org.example.Future'


def test_selected_linux_client_does_not_fallback_to_stale_sober_data(tmp_path, monkeypatch):
    stale_root = tmp_path / '.var' / 'app' / SOBER_CLIENT.app_id
    stale_root.mkdir(parents=True)
    monkeypatch.setattr(platform_linux, 'USER_HOME', tmp_path)
    monkeypatch.setattr(platform_linux, '_linux_client_preference', 'auto')
    monkeypatch.setattr(platform_linux, 'linux_client_installations', lambda: ())

    assert platform_linux.get_selected_linux_client_installation() is None


def test_arch_gui_dependency_check_reports_missing_qt6_base(tmp_path, monkeypatch):
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=cachyos\nID_LIKE="arch"\n', encoding='utf-8')
    calls = []
    log_calls = []

    monkeypatch.setattr(platform_linux.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(
        platform_linux.log_buffer,
        'log',
        lambda category, message: log_calls.append((category, message)),
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return platform_linux.subprocess.CompletedProcess(command, 1, '', 'not installed')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == ['qt6-base']
    assert calls[0][0] == ['/usr/bin/pacman', '-Q', 'qt6-base']
    assert log_calls == [
        (
            'Linux GUI',
            'Arch package query reports qt6-base as unavailable '
            '(pacman exit 1). Details: not installed',
        )
    ]


def test_arch_gui_dependency_check_accepts_installed_qt6_base(tmp_path, monkeypatch):
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=arch\n', encoding='utf-8')
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/pacman')
    monkeypatch.setattr(
        platform_linux.subprocess,
        'run',
        lambda command, **_kwargs: platform_linux.subprocess.CompletedProcess(
            command, 0, 'qt6-base 6.11.1-1\n', ''
        ),
    )

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def test_arch_gui_dependency_check_uses_host_libraries_when_frozen(tmp_path, monkeypatch):
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=arch\n', encoding='utf-8')
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    calls = []

    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/pacman')
    monkeypatch.setattr(platform_linux.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', str(host_libs))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return platform_linux.subprocess.CompletedProcess(command, 0, 'qt6-base 6.11.1-1\n', '')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []
    assert calls[0][0] == ['/usr/bin/pacman', '-Q', 'qt6-base']
    assert calls[0][1]['env']['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in calls[0][1]['env']


def test_non_arch_gui_dependency_check_does_not_query_pacman(tmp_path, monkeypatch):
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=fedora\n', encoding='utf-8')
    monkeypatch.setattr(
        platform_linux.subprocess,
        'run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected pacman query')),
    )

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def _detached_kwargs_with_env(env: dict[str, str] | None = None) -> dict:
    kwargs = dict(platform_linux._DETACHED_POPEN_KWARGS)
    kwargs['env'] = env or platform_linux._host_subprocess_env()
    return kwargs


class _FakePopen:
    def __init__(self, return_code: int = 0, *, times_out: bool = False):
        self.return_code = return_code
        self.times_out = times_out
        self.wait_timeouts = []

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.times_out:
            raise platform_linux.subprocess.TimeoutExpired('desktop-opener', timeout)
        return self.return_code


def test_find_sober_resource_dirs_prefers_asset_overlay(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    data_dir = installation.paths.data_root
    overlay, legacy = installation.paths.resource_roots
    legacy.mkdir(parents=True)

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )

    assert platform_linux.find_roblox_resource_dirs() == [overlay, legacy]


def test_global_settings_discovery_uses_selected_descriptor_paths(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    installation.paths.data_root.mkdir(parents=True)
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )

    assert platform_linux.find_linux_global_settings_dirs() == [installation.paths.data_root]


def test_sober_main_process_uses_pid_and_start_time_to_identify_engine(tmp_path, monkeypatch):
    process = tmp_path / '4242'
    process.mkdir()
    (process / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-test.scope\n', encoding='utf-8'
    )
    fields = ['S', *(['0'] * 18), '54321']
    (process / 'stat').write_text(f'4242 (Main) {" ".join(fields)}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(
        platform_linux, '_process_pids', lambda name: [4242] if name == 'Main' else []
    )
    monkeypatch.setattr(platform_linux.os, 'sysconf', lambda _name: 100)

    assert platform_linux.sober_main_process() == (4242, 543.21)


def test_client_pid_detection_rejects_same_name_outside_flatpak_cgroup(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    wrong = tmp_path / '100'
    right = tmp_path / '200'
    wrong.mkdir()
    right.mkdir()
    (wrong / 'cgroup').write_text('0::/user.slice/unrelated.scope\n', encoding='utf-8')
    (right / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-test.scope\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(platform_linux, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(platform_linux, '_process_pids', lambda _name: [100, 200])

    assert platform_linux._client_pids(installation) == [200]
    assert platform_linux._first_client_pid(installation) == 200


def test_terminate_roblox_does_not_signal_after_scoped_flatpak_kill(monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp/fleasion-test-home'))
    calls = []
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux, '_client_pids', lambda _installation: [4242])
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/flatpak')
    monkeypatch.setattr(
        platform_linux.subprocess,
        'run',
        lambda command, **_kwargs: (
            calls.append(command) or platform_linux.subprocess.CompletedProcess(command, 0, '', '')
        ),
    )
    monkeypatch.setattr(
        platform_linux.os,
        'kill',
        lambda *_args: (_ for _ in ()).throw(AssertionError('unexpected signal')),
    )

    assert platform_linux.terminate_roblox()
    assert calls == [['/usr/bin/flatpak', 'kill', SOBER_CLIENT.app_id]]


def test_terminate_roblox_fallback_signals_only_validated_pids(monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp/fleasion-test-home'))
    signals = []
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux, '_client_pids', lambda _installation: [4242])
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/flatpak')
    monkeypatch.setattr(
        platform_linux.subprocess,
        'run',
        lambda command, **_kwargs: platform_linux.subprocess.CompletedProcess(command, 1, '', ''),
    )
    monkeypatch.setattr(
        platform_linux.os,
        'kill',
        lambda pid, sig: signals.append((pid, sig)),
    )

    assert platform_linux.terminate_roblox()
    assert signals == [(4242, platform_linux.signal.SIGTERM)]


def test_normalise_linux_sober_resource_dir(tmp_path, monkeypatch):
    overlay = tmp_path / 'asset_overlay'
    overlay.mkdir()

    monkeypatch.setattr('fleasion.utils.roblox_dirs.sys.platform', 'linux')
    monkeypatch.setattr(platform_linux, 'SOBER_ASSET_OVERLAY_DIR', overlay)
    monkeypatch.setattr(platform_linux, 'SOBER_LEGACY_EXE_DIR', tmp_path / 'exe')

    assert _normalise_roblox_dir(overlay) == overlay


def test_launch_as_standard_user_opens_http_url(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: '/usr/bin/xdg-open' if name == 'xdg-open' else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    assert platform_linux.launch_as_standard_user('https://www.roblox.com/login')
    assert calls == [
        (
            ['/usr/bin/xdg-open', 'https://www.roblox.com/login'],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_opens_http_url_with_gio_fallback(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: '/usr/bin/gio' if name == 'gio' else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    assert platform_linux.launch_as_standard_user('https://www.roblox.com/login')
    assert calls == [
        (
            ['/usr/bin/gio', 'open', 'https://www.roblox.com/login'],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_returns_false_when_no_desktop_opener(monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.shutil, 'which', lambda name: None)
    monkeypatch.setattr(
        platform_linux.subprocess,
        'Popen',
        lambda *args, **kwargs: calls.append(args),
    )

    assert not platform_linux.launch_as_standard_user('https://www.roblox.com/login')
    assert calls == []


def test_launch_as_standard_user_runs_sober_flatpak_for_roblox_uri(monkeypatch):
    calls = []
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp'), executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_window', lambda timeout=15.0: True)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: 'flatpak' if name == 'flatpak' else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    uri = 'roblox-player:1+launchmode:app'
    assert platform_linux.launch_as_standard_user(uri)
    assert calls == [
        (
            ['flatpak', 'run', platform_linux.SOBER_APP_ID, uri],
            _detached_kwargs_with_env(),
        )
    ]


def test_launch_as_standard_user_strips_pyinstaller_env_for_sober_uri(monkeypatch, tmp_path):
    calls = []
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path, executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_window', lambda timeout=15.0: True)
    monkeypatch.setattr(platform_linux.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: 'flatpak' if name == 'flatpak' else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    uri = 'roblox-player:1+launchmode:app'
    assert platform_linux.launch_as_standard_user(uri)

    assert calls[0][0] == ['flatpak', 'run', platform_linux.SOBER_APP_ID, uri]
    assert calls[0][1]['env']['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in calls[0][1]['env']


def test_launch_as_standard_user_does_not_restart_running_sober_for_uri(monkeypatch):
    calls = []
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp'), executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: 'flatpak' if name == 'flatpak' else None,
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: True)

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    uri = 'roblox://experiences/start?placeId=121814103864070'
    assert platform_linux.launch_as_standard_user(uri)

    assert calls == [
        (
            ['flatpak', 'run', platform_linux.SOBER_APP_ID, uri],
            _detached_kwargs_with_env(),
        ),
    ]


def test_launch_failure_log_redacts_secret_bearing_roblox_uri(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    logs = []
    secret = 'synthetic-auth-ticket-value'
    uri = f'roblox-player:1+launchmode:play+gameinfo:{secret}'
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(
        platform_linux,
        '_standard_user_popen',
        lambda _command: (_ for _ in ()).throw(OSError('launch failed')),
    )
    monkeypatch.setattr(
        platform_linux.log_buffer,
        'log',
        lambda category, message: logs.append((category, message)),
    )

    assert not platform_linux.launch_as_standard_user(uri)
    assert logs == [('Launch', 'Failed to launch Roblox URI: launch failed')]
    assert secret not in logs[0][1]


def test_recover_stale_linux_env_proxy_override_clears_persisted_owner(tmp_path, monkeypatch):
    calls = []
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/flatpak')
    monkeypatch.setattr(
        platform_linux,
        '_standard_user_run',
        lambda command, **_kwargs: (
            calls.append(command) or platform_linux.subprocess.CompletedProcess(command, 0, '', '')
        ),
    )

    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert calls == [
        [
            '/usr/bin/flatpak',
            'override',
            '--user',
            *(f'--unset-env={name}' for name in SOBER_CLIENT.proxy_environment_names),
            platform_linux.SOBER_APP_ID,
        ]
    ]
    assert not state_file.exists()


def test_recover_stale_linux_env_proxy_override_keeps_marker_on_failure(tmp_path, monkeypatch):
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/flatpak')
    monkeypatch.setattr(
        platform_linux,
        '_standard_user_run',
        lambda command, **_kwargs: platform_linux.subprocess.CompletedProcess(
            command, 1, '', 'failed'
        ),
    )

    assert not platform_linux.recover_stale_linux_client_env_proxy_override()
    assert state_file.exists()


def test_recover_stale_linux_env_proxy_override_ignores_current_process_owner(
    tmp_path, monkeypatch
):
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', 'sober')
    monkeypatch.setattr(
        platform_linux,
        '_standard_user_run',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected cleanup')),
    )

    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert state_file.exists()


def test_sober_env_proxy_override_keeps_sober_as_browser_uri_handler(tmp_path, monkeypatch):
    calls = []
    proxy_url = 'http://127.0.0.1:58443'
    state_file = tmp_path / 'proxy-owner.json'

    monkeypatch.setattr(platform_linux.shutil, 'which', lambda _name: '/usr/bin/flatpak')
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(
        platform_linux.subprocess,
        'run',
        lambda command, **_kwargs: (
            calls.append(command) or platform_linux.subprocess.CompletedProcess(command, 0, '', '')
        ),
    )

    assert platform_linux.set_sober_env_proxy_override(proxy_url)
    assert platform_linux.clear_sober_env_proxy_override()

    assert calls[0][:3] == ['/usr/bin/flatpak', 'override', '--user']
    assert f'--env=HTTPS_PROXY={proxy_url}' in calls[0]
    assert calls[0][-1] == platform_linux.SOBER_APP_ID
    assert calls[1] == [
        '/usr/bin/flatpak',
        'override',
        '--user',
        *(f'--unset-env={name}' for name in SOBER_CLIENT.proxy_environment_names),
        platform_linux.SOBER_APP_ID,
    ]
    assert not state_file.exists()


def test_open_folder_uses_detached_standard_user_launch(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        lambda name: '/usr/bin/xdg-open' if name == 'xdg-open' else None,
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    target = tmp_path / 'exports'
    platform_linux.open_folder(target)

    assert target.is_dir()
    assert calls == [
        (
            ['/usr/bin/xdg-open', str(target)],
            _detached_kwargs_with_env(),
        )
    ]


def test_open_folder_falls_back_when_first_desktop_opener_exits_nonzero(tmp_path, monkeypatch):
    calls = []
    log_calls = []
    available = {
        'xdg-open': '/usr/bin/xdg-open',
        'gio': '/usr/bin/gio',
    }

    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(platform_linux.shutil, 'which', available.get)
    monkeypatch.setattr(
        platform_linux.log_buffer,
        'log',
        lambda category, message: log_calls.append((category, message)),
    )

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return _FakePopen(return_code=3 if args[0].endswith('xdg-open') else 0)

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    target = tmp_path / 'exports'

    assert platform_linux.open_folder(target) is True
    assert [call[0] for call in calls] == [
        ['/usr/bin/xdg-open', str(target)],
        ['/usr/bin/gio', 'open', str(target)],
    ]
    assert log_calls == [
        (
            'Launch',
            'xdg-open failed to open folder (exit 3); trying another desktop opener',
        )
    ]


def test_open_folder_treats_long_running_desktop_opener_as_success(tmp_path, monkeypatch):
    calls = []
    process = _FakePopen(times_out=True)
    available = {
        'xdg-open': '/usr/bin/xdg-open',
        'gio': '/usr/bin/gio',
    }

    monkeypatch.setattr(platform_linux.os, 'geteuid', lambda: 1000)
    monkeypatch.setattr(platform_linux.shutil, 'which', available.get)

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    target = tmp_path / 'exports'

    assert platform_linux.open_folder(target) is True
    assert [call[0] for call in calls] == [['/usr/bin/xdg-open', str(target)]]
    assert process.wait_timeouts == [platform_linux._DESKTOP_OPENER_STARTUP_TIMEOUT_SEC]


def test_open_folder_returns_false_when_no_desktop_opener(tmp_path, monkeypatch):
    calls = []

    monkeypatch.setattr(platform_linux.shutil, 'which', lambda name: None)
    monkeypatch.setattr(
        platform_linux.subprocess,
        'Popen',
        lambda *args, **kwargs: calls.append(args),
    )

    target = tmp_path / 'exports'

    assert platform_linux.open_folder(target) is False
    assert target.is_dir()
    assert calls == []


def test_delete_cache_clears_texpack_slots_but_preserves_predownloaded(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    app_cache = tmp_path / 'cache'
    predownloaded = app_cache / 'predownloaded'
    texpack_slots = app_cache / 'texpack_slots'
    converted_cache = app_cache / 'converted'
    for path in (predownloaded, texpack_slots, converted_cache):
        path.mkdir(parents=True)
    (predownloaded / 'asset.bin').write_bytes(b'keep')
    (texpack_slots / '88088208586015_slot0.ktx2').write_bytes(b'delete')
    (converted_cache / 'mesh.obj').write_text('delete', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', app_cache)
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)

    platform_linux.delete_cache()

    assert predownloaded.exists()
    assert (predownloaded / 'asset.bin').exists()
    assert not texpack_slots.exists()
    assert not converted_cache.exists()


def test_delete_cache_clears_sober_appdata_and_cache_storage(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    storage_db = installation.paths.storage_db
    cache_storage = installation.paths.cache_storage_dir
    assert storage_db is not None
    assert cache_storage is not None
    storage_db.parent.mkdir(parents=True)
    storage_db.write_bytes(b'cache')
    Path(str(storage_db) + '-wal').write_bytes(b'wal')

    appdata_storage = storage_db.parent / 'rbx-storage'
    appdata_storage.mkdir()
    (appdata_storage / 'entry').write_bytes(b'cache')

    cache_storage.mkdir(parents=True)
    (cache_storage / 'entry').write_bytes(b'cache')

    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', tmp_path / 'fleasion-cache')
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: False)

    messages = platform_linux.delete_cache()

    assert 'Storage database deleted successfully' in messages
    assert 'Storage database -wal deleted successfully' in messages
    assert 'Storage folder deleted successfully' in messages
    assert 'Cache storage folder deleted successfully' in messages
    assert not storage_db.exists()
    assert not Path(str(storage_db) + '-wal').exists()
    assert not appdata_storage.exists()
    assert not cache_storage.exists()


def test_delete_cache_terminates_sober_before_cleanup(tmp_path, monkeypatch):
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    app_cache = tmp_path / 'cache'
    predownloaded = app_cache / 'predownloaded'
    converted_cache = app_cache / 'converted'
    predownloaded.mkdir(parents=True)
    converted_cache.mkdir(parents=True)
    (predownloaded / 'asset.bin').write_bytes(b'keep')
    (converted_cache / 'mesh.obj').write_text('delete', encoding='utf-8')

    storage_db = installation.paths.storage_db
    assert storage_db is not None
    storage_db.parent.mkdir(parents=True)
    storage_db.write_bytes(b'cache')
    storage_folder = storage_db.parent / 'rbx-storage'
    storage_folder.mkdir()
    (storage_folder / 'db.dat').write_bytes(b'cache')

    calls = []

    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', app_cache)
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: installation,
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', lambda: True)
    monkeypatch.setattr(
        platform_linux, 'terminate_roblox', lambda: calls.append('terminate') or True
    )
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_exit', lambda timeout=10.0: True)

    messages = platform_linux.delete_cache()

    assert calls == ['terminate']
    assert messages[:2] == [
        'Sober is running, terminating...',
        'Sober terminated successfully',
    ]
    assert not storage_db.exists()
    assert not storage_folder.exists()
    assert predownloaded.exists()
    assert (predownloaded / 'asset.bin').exists()
    assert not converted_cache.exists()


def test_delete_cache_aborts_when_explicit_selection_is_unavailable(tmp_path, monkeypatch):
    sentinel = tmp_path / 'sober-cache-sentinel'
    sentinel.write_bytes(b'keep')
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        lambda: None,
    )

    assert platform_linux.delete_cache() == [
        'Selected Linux Roblox client is not installed',
        'Cache deletion aborted',
    ]
    assert sentinel.read_bytes() == b'keep'
