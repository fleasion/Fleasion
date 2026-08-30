from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from pytest import MonkeyPatch

from fleasion.scripts import build, macos_build


def _version_tuple(value: str) -> tuple[int, int, int]:
    callback = cast(
        'Callable[[str], tuple[int, int, int]]',
        getattr(macos_build.MacOSBuilder, '_version_tuple'),
    )
    return callback(value)


def _verify_app_architectures(builder: macos_build.MacOSBuilder, app_path: Path) -> None:
    callback = cast('Callable[[Path], None]', getattr(builder, '_verify_app_architectures'))
    callback(app_path)


def _build_arm64(builder: macos_build.MacOSBuilder) -> None:
    callback = cast('Callable[[], None]', getattr(builder, '_build_arm64'))
    callback()


def _build_x86_64(builder: macos_build.MacOSBuilder) -> None:
    callback = cast('Callable[[], None]', getattr(builder, '_build_x86_64'))
    callback()


def _slice_build_env() -> str:
    return cast(str, macos_build.__dict__['_SLICE_BUILD_ENV'])


def test_windows_packaging_uses_no_custom_python_runtime_hook() -> None:
    spec_path = Path(__file__).resolve().parents[1] / 'Fleasion.spec'
    spec_source = spec_path.read_text(encoding='utf-8')

    assert 'runtime_hooks=[]' in spec_source
    assert 'rthook_harden_dll_search' not in spec_source


def test_packaging_collects_numpy_extensions_without_upx() -> None:
    spec_path = Path(__file__).resolve().parents[1] / 'Fleasion.spec'
    spec_source = spec_path.read_text(encoding='utf-8')

    assert "_collect_package('numpy')" in spec_source
    assert "'numpy/*/*.pyd'" in spec_source
    assert "'numpy.libs/*.dll'" in spec_source


def test_windows_packaging_uses_upx_but_excludes_graphics_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    spec_source = (root / 'Fleasion.spec').read_text(encoding='utf-8')
    workflow_source = (root / '.github/workflows/build.yml').read_text(encoding='utf-8')

    assert "_use_upx = sys.platform == 'win32'" in spec_source
    assert 'Install UPX' in workflow_source
    for required_exclusion in (
        "'Qt6Gui.dll'",
        "'Qt6Widgets.dll'",
        "'Qt6OpenGL.dll'",
        "'PySide6/*.pyd'",
        "'qwindows.dll'",
        "'opengl32sw.dll'",
    ):
        assert required_exclusion in spec_source


def test_windows_archive_check_recurses_into_pyz_and_tracks_qopenglwindow() -> None:
    root = Path(__file__).resolve().parents[1]
    spec_source = (root / 'Fleasion.spec').read_text(encoding='utf-8')
    workflow_source = (root / '.github/workflows/build.yml').read_text(encoding='utf-8')

    assert 'pyi-archive_viewer -r -b -l' in workflow_source
    assert "'PySide6.QtOpenGL'," in spec_source
    assert "'PySide6.QtOpenGLWidgets'," not in spec_source


def test_packaging_collects_lz4_native_extensions() -> None:
    spec_path = Path(__file__).resolve().parents[1] / 'Fleasion.spec'
    spec_source = spec_path.read_text(encoding='utf-8')

    assert "_collect_package('lz4')" in spec_source


def _set_reproducible_environment(monkeypatch: MonkeyPatch) -> None:
    for name, value in build.REPRODUCIBLE_ENV.items():
        monkeypatch.setenv(name, value)


def test_build_dispatches_to_macos_release_builder(monkeypatch: MonkeyPatch) -> None:
    _set_reproducible_environment(monkeypatch)
    monkeypatch.setattr(build.sys, 'platform', 'darwin')
    monkeypatch.delenv(build.MACOS_SLICE_BUILD_ENV, raising=False)
    calls: list[None] = []

    def build_macos_release() -> None:
        calls.append(None)

    monkeypatch.setattr(macos_build, 'build_macos_release', build_macos_release)

    assert build.main([]) == 0
    assert calls == [None]


def test_macos_slice_build_runs_pyinstaller_without_redispatch(monkeypatch: MonkeyPatch) -> None:
    _set_reproducible_environment(monkeypatch)
    monkeypatch.setattr(build.sys, 'platform', 'darwin')
    monkeypatch.setenv(build.MACOS_SLICE_BUILD_ENV, '1')
    calls: list[tuple[list[str] | None, bool]] = []

    def run_pyinstaller(arguments: list[str] | None, *, skip_setup_logging: bool) -> None:
        calls.append((arguments, skip_setup_logging))

    monkeypatch.setattr(build, 'run_pyinstaller', run_pyinstaller)

    assert build.main(['--clean']) == 0
    assert calls == [(['--clean', '--noconfirm', 'Fleasion.spec'], True)]


def test_macos_versions_are_normalized_for_comparison() -> None:
    assert _version_tuple('11.0') == (11, 0, 0)
    assert _version_tuple('11.0.0') == (11, 0, 0)


