"""Platform-specific Roblox resource target helpers."""

from __future__ import annotations

import ntpath
import sys
import zipfile
from pathlib import Path

SOBER_TARGET_PATHS: dict[str, str] = {
    r'PlatformContent\pc\textures\sky\sky512_bk.tex': 'android/textures/sky/sky512_bk.tex',
    r'PlatformContent\pc\textures\sky\sky512_dn.tex': 'android/textures/sky/sky512_dn.tex',
    r'PlatformContent\pc\textures\sky\sky512_ft.tex': 'android/textures/sky/sky512_ft.tex',
    r'PlatformContent\pc\textures\sky\sky512_lf.tex': 'android/textures/sky/sky512_lf.tex',
    r'PlatformContent\pc\textures\sky\sky512_rt.tex': 'android/textures/sky/sky512_rt.tex',
    r'PlatformContent\pc\textures\sky\sky512_up.tex': 'android/textures/sky/sky512_up.tex',
    r'PlatformContent\pc\textures\sky\indoor512_bk.tex': 'android/textures/sky/indoor512_bk.tex',
    r'PlatformContent\pc\textures\sky\indoor512_dn.tex': 'android/textures/sky/indoor512_dn.tex',
    r'PlatformContent\pc\textures\sky\indoor512_ft.tex': 'android/textures/sky/indoor512_ft.tex',
    r'PlatformContent\pc\textures\sky\indoor512_lf.tex': 'android/textures/sky/indoor512_lf.tex',
    r'PlatformContent\pc\textures\sky\indoor512_rt.tex': 'android/textures/sky/indoor512_rt.tex',
    r'PlatformContent\pc\textures\sky\indoor512_up.tex': 'android/textures/sky/indoor512_up.tex',
    r'PlatformContent\pc\textures\plastic\diffuse.dds': 'android/textures/plastic/diffuse.dds',
    r'PlatformContent\pc\textures\plastic\normal.dds': 'android/textures/plastic/normal.dds',
    r'PlatformContent\pc\textures\plastic\normaldetail.dds': 'android/textures/plastic/normaldetail.ktx',
    r'PlatformContent\pc\textures\studs.dds': 'android/textures/studs.dds',
}


def _normalise_key(target_path: str | Path) -> str:
    return str(target_path or '').replace('\\', '/').strip('/')


def _normalise_relative_target(target_path: str | Path) -> str:
    text = str(target_path or '').strip()
    normalised = text.replace('\\', '/')
    drive, _tail = ntpath.splitdrive(text)
    parts = [part for part in normalised.split('/') if part and part != '.']
    if drive or normalised.startswith('/') or not parts or any(part == '..' for part in parts):
        raise ValueError('Target path must be a safe relative resource path')
    return '/'.join(parts)


_SOBER_TARGET_LOOKUP = {
    _normalise_key(source).casefold(): _normalise_key(destination)
    for source, destination in SOBER_TARGET_PATHS.items()
}
_SOBER_CANONICAL_LOOKUP = {
    destination.casefold(): _normalise_key(source)
    for source, destination in SOBER_TARGET_PATHS.items()
}


def target_path_for_current_platform(target_path: str | Path) -> str:
    """Return the persisted logical target path.

    Client-specific mapping happens only after a concrete resource root is
    known. Keeping this compatibility helper logical prevents import-time UI
    constants from baking one Linux backend's layout into saved entries.
    """
    return str(target_path)


def canonical_target_path(target_path: str | Path) -> str:
    """Return a logical target, migrating legacy Sober-resolved paths."""
    if not sys.platform.startswith('linux'):
        return str(target_path)
    try:
        normalised = _normalise_relative_target(target_path)
    except ValueError:
        return str(target_path)
    return _SOBER_CANONICAL_LOOKUP.get(normalised.casefold(), normalised)


def _linux_resource_client_key(resource_dir: Path) -> str | None:
    """Identify the registered backend which owns a concrete resource root."""
    if not sys.platform.startswith('linux'):
        return None
    root = Path(resource_dir)
    try:
        from ..utils.linux_clients import LINUX_CLIENTS
        from ..utils.paths import USER_HOME
        from ..utils.platform_linux import is_sober_resource_dir

        for descriptor in LINUX_CLIENTS:
            if descriptor.paths(home=USER_HOME).owns_resource_path(root):
                return descriptor.key
        # Preserve the narrow legacy Sober adapter used by tests and existing
        # installations whose Flatpak metadata is temporarily unavailable.
        if is_sober_resource_dir(root):
            return 'sober'
    except Exception:
        pass
    return None


def _target_path_for_client(client_key: str | None, target_path: str | Path) -> str:
    normalised = _normalise_relative_target(target_path)
    if client_key == 'sober':
        return _SOBER_TARGET_LOOKUP.get(normalised.casefold(), normalised)
    return normalised


