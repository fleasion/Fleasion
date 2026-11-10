"""Pinned macOS Rosemary installation remains isolated, checked and non-destructive."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from typing import TYPE_CHECKING

import pytest

from fleasion.utils import macos_rosemary_installer as installer

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def pin() -> installer.PinnedBuild:
    return installer.PinnedBuild(
        'a' * 40, 'b' * 40, 'fleasion-rosemary-test',
        {'arm64': '0' * 64, 'x86_64': 'f' * 64},
        {'arm64': '0' * 64, 'x86_64': 'f' * 64},
    )


def _make_archive(path: Path, pin: installer.PinnedBuild, *, malicious: bool = False) -> str:
    binary = b'arm64 test build bytes'
    vulkan = b'fake-universal-moltenvk'
    manifest = {
        'schema_version': 1,
        'platform': 'macos',
        'arch': 'arm64',
        'source_commit': pin.source_commit,
        'upstream_base': pin.upstream_base,
        'binary_sha256': hashlib.sha256(binary).hexdigest(),
        'vulkan_library': 'MoltenVK',
        'vulkan_version': '1.4.2',
        'vulkan_library_sha256': hashlib.sha256(vulkan).hexdigest(),
    }
    files = {
        'Rosemary/bin/rosemary': binary,
        'Rosemary/lib/libMoltenVK.dylib': vulkan,
        'Rosemary/LICENSE-MoltenVK': b'MoltenVK Apache-2.0 license',
        'Rosemary/source.json': json.dumps(manifest).encode(),
        'Rosemary/LICENSE': b'license',
        'Rosemary/usage.md': b'usage',
    }
    if malicious:
        files['Rosemary/../../bad'] = b'payload'
    with tarfile.open(path, 'w:gz') as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_hash(archive: Path) -> str:
    with tarfile.open(archive, 'r:gz') as compressed:
        manifest = compressed.extractfile('Rosemary/source.json')
        assert manifest is not None
        return hashlib.sha256(manifest.read()).hexdigest()



def test_v1_prerelease_pins_are_complete_for_both_macos_architectures() -> None:
    assert installer.PINNED_BUILD.available
    assert installer.PINNED_BUILD.source_commit == '7ea9941de8a0ea4435189d1f24159d707187d21f'
    assert installer.PINNED_BUILD.release_tag == 'fleasion-rosemary-v1'
    for arch in ('arm64', 'x86_64'):
        assert len(installer.PINNED_BUILD.archive_sha256[arch]) == 64
        assert len(installer.PINNED_BUILD.manifest_sha256[arch]) == 64
        assert installer.release_url(installer.PINNED_BUILD, arch).endswith(
            f'/Rosemary-Fleasion-7ea9941de8a0-macos-{arch}.tar.gz'
        )


def test_unpublished_release_refuses_download(pin: installer.PinnedBuild, tmp_path: Path) -> None:
    unconfigured = installer.PinnedBuild('', pin.upstream_base, pin.release_tag, {})
    assert installer.installed_binary(unconfigured, config_dir=tmp_path) is None
    with pytest.raises(installer.PinnedRuntimeError, match='not been published'):
        installer.install_pinned_runtime(unconfigured, arch='arm64', config_dir=tmp_path)


def test_verified_install_and_existing_install_are_idempotent(
    pin: installer.PinnedBuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / 'release.tar.gz'
    sha = _make_archive(archive, pin)
    pinned = installer.PinnedBuild(
        pin.source_commit, pin.upstream_base, pin.release_tag,
        {'arm64': sha, 'x86_64': 'f' * 64},
        {'arm64': _manifest_hash(archive), 'x86_64': 'f' * 64},
    )
    downloads: list[str] = []

    def download(url: str, destination: Path, digest: str) -> None:
        assert url == installer.release_url(pinned, 'arm64')
        assert digest == sha
        downloads.append(url)
        destination.write_bytes(archive.read_bytes())

    monkeypatch.setattr(installer, '_download_archive', download)
    monkeypatch.setattr(installer, '_verify_signature', lambda _p: None)
    binary = installer.install_pinned_runtime(pinned, arch='arm64', config_dir=tmp_path)
    assert binary.read_bytes() == b'arm64 test build bytes'
    assert (binary.parent.parent / 'lib' / 'libMoltenVK.dylib').read_bytes() == b'fake-universal-moltenvk'
    assert binary.stat().st_mode & 0o777 == 0o700
    assert installer.installed_binary(pinned, arch='arm64', config_dir=tmp_path) == binary
    assert installer.install_pinned_runtime(pinned, arch='arm64', config_dir=tmp_path) == binary
    assert len(downloads) == 1



def test_tampered_installed_binary_and_matching_local_manifest_are_rejected(
    pin: installer.PinnedBuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / 'release.tar.gz'
    sha = _make_archive(archive, pin)
    pinned = installer.PinnedBuild(
        pin.source_commit, pin.upstream_base, pin.release_tag,
        {'arm64': sha, 'x86_64': 'f' * 64},
        {'arm64': _manifest_hash(archive), 'x86_64': 'f' * 64},
    )
    monkeypatch.setattr(installer, '_download_archive', lambda _u, dest, _h: dest.write_bytes(archive.read_bytes()))
    monkeypatch.setattr(installer, '_verify_signature', lambda _p: None)
    binary = installer.install_pinned_runtime(pinned, arch='arm64', config_dir=tmp_path)
    binary.write_bytes(b'tampered-binary')
    manifest = binary.parent.parent / 'source.json'
    data = json.loads(manifest.read_text())
    data['binary_sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
    manifest.write_text(json.dumps(data))
    assert installer.installed_binary(pinned, arch='arm64', config_dir=tmp_path) is None
    with pytest.raises(installer.PinnedRuntimeError, match='already occupied'):
        installer.install_pinned_runtime(pinned, arch='arm64', config_dir=tmp_path)


def test_tampered_archive_never_installs(
    pin: installer.PinnedBuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / 'malicious.tar.gz'
    sha = _make_archive(archive, pin, malicious=True)
    pinned = installer.PinnedBuild(
        pin.source_commit, pin.upstream_base, pin.release_tag,
        {'arm64': sha, 'x86_64': 'f' * 64},
        {'arm64': _manifest_hash(archive), 'x86_64': 'f' * 64},
    )
    monkeypatch.setattr(installer, '_download_archive', lambda _url, dest, _sha: dest.write_bytes(archive.read_bytes()))
    monkeypatch.setattr(installer, '_verify_signature', lambda _p: None)
    with pytest.raises(installer.PinnedRuntimeError, match='unexpected files'):
        installer.install_pinned_runtime(pinned, arch='arm64', config_dir=tmp_path)
    assert not installer.install_root(pinned, config_dir=tmp_path).exists()
    assert not (tmp_path / 'bad').exists()


def test_source_commit_mismatch_cannot_be_reused(
    pin: installer.PinnedBuild, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    archive = tmp_path / 'release.tar.gz'
    _make_archive(archive, pin)
    other = installer.PinnedBuild(
        'c' * 40, pin.upstream_base, pin.release_tag,
        {'arm64': 'e' * 64, 'x86_64': 'f' * 64},
        {'arm64': _manifest_hash(archive), 'x86_64': 'f' * 64},
    )
    monkeypatch.setattr(installer, '_download_archive', lambda _url, dest, _sha: dest.write_bytes(archive.read_bytes()))
    monkeypatch.setattr(installer, '_verify_signature', lambda _p: None)
    with pytest.raises(installer.PinnedRuntimeError, match='source revision'):
        installer.install_pinned_runtime(other, arch='arm64', config_dir=tmp_path)


def test_download_rejects_non_https_github_urls(tmp_path: Path) -> None:
    for url in ('file:///private/test', 'http://github.com/something', 'https://evil.example/archive'):
        with pytest.raises(installer.PinnedRuntimeError, match='GitHub HTTPS'):
            installer._download_archive(url, tmp_path / 'archive.tar.gz', '0' * 64)
