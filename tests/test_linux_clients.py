import subprocess
from dataclasses import replace
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, cast

import pytest

from fleasion.utils.linux_clients import (
    LINUX_CLIENTS,
    LINUX_CLIENTS_BY_KEY,
    SOBER_CLIENT,
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


def test_registry_contains_only_sober() -> None:
    assert LINUX_CLIENTS == (SOBER_CLIENT,)
    assert dict(LINUX_CLIENTS_BY_KEY) == {'sober': SOBER_CLIENT}
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
        run=cast(CommandRunner, run),
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
        run=cast(CommandRunner, run),
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
        run=cast(CommandRunner, run),
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
        run=cast(CommandRunner, run_handler),
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
