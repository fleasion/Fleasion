from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from .logging import log_buffer
from .paths import CONFIG_DIR, DOWNLOADS_DIR, FORCED_ROBLOX_VERSION, LOCAL_APPDATA, ROBLOX_PROCESS

DEPLOYMENT_MIRRORS = [
    'https://setup.rbxcdn.com',
    'https://setup-aws.rbxcdn.com',
    'https://setup-ak.rbxcdn.com',
    'https://s3.amazonaws.com/setup.roblox.com',
]

PACKAGE_DIRECTORY_MAP = {
    'RobloxApp.zip': '',
    'Libraries.zip': '',
    'redist.zip': '',
    'shaders.zip': 'shaders',
    'ssl.zip': 'ssl',
    'WebView2.zip': '',
    'WebView2RuntimeInstaller.zip': 'WebView2RuntimeInstaller',
    'content-avatar.zip': os.path.join('content', 'avatar'),
    'content-configs.zip': os.path.join('content', 'configs'),
    'content-fonts.zip': os.path.join('content', 'fonts'),
    'content-sky.zip': os.path.join('content', 'sky'),
    'content-sounds.zip': os.path.join('content', 'sounds'),
    'content-textures2.zip': os.path.join('content', 'textures'),
    'content-models.zip': os.path.join('content', 'models'),
    'content-textures3.zip': os.path.join('PlatformContent', 'pc', 'textures'),
    'content-terrain.zip': os.path.join('PlatformContent', 'pc', 'terrain'),
    'content-platform-fonts.zip': os.path.join('PlatformContent', 'pc', 'fonts'),
    'content-platform-dictionaries.zip': os.path.join(
        'PlatformContent', 'pc', 'shared_compression_dictionaries'
    ),
    'extracontent-places.zip': os.path.join('ExtraContent', 'places'),
    'extracontent-luapackages.zip': os.path.join('ExtraContent', 'LuaPackages'),
    'extracontent-translations.zip': os.path.join('ExtraContent', 'translations'),
    'extracontent-models.zip': os.path.join('ExtraContent', 'models'),
    'extracontent-textures.zip': os.path.join('ExtraContent', 'textures'),
}

APP_SETTINGS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\r\n'
    '<Settings>\r\n'
    '\t<ContentFolder>content</ContentFolder>\r\n'
    '\t<BaseUrl>http://www.roblox.com</BaseUrl>\r\n'
    '</Settings>\r\n'
)


type ProgressCallback = Callable[[str, int, int, float], None]


@dataclass
class Package:
    name: str
    signature: str
    packed_size: int
    size: int


class InstallCancelled(RuntimeError):
    pass


