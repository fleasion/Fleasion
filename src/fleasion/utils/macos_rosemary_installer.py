"""Verified, per-user installation of the pinned macOS Rosemary runtime."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from fleasion.utils.paths import CONFIG_DIR

PINNED_UPSTREAM_BASE = '04cc76cd4c73e492ac3e780903c078d3b6115a63'
PINNED_RELEASE_TAG = 'fleasion-rosemary-v1'
# Filled only after the secure-IPC fork build passes both macOS runners and is released
PINNED_SOURCE_COMMIT = ''
PINNED_ARCHIVE_SHA256: dict[str, str] = {}
PINNED_MANIFEST_SHA256: dict[str, str] = {}

_REPO_RELEASES = 'https://github.com/Spencer0187/rosemary/releases/download'
_REQUIRED_FILES = frozenset(
    {
        'Rosemary/bin/rosemary',
        'Rosemary/lib/libMoltenVK.dylib',
        'Rosemary/source.json',
        'Rosemary/LICENSE',
        'Rosemary/LICENSE-MoltenVK',
        'Rosemary/usage.md',
    }
)
_MAX_ARCHIVE_BYTES = 160 * 1024 * 1024
_MAX_BINARY_BYTES = 90 * 1024 * 1024
_READ_CHUNK = 1024 * 1024


class PinnedRuntimeError(RuntimeError):
    """The pinned runtime could not be verified or installed safely."""


def _empty_manifests() -> dict[str, str]:
    return {}


@dataclass(frozen=True, slots=True)
class PinnedBuild:
    source_commit: str
    upstream_base: str
    release_tag: str
    archive_sha256: dict[str, str]
    manifest_sha256: dict[str, str] = field(default_factory=_empty_manifests)

    @property
    def available(self) -> bool:
        return (
            len(self.source_commit) == 40
            and len(self.upstream_base) == 40
            and bool(self.release_tag)
            and all(len(digest) == 64 for digest in self.archive_sha256.values())
            and {'arm64', 'x86_64'} <= self.archive_sha256.keys()
            and {'arm64', 'x86_64'} <= self.manifest_sha256.keys()
            and all(len(digest) == 64 for digest in self.manifest_sha256.values())
        )


PINNED_BUILD = PinnedBuild(
    PINNED_SOURCE_COMMIT,
    PINNED_UPSTREAM_BASE,
    PINNED_RELEASE_TAG,
    PINNED_ARCHIVE_SHA256,
    PINNED_MANIFEST_SHA256,
)


def host_arch() -> str:
    value = platform.machine().lower()
    if value in {'arm64', 'aarch64'}:
        return 'arm64'
    if value in {'x86_64', 'amd64'}:
        return 'x86_64'
    msg = f'Unsupported macOS architecture: {value}'
    raise PinnedRuntimeError(msg)


def install_root(pin: PinnedBuild = PINNED_BUILD, *, config_dir: Path | None = None) -> Path:
    root = config_dir if config_dir is not None else CONFIG_DIR
    return root / 'runtimes' / 'rosemary' / pin.source_commit[:12]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(_READ_CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_manifest(root: Path, pin: PinnedBuild, arch: str) -> Path:
    binary = root / 'bin' / 'rosemary'
    graphics = root / 'lib' / 'libMoltenVK.dylib'
    source_path = root / 'source.json'
    if (
        root.is_symlink()
        or any(path.is_symlink() for path in (binary, graphics, source_path))
        or not binary.is_file()
        or not graphics.is_file()
    ):
        msg = 'Pinned Rosemary executable is missing or is a symlink'
        raise PinnedRuntimeError(msg)
    try:
        # The release-pinned manifest hash is the trust anchor on subsequent
        # runs: a writable binary and its in-directory hash may both change.
        if _sha256(source_path) != pin.manifest_sha256[arch].lower():
            msg = 'Pinned Rosemary source manifest checksum mismatch'
            raise PinnedRuntimeError(msg)
        manifest_value: object = json.loads(source_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        msg = 'Pinned Rosemary source manifest is unreadable'
        raise PinnedRuntimeError(msg) from exc
    if not isinstance(manifest_value, dict):
        msg = 'Pinned Rosemary source manifest is not a JSON object'
        raise PinnedRuntimeError(msg)
    manifest = cast('dict[str, object]', manifest_value)
    if any(
        manifest.get(key) != expected
        for key, expected in (
            ('schema_version', 1),
            ('platform', 'macos'),
            ('arch', arch),
            ('source_commit', pin.source_commit),
            ('upstream_base', pin.upstream_base),
        )
    ):
        msg = 'Pinned Rosemary source revision or architecture differs from the expected build'
        raise PinnedRuntimeError(msg)
    expected_hash = manifest.get('binary_sha256')
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        msg = 'Pinned Rosemary executable hash is missing'
        raise PinnedRuntimeError(msg)
    if binary.stat().st_size > _MAX_BINARY_BYTES or _sha256(binary) != expected_hash.lower():
        msg = 'Pinned Rosemary executable checksum mismatch'
        raise PinnedRuntimeError(msg)
    graphics_hash = manifest.get('vulkan_library_sha256')
    if not isinstance(graphics_hash, str) or len(graphics_hash) != 64:
        msg = 'Pinned Rosemary Vulkan library hash is missing'
        raise PinnedRuntimeError(msg)
    if (
        manifest.get('vulkan_library') != 'MoltenVK'
        or manifest.get('vulkan_version') != '1.4.2'
        or graphics.stat().st_size > _MAX_BINARY_BYTES
        or _sha256(graphics) != graphics_hash.lower()
    ):
        msg = 'Pinned Rosemary Vulkan library failed integrity verification'
        raise PinnedRuntimeError(msg)
    return binary


def installed_binary(
    pin: PinnedBuild = PINNED_BUILD, *, arch: str | None = None, config_dir: Path | None = None
) -> Path | None:
    if not pin.available:
        return None
    arch = arch or host_arch()
    root = install_root(pin, config_dir=config_dir)
    try:
        return _validated_manifest(root, pin, arch)
    except OSError, PinnedRuntimeError:
        return None


def archive_name(pin: PinnedBuild, arch: str) -> str:
    return f'Rosemary-Fleasion-{pin.source_commit[:12]}-macos-{arch}.tar.gz'


def release_url(pin: PinnedBuild, arch: str) -> str:
    if not pin.available or arch not in pin.archive_sha256:
        msg = 'The pinned Fleasion Rosemary release has not been published and verified yet'
        raise PinnedRuntimeError(msg)
    return f'{_REPO_RELEASES}/{pin.release_tag}/{archive_name(pin, arch)}'


def _download_archive(url: str, destination: Path, expected_sha256: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or parsed.hostname != 'github.com':
        msg = 'Pinned Rosemary downloads must originate from GitHub HTTPS releases'
        raise PinnedRuntimeError(msg)
    request = urllib.request.Request(url, headers={'User-Agent': 'Fleasion-macOS-Rosemary'})  # ruff: ignore[suspicious-url-open-usage]
    digest = hashlib.sha256()
    received = 0
    with urllib.request.urlopen(request, timeout=45) as source, destination.open('xb') as output:  # ruff: ignore[suspicious-url-open-usage]
        if urlsplit(str(source.geturl())).scheme != 'https':
            msg = 'Pinned Rosemary download redirected outside HTTPS'
            raise PinnedRuntimeError(msg)
        while chunk := source.read(_READ_CHUNK):
            received += len(chunk)
            if received > _MAX_ARCHIVE_BYTES:
                msg = 'Pinned Rosemary download exceeded its size limit'
                raise PinnedRuntimeError(msg)
            output.write(chunk)
            digest.update(chunk)
        output.flush()
        os.fsync(output.fileno())
    if received == 0 or digest.hexdigest() != expected_sha256.lower():
        msg = 'Pinned Rosemary download checksum does not match Fleasion’s release pin'
        raise PinnedRuntimeError(msg)


def _extract_archive(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, 'r:gz') as compressed:
        members = {m.name.rstrip('/'): m for m in compressed.getmembers()}
        files = {name for name, member in members.items() if member.isfile()}
        if files != set(_REQUIRED_FILES) or set(members) - _REQUIRED_FILES - {
            'Rosemary',
            'Rosemary/bin',
            'Rosemary/lib',
        }:
            msg = 'Pinned Rosemary archive has unexpected files'
            raise PinnedRuntimeError(msg)
        if any(
            member.issym() or member.islnk() or not (member.isfile() or member.isdir())
            for member in members.values()
        ):
            msg = 'Pinned Rosemary archive contains unsupported links or device files'
            raise PinnedRuntimeError(msg)
        for name in sorted(files):
            member = members[name]
            if member.size < 0 or member.size > _MAX_BINARY_BYTES:
                msg = 'Pinned Rosemary archive member exceeds the extraction limit'
                raise PinnedRuntimeError(msg)
            source = compressed.extractfile(member)
            if source is None:
                msg = 'Pinned Rosemary archive is missing a file payload'
                raise PinnedRuntimeError(msg)
            target = destination / Path(name).relative_to('Rosemary')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open('xb') as output:
                shutil.copyfileobj(source, output, length=_READ_CHUNK)
                output.flush()
                os.fsync(output.fileno())
    executable = destination / 'bin' / 'rosemary'
    executable.chmod(0o700)


def _verify_signature(binary: Path) -> None:
    try:
        result = subprocess.run(
            ['/usr/bin/codesign', '--verify', '--strict', str(binary)],
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        msg = 'Could not verify the pinned Rosemary executable signature'
        raise PinnedRuntimeError(msg) from exc
    if result.returncode != 0:
        msg = 'Pinned Rosemary macOS code-signature validation failed'
        raise PinnedRuntimeError(msg)


def install_pinned_runtime(
    pin: PinnedBuild = PINNED_BUILD, *, arch: str | None = None, config_dir: Path | None = None
) -> Path:
    """Install only the explicitly pinned, checksum-verified release in user storage."""
    arch = arch or host_arch()
    existing = installed_binary(pin, arch=arch, config_dir=config_dir)
    if existing is not None:
        return existing
    url = release_url(pin, arch)
    root = install_root(pin, config_dir=config_dir)
    parent = root.parent
    parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.rosemary-install-', dir=parent) as temp:
        temp_dir = Path(temp)
        archive = temp_dir / archive_name(pin, arch)
        _download_archive(url, archive, pin.archive_sha256[arch])
        staged = temp_dir / 'payload'
        staged.mkdir()
        _extract_archive(archive, staged)
        binary = _validated_manifest(staged, pin, arch)
        _verify_signature(binary)
        _verify_signature(staged / 'lib' / 'libMoltenVK.dylib')
        if root.exists():
            # A second installer must not overwrite a previously installed runtime.
            binary = installed_binary(pin, arch=arch, config_dir=config_dir)
            if binary is not None:
                return binary
            msg = 'Pinned Rosemary installation directory is already occupied'
            raise PinnedRuntimeError(msg)
        staged.replace(root)
    return root / 'bin' / 'rosemary'
