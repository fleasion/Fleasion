"""Proxy master module."""

import asyncio
import threading
import time
import os

# Cache CA content to avoid expensive regeneration on repeated checks
_CA_CONTENT_CACHE: str | None = None

from mitmproxy import certs
from mitmproxy.options import Options
from mitmproxy.tools.dump import DumpMaster
import mitmproxy.log as mitm_log
import logging


def _clear_mitm_log_handlers() -> None:
    """Clear mitmproxy master references from any logging handlers.

    Mitmproxy's logging handlers keep a reference to a `master` whose
    `event_loop` they use to schedule thread-safe callbacks. If the
    event loop is closed before these handlers are cleared, they will
    raise a RuntimeError. We defensively clear `master` on handlers and
    the module global here.
    """
    try:
        # Clear module-level reference first
        mitm_log.master = None
    except Exception:
        pass

    try:
        # Clear any handler instances that have a `master` attribute
        # from the root logger and all existing named loggers. Wrap their
        # `emit` method so they don't attempt to access `master`.
        root = logging.getLogger()
        for h in list(root.handlers):
            if hasattr(h, 'master'):
                try:
                    orig_emit = h.emit

                    def _safe_emit(record, _orig=orig_emit):
                        try:
                            _orig(record)
                        except Exception:
                            # Swallow errors from mitmproxy logging during shutdown
                            pass

                    try:
                        setattr(h, 'emit', _safe_emit)
                    except Exception:
                        pass
                    try:
                        setattr(h, 'master', None)
                    except Exception:
                        pass
                except Exception:
                    pass

        mgr = logging.Logger.manager
        for name, obj in list(mgr.loggerDict.items()):
            if isinstance(obj, logging.Logger):
                for h in list(obj.handlers):
                    if hasattr(h, 'master'):
                        try:
                            orig_emit = h.emit

                            def _safe_emit_local(record, _orig=orig_emit):
                                try:
                                    _orig(record)
                                except Exception:
                                    pass

                            try:
                                if not hasattr(h, '_orig_emit'):
                                    setattr(h, '_orig_emit', orig_emit)
                                setattr(h, 'emit', _safe_emit_local)
                            except Exception:
                                pass
                            try:
                                setattr(h, 'master', None)
                            except Exception:
                                pass
                        except Exception:
                            pass
    except Exception:
        pass


def _restore_mitm_log_handlers() -> None:
    """Restore any previously-wrapped mitmproxy handler emits.

    This re-enables normal logging behavior if the proxy is started
    again within the same process.
    """
    try:
        root = logging.getLogger()
        for h in list(root.handlers):
            if hasattr(h, '_orig_emit'):
                try:
                    setattr(h, 'emit', getattr(h, '_orig_emit'))
                    delattr(h, '_orig_emit')
                except Exception:
                    pass

        mgr = logging.Logger.manager
        for name, obj in list(mgr.loggerDict.items()):
            if isinstance(obj, logging.Logger):
                for h in list(obj.handlers):
                    if hasattr(h, '_orig_emit'):
                        try:
                            setattr(h, 'emit', getattr(h, '_orig_emit'))
                            delattr(h, '_orig_emit')
                        except Exception:
                            pass
    except Exception:
        pass

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
    ca_file = MITMPROXY_DIR / 'mitmproxy-ca-cert.pem'
    global _CA_CONTENT_CACHE
    if _CA_CONTENT_CACHE is not None:
        return _CA_CONTENT_CACHE

    # If certificate file already exists, read and cache it.
    if ca_file.exists():
        _CA_CONTENT_CACHE = ca_file.read_text()
        return _CA_CONTENT_CACHE

    # Otherwise, generate the certificate store (may be slow); measure time.
    start = time.perf_counter()
    try:
        certs.CertStore.from_store(str(MITMPROXY_DIR), 'mitmproxy', 2048)
    except Exception as e:
        log_buffer.log('Certificate', f'CA generation error: {e}')
        return None
    gen_elapsed = (time.perf_counter() - start) * 1000.0
    log_buffer.log('Certificate', f'CA store generated in {gen_elapsed:.0f} ms')

    if ca_file.exists():
        _CA_CONTENT_CACHE = ca_file.read_text()
        return _CA_CONTENT_CACHE
    return None


