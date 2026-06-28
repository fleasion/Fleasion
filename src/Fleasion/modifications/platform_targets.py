"""Platform-specific Roblox resource target helpers."""

from __future__ import annotations

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


def target_path_for_current_platform(target_path: str | Path) -> str:
    """Return the resource path that should be written on this platform."""
    if not sys.platform.startswith('linux'):
        return str(target_path)

    normalised = _normalise_key(target_path)
    lookup = {
        _normalise_key(source): dest
        for source, dest in SOBER_TARGET_PATHS.items()
    }
    return lookup.get(normalised, normalised)


def target_path_candidates_for_current_platform(target_path: str | Path) -> list[str]:
    """Return target path candidates, preferred path first."""
    normalised = _normalise_key(target_path)
    candidates = [target_path_for_current_platform(normalised), normalised]
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def read_current_platform_original_asset(target_path: str | Path) -> bytes | None:
    """Read an original resource from platform-native storage when possible."""
    if not sys.platform.startswith('linux'):
        return None

    try:
        from ..utils.platform_linux import SOBER_DATA_DIR, SOBER_LEGACY_EXE_DIR
    except Exception:
        return None

    for rel in target_path_candidates_for_current_platform(target_path):
        for root in (SOBER_DATA_DIR / 'assets', SOBER_LEGACY_EXE_DIR):
            candidate = root / rel
            try:
                if candidate.is_file():
                    return candidate.read_bytes()
            except OSError:
                pass

        packages_dir = SOBER_DATA_DIR / 'packages'
        try:
            apks = sorted(packages_dir.glob('*/com.roblox.client/base.apk'))
        except OSError:
            apks = []
        member = f'assets/{rel}'
        for apk in apks:
            try:
                with zipfile.ZipFile(apk) as archive:
                    try:
                        return archive.read(member)
                    except KeyError:
                        continue
            except (OSError, zipfile.BadZipFile):
                continue

    return None
