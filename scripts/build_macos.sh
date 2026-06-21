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
HELPER_ARM64_EXEC_NAME="${HELPER_EXEC_NAME}-arm64"
HELPER_X86_EXEC_NAME="${HELPER_EXEC_NAME}-x86_64"
HELPER_DIST_PATH="dist/${HELPER_EXEC_NAME}"
HELPER_ARM64_DIST_PATH="dist/${HELPER_ARM64_EXEC_NAME}"
HELPER_X86_DIST_PATH="dist/${HELPER_X86_EXEC_NAME}"

MACOS_TARGET_ARCH="${MACOS_TARGET_ARCH:-universal2}"
MACOSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET:-10.15}"
PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/fleasion-pyinstaller}"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/fleasion-uv-cache}"
UV_MACOS_PROJECT_ENVIRONMENT="${UV_MACOS_PROJECT_ENVIRONMENT:-.tools/venv-macos}"
UV_MACOS_PYTHON_INSTALL_DIR="${UV_MACOS_PYTHON_INSTALL_DIR:-.tools/pythons-macos}"
UV_MACOS_PYTHON_VERSION="${UV_MACOS_PYTHON_VERSION:-3.12}"
UV_X86_CACHE_DIR="${UV_X86_CACHE_DIR:-/tmp/fleasion-uv-cache-x86}"
UV_X86_PROJECT_ENVIRONMENT="${UV_X86_PROJECT_ENVIRONMENT:-.tools/venv-x86}"
UV_X86_PYTHON_INSTALL_DIR="${UV_X86_PYTHON_INSTALL_DIR:-.tools/pythons-x86}"
UV_X86_PYTHON_VERSION="${UV_X86_PYTHON_VERSION:-3.12}"
UV_X86_PYTHON="${UV_X86_PYTHON:-cpython-${UV_X86_PYTHON_VERSION}-macos-x86_64-none}"
UV_X86_VERSION="${UV_X86_VERSION:-$(uv --version 2>/dev/null | sed -n 's/^uv \([^ ]*\).*/\1/p')}"
UV_X86_VERSION="${UV_X86_VERSION:-0.11.18}"
UV_X86_DIR=".tools/uv-x86_64-apple-darwin"
UV_X86_BIN="${UV_X86_DIR}/uv"

export MACOSX_DEPLOYMENT_TARGET PYINSTALLER_CONFIG_DIR UV_CACHE_DIR

macos_uv() {
    UV_PROJECT_ENVIRONMENT="$UV_MACOS_PROJECT_ENVIRONMENT" \
    UV_PYTHON_INSTALL_DIR="$UV_MACOS_PYTHON_INSTALL_DIR" \
    UV_MANAGED_PYTHON=1 \
    uv "$@"
}

x86_uv() {
    UV_CACHE_DIR="$UV_X86_CACHE_DIR" \
    UV_PROJECT_ENVIRONMENT="$UV_X86_PROJECT_ENVIRONMENT" \
    UV_PYTHON_INSTALL_DIR="$UV_X86_PYTHON_INSTALL_DIR" \
    UV_MANAGED_PYTHON=1 \
    arch -x86_64 "$UV_X86_BIN" "$@"
}

find_macos_python() {
    macos_uv python install "$UV_MACOS_PYTHON_VERSION" --install-dir "$UV_MACOS_PYTHON_INSTALL_DIR" >/dev/null
    macos_uv python find "$UV_MACOS_PYTHON_VERSION" --managed-python
}

find_x86_python() {
    x86_uv python install "$UV_X86_PYTHON" --install-dir "$UV_X86_PYTHON_INSTALL_DIR" >/dev/null
    x86_python_path="$(x86_uv python find "$UV_X86_PYTHON_VERSION" --managed-python)"
    x86_python_arch="$(arch -x86_64 "$x86_python_path" -c 'import platform; print(platform.machine())')"
    if [ "$x86_python_arch" != "x86_64" ]; then
        echo "Expected x86_64 Python for Intel build, got $x86_python_arch from $x86_python_path." >&2
        exit 1
    fi
    printf '%s\n' "$x86_python_path"
}

