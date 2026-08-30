from dataclasses import replace
from pathlib import Path
from typing import Never, cast

import pytest

pytest.importorskip('pwd', reason='Linux platform tests require the POSIX pwd module')

from fleasion.utils import platform_linux, roblox_dirs
from fleasion.utils.linux_clients import (
    SOBER_CLIENT,
    LinuxClientDescriptor,
    LinuxClientInstallation,
)

type _TextProcessResult = platform_linux.subprocess.CompletedProcess[str]


def _flatpak_installation(
    client: LinuxClientDescriptor,
    home: Path,
    executable: str | Path = '/usr/bin/flatpak',
) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=home, environ={}),
        executable=Path(executable),
    )


def _normalise_roblox_dir(value: str | Path) -> Path | None:
    return roblox_dirs._normalise_roblox_dir(value)  # pyright: ignore[reportPrivateUsage]


def _client_pids(installation: LinuxClientInstallation) -> list[int]:
    return platform_linux._client_pids(installation)  # pyright: ignore[reportPrivateUsage]


def _first_client_pid(installation: LinuxClientInstallation) -> int | None:
    return platform_linux._first_client_pid(installation)  # pyright: ignore[reportPrivateUsage]


def _desktop_opener_startup_timeout() -> float:
    return platform_linux._DESKTOP_OPENER_STARTUP_TIMEOUT_SEC  # pyright: ignore[reportPrivateUsage]


class _SelectedInstallation:
    def __init__(self, installation: LinuxClientInstallation) -> None:
        self.installation = installation

    def __call__(self) -> LinuxClientInstallation:
        return self.installation


def _selected_installation(installation: LinuxClientInstallation) -> _SelectedInstallation:
    return _SelectedInstallation(installation)


def _no_installation() -> None:
    return None


def _empty_installations() -> tuple[LinuxClientInstallation, ...]:
    return ()


def _standard_euid() -> int:
    return 1000


def _not_running() -> bool:
    return False


def _running() -> bool:
    return True


def _wait_for_window(_timeout: float = 15.0) -> bool:
    return True


def _wait_for_exit(_timeout: float = 10.0) -> bool:
    return True


def _flatpak_path(_name: str) -> str:
    return '/usr/bin/flatpak'


def _pacman_path(_name: str) -> str:
    return '/usr/bin/pacman'


def _no_executable(_name: str) -> None:
    return None


def _xdg_open_path(name: str) -> str | None:
    return '/usr/bin/xdg-open' if name == 'xdg-open' else None


def _gio_path(name: str) -> str | None:
    return '/usr/bin/gio' if name == 'gio' else None


def _flatpak_command_path(name: str) -> str | None:
    return 'flatpak' if name == 'flatpak' else None


def _client_pid_4242(_installation: LinuxClientInstallation) -> list[int]:
    return [4242]


def test_unavailable_explicit_client_keeps_configured_descriptor_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        _no_installation,
    )

    assert platform_linux.selected_linux_client_key() == 'future'
    assert platform_linux.selected_linux_client_display_name() == 'Future Client'
    assert platform_linux.selected_linux_client_app_id() == 'org.example.Future'


def test_selected_linux_client_does_not_fallback_to_stale_sober_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stale_root = tmp_path / '.var' / 'app' / SOBER_CLIENT.app_id
    stale_root.mkdir(parents=True)
    monkeypatch.setattr(platform_linux, 'USER_HOME', tmp_path)
    monkeypatch.setattr(platform_linux, '_linux_client_preference', 'auto')
    monkeypatch.setattr(platform_linux, 'linux_client_installations', _empty_installations)

    assert platform_linux.get_selected_linux_client_installation() is None


def test_arch_gui_dependency_check_reports_missing_qt6_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=cachyos\nID_LIKE="arch"\n', encoding='utf-8')
    calls: list[tuple[list[str], dict[str, object]]] = []
    log_calls: list[tuple[str, str]] = []

    def which(name: str) -> str:
        return f'/usr/bin/{name}'

    def log(category: str, message: str) -> None:
        log_calls.append((category, message))

    monkeypatch.setattr(platform_linux.shutil, 'which', which)
    monkeypatch.setattr(platform_linux.log_buffer, 'log', log)

    def run(command: list[str], **kwargs: object) -> _TextProcessResult:
        calls.append((command, kwargs))
        return platform_linux.subprocess.CompletedProcess(command, 1, '', 'not installed')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == ['qt6-base']
    assert calls[0][0] == ['/usr/bin/pacman', '-Q', 'qt6-base']
    assert log_calls == [
        (
            'Linux GUI',
            (
                'Arch package query reports qt6-base as unavailable '
                '(pacman exit 1). Details: not installed'
            ),
        )
    ]


