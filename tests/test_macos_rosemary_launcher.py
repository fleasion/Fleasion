"""Managed macOS update ordering, opt-in and private IPC launch contracts."""

from __future__ import annotations

import plistlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from fleasion.utils import macos_rosemary_launcher as launcher


@pytest.fixture(autouse=True)
def enable_managed_launch_for_internal_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', True)


if TYPE_CHECKING:
    from pathlib import Path


def _installed_app(root: Path, version: str) -> Path:
    bundle = root / 'Roblox.app'
    binary = bundle / 'Contents' / 'MacOS' / 'RobloxPlayer'
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b'not a real executable')
    (bundle / 'Contents' / 'Info.plist').write_bytes(
        plistlib.dumps({'CFBundleShortVersionString': version})
    )
    return bundle


def test_read_installed_version_without_spawning_native_player(tmp_path: Path) -> None:
    app = _installed_app(tmp_path, '0.746.0.7461264')
    assert launcher.installed_roblox_version(app) == '0.746.0.7461264'
    assert launcher.installed_roblox_version(tmp_path / 'absent.app') is None


def test_up_to_date_player_never_launches_native_updater(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installed_app(tmp_path, '0.746.0.7461264')
    monkeypatch.setattr(launcher, 'latest_roblox_version', lambda: '0.746.0.7461264')
    monkeypatch.setattr(launcher.subprocess, 'run', lambda *_args, **_kwargs: pytest.fail('unexpected native update'))
    assert launcher.native_update_if_needed(app=app) == app


def test_mismatched_version_updates_native_bundle_before_returning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = _installed_app(tmp_path, '0.744.0.7441080')
    next_version = '0.746.0.7461264'
    monkeypatch.setattr(launcher, 'latest_roblox_version', lambda: next_version)
    opened: list[tuple[str, ...]] = []
    def start_updater(cmd: list[str], **_kw: object) -> SimpleNamespace:
        opened.append(tuple(cmd))
        (app / 'Contents' / 'Info.plist').write_bytes(
            plistlib.dumps({'CFBundleShortVersionString': next_version})
        )
        return SimpleNamespace(returncode=0, stderr=b'')
    monkeypatch.setattr(launcher.subprocess, 'run', start_updater)
    monkeypatch.setattr(launcher, '_native_installer_running', lambda: False)
    tick = iter(range(100))
    monkeypatch.setattr(launcher.time, 'monotonic', lambda: float(next(tick)))
    monkeypatch.setattr(launcher.time, 'sleep', lambda _seconds: None)
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: False)
    assert launcher.native_update_if_needed(app=app) == app
    assert opened == [('/usr/bin/open', '-a', str(app))]
    assert launcher.installed_roblox_version(app) == next_version


def test_managed_launch_refuses_missing_release_without_updating(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        launcher.macos_rosemary_installer,
        'install_pinned_runtime',
        lambda: (_ for _ in ()).throw(launcher.macos_rosemary_installer.PinnedRuntimeError('not published')),
    )
    monkeypatch.setattr(launcher, 'native_update_if_needed', lambda **_kw: pytest.fail('update must not start'))
    with pytest.raises(launcher.macos_rosemary_installer.PinnedRuntimeError, match='not published'):
        launcher.launch_managed(cast('object', object()), cast('object', object()))


def test_managed_launch_starts_proxy_before_rosemary_and_attaches_ipc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    app = _installed_app(tmp_path, '0.746.0.7461264')
    runtime = tmp_path / 'pinned' / 'rosemary'
    events: list[str] = []
    monkeypatch.setattr(launcher.macos_rosemary_installer, 'install_pinned_runtime', lambda: runtime)
    monkeypatch.setattr(launcher, 'native_update_if_needed', lambda **_kwargs: events.append('native-check') or app)
    monkeypatch.setattr(launcher, 'ensure_managed_prefix', lambda *_args, **_kw: events.append('prefix-ready'))
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'active_session', lambda: None)
    config = SimpleNamespace(macos_rosemary_enabled=False, proxy_mode='hosts', proxy_features_enabled=False)

    class Proxy:
        is_running = False
        def start(self) -> None:
            events.append('proxy-start')
            self.is_running = True
        def wait_for_env_proxy_ready(self, timeout: float) -> bool:
            assert timeout > 0
            events.append('proxy-ready')
            return True
        def roblox_env_proxy_url(self) -> str:
            return 'http://127.0.0.1:58443'

    class Session:
        running = True
        process = SimpleNamespace(pid=12345)
        channel = SimpleNamespace(regions=lambda: [(4096, 4096)])

    session = Session()

    def spawn(command: list[str], *, env: dict[str, str]) -> Session:
        events.append('rosemary-launch')
        assert command == [str(runtime), 'run', '--native', str(app)]
        assert env['ROSEMARY_OBJC'] == 'builtin'
        assert env['ROSEMARY_LIBC'] == 'builtin'
        assert env['ROSEMARY_PREFIX'] != 'off'
        assert env['ROSEMARY_PREFIX'] == str(launcher.managed_prefix_directory())
        assert env['ROSEMARY_PREFIX_HOME'] == 'host'
        assert env['ROSEMARY_BUNDLED'] == '1'
        assert env['FLEASION_MANAGED_ROSEMARY'] == '1'
        assert env['ROSEMARY_PREFIX_SETUP'] == 'download'
        assert env['ROSEMARY_VULKAN_LIBRARY'] == str(runtime.parent.parent / 'lib' / 'libMoltenVK.dylib')
        assert env['HTTPS_PROXY'] == 'http://127.0.0.1:58443'
        assert 'HTTPS_PROXY=http://127.0.0.1:58443' in env['ROSEMARY_ENVIRON']
        return session

    monkeypatch.setattr(launcher.macos_rosemary_memory, 'launch_with_private_memory', spawn)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'set_active_session', lambda _session: events.append('registered'))
    result = launcher.launch_managed(cast('object', config), cast('object', Proxy()))
    assert result is session
    assert events == ['native-check', 'prefix-ready', 'proxy-start', 'proxy-ready', 'rosemary-launch', 'registered']
    assert config.macos_rosemary_enabled
    assert config.proxy_mode == 'env'
    assert config.proxy_features_enabled


