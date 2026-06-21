#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This build script must run on macOS." >&2
    exit 1
fi

APP_VERSION="$(sed -n "s/^APP_VERSION = ['\"]\\([^'\"]*\\)['\"]$/\\1/p" src/Fleasion/utils/paths.py)"
EXEC_NAME="Fleasion-v${APP_VERSION}"
APP_PATH="dist/Fleasion.app"
VERSIONED_APP_PATH="dist/${EXEC_NAME}.app"
ZIP_PATH="dist/${EXEC_NAME}-MacOS-Universal.zip"
EXEC_PATH="${APP_PATH}/Contents/MacOS/${EXEC_NAME}"
HELPER_EXEC_NAME="fleasion-proxy-helper"
HELPER_DIST_PATH="dist/${HELPER_EXEC_NAME}"
HELPER_APP_PATH="${APP_PATH}/Contents/Resources/${HELPER_EXEC_NAME}"

MACOS_TARGET_ARCH="${MACOS_TARGET_ARCH:-universal2}"
PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/fleasion-pyinstaller}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/fleasion-uv-cache}"
UV_X86_CACHE_DIR="${UV_X86_CACHE_DIR:-/tmp/fleasion-uv-cache-x86}"
UV_X86_PROJECT_ENVIRONMENT="${UV_X86_PROJECT_ENVIRONMENT:-.tools/venv-x86}"
UV_X86_PYTHON_INSTALL_DIR="${UV_X86_PYTHON_INSTALL_DIR:-.tools/pythons-x86}"
UV_X86_PYTHON_VERSION="${UV_X86_PYTHON_VERSION:-3.14}"
UV_X86_PYTHON="${UV_X86_PYTHON:-cpython-${UV_X86_PYTHON_VERSION}-macos-x86_64-none}"
UV_X86_VERSION="${UV_X86_VERSION:-$(uv --version 2>/dev/null | sed -n 's/^uv \([^ ]*\).*/\1/p')}"
UV_X86_VERSION="${UV_X86_VERSION:-0.11.18}"
UV_X86_DIR=".tools/uv-x86_64-apple-darwin"
UV_X86_BIN="${UV_X86_DIR}/uv"

export PYINSTALLER_CONFIG_DIR UV_CACHE_DIR

x86_uv() {
    UV_CACHE_DIR="$UV_X86_CACHE_DIR" \
    UV_PROJECT_ENVIRONMENT="$UV_X86_PROJECT_ENVIRONMENT" \
    UV_PYTHON_INSTALL_DIR="$UV_X86_PYTHON_INSTALL_DIR" \
    UV_MANAGED_PYTHON=1 \
    arch -x86_64 "$UV_X86_BIN" "$@"
}

find_x86_python() {
    x86_uv python install "$UV_X86_PYTHON" --install-dir "$UV_X86_PYTHON_INSTALL_DIR" >/dev/null
    x86_python_path="$(x86_uv python find "$UV_X86_PYTHON" --managed-python)"
    x86_python_arch="$(arch -x86_64 "$x86_python_path" -c 'import platform; print(platform.machine())')"
    if [ "$x86_python_arch" != "x86_64" ]; then
        echo "Expected x86_64 Python for Intel build, got $x86_python_arch from $x86_python_path." >&2
        exit 1
    fi
    printf '%s\n' "$x86_python_path"
}

require_archs() {
    file_path="$1"
    archs="$(lipo -archs "$file_path" 2>/dev/null || true)"
    for required_arch in "$@"; do
        if [ "$required_arch" = "$file_path" ]; then
            continue
        fi
        case " $archs " in
            *" $required_arch "*) ;;
            *)
                echo "$file_path is missing $required_arch slice; found '$archs'." >&2
                exit 1
                ;;
        esac
    done
}

plist_value() {
    plist_path="$1"
    key="$2"

    if [ -x /usr/libexec/PlistBuddy ]; then
        /usr/libexec/PlistBuddy -c "Print :${key}" "$plist_path" 2>/dev/null || true
        return
    fi

    sed -n "/<key>${key}<\/key>/{n;s/.*<string>\([^<]*\)<\/string>.*/\1/p;q;}" "$plist_path"
}

