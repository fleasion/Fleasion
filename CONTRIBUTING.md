# Contributing to Fleasion

Thanks for contributing to Fleasion. This document covers the development environment, source builds, validation, release/version workflow, and repository layout. User-facing installation and usage documentation belongs in [README.md](README.md).

## Development Requirements

- **Windows 10+, macOS 11+, or Linux with the Sober Flatpak**
- [**uv**](https://docs.astral.sh/uv/) package manager
- **Python 3.14+**
- Linux desktop installs need `pkexec`/Polkit available (installed by default on Mint and most desktop distributions)

## Running from Source

```bash
git clone https://github.com/fleasion/Fleasion.git
cd Fleasion
uv run fleasion
```

`uv` resolves and installs the project dependencies into its managed environment.

## Validation

Run the checks relevant to your change before submitting it:

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyright
```

## Building Standalone Releases

```bash
uv run build
```

`uv run build` is the build command on Windows, Linux, and macOS. Stable artifact filenames use the project version unchanged. Prerelease filenames add `+local` for local builds or `+g<short-sha>` on GitHub Actions; this provenance is not included in Fleasion's runtime version metadata.

### macOS universal builds

On macOS, the build command produces a universal release app by default. The output is copied to `dist/Fleasion-v{ARTIFACT_VERSION}.app`, mirrored at `dist/Fleasion.app`, and zipped as `dist/Fleasion-v{ARTIFACT_VERSION}-MacOS-Universal.zip`.

On Apple Silicon, the command builds the arm64 slice with the normal `uv` environment, bootstraps an ignored x86_64 build environment under `.tools/`, and resolves Python for both from the tracked `.python-version` pin. It builds the Intel slice under Rosetta, merges the app with `lipo`, signs it ad hoc, and verifies every Mach-O binary contains both `arm64` and `x86_64`.

Rosetta must be installed for the Intel build:

```bash
softwareupdate --install-rosetta --agree-to-license
```

For local single-architecture builds, set `MACOS_TARGET_ARCH=arm64` or `MACOS_TARGET_ARCH=x86_64`.

## Versioning and Releases

Use `uv version` to update the canonical version in `pyproject.toml` and `uv.lock`:

```bash
# Stable patch: 2.4.0 -> 2.4.1
uv version --bump patch

# First beta of the next minor: 2.4.0 -> 2.5.0b1
uv version --bump minor --bump beta

# Subsequent beta: 2.5.0b1 -> 2.5.0b2
uv version --bump beta

# Promote a prerelease: 2.5.0b2 -> 2.5.0
uv version --bump stable
```

Source runs, packaged distribution metadata, and stable GitHub releases use the canonical version. PyInstaller and GitHub Actions derive only the artifact filename label when building a prerelease.

The draft-release workflow accepts stable and prerelease project versions. Prereleases keep a clean version tag such as `v2.5.0b1`, publish artifacts containing their Git commit label, and are marked as GitHub prereleases. Stable installations check GitHub's latest stable release; prerelease installations follow newer published prereleases and automatically return to the stable channel after installing the final release. Draft releases are never offered by the updater.

## Project Structure

```text
├── Fleasion.spec   # PyInstaller specification for the standalone build
├── launcher.py     # Thin launcher used to start the packaged app
├── pyproject.toml  # Project metadata and dependency configuration
├── README.md       # User-facing project overview and usage guide
├── CONTRIBUTING.md # Development, build, validation, and release guide
├── scripts/
│   └── clear_first_time_setup.py  # Development utility for resetting initial setup
├── src/
│   └── fleasion/
│       ├── app.py                        # Application entrypoint, lifecycle, and startup wiring
│       ├── tray.py                       # System tray / menu bar integration
│       ├── cache/                        # Cache, preview, export, and conversion tooling
│       ├── config/                       # Settings persistence and config management
│       ├── gui/                          # Dashboard and supporting Qt widgets
│       ├── qml_api/                      # Qt/QML-facing application APIs
│       ├── modifications/                # FastFlags and client modification management
│       ├── prejsons/                     # Community preset support
│       ├── proxy/                        # Proxy server, routing, interception, and addons
│       ├── scripts/                      # Build orchestration
│       └── utils/                        # Shared platform and application utilities
├── tests/                                # Automated test suite
└── build/                                # Generated PyInstaller output (not source)
```

The repository changes frequently; use the source tree itself as the authoritative reference for individual modules.

## Contribution Guidelines

- Follow the existing project style and architecture rather than introducing parallel abstractions unnecessarily.
- Follow Pyright and Ruff rules.
- Use `uv run` for Python tooling and project commands.
- Keep platform-specific behavior behind the existing platform/client boundaries where possible.
- Add or update tests for behavior changes and regressions.
- Keep generated build artifacts out of hand-edited source changes.
- Keep user-facing documentation in `README.md`; keep contributor/development details here.