def test_managed_launch_fails_closed_when_proxy_never_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    app = _installed_app(tmp_path, '0.746.0.7461264')
    monkeypatch.setattr(launcher.macos_rosemary_installer, 'install_pinned_runtime', lambda: tmp_path / 'rosemary')
    monkeypatch.setattr(launcher, 'native_update_if_needed', lambda **_kwargs: app)
    monkeypatch.setattr(launcher, 'ensure_managed_prefix', lambda *_args, **_kw: None)
    monkeypatch.setattr(launcher.platform_macos, 'is_roblox_running', lambda: False)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'active_session', lambda: None)
    monkeypatch.setattr(launcher.macos_rosemary_memory, 'launch_with_private_memory', lambda *_args, **_kwargs: pytest.fail('should not spawn'))
    config = SimpleNamespace(macos_rosemary_enabled=False, proxy_mode='env', proxy_features_enabled=False)

    class Proxy:
        is_running = False
        def start(self) -> None:
            self.is_running = True
        def wait_for_env_proxy_ready(self, timeout: float) -> bool:
            del timeout
            return False

    with pytest.raises(launcher.ManagedLaunchError, match='did not become ready'):
        launcher.launch_managed(cast('object', config), cast('object', Proxy()))


def test_first_run_provisions_prefix_before_ipc_launch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(launcher, 'managed_prefix_directory', lambda: tmp_path / 'isolated-prefix')
    observed: list[tuple[list[str], dict[str, str], int]] = []

    def prefix_create(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed.append((command, cast('dict[str, str]', kwargs['env']), cast('int', kwargs['timeout'])))
        return SimpleNamespace(returncode=0, stderr=b'')

    monkeypatch.setattr(launcher.subprocess, 'run', prefix_create)
    launcher.ensure_managed_prefix(tmp_path / 'Rosemary/bin/rosemary')
    command, env, timeout = observed.pop()
    assert command == [str(tmp_path / 'Rosemary/bin/rosemary'), 'prefix', 'create', str(tmp_path / 'isolated-prefix')]
    assert env['ROSEMARY_PREFIX_SETUP'] == 'download'
    assert env['ROSEMARY_OBJC'] == 'builtin'
    assert env['ROSEMARY_LIBC'] == 'builtin'
    assert timeout > 25


def test_failed_prefix_provision_does_not_continue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        launcher.subprocess, 'run',
        lambda *_a, **_kw: SimpleNamespace(returncode=1, stderr=b'missing verified MiniDarwin base'),
    )
    with pytest.raises(launcher.ManagedLaunchError, match='missing verified MiniDarwin base'):
        launcher.ensure_managed_prefix(tmp_path / 'Rosemary/bin/rosemary')


def test_managed_launch_is_disabled_before_any_network_or_system_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher.macos_rosemary_gate, 'MACOS_ROSEMARY_AVAILABLE', False)
    monkeypatch.setattr(launcher.sys, 'platform', 'darwin')
    monkeypatch.setattr(
        launcher.macos_rosemary_installer,
        'install_pinned_runtime',
        lambda: pytest.fail('disabled launch attempted to download Rosemary'),
    )
    monkeypatch.setattr(
        launcher,
        'native_update_if_needed',
        lambda **_kwargs: pytest.fail('disabled launch attempted to update Roblox'),
    )
    monkeypatch.setattr(
        launcher,
        'ensure_managed_prefix',
        lambda *_args, **_kwargs: pytest.fail('disabled launch attempted to provision a prefix'),
    )
    with pytest.raises(launcher.ManagedLaunchError, match='disabled until Roblox rendering works'):
        launcher.launch_managed(cast('object', object()), cast('object', object()))