def install_certs() -> bool:
    """Install mitmproxy certificates into Roblox."""
    if not (ca := get_ca_content()):
        return False

    # Fast scan: check each immediate child of LOCAL_APPDATA for a 'Versions' folder
    # This finds official Roblox and third-party bootstrappers (e.g., Fishstrap) quickly
    start_scan = time.perf_counter()
    dirs: list = []

    try:
        for child in LOCAL_APPDATA.iterdir():
            if not child.is_dir():
                continue
            versions_sub = child / 'Versions'
            if versions_sub.exists():
                for entry in versions_sub.iterdir():
                    if entry.is_dir() and entry.name.startswith('version-'):
                        dirs.append(entry)
    except Exception:
        # Fall back to legacy Roblox-specific checks if iteration fails
        versions_dir = LOCAL_APPDATA / 'Roblox' / 'Versions'
        if versions_dir.exists():
            for entry in versions_dir.iterdir():
                if entry.is_dir() and entry.name.startswith('version-'):
                    dirs.append(entry)

    # Also include any immediate children directly named version-*
    try:
        for entry in LOCAL_APPDATA.iterdir():
            if entry.is_dir() and entry.name.startswith('version-') and entry not in dirs:
                dirs.append(entry)
    except Exception:
        pass

    scan_elapsed = (time.perf_counter() - start_scan) * 1000.0
    log_buffer.log('Certificate', f'Located version-* candidates ({len(dirs)} found) in {scan_elapsed:.0f} ms')

    for d in dirs:
        if d.is_dir() and (d / ROBLOX_PROCESS).exists():
            ssl_dir = d / 'ssl'
            ssl_dir.mkdir(exist_ok=True)
            ca_file = ssl_dir / 'cacert.pem'
            try:
                read_start = time.perf_counter()
                existing = ca_file.read_text() if ca_file.exists() else ''
                read_elapsed = (time.perf_counter() - read_start) * 1000.0
                if ca not in existing:
                    write_start = time.perf_counter()
                    ca_file.write_text(f'{existing}\n{ca}')
                    write_elapsed = (time.perf_counter() - write_start) * 1000.0
                    log_buffer.log('Certificate', f'Wrote cacert.pem for {d.name} in {write_elapsed:.0f} ms (read {read_elapsed:.0f} ms)')
            except (PermissionError, OSError) as e:
                log_buffer.log('Certificate', f'Failed to write cacert.pem for {d.name}: {e}')
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
        # Create cache scraper early so UI always gets a valid reference
        self.cache_scraper = CacheScraper(self.cache_manager)
        self.cache_scraper.set_enabled(False)  # Disabled by default
        self._master = None
        self._task = None
        self._running = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._loop = None

    @property
    def is_running(self) -> bool:
        """Check if proxy is running."""
        return self._running

    async def _run_proxy(self):
        """Run the proxy (internal)."""
        self._running = True
        self._loop = asyncio.get_running_loop()

        # Cleanup Roblox and cache (only if setting is enabled)
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
                    except (FileNotFoundError, PermissionError, OSError) as e:
                        log_buffer.log('Cleanup', f'Storage deletion: {e}')
            else:
                log_buffer.log('Cleanup', 'Roblox not running')
        else:
            log_buffer.log('Cleanup', 'Cache clear on launch disabled - skipping Roblox termination')

        # Create master with performance-optimized options
        opts = Options(
            mode=[f'local:{ROBLOX_PROCESS}'],
            # Reduce CPU overhead by not validating upstream certs for local interception
            upstream_cert=False,
        )
        self._master = DumpMaster(
            opts,
            with_termlog=False,
            with_dumper=False,
        )
        # Restore any logging handler emits that were wrapped during a
        # previous shutdown so mitmproxy logs work for this run.
        _restore_mitm_log_handlers()
        # Ensure mitmproxy's module-level master reference points at our
        # master so its logging handler can schedule callbacks safely.
        try:
            mitm_log.master = self._master
        except Exception:
            pass
        # Add texture stripper BEFORE cache scraper
        # This ensures we cache injected assets (like CSGs)
        self._master.addons.add(TextureStripper(self.config_manager))
        self._master.addons.add(self.cache_scraper)
        proxy_task = asyncio.create_task(self._master.run())

        # Install certificates (measure elapsed time)
        start = time.perf_counter()
        cert_installed = await wait_for_cert_install()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if not cert_installed:
            log_buffer.log('Certificate', f'Installation failed (took {elapsed_ms:.0f} ms)')
            self._running = False
            return
        log_buffer.log('Certificate', f'Installation completed in {elapsed_ms:.0f} ms')

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
            # Prevent mitmproxy log handler from scheduling calls on a closing
            # event loop by clearing its global master reference and any
            # handler instances that reference it.
            _clear_mitm_log_handlers()

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
            self._loop = None

    async def _wait_for_stop(self):
        """Wait for stop event (event-based, not polling)."""
        loop = asyncio.get_event_loop()
        # Use executor to wait on threading Event without busy polling
        await loop.run_in_executor(None, self._stop_event.wait)

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
            # Even if startup failed and `self._running` is False, the
            # background thread may still be alive or waiting on UAC/IO.
            # Attempt to stop/join the thread and signal the stop event
            # regardless of the `_running` flag so the app can exit cleanly.
            if not self._running and not (self._thread and self._thread.is_alive()):
                return

            log_buffer.log('Proxy', 'Stopping proxy...')
            # Ask mitmproxy to shutdown on its own event loop to avoid pending tasks
            # Clear mitmproxy logging references before attempting shutdown.
            _clear_mitm_log_handlers()

            if self._master and self._loop:
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._master.shutdown(), self._loop)
                    fut.result(timeout=2.0)
                except Exception:
                    pass
            # Ensure any waiters using the threading.Event are released
            self._stop_event.set()

        # Wait for thread to finish (with timeout)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                log_buffer.log('Proxy', 'Warning: Proxy thread did not stop cleanly')