version_lte() {
    awk -v left="$1" -v right="$2" '
        BEGIN {
            split(left, lhs, ".")
            split(right, rhs, ".")
            for (idx = 1; idx <= 3; idx++) {
                lval = lhs[idx] + 0
                rval = rhs[idx] + 0
                if (lval < rval) exit 0
                if (lval > rval) exit 1
            }
            exit 0
        }
    '
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

require_only_archs() {
    file_path="$1"
    shift
    archs="$(lipo -archs "$file_path" 2>/dev/null || true)"
    for required_arch in "$@"; do
        case " $archs " in
            *" $required_arch "*) ;;
            *)
                echo "$file_path is missing $required_arch slice; found '$archs'." >&2
                exit 1
                ;;
        esac
    done
    for found_arch in $archs; do
        allowed=0
        for required_arch in "$@"; do
            if [ "$found_arch" = "$required_arch" ]; then
                allowed=1
                break
            fi
        done
        if [ "$allowed" -ne 1 ]; then
            echo "$file_path contains unexpected $found_arch slice; expected only '$*'." >&2
            exit 1
        fi
    done
}

app_payload_path() {
    app_path="$1"
    file_name="$2"
    for payload_dir in "${app_path}/Contents/Resources" "${app_path}/Contents/Frameworks"; do
        if [ -e "${payload_dir}/${file_name}" ]; then
            printf '%s\n' "${payload_dir}/${file_name}"
            return 0
        fi
    done
    return 1
}

require_app_payload() {
    app_path="$1"
    file_name="$2"
    build_label="$3"
    payload_path="$(app_payload_path "$app_path" "$file_name" || true)"
    if [ -z "$payload_path" ] || [ ! -x "$payload_path" ]; then
        echo "${build_label} completed, but bundled payload was not found: ${file_name}" >&2
        exit 1
    fi
    printf '%s\n' "$payload_path"
}

single_arch_macho_allowed() {
    file_path="$1"
    archs="$2"

    case "$file_path:$archs" in
        *"/Contents/Frameworks/Cryptodome/Hash/_ghash_clmul.abi3.so:x86_64" | \
        *"/Contents/Frameworks/Cryptodome/Cipher/_raw_aesni.abi3.so:x86_64")
            return 0
            ;;
    esac
    return 1
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
}

verify_app_archs() {
    app_path="$1"
    require_archs "${app_path}/Contents/MacOS/${EXEC_NAME}" arm64 x86_64
    arm_helper_path="$(require_app_payload "$app_path" "$HELPER_ARM64_EXEC_NAME" "Universal app")"
    x86_helper_path="$(require_app_payload "$app_path" "$HELPER_X86_EXEC_NAME" "Universal app")"
    require_only_archs "$arm_helper_path" arm64
    require_only_archs "$x86_helper_path" x86_64

    missing_archs="$(mktemp /tmp/fleasion-missing-archs.XXXXXX)"
    find "$app_path" -type f -print | while IFS= read -r file_path; do
        case "$file_path" in
            *"/${HELPER_ARM64_EXEC_NAME}")
                continue
                ;;
            *"/${HELPER_X86_EXEC_NAME}")
                continue
                ;;
        esac
        archs="$(lipo -archs "$file_path" 2>/dev/null || true)"
        [ -n "$archs" ] || continue
        case " $archs " in *" arm64 "*) has_arm=1 ;; *) has_arm=0 ;; esac
        case " $archs " in *" x86_64 "*) has_x86=1 ;; *) has_x86=0 ;; esac
        if [ "$has_arm" -ne 1 ] || [ "$has_x86" -ne 1 ]; then
            if single_arch_macho_allowed "$file_path" "$archs"; then
                continue
            fi
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

verify_macos_compatibility() {
    app_path="$1"
    unavailable_frameworks="$(mktemp /tmp/fleasion-unavailable-frameworks.XXXXXX)"
    incompatible_minos="$(mktemp /tmp/fleasion-incompatible-minos.XXXXXX)"

    find "$app_path" -type f -print | while IFS= read -r file_path; do
        archs="$(lipo -archs "$file_path" 2>/dev/null || true)"
        case " $archs " in *" x86_64 "*) ;; *) continue ;; esac

        if otool -arch x86_64 -L "$file_path" 2>/dev/null | grep -q "/System/Library/Frameworks/UniformTypeIdentifiers.framework"; then
            printf '%s\n' "$file_path" >> "$unavailable_frameworks"
        fi
        minos="$(otool -arch x86_64 -l "$file_path" 2>/dev/null | awk '
            /LC_BUILD_VERSION/ { build = 1; version_min = 0; next }
            /LC_VERSION_MIN_MACOSX/ { version_min = 1; build = 0; next }
            build && /minos/ { print $2; exit }
            version_min && /version/ { print $2; exit }
        ')"
        if [ -n "$minos" ] && ! version_lte "$minos" "$MACOSX_DEPLOYMENT_TARGET"; then
            printf '%s: macOS %s\n' "$file_path" "$minos" >> "$incompatible_minos"
        fi
    done

    if [ -s "$unavailable_frameworks" ]; then
        echo "App links frameworks unavailable on macOS 10.15 Catalina:" >&2
        sed -n '1,40p' "$unavailable_frameworks" >&2
        rm -f "$unavailable_frameworks" "$incompatible_minos"
        exit 1
    fi
    if [ -s "$incompatible_minos" ]; then
        echo "App contains binaries requiring newer than macOS ${MACOSX_DEPLOYMENT_TARGET}:" >&2
        sed -n '1,40p' "$incompatible_minos" >&2
        rm -f "$unavailable_frameworks" "$incompatible_minos"
        exit 1
    fi
    rm -f "$unavailable_frameworks" "$incompatible_minos"
}

