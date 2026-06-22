import re
import stat
import subprocess
from pathlib import Path


EXEC_NAME = 'Fleasion-v2.1.0'
HELPER_EXEC_NAME = 'fleasion-proxy-helper'


def _build_script_functions() -> str:
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')
    functions = []
    for function_name in (
        'plist_value',
        'app_file_payload_path',
        'require_app_file_payload',
        'verify_app_bundle',
    ):
        match = re.search(
            rf'^{function_name}\(\) \{{\n.*?^}}\n',
            script,
            flags=re.MULTILINE | re.DOTALL,
        )
        assert match is not None, f'{function_name} function not found'
        functions.append(match.group(0))
    return '\n'.join(functions)


def _run_verify_app_bundle(tmp_path: Path, app_path: Path) -> subprocess.CompletedProcess[str]:
    runner = tmp_path / 'verify_app_bundle.sh'
    runner.write_text(
        '\n'.join(
            [
                '#!/bin/sh',
                'set -eu',
                f"EXEC_NAME='{EXEC_NAME}'",
                f"HELPER_EXEC_NAME='{HELPER_EXEC_NAME}'",
                _build_script_functions(),
                'verify_app_bundle "$1" "Intel build"',
            ]
        ),
        encoding='utf-8',
    )
    return subprocess.run(
        ['/bin/sh', str(runner), str(app_path)],
        check=False,
        capture_output=True,
        text=True,
    )


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_info_plist(
    app_path: Path,
    *,
    executable: str = EXEC_NAME,
    package_type: str = 'APPL',
    icon_file: str = 'fleasionlogoHR.icns',
) -> None:
    (app_path / 'Contents' / 'Info.plist').write_text(
        f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
\t<key>CFBundleExecutable</key>
\t<string>{executable}</string>
\t<key>CFBundleIconFile</key>
\t<string>{icon_file}</string>
\t<key>CFBundlePackageType</key>
\t<string>{package_type}</string>
</dict>
</plist>
''',
        encoding='utf-8',
    )


def _write_valid_app_bundle(tmp_path: Path) -> Path:
    app_path = tmp_path / 'Fleasion.app'
    contents = app_path / 'Contents'
    macos_dir = contents / 'MacOS'
    resources_dir = contents / 'Resources'
    frameworks_dir = contents / 'Frameworks'
    for directory in (macos_dir, resources_dir, frameworks_dir):
        directory.mkdir(parents=True)

    _write_info_plist(app_path)
    (resources_dir / 'fleasionlogoHR.icns').write_bytes(b'icon')
    sounddevice_dir = frameworks_dir / '_sounddevice_data' / 'portaudio-binaries'
    soundfile_dir = frameworks_dir / '_soundfile_data'
    sounddevice_dir.mkdir(parents=True)
    soundfile_dir.mkdir()
    (sounddevice_dir / 'libportaudio.dylib').write_bytes(b'portaudio')
    (soundfile_dir / 'libsndfile_arm64.dylib').write_bytes(b'sndfile-arm64')
    (soundfile_dir / 'libsndfile_x86_64.dylib').write_bytes(b'sndfile-x86_64')
    executable = macos_dir / EXEC_NAME
    helper = resources_dir / HELPER_EXEC_NAME
    executable.write_bytes(b'#!/bin/sh\n')
    helper.write_bytes(b'#!/bin/sh\n')
    _make_executable(executable)
    _make_executable(helper)
    return app_path


def test_macos_build_script_validates_intel_and_final_bundles():
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')

    assert 'verify_app_bundle "$APP_PATH" "Intel build"' in script
    assert 'verify_app_bundle "$app_path" "Final app"' in script


def test_macos_build_script_creates_permission_preserving_zip():
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')

    assert 'ZIP_PATH="dist/${EXEC_NAME}-MacOS-Universal.zip"' in script
    assert 'ditto -c -k --sequesterRsrc --keepParent "$app_path" "$ZIP_PATH"' in script
    assert 'verify_zip_package "$ZIP_PATH"' in script
    assert 'verify_app_bundle "${zip_check_dir}/${EXEC_NAME}.app" "Packaged zip"' in script
    assert 'verify_app_archs "${zip_check_dir}/${EXEC_NAME}.app"' in script
    assert 'MacOS-Universal.dmg' not in script
    assert 'hdiutil create' not in script


def test_macos_build_targets_catalina_compatible_qt_runtime():
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')
    project = Path('pyproject.toml').read_text(encoding='utf-8')

    assert 'MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-10.15}"' in script
    assert 'UV_MACOS_PYTHON_VERSION="${UV_MACOS_PYTHON_VERSION:-3.12}"' in script
    assert 'UV_X86_PYTHON_VERSION="${UV_X86_PYTHON_VERSION:-3.12}"' in script
    assert 'UniformTypeIdentifiers.framework' in script
    assert 'LC_BUILD_VERSION' in script
    assert 'LC_VERSION_MIN_MACOSX' in script
    assert 'case " $archs " in *" x86_64 "*) ;; *) continue ;; esac' in script
    assert 'otool -arch x86_64 -L "$file_path"' in script
    assert 'otool -arch x86_64 -l "$file_path"' in script
    assert 'App contains binaries requiring newer than macOS ${MACOSX_DEPLOYMENT_TARGET}' in script
    assert 'verify_macos_compatibility "$app_path"' in script
    assert '\'pyqt6==6.2.3; platform_system == "Darwin"\'' in project
    assert '\'pyqt6-qt6==6.2.4; platform_system == "Darwin"\'' in project
    assert '\'pyqt6>=6.8.0; platform_system != "Darwin"\'' in project
    assert '\'numpy==1.26.4; platform_system == "Darwin" and python_version < "3.13"\'' in project
    assert '\'numpy>=2.0.0; platform_system != "Darwin" or python_version >= "3.13"\'' in project


def test_macos_build_bundles_arch_specific_proxy_helpers():
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')
    spec = Path('Fleasion.spec').read_text(encoding='utf-8')

    assert 'HELPER_ARM64_EXEC_NAME="${HELPER_EXEC_NAME}-arm64"' in script
    assert 'HELPER_X86_EXEC_NAME="${HELPER_EXEC_NAME}-x86_64"' in script
    assert 'save_arch_helper "$target_arch"' in script
    assert 'save_arch_helper x86_64' in script
    assert 'require_only_archs "$arm_helper_path" arm64' in script
    assert 'require_only_archs "$x86_helper_path" x86_64' in script
    assert 'contains unexpected $found_arch slice; expected only' in script
    assert 'single_arch_macho_allowed "$file_path" "$archs"' in script
    assert 'Cryptodome/Hash/_ghash_clmul.abi3.so:x86_64' in script
    assert 'Cryptodome/Cipher/_raw_aesni.abi3.so:x86_64' in script
    assert 'cp -p "$x86_file" "$universal_file"' in script
    assert "pathlib.Path('dist/fleasion-proxy-helper-arm64')" in spec
    assert "pathlib.Path('dist/fleasion-proxy-helper-x86_64')" in spec


def test_macos_build_bundles_audio_runtime_libraries():
    script = Path('scripts/build_macos.sh').read_text(encoding='utf-8')
    spec = Path('Fleasion.spec').read_text(encoding='utf-8')

    assert "('_sounddevice_data', '_soundfile_data')" in spec
    assert "collect_all(audio_runtime_package)" in spec
    assert "startswith('libportaudio.so')" in spec
    assert 'the GUI player uses host PortAudio' in spec
    assert '_sounddevice_data/portaudio-binaries/libportaudio.dylib' in script
    assert '_soundfile_data/libsndfile_arm64.dylib' in script
    assert '_soundfile_data/libsndfile_x86_64.dylib' in script


def test_github_workflow_uploads_macos_zip_without_artifact_rezipping():
    workflow = Path('.github/workflows/build.yml').read_text(encoding='utf-8')

    assert 'artifact_path: dist/Fleasion-v*-MacOS-Universal.zip' in workflow
    assert 'uses: actions/upload-artifact@v7' in workflow
    assert 'archive: false' in workflow
    assert 'artifact_path: dist/Fleasion-v*-MacOS-Universal.dmg' not in workflow


def test_verify_app_bundle_accepts_complete_bundle(tmp_path):
    app_path = _write_valid_app_bundle(tmp_path)

    result = _run_verify_app_bundle(tmp_path, app_path)

    assert result.returncode == 0
    assert result.stderr == ''


def test_verify_app_bundle_rejects_executable_without_app_metadata(tmp_path):
    app_path = tmp_path / 'Fleasion.app'
    macos_dir = app_path / 'Contents' / 'MacOS'
    macos_dir.mkdir(parents=True)
    executable = macos_dir / EXEC_NAME
    executable.write_bytes(b'#!/bin/sh\n')
    _make_executable(executable)

    result = _run_verify_app_bundle(tmp_path, app_path)

    assert result.returncode != 0
    assert 'app bundle is missing Info.plist' in result.stderr


def test_verify_app_bundle_rejects_non_application_plist(tmp_path):
    app_path = _write_valid_app_bundle(tmp_path)
    _write_info_plist(app_path, package_type='BNDL')

    result = _run_verify_app_bundle(tmp_path, app_path)

    assert result.returncode != 0
    assert "CFBundlePackageType is 'BNDL' instead of 'APPL'" in result.stderr


def test_verify_app_bundle_rejects_plist_executable_mismatch(tmp_path):
    app_path = _write_valid_app_bundle(tmp_path)
    _write_info_plist(app_path, executable='Fleasion')

    result = _run_verify_app_bundle(tmp_path, app_path)

    assert result.returncode != 0
    assert "CFBundleExecutable is 'Fleasion' instead of 'Fleasion-v2.1.0'" in result.stderr
