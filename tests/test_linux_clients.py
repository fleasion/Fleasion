import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Literal, cast

import pytest

from fleasion.utils.linux_clients import (
    LINUX_CLIENTS,
    LINUX_CLIENTS_BY_KEY,
    SOBER_CLIENT,
    MOCKTAIL_CLIENT,
    ROSEMARY_CLIENT,
    CommandRunner,
    LinuxClientDescriptor,
    LinuxClientInstallation,
    detect_installed_clients,
    get_linux_client,
    identify_resource_owner,
    query_default_roblox_handlers,
    select_linux_client,
)


def _installation(client: LinuxClientDescriptor, tmp_path: Path) -> LinuxClientInstallation:
    return LinuxClientInstallation(
        client=client,
        paths=client.paths(home=tmp_path, environ={}),
        executable=Path('/usr/bin/flatpak'),
    )


def _flatpak_which(name: str) -> str | None:
    return '/usr/bin/flatpak' if name == 'flatpak' else None


def _xdg_which(name: str) -> str | None:
    return '/usr/bin/xdg-mime' if name == 'xdg-mime' else None


def _no_which(_name: str) -> None:
    return None


def test_registry_contains_sober_mocktail_and_rosemary() -> None:
    assert LINUX_CLIENTS == (SOBER_CLIENT, MOCKTAIL_CLIENT, ROSEMARY_CLIENT)
    assert dict(LINUX_CLIENTS_BY_KEY) == {
        'sober': SOBER_CLIENT, 'mocktail': MOCKTAIL_CLIENT, 'rosemary': ROSEMARY_CLIENT
    }
    assert get_linux_client('SOBER') is SOBER_CLIENT
    assert SOBER_CLIENT.app_id == 'org.vinegarhq.Sober'
    assert SOBER_CLIENT.desktop_id == 'org.vinegarhq.Sober.desktop'


def test_sober_flatpak_paths_match_existing_layout(tmp_path: Path) -> None:
    paths = SOBER_CLIENT.paths(home=tmp_path, environ={})
    root = tmp_path / '.var' / 'app' / 'org.vinegarhq.Sober'

    assert paths.flatpak_root == root
    assert paths.config_root == root / 'config' / 'sober'
    assert paths.data_root == root / 'data' / 'sober'
    assert paths.cache_root == root / 'cache' / 'sober'
    assert paths.config_file == root / 'config' / 'sober' / 'config.json'
    assert paths.resource_roots == (
        root / 'data' / 'sober' / 'asset_overlay',
        root / 'data' / 'sober' / 'exe',
    )
    assert paths.storage_db == root / 'data' / 'sober' / 'appData' / 'rbx-storage.db'
    assert paths.cache_storage_dir == root / 'cache' / 'sober' / 'rbx-storage'


def test_paths_use_explicit_environment_home(tmp_path: Path) -> None:
    home = tmp_path / 'desktop-user'
    paths = SOBER_CLIENT.paths(environ={'HOME': str(home)})

    assert paths.home == home
    assert paths.flatpak_root == home / '.var' / 'app' / SOBER_CLIENT.app_id


def test_detect_installed_clients_uses_flatpak_info(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: Literal[True],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        command_list = list(command)
        commands.append(command_list)
        return subprocess.CompletedProcess(command_list, 0, '', '')

    installed = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=_flatpak_which,
        run=cast('CommandRunner', run),
    )

    assert [item.key for item in installed] == ['sober']
    assert commands == [['/usr/bin/flatpak', 'info', 'org.vinegarhq.Sober']]
    assert installed[0].launch_command('roblox://experiences/start?placeId=1818') == [
        '/usr/bin/flatpak',
        'run',
        'org.vinegarhq.Sober',
        'roblox://experiences/start?placeId=1818',
    ]


