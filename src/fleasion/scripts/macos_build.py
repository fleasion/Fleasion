"""Build and package the standalone macOS application."""

from __future__ import annotations

import logging
import os
import platform
import plistlib
import re
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import tarfile
import tempfile
import urllib.request
from functools import cached_property
from pathlib import Path

from fleasion.version import build_artifact_version, read_project_version

log = logging.getLogger(__name__)


# Env defaults
DEFAULT_TARGET_ARCHITECTURE = 'universal2'
DEFAULT_DEPLOYMENT_TARGET = '11.0'
DEFAULT_X86_ENVIRONMENT_PATH = '.tools/venv-x86'
ARM64_PYTHON_PLATFORM = 'aarch64-apple-darwin'
X86_64_PYTHON_PLATFORM = 'x86_64-apple-darwin'

_SLICE_BUILD_ENV = 'FLEASION_MACOS_SLICE_BUILD'
_HELPER_NAME = 'fleasion-proxy-helper'
_SUPPORTED_ARCHITECTURES = frozenset({'arm64', 'x86_64', 'universal2'})
# These optional Cryptodome accelerators are only published for Intel Macs
_ALLOWED_SINGLE_ARCH_MACHO = frozenset(
    {
        'Contents/Frameworks/Cryptodome/Hash/_ghash_clmul.abi3.so',
        'Contents/Frameworks/Cryptodome/Cipher/_raw_aesni.abi3.so',
    }
)