verify_app_bundle() {
    app_path="$1"
    build_label="$2"
    contents_path="${app_path}/Contents"
    info_plist="${contents_path}/Info.plist"
    macos_path="${contents_path}/MacOS"
    resources_path="${contents_path}/Resources"
    frameworks_path="${contents_path}/Frameworks"
    exec_path="${macos_path}/${EXEC_NAME}"
    helper_path="${resources_path}/${HELPER_EXEC_NAME}"

    if [ ! -d "$app_path" ]; then
        echo "${build_label} completed, but expected app bundle directory was not found: $app_path" >&2
        exit 1
    fi
    if [ ! -d "$contents_path" ]; then
        echo "${build_label} completed, but app bundle is missing Contents: $contents_path" >&2
        exit 1
    fi
    if [ ! -f "$info_plist" ]; then
        echo "${build_label} completed, but app bundle is missing Info.plist: $info_plist" >&2
        exit 1
    fi
    if [ ! -d "$macos_path" ]; then
        echo "${build_label} completed, but app bundle is missing MacOS directory: $macos_path" >&2
        exit 1
    fi
    if [ ! -d "$resources_path" ]; then
        echo "${build_label} completed, but app bundle is missing Resources directory: $resources_path" >&2
        exit 1
    fi
    if [ ! -d "$frameworks_path" ]; then
        echo "${build_label} completed, but app bundle is missing Frameworks directory: $frameworks_path" >&2
        exit 1
    fi

    icon_file="$(plist_value "$info_plist" CFBundleIconFile)"
    package_type="$(plist_value "$info_plist" CFBundlePackageType)"
    bundle_executable="$(plist_value "$info_plist" CFBundleExecutable)"

    if [ "$package_type" != "APPL" ]; then
        echo "${build_label} completed, but Info.plist CFBundlePackageType is '$package_type' instead of 'APPL': $info_plist" >&2
        exit 1
    fi
    if [ "$bundle_executable" != "$EXEC_NAME" ]; then
        echo "${build_label} completed, but Info.plist CFBundleExecutable is '$bundle_executable' instead of '$EXEC_NAME': $info_plist" >&2
        exit 1
    fi
    if [ -z "$icon_file" ] || [ ! -f "${resources_path}/${icon_file}" ]; then
        echo "${build_label} completed, but app bundle icon was not found in Resources: ${resources_path}/${icon_file}" >&2
        exit 1
    fi
    if [ ! -x "$exec_path" ]; then
        echo "${build_label} completed, but expected executable was not found: $exec_path" >&2
        exit 1
    fi
    if [ ! -x "$helper_path" ]; then
        echo "${build_label} completed, but bundled proxy helper was not found: $helper_path" >&2
        exit 1
    fi
}

verify_app_archs() {
    app_path="$1"
    require_archs "${app_path}/Contents/MacOS/${EXEC_NAME}" arm64 x86_64
    require_archs "${app_path}/Contents/Resources/${HELPER_EXEC_NAME}" arm64 x86_64

    missing_archs="$(mktemp /tmp/fleasion-missing-archs.XXXXXX)"
    find "$app_path" -type f -print | while IFS= read -r file_path; do
        archs="$(lipo -archs "$file_path" 2>/dev/null || true)"
        [ -n "$archs" ] || continue
        case " $archs " in *" arm64 "*) has_arm=1 ;; *) has_arm=0 ;; esac
        case " $archs " in *" x86_64 "*) has_x86=1 ;; *) has_x86=0 ;; esac
        if [ "$has_arm" -ne 1 ] || [ "$has_x86" -ne 1 ]; then
            printf '%s: %s\n' "$file_path" "$archs" >> "$missing_archs"
        fi
    done

    if [ -s "$missing_archs" ]; then
        echo "Universal app still contains single-arch Mach-O files:" >&2
        sed -n '1,40p' "$missing_archs" >&2
        rm -f "$missing_archs"
        exit 1
    fi
    rm -f "$missing_archs"
}