save_arch_helper() {
    target_arch="$1"
    case "$target_arch" in
        arm64)
            arch_helper_path="$HELPER_ARM64_DIST_PATH"
            arch_helper_name="$HELPER_ARM64_EXEC_NAME"
            ;;
        x86_64)
            arch_helper_path="$HELPER_X86_DIST_PATH"
            arch_helper_name="$HELPER_X86_EXEC_NAME"
            ;;
        *)
            echo "Unsupported helper architecture: $target_arch" >&2
            exit 1
            ;;
    esac

    cp "$HELPER_DIST_PATH" "$arch_helper_path"
    chmod "$(stat -f %Lp "$HELPER_DIST_PATH")" "$arch_helper_path"
    require_only_archs "$arch_helper_path" "$target_arch"
    echo "Saved ${arch_helper_name} (${target_arch})"
}

build_current_arch() {
    target_arch="$1"
    macos_python_path="$(find_macos_python)"
    rm -rf "$UV_MACOS_PROJECT_ENVIRONMENT"
    macos_uv sync --locked --python "$macos_python_path" --group dev

    MACOS_TARGET_ARCH="$target_arch" macos_uv run --python "$macos_python_path" pyinstaller --clean --noconfirm FleasionProxyHelper.spec
    if [ ! -x "$HELPER_DIST_PATH" ]; then
        echo "Helper build completed, but expected executable was not found: $HELPER_DIST_PATH" >&2
        exit 1
    fi
    require_archs "$HELPER_DIST_PATH" "$target_arch"
    save_arch_helper "$target_arch"

    MACOS_TARGET_ARCH="$target_arch" macos_uv run --python "$macos_python_path" pyinstaller --clean --noconfirm Fleasion.spec

    verify_app_bundle "$APP_PATH" "Build"
    require_archs "$EXEC_PATH" "$target_arch"
    helper_app_path="$(require_app_payload "$APP_PATH" "${HELPER_EXEC_NAME}-${target_arch}" "Build")"
    require_only_archs "$helper_app_path" "$target_arch"
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
    save_arch_helper x86_64

    MACOS_TARGET_ARCH=x86_64 \
    x86_uv run --python "$x86_python_path" pyinstaller --clean --noconfirm Fleasion.spec

    verify_app_bundle "$APP_PATH" "Intel build"
    require_archs "$EXEC_PATH" x86_64
    helper_app_path="$(require_app_payload "$APP_PATH" "$HELPER_X86_EXEC_NAME" "Intel build")"
    require_only_archs "$helper_app_path" x86_64
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

    find "$x86_app" -type f -print | while IFS= read -r x86_file; do
        rel="${x86_file#${x86_app}/}"
        universal_file="${universal_app}/${rel}"
        [ ! -e "$universal_file" ] || continue
        mkdir -p "$(dirname "$universal_file")"
        cp -p "$x86_file" "$universal_file"
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
    verify_macos_compatibility "${zip_check_dir}/${EXEC_NAME}.app"
    rm -rf "$zip_check_dir"
}

finalize_app() {
    app_path="$1"
    app_arch="$2"

    verify_app_bundle "$app_path" "Final app"
    codesign --force --deep --sign - "$app_path"

    if [ "$app_arch" = "universal2" ]; then
        verify_app_archs "$app_path"
        verify_macos_compatibility "$app_path"
        rm -f "$ZIP_PATH"
        ditto -c -k --sequesterRsrc --keepParent "$app_path" "$ZIP_PATH"
        verify_zip_package "$ZIP_PATH"
        echo "Built ${app_path} (${app_arch})"
        echo "Built ${ZIP_PATH}"
    else
        require_archs "${app_path}/Contents/MacOS/${EXEC_NAME}" "$app_arch"
        helper_app_path="$(require_app_payload "$app_path" "${HELPER_EXEC_NAME}-${app_arch}" "Final app")"
        require_only_archs "$helper_app_path" "$app_arch"
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