def test_arch_gui_dependency_check_accepts_installed_qt6_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=arch\n', encoding='utf-8')

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        return platform_linux.subprocess.CompletedProcess(command, 0, 'qt6-base 6.11.1-1\n', '')

    monkeypatch.setattr(platform_linux.shutil, 'which', _pacman_path)
    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def test_arch_gui_dependency_check_uses_host_libraries_when_frozen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=arch\n', encoding='utf-8')
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform_linux.shutil, 'which', _pacman_path)
    monkeypatch.setattr(platform_linux.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.setenv('LD_LIBRARY_PATH_ORIG', str(host_libs))

    def run(command: list[str], **kwargs: object) -> _TextProcessResult:
        calls.append((command, kwargs))
        return platform_linux.subprocess.CompletedProcess(command, 0, 'qt6-base 6.11.1-1\n', '')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []
    assert calls[0][0] == ['/usr/bin/pacman', '-Q', 'qt6-base']
    env = cast('dict[str, str]', calls[0][1]['env'])
    assert env['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in env


def test_non_arch_gui_dependency_check_does_not_query_pacman(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    os_release = tmp_path / 'os-release'
    os_release.write_text('ID=fedora\n', encoding='utf-8')

    def unexpected_run(*_args: object, **_kwargs: object) -> Never:
        message = 'unexpected pacman query'
        error = AssertionError(message)
        raise error

    monkeypatch.setattr(platform_linux.subprocess, 'run', unexpected_run)

    assert platform_linux.missing_linux_gui_packages(os_release_path=os_release) == []


def _detached_kwargs_with_env(env: dict[str, str] | None = None) -> dict[str, object]:
    kwargs: dict[str, object] = dict(
        platform_linux._DETACHED_POPEN_KWARGS  # pyright: ignore[reportPrivateUsage]
    )
    kwargs['env'] = env or platform_linux._host_subprocess_env()  # pyright: ignore[reportPrivateUsage]
    return kwargs


class _FakePopen:
    def __init__(self, return_code: int = 0, *, times_out: bool = False) -> None:
        self.return_code: int = return_code
        self.times_out: bool = times_out
        self.wait_timeouts: list[float | None] = []

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeouts.append(timeout)
        if self.times_out:
            assert timeout is not None
            command = 'desktop-opener'
            raise platform_linux.subprocess.TimeoutExpired(command, timeout)
        return self.return_code


def test_find_sober_resource_dirs_prefers_asset_overlay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    overlay, legacy = installation.paths.resource_roots
    legacy.mkdir(parents=True)

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )

    assert platform_linux.find_roblox_resource_dirs() == [overlay, legacy]


def test_global_settings_discovery_uses_selected_descriptor_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    installation.paths.data_root.mkdir(parents=True)
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )

    assert platform_linux.find_linux_global_settings_dirs() == [installation.paths.data_root]


def test_sober_main_process_uses_pid_and_start_time_to_identify_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = tmp_path / '4242'
    process.mkdir()
    (process / 'cgroup').write_text(
        '0::/user.slice/app-flatpak-org.vinegarhq.Sober-test.scope\n', encoding='utf-8'
    )
    fields = ['S', *(['0'] * 18), '54321']
    (process / 'stat').write_text(f'4242 (Main) {" ".join(fields)}\n', encoding='utf-8')

    def process_pids(name: str) -> list[int]:
        return [4242] if name == 'Main' else []

    def sysconf(_name: str) -> int:
        return 100

    monkeypatch.setattr(platform_linux, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(platform_linux, '_process_pids', process_pids)
    monkeypatch.setattr(platform_linux.os, 'sysconf', sysconf)

    assert platform_linux.sober_main_process() == (4242, 543.21)


def test_client_pid_detection_rejects_same_name_outside_flatpak_cgroup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    def process_pids(_name: str) -> list[int]:
        return [100, 200]

    monkeypatch.setattr(platform_linux, 'PROC_ROOT', tmp_path)
    monkeypatch.setattr(platform_linux, '_process_pids', process_pids)

    assert _client_pids(installation) == [200]
    assert _first_client_pid(installation) == 200


def test_terminate_roblox_does_not_signal_after_scoped_flatpak_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp/fleasion-test-home'))
    calls: list[list[str]] = []
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux, '_client_pids', _client_pid_4242)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        calls.append(command)
        return platform_linux.subprocess.CompletedProcess(command, 0, '', '')

    def unexpected_kill(*_args: object) -> Never:
        message = 'unexpected signal'
        error = AssertionError(message)
        raise error

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)
    monkeypatch.setattr(platform_linux.os, 'kill', unexpected_kill)

    assert platform_linux.terminate_roblox()
    assert calls == [['/usr/bin/flatpak', 'kill', SOBER_CLIENT.app_id]]


