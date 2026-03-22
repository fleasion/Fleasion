"""ProxyMaster: manages the lifecycle of the Fleasion proxy.

Interception strategy:
  1. Write hosts file entries pointing assetdelivery.roblox.com and
     fts.rbxcdn.com at 127.0.0.1.  Roblox uses libcurl which honours
     the OS hosts file unconditionally (unlike WinINet PAC files).
  2. Run a direct TLS server on 127.0.0.1:443.  Roblox connects directly
     (no HTTP CONNECT tunnel needed) and we present a leaf cert signed by
     our local CA.  Roblox's libcurl validates it against the CA we install
     into each Roblox version's ssl/cacert.pem.
  3. On stop, remove our hosts entries and stop the server.

Admin requirement:
  Writing to %SystemRoot%\\System32\\drivers\\etc\\hosts and binding port 443
  both require administrator privileges. Fleasion will check for elevation
  and log an error if it is missing.

VPN compatibility:
  Loopback (127.0.0.1) traffic is never routed through VPN adapters.
  Only our proxy->CDN upstream connections go through the VPN (correct).
"""

import asyncio
import ctypes
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Set

from ..utils import (
    LOCAL_APPDATA,
    PROXY_CA_DIR,
    PROXY_PORT,
    ROBLOX_PROCESS,
    STORAGE_DB,
    STORAGE_DB_GDK,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
)
from .addons import CacheScraper, TextureStripper
from .server import FleasionProxy, INTERCEPT_HOSTS
from ..cache.cache_manager import CacheManager
from ..utils.certs import generate_ca, generate_host_cert, get_ca_pem

logger = logging.getLogger(__name__)

HOSTS_FILE = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'drivers' / 'etc' / 'hosts'
_HOSTS_MARKER = '# Fleasion proxy entry'


def _resolve_real_ips(hosts: set) -> dict:
    """Resolve real IPs for each host BEFORE we write hosts file entries.

    We MUST do this first - once hosts file points them to 127.0.0.1, any
    subsequent socket.getaddrinfo() call would return 127.0.0.1, causing our
    upstream connections to loop back to ourselves.
    """
    import socket
    real_ips: dict = {}
    for host in sorted(hosts):
        try:
            # getaddrinfo returns all IPs; take the first IPv4 one
            results = socket.getaddrinfo(host, 443, socket.AF_INET, socket.SOCK_STREAM)
            ips = [r[4][0] for r in results if r[4][0] != '127.0.0.1']
            if ips:
                real_ips[host] = ips
                log_buffer.log('Proxy', f'Resolved {host} -> {ips[0]}')
            else:
                log_buffer.log('Proxy', f'Warning: no real IPs found for {host}')
        except Exception as exc:
            log_buffer.log('Proxy', f'DNS resolve failed for {host}: {exc}')
    return real_ips


def _flush_dns() -> None:
    """Flush Windows DNS client cache so the hosts file changes take effect immediately."""
    import subprocess
    try:
        subprocess.run(
            ['ipconfig', '/flushdns'],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=5,
        )
        log_buffer.log('Hosts', 'DNS cache flushed')
    except Exception as exc:
        log_buffer.log('Hosts', f'DNS flush failed (non-fatal): {exc}')


# ---------------------------------------------------------------------------
# Admin check
# ---------------------------------------------------------------------------

def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Hosts file management
# ---------------------------------------------------------------------------

def _add_hosts_entries(hosts: Set[str]) -> bool:
    """Append redirect entries for *hosts* to the system hosts file.

    Returns True on success.  Skips entries already present.
    """
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError as exc:
        log_buffer.log('Hosts', f'Cannot read hosts file: {exc}')
        return False

    lines_to_add = []
    for host in sorted(hosts):
        entry = f'127.0.0.1 {host} {_HOSTS_MARKER}'
        if host not in existing:
            lines_to_add.append(entry)

    if not lines_to_add:
        log_buffer.log('Hosts', 'Hosts entries already present, skipping')
        return True

    new_content = existing.rstrip('\n') + '\n' + '\n'.join(lines_to_add) + '\n'
    try:
        HOSTS_FILE.write_text(new_content, encoding='utf-8')
        for host in sorted(hosts):
            log_buffer.log('Hosts', f'Added redirect: {host} -> 127.0.0.1')
        return True
    except PermissionError:
        log_buffer.log('Hosts', 'Permission denied writing hosts file - run as Administrator')
        return False
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to write hosts file: {exc}')
        return False