def test_detect_installed_clients_strips_pyinstaller_library_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle_root = tmp_path / '_MEI12345'
    host_libs = tmp_path / 'host-libs'
    calls: list[tuple[list[str], Mapping[str, str]]] = []
    monkeypatch.setattr(
        'fleasion.utils.linux_clients.sys._MEIPASS', str(bundle_root), raising=False
    )

    def run(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: Literal[True],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout
        command_list = list(command)
        calls.append((command_list, env))
        return subprocess.CompletedProcess(command_list, 0, '', '')

    installed = detect_installed_clients(
        home=tmp_path,
        environ={'LD_LIBRARY_PATH': f'{bundle_root}:{host_libs}'},
        which=_flatpak_which,
        run=cast('CommandRunner', run),
    )

    assert [item.key for item in installed] == ['sober']
    assert calls[0][1]['HOME'] == str(tmp_path)
    assert calls[0][1]['LD_LIBRARY_PATH'] == str(host_libs)


def test_detect_installed_clients_ignores_stale_flatpak_data_without_install(
    tmp_path: Path,
) -> None:
    root = tmp_path / '.var' / 'app' / SOBER_CLIENT.app_id
    root.mkdir(parents=True)

    installed = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=_no_which,
    )

    assert installed == ()


def test_detect_installed_clients_returns_empty_without_metadata(tmp_path: Path) -> None:
    installed = detect_installed_clients(
        home=tmp_path,
        environ={},
        which=_no_which,
    )

    assert installed == ()


