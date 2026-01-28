"""Proxy master module."""

import asyncio
import threading

from mitmproxy import certs
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster

from ..utils import (
    LOCAL_APPDATA,
    MITMPROXY_DIR,
    ROBLOX_PROCESS,
    STORAGE_DB,
    log_buffer,
    terminate_roblox,
    wait_for_roblox_exit,
)
from .addons import TextureStripper
from .addons.cache_scraper import CacheScraper
from ..cache.cache_manager import CacheManager


def get_ca_content() -> str | None:
    """Get the CA certificate content."""
    MITMPROXY_DIR.mkdir(exist_ok=True)
    certs.CertStore.from_store(str(MITMPROXY_DIR), 'mitmproxy', 2048)
    ca_file = MITMPROXY_DIR / 'mitmproxy-ca-cert.pem'
    return ca_file.read_text() if ca_file.exists() else None


def install_certs() -> bool:
    """Install mitmproxy certificates into Roblox."""
    if not (ca := get_ca_content()):
        return False
    for d in LOCAL_APPDATA.glob('**/version-*'):
        if d.is_dir() and (d / ROBLOX_PROCESS).exists():
            ssl_dir = d / 'ssl'
            ssl_dir.mkdir(exist_ok=True)
            ca_file = ssl_dir / 'cacert.pem'
            try:
                existing = ca_file.read_text() if ca_file.exists() else ''
                if ca not in existing:
                    ca_file.write_text(f'{existing}\n{ca}')
            except (PermissionError, OSError):
                pass
    return True


async def wait_for_cert_install(timeout: float = 10.0) -> bool:
    """Wait for certificate installation."""
    for _ in range(int(timeout / 0.1)):
        if install_certs():
            return True
        await asyncio.sleep(0.1)
    return False


class ProxyMaster:
    """Manages the mitmproxy instance."""

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.cache_manager = CacheManager(config_manager)
        self._master = None
        self._task = None
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None

    @property
    def is_running(self) -> bool:
        """Check if proxy is running."""
        return self._running

    async def _run_proxy(self):
        """Run the proxy (internal)."""
        self._running = True

        # Cleanup Roblox and cache
        if terminate_roblox():
            log_buffer.log('Cleanup', 'Roblox found, terminating...')
            if not wait_for_roblox_exit():
                log_buffer.log('Cleanup', 'Termination timed out')
            else:
                log_buffer.log('Cleanup', 'Roblox terminated')
                try:
                    STORAGE_DB.unlink()
                    log_buffer.log('Cleanup', 'Storage deleted')
                except (FileNotFoundError, PermissionError, OSError) as e:
                    log_buffer.log('Cleanup', f'Storage deletion: {e}')
        else:
            log_buffer.log('Cleanup', 'Roblox not running')

        # Create master
        self._master = DumpMaster(
            Options(mode=[f'local:{ROBLOX_PROCESS}']),
            with_termlog=False,
            with_dumper=False,
        )
        # IMPORTANT: Add cache scraper BEFORE texture stripper
        # This ensures we cache original assets before any modifications
        # Cache scraper is disabled by default - user can enable in cache tab
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)  # Disabled by default
        self._master.addons.add(self.cache_scraper)
        self._master.addons.add(TextureStripper(self.config_manager))
        proxy_task = asyncio.create_task(self._master.run())

        # Install certificates
        if not await wait_for_cert_install():
            log_buffer.log('Certificate', 'Installation failed')
            self._running = False
            return

        log_buffer.log('Info', '=' * 50)
        log_buffer.log('Info', 'No Textures Proxy Active')
        log_buffer.log('Info', f'Intercepting: {ROBLOX_PROCESS}')
        log_buffer.log('Info', 'Launch Roblox')
        log_buffer.log('Info', '=' * 50)

        # Wait for stop event or proxy task completion
        try:
            done, pending = await asyncio.wait(
                [proxy_task, asyncio.create_task(self._wait_for_stop())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except Exception as e:
            log_buffer.log('Error', f'Proxy error: {e}')
        finally:
            if self._master:
                try:
                    await self._master.shutdown()
                except Exception:
                    pass
            # Cancel any remaining tasks to avoid "Event loop is closed" warnings
            try:
                loop = asyncio.get_running_loop()
                for task in asyncio.all_tasks(loop):
                    if task is not asyncio.current_task():
                        task.cancel()
                # Give tasks a moment to cancel
                await asyncio.sleep(0.1)
            except Exception:
                pass
            self._running = False

    async def _wait_for_stop(self):
        """Wait for stop event."""
        loop = asyncio.get_event_loop()
        while not self._stop_event.is_set():
            await asyncio.sleep(0.1)

    def start(self):
        """Start the proxy in a background thread."""
        with self._lock:
            if self._running:
                return

            self._stop_event.clear()

            def run_proxy_thread():
                try:
                    asyncio.run(self._run_proxy())
                except Exception as e:
                    log_buffer.log('Error', f'Proxy failed: {e}')
                    self._running = False

            self._thread = threading.Thread(target=run_proxy_thread, daemon=True)
            self._thread.start()

    def stop(self):
        """Stop the proxy."""
        with self._lock:
            if not self._running:
                return

            log_buffer.log('Proxy', 'Stopping proxy...')
            self._stop_event.set()

        # Wait for thread to finish (with timeout)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: Proxy thread did not stop cleanly')