def _handle_remove_readonly(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    try:
        Path(path).chmod(stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _remove_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, onexc=_handle_remove_readonly)


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event and cancel_event.is_set():
        raise InstallCancelled('Installation cancelled')


def _get_macos_applications_dir() -> Path:
    apps_dir = Path('/Applications')
    probe = apps_dir / f'.fleasion_write_probe_{uuid.uuid4().hex}'
    try:
        probe.write_bytes(b'')
        probe.unlink(missing_ok=True)
        return apps_dir
    except OSError:
        user_apps = Path.home() / 'Applications'
        user_apps.mkdir(parents=True, exist_ok=True)
        return user_apps


def fetch_macos_client_upload_version() -> str:
    hosts = [
        'https://clientsettingscdn.roblox.com',
        'https://clientsettings.roblox.com',
    ]
    for host in hosts:
        url = f'{host}/v2/client-version/MacPlayer'
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Roblox/WinInet'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode('utf-8'))
                    upload = data.get('clientVersionUpload')
                    if upload and upload.startswith('version-'):
                        return upload
        except Exception:
            continue
    raise RuntimeError('Could not determine the current macOS Roblox version')


def get_forced_version_candidate_dirs(local_appdata: Path | None = None) -> list[Path]:
    if sys.platform == 'darwin':
        return [
            Path('/Applications/Roblox.app'),
            Path.home() / 'Applications' / 'Roblox.app',
        ]
    base_appdata = local_appdata if local_appdata is not None else LOCAL_APPDATA
    return [
        CONFIG_DIR / 'Versions' / FORCED_ROBLOX_VERSION,
        base_appdata / 'Roblox' / 'Versions' / FORCED_ROBLOX_VERSION,
    ]


def get_forced_version_exe_path(local_appdata: Path | None = None) -> Path | None:
    if sys.platform == 'darwin':
        for app in get_forced_version_candidate_dirs():
            exe = app / 'Contents' / 'MacOS' / 'RobloxPlayer'
            if exe.is_file():
                return exe
        return None

    for folder in get_forced_version_candidate_dirs(local_appdata):
        exe_file = folder / ROBLOX_PROCESS
        try:
            if exe_file.is_file():
                return exe_file
        except OSError:
            pass
    return None


def is_forced_version_installed(local_appdata: Path | None = None) -> bool:
    return get_forced_version_exe_path(local_appdata) is not None


def get_default_install_dir() -> Path:
    if sys.platform == 'darwin':
        return _get_macos_applications_dir() / 'Roblox.app'
    return CONFIG_DIR / 'Versions' / FORCED_ROBLOX_VERSION


def get_download_cache_dirs() -> list[Path]:
    return [
        DOWNLOADS_DIR,
        LOCAL_APPDATA / 'Roblox' / 'Downloads',
    ]


def _compute_file_md5(file_path: Path) -> str:
    hasher = hashlib.md5()
    with Path(file_path).open('rb') as f:
        while chunk := f.read(131072):
            hasher.update(chunk)
    return hasher.hexdigest().lower()


def find_cached_package(signature: str, expected_size: int) -> Path | None:
    for cache_dir in get_download_cache_dirs():
        candidate = cache_dir / signature
        try:
            if candidate.is_file():
                if expected_size > 0 and candidate.stat().st_size != expected_size:
                    continue
                if _compute_file_md5(candidate) == signature.lower():
                    return candidate
        except OSError:
            pass
    return None


def fetch_manifest(version_guid: str, cancel_event: threading.Event | None = None) -> list[Package]:
    urls: list[str] = []
    for mirror in DEPLOYMENT_MIRRORS:
        urls.append(f'{mirror}/channel/common/{version_guid}-rbxPkgManifest.txt')
        urls.append(f'{mirror}/{version_guid}-rbxPkgManifest.txt')

    manifest_text: str | None = None
    last_exc: Exception | None = None
    for url in urls:
        _check_cancel(cancel_event)
        try:
            req = urllib.request.Request(
                url,
                headers={'User-Agent': 'Roblox/WinInet', 'Accept': 'text/plain'},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status == 200:
                    manifest_text = resp.read().decode('utf-8', errors='replace')
                    break
        except Exception as exc:
            last_exc = exc
            continue

    if not manifest_text:
        raise RuntimeError(f'Failed to fetch manifest for {version_guid}: {last_exc}')

    lines = [line.strip() for line in manifest_text.splitlines() if line.strip()]
    if not lines or lines[0] != 'v0':
        raise RuntimeError('Invalid manifest format (expected v0)')

    packages: list[Package] = []
    idx = 1
    while idx + 3 < len(lines):
        name = lines[idx]
        try:
            packed_size = int(lines[idx + 2])
            size = int(lines[idx + 3])
        except ValueError as exc:
            raise RuntimeError(f'Invalid manifest entry for {name}') from exc
        if name.lower().endswith('.zip'):
            packages.append(
                Package(name=name, signature=lines[idx + 1], packed_size=packed_size, size=size)
            )
        idx += 4

    if not packages:
        raise RuntimeError('Manifest contained no packages')
    return packages


def _get_package_urls(version_guid: str, package_name: str) -> list[str]:
    urls: list[str] = []
    for mirror in DEPLOYMENT_MIRRORS:
        urls.append(f'{mirror}/channel/common/{version_guid}-{package_name}')
        urls.append(f'{mirror}/{version_guid}-{package_name}')
    return urls


def download_single_package(
    package: Package,
    version_guid: str,
    cache_dir: Path,
    progress_tracker: dict[str, int],
    progress_lock: threading.Lock,
    on_progress_update: Callable[[], None] | None,
    cancel_event: threading.Event | None = None,
) -> Path:
    def set_progress(value: int, add: bool = False):
        with progress_lock:
            progress_tracker[package.name] = (
                progress_tracker.get(package.name, 0) if add else 0
            ) + value
        if on_progress_update:
            on_progress_update()

    cached = find_cached_package(package.signature, package.packed_size)
    if cached:
        set_progress(package.packed_size)
        return cached

    cache_dir.mkdir(parents=True, exist_ok=True)
    dest_file = cache_dir / package.signature
    temp_file = cache_dir / f'{package.signature}.part'
    signature = package.signature.lower()

    urls = _get_package_urls(version_guid, package.name)
    last_exc: Exception | None = None

    for attempt in range(len(urls)):
        _check_cancel(cancel_event)
        url = urls[attempt]
        try:
            resume_offset = 0
            if temp_file.is_file():
                resume_offset = temp_file.stat().st_size
                if resume_offset >= package.packed_size:
                    if (
                        resume_offset == package.packed_size
                        and _compute_file_md5(temp_file) == signature
                    ):
                        temp_file.replace(dest_file)
                        set_progress(package.packed_size)
                        return dest_file
                    temp_file.unlink(missing_ok=True)
                    resume_offset = 0

            headers = {'User-Agent': 'Roblox/WinInet'}
            if resume_offset > 0:
                headers['Range'] = f'bytes={resume_offset}-'

            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=25) as resp:
                if resume_offset > 0 and getattr(resp, 'status', 200) != 206:
                    resume_offset = 0
                set_progress(resume_offset)

                with Path(temp_file).open('ab' if resume_offset > 0 else 'wb') as out_f:
                    while True:
                        _check_cancel(cancel_event)
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        set_progress(len(chunk), add=True)

            if _compute_file_md5(temp_file) != signature:
                temp_file.unlink(missing_ok=True)
                set_progress(0)
                raise ValueError(f'Checksum mismatch for {package.name}')

            temp_file.replace(dest_file)
            return dest_file
        except InstallCancelled:
            raise
        except Exception as exc:
            last_exc = exc
            delay = 0.3 * (attempt + 1)
            if cancel_event:
                cancel_event.wait(delay)
            else:
                time.sleep(delay)

    raise RuntimeError(f'Failed to download {package.name}: {last_exc}')


def extract_package(package: Package, zip_path: Path, staging_dir: Path):
    rel_dir = PACKAGE_DIRECTORY_MAP.get(package.name, '')
    dest_dir = staging_dir if not rel_dir else staging_dir / rel_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dir_resolved = dest_dir.resolve()

    with zipfile.ZipFile(zip_path, 'r') as zf:
        for member in zf.infolist():
            name = member.filename.replace('\\', '/')
            extracted_path = (dest_dir_resolved / name).resolve()
            if not extracted_path.is_relative_to(dest_dir_resolved):
                continue
            if name.endswith('/'):
                extracted_path.mkdir(parents=True, exist_ok=True)
                continue
            extracted_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, Path(extracted_path).open('wb') as dst:
                shutil.copyfileobj(src, dst)


def install_macos_roblox_version(
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if progress_cb:
        progress_cb('Checking for latest macOS Roblox version...', 0, 0, 0.0)

    upload = fetch_macos_client_upload_version()
    machine = platform.machine().lower()
    is_arm = 'arm' in machine or 'aarch64' in machine
    arch_prefix = 'mac/arm64/' if is_arm else 'mac/'
    relative_path = f'{arch_prefix}{upload}-RobloxPlayer.zip'
    pkg_filename = f'{upload}-{"arm64-" if is_arm else ""}RobloxPlayer.zip'

    cache_dir = DOWNLOADS_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest_archive = cache_dir / pkg_filename
    temp_archive = cache_dir / f'{pkg_filename}.download'

    if not dest_archive.is_file():
        last_error: Exception | None = None
        for mirror in DEPLOYMENT_MIRRORS:
            _check_cancel(cancel_event)
            url = f'{mirror}/{relative_path}'
            try:
                resume_offset = temp_archive.stat().st_size if temp_archive.exists() else 0

                headers = {'User-Agent': 'Roblox/WinInet'}
                if resume_offset > 0:
                    headers['Range'] = f'bytes={resume_offset}-'

                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resume_offset > 0 and getattr(resp, 'status', 200) != 206:
                        resume_offset = 0

                    content_len = resp.headers.get('Content-Length')
                    total_size = int(content_len) + resume_offset if content_len else 0

                    with Path(temp_archive).open('ab' if resume_offset > 0 else 'wb') as out_f:
                        received = resume_offset
                        while True:
                            _check_cancel(cancel_event)
                            chunk = resp.read(65536)
                            if not chunk:
                                break
                            out_f.write(chunk)
                            received += len(chunk)
                            if progress_cb:
                                pct = (received / total_size * 100.0) if total_size > 0 else 0.0
                                progress_cb(
                                    f'Downloading Roblox for macOS ({pct:.1f}%)...',
                                    received,
                                    total_size,
                                    pct,
                                )

                if total_size and temp_archive.stat().st_size != total_size:
                    raise RuntimeError('Download ended early')
                if not zipfile.is_zipfile(temp_archive):
                    temp_archive.unlink(missing_ok=True)
                    raise RuntimeError('Downloaded archive is corrupt')
                temp_archive.replace(dest_archive)
                break
            except InstallCancelled:
                raise
            except Exception as exc:
                last_error = exc

        if not dest_archive.is_file():
            raise RuntimeError(f'Failed to download macOS Roblox package: {last_error}')

    _check_cancel(cancel_event)

    apps_dir = _get_macos_applications_dir()
    target_bundle = apps_dir / 'Roblox.app'
    staging_work = CONFIG_DIR / 'staging' / f'mac_work_{uuid.uuid4().hex}'
    staging_bundle = apps_dir / f'.Roblox.app.installing-{uuid.uuid4().hex}'

    staging_work.mkdir(parents=True, exist_ok=True)

    try:
        if progress_cb:
            progress_cb('Extracting Roblox application bundle...', 50, 100, 50.0)

        if Path('/usr/bin/ditto').is_file():
            subprocess.run(
                ['/usr/bin/ditto', '-x', '-k', str(dest_archive), str(staging_work)], check=True
            )
        else:
            with zipfile.ZipFile(dest_archive, 'r') as zf:
                zf.extractall(staging_work)

        found_bundle = next(
            (entry for entry in staging_work.glob('**/*.app') if entry.is_dir()), None
        )
        if found_bundle is None:
            raise RuntimeError('Downloaded package did not contain a Roblox .app bundle')

        _check_cancel(cancel_event)
        if progress_cb:
            progress_cb('Installing Roblox into Applications...', 80, 100, 80.0)

        if Path('/usr/bin/ditto').is_file():
            subprocess.run(['/usr/bin/ditto', str(found_bundle), str(staging_bundle)], check=True)
        else:
            shutil.copytree(found_bundle, staging_bundle, symlinks=True)

        _remove_tree(target_bundle)
        if target_bundle.exists():
            raise RuntimeError(f'Could not replace {target_bundle}, quit Roblox and try again')
        staging_bundle.rename(target_bundle)

        exe_path = target_bundle / 'Contents' / 'MacOS' / 'RobloxPlayer'
        if not exe_path.is_file():
            raise RuntimeError(f'Executable missing in installed bundle: {exe_path}')

        try:
            Path(exe_path).chmod(
                os.stat(exe_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
            )
        except OSError:
            pass

        log_buffer.log('Downloader', f'Successfully installed macOS Roblox to {target_bundle}')
        return exe_path
    finally:
        _remove_tree(staging_work)
        _remove_tree(staging_bundle)


def install_forced_roblox_version(
    progress_cb: ProgressCallback | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    if sys.platform == 'darwin':
        return install_macos_roblox_version(progress_cb=progress_cb, cancel_event=cancel_event)

    cache_dir = DOWNLOADS_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)

    final_dir = get_default_install_dir()
    staging_dir = final_dir.parent / f'{FORCED_ROBLOX_VERSION}.installing'
    backup_dir = final_dir.parent / f'{FORCED_ROBLOX_VERSION}.old'

    if progress_cb:
        progress_cb('Connecting to Roblox...', 0, 0, 0.0)

    manifest = fetch_manifest(FORCED_ROBLOX_VERSION, cancel_event)
    total_packed_bytes = sum(p.packed_size for p in manifest)

    progress_tracker: dict[str, int] = {}
    progress_lock = threading.Lock()
    last_notify = [0.0]

    def notify_progress():
        if not progress_cb:
            return
        now = time.monotonic()
        with progress_lock:
            if now - last_notify[0] < 0.1:
                return
            last_notify[0] = now
            current_bytes = sum(progress_tracker.values())
        pct = (
            min(100.0, current_bytes / total_packed_bytes * 100.0)
            if total_packed_bytes > 0
            else 0.0
        )
        progress_cb(
            f'Downloading packages ({current_bytes // 1048576} MB / {total_packed_bytes // 1048576} MB)...',
            current_bytes,
            total_packed_bytes,
            pct,
        )

    worker_stop = threading.Event()

    def download(pkg: Package) -> Path:
        if cancel_event and cancel_event.is_set():
            worker_stop.set()
        return download_single_package(
            pkg,
            FORCED_ROBLOX_VERSION,
            cache_dir,
            progress_tracker,
            progress_lock,
            notify_progress,
            worker_stop,
        )

    def relay_cancel():
        while not worker_stop.is_set():
            if cancel_event and cancel_event.wait(0.2):
                worker_stop.set()
            elif not cancel_event:
                return

    relay = threading.Thread(target=relay_cancel, daemon=True)
    relay.start()

    package_files: dict[str, Path] = {}
    try:
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(download, pkg): pkg for pkg in manifest}
            try:
                for future in as_completed(futures):
                    package_files[futures[future].name] = future.result()
            except BaseException:
                worker_stop.set()
                raise
    finally:
        worker_stop.set()

    _check_cancel(cancel_event)

    _remove_tree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    try:
        total_packages = len(manifest)
        for i, pkg in enumerate(manifest):
            _check_cancel(cancel_event)
            if progress_cb:
                pct = (i / total_packages) * 100.0
                progress_cb(
                    f'Extracting {pkg.name} ({i + 1}/{total_packages})...',
                    i + 1,
                    total_packages,
                    pct,
                )
            extract_package(pkg, package_files[pkg.name], staging_dir)

        if progress_cb:
            progress_cb('Configuring installation...', total_packages, total_packages, 100.0)

        (staging_dir / 'AppSettings.xml').write_text(APP_SETTINGS_XML, encoding='utf-8')
        (staging_dir / '.fleasion-managed').write_text('WindowsPlayer', encoding='utf-8')

        if not (staging_dir / ROBLOX_PROCESS).is_file():
            raise RuntimeError(f'{ROBLOX_PROCESS} missing from the downloaded packages')

        final_dir.parent.mkdir(parents=True, exist_ok=True)
        _remove_tree(backup_dir)
        if final_dir.exists():
            try:
                final_dir.rename(backup_dir)
            except OSError as exc:
                raise RuntimeError(
                    f'Could not replace {final_dir}, close Roblox and try again ({exc})'
                ) from exc
        staging_dir.rename(final_dir)
    except BaseException:
        _remove_tree(staging_dir)
        raise

    _remove_tree(backup_dir)

    try:
        from .roblox_dirs import load_saved_roblox_dirs, save_saved_roblox_dirs

        existing_dirs = load_saved_roblox_dirs()
        if final_dir not in existing_dirs:
            save_saved_roblox_dirs([final_dir] + existing_dirs)
    except Exception:
        pass

    exe_path = final_dir / ROBLOX_PROCESS
    log_buffer.log('Downloader', f'Successfully installed {FORCED_ROBLOX_VERSION} to {final_dir}')
    return exe_path