def test_terminate_roblox_fallback_signals_only_validated_pids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp/fleasion-test-home'))
    signals: list[tuple[int, platform_linux.signal.Signals]] = []
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux, '_client_pids', _client_pid_4242)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        return platform_linux.subprocess.CompletedProcess(command, 1, '', '')

    def kill(pid: int, sig: platform_linux.signal.Signals) -> None:
        signals.append((pid, sig))

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)
    monkeypatch.setattr(platform_linux.os, 'kill', kill)

    assert platform_linux.terminate_roblox()
    assert signals == [(4242, platform_linux.signal.SIGTERM)]


def test_normalise_linux_sober_resource_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    overlay = tmp_path / 'asset_overlay'
    overlay.mkdir()

    monkeypatch.setattr('fleasion.utils.roblox_dirs.sys.platform', 'linux')
    monkeypatch.setattr(platform_linux, 'SOBER_ASSET_OVERLAY_DIR', overlay)
    monkeypatch.setattr(platform_linux, 'SOBER_LEGACY_EXE_DIR', tmp_path / 'exe')

    assert _normalise_roblox_dir(overlay) == overlay


def test_launch_as_standard_user_opens_http_url(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _xdg_open_path,
    )

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
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


def test_launch_as_standard_user_opens_http_url_with_gio_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _gio_path,
    )

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
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


def test_launch_as_standard_user_returns_false_when_no_desktop_opener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_popen(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(platform_linux.shutil, 'which', _no_executable)
    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    assert not platform_linux.launch_as_standard_user('https://www.roblox.com/login')
    assert calls == []


def test_launch_as_standard_user_runs_sober_flatpak_for_roblox_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp'), executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _not_running)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_window', _wait_for_window)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _flatpak_command_path,
    )

    def fake_popen(args: list[str], **kwargs: object) -> object:
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


def test_launch_as_standard_user_strips_pyinstaller_env_for_sober_uri(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path, executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _not_running)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_window', _wait_for_window)
    monkeypatch.setattr(platform_linux.sys, '_MEIPASS', str(bundle_root), raising=False)
    monkeypatch.setenv('LD_LIBRARY_PATH', f'{bundle_root}:{host_libs}')
    monkeypatch.delenv('LD_LIBRARY_PATH_ORIG', raising=False)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _flatpak_command_path,
    )

    def fake_popen(args: list[str], **kwargs: object) -> object:
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    uri = 'roblox-player:1+launchmode:app'
    assert platform_linux.launch_as_standard_user(uri)

    assert calls[0][0] == ['flatpak', 'run', platform_linux.SOBER_APP_ID, uri]
    env = cast('dict[str, str]', calls[0][1]['env'])
    assert env['LD_LIBRARY_PATH'] == str(host_libs)
    assert 'LD_LIBRARY_PATH_ORIG' not in env


def test_launch_as_standard_user_does_not_restart_running_sober_for_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    installation = _flatpak_installation(SOBER_CLIENT, Path('/tmp'), executable='flatpak')

    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _flatpak_command_path,
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _running)

    def fake_popen(args: list[str], **kwargs: object) -> object:
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


def test_launch_failure_log_redacts_secret_bearing_roblox_uri(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    installation = _flatpak_installation(SOBER_CLIENT, tmp_path)
    logs: list[tuple[str, str]] = []
    secret = 'synthetic-auth-ticket-value'
    uri = f'roblox-player:1+launchmode:play+gameinfo:{secret}'
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )

    def fail_popen(_command: list[str]) -> Never:
        message = 'launch failed'
        error = OSError(message)
        raise error

    def log(category: str, message: str) -> None:
        logs.append((category, message))

    monkeypatch.setattr(platform_linux, '_standard_user_popen', fail_popen)
    monkeypatch.setattr(platform_linux.log_buffer, 'log', log)

    assert not platform_linux.launch_as_standard_user(uri)
    assert logs == [('Launch', 'Failed to launch Roblox URI: launch failed')]
    assert secret not in logs[0][1]


def test_recover_stale_linux_env_proxy_override_clears_persisted_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        calls.append(command)
        return platform_linux.subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(platform_linux, '_standard_user_run', run)

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


def test_recover_stale_linux_env_proxy_override_keeps_marker_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)
    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        return platform_linux.subprocess.CompletedProcess(command, 1, '', 'failed')

    monkeypatch.setattr(platform_linux, '_standard_user_run', run)

    assert not platform_linux.recover_stale_linux_client_env_proxy_override()
    assert state_file.exists()