build_current_arch() {
    target_arch="$1"
    MACOS_TARGET_ARCH="$target_arch" uv run pyinstaller --clean --noconfirm FleasionProxyHelper.spec
    if [ ! -x "$HELPER_DIST_PATH" ]; then
        echo "Helper build completed, but expected executable was not found: $HELPER_DIST_PATH" >&2
        exit 1
    fi
    require_archs "$HELPER_DIST_PATH" "$target_arch"

    MACOS_TARGET_ARCH="$target_arch" uv run pyinstaller --clean --noconfirm Fleasion.spec

    verify_app_bundle "$APP_PATH" "Build"
    require_archs "$EXEC_PATH" "$target_arch"
    require_archs "$HELPER_APP_PATH" "$target_arch"
}

ensure_x86_uv() {
    if ! arch -x86_64 /usr/bin/true >/dev/null 2>&1; then
        echo "Rosetta is required for the Intel build. Install it with:" >&2
        echo "softwareupdate --install-rosetta --agree-to-license" >&2
        exit 1
    fi

    if [ -x "$UV_X86_BIN" ]; then
        return
    fi

    mkdir -p .tools
    archive="/tmp/uv-x86_64-apple-darwin-${UV_X86_VERSION}.tar.gz"
    url="https://github.com/astral-sh/uv/releases/download/${UV_X86_VERSION}/uv-x86_64-apple-darwin.tar.gz"
    echo "Downloading x86_64 uv ${UV_X86_VERSION}..."
    curl -L "$url" -o "$archive"
    tar -xzf "$archive" -C .tools
}

build_x86_64() {
    ensure_x86_uv

    x86_python_path="$(find_x86_python)"
    rm -rf "$UV_X86_PROJECT_ENVIRONMENT"
    x86_uv sync --locked --python "$x86_python_path" --group dev

    MACOS_TARGET_ARCH=x86_64 \
    x86_uv run --python "$x86_python_path" pyinstaller --clean --noconfirm FleasionProxyHelper.spec

    if [ ! -x "$HELPER_DIST_PATH" ]; then
        echo "Intel helper build completed, but expected executable was not found: $HELPER_DIST_PATH" >&2
        exit 1
    fi
    require_archs "$HELPER_DIST_PATH" x86_64

    MACOS_TARGET_ARCH=x86_64 \
    x86_uv run --python "$x86_python_path" pyinstaller --clean --noconfirm Fleasion.spec

    verify_app_bundle "$APP_PATH" "Intel build"
    require_archs "$EXEC_PATH" x86_64
    require_archs "$HELPER_APP_PATH" x86_64
}

copy_app() {
    src="$1"
    dst="$2"
    rm -rf "$dst"
    cp -R "$src" "$dst"
}

merge_apps() {
    arm_app="$1"
    x86_app="$2"
    universal_app="$3"

    copy_app "$arm_app" "$universal_app"

    find "$x86_app" -type f -print | while IFS= read -r x86_file; do
        rel="${x86_file#${x86_app}/}"
        arm_file="${universal_app}/${rel}"
        [ -f "$arm_file" ] || continue

        x86_archs="$(lipo -archs "$x86_file" 2>/dev/null || true)"
        arm_archs="$(lipo -archs "$arm_file" 2>/dev/null || true)"
        case " $x86_archs " in *" x86_64 "*) ;; *) continue ;; esac
        case " $arm_archs " in *" arm64 "*) ;; *) continue ;; esac
        case " $arm_archs " in *" x86_64 "*) continue ;; esac

        tmp_file="${arm_file}.universal-tmp"
        if lipo -create "$arm_file" "$x86_file" -output "$tmp_file"; then
            chmod "$(stat -f %Lp "$arm_file")" "$tmp_file"
            mv "$tmp_file" "$arm_file"
        else
            rm -f "$tmp_file"
            echo "Failed to merge $rel" >&2
            exit 1
        fi
    done

    merge_soundfile_dylib "$arm_app" "$x86_app" "$universal_app"
}

