#!/bin/sh
set -eu

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This build script must run on macOS." >&2
    exit 1
fi

PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/fleasion-pyinstaller}" \
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/fleasion-uv-cache}" \
    uv run pyinstaller --clean --noconfirm Fleasion.spec

echo "Built dist/Fleasion.app"