def test_query_default_handlers_uses_scheme_order_and_explicit_home(tmp_path: Path) -> None:
    calls: list[tuple[list[str], str]] = []

    def run(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: Literal[True],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del capture_output, text, timeout
        command_list = list(command)
        calls.append((command_list, env['HOME']))
        return subprocess.CompletedProcess(
            command_list,
            0,
            'org.vinegarhq.Sober.desktop\n',
            '',
        )

    handlers = query_default_roblox_handlers(
        home=tmp_path,
        environ={},
        which=_xdg_which,
        run=cast('CommandRunner', run),
    )

    assert handlers == ('org.vinegarhq.Sober.desktop',)
    assert calls == [
        (
            [
                '/usr/bin/xdg-mime',
                'query',
                'default',
                'x-scheme-handler/roblox',
            ],
            str(tmp_path),
        ),
        (
            [
                '/usr/bin/xdg-mime',
                'query',
                'default',
                'x-scheme-handler/roblox-player',
            ],
            str(tmp_path),
        ),
    ]


def test_auto_selection_prefers_current_handler_for_future_descriptor(tmp_path: Path) -> None:
    future = replace(
        SOBER_CLIENT,
        key='future',
        display_name='Future Client',
        app_id='org.example.Future',
        desktop_ids=('org.example.Future.desktop',),
        xdg_namespace='future',
        process_names=('future-client',),
        cgroup_marker='app-flatpak-org.example.Future',
    )
    installations = (
        _installation(SOBER_CLIENT, tmp_path),
        _installation(future, tmp_path),
    )

    def run_handler(
        command: Sequence[str],
        *,
        env: Mapping[str, str],
        capture_output: bool,
        text: Literal[True],
        timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        return subprocess.CompletedProcess(
            list(command),
            0,
            'org.example.Future.desktop\n',
            '',
        )

    selected = select_linux_client(
        installed=installations,
        home=tmp_path,
        environ={},
        which=_xdg_which,
        run=cast('CommandRunner', run_handler),
    )

    assert selected is installations[1]


def test_auto_selection_has_deterministic_registry_fallback(tmp_path: Path) -> None:
    future = replace(
        SOBER_CLIENT,
        key='future',
        display_name='Future Client',
        app_id='org.example.Future',
        desktop_ids=('org.example.Future.desktop',),
    )
    sober = _installation(SOBER_CLIENT, tmp_path)

    selected = select_linux_client(
        installed=(_installation(future, tmp_path), sober),
        home=tmp_path,
        environ={},
        which=_no_which,
    )

    assert selected is sober


def test_explicit_selection_never_falls_back(tmp_path: Path) -> None:
    sober = _installation(SOBER_CLIENT, tmp_path)

    assert select_linux_client('sober', installed=(sober,)) is sober
    assert select_linux_client('sober', installed=()) is None
    with pytest.raises(ValueError, match='auto, sober'):
        select_linux_client('unknown', installed=(sober,))


def test_resource_owner_uses_exact_registered_roots(tmp_path: Path) -> None:
    installation = _installation(SOBER_CLIENT, tmp_path)
    overlay, legacy = installation.paths.resource_roots

    assert identify_resource_owner(overlay / 'content' / 'sounds', (installation,)) is installation
    assert identify_resource_owner(legacy / 'PlatformContent', (installation,)) is installation
    assert identify_resource_owner(overlay.parent / 'asset_overlay-old', (installation,)) is None
    assert (
        identify_resource_owner(tmp_path / 'unrelated' / 'asset_overlay', (installation,)) is None
    )


def _sober_proc(root: Path, pid: int, *, ticks: int, cgroup: str) -> None:
    process = root / str(pid)
    process.mkdir(parents=True, exist_ok=True)
    (process / 'comm').write_text('Main\n', encoding='utf-8')
    (process / 'cgroup').write_text(cgroup, encoding='utf-8')
    fields = ['S', *['0'] * 18, str(ticks)]
    (process / 'stat').write_text(
        f'{pid} (Main) ' + ' '.join(fields), encoding='utf-8'
    )


def test_running_sober_players_requires_flatpak_cgroup(tmp_path: Path) -> None:
    from fleasion.utils.linux_clients import SoberSession, running_sober_players

    proc = tmp_path / 'proc'
    proc.mkdir()
    _sober_proc(
        proc, 1234, ticks=500,
        cgroup='0::/user.slice/app-flatpak-org.vinegarhq.Sober-abc.scope\n',
    )
    _sober_proc(proc, 5678, ticks=900, cgroup='0::/user.slice/other.scope\n')
    assert running_sober_players(proc_root=proc) == (SoberSession(1234, 500),)


def test_auto_selection_uses_running_sober_over_rosemary_handler(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    _sober_proc(
        proc, 1234, ticks=500,
        cgroup='0::/user.slice/app-flatpak-org.vinegarhq.Sober-abc.scope\n',
    )
    sober = _installation(SOBER_CLIENT, tmp_path)
    rosemary = _installation(ROSEMARY_CLIENT, tmp_path)

    def nutmeg_handler(
        command: Sequence[str], *, env: Mapping[str, str],
        capture_output: bool, text: Literal[True], timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        return subprocess.CompletedProcess(
            list(command), 0, 'io.github.frolleks.Nutmeg.desktop\n', ''
        )

    assert select_linux_client(
        installed=(sober, rosemary), proc_root=proc, home=tmp_path,
        which=_xdg_which, run=cast('CommandRunner', nutmeg_handler),
    ) is sober
    assert select_linux_client(
        'rosemary', installed=(sober, rosemary), proc_root=proc, home=tmp_path,
    ) is rosemary


def test_auto_selection_chooses_most_recent_running_player(tmp_path: Path) -> None:
    installations = tuple(_installation(client, tmp_path) for client in LINUX_CLIENTS)
    for starts, chosen in (
        ({'rosemary': 100, 'mocktail': 200, 'sober': 300}, 'sober'),
        ({'rosemary': 300, 'mocktail': 200, 'sober': 100}, 'rosemary'),
        ({'rosemary': 100, 'mocktail': 300, 'sober': 200}, 'mocktail'),
    ):
        selected = select_linux_client(
            installed=installations, home=tmp_path, which=_no_which,
            running_client_starts=starts,
        )
        assert selected is not None and selected.key == chosen


def test_auto_selection_ignores_non_installed_running_clients(tmp_path: Path) -> None:
    sober = _installation(SOBER_CLIENT, tmp_path)
    selected = select_linux_client(
        installed=(sober,), running_client_starts={'rosemary': 900}
    )
    assert selected is sober


def test_auto_selection_tie_prefers_default_handler(tmp_path: Path) -> None:
    sober = _installation(SOBER_CLIENT, tmp_path)
    rosemary = _installation(ROSEMARY_CLIENT, tmp_path)

    def nutmeg_handler(
        command: Sequence[str], *, env: Mapping[str, str],
        capture_output: bool, text: Literal[True], timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        del env, capture_output, text, timeout
        return subprocess.CompletedProcess(
            list(command), 0, 'io.github.frolleks.Nutmeg.desktop\n', ''
        )

    assert select_linux_client(
        installed=(sober, rosemary), home=tmp_path,
        running_client_starts={'sober': 500, 'rosemary': 500},
        which=_xdg_which, run=cast('CommandRunner', nutmeg_handler),
    ) is rosemary


def test_auto_selection_reads_verified_process_generations(tmp_path: Path) -> None:
    proc = tmp_path / 'proc'
    proc.mkdir()
    rosemary_bundle = tmp_path / 'Roblox.app'
    player = rosemary_bundle / 'Contents/MacOS/RobloxPlayer'
    player.parent.mkdir(parents=True)
    player.write_bytes(b'mach-o')
    certificate = rosemary_bundle / 'Contents/Resources/ssl/cacert.pem'
    certificate.parent.mkdir(parents=True)
    certificate.write_bytes(b'cert')
    rosemary_process = proc / '1000'
    rosemary_process.mkdir()
    (rosemary_process / 'cmdline').write_bytes(
        f'/bin/rosemary\0run\0{rosemary_bundle}\0'.encode()
    )
    (rosemary_process / 'stat').write_text(
        '1000 (Main) ' + ' '.join(['S', *['0'] * 18, '700']), encoding='utf-8'
    )

    mocktail_binary = tmp_path / 'bin/mocktail'
    mocktail_binary.parent.mkdir()
    mocktail_binary.write_bytes(b'elf')
    mocktail_process = proc / '2000'
    mocktail_process.mkdir()
    (mocktail_process / 'exe').symlink_to(mocktail_binary)
    (mocktail_process / 'cmdline').write_bytes(str(mocktail_binary).encode() + b'\0')
    (mocktail_process / 'comm').write_text('Main\n', encoding='utf-8')
    (mocktail_process / 'maps').write_text(
        '1000-2000 r-xp 0 00:01 1 /mocktail/libroblox.so\n', encoding='utf-8'
    )
    (mocktail_process / 'stat').write_text(
        '2000 (Main) ' + ' '.join(['S', *['0'] * 18, '900']), encoding='utf-8'
    )

    _sober_proc(
        proc, 3000, ticks=800,
        cgroup='0::/user.slice/app-flatpak-org.vinegarhq.Sober-abc.scope\n',
    )
    installations = (
        _installation(SOBER_CLIENT, tmp_path),
        LinuxClientInstallation(
            MOCKTAIL_CLIENT, MOCKTAIL_CLIENT.paths(home=tmp_path), mocktail_binary
        ),
        LinuxClientInstallation(
            ROSEMARY_CLIENT, ROSEMARY_CLIENT.paths(home=tmp_path),
            Path('/bin/rosemary'), rosemary_bundle,
        ),
    )
    selected = select_linux_client(
        installed=installations, proc_root=proc, home=tmp_path, which=_no_which,
    )
    assert selected is installations[1]

    _sober_proc(
        proc, 3001, ticks=1000,
        cgroup='0::/user.slice/app-flatpak-org.vinegarhq.Sober-def.scope\n',
    )
    selected = select_linux_client(
        installed=installations, proc_root=proc, home=tmp_path, which=_no_which,
    )
    assert selected is installations[0]