merge_soundfile_dylib() {
    arm_app="$1"
    x86_app="$2"
    universal_app="$3"
    arm_dylib="${arm_app}/Contents/Frameworks/_soundfile_data/libsndfile_arm64.dylib"
    x86_dylib="${x86_app}/Contents/Frameworks/_soundfile_data/libsndfile_x86_64.dylib"
    universal_dir="${universal_app}/Contents/Frameworks/_soundfile_data"
    resource_dir="${universal_app}/Contents/Resources/_soundfile_data"

    if [ ! -f "$arm_dylib" ] || [ ! -f "$x86_dylib" ]; then
        return
    fi

    mkdir -p "$universal_dir" "$resource_dir"
    lipo -create "$arm_dylib" "$x86_dylib" -output "${universal_dir}/libsndfile_universal.dylib"
    cp "${universal_dir}/libsndfile_universal.dylib" "${universal_dir}/libsndfile_arm64.dylib"
    cp "${universal_dir}/libsndfile_universal.dylib" "${universal_dir}/libsndfile_x86_64.dylib"
    rm -f "${resource_dir}/libsndfile_arm64.dylib" "${resource_dir}/libsndfile_x86_64.dylib"
    ln -s ../../Frameworks/_soundfile_data/libsndfile_arm64.dylib "${resource_dir}/libsndfile_arm64.dylib"
    ln -s ../../Frameworks/_soundfile_data/libsndfile_x86_64.dylib "${resource_dir}/libsndfile_x86_64.dylib"
}

verify_zip_package() {
    zip_path="$1"
    zip_check_dir="$(mktemp -d /tmp/fleasion-zip-check.XXXXXX)"

    ditto -x -k "$zip_path" "$zip_check_dir"
    verify_app_bundle "${zip_check_dir}/${EXEC_NAME}.app" "Packaged zip"
    verify_app_archs "${zip_check_dir}/${EXEC_NAME}.app"
    rm -rf "$zip_check_dir"
}

finalize_app() {
    app_path="$1"
    app_arch="$2"

    verify_app_bundle "$app_path" "Final app"
    codesign --force --deep --sign - "$app_path"

    if [ "$app_arch" = "universal2" ]; then
        verify_app_archs "$app_path"
        rm -f "$ZIP_PATH"
        ditto -c -k --sequesterRsrc --keepParent "$app_path" "$ZIP_PATH"
        verify_zip_package "$ZIP_PATH"
        echo "Built ${app_path} (${app_arch})"
        echo "Built ${ZIP_PATH}"
    else
        require_archs "${app_path}/Contents/MacOS/${EXEC_NAME}" "$app_arch"
        require_archs "${app_path}/Contents/Resources/${HELPER_EXEC_NAME}" "$app_arch"
        echo "Built ${app_path} (${app_arch})"
    fi
}

case "$MACOS_TARGET_ARCH" in
    arm64|x86_64)
        if [ "$MACOS_TARGET_ARCH" = "x86_64" ]; then
            build_x86_64
        else
            build_current_arch arm64
        fi
        copy_app "$APP_PATH" "$VERSIONED_APP_PATH"
        finalize_app "$VERSIONED_APP_PATH" "$MACOS_TARGET_ARCH"
        ;;
    universal2)
        ARM_APP="dist/Fleasion-arm64.app"
        X86_APP="dist/Fleasion-x86_64.app"
        UNIVERSAL_APP="dist/Fleasion-universal.app"

        build_current_arch arm64
        copy_app "$APP_PATH" "$ARM_APP"

        build_x86_64
        copy_app "$APP_PATH" "$X86_APP"

        merge_apps "$ARM_APP" "$X86_APP" "$UNIVERSAL_APP"
        copy_app "$UNIVERSAL_APP" "$VERSIONED_APP_PATH"
        finalize_app "$VERSIONED_APP_PATH" universal2
        copy_app "$VERSIONED_APP_PATH" "$APP_PATH"
        ;;
    *)
        echo "Unsupported MACOS_TARGET_ARCH: $MACOS_TARGET_ARCH" >&2
        echo "Expected one of: universal2, arm64, x86_64" >&2
        exit 1
        ;;
esac