def test_recover_stale_linux_env_proxy_override_ignores_current_process_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_file = tmp_path / 'proxy-owner.json'
    state_file.write_text('{"client": "sober"}\n', encoding='utf-8')

    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', 'sober')

    def unexpected_run(*_args: object, **_kwargs: object) -> Never:
        message = 'unexpected cleanup'
        error = AssertionError(message)
        raise error

    monkeypatch.setattr(platform_linux, '_standard_user_run', unexpected_run)

    assert platform_linux.recover_stale_linux_client_env_proxy_override()
    assert state_file.exists()


def test_sober_env_proxy_override_keeps_sober_as_browser_uri_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    proxy_url = 'http://127.0.0.1:58443'
    state_file = tmp_path / 'proxy-owner.json'

    monkeypatch.setattr(platform_linux.shutil, 'which', _flatpak_path)
    monkeypatch.setattr(platform_linux, 'LINUX_PROXY_OVERRIDE_STATE', state_file)
    monkeypatch.setattr(platform_linux, '_active_linux_proxy_client_key', None)

    def run(command: list[str], **_kwargs: object) -> _TextProcessResult:
        calls.append(command)
        return platform_linux.subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(platform_linux.subprocess, 'run', run)

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


def test_open_folder_uses_detached_standard_user_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(
        platform_linux.shutil,
        'which',
        _xdg_open_path,
    )

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
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


def test_open_folder_falls_back_when_first_desktop_opener_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    log_calls: list[tuple[str, str]] = []
    available = {
        'xdg-open': '/usr/bin/xdg-open',
        'gio': '/usr/bin/gio',
    }

    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(platform_linux.shutil, 'which', available.get)

    def log(category: str, message: str) -> None:
        log_calls.append((category, message))

    monkeypatch.setattr(platform_linux.log_buffer, 'log', log)

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
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


def test_open_folder_treats_long_running_desktop_opener_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    process = _FakePopen(times_out=True)
    available = {
        'xdg-open': '/usr/bin/xdg-open',
        'gio': '/usr/bin/gio',
    }

    monkeypatch.setattr(platform_linux.os, 'geteuid', _standard_euid)
    monkeypatch.setattr(platform_linux.shutil, 'which', available.get)

    def fake_popen(args: list[str], **kwargs: object) -> _FakePopen:
        calls.append((args, kwargs))
        return process

    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    target = tmp_path / 'exports'

    assert platform_linux.open_folder(target) is True
    assert [call[0] for call in calls] == [['/usr/bin/xdg-open', str(target)]]
    assert process.wait_timeouts == [_desktop_opener_startup_timeout()]


def test_open_folder_returns_false_when_no_desktop_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []

    def fake_popen(*args: object, **_kwargs: object) -> None:
        calls.append(args)

    monkeypatch.setattr(platform_linux.shutil, 'which', _no_executable)
    monkeypatch.setattr(platform_linux.subprocess, 'Popen', fake_popen)

    target = tmp_path / 'exports'

    assert platform_linux.open_folder(target) is False
    assert target.is_dir()
    assert calls == []


def test_delete_cache_clears_texpack_slots_but_preserves_predownloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _not_running)

    platform_linux.delete_cache()

    assert predownloaded.exists()
    assert (predownloaded / 'asset.bin').exists()
    assert not texpack_slots.exists()
    assert not converted_cache.exists()


def test_delete_cache_clears_sober_appdata_and_cache_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _not_running)

    messages = platform_linux.delete_cache()

    assert 'Storage database deleted successfully' in messages
    assert 'Storage database -wal deleted successfully' in messages
    assert 'Storage folder deleted successfully' in messages
    assert 'Cache storage folder deleted successfully' in messages
    assert not storage_db.exists()
    assert not Path(str(storage_db) + '-wal').exists()
    assert not appdata_storage.exists()
    assert not cache_storage.exists()


def test_delete_cache_terminates_sober_before_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    calls: list[str] = []

    monkeypatch.setattr(platform_linux, 'APP_CACHE_DIR', app_cache)
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _selected_installation(installation),
    )
    monkeypatch.setattr(platform_linux, 'is_roblox_running', _running)

    def terminate() -> bool:
        calls.append('terminate')
        return True

    monkeypatch.setattr(platform_linux, 'terminate_roblox', terminate)
    monkeypatch.setattr(platform_linux, 'wait_for_roblox_exit', _wait_for_exit)

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


def test_delete_cache_aborts_when_explicit_selection_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = tmp_path / 'sober-cache-sentinel'
    sentinel.write_bytes(b'keep')
    monkeypatch.setattr(
        platform_linux,
        'get_selected_linux_client_installation',
        _no_installation,
    )

    assert platform_linux.delete_cache() == [
        'Selected Linux Roblox client is not installed',
        'Cache deletion aborted',
    ]
    assert sentinel.read_bytes() == b'keep'