def test_macos_prerelease_paths_use_local_artifact_version(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(macos_build, 'read_project_version', lambda: '2.4.0b1')
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)

    builder = macos_build.MacOSBuilder()

    assert builder.executable_name == 'Fleasion-v2.4.0b1+local'
    assert builder.versioned_app_path == Path('dist/Fleasion-v2.4.0b1+local.app')
    assert builder.zip_path == Path('dist/Fleasion-v2.4.0b1+local-MacOS-Universal.zip')


def test_macos_stable_paths_use_canonical_version(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(macos_build, 'read_project_version', lambda: '2.4.0')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.delenv('GITHUB_SHA', raising=False)

    builder = macos_build.MacOSBuilder()

    assert builder.executable_name == 'Fleasion-v2.4.0'
    assert builder.zip_path == Path('dist/Fleasion-v2.4.0-MacOS-Universal.zip')


def test_universal_verification_ignores_helper_symlink_targets(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.executable_name = 'Fleasion-v1.0.0'
    resources = tmp_path / 'Contents/Resources'
    frameworks = tmp_path / 'Contents/Frameworks'
    resources.mkdir(parents=True)
    frameworks.mkdir(parents=True)

    helper_paths: dict[str, Path] = {}
    framework_helpers: list[Path] = []
    for architecture in ('arm64', 'x86_64'):
        helper_name = f'fleasion-proxy-helper-{architecture}'
        framework_helper = frameworks / helper_name
        framework_helper.touch()
        resource_helper = resources / helper_name
        try:
            resource_helper.symlink_to(framework_helper)
        except OSError as exc:
            pytest.skip(f'helper symlinks require Windows developer-mode privileges: {exc}')
        helper_paths[helper_name] = resource_helper
        framework_helpers.append(framework_helper)

    def require_architectures(_file_path: Path, *_required: str) -> None:
        return None

    def require_payload(
        _app_path: Path,
        relative_path: str,
        _build_label: str,
        *,
        executable: bool = False,
    ) -> Path:
        assert executable
        return helper_paths[relative_path]

    def require_only_architectures(_file_path: Path, *_required: str) -> None:
        return None

    monkeypatch.setattr(builder, '_require_architectures', require_architectures)
    monkeypatch.setattr(builder, '_require_payload', require_payload)
    monkeypatch.setattr(builder, '_require_only_architectures', require_only_architectures)

    def regular_files(_app_path: Path) -> list[Path]:
        return framework_helpers

    monkeypatch.setattr(builder, '_regular_files', regular_files)

    _verify_app_architectures(builder, tmp_path)


def test_arm_build_resolves_for_the_deployment_platform(monkeypatch: MonkeyPatch) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.base_environment = {'MACOSX_DEPLOYMENT_TARGET': '11.0'}
    commands: list[tuple[list[str], dict[str, str] | None]] = []
    verified_slices: list[tuple[str, str]] = []

    def subprocess_run(
        command: list[str], *, environment: dict[str, str] | None = None, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append((command, environment))
        return subprocess.CompletedProcess(command, 0, '', '')

    def verify_slice(architecture: str, label: str) -> None:
        verified_slices.append((architecture, label))

    monkeypatch.setattr(builder, '_verify_slice', verify_slice)
    monkeypatch.setattr(macos_build, 'subprocess_run', subprocess_run)

    _build_arm64(builder)

    assert commands[0] == (
        [
            'uv',
            'sync',
            '--locked',
            '--python-platform',
            macos_build.ARM64_PYTHON_PLATFORM,
            '--group',
            'dev',
        ],
        builder.base_environment,
    )
    assert commands[1][0] == [
        macos_build.sys.executable,
        '-m',
        'fleasion.scripts.build',
        '--clean',
    ]
    assert commands[1][1] == {
        'MACOSX_DEPLOYMENT_TARGET': '11.0',
        'MACOS_TARGET_ARCH': 'arm64',
        _slice_build_env(): '1',
    }
    assert verified_slices == [('arm64', 'Build')]


def test_x86_build_uses_the_project_python_pin(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.x86_environment_path = tmp_path / 'venv-x86'
    builder.x86_uv_path = tmp_path / 'uv-x86_64'
    builder.base_environment = {}
    uv_calls: list[tuple[str, ...]] = []
    commands: list[list[str]] = []
    verified_slices: list[tuple[str, str]] = []

    def x86_uv(*arguments: str, capture_output: bool = False) -> str:
        assert not capture_output
        uv_calls.append(arguments)
        return ''

    def subprocess_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '', '')

    def ensure_x86_uv() -> None:
        return None

    def verify_slice(architecture: str, label: str) -> None:
        verified_slices.append((architecture, label))

    monkeypatch.setattr(builder, '_ensure_x86_uv', ensure_x86_uv)
    monkeypatch.setattr(builder, '_x86_uv', x86_uv)
    monkeypatch.setattr(builder, '_verify_slice', verify_slice)
    monkeypatch.setattr(macos_build, 'subprocess_run', subprocess_run)

    _build_x86_64(builder)

    assert uv_calls == [
        (
            'sync',
            '--locked',
            '--python-platform',
            macos_build.X86_64_PYTHON_PLATFORM,
            '--group',
            'dev',
        )
    ]
    assert commands == [
        ['arch', '-x86_64', str(builder.x86_uv_path), 'run', '--no-sync', 'build', '--clean']
    ]
    assert verified_slices == [('x86_64', 'Intel build')]