def subprocess_run(
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    capture_output: bool = False,
    check: bool = True,
    log_command: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a command with consistent logging and text output."""
    if log_command:
        log.info('Running %s', shlex.join(command))
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        command, check=check, capture_output=capture_output, env=environment, text=True
    )


class MacOSBuilder:
    """Orchestrate native macOS slice builds and release packaging."""

    app_path = Path('dist/Fleasion.app')
    x86_uv_directory = Path('.tools/uv-x86_64-apple-darwin')

    def __init__(self) -> None:
        self.executable_name = f'Fleasion-v{self.version}'
        self.versioned_app_path = Path(f'dist/{self.executable_name}.app')
        self.zip_path = Path(f'dist/{self.executable_name}-MacOS-Universal.zip')
        self.x86_uv_path = self.x86_uv_directory / 'uv'
        self.target_architecture = os.environ.get('MACOS_TARGET_ARCH', DEFAULT_TARGET_ARCHITECTURE)
        self.deployment_target = os.environ.get(
            'MACOSX_DEPLOYMENT_TARGET', DEFAULT_DEPLOYMENT_TARGET
        )
        self.x86_environment_path = Path(
            os.environ.get('UV_X86_PROJECT_ENVIRONMENT', DEFAULT_X86_ENVIRONMENT_PATH)
        )
        self.base_environment = os.environ.copy()
        self.base_environment.setdefault('MACOSX_DEPLOYMENT_TARGET', self.deployment_target)

    @cached_property
    def app_version(self) -> str:
        """Return the canonical application version."""
        return read_project_version()

    @cached_property
    def version(self) -> str:
        """Return the artifact filename version."""
        return build_artifact_version(self.app_version)

    @cached_property
    def x86_uv_version(self) -> str:
        """Return the version of uv running the build."""
        result = subprocess_run(['uv', '--version'], capture_output=True, log_command=False)
        output = result.stdout.strip()
        match = re.fullmatch(r'uv ([^ ]+)(?: .*)?', output)
        if match is None:
            msg = f'Could not determine the installed uv version from {output!r}.'
            raise RuntimeError(msg)
        return match.group(1)

    @property
    def x86_environment(self) -> dict[str, str]:
        """Create an environment isolated from the native arm64 venv."""
        environment = self.base_environment.copy()
        environment.update(
            {'UV_PROJECT_ENVIRONMENT': str(self.x86_environment_path), 'UV_MANAGED_PYTHON': '1'}
        )
        return environment

    def _x86_uv(self, *arguments: str, capture_output: bool = False) -> str:
        """Run the Intel uv binary through Rosetta."""
        result = subprocess_run(
            ['arch', '-x86_64', str(self.x86_uv_path), *arguments],
            environment=self.x86_environment,
            capture_output=capture_output,
        )
        return result.stdout.strip() if capture_output else ''

    @staticmethod
    def _architectures(file_path: Path) -> set[str]:
        """Return the Mach-O architectures present in a file."""
        # Lipo fails for ordinary data files, which simply have no architectures
        result = subprocess_run(
            ['lipo', '-archs', str(file_path)], capture_output=True, check=False, log_command=False
        )
        output = result.stdout if result.returncode == 0 else ''
        return set(output.split())

    def _require_architectures(self, file_path: Path, *required: str) -> None:
        architectures = self._architectures(file_path)
        missing = set(required) - architectures
        if missing:
            msg = (
                f'{file_path} is missing {", ".join(sorted(missing))}; '
                f'found {" ".join(sorted(architectures))!r}.'
            )
            raise RuntimeError(msg)

    def _require_only_architectures(self, file_path: Path, *required: str) -> None:
        architectures = self._architectures(file_path)
        expected = set(required)
        if architectures != expected:
            msg = (
                f'{file_path} contains {" ".join(sorted(architectures))!r}; '
                f'expected only {" ".join(sorted(expected))!r}.'
            )
            raise RuntimeError(msg)

    @staticmethod
    def _payload_path(app_path: Path, relative_path: str) -> Path | None:
        for directory in ('Resources', 'Frameworks'):
            payload_path = app_path / 'Contents' / directory / relative_path
            if payload_path.exists():
                return payload_path
        return None

    def _require_payload(
        self, app_path: Path, relative_path: str, build_label: str, *, executable: bool = False
    ) -> Path:
        payload_path = self._payload_path(app_path, relative_path)
        if payload_path is None or (executable and not os.access(payload_path, os.X_OK)):
            msg = f'{build_label} completed, but bundled payload was not found: {relative_path}'
            raise RuntimeError(msg)
        return payload_path

    def _verify_app_bundle(self, app_path: Path, build_label: str) -> None:
        """Validate the required app bundle structure and payloads."""
        contents_path = app_path / 'Contents'
        info_plist = contents_path / 'Info.plist'
        macos_path = contents_path / 'MacOS'
        resources_path = contents_path / 'Resources'
        frameworks_path = contents_path / 'Frameworks'
        executable_path = macos_path / self.executable_name

        required_directories = [
            app_path,
            contents_path,
            macos_path,
            resources_path,
            frameworks_path,
        ]
        missing_directories = [path for path in required_directories if not path.is_dir()]
        if missing_directories:
            msg = (
                f'{build_label} completed, but required app directory is missing: '
                f'{missing_directories[0]}'
            )
            raise RuntimeError(msg)
        if not info_plist.is_file():
            msg = f'{build_label} completed, but Info.plist is missing: {info_plist}'
            raise RuntimeError(msg)

        with info_plist.open('rb') as plist_file:
            plist = plistlib.load(plist_file)
        if plist.get('CFBundlePackageType') != 'APPL':
            msg = f'{info_plist} does not describe an APPL bundle.'
            raise RuntimeError(msg)
        if plist.get('CFBundleExecutable') != self.executable_name:
            msg = (
                f'{info_plist} has an unexpected CFBundleExecutable: '
                f'{plist.get("CFBundleExecutable")!r}.'
            )
            raise RuntimeError(msg)
        icon_file = plist.get('CFBundleIconFile')
        if not isinstance(icon_file, str) or not (resources_path / icon_file).is_file():
            msg = f'{build_label} completed, but its app icon is missing.'
            raise RuntimeError(msg)
        if not executable_path.is_file() or not os.access(executable_path, os.X_OK):
            msg = f'{build_label} completed, but its executable is missing: {executable_path}'
            raise RuntimeError(msg)

        self._require_payload(
            app_path, '_sounddevice_data/portaudio-binaries/libportaudio.dylib', build_label
        )
        soundfile_payloads = (
            '_soundfile_data/libsndfile_arm64.dylib',
            '_soundfile_data/libsndfile_x86_64.dylib',
        )
        if not any(self._payload_path(app_path, payload) for payload in soundfile_payloads):
            msg = (
                f'{build_label} completed, but bundled payload was not found: '
                '_soundfile_data/libsndfile_*.dylib'
            )
            raise RuntimeError(msg)

    @staticmethod
    def _is_allowed_single_arch_macho(app_path: Path, file_path: Path, archs: set[str]) -> bool:
        relative_path = file_path.relative_to(app_path).as_posix()
        return relative_path in _ALLOWED_SINGLE_ARCH_MACHO and archs == {'x86_64'}

    def _verify_app_architectures(self, app_path: Path) -> None:
        """Ensure a universal bundle contains both architecture slices."""
        self._require_architectures(
            app_path / 'Contents/MacOS' / self.executable_name, 'arm64', 'x86_64'
        )
        arm_helper = self._require_payload(
            app_path, f'{_HELPER_NAME}-arm64', 'Universal app', executable=True
        )
        x86_helper = self._require_payload(
            app_path, f'{_HELPER_NAME}-x86_64', 'Universal app', executable=True
        )
        self._require_only_architectures(arm_helper, 'arm64')
        self._require_only_architectures(x86_helper, 'x86_64')
        helper_names = {arm_helper.name, x86_helper.name}

        invalid_files: list[str] = []
        for file_path in self._regular_files(app_path):
            # Resources contains symlinks while the scan sees the Frameworks targets
            if file_path.name in helper_names:
                continue
            architectures = self._architectures(file_path)
            if not architectures:
                continue
            if {'arm64', 'x86_64'} <= architectures:
                continue
            if self._is_allowed_single_arch_macho(app_path, file_path, architectures):
                continue
            invalid_files.append(f'{file_path}: {" ".join(sorted(architectures))}')

        if invalid_files:
            details = '\n'.join(invalid_files[:40])
            msg = f'Universal app still contains single-architecture Mach-O files:\n{details}'
            raise RuntimeError(msg)

    @staticmethod
    def _minimum_macos_version(file_path: Path, architecture: str) -> str | None:
        """Read a Mach-O slice's minimum supported macOS version."""
        result = subprocess_run(
            ['otool', '-arch', architecture, '-l', str(file_path)],
            capture_output=True,
            check=False,
            log_command=False,
        )
        output = result.stdout if result.returncode == 0 else ''
        command: str | None = None
        for line in output.splitlines():
            value = line.strip()
            if value == 'cmd LC_BUILD_VERSION':
                command = 'build'
            elif value == 'cmd LC_VERSION_MIN_MACOSX':
                command = 'minimum'
            elif (command == 'build' and value.startswith('minos ')) or (
                command == 'minimum' and value.startswith('version ')
            ):
                return value.split(maxsplit=1)[1]
        return None

    @staticmethod
    def _version_tuple(version: str) -> tuple[int, ...]:
        parts = tuple(int(part) for part in version.split('.'))
        return parts + (0,) * (3 - len(parts))

    def _verify_macos_compatibility(self, app_path: Path) -> None:
        """Reject binaries newer than the configured deployment target."""
        maximum_version = self._version_tuple(self.deployment_target)
        incompatible: list[str] = []
        for file_path in self._regular_files(app_path):
            for architecture in self._architectures(file_path) & {'arm64', 'x86_64'}:
                minimum_version = self._minimum_macos_version(file_path, architecture)
                if minimum_version is None:
                    continue
                if self._version_tuple(minimum_version) > maximum_version:
                    incompatible.append(f'{file_path} [{architecture}]: macOS {minimum_version}')
        if incompatible:
            details = '\n'.join(incompatible[:40])
            msg = (
                f'App contains binaries requiring newer than macOS '
                f'{self.deployment_target}:\n{details}'
            )
            raise RuntimeError(msg)

    def _build_arm64(self) -> None:
        """Build the native arm64 slice with the active pinned Python."""
        # Explicit platform resolution makes uv respect MACOSX_DEPLOYMENT_TARGET
        subprocess_run(
            [
                'uv',
                'sync',
                '--locked',
                '--python-platform',
                ARM64_PYTHON_PLATFORM,
                '--group',
                'dev',
            ],
            environment=self.base_environment,
        )
        environment = self.base_environment.copy()
        environment.update({'MACOS_TARGET_ARCH': 'arm64', _SLICE_BUILD_ENV: '1'})
        # Reuse the interpreter selected by the outer `uv run build` invocation
        subprocess_run(
            [sys.executable, '-m', 'fleasion.scripts.build', '--clean'], environment=environment
        )
        self._verify_slice('arm64', 'Build')

    def _ensure_x86_uv(self) -> None:
        """Ensure Rosetta and an Intel uv executable are available."""
        rosetta = subprocess_run(
            ['arch', '-x86_64', '/usr/bin/true'],
            check=False,
            capture_output=True,
            log_command=False,
        )
        if rosetta.returncode != 0:
            msg = (
                'Rosetta is required for the Intel build. Install it with: '
                'softwareupdate --install-rosetta --agree-to-license'
            )
            raise RuntimeError(msg)
        if self.x86_uv_path.is_file() and os.access(self.x86_uv_path, os.X_OK):
            return

        url = (
            'https://github.com/astral-sh/uv/releases/download/'
            f'{self.x86_uv_version}/uv-x86_64-apple-darwin.tar.gz'
        )
        log.info('Downloading x86_64 uv %s', self.x86_uv_version)
        with tempfile.TemporaryDirectory(prefix='fleasion-uv-') as temp_dir:
            archive_path = Path(temp_dir) / 'uv-x86_64-apple-darwin.tar.gz'
            with urllib.request.urlopen(url) as response, archive_path.open('wb') as archive_file:  # ruff: ignore[suspicious-url-open-usage]
                shutil.copyfileobj(response, archive_file)
            Path('.tools').mkdir(parents=True, exist_ok=True)
            with tarfile.open(archive_path, 'r:gz') as archive:
                archive.extractall('.tools', filter='data')
        if not self.x86_uv_path.is_file():
            msg = f'uv archive did not contain {self.x86_uv_path}.'
            raise RuntimeError(msg)

    def _build_x86_64(self) -> None:
        """Build the Intel slice in an isolated Rosetta environment."""
        self._ensure_x86_uv()
        shutil.rmtree(self.x86_environment_path, ignore_errors=True)
        # Intel uv resolves the same Python pin for the configured macOS target
        self._x86_uv(
            'sync',
            '--locked',
            '--python-platform',
            X86_64_PYTHON_PLATFORM,
            '--group',
            'dev',
        )
        environment = self.x86_environment
        environment.update({'MACOS_TARGET_ARCH': 'x86_64', _SLICE_BUILD_ENV: '1'})
        subprocess_run(
            ['arch', '-x86_64', str(self.x86_uv_path), 'run', '--no-sync', 'build', '--clean'],
            environment=environment,
        )
        self._verify_slice('x86_64', 'Intel build')

    def _verify_slice(self, architecture: str, build_label: str) -> None:
        """Validate a single-architecture build and its helper."""
        self._verify_app_bundle(self.app_path, build_label)
        self._require_architectures(
            self.app_path / 'Contents/MacOS' / self.executable_name, architecture
        )
        helper = self._require_payload(
            self.app_path, f'{_HELPER_NAME}-{architecture}', build_label, executable=True
        )
        self._require_only_architectures(helper, architecture)

    @staticmethod
    def _copy_app(source: Path, destination: Path) -> None:
        shutil.rmtree(destination, ignore_errors=True)
        shutil.copytree(source, destination, symlinks=True, copy_function=shutil.copy2)

    @staticmethod
    def _regular_files(root: Path) -> list[Path]:
        return sorted(path for path in root.rglob('*') if path.is_file() and not path.is_symlink())

    def _merge_apps(self, arm_app: Path, x86_app: Path, universal_app: Path) -> None:
        """Merge matching Mach-O files from two app bundles."""
        # Start with the arm bundle so resources and metadata have one source of truth
        self._copy_app(arm_app, universal_app)
        # Merge only files that provide complementary architecture slices
        for x86_file in self._regular_files(x86_app):
            relative_path = x86_file.relative_to(x86_app)
            arm_file = universal_app / relative_path
            if not arm_file.is_file() or arm_file.is_symlink():
                continue
            x86_architectures = self._architectures(x86_file)
            arm_architectures = self._architectures(arm_file)
            if 'x86_64' not in x86_architectures or 'arm64' not in arm_architectures:
                continue
            if 'x86_64' in arm_architectures:
                continue

            temporary_file = arm_file.with_name(f'{arm_file.name}.universal-tmp')
            try:
                subprocess_run(
                    [
                        'lipo',
                        '-create',
                        str(arm_file),
                        str(x86_file),
                        '-output',
                        str(temporary_file),
                    ]
                )
                shutil.copymode(arm_file, temporary_file)
                temporary_file.replace(arm_file)
            except BaseException:
                temporary_file.unlink(missing_ok=True)
                raise

        # Preserve Intel-only files, including the architecture-specific helper
        for x86_file in self._regular_files(x86_app):
            universal_file = universal_app / x86_file.relative_to(x86_app)
            if universal_file.exists():
                continue
            universal_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(x86_file, universal_file)

        self._merge_soundfile_library(arm_app, x86_app, universal_app)

    @staticmethod
    def _merge_soundfile_library(arm_app: Path, x86_app: Path, universal_app: Path) -> None:
        """Create the universal libsndfile payload expected by soundfile."""
        relative_directory = Path('Contents/Frameworks/_soundfile_data')
        arm_library = arm_app / relative_directory / 'libsndfile_arm64.dylib'
        x86_library = x86_app / relative_directory / 'libsndfile_x86_64.dylib'
        if not arm_library.is_file() or not x86_library.is_file():
            return

        universal_directory = universal_app / relative_directory
        resource_directory = universal_app / 'Contents/Resources/_soundfile_data'
        universal_directory.mkdir(parents=True, exist_ok=True)
        resource_directory.mkdir(parents=True, exist_ok=True)
        universal_library = universal_directory / 'libsndfile_universal.dylib'
        subprocess_run(
            [
                'lipo',
                '-create',
                str(arm_library),
                str(x86_library),
                '-output',
                str(universal_library),
            ]
        )
        for architecture in ('arm64', 'x86_64'):
            library_name = f'libsndfile_{architecture}.dylib'
            shutil.copy2(universal_library, universal_directory / library_name)
            resource_library = resource_directory / library_name
            resource_library.unlink(missing_ok=True)
            resource_library.symlink_to(Path('../../Frameworks/_soundfile_data') / library_name)

    def _verify_zip_package(self) -> None:
        """Extract and validate the finished release archive."""
        with tempfile.TemporaryDirectory(prefix='fleasion-zip-check-') as temporary_directory:
            subprocess_run(['ditto', '-x', '-k', str(self.zip_path), temporary_directory])
            packaged_app = Path(temporary_directory) / self.versioned_app_path.name
            self._verify_app_bundle(packaged_app, 'Packaged zip')
            self._verify_app_architectures(packaged_app)
            self._verify_macos_compatibility(packaged_app)

    def _finalize_app(self, app_path: Path, architecture: str) -> None:
        """Sign, validate, and package a completed app bundle."""
        self._verify_app_bundle(app_path, 'Final app')
        subprocess_run(['codesign', '--force', '--deep', '--sign', '-', str(app_path)])

        if architecture == 'universal2':
            self._verify_app_architectures(app_path)
            self._verify_macos_compatibility(app_path)
            self.zip_path.unlink(missing_ok=True)
            subprocess_run(
                [
                    'ditto',
                    '-c',
                    '-k',
                    '--sequesterRsrc',
                    '--keepParent',
                    str(app_path),
                    str(self.zip_path),
                ]
            )
            self._verify_zip_package()
            log.info('Built %s (%s)', app_path, architecture)
            log.info('Built %s', self.zip_path)
            return

        self._require_architectures(
            app_path / 'Contents/MacOS' / self.executable_name, architecture
        )
        helper = self._require_payload(
            app_path, f'{_HELPER_NAME}-{architecture}', 'Final app', executable=True
        )
        self._require_only_architectures(helper, architecture)
        log.info('Built %s (%s)', app_path, architecture)

    def build(self) -> None:
        """Build the configured macOS application architecture."""
        if platform.system() != 'Darwin':
            msg = 'The macOS release builder must run on macOS.'
            raise RuntimeError(msg)
        if self.target_architecture not in _SUPPORTED_ARCHITECTURES:
            expected = ', '.join(sorted(_SUPPORTED_ARCHITECTURES))
            msg = (
                f'Unsupported MACOS_TARGET_ARCH: {self.target_architecture}. '
                f'Expected one of: {expected}.'
            )
            raise RuntimeError(msg)

        if self.target_architecture in {'arm64', 'x86_64'}:
            if self.target_architecture == 'arm64':
                self._build_arm64()
            else:
                self._build_x86_64()
            self._copy_app(self.app_path, self.versioned_app_path)
            self._finalize_app(self.versioned_app_path, self.target_architecture)
            return

        arm_app = Path('dist/Fleasion-arm64.app')
        x86_app = Path('dist/Fleasion-x86_64.app')
        universal_app = Path('dist/Fleasion-universal.app')

        self._build_arm64()
        self._copy_app(self.app_path, arm_app)
        self._build_x86_64()
        self._copy_app(self.app_path, x86_app)
        self._merge_apps(arm_app, x86_app, universal_app)
        self._copy_app(universal_app, self.versioned_app_path)
        self._finalize_app(self.versioned_app_path, 'universal2')
        self._copy_app(self.versioned_app_path, self.app_path)


def build_macos_release() -> None:
    """Build and package Fleasion for macOS."""
    MacOSBuilder().build()