def _remove_hosts_entries(hosts: Set[str]) -> None:
    """Remove any hosts file entries we previously added."""
    try:
        existing = HOSTS_FILE.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return

    lines = existing.splitlines(keepends=True)
    filtered = [
        line for line in lines
        if _HOSTS_MARKER not in line and not any(
            f'127.0.0.1 {h}' in line for h in hosts
        )
    ]

    if len(filtered) == len(lines):
        return  # nothing to remove

    try:
        HOSTS_FILE.write_text(''.join(filtered), encoding='utf-8')
        log_buffer.log('Hosts', 'Removed proxy hosts entries')
    except OSError as exc:
        log_buffer.log('Hosts', f'Failed to clean hosts file: {exc}')


# ---------------------------------------------------------------------------
# Roblox CA installation
# ---------------------------------------------------------------------------

def _find_roblox_dirs() -> list:
    """Locate every RobloxPlayerBeta.exe installation via registry and known paths.

    Methods used (combined):
      1. Main Registry   — HKCU\\Software (two levels) for REG_SZ "PlayerPath"
      2. MS Store        — C:\\XboxGames\\Roblox up to two layers deep
      3. Active Roblox   — HKCU\\...\\roblox-player\\open\\command (Default)
      4. Regular Roblox  — %LocalAppData%\\Roblox\\Versions one layer deep
    """
    import winreg

    found: list = []
    seen: set = set()

    def _add(path: Path) -> bool:
        key = str(path)
        if key not in seen:
            found.append(path)
            seen.add(key)
            return True
        return False

    def _scan_for_exe(root: Path, max_depth: int) -> list:
        """Return all subdirs up to max_depth layers under root that contain RobloxPlayerBeta.exe."""
        results: list = []

        def _recurse(path: Path, depth: int) -> None:
            try:
                for entry in os.scandir(path):
                    if not entry.is_dir():
                        continue
                    if os.path.isfile(os.path.join(entry.path, ROBLOX_PROCESS)):
                        results.append(Path(entry.path))
                    if depth < max_depth:
                        _recurse(Path(entry.path), depth + 1)
            except OSError:
                pass

        if root.is_dir():
            _recurse(root, 1)
        return results

    # ── 1. Main Registry Search ──────────────────────────────────────────
    # Walk HKCU\Software and one layer of subkeys; collect any "PlayerPath" value.
    t = time.perf_counter()
    reg_found = 0

    def _check_player_path_key(key) -> None:
        nonlocal reg_found
        try:
            val, rtype = winreg.QueryValueEx(key, 'PlayerPath')
        except OSError:
            return
        if rtype != winreg.REG_SZ or not val:
            return
        p = Path(val)
        # PlayerPath may occasionally point at the exe itself rather than the dir
        if p.name.lower() == ROBLOX_PROCESS.lower():
            p = p.parent
        if os.path.isfile(os.path.join(str(p), ROBLOX_PROCESS)):
            reg_found += 1
            _add(p)
        else:
            for d in _scan_for_exe(p, 1):
                reg_found += 1
                _add(d)

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software') as hkey:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(hkey, i); i += 1
                except OSError:
                    break
                try:
                    with winreg.OpenKey(hkey, name) as sk:
                        _check_player_path_key(sk)
                        j = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(sk, j); j += 1
                            except OSError:
                                break
                            try:
                                with winreg.OpenKey(sk, sub) as ssk:
                                    _check_player_path_key(ssk)
                            except OSError:
                                pass
                except OSError:
                    pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Registry PlayerPath: {int((time.perf_counter() - t) * 1000)} ms ({reg_found} found)')

    # ── 2. MS Store Version ──────────────────────────────────────────────
    # C:\XboxGames\Roblox, up to two layers deep.
    t = time.perf_counter()
    xbox_found = 0
    for d in _scan_for_exe(Path(r'C:\XboxGames\Roblox'), 2):
        xbox_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  XboxGames\\Roblox: {int((time.perf_counter() - t) * 1000)} ms ({xbox_found} found)')

    # ── 3. Active Roblox ─────────────────────────────────────────────────
    # Read HKCU\...\roblox-player\shell\open\command (Default); parse the exe
    # path and search up to two layers under its parent directory.
    t = time.perf_counter()
    active_found = 0
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r'SOFTWARE\Classes\roblox-player\shell\open\command',
        ) as key:
            try:
                cmd, rtype = winreg.QueryValueEx(key, '')
                if rtype == winreg.REG_SZ and cmd:
                    cmd = cmd.strip()
                    if cmd.startswith('"'):
                        exe_path = cmd[1 : cmd.index('"', 1)]
                    else:
                        exe_path = cmd.split()[0]
                    exe_dir = Path(exe_path).parent
                    for d in _scan_for_exe(exe_dir, 2):
                        active_found += 1
                        _add(d)
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    log_buffer.log('Certificate', f'  Active Roblox (registry): {int((time.perf_counter() - t) * 1000)} ms ({active_found} found)')

    # ── 4. Regular Roblox ────────────────────────────────────────────────
    # %LocalAppData%\Roblox\Versions — one layer down.
    t = time.perf_counter()
    roblox_found = 0
    for d in _scan_for_exe(LOCAL_APPDATA / 'Roblox' / 'Versions', 1):
        roblox_found += 1
        _add(d)
    log_buffer.log('Certificate', f'  AppData Roblox\\Versions: {int((time.perf_counter() - t) * 1000)} ms ({roblox_found} found)')

    return found