def target_path_for_resource_dir(
    target_path: str | Path,
    resource_dir: Path,
) -> str:
    """Resolve a logical target for one concrete resource root.

    Unknown/future resource layouts keep the logical path until their backend
    registers an explicit mapping adapter here.
    """
    client_key = _linux_resource_client_key(resource_dir)
    return _target_path_for_client(client_key, target_path)


def content_prefixed_resource_root(resource_dir: Path) -> Path:
    """Return the base to which legacy ``content/...`` helpers may append."""
    return Path(resource_dir)


def target_path_candidates_for_current_platform(target_path: str | Path) -> list[str]:
    """Return legacy Sober target candidates, preferred path first."""
    normalised = _normalise_relative_target(target_path)
    candidates = [_target_path_for_client('sober', normalised), normalised]
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def target_path_candidates_for_resource_dir(
    target_path: str | Path,
    resource_dir: Path,
) -> list[str]:
    """Return root-aware target candidates, preferred path first."""
    normalised = _normalise_relative_target(target_path)
    candidates = [target_path_for_resource_dir(normalised, resource_dir), normalised]

    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = _normalise_key(candidate)
        if key and key not in seen:
            unique.append(key)
            seen.add(key)
    return unique


def _read_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes() if path.is_file() else None
    except OSError:
        return None


def _read_zip_member(archive_path: Path, member: str) -> bytes | None:
    try:
        with zipfile.ZipFile(archive_path) as archive:
            return archive.read(member)
    except KeyError, OSError, zipfile.BadZipFile:
        return None


def _read_sober_original_asset(target_path: str | Path) -> bytes | None:
    try:
        from ..utils.platform_linux import SOBER_DATA_DIR, SOBER_LEGACY_EXE_DIR
    except Exception:
        return None

    for rel in target_path_candidates_for_current_platform(target_path):
        for root in (SOBER_DATA_DIR / 'assets', SOBER_LEGACY_EXE_DIR):
            direct = _read_file(root / rel)
            if direct is not None:
                return direct

        packages_dir = SOBER_DATA_DIR / 'packages'
        try:
            apks = sorted(packages_dir.glob('*/com.roblox.client/base.apk'))
        except OSError:
            apks = []
        for apk in apks:
            archived = _read_zip_member(apk, f'assets/{rel}')
            if archived is not None:
                return archived
    return None


def _read_sober_original_asset_directory(target_dir: str | Path) -> dict[str, bytes]:
    """Read immediate files from a directory in Sober's packaged assets."""
    try:
        from ..utils.platform_linux import SOBER_DATA_DIR, SOBER_LEGACY_EXE_DIR
    except Exception:
        return {}

    try:
        rel = _normalise_relative_target(target_dir)
    except ValueError:
        return {}

    result: dict[str, bytes] = {}
    for root in (SOBER_DATA_DIR / 'assets', SOBER_LEGACY_EXE_DIR):
        directory = root / rel
        try:
            paths = sorted(path for path in directory.iterdir() if path.is_file())
        except OSError:
            paths = []
        for path in paths:
            data = _read_file(path)
            if data is not None:
                result.setdefault(path.name, data)

    packages_dir = SOBER_DATA_DIR / 'packages'
    try:
        apks = sorted(packages_dir.glob('*/com.roblox.client/base.apk'))
    except OSError:
        apks = []

    prefix = f'assets/{rel.rstrip("/")}/'
    for apk in apks:
        try:
            with zipfile.ZipFile(apk) as archive:
                for member in archive.namelist():
                    if not member.startswith(prefix):
                        continue
                    name = member[len(prefix) :]
                    if not name or '/' in name or name in result:
                        continue
                    try:
                        result[name] = archive.read(member)
                    except KeyError:
                        continue
        except OSError, zipfile.BadZipFile:
            continue
    return result


def read_current_platform_original_asset(
    target_path: str | Path,
    resource_dir: Path | None = None,
) -> bytes | None:
    """Read an original resource from platform-native storage when possible."""
    if not sys.platform.startswith('linux'):
        return None

    if resource_dir is not None:
        client_key = _linux_resource_client_key(resource_dir)
    else:
        try:
            from ..utils.platform_linux import selected_linux_client_key

            client_key = selected_linux_client_key()
        except Exception:
            client_key = 'sober'
    if client_key == 'sober':
        return _read_sober_original_asset(target_path)
    return None


def read_current_platform_original_directory(
    target_dir: str | Path,
    resource_dir: Path | None = None,
) -> dict[str, bytes]:
    """Read immediate original files from a platform-native asset directory."""
    if not sys.platform.startswith('linux'):
        return {}

    if resource_dir is not None:
        client_key = _linux_resource_client_key(resource_dir)
    else:
        try:
            from ..utils.platform_linux import selected_linux_client_key

            client_key = selected_linux_client_key()
        except Exception:
            client_key = 'sober'
    if client_key == 'sober':
        return _read_sober_original_asset_directory(target_dir)
    return {}