def _install_ca_into_roblox(ca_pem: str) -> None:
    """Append our CA cert to ssl/cacert.pem next to every found RobloxPlayerBeta.exe."""
    t0 = time.perf_counter()
    dirs = _find_roblox_dirs()
    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    if not dirs:
        log_buffer.log('Certificate', f'No Roblox installs found to patch (scanned in {elapsed_ms} ms)')
        return
    log_buffer.log('Certificate', f'Found {len(dirs)} Roblox install(s) to patch (scanned in {elapsed_ms} ms)')

    for d in dirs:
        ssl_dir = d / 'ssl'
        ssl_dir.mkdir(exist_ok=True)
        ca_file = ssl_dir / 'cacert.pem'
        try:
            existing = ca_file.read_text() if ca_file.exists() else ''
            if ca_pem not in existing:
                ca_file.write_text(f'{existing}\n{ca_pem}')
                log_buffer.log('Certificate', f'Installed CA into {d.name}')
            else:
                log_buffer.log('Certificate', f'CA already installed in {d.name}')
        except (PermissionError, OSError) as exc:
            log_buffer.log('Certificate', f'Failed to write CA for {d.name}: {exc}')


# ---------------------------------------------------------------------------
# ProxyMaster
# ---------------------------------------------------------------------------

class ProxyMaster:
    """Manages the Fleasion proxy lifecycle."""

    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self.cache_manager = CacheManager(config_manager)

        # Singleton addon instances - GUI holds references to these directly
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)
        self._texture_stripper: Optional[TextureStripper] = None

        self._proxy: Optional[FleasionProxy] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._lock = threading.Lock()
        self._hosts_installed: bool = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self) -> None:
        with self._lock:
            if self._running:
                return

            def _run():
                try:
                    asyncio.run(self._run_proxy())
                except Exception as exc:
                    log_buffer.log('Error', f'Proxy failed: {exc}')
                    self._running = False

            self._thread = threading.Thread(target=_run, daemon=True, name='fleasion-proxy')
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self._running and not (self._thread and self._thread.is_alive()):
                return
            log_buffer.log('Proxy', 'Stopping proxy...')

            # Clean up hosts file first so Roblox stops routing to us immediately
            if self._hosts_installed:
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()  # Clear stale 127.0.0.1 cache so new connections stop coming in

            # Stop the asyncio server
            if self._proxy and self._loop and self._loop.is_running():
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._proxy.stop(), self._loop)
                    fut.result(timeout=3.0)
                except Exception:
                    pass

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: proxy thread did not stop cleanly')

    async def _run_proxy(self) -> None:
        self._running = True
        self._loop = asyncio.get_running_loop()

        # ── Admin check ───────────────────────────────────────────────────
        if not _is_admin():
            log_buffer.log('Error', (
                'Fleasion requires administrator privileges to modify the hosts file '
                'and bind port 443.  Please run as Administrator.'
            ))
            self._running = False
            return

        # ── Optional cache clear on launch ───────────────────────────────
        if self.config_manager.clear_cache_on_launch:
            if terminate_roblox():
                log_buffer.log('Cleanup', 'Roblox found, terminating...')
                if not wait_for_roblox_exit():
                    log_buffer.log('Cleanup', 'Termination timed out')
                else:
                    log_buffer.log('Cleanup', 'Roblox terminated')
                    try:
                        STORAGE_DB.unlink()
                        log_buffer.log('Cleanup', 'Storage deleted')
                    except (FileNotFoundError, PermissionError, OSError) as exc:
                        log_buffer.log('Cleanup', f'Storage deletion: {exc}')
                    if STORAGE_DB_GDK.parent.exists():
                        try:
                            STORAGE_DB_GDK.unlink()
                            log_buffer.log('Cleanup', 'Storage (GDK) deleted')
                        except FileNotFoundError:
                            pass
                        except (PermissionError, OSError) as exc:
                            log_buffer.log('Cleanup', f'Storage (GDK) deletion: {exc}')
            else:
                log_buffer.log('Cleanup', 'Roblox not running')
        else:
            log_buffer.log('Cleanup', 'Cache clear on launch disabled - skipping')

        # ── Certificate setup ─────────────────────────────────────────────
        log_buffer.log('Certificate', 'Generating/loading CA certificates...')
        t0 = time.perf_counter()
        try:
            ca_cert_path, ca_key_path = generate_ca(PROXY_CA_DIR)
        except Exception as exc:
            log_buffer.log('Certificate', f'CA generation failed: {exc}')
            self._running = False
            return

        host_certs = {}
        for host in INTERCEPT_HOSTS:
            try:
                cert_path, key_path = generate_host_cert(
                    host, ca_cert_path, ca_key_path, PROXY_CA_DIR,
                )
                host_certs[host] = (cert_path, key_path)
            except Exception as exc:
                log_buffer.log('Certificate', f'Leaf cert failed for {host}: {exc}')
                self._running = False
                return

        elapsed_ms = (time.perf_counter() - t0) * 1000
        log_buffer.log('Certificate', f'Certificates ready in {elapsed_ms:.0f} ms')

        # Install CA into Roblox ssl dirs
        ca_pem = get_ca_pem(ca_cert_path)
        _install_ca_into_roblox(ca_pem)

        # ── Clean up any stale hosts entries from a previous crash ─────
        # If the previous session crashed without calling stop(), our hosts
        # entries may still be present. If we resolve IPs while they're there,
        # getaddrinfo() returns 127.0.0.1 instead of real CDN IPs, causing
        # every upstream connection to loop back to ourselves.
        _remove_hosts_entries(set(INTERCEPT_HOSTS))
        _flush_dns()

        # ── Resolve real CDN IPs BEFORE writing new hosts file entries ────
        # CRITICAL: must happen after removing stale entries (above) and before
        # writing new ones. This guarantees getaddrinfo() returns real IPs.
        real_ips = _resolve_real_ips(set(INTERCEPT_HOSTS))

        # ── Create addon instances ────────────────────────────────────────
        self._texture_stripper = TextureStripper(self.config_manager)
        # Give the scraper real IPs for ALL intercepted hosts so its API
        # calls bypass our hosts file redirect (including CDN redirects).
        scraper_ips = {
            host: ips[0]
            for host, ips in real_ips.items()
            if ips
        }
        self.cache_scraper.set_real_ips(scraper_ips)

        # Wire the scraper into the json_viewer's AssetFetcherThread so the
        # Preview tab in the standalone JSON viewer also bypasses the hosts file.
        try:
            from ..gui.json_viewer import AssetFetcherThread
            AssetFetcherThread.set_scraper(self.cache_scraper)
        except Exception:
            pass

        # ── Start TLS proxy server ────────────────────────────────────────
        self._proxy = FleasionProxy(
            texture_stripper=self._texture_stripper,
            cache_scraper=self.cache_scraper,
            host_certs=host_certs,
            upstream_ips=real_ips,
            port=PROXY_PORT,
        )
        try:
            await self._proxy.start()
        except OSError as exc:
            if exc.errno == 10013 or 'access' in str(exc).lower() or '443' in str(exc):
                log_buffer.log('Error', (
                    f'Cannot bind port {PROXY_PORT}: access denied. '
                    'Ensure no other process is using this port and run as Administrator.'
                ))
            else:
                log_buffer.log('Error', f'Failed to start proxy: {exc}')
            self._running = False
            return
        except Exception as exc:
            log_buffer.log('Error', f'Failed to start proxy: {exc}')
            self._running = False
            return

        # ── Write hosts file entries ──────────────────────────────────────
        if not _add_hosts_entries(set(INTERCEPT_HOSTS)):
            # Hosts write failed - stop the server and bail
            await self._proxy.stop()
            self._running = False
            return
        self._hosts_installed = True
        _flush_dns()  # Make the new entries take effect immediately



        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'Fleasion Proxy Active')
        log_buffer.log('Info', f'Intercepting: {", ".join(sorted(INTERCEPT_HOSTS))}')
        log_buffer.log('Info', f'Port: {PROXY_PORT}')
        log_buffer.log('Info', 'Launch Roblox')
        log_buffer.log('Info', '=' * 50)

        # ── Run until the server is stopped ──────────────────────────────
        try:
            await self._proxy._server.serve_forever()
        except (asyncio.CancelledError, Exception):
            pass  # Normal shutdown path
        finally:
            # Ensure hosts file is cleaned up even if stop() wasn't called
            if self._hosts_installed:
                _remove_hosts_entries(set(INTERCEPT_HOSTS))
                self._hosts_installed = False
                _flush_dns()
            try:
                await self._proxy.stop()
            except Exception:
                pass
            self._running = False
            self._loop = None
